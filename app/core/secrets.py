import base64
import hashlib
import hmac
import os
from typing import Any

from app.core.config import AppConfig


ENCRYPTED_PREFIX = "enc:v1:"


def _key_bytes() -> bytes:
    configured = AppConfig.CREDENTIAL_ENCRYPTION_KEY
    if AppConfig.ENVIRONMENT in {"prod", "production"} and not configured:
        raise RuntimeError("SSLAGO_CREDENTIAL_ENCRYPTION_KEY is required in production")
    seed = configured or "sslago-development-credential-key"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    output = b""
    counter = 0
    while len(output) < length:
        counter += 1
        output += hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
    return output[:length]


def encrypt_secret(value: Any) -> Any:
    if value is None or value == "":
        return value
    if isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
        return value
    plaintext = str(value).encode("utf-8")
    key = _key_bytes()
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in zip(plaintext, _keystream(key, nonce, len(plaintext))))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    payload = base64.urlsafe_b64encode(nonce + tag + cipher).decode("ascii")
    return ENCRYPTED_PREFIX + payload


def decrypt_secret(value: Any) -> Any:
    if not (isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX)):
        return value
    raw = base64.urlsafe_b64decode(value[len(ENCRYPTED_PREFIX):].encode("ascii"))
    nonce, tag, cipher = raw[:16], raw[16:32], raw[32:]
    key = _key_bytes()
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Encrypted secret integrity check failed")
    plaintext = bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher))))
    return plaintext.decode("utf-8")


def encrypt_secret_fields(values: dict, secret_fields: set[str]) -> dict:
    encrypted = dict(values or {})
    for field_name in secret_fields:
        if field_name in encrypted:
            encrypted[field_name] = encrypt_secret(encrypted[field_name])
    return encrypted


def decrypt_secret_fields(values: dict, secret_fields: set[str]) -> dict:
    decrypted = dict(values or {})
    for field_name in secret_fields:
        if field_name in decrypted:
            decrypted[field_name] = decrypt_secret(decrypted[field_name])
    return decrypted
