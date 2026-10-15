"""Decision receipts and controlled alternative re-solve engine (Section 7.9 & Step 12)."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from app.domain.units import paise_to_rupees_str
from app.planning.candidates import generate_candidate_trips
from app.planning.deadlines import compute_batch_deadlines
from app.planning.mip import solve_deterministic_mip
from app.schemas.models import (
    AlternativeComparison,
    DecisionReceipt,
    PlanResult,
    PlanningSnapshot,
    SolverStatus,
)

# Cache for controlled alternative re-solves keyed by (plan_id, batch_id, dest_id, forced_qty)
_ALTERNATIVE_CACHE: Dict[Tuple[str, str, str, int], AlternativeComparison] = {}


def build_decision_receipt(
    snapshot: PlanningSnapshot,
    plan: PlanResult,
    batch_id: str,
) -> DecisionReceipt:
    """Build structured decision receipt for a specific batch from actual inputs, constraints, and plan outputs."""
    scenario = snapshot.scenario
    batch = next((b for b in scenario.batches if b.id == batch_id), None)
    if batch is None:
        raise ValueError(f"Batch '{batch_id}' not found in scenario.")

    avail_q = snapshot.warehouse_stock_by_batch.get(batch.id, batch.quantity_units)
    chosen_allocs = [a for a in plan.allocations if a.batch_id == batch.id]
    alloc_q = sum(a.quantity_units for a in chosen_allocs)
    unalloc_q = plan.unallocated_by_batch.get(batch.id, max(0, avail_q - alloc_q))

    if plan.deadlines:
        batch_deadlines = [dl for dl in plan.deadlines if dl.batch_id == batch.id]
    else:
        cands, _ = generate_candidate_trips(snapshot)
        all_dls = compute_batch_deadlines(snapshot, cands, allocations=plan.allocations)
        batch_deadlines = [dl for dl in all_dls if dl.batch_id == batch.id]

    binding_constraints: List[str] = []
    if batch.status.value != "available":
        binding_constraints.append(f"Batch status is '{batch.status.value}' (quarantined/unavailable stock cannot be dispatched).")
    if batch.estimated_expiry_nominal_min <= snapshot.decision_at_min:
        binding_constraints.append(f"Batch already expired at t={batch.estimated_expiry_nominal_min} min.")

    for dl in batch_deadlines:
        if not dl.is_eligible and dl.rejection_reason:
            binding_constraints.append(f"Destination {dl.destination_id} excluded: {dl.rejection_reason}.")
        elif dl.is_eligible and dl.dispatch_slack_min is not None and dl.dispatch_slack_min <= 30:
            binding_constraints.append(
                f"Tight dispatch window on {dl.destination_id}: slack is {dl.dispatch_slack_min} min (latest departure t={dl.scheduled_latest_departure_min} min)."
            )

    # Check if any chosen trip is at full capacity or bucket at demand cap
    trip_map = {t.trip_id: t for t in plan.trips}
    bucket_map = {db.id: db for db in scenario.demand_buckets}
    for a in chosen_allocs:
        t = trip_map.get(a.trip_id)
        if t and t.total_loaded_units >= t.capacity_units:
            binding_constraints.append(
                f"Vehicle {t.vehicle_id} on trip {t.trip_id} is at 100% capacity ({t.total_loaded_units}/{t.capacity_units} kg)."
            )
        db = bucket_map.get(a.bucket_id)
        if db:
            tier_spec = next((ts for ts in db.price_tiers if ts.tier_id == a.tier_id), None)
            res_cap = snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap)
            if tier_spec:
                bucket_total = sum(x.quantity_units for x in plan.allocations if x.bucket_id == db.id)
                limit = min(tier_spec.forecast_sales_units, res_cap)
                if bucket_total >= limit:
                    binding_constraints.append(
                        f"Bucket {db.id} ({db.destination_id}) reached tier '{a.tier_id}' allocation limit ({bucket_total}/{limit} kg)."
                    )

    if not binding_constraints:
        binding_constraints.append("Allocated to maximize net contribution within nominal shelf-life and route schedules.")

    # Tier comparison summary for relevant commodity buckets
    tier_comparisons: List[Dict[str, Any]] = []
    for db in scenario.demand_buckets:
        if db.commodity_id != batch.commodity_id:
            continue
        selected_tid = plan.selected_tier_by_bucket.get(db.id, db.price_tiers[0].tier_id)
        var_handling = scenario.cost_model.get_handling_cost(batch.commodity_id)
        for ts in db.price_tiers:
            eff_cap = min(ts.forecast_sales_units, snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap))
            tier_comparisons.append(
                {
                    "bucket_id": db.id,
                    "destination_id": db.destination_id,
                    "tier_id": ts.tier_id,
                    "discount_pct": ts.discount_pct,
                    "price_paise_per_unit": ts.price_paise_per_unit,
                    "forecast_sales_units": ts.forecast_sales_units,
                    "effective_cap_units": eff_cap,
                    "max_gross_margin_paise": eff_cap * (ts.price_paise_per_unit - var_handling),
                    "is_selected": (ts.tier_id == selected_tid),
                }
            )

    if chosen_allocs:
        parts = [
            f"{a.quantity_units} kg -> {a.destination_id} via {a.vehicle_id} (depart t={a.departure_min}m, tier {a.tier_id} @ {paise_to_rupees_str(a.unit_price_paise)}/kg, slack={a.dispatch_slack_min}m)"
            for a in chosen_allocs
        ]
        summary_str = (
            f"Batch {batch.id} ({batch.commodity_id}, {avail_q} kg available, est. expiry t={batch.estimated_expiry_nominal_min}m): "
            f"Allocated {alloc_q} kg ({'; '.join(parts)}); {unalloc_q} kg unallocated."
        )
    else:
        summary_str = (
            f"Batch {batch.id} ({batch.commodity_id}, {avail_q} kg available, est. expiry t={batch.estimated_expiry_nominal_min}m): "
            f"0 kg allocated ({unalloc_q} kg retained at origin). Constraints: {' '.join(binding_constraints[:2])}"
        )

    return DecisionReceipt(
        batch_id=batch.id,
        commodity_id=batch.commodity_id,
        available_quantity_units=avail_q,
        allocated_quantity_units=alloc_q,
        unallocated_quantity_units=unalloc_q,
        estimated_expiry_min=batch.estimated_expiry_nominal_min,
        chosen_allocations=chosen_allocs,
        deadlines_by_destination=batch_deadlines,
        binding_constraints=binding_constraints,
        tier_comparison_summary=tier_comparisons,
        explanation_text=summary_str,
    )


def compare_alternative_destination(
    snapshot: PlanningSnapshot,
    base_plan: PlanResult,
    batch_id: str,
    alternative_destination_id: str,
    forced_quantity_units: int | None = None,
) -> AlternativeComparison:
    """Controlled re-solve (Section 7.9): force a quantity of batch_id to alternative_destination_id
    while keeping all original constraints intact, and report infeasibility or forecast objective delta.
    """
    batch = next((b for b in snapshot.scenario.batches if b.id == batch_id), None)
    if batch is None:
        raise ValueError(f"Unknown batch '{batch_id}'.")

    avail_q = snapshot.warehouse_stock_by_batch.get(batch_id, batch.quantity_units)
    base_alloc_q = sum(a.quantity_units for a in base_plan.allocations if a.batch_id == batch_id)
    qty_to_force = forced_quantity_units if forced_quantity_units is not None else (base_alloc_q if base_alloc_q > 0 else min(20, avail_q))

    cache_key = (base_plan.plan_id, batch_id, alternative_destination_id, qty_to_force)
    if cache_key in _ALTERNATIVE_CACHE:
        return _ALTERNATIVE_CACHE[cache_key]

    # Check if alternative destination is already ineligible in deadlines
    dl_match = next(
        (dl for dl in base_plan.deadlines if dl.batch_id == batch_id and dl.destination_id == alternative_destination_id),
        None,
    )
    if dl_match is not None and not dl_match.is_eligible:
        res = AlternativeComparison(
            plan_id=base_plan.plan_id,
            batch_id=batch_id,
            alternative_destination_id=alternative_destination_id,
            forced_quantity_units=qty_to_force,
            is_feasible=False,
            infeasibility_reason=dl_match.rejection_reason or "INELIGIBLE_BATCH_DESTINATION",
            base_forecast_net_paise=base_plan.forecast_economics.net_outcome_paise,
        )
        _ALTERNATIVE_CACHE[cache_key] = res
        return res

    alt_plan = solve_deterministic_mip(
        snapshot=snapshot,
        config_id=f"{base_plan.config_id}_alt_{alternative_destination_id}",
        forced_allocations=[
            {
                "batch_id": batch_id,
                "destination_id": alternative_destination_id,
                "quantity_units": qty_to_force,
            }
        ],
    )

    if alt_plan.solver_status in (SolverStatus.INFEASIBLE, SolverStatus.NO_INCUMBENT_TIMEOUT, SolverStatus.SOLVER_ERROR):
        res = AlternativeComparison(
            plan_id=base_plan.plan_id,
            batch_id=batch_id,
            alternative_destination_id=alternative_destination_id,
            forced_quantity_units=qty_to_force,
            is_feasible=False,
            infeasibility_reason=f"Controlled re-solve returned {alt_plan.solver_status.value} (exceeds demand/capacity or conflicts with vehicle schedule).",
            base_forecast_net_paise=base_plan.forecast_economics.net_outcome_paise,
        )
    else:
        base_net = base_plan.forecast_economics.net_outcome_paise
        alt_net = alt_plan.forecast_economics.net_outcome_paise
        base_trips = {t.trip_id for t in base_plan.trips}
        alt_trips = {t.trip_id for t in alt_plan.trips}
        changed = sorted(base_trips.symmetric_difference(alt_trips))
        res = AlternativeComparison(
            plan_id=base_plan.plan_id,
            batch_id=batch_id,
            alternative_destination_id=alternative_destination_id,
            forced_quantity_units=qty_to_force,
            is_feasible=True,
            infeasibility_reason=None,
            base_forecast_net_paise=base_net,
            alternative_forecast_net_paise=alt_net,
            net_delta_paise=alt_net - base_net,
            affected_trips=changed,
        )

    _ALTERNATIVE_CACHE[cache_key] = res
    return res
