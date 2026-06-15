import base64
import hashlib
import os
import time
import uuid
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from app.domain.brokers.diagnostics import (
    log_aliceblue_diagnostic,
    response_summary,
)


ALICEBLUE_LOGIN_BASE_URL = "https://ant.aliceblueonline.com/omk/"
ALICEBLUE_LOGIN_ORIGIN = "https://ant.aliceblueonline.com"
ALICEBLUE_SESSION_URL = (
    "https://a3.aliceblueonline.com/open-api/od/v1/vendor/getUserDetails"
)


class AliceBlueDirectAuthError(RuntimeError):
    pass


def _openssl_key_and_iv(passphrase, salt, key_length=32, iv_length=16):
    material = b""
    previous = b""
    while len(material) < key_length + iv_length:
        previous = hashlib.md5(previous + passphrase + salt).digest()
        material += previous
    return material[:key_length], material[key_length:key_length + iv_length]


def encrypt_cryptojs_aes(value, passphrase, salt=None):
    """Match CryptoJS.AES.encrypt(value, passphrase).toString()."""
    salt = salt or os.urandom(8)
    key, iv = _openssl_key_and_iv(
        str(passphrase).encode("utf-8"),
        salt,
    )
    encrypted = AES.new(key, AES.MODE_CBC, iv).encrypt(
        pad(str(value).encode("utf-8"), AES.block_size)
    )
    return base64.b64encode(b"Salted__" + salt + encrypted).decode("ascii")


