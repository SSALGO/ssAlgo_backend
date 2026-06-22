import datetime
import os
import secrets
from pathlib import Path


def _csv_env(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


def _load_local_env():
    env_path = Path(__file__).resolve().parents[2] / '.env'
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()


class AppConfig:
    ENVIRONMENT = os.getenv('SSLAGO_ENVIRONMENT', 'development').lower()
    JWT_SECRET_KEY = os.getenv('SSLAGO_JWT_SECRET_KEY') or secrets.token_urlsafe(48)
    FLASK_SECRET_KEY = os.getenv('SSLAGO_FLASK_SECRET_KEY') or secrets.token_urlsafe(48)
    CREDENTIAL_ENCRYPTION_KEY = os.getenv('SSLAGO_CREDENTIAL_ENCRYPTION_KEY', '')
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

    ALICEBLUE_APP_CODE = os.getenv('SSLAGO_ALICEBLUE_APP_CODE') or os.getenv('ALICEBLUE_APP_CODE', '')
    ALICEBLUE_APP_SECRET = os.getenv('SSLAGO_ALICEBLUE_APP_SECRET') or os.getenv('ALICEBLUE_APP_SECRET', '')
    ALICEBLUE_CALLBACK_URL = os.getenv('SSLAGO_ALICEBLUE_CALLBACK_URL', '')
    KITE_API_KEY = os.getenv('SSLAGO_KITE_API_KEY') or os.getenv('KITE_API_KEY', '')
    KITE_API_SECRET = os.getenv('SSLAGO_KITE_API_SECRET') or os.getenv('KITE_API_SECRET', '')
    KITE_REDIRECT_URL = os.getenv('SSLAGO_KITE_REDIRECT_URL') or os.getenv('KITE_REDIRECT_URL', '')
    KITE_POSTBACK_URL = os.getenv('SSLAGO_KITE_POSTBACK_URL') or os.getenv('KITE_POSTBACK_URL', '')
    DHAN_POSTBACK_SECRET = os.getenv('SSLAGO_DHAN_POSTBACK_SECRET', '').strip()
    MARKET_FEED_PROVIDER = os.getenv('SSLAGO_MARKET_FEED_PROVIDER', 'zerodha').strip().lower()
    MARKET_FEED_PROVIDERS = _csv_env(
        'SSLAGO_MARKET_FEED_PROVIDERS',
        os.getenv('SSLAGO_MARKET_FEED_PROVIDER', 'upstox,aliceblue,zerodha'),
    )
    MARKET_FEED_FAILOVER_MODE = os.getenv(
        'SSLAGO_MARKET_FEED_FAILOVER_MODE',
        'connect_failure_only',
    ).strip().lower()
    MARKET_FEED_USER = os.getenv('SSLAGO_MARKET_FEED_USER', '').strip()
    MARKET_FEED_ACCESS_TOKEN = os.getenv('SSLAGO_MARKET_FEED_ACCESS_TOKEN', '').strip()
    UPSTOX_ACCESS_TOKEN = os.getenv('SSLAGO_UPSTOX_ACCESS_TOKEN', '').strip()
    ALICEBLUE_MARKET_FEED_USER = os.getenv('SSLAGO_ALICEBLUE_MARKET_FEED_USER', '').strip()
    ALICEBLUE_MARKET_FEED_SESSION_ID = os.getenv('SSLAGO_ALICEBLUE_MARKET_FEED_SESSION_ID', '').strip()
    MARKET_PRICE_STALE_SECONDS = int(os.getenv('SSLAGO_MARKET_PRICE_STALE_SECONDS', '15'))
    MARKET_PRICE_WRITE_INTERVAL_SECONDS = float(os.getenv('SSLAGO_MARKET_PRICE_WRITE_INTERVAL_SECONDS', '0.25'))
    FRONTEND_BROKER_CALLBACK_URL = os.getenv(
        'SSLAGO_FRONTEND_BROKER_CALLBACK_URL',
        'http://localhost:5173/broker-setup',
    )

    SHOONYA_CREDENTIALS_FILE = os.getenv('SSLAGO_SHOONYA_CREDENTIALS_FILE', '')
    SSL_CERT_FILE = os.getenv('SSLAGO_SSL_CERT_FILE', '')
    SSL_KEY_FILE = os.getenv('SSLAGO_SSL_KEY_FILE', '')
