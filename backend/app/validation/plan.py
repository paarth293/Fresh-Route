"""Independent plan validator (Section 7.5 & Step 7).
Validates any proposed PlanResult directly against canonical PlanningSnapshot rules,
including full cross-field reference consistency and assumption-aware buffered timing contracts.
"""
from __future__ import annotations
import json
from collections import Counter
from typing import Dict, List, Tuple
from app.domain.units import is_batch_fresh_for_sale
from app.planning.eligibility import compute_trip_timing, intervals_overlap
from app.schemas.models import (
    BatchStatus,
    CommitmentState,
    PlanningSnapshot,
    PlanResult,
    ValidationReport,
    ViolationItem,
)


def validate_plan_independently(
    snapshot: PlanningSnapshot,
    plan: PlanResult,
    checked_under_assumptions: str = "nominal_planning_snapshot",
) -> ValidationReport:
    """Validate stock conservation, capacity, single tier per bucket, demand/acceptance caps,
    vehicle overlaps, route timing, receiving windows, freshness buffer, locked commitments,
    and full cross-field reference consistency across batches, trips, routes, vehicles, buckets, and tiers.
    """
    violations: List[ViolationItem] = []
    warnings: List[ViolationItem] = []
    scenario = snapshot.scenario

    batch_map = {b.id: b for b in scenario.batches}
    veh_map = {v.id: v for v in scenario.vehicles}
    route_map = {r.id: r for r in scenario.routes}
    dest_map = {d.id: d for d in scenario.destinations}
    bucket_map = {db.id: db for db in scenario.demand_buckets}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}

    declared_extra_delay = int((plan.candidate_diagnostics or {}).get("extra_route_delay_min", 0))
    declared_expiry_offset = int((plan.candidate_diagnostics or {}).get("expiry_offset_min", 0))

    def add_v(code: str, msg: str, entity_id: str, **details: object) -> None:
        violations.append(ViolationItem(code=code, message=msg, entity_id=entity_id, details=dict(details)))

    # 0. Check duplicate trip IDs
    seen_trip_ids: set[str] = set()
    for t in plan.trips:
        if t.trip_id in seen_trip_ids:
            add_v("DUPLICATE_TRIP_ID", f"Duplicate trip_id '{t.trip_id}' in plan.trips.", t.trip_id)
        seen_trip_ids.add(t.trip_id)

    # 1. Check batch existence, cross-field commodity match, status, and stock conservation
    alloc_by_batch: Dict[str, int] = {b.id: 0 for b in scenario.batches}
    for alloc in plan.allocations:
        if alloc.quantity_units <= 0:
            add_v("NONPOSITIVE_ALLOCATION_QTY", f"Allocation has quantity {alloc.quantity_units} <= 0.", alloc.batch_id)
        if alloc.batch_id not in batch_map:
            add_v("UNKNOWN_BATCH", f"Allocation references unknown batch '{alloc.batch_id}'.", alloc.batch_id)
            continue
        b = batch_map[alloc.batch_id]
        if alloc.commodity_id != b.commodity_id:
            add_v(
                "ALLOCATION_COMMODITY_MISMATCH",
                f"Allocation for batch '{b.id}' has commodity_id '{alloc.commodity_id}' != batch commodity '{b.commodity_id}'.",
                b.id,
            )
        if b.status != BatchStatus.AVAILABLE:
            add_v(
                "INELIGIBLE_BATCH_STATUS",
                f"Batch '{b.id}' has status '{b.status.value}' and cannot be allocated.",
                b.id,
                status=b.status.value,
            )
        if b.estimated_expiry_nominal_min <= snapshot.decision_at_min:
            add_v(
                "EXPIRED_BATCH_ALLOCATED",
                f"Batch '{b.id}' is already expired at decision time {snapshot.decision_at_min}.",
                b.id,
            )
        alloc_by_batch[alloc.batch_id] += alloc.quantity_units

    for b_id, b in batch_map.items():
        avail_q = snapshot.warehouse_stock_by_batch.get(b_id, b.quantity_units)
        allocated_q = alloc_by_batch.get(b_id, 0)
        unalloc_q = plan.unallocated_by_batch.get(b_id, 0)
        if allocated_q > avail_q:
            add_v(
                "STOCK_EXCEEDED",
                f"Batch '{b_id}' allocated {allocated_q} units, exceeding available warehouse stock {avail_q}.",
                b_id,
                allocated=allocated_q,
                available=avail_q,
            )
        if unalloc_q < 0 or (allocated_q + unalloc_q != avail_q):
            add_v(
                "STOCK_CONSERVATION_MISMATCH",
                f"Batch '{b_id}' allocated ({allocated_q}) + unallocated ({unalloc_q}) != available ({avail_q}).",
                b_id,
                allocated=allocated_q,
                unallocated=unalloc_q,
                available=avail_q,
            )

    # 2. Check trips, capacity, route timing (nominal or declared buffer), receiving windows, and vehicle shifts
    trip_map = {t.trip_id: t for t in plan.trips}
    alloc_by_trip: Dict[str, int] = {t.trip_id: 0 for t in plan.trips}
    for alloc in plan.allocations:
        if alloc.trip_id not in trip_map:
            add_v("ORPHAN_ALLOCATION_TRIP", f"Allocation references missing trip '{alloc.trip_id}'.", alloc.trip_id)
        else:
            alloc_by_trip[alloc.trip_id] += alloc.quantity_units

    active_intervals_by_vehicle: Dict[str, List[Tuple[int, int, str]]] = {}
    for comm in scenario.commitments:
        if comm.state in (CommitmentState.RESERVED, CommitmentState.LOADING, CommitmentState.DISPATCHED, CommitmentState.DELIVERED):
            active_intervals_by_vehicle.setdefault(comm.vehicle_id, []).append(
                (comm.loading_start_min, comm.vehicle_release_min, f"committed:{comm.id}")
            )

    for t in plan.trips:
        loaded = alloc_by_trip.get(t.trip_id, 0)
        nested_loaded = sum(a.quantity_units for a in t.allocations)
        flat = Counter(json.dumps(a.model_dump(), sort_keys=True) for a in plan.allocations if a.trip_id == t.trip_id)
        nested = Counter(json.dumps(a.model_dump(), sort_keys=True) for a in t.allocations)
        if flat != nested:
            add_v("NESTED_ALLOCATION_MISMATCH", "Trip allocations differ from the validated plan allocations.", t.trip_id)
        if loaded != t.total_loaded_units or (t.allocations and nested_loaded != t.total_loaded_units):
            add_v(
                "TRIP_LOAD_MISMATCH",
                f"Trip '{t.trip_id}' total_loaded_units ({t.total_loaded_units}) != sum of allocations ({loaded}, nested={nested_loaded}).",
                t.trip_id,
            )
        if t.vehicle_id not in veh_map:
            add_v("UNKNOWN_VEHICLE", f"Trip '{t.trip_id}' uses unknown vehicle '{t.vehicle_id}'.", t.trip_id)
            continue
        if t.route_id not in route_map:
            add_v("MISSING_ROUTE", f"Trip '{t.trip_id}' uses unknown route '{t.route_id}'.", t.trip_id)
            continue
        if t.destination_id not in dest_map:
            add_v("UNKNOWN_DESTINATION", f"Trip '{t.trip_id}' uses unknown destination '{t.destination_id}'.", t.trip_id)
            continue

        veh = veh_map[t.vehicle_id]
        route = route_map[t.route_id]
        dest = dest_map[t.destination_id]
        if t.fixed_trip_cost_paise != route.fixed_trip_cost_paise:
            add_v("TRIP_COST_MISMATCH", "Trip fixed cost differs from its route.", t.trip_id)

        if route.destination_id != t.destination_id:
            add_v(
                "TRIP_ROUTE_DESTINATION_MISMATCH",
                f"Trip '{t.trip_id}' destination '{t.destination_id}' != route '{route.id}' destination '{route.destination_id}'.",
                t.trip_id,
            )

        if veh.unavailable:
            add_v("VEHICLE_UNAVAILABLE", f"Trip '{t.trip_id}' uses unavailable vehicle '{veh.id}'.", veh.id)
        if dest.closed:
            add_v("DESTINATION_CLOSED", f"Trip '{t.trip_id}' serves closed destination '{dest.id}'.", dest.id)
        if loaded > veh.capacity_units or t.total_loaded_units > veh.capacity_units:
            add_v(
                "VEHICLE_CAPACITY_EXCEEDED",
                f"Trip '{t.trip_id}' load ({max(loaded, t.total_loaded_units)}) exceeds vehicle '{veh.id}' capacity ({veh.capacity_units}).",
                t.trip_id,
                loaded=max(loaded, t.total_loaded_units),
                capacity=veh.capacity_units,
            )

        nom_timing = compute_trip_timing(route, dest, t.departure_min, extra_route_delay_min=0)
        buf_timing = compute_trip_timing(route, dest, t.departure_min, extra_route_delay_min=declared_extra_delay)

        def _matches_timing(cand_t: Dict[str, int]) -> bool:
            return (
                t.loading_start_min == cand_t["loading_start_min"]
                and t.arrival_min == cand_t["arrival_min"]
                and t.unloading_start_min == cand_t["unloading_start_min"]
                and t.unloading_end_min == cand_t["unloading_end_min"]
                and t.ready_for_sale_min == cand_t["ready_for_sale_min"]
                and t.vehicle_release_min == cand_t["vehicle_release_min"]
            )

        if not (_matches_timing(nom_timing) or (declared_extra_delay > 0 and _matches_timing(buf_timing))):
            add_v(
                "TRIP_TIMING_INCONSISTENT",
                f"Trip '{t.trip_id}' timestamps do not match canonical nominal or declared buffer (+{declared_extra_delay}m) route timing.",
                t.trip_id,
            )

        if t.loading_start_min < snapshot.decision_at_min:
            add_v("DEPARTURE_BEFORE_DECISION_TIME", f"Trip '{t.trip_id}' starts loading before decision time.", t.trip_id)
        if t.loading_start_min < veh.available_from_min or t.vehicle_release_min > veh.available_to_min:
            add_v("VEHICLE_SHIFT_VIOLATION", f"Trip '{t.trip_id}' violates shift of vehicle '{veh.id}'.", t.trip_id)
        if t.unloading_end_min > dest.receiving_close_min:
            add_v(
                "RECEIVING_WINDOW_VIOLATION",
                f"Trip '{t.trip_id}' unloading ends at {t.unloading_end_min} > receiving_close {dest.receiving_close_min}.",
                t.trip_id,
            )

        if loaded > 0:
            active_intervals_by_vehicle.setdefault(veh.id, []).append(
                (t.loading_start_min, t.vehicle_release_min, t.trip_id)
            )

    # 3. Check vehicle non-overlap across all active trips and locked commitments
    for v_id, intervals in active_intervals_by_vehicle.items():
        for i in range(len(intervals)):
            s1, e1, id1 = intervals[i]
            for j in range(i + 1, len(intervals)):
                s2, e2, id2 = intervals[j]
                if intervals_overlap(s1, e1, s2, e2):
                    if id1.startswith("committed:") and id2.startswith("committed:"):
                        warnings.append(ViolationItem(
                            code="EXISTING_COMMITMENT_OVERLAP",
                            message=f"Existing trips on {v_id} overlap between minutes {max(s1, s2)} and {min(e1, e2)}; operator intervention is required. New-plan validation covers remaining capacity only.",
                            entity_id=v_id, details={"trip_a": id1, "trip_b": id2},
                        ))
                        continue
                    add_v(
                        "VEHICLE_TRIP_OVERLAP",
                        f"Vehicle '{v_id}' has overlapping intervals: '{id1}' [{s1}, {e1}) and '{id2}' [{s2}, {e2}).",
                        v_id,
                        trip_a=id1,
                        trip_b=id2,
                    )

    # 4. Check allocation cross-field references, eligibility, single price tier per bucket, and demand/acceptance caps
    tiers_used_by_bucket: Dict[str, set[str]] = {}
    qty_by_bucket: Dict[str, int] = {}

    for alloc in plan.allocations:
        if alloc.destination_id not in dest_map:
            add_v(
                "UNKNOWN_ALLOCATION_DESTINATION",
                f"Allocation for batch '{alloc.batch_id}' references unknown destination '{alloc.destination_id}'.",
                alloc.batch_id,
            )
        if alloc.vehicle_id not in veh_map:
            add_v(
                "UNKNOWN_ALLOCATION_VEHICLE",
                f"Allocation for batch '{alloc.batch_id}' references unknown vehicle '{alloc.vehicle_id}'.",
                alloc.batch_id,
            )
        if alloc.route_id not in route_map:
            add_v(
                "UNKNOWN_ALLOCATION_ROUTE",
                f"Allocation for batch '{alloc.batch_id}' references unknown route '{alloc.route_id}'.",
                alloc.batch_id,
            )
        if alloc.bucket_id not in bucket_map:
            add_v(
                "UNKNOWN_DEMAND_BUCKET",
                f"Allocation for batch '{alloc.batch_id}' references unknown bucket_id '{alloc.bucket_id}'.",
                alloc.batch_id,
            )

        if alloc.batch_id not in batch_map or alloc.trip_id not in trip_map:
            continue

        b = batch_map[alloc.batch_id]
        t = trip_map[alloc.trip_id]
        veh = veh_map.get(t.vehicle_id)

        # Cross-field consistency between allocation and its parent trip
        if alloc.destination_id != t.destination_id:
            add_v(
                "ALLOCATION_TRIP_DESTINATION_MISMATCH",
                f"Allocation destination '{alloc.destination_id}' != trip '{t.trip_id}' destination '{t.destination_id}'.",
                alloc.trip_id,
            )
        if alloc.vehicle_id != t.vehicle_id:
            add_v(
                "ALLOCATION_TRIP_VEHICLE_MISMATCH",
                f"Allocation vehicle '{alloc.vehicle_id}' != trip '{t.trip_id}' vehicle '{t.vehicle_id}'.",
                alloc.trip_id,
            )
        if alloc.route_id != t.route_id:
            add_v(
                "ALLOCATION_TRIP_ROUTE_MISMATCH",
                f"Allocation route '{alloc.route_id}' != trip '{t.trip_id}' route '{t.route_id}'.",
                alloc.trip_id,
            )
        if alloc.departure_min != t.departure_min or alloc.ready_for_sale_min != t.ready_for_sale_min:
            add_v(
                "ALLOCATION_TIMING_MISMATCH",
                f"Allocation timing (dep={alloc.departure_min}, ready={alloc.ready_for_sale_min}) != trip '{t.trip_id}' timing (dep={t.departure_min}, ready={t.ready_for_sale_min}).",
                alloc.trip_id,
            )

        db = bucket_map.get(alloc.bucket_id)
        expected_db = dest_comm_to_bucket.get((t.destination_id, b.commodity_id))
        if db is None or expected_db is None or db.id != expected_db.id:
            add_v(
                "ALLOCATION_BUCKET_MISMATCH",
                f"Allocation bucket '{alloc.bucket_id}' does not match canonical bucket for ({t.destination_id}, {b.commodity_id}).",
                alloc.batch_id,
            )
            continue

        if db.destination_id != alloc.destination_id or db.commodity_id != alloc.commodity_id:
            add_v(
                "ALLOCATION_BUCKET_REFERENCE_MISMATCH",
                f"Allocation ({alloc.destination_id}, {alloc.commodity_id}) mismatches bucket '{db.id}' ({db.destination_id}, {db.commodity_id}).",
                alloc.batch_id,
            )

        tier_spec = next((ts for ts in db.price_tiers if ts.tier_id == alloc.tier_id), None)
        if tier_spec is None:
            add_v("UNKNOWN_PRICE_TIER", f"Allocation uses unknown tier '{alloc.tier_id}' on bucket '{db.id}'.", db.id)
        else:
            if alloc.unit_price_paise != tier_spec.price_paise_per_unit:
                add_v(
                    "TAMPERED_UNIT_PRICE",
                    f"Allocation unit_price_paise ({alloc.unit_price_paise}) != tier '{tier_spec.tier_id}' price ({tier_spec.price_paise_per_unit}).",
                    db.id,
                )
            if alloc.discount_pct != tier_spec.discount_pct:
                add_v(
                    "TAMPERED_DISCOUNT_PCT",
                    f"Allocation discount_pct ({alloc.discount_pct}) != tier '{tier_spec.tier_id}' discount ({tier_spec.discount_pct}).",
                    db.id,
                )

        if veh and veh.compatible_commodity_ids is not None and b.commodity_id not in veh.compatible_commodity_ids:
            add_v("INCOMPATIBLE_VEHICLE_COMMODITY", f"Vehicle '{veh.id}' cannot carry '{b.commodity_id}'.", alloc.trip_id)

        if t.ready_for_sale_min > db.sale_at_min:
            add_v(
                "ARRIVAL_AFTER_SALE_INSTANT",
                f"Trip '{t.trip_id}' ready at {t.ready_for_sale_min} > sale_at_min {db.sale_at_min}.",
                alloc.trip_id,
            )

        effective_expiry = b.estimated_expiry_nominal_min + declared_expiry_offset
        if not is_batch_fresh_for_sale(db.sale_at_min, db.freshness_buffer_min, b.estimated_expiry_nominal_min):
            add_v(
                "FRESHNESS_BUFFER_VIOLATION",
                f"Batch '{b.id}' expiry ({b.estimated_expiry_nominal_min}) violates strict inequality S_dc ({db.sale_at_min}) + B_dc ({db.freshness_buffer_min}) < E_b.",
                b.id,
            )
        elif declared_expiry_offset < 0 and not is_batch_fresh_for_sale(db.sale_at_min, db.freshness_buffer_min, effective_expiry):
            add_v(
                "BUFFERED_FRESHNESS_VIOLATION",
                f"Batch '{b.id}' buffered expiry ({effective_expiry}) violates strict inequality under profile offset {declared_expiry_offset}m.",
                b.id,
            )

        tiers_used_by_bucket.setdefault(db.id, set()).add(alloc.tier_id)
        qty_by_bucket[db.id] = qty_by_bucket.get(db.id, 0) + alloc.quantity_units

    for b_id, used_tiers in tiers_used_by_bucket.items():
        db = bucket_map[b_id]
        if len(used_tiers) > 1:
            add_v(
                "MULTIPLE_PRICE_TIERS_IN_BUCKET",
                f"Demand bucket '{b_id}' has multiple simultaneous price tiers allocated: {sorted(used_tiers)}.",
                b_id,
            )
        tier_id = next(iter(used_tiers))
        if b_id in snapshot.locked_tier_by_bucket and snapshot.locked_tier_by_bucket[b_id] != tier_id:
            add_v(
                "LOCKED_PRICE_TIER_VIOLATION",
                f"Bucket '{b_id}' tier '{tier_id}' conflicts with committed locked tier '{snapshot.locked_tier_by_bucket[b_id]}'.",
                b_id,
            )
        tier_spec = next((ts for ts in db.price_tiers if ts.tier_id == tier_id), None)
        if tier_spec is None:
            add_v("UNKNOWN_PRICE_TIER", f"Bucket '{b_id}' uses unknown tier '{tier_id}'.", b_id)
            continue

        residual_cap = snapshot.residual_cap_by_bucket.get(b_id, db.accepted_quantity_cap)
        allowed_max = min(tier_spec.forecast_sales_units, residual_cap)
        if qty_by_bucket[b_id] > allowed_max:
            add_v(
                "DEMAND_OR_ACCEPTANCE_CAP_EXCEEDED",
                f"Bucket '{b_id}' allocated {qty_by_bucket[b_id]} units > allowed limit {allowed_max} (forecast={tier_spec.forecast_sales_units}, residual_cap={residual_cap}).",
                b_id,
                allocated=qty_by_bucket[b_id],
                allowed_max=allowed_max,
            )

    return ValidationReport(
        is_valid=(len(violations) == 0),
        checked_under_assumptions=checked_under_assumptions,
        violations=violations,
        warnings=warnings,
    )
