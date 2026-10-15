#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$ROOT_DIR/backend"
PYTHON="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then echo 'Create .venv and install dependencies first.'; exit 1; fi

echo "==> Running FreshRoute: LastWindow Verification & Test Suite..."
"$PYTHON" "$ROOT_DIR/backend/tests/run_all_tests.py"
(cd "$ROOT_DIR/frontend" && npm run build)

echo "==> Starting FastAPI Backend Server on http://127.0.0.1:8000 ..."
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
