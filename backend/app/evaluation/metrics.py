"""Evaluation metrics, denominators, and plan churn calculations (Section 12.4)."""
from __future__ import annotations
from typing import Any, Dict
from app.schemas.models import PlanResult, ReplayResult


def compute_paired_comparison(
    candidate_replay: ReplayResult,
    baseline_replay: ReplayResult,
) -> Dict[str, Any]:
    """Compute paired comparison between candidate and baseline on the exact same SimulationWorld.
    Handles zero-baseline spoilage (Test T20) and zero/negative baseline net revenue safely.
    """
    base_spoil = baseline_replay.expired_unsold_total_units
    cand_spoil = candidate_replay.expired_unsold_total_units
    spoil_diff_units = base_spoil - cand_spoil  # positive = spoilage reduced

    if base_spoil > 0:
        spoil_reduction_pct: float | None = round((spoil_diff_units / base_spoil) * 100.0, 2)
    else:
        # Test T20: When baseline spoilage is 0, percentage reduction is not applicable (None)
        spoil_reduction_pct = None

    base_net = baseline_replay.realized_economics.net_outcome_paise
    cand_net = candidate_replay.realized_economics.net_outcome_paise
    net_delta_paise = cand_net - base_net

    if base_net > 0:
        net_improvement_pct: float | None = round((net_delta_paise / base_net) * 100.0, 2)
    else:
        net_improvement_pct = None

    base_sold = baseline_replay.sold_total_units
    cand_sold = candidate_replay.sold_total_units
    sold_delta_units = cand_sold - base_sold

    base_ful = baseline_replay.demand_fulfillment_rate or 0.0
    cand_ful = candidate_replay.demand_fulfillment_rate or 0.0
    ful_delta_pct = round((cand_ful - base_ful) * 100.0, 2)

    base_ref_ful = baseline_replay.reference_fulfillment_rate or 0.0
    cand_ref_ful = candidate_replay.reference_fulfillment_rate or 0.0

    return {
        "world_id": candidate_replay.world_id,
        "candidate_policy": candidate_replay.policy.value,
        "baseline_policy": baseline_replay.policy.value,
        "spoilage_reduction_units": spoil_diff_units,
        "spoilage_reduction_pct": spoil_reduction_pct,
        "net_delta_paise": net_delta_paise,
        "net_improvement_pct": net_improvement_pct,
        "sold_delta_units": sold_delta_units,
        "fulfillment_rate_delta_pct": ful_delta_pct,
        "candidate_reference_fulfillment_rate": round(cand_ref_ful, 4),
        "baseline_reference_fulfillment_rate": round(base_ref_ful, 4),
        "candidate_net_paise": cand_net,
        "baseline_net_paise": base_net,
        "candidate_spoilage_units": cand_spoil,
        "baseline_spoilage_units": base_spoil,
        "candidate_carryover_units": candidate_replay.unexpired_carryover_total_units,
        "baseline_carryover_units": baseline_replay.unexpired_carryover_total_units,
        "candidate_service_failures": candidate_replay.realized_service_failures,
        "baseline_service_failures": baseline_replay.realized_service_failures,
    }


def compute_plan_churn(prior_plan: PlanResult, revised_plan: PlanResult) -> Dict[str, Any]:
    """Compute plan churn (changed unexecuted assignments and trips relative to prior plan)."""
    prior_map: Dict[tuple[str, str], int] = {}
    for a in prior_plan.allocations:
        key = (a.batch_id, a.destination_id)
        prior_map[key] = prior_map.get(key, 0) + a.quantity_units

    rev_map: Dict[tuple[str, str], int] = {}
    for a in revised_plan.allocations:
        key = (a.batch_id, a.destination_id)
        rev_map[key] = rev_map.get(key, 0) + a.quantity_units

    all_keys = set(prior_map.keys()).union(set(rev_map.keys()))
    changed_pairs = 0
    changed_units_l1 = 0
    for k in all_keys:
        q1 = prior_map.get(k, 0)
        q2 = rev_map.get(k, 0)
        if q1 != q2:
            changed_pairs += 1
            changed_units_l1 += abs(q2 - q1)

    prior_trips = {t.trip_id for t in prior_plan.trips}
    rev_trips = {t.trip_id for t in revised_plan.trips}
    changed_trips = len(prior_trips.symmetric_difference(rev_trips))

    return {
        "changed_batch_destination_pairs": changed_pairs,
        "changed_quantity_l1_units": changed_units_l1,
        "changed_trip_count": changed_trips,
    }
