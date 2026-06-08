from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.fastapi_routers import (
    auth_router,
    backtest_router,
    broker_router,
    legacy_router,
    order_router,
    paper_router,
    ws_router,
)
from app.api.legacy_bridge import migration_router
from app.api.native_legacy_routes import native_legacy_router
from app.api.worker_routes import worker_router
from app.core.config import AppConfig
from app.core.database import get_database


app = FastAPI(
    title="ssAlgo API",
    version="0.1.0",
    description="FastAPI strangler API for ssAlgo trading services.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=AppConfig.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    db = get_database()
    db.command("ping")
    return {"success": True, "message": "ok", "database": db.name}


app.include_router(auth_router)
app.include_router(broker_router)
app.include_router(paper_router)
app.include_router(order_router)
app.include_router(backtest_router)
app.include_router(worker_router)
app.include_router(legacy_router)
app.include_router(native_legacy_router)
app.include_router(ws_router)
app.include_router(migration_router)
