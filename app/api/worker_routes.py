from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from app.api.fastapi_auth import require_admin
from app.api.fastapi_schemas import ApiResponse, WorkerOrderRequest
from app.domain.audit.service import AuditLogService
from app.api.fastapi_services import FastAPITradingServices, get_trading_services
from app.workers.control import WorkerControlService


worker_router = APIRouter(prefix="/api/worker", tags=["worker"])


def get_worker_control(services: FastAPITradingServices = Depends(get_trading_services)):
    return WorkerControlService(services.db)


@worker_router.get("/status", response_model=ApiResponse)
def worker_status(
    _admin=Depends(require_admin),
    control: WorkerControlService = Depends(get_worker_control),
):
    return ApiResponse(success=True, message="Worker status fetched", data=control.get_status())


@worker_router.post("/start", response_model=ApiResponse)
def worker_start(
    _admin=Depends(require_admin),
    control: WorkerControlService = Depends(get_worker_control),
):
    command = control.enqueue("start", _admin["username"])
    return ApiResponse(success=True, message="Worker start command queued", data=command)


@worker_router.post("/stop", response_model=ApiResponse)
def worker_stop(
    _admin=Depends(require_admin),
    control: WorkerControlService = Depends(get_worker_control),
):
    command = control.enqueue("stop", _admin["username"])
    return ApiResponse(success=True, message="Worker stop command queued", data=command)


@worker_router.post("/commands/{command}", response_model=ApiResponse)
async def worker_command(
    command: str,
    request: Request,
    _admin=Depends(require_admin),
    control: WorkerControlService = Depends(get_worker_control),
):
    payload = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
        payload = body if isinstance(body, dict) else {}
    else:
        form = await request.form()
        payload = {key: value for key, value in form.items()}
    if command == "place_order":
        try:
            payload = WorkerOrderRequest(**payload).model_dump()
        except ValidationError as exc:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.errors())
    queued = control.enqueue(command, _admin["username"], payload=payload)
    AuditLogService(control.db).record(
        "worker_command_queued",
        user=str(payload.get("user") or ""),
        actor=_admin["username"],
        resource_type="worker_command",
        resource_id=queued.get("_id"),
        details={"command": command},
    )
    return ApiResponse(success=True, message="Worker command queued", data=queued)
