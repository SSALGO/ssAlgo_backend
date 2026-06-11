@ECHO OFF
SETLOCAL

SET "PYTHON_EXE=%CD%\venv\Scripts\python.exe"

IF NOT EXIST "%PYTHON_EXE%" (
    ECHO ERROR: Backend virtual environment is missing.
    ECHO Run .\scripts\setup_backend.ps1 first.
    EXIT /B 1
)

"%PYTHON_EXE%" --version >NUL 2>&1
IF ERRORLEVEL 1 (
    ECHO ERROR: The backend virtual environment is broken.
    ECHO Reinstall Python 3.12 and run .\scripts\setup_backend.ps1.
    EXIT /B 1
)
FOR /F "delims=" %%V IN ('"%PYTHON_EXE%" --version 2^>^&1') DO SET "PYTHON_VERSION=%%V"

IF NOT EXIST logs MKDIR logs
SET SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE=true
SET DEBUG_TRADING=true

ECHO Starting trading worker with %PYTHON_VERSION%...
START "ssAlgo Trading Worker" /B "%PYTHON_EXE%" -m app.workers.trading_worker_main >> logs\trading_worker.log 2>&1

TIMEOUT /T 3 /NOBREAK >NUL
ECHO Starting API...
"%PYTHON_EXE%" -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
ENDLOCAL
