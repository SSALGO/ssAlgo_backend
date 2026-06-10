# ssAlgo Backend

Target runtime: Python 3.12.

## Setup

```powershell
cd D:\SSLAGO\ssAlgo_backend
.\scripts\setup_backend.ps1
```

If the Python launcher cannot find 3.12, install it first:

```powershell
winget install -e --id Python.Python.3.12
```

## Run Tests

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

## Run API

```powershell
.\venv\Scripts\python.exe -m uvicorn app.main_fastapi:app --reload
```

## Required Production Secrets

Set these before production startup:

- `SSLAGO_JWT_SECRET_KEY`
- `SSLAGO_FLASK_SECRET_KEY`
- `SSLAGO_CREDENTIAL_ENCRYPTION_KEY`
- `SSLAGO_MONGO_URI`
- `SSLAGO_RAZORPAY_KEY_ID`
- `SSLAGO_RAZORPAY_KEY_SECRET`

Live trading remains blocked unless explicitly enabled through both environment and user risk settings.
