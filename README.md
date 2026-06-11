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

## Run Trading Runtime

The API only changes strategy configuration and status. Signal evaluation and
order execution require the trading runtime in a second process:

```powershell
$env:SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE = "true"
.\venv\Scripts\python.exe -m app.workers.trading_worker_main
```

On Windows, `runbot.bat` starts both the trading runtime and the API.

## Required Production Secrets

Set these before production startup:

- `SSLAGO_JWT_SECRET_KEY`
- `SSLAGO_FLASK_SECRET_KEY`
- `SSLAGO_CREDENTIAL_ENCRYPTION_KEY`
- `SSLAGO_MONGO_URI`
- `SSLAGO_RAZORPAY_KEY_ID`
- `SSLAGO_RAZORPAY_KEY_SECRET`

Live trading remains blocked unless explicitly enabled through both environment and user risk settings.
