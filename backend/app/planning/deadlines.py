"""Last-window and dispatch-slack calculations (Section 7.8 & Step 12)."""
from __future__ import annotations
from typing import Dict, List
from app.domain.units import is_batch_fresh_for_sale
from app.planning.eligibility import check_batch_trip_bucket_eligibility
from app.schemas.models import (
    AllocationItem,
    BatchDeadlineInfo,
    BatchStatus,
    CandidateTrip,
    PlanningSnapshot,
)


def compute_batch_deadlines(
    snapshot: PlanningSnapshot,
    candidates: List[CandidateTrip],
    allocations: List[AllocationItem] | None = None,
    expiry_override_by_batch: Dict[str, int] | None = None,
) -> List[BatchDeadlineInfo]:
    """Compute both analytical local timing limit and scheduled opportunity for every (batch, destination) pair."""
    scenario = snapshot.scenario
    veh_map = {v.id: v for v in scenario.vehicles}
    routes_by_dest: Dict[str, List] = {}
    for r in scenario.routes:
        routes_by_dest.setdefault(r.destination_id, []).append(r)

    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}
    planned_dep_by_batch_dest: Dict[tuple[str, str], int] = {}
    if allocations:
        for a in allocations:
            key = (a.batch_id, a.destination_id)
            if key not in planned_dep_by_batch_dest or a.departure_min < planned_dep_by_batch_dest[key]:
                planned_dep_by_batch_dest[key] = a.departure_min

    results: List[BatchDeadlineInfo] = []

    for b in scenario.batches:
        est_exp = (
            expiry_override_by_batch[b.id]
            if (expiry_override_by_batch and b.id in expiry_override_by_batch)
            else b.estimated_expiry_nominal_min
        )
        for d in scenario.destinations:
            planned_dep = planned_dep_by_batch_dest.get((b.id, d.id))
            if b.status != BatchStatus.AVAILABLE:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason=f"BATCH_STATUS_{b.status.value.upper()}",
                    )
                )
                continue
            if est_exp <= snapshot.decision_at_min:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason="BATCH_ALREADY_EXPIRED",
                    )
                )
                continue
            if d.closed:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason="DESTINATION_CLOSED",
                    )
                )
                continue

            db = dest_comm_to_bucket.get((d.id, b.commodity_id))
            if db is None:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason="NO_DEMAND_BUCKET",
                    )
                )
                continue

            dest_routes = routes_by_dest.get(d.id, [])
            if not dest_routes:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason="MISSING_ROUTE",
                    )
                )
                continue

            # Section 7.8: First reject if S_dc + B_dc >= E_b
            if not is_batch_fresh_for_sale(db.sale_at_min, db.freshness_buffer_min, est_exp):
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        rejection_reason="INSUFFICIENT_FRESHNESS_AT_SALE",
                    )
                )
                continue

            # 1. Analytical local timing limit across routes serving destination d
            analytical_best: int | None = None
            for r in dest_routes:
                latest_ready = min(db.sale_at_min, est_exp - db.freshness_buffer_min - 1)
                latest_unloading_end = min(d.receiving_close_min, latest_ready - r.downstream_handling_duration_min)
                latest_dep = latest_unloading_end - r.unloading_duration_min - r.outbound_travel_min
                if latest_dep - r.loading_duration_min >= snapshot.decision_at_min:
                    if analytical_best is None or latest_dep > analytical_best:
                        analytical_best = latest_dep

            # 2. Scheduled opportunity across candidate trips
            eligible_deps: List[int] = []
            last_reason = "NO_COMPATIBLE_CANDIDATE_TRIP"
            for cand in candidates:
                if cand.destination_id != d.id:
                    continue
                veh = veh_map.get(cand.vehicle_id)
                if veh is None:
                    continue
                ok, reason = check_batch_trip_bucket_eligibility(
                    b, veh, d, db, cand, snapshot.decision_at_min, expiry_override_min=est_exp
                )
                if ok:
                    eligible_deps.append(cand.departure_min)
                else:
                    last_reason = reason

            if not eligible_deps:
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=False,
                        analytical_latest_departure_min=analytical_best,
                        scheduled_latest_departure_min=None,
                        planned_departure_min=planned_dep,
                        dispatch_slack_min=None,
                        rejection_reason=last_reason,
                    )
                )
            else:
                sched_latest = max(eligible_deps)
                slack = (sched_latest - planned_dep) if planned_dep is not None else None
                results.append(
                    BatchDeadlineInfo(
                        batch_id=b.id,
                        destination_id=d.id,
                        is_eligible=True,
                        analytical_latest_departure_min=analytical_best,
                        scheduled_latest_departure_min=sched_latest,
                        planned_departure_min=planned_dep,
                        dispatch_slack_min=slack,
                        rejection_reason=None,
                    )
                )

    return results
