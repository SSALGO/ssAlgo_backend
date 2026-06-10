import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.fastapi_auth import require_admin
from app.api.fastapi_schemas import ApiResponse
from app.api.fastapi_services import FastAPITradingServices, get_trading_services
from app.domain.audit.service import AuditLogService
from app.domain.readiness.service import LiveReadinessService
from app.domain.reconciliation.service import BrokerReconciliationService
from app.domain.risk.service import RiskControlService


ops_router = APIRouter(prefix="/api/ops", tags=["ops"])


def parse_date(value: str):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=datetime.UTC)
        return parsed.astimezone(datetime.UTC)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid date: {value}")


@ops_router.get("/audit-logs", response_model=ApiResponse)
def list_audit_logs(
    user: str = "",
    event: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = Query(100, ge=1, le=1000),
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    logs = AuditLogService(services.db).list_events(
        user=user,
        event=event,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
        limit=limit,
    )
    return ApiResponse(success=True, message="Audit logs fetched", data=logs)


@ops_router.get("/audit-logs/export")
def export_audit_logs(
    user: str = "",
    event: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = Query(1000, ge=1, le=10000),
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    csv_payload = AuditLogService(services.db).export_csv(
        user=user,
        event=event,
        date_from=parse_date(date_from),
        date_to=parse_date(date_to),
        limit=limit,
    )
    return Response(
        content=csv_payload,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
    )


@ops_router.post("/audit-logs/prune", response_model=ApiResponse)
def prune_audit_logs(
    days: int = Query(365, ge=30),
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    deleted = AuditLogService(services.db).prune_older_than(days)
    return ApiResponse(success=True, message="Old audit logs pruned", data={"deleted": deleted, "retention_days": days})


@ops_router.post("/brokers/{broker}/smoke-test", response_model=ApiResponse)
def broker_smoke_test(
    broker: str,
    user: str,
    symbol: str = "",
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    result = BrokerReconciliationService(services.db, adapter_factory=services.adapter_factory, audit_service=services.audit).smoke_test(user, broker, symbol)
    return ApiResponse(success=bool(result.get("success")), message="Broker smoke test completed", data=result)


@ops_router.post("/brokers/{broker}/reconcile-positions", response_model=ApiResponse)
def reconcile_broker_positions(
    broker: str,
    user: str,
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    result = BrokerReconciliationService(services.db, adapter_factory=services.adapter_factory, audit_service=services.audit).reconcile_positions(user, broker)
    return ApiResponse(success=bool(result.get("success")), message="Broker position reconciliation completed", data=result)


@ops_router.get("/users/{username}/live-readiness", response_model=ApiResponse)
def live_readiness(
    username: str,
    min_orders: int = Query(10, ge=0),
    min_days: int = Query(2, ge=0),
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    result = LiveReadinessService(services.db).check_user(username, min_orders=min_orders, min_days=min_days)
    return ApiResponse(success=True, message="Live readiness checked", data=result)


@ops_router.post("/users/{username}/risk-settings/live-defaults", response_model=ApiResponse)
def apply_live_risk_defaults(
    username: str,
    _admin=Depends(require_admin),
    services: FastAPITradingServices = Depends(get_trading_services),
):
    settings = RiskControlService(services.db).apply_production_live_defaults(username)
    services.audit.record(
        "risk_live_defaults_applied",
        user=username,
        actor=getattr(_admin, "username", "") or getattr(_admin, "user", "") or "",
        resource_type="risk_settings",
        resource_id=username,
        details={
            "enabled_checks": [
                "market_hours",
                "fresh_quote",
                "duplicate_signal_window",
                "broker_disconnect",
                "daily_order_limit",
                "open_position_limit",
            ],
            "settings": settings,
        },
    )
    return ApiResponse(success=True, message="Production live risk defaults applied", data=settings)
