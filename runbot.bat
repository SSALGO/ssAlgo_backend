@ECHO OFF
SETLOCAL

IF EXIST venv\Scripts\activate.bat (
    CALL venv\Scripts\activate.bat
)

python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
ENDLOCAL