class AliceBlueDirectAuthenticator:
    def __init__(self, http=None, timeout=20):
        self.http = http or requests.Session()
        self.timeout = timeout

    @staticmethod
    def _message(payload, fallback):
        if not isinstance(payload, dict):
            return fallback
        return str(
            payload.get("message")
            or payload.get("emsg")
            or payload.get("error")
            or fallback
        )

    @staticmethod
    def _first_result(payload):
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result[0]
        return {}

    def _post(self, url, payload, headers, stage):
        response = None
        body = None
        try:
            log_aliceblue_diagnostic(
                "aliceblue_auth_request",
                stage=stage,
                url=url,
                request_payload=payload,
                request_headers=headers,
            )
            response = self.http.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            log_aliceblue_diagnostic(
                "aliceblue_auth_response",
                stage=stage,
                **response_summary(response, body),
            )
        except requests.RequestException as exc:
            if response is not None:
                try:
                    body = response.json()
                except ValueError:
                    body = getattr(response, "text", "")
                log_aliceblue_diagnostic(
                    "aliceblue_auth_response_error",
                    stage=stage,
                    error=str(exc),
                    **response_summary(response, body),
                )
            raise AliceBlueDirectAuthError(
                f"{stage} request failed: {type(exc).__name__}"
            ) from exc
        except ValueError as exc:
            log_aliceblue_diagnostic(
                "aliceblue_auth_non_json_response",
                stage=stage,
                http_status=getattr(response, "status_code", None),
                body=getattr(response, "text", ""),
            )
            raise AliceBlueDirectAuthError(
                f"{stage} returned a non-JSON response"
            ) from exc

        status = str(body.get("status") or body.get("stat") or "").strip().lower()
        if status not in {"ok", "success"}:
            log_aliceblue_diagnostic(
                "aliceblue_auth_rejected",
                stage=stage,
                response_body=body,
            )
            raise AliceBlueDirectAuthError(
                f"{stage} rejected: {self._message(body, 'unknown error')}"
            )
        return body

    @staticmethod
    def _totp_value(totp_secret):
        remaining = 30 - (time.time() % 30)
        if remaining < 3:
            time.sleep(remaining + 0.25)
        return pyotp.TOTP(str(totp_secret).replace(" ", "").strip()).now()

    @staticmethod
    def _authorization_header(user_id, login_result):
        token = str(login_result.get("token") or "").strip()
        if not token:
            raise AliceBlueDirectAuthError(
                "AliceBlue password login did not return an authorization token"
            )
        return f"Bearer {user_id} WEB {token}"

    @staticmethod
    def _parse_redirect(redirect_url, expected_user_id):
        query = parse_qs(urlparse(str(redirect_url or "")).query)
        auth_code = (
            query.get("authCode")
            or query.get("authcode")
            or query.get("code")
            or [""]
        )[0]
        returned_user_id = (
            query.get("userId")
            or query.get("userid")
            or query.get("clientId")
            or [""]
        )[0]
        if not auth_code or not returned_user_id:
            raise AliceBlueDirectAuthError(
                "AliceBlue vendor authorization did not return authCode and userId"
            )
        if returned_user_id.strip().upper() != expected_user_id.strip().upper():
            raise AliceBlueDirectAuthError(
                "AliceBlue vendor authorization returned a different user"
            )
        return auth_code, returned_user_id

    def authenticate(
        self,
        *,
        user_id,
        password,
        totp_secret,
        app_code,
        app_secret,
    ):
        required = {
            "apikey": user_id,
            "alice_password": password,
            "totp_key": totp_secret,
            "app_key": app_code,
            "apisecret": app_secret,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise AliceBlueDirectAuthError(
                f"AliceBlue direct login is missing {', '.join(missing)}"
            )

        user_id = str(user_id).strip()
        app_code = str(app_code).strip()
        common_headers = {
            "Content-Type": "application/json",
            "Origin": ALICEBLUE_LOGIN_ORIGIN,
            "Referer": f"{ALICEBLUE_LOGIN_ORIGIN}/?appcode={app_code}",
            "User-Agent": "Mozilla/5.0 SSALGO-AliceBlue-Auth",
        }

        self._post(
            ALICEBLUE_LOGIN_BASE_URL + "auth/access/verify/user",
            {"userId": user_id},
            common_headers,
            "AliceBlue user verification",
        )
        encryption_body = self._post(
            ALICEBLUE_LOGIN_BASE_URL + "auth/access/client/enckey",
            {"userId": user_id},
            common_headers,
            "AliceBlue encryption-key request",
        )
        encryption_key = self._first_result(encryption_body).get("encKey")
        if not encryption_key:
            raise AliceBlueDirectAuthError(
                "AliceBlue encryption-key request returned no key"
            )

        password_body = self._post(
            ALICEBLUE_LOGIN_BASE_URL + "auth/access/v2/pwd/validate",
            {
                "userId": user_id,
                "userData": encrypt_cryptojs_aes(password, encryption_key),
                "source": "WEB",
            },
            common_headers,
            "AliceBlue password login",
        )
        password_result = self._first_result(password_body)
        authenticated_headers = dict(common_headers)
        authenticated_headers["Authorization"] = self._authorization_header(
            user_id,
            password_result,
        )

        device_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"ssalgo:aliceblue:{user_id.upper()}")
        )
        totp_body = self._post(
            ALICEBLUE_LOGIN_BASE_URL + "auth/access/topt/verify",
            {
                "userId": user_id,
                "totp": self._totp_value(totp_secret),
                "source": "WEB",
                "deviceId": device_id,
                "deviceNumber": device_id,
                "vendor": app_code,
            },
            authenticated_headers,
            "AliceBlue TOTP verification",
        )
        totp_result = self._first_result(totp_body)
        if totp_result.get("token"):
            authenticated_headers["Authorization"] = self._authorization_header(
                user_id,
                totp_result,
            )

        authorization_body = self._post(
            ALICEBLUE_LOGIN_BASE_URL + "auth/sso/vendor/authorize/check",
            {"userId": user_id, "vendor": app_code},
            authenticated_headers,
            "AliceBlue vendor authorization check",
        )
        authorization_result = self._first_result(authorization_body)
        if authorization_result.get("authorized") is False:
            authorization_body = self._post(
                ALICEBLUE_LOGIN_BASE_URL + "auth/sso/vendor/authorize",
                {"userId": user_id, "vendor": app_code},
                authenticated_headers,
                "AliceBlue vendor authorization",
            )
            authorization_result = self._first_result(authorization_body)

        auth_code, returned_user_id = self._parse_redirect(
            authorization_result.get("redirectUrl"),
            user_id,
        )
        checksum = hashlib.sha256(
            f"{returned_user_id}{auth_code}{app_secret}".encode("utf-8")
        ).hexdigest()
        session_body = self._post(
            ALICEBLUE_SESSION_URL,
            {"checkSum": checksum},
            {"Content-Type": "application/json"},
            "AliceBlue session exchange",
        )
        session_id = session_body.get("userSession")
        if not session_id:
            raise AliceBlueDirectAuthError(
                "AliceBlue session exchange returned no userSession"
            )
        return {
            "user_id": returned_user_id,
            "auth_code": auth_code,
            "session_id": session_id,
        }
