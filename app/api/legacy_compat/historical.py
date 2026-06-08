from app.api.legacy_compat.common import *


def api_historicalbacktest(
    start_date: str = Query("", alias="start_date"),
    date: str = Query("", alias="date"),
    end_date: str = Query(""),
    user=Depends(get_current_user),
):
    selected_start, selected_end, start_ts, end_ts = parse_date_range(start_date or date, end_date)
    history, pnl = historical_rows(current_username(user), start_ts, end_ts, include_pnl=True)
    return response("Successfully Fetched User History", {
        "history": history,
        "selected_start_date": selected_start,
        "selected_end_date": selected_end,
        "pnl": pnl,
    })


def api_mainhistoricalbacktest(
    start_date: str = Query("", alias="start_date"),
    date: str = Query("", alias="date"),
    end_date: str = Query(""),
    _user=Depends(get_current_user),
):
    selected_start, selected_end, start_ts, end_ts = parse_date_range(start_date or date, end_date)
    history = historical_rows("kinguniverse129", start_ts, end_ts)
    return response("Successfully Fetched Main History", {
        "history": history,
        "selected_start_date": selected_start,
        "selected_end_date": selected_end,
    })


router = APIRouter(tags=["legacy historical"])

router.add_api_route("/api_historicalbacktest", api_historicalbacktest, methods=["POST"], response_model=ApiResponse)
router.add_api_route("/api_mainhistoricalbacktest", api_mainhistoricalbacktest, methods=["GET"], response_model=ApiResponse)
