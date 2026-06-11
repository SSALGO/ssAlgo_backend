@ECHO OFF
SETLOCAL

IF EXIST venv\Scripts\activate.bat (
    CALL venv\Scripts\activate.bat
)

SET SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE=true
START "ssAlgo Trading Worker" /B python -m app.workers.trading_worker_main
python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
ENDLOCAL
