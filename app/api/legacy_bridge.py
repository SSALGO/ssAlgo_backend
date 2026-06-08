from fastapi import APIRouter, Depends

from app.api.fastapi_auth import require_admin
from app.api.fastapi_schemas import ApiResponse
from app.api.native_legacy_routes import (
    ADD_STRATEGY_ROUTES,
    ADMIN_EDIT_STRATEGY_ROUTES,
    EDIT_STRATEGY_ROUTES,
)


migration_router = APIRouter(prefix="/api/migration", tags=["migration"])


def migrated_route(path, methods=None, domain="legacy"):
    return {
        "path": path,
        "endpoint": path.strip("/").replace("/", "_").replace("{", "").replace("}", ""),
        "methods": methods or ["POST"],
        "domain": domain,
        "status": "fastapi_native",
    }


MIGRATED_LEGACY_ROUTES = [
    migrated_route("/api_login", domain="auth"),
    migrated_route("/api_register", domain="auth"),
    migrated_route("/api_logout", ["GET", "POST"], domain="auth"),
    migrated_route("/api_forgot_reset_password", domain="auth"),
    migrated_route("/api_reset_password/{reset_token}", ["GET", "POST"], domain="auth"),
    migrated_route("/api_forgot_otp_reset_password", domain="auth"),
    migrated_route("/api_otp_verify", domain="auth"),
    migrated_route("/api_otp_reset_password", domain="auth"),
    migrated_route("/api_searchsymbol", ["GET", "POST"], domain="symbols"),
    migrated_route("/api_index", domain="dashboard"),
    migrated_route("/api_delete_oposition", domain="positions"),
    migrated_route("/api_user_profile", ["GET", "POST"], domain="users"),
    migrated_route("/api_users", domain="users"),
    migrated_route("/api_update_user/{user_id}", domain="users"),
    migrated_route("/api_delete_user/{user_id}", domain="users"),
    migrated_route("/api_pricing", domain="payments"),
    migrated_route("/api_pay", domain="payments"),
    migrated_route("/api_pay_verify", domain="payments"),
    migrated_route("/api_pay_fail", domain="payments"),
    migrated_route("/api_historicalbacktest", domain="historical"),
    migrated_route("/api_mainhistoricalbacktest", ["GET"], domain="historical"),
    migrated_route("/api_broker_status", domain="brokers"),
    migrated_route("/api_apis", domain="brokers"),
    migrated_route("/api_strategys", domain="strategies"),
    migrated_route("/api_get_api", domain="brokers"),
    migrated_route("/api_update_api", domain="brokers"),
    migrated_route("/api_add_apikey", domain="brokers"),
    migrated_route("/api_add_multi_apikey", domain="brokers"),
    migrated_route("/api_edit_apikey", domain="brokers"),
    migrated_route("/api_edit_multi_apikey", domain="brokers"),
    migrated_route("/api_multi_api", domain="brokers"),
    migrated_route("/api_broker_multi_api", domain="brokers"),
    migrated_route("/api_delete_api", domain="brokers"),
    migrated_route("/api_admin", domain="admin"),
    migrated_route("/api_subscription", domain="subscriptions"),
    migrated_route("/api_create_subscription", domain="subscriptions"),
    migrated_route("/api_get_subscription", domain="subscriptions"),
    migrated_route("/api_update_subscription", domain="subscriptions"),
    migrated_route("/api_delete_subscription", domain="subscriptions"),
    migrated_route("/api_add_strategy_form", domain="strategies"),
    migrated_route("/api_add_strategy_form/", domain="strategies"),
    migrated_route("/api_edit_strategy_form/{order_time}", domain="strategies"),
    migrated_route("/api_edit_admin_strategy_form/{order_time}", domain="strategies"),
    migrated_route("/api_edit_strategyinput", domain="strategies"),
    migrated_route("/api_edit_strategyinput_form", domain="strategies"),
    migrated_route("/api_stop_ssalgo", domain="strategies"),
    migrated_route("/api_start_ssalgo", domain="strategies"),
    migrated_route("/api_stop_admin_ssalgo", domain="strategies"),
    migrated_route("/api_start_admin_ssalgo", domain="strategies"),
    migrated_route("/api_start_control", domain="admin"),
    migrated_route("/api_stop_control", domain="admin"),
    migrated_route("/api_start_cebuy", domain="admin"),
    migrated_route("/api_start_cesell", domain="admin"),
    migrated_route("/api_start_pebuy", domain="admin"),
    migrated_route("/api_start_pesell", domain="admin"),
    migrated_route("/api_start_strategyco", domain="admin"),
    migrated_route("/api_stop_strategyco", domain="admin"),
    migrated_route("/api_delete_admin_ssalgo", domain="strategies"),
    migrated_route("/api_delete_strategy", domain="strategies"),
    migrated_route("/api_paper_order", domain="paper"),
    migrated_route("/api_order_lifecycle", domain="orders"),
    migrated_route("/api_backtest", domain="backtests"),
]

for route_path in ADD_STRATEGY_ROUTES:
    MIGRATED_LEGACY_ROUTES.append(migrated_route(route_path, domain="strategies"))
for route_path in EDIT_STRATEGY_ROUTES:
    MIGRATED_LEGACY_ROUTES.append(migrated_route(route_path, domain="strategies"))
for route_path in ADMIN_EDIT_STRATEGY_ROUTES:
    MIGRATED_LEGACY_ROUTES.append(migrated_route(route_path, domain="strategies"))
    MIGRATED_LEGACY_ROUTES.append(migrated_route(f"{route_path}/", domain="strategies"))


@migration_router.get("/legacy-routes", response_model=ApiResponse)
def legacy_route_inventory(_admin=Depends(require_admin)):
    routes = sorted(MIGRATED_LEGACY_ROUTES, key=lambda item: item["path"])
    return ApiResponse(
        success=True,
        message="FastAPI-native legacy API routes fetched",
        data={
            "count": len(routes),
            "routes": routes,
            "bridge_removed": True,
            "next_step": "Delete the old Flask module once any non-API template pages are no longer needed.",
        },
    )
