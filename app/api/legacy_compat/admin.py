from app.api.legacy_compat.common import *


def api_admin(_admin=Depends(require_admin)):
    data = {
        "controls": [clean_document(doc) for doc in collection("admincontrol").find({})],
        "strategyco": [clean_document(doc) for doc in collection("strategyinput").find({})],
    }
    return response("Successfully fetched Admin Page", data)


async def update_admin_control(request: Request, field, value, message, _admin):
    payload = await payload_from_request(request)
    symbol = form_value(payload, "symbol")
    if not symbol:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="symbol is required")
    collection("admincontrol").update_one({"symbol": symbol}, {"$set": {field: value}})
    return response(message)


async def api_start_control(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "controlmode", True, "Successfully started control.", _admin)


async def api_stop_control(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "controlmode", False, "Successfully stopped control.", _admin)


async def api_start_cebuy(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Buytrade", True, "Successfully triggered CE buy.", _admin)


async def api_start_cesell(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Buytrade", False, "Successfully triggered CE sell.", _admin)


async def api_start_pebuy(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Selltrade", True, "Successfully triggered PE buy.", _admin)


async def api_start_pesell(request: Request, _admin=Depends(require_admin)):
    return await update_admin_control(request, "Selltrade", False, "Successfully triggered PE sell.", _admin)


async def api_start_strategyco(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    existing = collection("strategyinput").find_one({"strategy": strategy})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    collection("strategyinput").update_one({"strategy": strategy}, {"$set": {"update": True}})
    return response("Successfully started the strategy.")


async def api_stop_strategyco(request: Request, _admin=Depends(require_admin)):
    payload = await payload_from_request(request)
    strategy = form_value(payload, "strategy")
    existing = collection("strategyinput").find_one({"strategy": strategy})
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    collection("strategyinput").update_one({"strategy": strategy}, {"$set": {"update": False}})
    return response("Successfully stopped the strategy.")


router = APIRouter(tags=["legacy admin"])

router.add_api_route("/api_admin", api_admin, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_control", api_start_control, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_stop_control", api_stop_control, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_cebuy", api_start_cebuy, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_cesell", api_start_cesell, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_pebuy", api_start_pebuy, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_pesell", api_start_pesell, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_start_strategyco", api_start_strategyco, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_stop_strategyco", api_stop_strategyco, methods=["POST"], response_model=ApiResponse)
