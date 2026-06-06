import datetime
import os
import secrets


def _csv_env(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


class AppConfig:
    JWT_SECRET_KEY = os.getenv('SSLAGO_JWT_SECRET_KEY') or secrets.token_urlsafe(48)
    FLASK_SECRET_KEY = os.getenv('SSLAGO_FLASK_SECRET_KEY') or secrets.token_urlsafe(48)
    JWT_ACCESS_TOKEN_EXPIRES = datetime.timedelta(
        hours=int(os.getenv('SSLAGO_JWT_EXPIRES_HOURS', '12'))
    )

    CORS_ALLOWED_ORIGINS = _csv_env(
        'SSLAGO_CORS_ALLOWED_ORIGINS',
        'https://ssalgo.com,https://www.ssalgo.com,http://localhost:5173,http://127.0.0.1:5173',
    )

    MAIL_SERVER = os.getenv('SSLAGO_MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('SSLAGO_MAIL_PORT', '465'))
    MAIL_USE_SSL = os.getenv('SSLAGO_MAIL_USE_SSL', 'true').lower() == 'true'
    MAIL_USERNAME = os.getenv('SSLAGO_MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('SSLAGO_MAIL_PASSWORD', '')

    MONGO_URI = os.getenv('SSLAGO_MONGO_URI', 'mongodb://localhost:27017')
    MONGO_DB = os.getenv('SSLAGO_MONGO_DB', 'demo')

    RAZORPAY_KEY_ID = os.getenv('SSLAGO_RAZORPAY_KEY_ID', '')
    RAZORPAY_KEY_SECRET = os.getenv('SSLAGO_RAZORPAY_KEY_SECRET', '')

    SHOONYA_CREDENTIALS_FILE = os.getenv('SSLAGO_SHOONYA_CREDENTIALS_FILE', '')
    SSL_CERT_FILE = os.getenv('SSLAGO_SSL_CERT_FILE', '')
    SSL_KEY_FILE = os.getenv('SSLAGO_SSL_KEY_FILE', '')
