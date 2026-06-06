import datetime

import bcrypt
from flask import jsonify, request, session


def register_auth_api_routes(app, services):
    def api_logout():
        session.pop('username', None)
        return jsonify({'success': True, "message": "Successfully Log Out"})

    def api_login():
        try:
            username_or_email = services.get_clean_form_value('username').lower()
            password = services.get_clean_form_value('password')

            if not username_or_email or not password:
                return services.api_json_response(False, 'Username/email and password are required.', 400)

            login_user = services.get_user_by_username_or_email(username_or_email)
            if not login_user:
                return services.api_json_response(False, 'User not found', 404)

            if not services.verify_password(password, login_user.get('password')):
                return services.api_json_response(False, 'Incorrect password', 401)

            session['username'] = login_user['username']
            services.ensure_free_subscription(login_user['username'])
            return services.api_json_response(
                True,
                'Successfully logged in',
                **services.build_login_payload(login_user['username']),
            )
        except Exception:
            app.logger.exception('Error in api_login')
            return services.api_json_response(False, 'An error occurred during login', 500)

    def api_register():
        try:
            username = services.get_clean_form_value('username').lower()
            email = services.get_clean_form_value('email').lower()
            password = services.get_clean_form_value('password')
            mobile = services.get_clean_form_value('mobile')

            if not username or not email or not password:
                return services.api_json_response(False, 'Missing username, email, or password', 400)

            existing_user = services.users_collection.find_one({'$or': [{'username': username}, {'email': email}]})
            if existing_user is not None:
                return services.api_json_response(False, 'User already exists', 409)

            hashpass = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            services.users_collection.insert_one({
                'username': username,
                'email': email,
                'mobile': mobile,
                'password': hashpass,
                "StrategyLimit": 10,
            })

            session['username'] = username
            services.ensure_free_subscription(username)
            return services.api_json_response(
                True,
                'Successfully Registered & Logged in',
                **services.build_login_payload(username),
            )

        except Exception:
            app.logger.exception('Error in api_register')
            return services.api_json_response(False, 'Internal Server Error', 500)

    def api_forgot_reset_password():
        if request.method != 'POST':
            return services.api_json_response(False, 'Invalid request method', 405)

        email = services.get_clean_form_value('email').lower()
        if not email:
            return services.api_json_response(False, 'Email is required.', 400)

        users = services.get_user_collection()
        user = users.find_one({'email': email})
        if not user:
            return services.api_json_response(False, 'No user found with that email address.', 404)

        reset_token = user.get('reset_token')
        if reset_token:
            return services.api_json_response(False, 'A password reset email has already been sent. Please check your email.')

        reset_token = services.generate_reset_token()
        users.update_one({'_id': user['_id']}, {'$set': {'reset_token': reset_token}})
        services.send_reset_email(email, reset_token)
        return services.api_json_response(True, 'An email with instructions to reset your password has been sent.')

    def api_reset_password(reset_token):
        users = services.get_user_collection()
        user = users.find_one({'reset_token': reset_token})

        if not user:
            return services.api_json_response(False, "Invalid or expired reset token.", 400)

        if request.method == 'POST':
            new_password = services.get_clean_form_value('new_password')
            confirm_password = services.get_clean_form_value('confirm_password')
            validation_response = services.validate_password_inputs(new_password, confirm_password)
            if validation_response:
                return validation_response

            hashpass = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            users.update_one(
                {'_id': user['_id']},
                {'$set': {'password': hashpass, 'reset_token': None}},
            )

            return services.api_json_response(True, "Your password has been successfully reset. You can now log in with your new password.")

        return services.api_json_response(True, "Reset token is valid. You can now reset your password.")

    def api_forgot_otp_reset_password():
        if request.method != 'POST':
            return services.api_json_response(False, 'Invalid request method', 405)

        email = services.get_clean_form_value('email').lower()
        if not email:
            return services.api_json_response(False, 'Email is required.', 400)

        users = services.get_user_collection()
        user = users.find_one({'email': email})
        if not user:
            return services.api_json_response(False, 'No user found with that email address.', 404)

        otp_expiration = user.get('otp_expiration')
        if user.get('otp') and otp_expiration and otp_expiration >= datetime.datetime.utcnow():
            return services.api_json_response(False, 'An OTP email has already been sent. Please check your email.')

        otp = services.randint(100000, 999999)
        otp_expiration = datetime.datetime.utcnow() + datetime.timedelta(minutes=services.OTP_VALIDITY_MINUTES)
        users.update_one({'_id': user['_id']}, {'$set': {'otp': otp, 'otp_expiration': otp_expiration}})
        services.send_otp_email(email, otp)
        return services.api_json_response(True, 'An email with instructions to reset your password (including OTP) has been sent.')

    def api_otp_verify():
        _, error_response = services.validate_otp_for_user(
            services.get_clean_form_value('email'),
            services.get_clean_form_value('otp'),
        )
        if error_response:
            return error_response

        return services.api_json_response(True, "Your OTP has been successfully Matched.")

    def api_otp_reset_password():
        user, error_response = services.validate_otp_for_user(
            services.get_clean_form_value('email'),
            services.get_clean_form_value('otp'),
        )
        if error_response:
            return error_response

        new_password = services.get_clean_form_value('new_password')
        confirm_password = services.get_clean_form_value('confirm_password')
        validation_response = services.validate_password_inputs(new_password, confirm_password)
        if validation_response:
            return validation_response

        hashpass = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        services.get_user_collection().update_one(
            {'_id': user['_id']},
            {'$set': {'password': hashpass, 'otp': None, 'otp_expiration': None}},
        )
        return services.api_json_response(True, "Your password has been successfully reset. You can now log in with your new password.")

    app.add_url_rule('/api_logout', view_func=api_logout)
    app.add_url_rule('/api_login', view_func=api_login, methods=['POST'])
    app.add_url_rule('/api_register', view_func=api_register, methods=['POST'])
    app.add_url_rule('/api_forgot_reset_password', view_func=api_forgot_reset_password, methods=['GET', 'POST'])
    app.add_url_rule('/api_reset_password/<reset_token>', view_func=api_reset_password, methods=['GET', 'POST'])
    app.add_url_rule('/api_forgot_otp_reset_password', view_func=api_forgot_otp_reset_password, methods=['GET', 'POST'])
    app.add_url_rule('/api_otp_verify', view_func=api_otp_verify, methods=['POST'])
    app.add_url_rule('/api_otp_reset_password', view_func=api_otp_reset_password, methods=['POST'])
