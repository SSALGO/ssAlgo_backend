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
export SSLAGO_ALICEBLUE_DIAGNOSTICS=true
export SSLAGO_EXPECTED_OUTBOUND_IP=3.108.156.143
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

For AliceBlue EC097/IP-restriction debugging, enable AliceBlue diagnostics and
set the expected outbound IP before starting the worker:

```bash
export SSLAGO_ALICEBLUE_DIAGNOSTICS=true
export SSLAGO_EXPECTED_OUTBOUND_IP=3.108.156.143
journalctl -u ssalgo-worker -f | grep -E "trading_worker_outbound_ip|aliceblue_session|legacy_broker_login_result|aliceblue_order_final_request|aliceblue_order_client_response|aliceblue_order_client_exception|EC097"
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

## Zerodha Kite Setup

Set these Kite Connect values on the backend server only. Do not expose the API
secret to the frontend.

```bash
export SSLAGO_KITE_API_KEY=your_kite_api_key
export SSLAGO_KITE_API_SECRET=your_kite_api_secret
export SSLAGO_KITE_REDIRECT_URL=https://YOUR_DOMAIN/api/brokers/kite/callback
export SSLAGO_KITE_POSTBACK_URL=https://YOUR_DOMAIN/api/brokers/kite/postback
```

The backend also accepts `KITE_API_KEY`, `KITE_API_SECRET`, and
`KITE_REDIRECT_URL`, and `KITE_POSTBACK_URL` as aliases.

Configure these values in the Zerodha Kite Developer Console:

- Redirect URL: `https://YOUR_DOMAIN/api/brokers/kite/callback`
- Postback URL: `https://YOUR_DOMAIN/api/brokers/kite/postback`

The redirect/callback endpoint is only for login and token exchange. The
postback endpoint is only for order updates from Zerodha. For live order
placement, whitelist the backend server Elastic IP/static public IP in the Kite
Developer Console. Orders must be sent by the backend API or trading worker;
the frontend should only request login URLs, show broker status, and start/stop
strategies.

Kite access tokens are valid for the trading day. If `tokenDate` is not today's
date, the backend marks the broker token as expired and rejects live orders with
`Kite session expired. Please reconnect Kite.`

Daily instrument sync:

```bash
.\venv\Scripts\python.exe scripts\sync_kite_instruments.py
```

Run this before market open from your scheduler/cron so strategies can resolve
`exchange + tradingsymbol` to Kite `instrument_token`.
