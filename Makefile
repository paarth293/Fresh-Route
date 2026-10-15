.PHONY: install test test-unit test-integration benchmark run-backend run-worker run-frontend

install:
	cd backend && pip install -r requirements.lock
	cd frontend && npm ci

test:
	PYTHONPATH=backend python3 backend/tests/run_all_tests.py

benchmark:
	PYTHONPATH=backend python3 scripts/run_benchmarks.py

export-schema:
	PYTHONPATH=backend python3 scripts/export_schema.py

run-backend:
	PYTHONPATH=backend uvicorn app.main:app --host 127.0.0.1 --port 8000

run-frontend:
	cd frontend && npm run dev
