# SSLAGO Development Cleanup Notes

## Legacy Connector

- `connectors/connector.py` is a compatibility facade.
- `connectors/exchange.py` holds the current legacy `Exchange` implementation.
- New connector modules define the intended boundaries for future extraction:
  broker sessions, market data, contracts, strategy engine, and order execution.

## Contract Masters

- Contract master files live in `data/contracts/`.
- Code should resolve them through `connectors.contracts.contract_file_path(...)`.
- The resolver falls back to the backend root for older deployments.

## Development Scratch Files

- Manual websocket/login scratch scripts live in `scripts/dev/`.
- They are not part of the runtime startup path.

## Runtime Hygiene

- Virtual environments, caches, logs, and generated build folders should stay out of source control.
- Backend runtime entrypoints remain `fastapi_app:app` for web and
  `python -m app.workers.trading_worker_main` for the worker.
