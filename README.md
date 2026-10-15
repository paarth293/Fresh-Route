# FreshRoute: LastWindow

Software decision support for perishable produce distribution (PS03). LastWindow chooses batch quantities, destinations, departure times, vehicles, and permitted price tiers. It shows dispatch deadlines, explains allocations, preserves existing reservations, and compares simulated outcomes under uncertainty.

## Requirements

- Python **3.11**, 64 bit (the audited Windows environment uses 3.11.9).
- Node.js **24 LTS** and npm (the current test tools require Node 22.12+ or 24+).
- Internet access for initial dependency and browser installation.
- On Windows, the Microsoft Visual C++ runtime supported by OR-Tools.

## Setup on Windows

Open PowerShell in this repository and run each command in order:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
Set-Location frontend
npm ci
npx playwright install chromium
Set-Location ..
```

If your organization intercepts HTTPS using a trusted Windows certificate, pip 24 on Python 3.11 supports `--use-feature=truststore`. Keep certificate verification enabled.

Start the application after setup:

```powershell
.\scripts\start.ps1
```

The startup script verifies the backend and builds React before serving both at **http://127.0.0.1:8000**. API documentation is at **http://127.0.0.1:8000/docs**. Stop the server with Ctrl+C.

For a faster restart after verification:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Build the frontend again whenever its source changes. Opening `frontend/dist/index.html` directly is unsupported: the app requires its local API service. The service imports a sample scenario when the UI first opens on a fresh database.

## Setup on Linux or macOS

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
cd frontend
npm ci
npx playwright install chromium
cd ..
bash scripts/start.sh
```

## Verification

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe backend/tests/run_all_tests.py
.\.venv\Scripts\python.exe scripts/verify_scenarios.py
Set-Location frontend
npm run build
npm test
npm run test:e2e
npm run test:e2e:auth
Set-Location ..
.\.venv\Scripts\python.exe scripts/run_benchmarks.py --partition test --world-count 2
.\.venv\Scripts\python.exe scripts/verify_responsiveness.py
```

Browser tests start a separate API server and database in `work/`. Port 8000 must be free. They exercise the actual API and solver: importing scenarios, planning, receipts, exports, replay, commitments, disruptions, replanning, and inspection persistence.

## Demo walkthrough

1. Open the app and use **Generate plan** with the nominal policy.
2. Check forecast economics on Overview and dispatch windows on Dispatch.
3. Open a batch receipt from Inventory and export the plan.
4. Run a simulated replay under Evaluation; distinguish its realized simulation result from the planning forecast.
5. Commit the plan. The displayed plan becomes historical because reserving its trips advances the operating revision.
6. Record a route delay under Disruptions and generate a new plan for remaining stock and capacity. If existing reservations conflict, move an unstarted reservation to a feasible later slot or release it for the displayed cancellation fee, then replan.
7. Start a fresh scenario version by importing a demo JSON file. Rank inspections, simulate an observation, and generate a plan using the updated state.

An imported scenario with an existing ID creates a new version and resets its active operating state. Older plans and snapshots remain stored, but cannot be committed against the new version.

## Architecture and files

| Location | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI entry point and compiled frontend hosting |
| `backend/app/api/routes.py` | Scenario, planning, inspection, commitment, replay, and analysis APIs |
| `backend/app/domain/` | Inventory ledger, event transitions, financial calculations |
| `backend/app/planning/` | Candidate generation, SCIP solver, baselines, deadlines, explanations, recovery |
| `backend/app/simulation/` | Seeded worlds, replay, simulated inspections |
| `backend/app/validation/` | Scenario validation and independent plan validation |
| `backend/app/persistence/database.py` | SQLite, transactions, immutable snapshots, version and idempotency checks |
| `backend/tests/` | Backend unit, integration, and audit regression tests |
| `frontend/src/App.tsx` | Connected React application |
| `frontend/src/api/client.ts` | Real API requests and job result handling |
| `frontend/tests/` | Frontend unit tests and Playwright browser tests |
| `scenarios/` | Eight synthetic demo, development, validation, and test fixtures |
| `configs/` | Planning policy configurations |
| `reports/` | Benchmark outputs and build verification report |

The production frontend is generated from `frontend/src/` using Vite. The prior standalone demo and copied-function frontend tests were retained in ignored `work/` files for reference; they are not application entry points.

## Configuration and persistence

The default database is `data/freshroute.sqlite3`, relative to this repository. Its parent directory is created automatically. Set `FRESHROUTE_DB_PATH` in the environment to override it. The application does not automatically load `.env.example`. `FRESHROUTE_ALLOWED_ORIGINS` controls development frontend CORS origins.

Planning jobs record scenario identity and revision. Commitments verify both inside a transaction. Replay, receipts, fragility, sensitivity, and briefings use the saved planning snapshot. Inspection events persist the observation, elapsed time, cost, history, and posterior weights together.

After a disruption, conflicts between existing commitments appear as explicit warnings. **Move reservation** validates the vehicle interval, receiving window, sale deadline, and nominal freshness before moving an unstarted reservation on the same route. **Release** restores its stock and capacity and charges the displayed cancellation fee exactly once. Loading and dispatched trips stay locked. Some disruptions cannot be repaired while preserving all deadlines. A nominal-only shelf-life observation shifts the previous uncertainty interval by the same amount; explicit low and high values override that interval.

The frontend submits jobs with `Prefer: respond-async`. The API immediately returns a persistent job record, which the client polls while a bounded local queue runs one isolated solver process. **Cancel operation** terminates that process. Queued jobs resume on restart; running work becomes interrupted and must be retried explicitly. Workers monitor their parent and exit if the service disappears. Run one API process per database; this queue is designed for one host, not distributed deployment. Legacy API requests without that preference retain synchronous responses and cooperative cancellation.

For a password-protected shared workspace, set `FRESHROUTE_OPERATOR_PASSWORD` in the process environment before starting the server. Use at least 12 characters and enter the same password at the sign-in screen. Password checks use a salted PBKDF2 digest. Sessions use HttpOnly, SameSite cookies, expire after eight hours, and are revoked on logout or server restart. Failed login attempts are rate limited. HTTPS is required for a shared remote deployment so credentials travel encrypted and cookies use the Secure flag. This is one shared workspace, not separate customer accounts or tenant isolation.

## Evidence and limits

- Economic values are integer paise; quantities are integer units treated as kg in this MVP; time is integer minutes.
- The MIP is optimal only over its generated candidate set when the solver reports optimality. Candidate generation uses a departure grid and, for larger inputs, a bounded two-wave search. It does not establish a globally optimal unrestricted logistics schedule.
- `organizer_baseline` is an illustrative FIFO implementation in this repository, not an organizer-provided or commercial baseline.
- Benchmark data and uncertainty distributions are synthetic. Test fixtures have been inspected during development and no longer constitute an unseen holdout. Field performance and winning a hackathon are not established by these results.
- The route-delay ablation measures pre-dispatch replanning. Separate state-transition tests cover reserved commitments; the ablation does not prove recovery for dispatched vehicles.
- Inspection value estimates reuse a finite simulation ensemble and need independent statistical validation. Observations are simulated. The explicit UI inspection action updates the ledger before replanning; planning jobs reject implicit inspection execution.
- Cold-chain coefficients are illustrative and provide no food-safety certification. The copilot is a deterministic briefing, not an external language model.
- Optional operator authentication protects a shared workspace. Tenant isolation, identity-provider integration, distributed scaling, and production capacity certification remain outside this MVP. Keep the service on loopback unless deployment security has been configured and reviewed.

See `reports/build-verification.md` for the checks and remaining boundaries of the audit.
