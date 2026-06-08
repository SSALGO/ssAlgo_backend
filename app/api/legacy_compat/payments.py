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
    amount, days = price_plan(form_value(payload, "price"))
    client = require_razorpay_client()
    payment = client.order.create(data={"amount": amount, "currency": "INR", "receipt": "#11"})
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
    duration = form_value(payload, "duration")
    if not all(params.values()) or not duration:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment id, order id, signature, and duration are required")
    client = require_razorpay_client()
    verified = client.utility.verify_payment_signature(params)
    collection("payreceipt").insert_one({
        "time": datetime.datetime.now(),
        "user": current_username(user),
        "order_id": params["razorpay_order_id"],
        "payment_id": params["razorpay_payment_id"],
        "status": verified,
    })
    subscription = extend_subscription(current_username(user), duration)
    return response("Payment verified successfully", {"subscription": clean_document(subscription)})


def api_pay_fail(_user=Depends(get_current_user)):
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment couldn't go through and failed due to some reason.")


router = APIRouter(tags=["legacy payments"])

router.add_api_route("/api_pricing", api_pricing, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay", api_pay, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay_verify", api_pay_verify, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_pay_fail", api_pay_fail, methods=["POST"], response_model=ApiResponse)
