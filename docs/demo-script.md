# Presenter walkthrough

Use the [judge's operating guide](judges-guide.md) for complete instructions and the [README](../README.md#judge-access) for access. The [three-minute video](../deliverables/LastWindow_Hackathon_3min.mp4) has its own [timed transcript](../deliverables/LastWindow_Video_Script.md).

This live presentation takes approximately five minutes plus calculation time. Start with a fresh normal fixture. Extend the inspection and policy sections when time permits.

| Segment | Demonstration | Explanation |
|---|---|---|
| 0:00–0:40 | Sign in; show Overview and the normal scenario. | Perishable dispatch coordinates freshness, receiving hours, demand, price tiers, costs, and vehicle availability. |
| 0:40–1:20 | Choose Nominal optimizer and Generate plan. | The application creates a schedule and independently validates modeled constraints. |
| 1:20–2:00 | Open Inventory → View receipt, then Dispatch. | Explain an allocation, its latest scheduled departure, and slack. These are modeled opportunities, not safety certifications. |
| 2:00–2:40 | Open Evaluation → Run replay. | Distinguish forecast contribution from simulated outcomes. Show sales, expired stock, carryover, failures, and reconciliation. |
| 2:40–4:10 | Commit plan; Disruptions → R-EAST → 20-minute delay; replan; release eligible V-TRUCK-01 / R-EAST reservation; replan again. | Existing resources stay locked. Conflicts are explicit, and eligible unstarted reservations can be repaired or released with a fee. The tested sequence assumes the unchanged normal fixture. |
| 4:10–5:00 | Explain Inspections and policy comparison; run them if time permits. | Inspection ranking accounts for estimated information value, time, and cost. Policy comparison uses shared seeded synthetic worlds. |

For a longer demonstration, reimport the normal fixture, open Inspections, run Rank inspections, simulate an observation, and generate a new plan. Then run Evaluation → Compare policies and inspect its manifest.

Use the controls present in the interface. The disruption form records route delays; it does not expose every API event. Commit plan reserves trips and is not a dispatched-state selector. Compare recovery options computes evidence and does not apply a repair.

Conclude with the next validation step: a pilot using real operating data. Do not present synthetic benchmark results as field profit, a commercial comparison, or an unseen holdout result.
