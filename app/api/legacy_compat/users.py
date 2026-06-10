from app.api.legacy_compat.common import *
from app.domain.auth.reset_service import (
    MAX_OTP_ATTEMPTS,
    create_otp,
    create_reset_token,
    hash_password,
    hash_token,
    verify_otp_hash,
    verify_reset_token,
)
from app.domain.auth.notifications import send_security_email


async def api_user_profile(request: Request, user=Depends(get_current_user)):
    username = current_username(user)
    if request.method == "POST":
        payload = await payload_from_request(request)
        limit_fields = ("day_profit_limit", "day_loss_limit", "trade_limit")
        updates = {}
        for field_name in limit_fields:
            if field_name not in payload:
                continue
            value = str(form_value(payload, field_name)).strip()
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} must be a number",
                )
            if numeric_value < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{field_name} cannot be negative",
                )
            updates[field_name] = str(int(numeric_value)) if numeric_value.is_integer() else str(numeric_value)

        if updates:
            collection("users").update_one({"username": username}, {"$set": updates})
            user = collection("users").find_one({"username": username}) or {**user, **updates}

    profile = clean_document(user, hide_password=True) or {}
    profile.pop("_id", None)
    profile.setdefault("day_profit_limit", "25000")
    profile.setdefault("day_loss_limit", "25000")
    profile.setdefault("trade_limit", "100")
    sub = collection("subscriptionperiod").find_one({"user": username})
    profile["end"] = sub.get("end", "None") if sub else "None"
    profile["subtype"] = sub.get("subtype", "None") if sub else "None"
    profile["StrategyRemaining"] = int(profile.get("StrategyLimit", 10)) - active_strategy_units(username)
    return response("Complete User Profile", {key: str(value) for key, value in profile.items()})


def api_users(_admin=Depends(require_admin)):
    users = []
    for user in collection("users").find({}):
        if "StrategyLimit" not in user:
            user["StrategyLimit"] = 10
            collection("users").update_one({"username": user["username"]}, {"$set": {"StrategyLimit": 10}})
        cleaned = clean_document(user, hide_password=True)
        cleaned.pop("_id", None)
        users.append(cleaned)
    return response("Users fetched successfully", users)


async def api_update_user(user_id: str, request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    data = {
        "username": form_value(payload, "username"),
        "email": form_value(payload, "email"),
        "mobile": form_value(payload, "mobile"),
        "StrategyLimit": form_value(payload, "StrategyLimit", 10),
    }
    collection("users").update_one({"_id": object_id(user_id, "user_id")}, {"$set": data})
    return response("updated Successfully User", data)


def api_delete_user(user_id: str, _admin=Depends(require_admin)):
    result = collection("users").delete_one({"_id": object_id(user_id, "user_id")})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return response("Successfully Deleted User")


async def api_forgot_reset_password(request: Request):
    payload = await payload_from_request(request)
    email = str(form_value(payload, "email")).lower()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required.")
    user = collection("users").find_one({"email": email})
    if user:
        reset_token, reset_hash, expiration = create_reset_token()
        collection("users").update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "reset_token_hash": reset_hash,
                    "reset_token_expiration": expiration,
                    "reset_token_used": False,
                    "reset_requested_at": datetime.datetime.utcnow(),
                },
                "$unset": {"reset_token": ""},
            },
        )
        collection("security_audit").insert_one({
            "user": user.get("username"),
            "email": email,
            "event": "password_reset_requested",
            "created_at": datetime.datetime.utcnow(),
        })
        audit_event("password_reset_requested", user=user.get("username"), resource_type="user", resource_id=user.get("username"))
        sent = send_security_email(
            email,
            "ssAlgo password reset",
            f"Use this password reset token within 30 minutes: {reset_token}",
        )
        collection("security_audit").insert_one({
            "user": user.get("username"),
            "email": email,
            "event": "password_reset_email_sent" if sent else "password_reset_email_not_configured",
            "created_at": datetime.datetime.utcnow(),
        })
    return response("If the email exists, password reset instructions have been sent.")


