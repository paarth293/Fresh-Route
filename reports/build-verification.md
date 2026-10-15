# FreshRoute: LastWindow — build verification

Verified on **15 October 2026**, on Windows, using Python **3.11.9**, Node **24.18.0**, OR-Tools **9.14.6206**, SCIP **9.2.2**, Vite **8.3.4**, and Vitest **5.0.5**.

## Result

All checks listed below pass after the repairs. The source frontend builds into the application actually served by FastAPI, and browser tests exercise the local backend and native solver. This verifies the tested build and workflows; it is not a guarantee that every possible input, deployment environment, or real-world operation is error-free.

| Check | Result | Evidence |
|---|---|---|
| Backend unit and integration suite | **73 passed**, 0 failures | [Backend test log](backend-test-results.txt); [focused hardening tests](hardening-test-results.txt) |
| Mandatory scenarios T01–T24 | **24 passed**, included in the 73 | Backend test log |
| Actual frontend unit tests | **9 passed** | [Frontend test log](frontend-test-results.txt) |
| Browser workflows against the real API | **7 passed**: 6 normal workflows + 1 password-protected workflow | [Browser results](browser-test-results.json), [login workflow](browser-auth-results.json) |
| Clean frontend dependency install | `npm ci` succeeded | Final verification run |
| TypeScript and production bundle | `npm run build` succeeded | [Build log](frontend-build-results.txt) |
| Python source compilation | Passed for backend and scripts | `python -m compileall -q backend/app scripts` |
| Python package compatibility | `pip check`: no broken requirements | Installed project virtual environment |
| Frontend dependency vulnerability audit | **0 reported vulnerabilities**, including development tools | [Dependency audit](frontend-dependency-audit.json) |
| Every supplied scenario | **8 passed** validation, solve, independent validation, nominal replay reconciliation, and matching forecast/replay economics | [Scenario results](scenario-verification.json) |
| Policy comparison benchmark | Completed: 2 synthetic test scenarios × 2 worlds × 5 policies | [Benchmark report](benchmark_summary_v1.md) |
| Responsiveness during native solver work | **100 reads**, concurrency **8**, **0 failures**; p95 **93.65 ms** on this machine | [Responsiveness results](responsiveness-verification.json) |

The npm audit result applies to the frontend dependency tree and its advisory database at execution time. Python packages were checked for dependency compatibility; this is not a Python vulnerability audit or a penetration test.

## Repairs made

The subsequent [interface redesign](ui-redesign.md) was verified through the production build, frontend tests, and browser workflows. Backend verification below is retained from the preceding backend audit; the interface redesign did not change backend algorithms or APIs.

### Shortcomings addressed in the second pass

- The frontend now submits asynchronous jobs. A persistent SQLite queue runs one isolated solver process per database and returns a job record immediately, keeping other API requests responsive.
- The operator can cancel an active calculation from the UI. Cancellation terminates the associated native process, preserves the operating ledger, and cannot overwrite a completed or interrupted result.
- Queued work resumes at service startup. Running work interrupted by a restart is marked interrupted for explicit retry. Solver children monitor the parent process and exit when it disappears.
- Unstarted reservations can be moved to a later feasible same-route slot or released for the displayed cancellation fee. Rescheduling validates vehicle overlap, shifts, receiving windows, sale deadlines, and nominal freshness. Release restores stock and demand capacity, rebuilds tier locks, and records its fee once in forecast and replay accounting. Loading and dispatched trips remain locked.
- Optional workspace password protection includes salted PBKDF2 checks, HttpOnly/SameSite sessions, eight-hour expiry, logout revocation, login rate limiting, and API/documentation access checks. Cross-origin writes are rejected even when login is disabled.
- Request validation now rejects invalid identifier shapes, unbounded numeric values, invalid world counts, non-finite uncertainty bounds, and invalid observation probability matrices.
- Health probes perform a lightweight database read instead of repeatedly initializing the schema.
- Added actual browser coverage for releasing a conflicting reservation, cancelling a native job, and signing in/planning/signing out. Added a repeatable live concurrency smoke check and regression coverage for startup recovery, orphan workers, idempotency, and cancellation races.

The original repairs below remain in place.

### Build and application wiring

- Replaced the incomplete npm lockfile with a reproducible dependency lockfile and verified a clean install.
- Fixed TypeScript imports that prevented compilation.
- Replaced frontend tests that tested copied helper implementations with tests importing actual application functions and components.
- Connected the React workspace to scenario import, initialization, planning, receipts, exports, reservations, events, replay, inspection, fragility, and recovery APIs.
- Added usable styling and responsive layout to the actual source frontend. Production assets are generated from that source.
- Made FastAPI serve the production frontend and its assets from the same local address as the API.
- Fixed Windows database paths and automatic creation of the data directory.
- Fixed the Windows browser-test launcher and added platform-aware virtual environment paths.
- Updated vulnerable Vite/Vitest development dependencies, then rebuilt and reran tests.
- Updated startup scripts, setup instructions, and pinned Python transitive dependencies.

