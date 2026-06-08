from fastapi import APIRouter, Depends

from app.api.fastapi_auth import require_admin
from app.api.fastapi_schemas import ApiResponse
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
def worker_command(
    command: str,
    _admin=Depends(require_admin),
    control: WorkerControlService = Depends(get_worker_control),
):
    queued = control.enqueue(command, _admin["username"])
    return ApiResponse(success=True, message="Worker command queued", data=queued)