async def api_reset_password(reset_token: str, request: Request):
    user = collection("users").find_one({"reset_token_hash": hash_token(reset_token)})
    if not verify_reset_token(user, reset_token):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token.")
    if request.method == "GET":
        return response("Reset token is valid. You can now reset your password.")
    payload = await payload_from_request(request)
    new_password = form_value(payload, "new_password")
    confirm_password = form_value(payload, "confirm_password")
    if not new_password or new_password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")
    try:
        password_hash = hash_password(new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    collection("users").update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password": password_hash,
                "reset_token_used": True,
                "password_reset_at": datetime.datetime.utcnow(),
            },
            "$unset": {
                "reset_token": "",
                "reset_token_hash": "",
                "reset_token_expiration": "",
            },
        },
    )
    audit_event("otp_password_reset_completed", user=user.get("username"), resource_type="user", resource_id=user.get("username"))
    return response("Your password has been successfully reset. You can now log in with your new password.")


async def api_forgot_otp_reset_password(request: Request):
    payload = await payload_from_request(request)
    email = str(form_value(payload, "email")).lower()
    user = collection("users").find_one({"email": email})
    if user:
        otp, otp_hash, expiration = create_otp()
        collection("users").update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "otp_hash": otp_hash,
                    "otp_expiration": expiration,
                    "otp_attempts": 0,
                    "otp_requested_at": datetime.datetime.utcnow(),
                },
                "$unset": {"otp": ""},
            },
        )
        collection("security_audit").insert_one({
            "user": user.get("username"),
            "email": email,
            "event": "otp_reset_requested",
            "created_at": datetime.datetime.utcnow(),
        })
        audit_event("otp_reset_requested", user=user.get("username"), resource_type="user", resource_id=user.get("username"))
        sent = send_security_email(
            email,
            "ssAlgo password reset OTP",
            f"Use this OTP within 10 minutes: {otp}",
        )
        collection("security_audit").insert_one({
            "user": user.get("username"),
            "email": email,
            "event": "otp_reset_email_sent" if sent else "otp_reset_email_not_configured",
            "created_at": datetime.datetime.utcnow(),
        })
    return response("If the email exists, OTP reset instructions have been sent.")


def verify_otp(email, otp):
    user = collection("users").find_one({"email": str(email).lower()})
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP.")
    ok, message = verify_otp_hash(user, otp)
    if not ok:
        update = {"$set": {"last_otp_failure_at": datetime.datetime.utcnow()}}
        if int(user.get("otp_attempts") or 0) < MAX_OTP_ATTEMPTS:
            update["$inc"] = {"otp_attempts": 1}
        collection("users").update_one({"_id": user["_id"]}, update)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return user


async def api_otp_verify(request: Request):
    payload = await payload_from_request(request)
    verify_otp(form_value(payload, "email"), form_value(payload, "otp"))
    return response("Your OTP has been successfully Matched.")


async def api_otp_reset_password(request: Request):
    payload = await payload_from_request(request)
    user = verify_otp(form_value(payload, "email"), form_value(payload, "otp"))
    new_password = form_value(payload, "new_password")
    confirm_password = form_value(payload, "confirm_password")
    if not new_password or new_password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")
    try:
        password_hash = hash_password(new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    collection("users").update_one(
        {"_id": user["_id"]},
        {
            "$set": {"password": password_hash, "password_reset_at": datetime.datetime.utcnow()},
            "$unset": {"otp": "", "otp_hash": "", "otp_expiration": "", "otp_attempts": ""},
        },
    )
    audit_event("password_reset_completed", user=user.get("username"), resource_type="user", resource_id=user.get("username"))
    return response("Your password has been successfully reset. You can now log in with your new password.")


router = APIRouter(tags=["legacy users"])

router.add_api_route("/api_user_profile", api_user_profile, methods=["GET", "POST"], response_model=ApiResponse)
router.add_api_route("/api_users", api_users, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_update_user/{user_id}", api_update_user, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_user/{user_id}", api_delete_user, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_forgot_reset_password", api_forgot_reset_password, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_reset_password/{reset_token}", api_reset_password, methods=["GET", "POST"], response_model=ApiResponse)
router.add_api_route("/api_forgot_otp_reset_password", api_forgot_otp_reset_password, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_otp_verify", api_otp_verify, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_otp_reset_password", api_otp_reset_password, methods=["POST"], response_model=ApiResponse)