### State, inventory, and economics

- Persisted inspection time, observation, history, posterior weights, and cost together through the inspection event. Repeat execution stays idempotent.
- Fixed repeated charging of a trip's initial committed cost when it contained multiple allocations.
- Kept replay and historical analyses tied to the saved planning snapshot after the active scenario changes.
- Made repeated imports create increasing scenario versions. A plan from an earlier version cannot be committed against the replacement scenario.
- Checked saved scenario identity and revision again before a planning job executes.
- Preserved absolute expiry when a batch receives a status-only update. A nominal-only observation shifts its uncertainty interval consistently.
- Added handling for trip loading, dispatch, and delivery events. Delivery moves stock to the destination and retains the return-trip vehicle lock.
- Distinguished overlap between existing commitments from violations introduced by a new residual plan. Existing conflicts remain visible as operator warnings and are not claimed as repaired.
- Corrected reference-demand fulfillment to cap sales per destination/commodity bucket rather than pooling excess sales across destinations.

### Validation and optimization

- Rejected malformed JSON objects, invalid numeric fields, unknown event targets, negative route delays, invalid uncertainty settings, and initial commitments exceeding stock.
- Restricted internal inspection and commitment events to their dedicated API actions.
- Rejected invalid commitment states and trip selections.
- Blocked configuration path traversal.
- Compared the full nested and flat allocation contents in independent validation, including trip timing and fixed costs.
- Preserved a valid first-stage solver incumbent when the secondary tie-break solve fails to produce an incumbent.
- Made the small exhaustive oracle explicitly reject oversized searches instead of silently truncating them.
- Added diagnostics that limit optimality claims to the generated candidate set.
- Prevented cancellation of already completed jobs and moved expensive job execution off the API event loop.

## Browser coverage

1. Load a real scenario; solve; inspect a batch receipt; export JSON; replay; commit reservations; observe a route delay; replan; release the conflicting unstarted reservation; replan again and verify the warning is cleared.
2. Show an invalid-upload error and then import a valid supplied scenario.
3. Rank simulated inspections, execute one, and verify that the backend persisted its history.
4. Solve using uncertainty-aware selection at a **390 px** viewport, open fragility evidence, verify no horizontal page overflow, and compute recovery options.
5. Start an inspection calculation, cancel its worker through the UI, and verify the operating revision and inspection history remain unchanged.
6. With password protection enabled, reject an incorrect password, sign in, generate a real plan, sign out, and verify protected API access is revoked.
7. Use the redesigned network's zoom, destination selection, and reset controls; search inventory; verify matching and empty results; inspect desktop screenshots.

Screenshots were visually inspected: [redesigned desktop overview](ui-overview-desktop.png), [inventory](ui-inventory-desktop.png), and [mobile overview](ui-overview-mobile.png).

## Remaining product boundaries

- This is a single-workspace, single-host application. Optional operator authentication is verified; separate tenant accounts, identity-provider integration, distributed workers, and production deployment certification are outside this build.
- The UI uses the isolated asynchronous queue. Legacy API callers that omit `Prefer: respond-async` retain synchronous execution and cooperative cancellation. Run one API process per database.
- The responsiveness result is a local smoke check under one solver workload, not a production capacity or service-level guarantee.
- Operators can repair feasible unstarted reservations or release them for a fee. Some disruptions have no solution that preserves every deadline. A residual plan does not automatically repair already loading or dispatched commitments.
- Candidate generation is bounded. `OPTIMAL` means optimal over the generated candidate model, not all unrestricted departure schedules.
- Inspection execution is an explicit action before replanning. Planning jobs reject the implicit inspection policy; the offline benchmark separately simulates that policy.
- Inspection value estimates, uncertainty distributions, and cold-chain coefficients need independent empirical validation. They do not certify food safety.
- The benchmark uses synthetic fixtures. The inspected test partition is no longer an unseen holdout. Its illustrative FIFO baseline was implemented in this repository and was not supplied by the organizer.
- The delay ablation measures pre-dispatch replanning. Its results do not establish recovery performance for already dispatched trips.
- No external commercial integrations, real sensor feeds, field impact, or hackathon outcome have been verified.

## Reproduce

Use the setup and verification commands in [the project README](../README.md). The audit databases and original standalone demo are retained separately in ignored `work/` files. The default application database is in `data/`; the browser test database is isolated in `work/`.
