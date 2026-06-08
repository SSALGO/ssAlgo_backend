from fastapi import APIRouter

from app.api.legacy_compat.admin import router as admin_router
from app.api.legacy_compat.brokers import router as broker_router
from app.api.legacy_compat.dashboard import router as dashboard_router
from app.api.legacy_compat.historical import router as historical_router
from app.api.legacy_compat.payments import router as payment_router
from app.api.legacy_compat.strategies import router as strategy_router
from app.api.legacy_compat.subscriptions import router as subscription_router
from app.api.legacy_compat.users import router as user_router


native_legacy_router = APIRouter()
native_legacy_router.include_router(user_router)
native_legacy_router.include_router(broker_router)
native_legacy_router.include_router(strategy_router)
native_legacy_router.include_router(payment_router)
native_legacy_router.include_router(subscription_router)
native_legacy_router.include_router(admin_router)
native_legacy_router.include_router(historical_router)
native_legacy_router.include_router(dashboard_router)
