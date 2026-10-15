# Four-to-Five-Minute Judge Demonstration Script

1. **0:00–0:30 — The Operational Decision (Overview & Inventory):**
   Load `demo-normal-001` (Morning Distribution). Point out decision time (`06:00 IST`), batch remaining life estimates, and the difference between forecast planning assumptions and realized replay outcomes.
2. **0:30–1:15 — The Baseline Comparison:**
   Run `organizer_baseline` and `fefo_greedy`. Show how naive dispatch sends fragile strawberry/leafy-green batches on long routes or misses discount-tier absorption when supply is high.
3. **1:15–2:00 — LastWindow Plan & Decision Receipt:**
   Run `scenario_selected`. Open the Batch Decision Receipt drawer for Batch `B-TOM-01` or `B-STR-01` to inspect `latest_feasible_departure_min`, `dispatch_slack_min`, binding constraints, and controlled alternative destination re-solve.
4. **2:00–2:45 — Disruption Lab & Commitment-Aware Replanning:**
   Commit the initial plan (`reserved` / `dispatched`), then inject a `ROUTE_DELAY_OBSERVED` (+45 min on Route R2) or `VEHICLE_UNAVAILABLE` event. Show that already-dispatched stock stays locked (`T11`), stale uncommitted plans are blocked from commit (`STATE_REVISION_CONFLICT`), and the revised plan cleanly re-optimizes residual stock.
5. **2:45–3:30 — Inspection Queue (Value of Information):**
   Open the Inspection Queue. Show how uncertain batches are ranked by expected net Value of Information minus inspection cost and delay. Trigger `Simulate Inspection` on the top-ranked batch and observe the posterior update.
6. **3:30–5:00 — Benchmark Evidence & Live Judge Parameter Edit:**
   Open the Evaluation screen to show paired comparisons across held-out worlds, economic waterfalls, and a transparent losing/neutral case where conservative buffering sacrifices upside in a benign world.
