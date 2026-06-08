from app.api.legacy_compat.common import *


def api_subscription(_admin=Depends(require_admin)):
    data = [clean_document(doc) for doc in collection("subscriptionperiod").find({})]
    return response("Successfully fetched subscription data.", data)


async def api_create_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    data = {
        "user": form_value(payload, "user"),
        "start": form_value(payload, "start"),
        "end": form_value(payload, "end"),
        "subtype": form_value(payload, "subtype"),
    }
    if not all(data.values()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All fields are required.")
    inserted_id = collection("subscriptionperiod").insert_one(data).inserted_id
    return response("Successfully created subscription.", str(inserted_id))


async def api_get_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    subscription_id = form_value(payload, "id")
    subscription = collection("subscriptionperiod").find_one({"_id": object_id(subscription_id)})
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    return response("Successfully fetched subscription.", clean_document(subscription))


async def api_update_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    subscription_id = form_value(payload, "id")
    data = {
        "start": form_value(payload, "start"),
        "end": form_value(payload, "end"),
        "subtype": form_value(payload, "subtype"),
    }
    if not all(data.values()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="All fields are required.")
    result = collection("subscriptionperiod").update_one({"_id": object_id(subscription_id)}, {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found.")
    return response("Successfully updated subscription.")


async def api_delete_subscription(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    result = collection("subscriptionperiod").delete_one({"_id": object_id(form_value(payload, "id"))})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription ID not found")
    return response("Subscription  Deleted Successfully")


router = APIRouter(tags=["legacy subscriptions"])

router.add_api_route("/api_subscription", api_subscription, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_create_subscription", api_create_subscription, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_get_subscription", api_get_subscription, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_update_subscription", api_update_subscription, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_delete_subscription", api_delete_subscription, methods=["POST"], response_model=ApiResponse)
