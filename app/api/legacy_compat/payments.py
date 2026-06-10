from app.api.legacy_compat.common import *


def api_pricing():
    plans = [
        ["1 Month", 2999, 2999],
        ["3 Months", 9000, 8547],
        ["6 Months", 18000, 16200],
        ["12 Months", 36000, 30600],
        ["LIFETIME", 360000, 99999],
    ]
    return response("Successfully Fetched Pricing Plans", plans)


async def api_pay(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    plan_label = form_value(payload, "price")
    amount, days = price_plan(plan_label)
    client = require_razorpay_client()
    receipt = f"sslago-{current_username(user)}-{int(datetime.datetime.utcnow().timestamp())}"
    payment = client.order.create(data={"amount": amount, "currency": "INR", "receipt": receipt})
    collection("payment_orders").insert_one({
        "user": current_username(user),
        "razorpay_order_id": payment.get("id"),
        "plan_label": str(plan_label),
        "amount": amount,
        "currency": "INR",
        "duration": days,
        "status": "created",
        "created_at": datetime.datetime.utcnow(),
        "receipt": receipt,
    })
    audit_event(
        "payment_order_created",
        user=current_username(user),
        resource_type="payment_order",
        resource_id=payment.get("id"),
        details={"plan_label": str(plan_label), "amount": amount, "duration": days},
    )
    return ApiResponse(
        success=True,
        message="Payment order created",
        data={
            "name": user["username"],
            "email": user.get("email", ""),
            "ph_nm": user.get("mobile", ""),
            "duration": days,
            "payment": payment,
        },
        token=None,
    ).model_copy(update={"data": {
        "name": user["username"],
        "email": user.get("email", ""),
        "ph_nm": user.get("mobile", ""),
        "duration": days,
        "payment": payment,
        "key": AppConfig.RAZORPAY_KEY_ID,
    }})


async def api_pay_verify(request: Request, user=Depends(get_current_user)):
    payload = await payload_from_request(request)
    params = {
        "razorpay_order_id": form_value(payload, "order_id"),
        "razorpay_payment_id": form_value(payload, "payment_id"),
        "razorpay_signature": form_value(payload, "signature"),
    }
    if not all(params.values()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment id, order id, and signature are required")
    payment_order = collection("payment_orders").find_one({
        "user": current_username(user),
        "razorpay_order_id": params["razorpay_order_id"],
    })
    if not payment_order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment order not found")
    if payment_order.get("status") == "verified":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment order has already been verified")
    client = require_razorpay_client()
    verified = client.utility.verify_payment_signature(params)
    if verified is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment signature verification failed")
    payment_fetch = getattr(getattr(client, "payment", None), "fetch", None)
    if callable(payment_fetch):
        fetched_payment = payment_fetch(params["razorpay_payment_id"])
        if int(fetched_payment.get("amount") or 0) != int(payment_order.get("amount") or 0):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment amount mismatch")
        if fetched_payment.get("currency") and fetched_payment.get("currency") != payment_order.get("currency"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment currency mismatch")
    collection("payreceipt").insert_one({
        "time": datetime.datetime.now(),
        "user": current_username(user),
        "order_id": params["razorpay_order_id"],
        "payment_id": params["razorpay_payment_id"],
        "status": verified,
        "amount": payment_order.get("amount"),
        "duration": payment_order.get("duration"),
    })
    collection("payment_orders").update_one(
        {"_id": payment_order["_id"]},
        {"$set": {
            "status": "verified",
            "razorpay_payment_id": params["razorpay_payment_id"],
            "verified_at": datetime.datetime.utcnow(),
        }},
    )
    subscription = extend_subscription(current_username(user), payment_order.get("duration"))
    audit_event(
        "payment_verified",
        user=current_username(user),
        resource_type="payment_order",
        resource_id=params["razorpay_order_id"],
        details={"payment_id": params["razorpay_payment_id"], "duration": payment_order.get("duration")},
    )
    return response("Payment verified successfully", {"subscription": clean_document(subscription)})


def api_pay_fail(_user=Depends(get_current_user)):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment couldn't go through and failed due to some reason.")


router = APIRouter(tags=["legacy payments"])

router.add_api_route("/api_pricing", api_pricing, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay", api_pay, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay_verify", api_pay_verify, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay_fail", api_pay_fail, methods=["POST"], response_model=ApiResponse)
