import datetime

import bcrypt
from flask import jsonify, request, url_for
from flask_mail import Message


class BackendServices:
    API_FREE_SUBSCRIPTION_DAYS = 2
    MIN_PASSWORD_LENGTH = 8
    OTP_VALIDITY_MINUTES = 10

    def __init__(
        self,
        app,
        logger,
        mail,
        db,
        users_collection,
        apikeys_collection,
        subscriptionperiod_collection,
        get_user_from_token,
        create_access_token_func,
        generate_reset_token,
        randint_func,
    ):
        self.app = app
        self.logger = logger
        self.mail = mail
        self.db = db
        self.users_collection = users_collection
        self.apikeys_collection = apikeys_collection
        self.subscriptionperiod_collection = subscriptionperiod_collection
        self.get_user_from_token = get_user_from_token
        self.create_access_token = create_access_token_func
        self.generate_reset_token = generate_reset_token
        self.randint = randint_func

    def get_user_collection(self):
        return self.db["users"]

    def api_json_response(self, success, message, status_code=200, **extra):
        payload = {'success': success, 'message': message}
        payload.update(extra)
        return jsonify(payload), status_code

    def get_mail_sender(self):
        return self.app.config.get('MAIL_DEFAULT_SENDER') or self.app.config.get('MAIL_USERNAME') or 'your_email@example.com'

    def get_clean_form_value(self, field_name, default=''):
        value = request.form.get(field_name, default)
        return value.strip() if isinstance(value, str) else value

    def get_clean_form_payload(self, excluded_keys=None):
        excluded_keys = set(excluded_keys or [])
        payload = {}
        for key in request.form.keys():
            if key in excluded_keys:
                continue
            payload[key] = self.get_clean_form_value(key)
        return payload

    def get_user_by_username_or_email(self, identifier):
        normalized_identifier = identifier.lower().strip()
        if not normalized_identifier:
            return None
        return self.users_collection.find_one({
            '$or': [
                {'username': normalized_identifier},
                {'email': normalized_identifier},
            ]
        })

    def build_login_payload(self, username):
        return {
            'token': username,
            'username': username,
            'access_token': self.create_access_token(identity=username),
        }

    def verify_password(self, password, password_hash):
        if not password or not password_hash:
            return False
        return bcrypt.hashpw(password.encode('utf-8'), password_hash) == password_hash

    def ensure_free_subscription(self, username, days=None):
        subscription_days = days or self.API_FREE_SUBSCRIPTION_DAYS
        existing_subscription = self.subscriptionperiod_collection.find_one({'user': username})
        if existing_subscription:
            return

        today_date = datetime.datetime.now().date()
        future_date = today_date + datetime.timedelta(days=subscription_days)
        self.subscriptionperiod_collection.insert_one({
            'user': username,
            'start': today_date.strftime('%Y-%m-%d'),
            'end': future_date.strftime('%Y-%m-%d'),
            'subtype': "free",
        })

    def validate_password_inputs(self, new_password, confirm_password):
        if not new_password or not confirm_password:
            return self.api_json_response(False, "Both password fields are required.", 400)
        if new_password != confirm_password:
            return self.api_json_response(False, "Passwords do not match.", 400)
        if len(new_password) < self.MIN_PASSWORD_LENGTH:
            return self.api_json_response(
                False,
                f"Password must be at least {self.MIN_PASSWORD_LENGTH} characters long.",
                400,
            )
        return None

    def get_user_by_email(self, email):
        normalized_email = email.lower().strip()
        if not normalized_email:
            return None
        return self.get_user_collection().find_one({'email': normalized_email})

    def validate_otp_for_user(self, email, otp):
        normalized_email = email.lower().strip()
        otp_value = otp.strip()

        if not normalized_email or not otp_value:
            return None, self.api_json_response(False, "Email and OTP are required.", 400)

        user = self.get_user_by_email(normalized_email)
        if not user:
            self.logger.warning("OTP validation failed for unknown email %s", normalized_email)
            return None, self.api_json_response(False, "Invalid email.", 400)

        try:
            parsed_otp = int(otp_value)
        except ValueError:
            return None, self.api_json_response(False, "Invalid OTP.", 400)

        if user.get('otp') != parsed_otp:
            self.logger.warning("OTP mismatch for user %s", normalized_email)
            return None, self.api_json_response(False, "Invalid OTP.", 400)

        otp_expiration = user.get('otp_expiration')
        if not otp_expiration or otp_expiration < datetime.datetime.utcnow():
            self.logger.info("Expired OTP used for user %s", normalized_email)
            return None, self.api_json_response(False, "OTP has expired.", 400)

        return user, None

    def get_api_request_user(self, require_broker=False):
        token = self.get_clean_form_value('token')
        broker = self.get_clean_form_value('broker')

        if not token:
            return None, broker, self.api_json_response(False, "Token is required.", 400)
        if require_broker and not broker:
            return None, broker, self.api_json_response(False, "broker is required.", 400)

        user = self.get_user_from_token(token)
        if not user:
            return None, broker, self.api_json_response(False, "User not found.", 404)

        return user, broker, None

    def build_api_key_payload(self, username):
        payload = self.get_clean_form_payload(excluded_keys={'_id'})
        payload['user'] = username
        return payload

    def create_apikey_for_user(self, require_broker=False):
        user, broker, error_response = self.get_api_request_user(require_broker=require_broker)
        if error_response:
            return error_response

        query = {'user': user['username']}
        if require_broker:
            query['broker'] = broker

        existing_apikey = self.apikeys_collection.find_one(query)
        if existing_apikey:
            return self.api_json_response(False, "API key already exists for this user.", 409)

        self.apikeys_collection.insert_one(self.build_api_key_payload(user['username']))
        return self.api_json_response(True, "Successfully added API key.")

    def update_apikey_for_user(self, require_broker=False):
        user, broker, error_response = self.get_api_request_user(require_broker=require_broker)
        if error_response:
            return error_response

        query = {'user': user['username']}
        if require_broker:
            query['broker'] = broker

        result = self.apikeys_collection.update_one(
            query,
            {'$set': self.build_api_key_payload(user['username'])},
        )
        if result.matched_count == 0:
            return self.api_json_response(False, "API key not found for this user.", 404)

        return self.api_json_response(True, "Successfully updated API key.")

    def send_reset_email(self, email, reset_token):
        msg = Message('Password Reset Request', sender=self.get_mail_sender(), recipients=[email])
        msg.body = f'''To reset your password, visit the following link:
{url_for('reset_password', reset_token=reset_token, _external=True)}

If you did not make this request, ignore this email.
'''
        self.mail.send(msg)

    def send_otp_email(self, email, otp):
        msg = Message('Password Reset OTP', sender=self.get_mail_sender(), recipients=[email])
        msg.body = f'''To reset your password, use the following OTP:
{otp}

This OTP is valid for 10 minutes.

If you did not make this request, ignore this email.
'''
        self.mail.send(msg)
