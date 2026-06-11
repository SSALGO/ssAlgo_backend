web: uvicorn fastapi_app:app --host 0.0.0.0 --port ${PORT:-8000}
worker: python -m app.workers.trading_worker_main
