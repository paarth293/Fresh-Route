# Judge's operating guide

FreshRoute LastWindow is an interactive decision-support prototype for perishable produce distribution. This guide follows the working interface.

## 1. Open and sign in

Open **https://lastwindow-production.up.railway.app**. Enter the **Workspace password** supplied in the private submission access notes (see [access instructions](../README.md#judge-access)), then select **Sign in**. There is no username, login ID, email field, or registration step.

The workspace is shared and editable. Judges use the same saved operating state. The currently displayed result is held in each browser's interface; refreshing can clear that display without deleting the saved database record. Generate a plan again when needed.

## 2. Start from a known scenario

The default is **Morning Regional Produce Distribution (Balanced)**, ID `demo-normal-001`: six batches, three vehicles, three destinations, and 430 kg of initial stock.

If an earlier evaluation changed it, download [demo-normal-001.json](https://raw.githubusercontent.com/paarth293/Fresh-Route/deploy/railway/scenarios/demo/demo-normal-001.json), select **Import JSON**, and upload the file. Wait for **Scenario imported as a new version.** This replaces the active state for everyone using that scenario ID. Historical plans remain stored. Reselecting a scenario or refreshing the page does not reset it.

## 3. Generate the first plan

1. Open **Overview**.
2. Choose **Nominal optimizer** in **Planning policy**.
3. Select **Generate plan** once and watch the calculation banner.
4. Wait for **Plan generated and independently validated.**
5. Review available stock, forecast net contribution, planned trips, and validation status.

The reference run on the untouched normal fixture produced 4 trips, 423 kg allocated, and ₹20,317.70 forecast net contribution. This is a scenario-specific planning forecast, not field profit or a guaranteed output for changed inputs.

The network is a schematic, not GPS tracking. Select a destination to focus its corridor, use the zoom controls, or select **Reset view**. The freshness watch uses the scenario's remaining-life estimates. **Analyze fragility** opens computed evidence about the plan.

Supported queued or running calculations expose **Cancel operation**. Cancellation does not undo a previously recorded event or reservation.

## 4. Inspect a batch explanation

1. Open **Inventory**.
2. Search for `B-TOM-01`, or inspect another batch.
3. Select **View receipt**. A generated plan is required.
4. Read the allocation explanation and relevant constraints.
5. Expand **Complete evidence** for the detailed record.
6. Select **Close receipt**.

Search filters the table without changing stock. Receipts explain modeled decisions; they do not certify physical quality or food safety.

## 5. Read the dispatch schedule

Open **Dispatch** and review:

| Table | What it answers |
|---|---|
| Vehicle schedule | Which vehicle goes where, when loading starts, when it departs, when goods are ready for sale, and when the vehicle is released. |
| Batch allocations | The quantity sent from each batch, its destination, allowed price, and discount. |
| Dispatch windows | Latest scheduled departure, remaining slack, and eligibility or rejection reasons. |

Times such as `15m` are minutes from the scenario start. Small slack means little modeled room for delay. An **Unavailable** departure must be read with its reason; it is not an approved shipment.

Select **Export plan** to download the current plan as JSON. This is a planning result, not a scenario package that can be reimported.

## 6. Replay the plan

1. Open **Evaluation** and select **Run replay**.
2. Review sold quantity, expired unsold stock, unexpired carryover, realized simulated net contribution, and service failures.
3. Confirm **Inventory reconciled: Yes**.
4. Expand **Replay evidence** to inspect the record.

Replay uses the stored planning snapshot and a seeded simulation world; it does not replace the live ledger. Forecast and replay outcomes can differ because the simulation samples conditions beyond nominal assumptions. Unexpired carryover is not automatically spoilage.

## 7. Reserve trips and recover from a delay

This changes the operating state. Start with the fresh normal fixture and a nominal plan.

1. Select **Commit plan**. Wait for **Trips reserved. Generate a new plan for remaining stock.**
2. Open **Disruptions** and inspect **Existing commitments**.
3. Select route **R-EAST**.
4. Set **Additional delay (minutes)** to **20** and select **Apply delay**.
5. Generate a revised plan. Committed stock and vehicle intervals remain locked. If existing reservations no longer fit, a warning reports that operator intervention is required.
6. Locate the eligible unstarted **V-TRUCK-01 / R-EAST** reservation. Select **Release**, which shows its configured cancellation fee.
7. Wait for **Reservation released and cancellation fee recorded. Generate a new plan for the recovered stock.**
8. Select **Generate plan** and inspect the result.

The tested normal-demo sequence clears the conflict after that release and replanning. Shared-state changes can alter the available reservations; restore the fixture if the expected trip is missing.

Alternatively, **Move reservation** searches for a feasible later same-route slot. Timing, vehicle overlap, receiving window, deadline, and nominal freshness must permit the move. Loading or dispatched trips cannot be moved or released by these controls. Some disruptions have no feasible repair. A valid residual plan does not establish that every older commitment has been repaired.

**Compare recovery options** computes evidence; it does not apply a repair or release a trip.

## 8. Explore inspection value

Restore a fresh normal fixture first so previous delays and reservations do not consume its availability.

1. Open **Inspections** and select **Rank inspections**.
2. Wait for the table; this may take longer than nominal planning.
3. Compare **Estimated value**, **Cost**, **Duration**, and **Recommendation**.
4. Select **Simulate inspection** for an eligible batch.
5. Read the observation confirmation. Its time and cost are recorded in the operating state.
6. Generate a new plan using the updated estimate.

The ranking estimates whether information may improve a decision enough to justify its cost. Observations are simulated, not laboratory results or real sensor measurements. Inspection is an explicit action; generating a plan does not silently perform it.

## 9. Compare policies

1. Open **Evaluation** and select **Compare policies**.
2. Wait for the comparison table. Multiple policies and worlds make this more expensive than one plan.
3. Compare mean simulated net contribution, mean sold quantity, and mean spoilage.
4. Expand **Comparison manifest and evidence**.

The current-scenario comparison uses two shared simulation worlds. The toolbar separately lets you generate a plan with **Uncertainty-aware selection**, **FEFO baseline**, or **Illustrative FIFO baseline**. Changing the dropdown requires **Generate plan** to compute a new result.

The FIFO baseline is repository code, not an organizer-provided or commercial baseline. These synthetic comparisons and inspected fixtures do not establish field impact or unseen holdout performance. No policy is expected to win on every input and metric.

## 10. Finish the evaluation

Export any plan you want to retain. If appropriate, coordinate with other judges and restore the normal fixture for the next demonstration. Select **Sign out** to end your session.

Signing out does not erase saved scenarios, delays, reservations, or inspections. The MVP has one shared workspace and no individual judge accounts.

## Verification and further reading

The [hosted verification report](../reports/deployment.md) records HTTPS access, login, native solver execution, validation, receipt display, replay, mobile layout, logout, and saved-plan retrieval from a fresh session. Additional local tests cover reservations, recovery, inspections, and cancellation. See the [build audit](../reports/build-verification.md) for their scope and the [README](../README.md) for setup, architecture, troubleshooting, and limitations.
