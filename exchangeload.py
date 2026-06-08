import asyncio
import logging
import os
import ssl
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymongo
import pyotp
import yaml

try:
    from NorenRestApiPy.NorenApi import NorenApi
except ImportError:
    NorenApi = None
NorenBase = NorenApi or object

from backend_modules.config import AppConfig


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml_credentials():
    credentials_file = AppConfig.SHOONYA_CREDENTIALS_FILE
    if not credentials_file:
        return {}

    path = Path(credentials_file)
    if not path.exists():
        logging.warning("Shoonya credentials file does not exist: %s", path)
        return {}

    with path.open("r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=yaml.FullLoader) or {}


client = None
db = None


def get_database():
    global client, db
    if db is not None:
        return db
    try:
        client = pymongo.MongoClient(AppConfig.MONGO_URI, maxPoolSize=100)
        db = client[AppConfig.MONGO_DB]
        logging.info("MongoDB connection established")
    except Exception as exc:
        logging.error("MongoDB connection failed: %s", exc)
        db = None
    return db


cred = {}


class NorenApiPy(NorenBase):
    def __init__(self):
        if NorenApi is None:
            raise ImportError("NorenRestApiPy is required for Shoonya session initialization")
        super().__init__(
            host="https://api.shoonya.com/NorenWClientAPI/",
            websocket="wss://api.shoonya.com/NorenWS/",
        )

    def _NorenApi__ws_run_forever(self):
        while not self._NorenApi__stop_event.is_set():
            try:
                self._NorenApi__websocket.run_forever(
                    ping_interval=3,
                    ping_payload='{"t":"h"}',
                    reconnect=5,
                    sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False},
                )
            except Exception as exc:
                logging.warning("websocket run forever ended in exception, %s", exc)
            else:
                if not self._NorenApi__stop_event.is_set():
                    logging.warning("websocket run_forever returned; reconnecting")
            if not self._NorenApi__stop_event.is_set():
                self._NorenApi__websocket_connected = False
                time.sleep(2)


def _get_oauth_code_with_browser(user_id, password, totp_secret):
    from playwright.sync_api import sync_playwright

    login_url = (
        "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
        f"?api_key={user_id}_U&route_to={user_id}"
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(login_url)
            page.locator("#lgnusrid").fill(user_id)
            page.locator("#lgnpwd").fill(password)
            page.locator("#lgnotp").fill(pyotp.TOTP(totp_secret).now())
            page.get_by_role("button", name="LOGIN").click()
            page.wait_for_url("**?code=*")
            query_params = parse_qs(urlparse(page.url).query)
            return query_params.get("code", [None])[0]
        finally:
            context.close()
            browser.close()


def _resolve_auth_code(user_id):
    auth_code = os.getenv("SSLAGO_SHOONYA_AUTH_CODE", "").strip()
    if auth_code:
        return auth_code

    if not _env_bool("SSLAGO_SHOONYA_AUTO_LOGIN", False):
        return None

    password = os.getenv("SSLAGO_SHOONYA_PASSWORD", "").strip()
    totp_secret = os.getenv("SSLAGO_SHOONYA_TOTP_SECRET", "").strip()
    if not user_id or not password or not totp_secret:
        raise RuntimeError(
            "SSLAGO_SHOONYA_AUTO_LOGIN requires SSLAGO_SHOONYA_USER_ID, "
            "SSLAGO_SHOONYA_PASSWORD, and SSLAGO_SHOONYA_TOTP_SECRET"
        )

    return _get_oauth_code_with_browser(user_id, password, totp_secret)


def create_shoonya_session():
    global cred
    if not cred:
        cred = _load_yaml_credentials()
    user_id = os.getenv("SSLAGO_SHOONYA_USER_ID", "").strip() or cred.get("UID", "")
    secret_code = os.getenv("SSLAGO_SHOONYA_SECRET_CODE", "").strip() or cred.get("Secret_Code", "")

    if not user_id or not secret_code:
        raise RuntimeError(
            "Shoonya is not configured. Set SSLAGO_SHOONYA_USER_ID and "
            "SSLAGO_SHOONYA_SECRET_CODE, or provide SSLAGO_SHOONYA_CREDENTIALS_FILE."
        )

    auth_code = _resolve_auth_code(user_id)
    if not auth_code:
        raise RuntimeError(
            "Shoonya auth code is missing. Set SSLAGO_SHOONYA_AUTH_CODE, or enable "
            "SSLAGO_SHOONYA_AUTO_LOGIN with password/TOTP env vars."
        )

    api_client = NorenApiPy()
    client_id = f"{user_id}_U"
    result = api_client.getAccessToken(auth_code, secret_code, client_id, user_id)
    if result is None:
        raise RuntimeError("Failed to retrieve Shoonya access token")

    access_token, _usrid, _refresh_token, account_id = result
    cred.update(
        {
            "client_id": client_id,
            "Secret_Code": secret_code,
            "UID": user_id,
            "oauth_url": f"https://api.shoonya.com/NorenWClientAPI/authenticate/{client_id}",
            "Access_token": access_token,
            "Account_ID": account_id,
        }
    )
    api_client.injectOAuthHeader(access_token, user_id, account_id)
    return api_client, api_client, None


class DeferredTrader:
    def __init__(self):
        self._ready = threading.Event()
        self._trader = None
        self._error = None

    def initialize(self, factory):
        def _runner():
            try:
                trader_instance = factory()
                trader_instance.real = True
                self._trader = trader_instance
            except Exception as exc:
                self._error = exc
                logging.exception("Trader initialization failed")
            finally:
                self._ready.set()

        threading.Thread(target=_runner, daemon=True).start()

    def mark_unavailable(self, exc):
        self._error = exc
        self._ready.set()

    def wait_ready(self, timeout=None):
        return self._ready.wait(timeout)

    def __getattr__(self, name):
        self._ready.wait()
        if self._error is not None:
            raise RuntimeError("Trader initialization failed") from self._error
        return getattr(self._trader, name)


api = None
api1 = None
sessionusertoken = None

trader = DeferredTrader()

if _env_bool("SSLAGO_ENABLE_TRADER_ON_IMPORT", False):
    try:
        from connectors.connector import Exchange

        get_database()
        api, api1, sessionusertoken = create_shoonya_session()
    except Exception as exc:
        trader.mark_unavailable(exc)
    else:
        trader.initialize(lambda: Exchange(api, db, cred, api1, sessionusertoken))
else:
    trader.mark_unavailable(
        RuntimeError("Trader import-time initialization is disabled by SSLAGO_ENABLE_TRADER_ON_IMPORT")
    )
