import os
import socket
import time
from typing import Any

import requests


_PUBLIC_IP_CACHE = {
    "ip": None,
    "checked_at": 0.0,
    "error": "",
}


def _cache_ttl_seconds():
    try:
        return max(0, int(os.getenv("SSLAGO_PUBLIC_IP_CACHE_SECONDS", "30")))
    except (TypeError, ValueError):
        return 30


def expected_outbound_ip():
    return (
        os.getenv("SSLAGO_EXPECTED_OUTBOUND_IP")
        or os.getenv("AWS_ELASTIC_IP")
        or os.getenv("ELASTIC_IP")
        or ""
    ).strip()


def current_public_ip(timeout=3):
    now = time.monotonic()
    ttl = _cache_ttl_seconds()
    cached_ip = _PUBLIC_IP_CACHE.get("ip")
    if cached_ip and ttl and now - float(_PUBLIC_IP_CACHE.get("checked_at") or 0) < ttl:
        return cached_ip
    try:
        response = requests.get("https://api.ipify.org", timeout=timeout)
        response.raise_for_status()
        public_ip = response.text.strip()
        _PUBLIC_IP_CACHE.update({"ip": public_ip, "checked_at": now, "error": ""})
        return public_ip
    except Exception as exc:
        _PUBLIC_IP_CACHE.update({"ip": None, "checked_at": now, "error": str(exc)})
        return ""


def outbound_identity(timeout=3) -> dict[str, Any]:
    public_ip = current_public_ip(timeout=timeout)
    expected_ip = expected_outbound_ip()
    return {
        "hostname": socket.gethostname(),
        "public_ip": public_ip,
        "expected_public_ip": expected_ip,
        "matches_expected_public_ip": bool(public_ip and expected_ip and public_ip == expected_ip),
        "public_ip_error": "" if public_ip else _PUBLIC_IP_CACHE.get("error", ""),
    }
