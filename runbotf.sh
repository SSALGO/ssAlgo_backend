#!/bin/bash
set -euo pipefail

if [ -f "venv/bin/activate" ]; then
  . "venv/bin/activate"
fi

python -m uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
