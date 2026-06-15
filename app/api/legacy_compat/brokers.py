from app.api.legacy_compat.common import *


ALICEBLUE_FORBIDDEN_SECRET_FIELDS = {
    "alice_password",
    "password",
    "pwd",
    "totp_key",
    "totp_secret",
    "apisecret",
    "api_secret",
    "secret_key",
    "app_secret",
}


def reject_aliceblue_password_totp(data):
    broker = str(data.get("broker") or "").strip().lower()
    if broker not in {"aliceblue", "alice"}:
        return
    if any(str(data.get(field) or "").strip() for field in ALICEBLUE_FORBIDDEN_SECRET_FIELDS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "AliceBlue password/TOTP credential storage is disabled. "
                "Use Connect AliceBlue redirect login."
            ),
        )


def api_apis(_admin=Depends(require_admin)):
    data = [clean_document(doc, mask_secrets=True) for doc in collection("apis").find({})]
    return response("Fetched Successfully APIs", data)


def api_get_api(user=Depends(get_current_user)):
    api = collection("apis").find_one({"user": current_username(user)})
    if not api:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")
    return response("Fetched Successfully API", clean_document(api, mask_secrets=True))


async def api_update_api(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    api_id = form_value(payload, "id")
    data = {
        "apikey": form_value(payload, "apikey"),
        "apisecret": form_value(payload, "apisecret"),
        "user": form_value(payload, "user") or form_value(payload, "token"),
    }
    if "auth_code" in payload:
        data["auth_code"] = form_value(payload, "auth_code")
    collection("apis").update_one({"_id": object_id(api_id)}, {"$set": encrypted_secret_update(data)})
    audit_event("broker_credentials_admin_updated", user=data.get("user"), resource_type="broker_api", resource_id=api_id, actor=_admin.get("username"))
    return response("Fetched Successfully Updated API")


async def api_add_apikey(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    data = flat_form(payload)
    data["user"] = current_username(user)
    data.pop("token", None)
    reject_aliceblue_password_totp(data)
    inserted_id = collection("apis").insert_one(encrypted_secret_update(data)).inserted_id
    audit_event("broker_credentials_created", user=current_username(user), resource_type="broker_api", resource_id=inserted_id, details={"broker": data.get("broker")})
    return response("API key added successfully", {"id": str(inserted_id)})


async def api_edit_apikey(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    data = flat_form(payload)
    data["user"] = current_username(user)
    api_id = data.pop("id", "")
    data.pop("token", None)
    reject_aliceblue_password_totp(data)
    query = {"_id": object_id(api_id), "user": current_username(user)} if api_id else {"user": current_username(user), "broker": data.get("broker")}
    result = collection("apis").update_one(query, {"$set": encrypted_secret_update(data)}, upsert=not api_id)
    if api_id and result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")
    audit_event("broker_credentials_updated", user=current_username(user), resource_type="broker_api", resource_id=api_id or result.upserted_id, details={"broker": data.get("broker")})
    return response("API key updated successfully", {
        "matched": result.matched_count,
        "modified": result.modified_count,
        "upserted_id": str(result.upserted_id) if result.upserted_id else None,
    })


async def api_multi_api(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    operation = str(form_value(payload, "operation", "get")).lower()
    broker = form_value(payload, "broker")
    if not broker:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="broker is required")
    query = {"user": current_username(user), "broker": broker}
    if operation == "get":
        api = collection("apis").find_one(query)
        if not api:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API not found")
        return response("Fetched Successfully API", clean_document(api, mask_secrets=True))
    if operation == "update":
        data = flat_form(payload)
        data["user"] = current_username(user)
        data.pop("token", None)
        data.pop("operation", None)
        reject_aliceblue_password_totp(data)
        result = collection("apis").update_one(query, {"$set": encrypted_secret_update(data)}, upsert=True)
        message = "Successfully Created API" if result.upserted_id else "Successfully Updated API"
        audit_event("broker_credentials_updated", user=current_username(user), resource_type="broker_api", resource_id=result.upserted_id or broker, details={"broker": broker, "operation": operation})
        return response(message, {
            "matched": result.matched_count,
            "modified": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        })
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid operation: {operation}")


async def api_broker_multi_api(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    username = current_username(user)
    registry = broker_payload()
    broker_data = collection("broker").find_one({"user": username})
    current_broker = (broker_data or {}).get("selectedbroker", "aliceblue")
    if not broker_data:
        collection("broker").update_one({"user": username}, {"$set": {"user": username, "selectedbroker": current_broker}}, upsert=True)
    requested_broker = form_value(payload, "selectedbroker")
    data = {
        "broker_requirements": registry["broker_requirements"],
        "broker_actions": registry["broker_actions"],
        "broker_display_names": registry["broker_display_names"],
        "broker_status": registry["broker_status"],
        "current_broker": current_broker,
    }
    if requested_broker and requested_broker in registry["broker_requirements"]:
        data = {
            "broker_requirements": {requested_broker: registry["broker_requirements"][requested_broker]},
            "broker_actions": {requested_broker: registry["broker_actions"].get(requested_broker, {})},
            "broker_display_names": {requested_broker: registry["broker_display_names"].get(requested_broker, requested_broker)},
            "broker_status": {requested_broker: registry["broker_status"].get(requested_broker, {})},
            "current_broker": current_broker,
        }
    return response("Successfully fetched broker requirements", data)


async def api_delete_api(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    api_id = form_value(payload, "id")
    result = collection("apis").delete_one({"_id": object_id(api_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API ID not found")
    return response("API Key Deleted Successfully")


router = APIRouter(tags=["legacy brokers"])

router.add_api_route("/api_apis", api_apis, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_get_api", api_get_api, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_update_api", api_update_api, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_add_apikey", api_add_apikey, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_add_multi_apikey", api_add_apikey, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_edit_apikey", api_edit_apikey, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_edit_multi_apikey", api_edit_apikey, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_multi_api", api_multi_api, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_broker_multi_api", api_broker_multi_api, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_api", api_delete_api, methods=["POST"], response_model=ApiResponse)
