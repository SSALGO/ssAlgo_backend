"""Read-only AliceBlue session generation test for a saved broker account.

This script loads the saved AliceBlue auth_code/user id for a user, generates
the AliceBlue checksum using the backend app secret, and calls getUserDetails.
It does not write the generated session back to MongoDB.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BACKEND_ROOT / ".env")

from app.core.config import AppConfig  # noqa: E402
from app.core.database import get_database  # noqa: E402
from app.core.logging_config import mask_value  # noqa: E402
from app.core.secrets import decrypt_secret_fields  # noqa: E402
from app.domain.brokers.aliceblue_auth import (  # noqa: E402
    AliceBlueSessionExchangeError,
    exchange_auth_code_for_session,
)
from app.domain.brokers.health import SECRET_FIELD_NAMES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a fresh AliceBlue userSession from saved auth_code."
    )
    parser.add_argument("--user", default="sjguptha", help="SSALGO username")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    args = parser.parse_args()

    app_secret = str(AppConfig.ALICEBLUE_APP_SECRET or "").strip()
    if not app_secret:
        print(
            "ERROR: Missing SSLAGO_ALICEBLUE_APP_SECRET or ALICEBLUE_APP_SECRET. "
            "Cannot generate AliceBlue checksum without apiSecret."
        )
        return 2

    db = get_database()
    row = db["apis"].find_one(
        {"user": args.user, "broker": {"$in": ["aliceblue", "alice"]}}
    )
    if not row:
        print(f"ERROR: No AliceBlue API row found for user={args.user!r}")
        return 3

    values = decrypt_secret_fields(dict(row), SECRET_FIELD_NAMES)
    alice_user_id = str(
        values.get("apikey") or values.get("alice_client_id") or ""
    ).strip()
    auth_code = str(values.get("auth_code") or "").strip()

    missing = [
        name
        for name, value in (
            ("userId/apikey", alice_user_id),
            ("auth_code", auth_code),
        )
        if not value
    ]
    if missing:
        print(
            "ERROR: Saved AliceBlue row is missing "
            + ", ".join(missing)
            + ". Reconnect AliceBlue first."
        )
        print(
            "Found row fields: "
            + ", ".join(sorted(str(key) for key in values.keys() if not key.startswith("_")))
        )
        return 4

    print("Testing AliceBlue session generation")
    print(f"SSALGO user: {args.user}")
    print(f"AliceBlue userId: {alice_user_id}")
    print(f"authCode: {mask_value(auth_code)}")
    print(f"apiSecret source: environment ({mask_value(app_secret)})")

    try:
        session = exchange_auth_code_for_session(
            alice_user_id,
            auth_code,
            app_secret,
            timeout=args.timeout,
        )
    except AliceBlueSessionExchangeError as exc:
        print(f"FAILED: {exc}")
        return 5

    session_id = session.get("session_id")
    print("SUCCESS: AliceBlue returned userSession")
    print(f"userSession: {mask_value(session_id)}")
    print("DB write: skipped (read-only test)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
