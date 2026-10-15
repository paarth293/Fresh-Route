# FreshRoute: LastWindow — three-minute submission video

Duration: 03:00. Format: 1920 × 1080, 30 fps, H.264/AAC MP4.
Narration: locally synthesized English using the installed Windows voice. Captions are embedded in the picture; a separate SRT is supplied. Screens show the actual app, and the recovery segment includes recorded interactions.

## Narration and chapter timings

### 00:00–00:11 — Freshness has a window.

Fresh produce has a deadline. One late route can turn a valuable batch into waste. FreshRoute LastWindow helps distributors make the next decision before that window closes.

### 00:11–00:27 — More than a routing problem.

Our problem is not simply finding the shortest route. Dispatchers must coordinate shelf life, receiving hours, vehicle capacity, demand, permitted discounts, and costs. LastWindow brings these constraints into one workspace, so operational decisions can be examined together.

### 00:27–00:46 — A fresher outlook.

Here is the working product: six batches, three vehicles, and three destinations. Generate Plan turns available inventory into a proposed dispatch schedule. The dashboard connects the route network, shelf-life watchlist, forecast contribution, and independent validation. Every number comes from the scenario and its generated plan.

### 00:46–01:05 — Feasible first. Valuable next.

Underneath, an OR-Tools mixed-integer model chooses quantities, routes, departure times, vehicles, and allowed price tiers. It accounts for capacity, receiving windows, freshness, and costs. A separate validator checks the result before commitment. Optimality is claimed only within the generated candidate set.

### 01:05–01:23 — Know the last window.

Our key idea is the last window: the latest feasible departure before a batch loses its opportunity. Dispatch slack shows how much room is left. A decision receipt explains each batch's allocations, constraints, and price-tier comparisons, so the operator can understand the recommendation instead of following a black box.

### 01:23–01:44 — Recover without rewriting history.

Now a working workflow: reserve the plan, then record a twenty-minute route delay. Existing stock and vehicles remain locked, and conflicting reservations are flagged. An unstarted trip can move to a feasible later slot, or be released for its cancellation fee. Replanning uses the recovered availability, while historical plans retain their original snapshots.

### 01:44–02:05 — Look closer. Decide better.

LastWindow also compares bounded policy candidates across sampled travel, shelf-life, and demand conditions. It can rank batches by estimated inspection value. Simulating an inspection consumes time, records its cost, updates the estimate, and preserves the observation. Inspect only when the information is worth its operational cost.

### 02:05–02:27 — Test the plan beyond the forecast.

The evaluation view replays stored plans under seeded simulation worlds. We compare illustrative first-in-first-out, earliest-expiry-first, nominal optimization, and uncertainty-aware selection under shared conditions. Sales, spoilage, carryover, service failures, and net contribution are reported separately. These are synthetic results, not field measurements.

### 02:27–02:47 — A working system, end to end.

React and TypeScript power the interface. FastAPI, OR-Tools with SCIP, and SQLite support planning and saved snapshots. The build includes cancellable jobs, revision checks, and optional password access. Seventy-three backend tests, nine frontend tests, and seven browser workflows have passed.

### 02:47–03:00 — Better decisions.

LastWindow makes perishable distribution explainable, time-aware, and recoverable. The next step is a pilot with real operational data. Better decisions, before freshness runs out.

## Evidence and scope

- Demonstration: supplied demo-normal-001 scenario, real API, native solver, inspection and reservation workflows.
- Demo outcome shown: 4 generated trips, 423 kg allocated, Rs 20,317.70 forecast net contribution. This is a planning forecast, not realized field profit.
- Benchmark chart: manifest_09aa44f5d2; two synthetic scenarios and two worlds per scenario. FIFO is an illustrative repository baseline. Test fixtures have been inspected during development and are not an unseen holdout.
- Verification counts are from the project build and UI verification reports: 73 backend tests, 9 frontend tests, 7 browser workflows, and 8 supplied scenario checks.
- The candidate solver has a bounded search space. Inspection value and cold-chain models need empirical calibration. This video makes no field-impact or food-safety-certification claim.
- No real customer information, external accounts, or credentials are shown.
