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

On Ubuntu:

```bash
cd ~/ssAlgo_backend
source venv/bin/activate
export PYTHONUNBUFFERED=1
export SSLAGO_ENABLE_LEGACY_STRATEGY_ENGINE=true
export DEBUG_TRADING=true
python -m app.workers.trading_worker_main
```

Startup now fails with a non-zero exit if MongoDB cannot be reached or the
legacy strategy engine cannot initialize. It also logs every database position
with `status: open` because those positions remain under exit management even
when the corresponding strategy is paused.

The worker, command queue, broker health services, and legacy strategy engine
share the same database object created from `SSLAGO_MONGO_URI` and
`SSLAGO_MONGO_DB`. Startup logs the Mongo host and database name without
printing credentials.

## Trading Debug Mode

Enable structured decision, risk, feed, and broker logs while diagnosing a
strategy:

```powershell
$env:DEBUG_TRADING = "true"
.\venv\Scripts\python.exe -m app.workers.trading_worker_main
```

Debug logging masks configured credential and token fields. Disable it after
the investigation because per-strategy decision logs can be verbose.

Logs are written to `logs/trading_debug.log` and also printed in the trading
worker console. Override the file path with `DEBUG_TRADING_LOG_FILE`.

## Required Production Secrets

Set these before production startup:

- `SSLAGO_JWT_SECRET_KEY`
- `SSLAGO_FLASK_SECRET_KEY`
- `SSLAGO_CREDENTIAL_ENCRYPTION_KEY`
- `SSLAGO_MONGO_URI`
- `SSLAGO_RAZORPAY_KEY_ID`
- `SSLAGO_RAZORPAY_KEY_SECRET`

Live trading remains blocked unless explicitly enabled through both environment and user risk settings.
