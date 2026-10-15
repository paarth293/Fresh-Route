"""Pure event reducer and commitment transitions (Section 4 & 8.6)."""
from __future__ import annotations
import copy
from typing import List
from app.planning.eligibility import compute_trip_timing, intervals_overlap
from app.domain.units import is_batch_fresh_for_sale
from app.domain.inventory import OperationalStateLedger
from app.schemas.models import (
    BatchStatus,
    Commitment,
    CommitmentState,
    CommittedAllocation,
    EventType,
    InspectionObservation,
    OperationalEvent,
    PlanResult,
)


def apply_operational_event(
    ledger: OperationalStateLedger,
    event: OperationalEvent,
) -> OperationalStateLedger:
    """Pure, idempotent state reducer: prior state + event -> next state.
    If event.id has already been applied, returns an unchanged copy (Test T12).
    """
    next_ledger = copy.deepcopy(ledger)

    if event.id in next_ledger.applied_event_ids:
        return next_ledger

    if event.effective_at_min < ledger.current_time_min:
        raise ValueError("EVENT_IN_PAST: Events cannot move or revise the operational clock backwards.")
    if event.effective_at_min > ledger.scenario.evaluation_end_min:
        raise ValueError("EVENT_AFTER_HORIZON: Event is beyond the operating cycle.")
    targets = {
        EventType.ROUTE_DELAY_OBSERVED: {r.id for r in ledger.scenario.routes},
        EventType.VEHICLE_UNAVAILABLE: {v.id for v in ledger.scenario.vehicles},
        EventType.DEMAND_UPDATED: {d.id for d in ledger.scenario.demand_buckets} | {d.id for d in ledger.scenario.destinations},
        EventType.BATCH_ESTIMATE_UPDATED: {b.id for b in ledger.scenario.batches},
        EventType.DESTINATION_CLOSED: {d.id for d in ledger.scenario.destinations},
        EventType.INSPECTION_EXECUTED: {b.id for b in ledger.scenario.batches},
    }
    if event.event_type in targets and event.entity_id not in targets[event.event_type]:
        raise ValueError("UNKNOWN_EVENT_TARGET: Entity does not exist in this scenario.")
    if event.event_type == EventType.ROUTE_DELAY_OBSERVED and int(event.payload.get("delay_minutes", 0)) < 0:
        raise ValueError("INVALID_DELAY: Delay must be nonnegative.")

    if event.expected_prior_revision is not None and event.expected_prior_revision != next_ledger.state_revision:
        raise ValueError(
            f"STATE_REVISION_CONFLICT: expected revision {event.expected_prior_revision}, actual {next_ledger.state_revision}"
        )

    if event.effective_at_min > next_ledger.current_time_min:
        next_ledger.current_time_min = event.effective_at_min
        next_ledger.scenario.decision_at_min = event.effective_at_min

    # Advance any reserved commitments whose loading/dispatch time has passed into loading/dispatched state
    _advance_time_triggered_commitments(next_ledger, next_ledger.current_time_min)

    et = event.event_type
    payload = event.payload

    if et == EventType.ROUTE_DELAY_OBSERVED:
        delay_add = int(payload.get("delay_minutes", 0))
        for route in next_ledger.scenario.routes:
            if route.id == event.entity_id:
                route.observed_delay_min += delay_add
                route.outbound_travel_min += delay_add
        # Also delay arrival and release of any in-transit or reserved commitment on that route
        for comm in next_ledger.commitments.values():
            if comm.route_id == event.entity_id and comm.state in (
                CommitmentState.RESERVED,
                CommitmentState.LOADING,
                CommitmentState.DISPATCHED,
            ):
                comm.arrival_min += delay_add
                comm.unloading_end_min += delay_add
                comm.ready_for_sale_min += delay_add
                comm.vehicle_release_min += delay_add

    elif et == EventType.VEHICLE_UNAVAILABLE:
        for v in next_ledger.scenario.vehicles:
            if v.id == event.entity_id:
                v.unavailable = bool(payload.get("unavailable", True))
                if "available_from_min" in payload:
                    v.available_from_min = int(payload["available_from_min"])

    elif et == EventType.DEMAND_UPDATED:
        multiplier = float(payload.get("multiplier", 1.0))
        new_cap = payload.get("accepted_quantity_cap")
        for db in next_ledger.scenario.demand_buckets:
            if db.id == event.entity_id or db.destination_id == event.entity_id:
                if new_cap is not None:
                    db.accepted_quantity_cap = max(0, int(new_cap))
                for tier in db.price_tiers:
                    if "forecast_by_tier" in payload and tier.tier_id in payload["forecast_by_tier"]:
                        tier.forecast_sales_units = min(
                            db.accepted_quantity_cap,
                            max(0, int(payload["forecast_by_tier"][tier.tier_id])),
                        )
                    else:
                        tier.forecast_sales_units = min(
                            db.accepted_quantity_cap,
                            max(0, int(tier.forecast_sales_units * multiplier)),
                        )

    elif et == EventType.BATCH_ESTIMATE_UPDATED:
        for b in next_ledger.scenario.batches:
            if b.id == event.entity_id:
                elapsed = event.effective_at_min - b.observed_at_min
                old_nominal = b.remaining_life_nominal_min
                for field in ("remaining_life_nominal_min", "remaining_life_low_min", "remaining_life_high_min"):
                    previous = getattr(b, field)
                    setattr(b, field, max(0, (old_nominal if previous is None else previous) - elapsed))
                b.observed_at_min = event.effective_at_min
                if "remaining_life_nominal_min" in payload:
                    # A nominal-only new observation shifts the old uncertainty
                    # interval by the same amount, preserving its width.
                    shift = int(payload["remaining_life_nominal_min"]) - b.remaining_life_nominal_min
                    b.remaining_life_low_min = max(0, b.remaining_life_low_min + shift)
                    b.remaining_life_high_min = max(0, b.remaining_life_high_min + shift)
                    b.remaining_life_nominal_min = int(payload["remaining_life_nominal_min"])
                if "remaining_life_low_min" in payload:
                    b.remaining_life_low_min = int(payload["remaining_life_low_min"])
                if "remaining_life_high_min" in payload:
                    b.remaining_life_high_min = int(payload["remaining_life_high_min"])
                if "status" in payload:
                    b.status = BatchStatus(payload["status"])
                if not 0 <= b.remaining_life_low_min <= b.remaining_life_nominal_min <= b.remaining_life_high_min:
                    raise ValueError("INVALID_LIFE_ORDERING: Require 0 <= low <= nominal <= high.")

    elif et == EventType.DESTINATION_CLOSED:
        for d in next_ledger.scenario.destinations:
            if d.id == event.entity_id:
                d.closed = bool(payload.get("closed", True))

    elif et == EventType.INSPECTION_EXECUTED:
        obs = InspectionObservation.model_validate(payload["observation"])
        next_ledger.inspection_history.append(obs)
        next_ledger.sunk_cost_paise += obs.cost_paise
        for b in next_ledger.scenario.batches:
            if b.id == obs.batch_id:
                b.observed_at_min = obs.observed_at_min
                b.remaining_life_nominal_min = obs.updated_remaining_life_nominal_min
                b.remaining_life_low_min = obs.updated_remaining_life_low_min
                b.remaining_life_high_min = obs.updated_remaining_life_high_min
        if "posterior_world_weights" in payload and payload["posterior_world_weights"]:
            next_ledger.posterior_world_weights = {
                str(k): float(v) for k, v in payload["posterior_world_weights"].items()
            }

    elif et == EventType.RESERVATION_RELEASED:
        comm = next_ledger.commitments.get(event.entity_id)
        if comm is None:
            raise ValueError("UNKNOWN_COMMITMENT: Reservation does not exist.")
        if comm.state != CommitmentState.RESERVED or next_ledger.current_time_min >= comm.loading_start_min:
            raise ValueError("LOCKED_COMMITMENT: Loading or dispatched stock cannot be released.")
        for allocation in comm.allocations:
            next_ledger.reserved_stock[allocation.batch_id] -= allocation.quantity_units
            next_ledger.warehouse_unreserved[allocation.batch_id] += allocation.quantity_units
        fee = next_ledger.scenario.cost_model.cancellation_fee_paise_per_trip
        next_ledger.scenario.cost_model.cancellation_cost_incurred_paise += fee
        next_ledger.sunk_cost_paise += fee
        del next_ledger.commitments[comm.id]
        bucket_ids = {(b.destination_id, b.commodity_id): b.id for b in next_ledger.scenario.demand_buckets}
        next_ledger.locked_tier_by_bucket = {
            bucket_ids[(a.destination_id, a.commodity_id)]: a.tier_id
            for remaining in next_ledger.commitments.values() for a in remaining.allocations
            if (a.destination_id, a.commodity_id) in bucket_ids
        }

    elif et == EventType.RESERVATION_RESCHEDULED:
        comm = next_ledger.commitments.get(event.entity_id)
        if comm is None:
            raise ValueError("UNKNOWN_COMMITMENT: Reservation does not exist.")
        departure = payload.get("departure_min")
        if isinstance(departure, bool) or not isinstance(departure, int):
            raise ValueError("Departure must be an integer number of minutes.")
        timing = validate_reserved_departure(next_ledger, comm.id, departure)
        for name, value in timing.items():
            if hasattr(comm, name):
                setattr(comm, name, value)

    elif et in (EventType.TRIP_LOADING_STARTED, EventType.TRIP_DISPATCHED, EventType.TRIP_DELIVERED):
        comm = next((c for c in next_ledger.commitments.values() if c.id == event.entity_id or c.trip_id == event.entity_id), None)
        if comm is None:
            raise ValueError("UNKNOWN_COMMITMENT: Trip has not been committed.")
        required_time = {
            EventType.TRIP_LOADING_STARTED: comm.loading_start_min,
            EventType.TRIP_DISPATCHED: comm.departure_min,
            EventType.TRIP_DELIVERED: comm.unloading_end_min,
        }[et]
        if event.effective_at_min < required_time:
            raise ValueError("EARLY_TRIP_TRANSITION: Transition precedes its scheduled time.")
        if et == EventType.TRIP_DELIVERED:
            if comm.state != CommitmentState.DISPATCHED:
                raise ValueError("INVALID_TRIP_TRANSITION: Only dispatched trips can be delivered.")
            for alloc in comm.allocations:
                next_ledger.in_transit_stock[alloc.batch_id] -= alloc.quantity_units
                next_ledger.destination_stock[alloc.batch_id] += alloc.quantity_units
            comm.state = CommitmentState.DELIVERED
        elif et == EventType.TRIP_DISPATCHED and comm.state != CommitmentState.DISPATCHED:
            raise ValueError("INVALID_TRIP_TRANSITION: Trip is not awaiting dispatch.")
        elif et == EventType.TRIP_LOADING_STARTED and comm.state != CommitmentState.LOADING:
            raise ValueError("INVALID_TRIP_TRANSITION: Trip is not awaiting loading.")

    else:
        raise ValueError(f"UNSUPPORTED_EVENT_TYPE: {et.value}")

    next_ledger.applied_event_ids.add(event.id)
    next_ledger.event_log.append(event)
    next_ledger.state_revision += 1
    next_ledger.scenario.commitments = list(next_ledger.commitments.values())

    if not next_ledger.verify_conservation():
        raise RuntimeError(f"Stock conservation invariant violated after event {event.id}")

    return next_ledger


def validate_reserved_departure(ledger, commitment_id, departure):
    """Validate a same-route move before loading; preserve stock, tier, and cost."""
    comm = ledger.commitments[commitment_id]
    if comm.state != CommitmentState.RESERVED or ledger.current_time_min >= comm.loading_start_min:
        raise ValueError("LOCKED_COMMITMENT: Only reservations that have not started loading can be moved.")
    route = next(r for r in ledger.scenario.routes if r.id == comm.route_id)
    destination = next(d for d in ledger.scenario.destinations if d.id == comm.destination_id)
    vehicle = next(v for v in ledger.scenario.vehicles if v.id == comm.vehicle_id)
    timing = compute_trip_timing(route, destination, departure)
    if destination.closed or vehicle.unavailable:
        raise ValueError("Destination or vehicle is unavailable.")
    if timing['loading_start_min'] < max(ledger.current_time_min, vehicle.available_from_min) or timing['vehicle_release_min'] > min(vehicle.available_to_min, ledger.scenario.evaluation_end_min):
        raise ValueError("Proposed reservation is outside the available vehicle shift or planning horizon.")
    if timing['unloading_end_min'] > destination.receiving_close_min:
        raise ValueError("Proposed reservation misses the receiving window.")
    for other in ledger.commitments.values():
        if other.id != comm.id and other.vehicle_id == comm.vehicle_id and other.state != CommitmentState.CLOSED:
            if intervals_overlap(timing['loading_start_min'], timing['vehicle_release_min'], other.loading_start_min, other.vehicle_release_min):
                raise ValueError("Proposed reservation overlaps another committed trip.")
    batches = {b.id: b for b in ledger.scenario.batches}
    buckets = {(b.destination_id, b.commodity_id): b for b in ledger.scenario.demand_buckets}
    for allocation in comm.allocations:
        batch = batches[allocation.batch_id]
        bucket = buckets[(allocation.destination_id, allocation.commodity_id)]
        if timing['ready_for_sale_min'] > bucket.sale_at_min or not is_batch_fresh_for_sale(sale_at_min=bucket.sale_at_min, freshness_buffer_min=bucket.freshness_buffer_min, absolute_expiry_min=batch.estimated_expiry_nominal_min):
            raise ValueError("Proposed reservation misses the sale or freshness deadline.")
    return timing


def next_reserved_departure(ledger, commitment_id):
    comm = ledger.commitments[commitment_id]
    route = next(r for r in ledger.scenario.routes if r.id == comm.route_id)
    candidates = {comm.departure_min + 1, ledger.current_time_min + route.loading_duration_min}
    candidates.update(other.vehicle_release_min + route.loading_duration_min for other in ledger.commitments.values() if other.id != comm.id and other.vehicle_id == comm.vehicle_id)
    for departure in sorted(d for d in candidates if d > comm.departure_min):
        try:
            return validate_reserved_departure(ledger, commitment_id, departure)
        except ValueError:
            continue
    raise ValueError("NO_FEASIBLE_RESERVATION_SLOT: No later departure preserves all commitments and deadlines.")


def _advance_time_triggered_commitments(ledger: OperationalStateLedger, current_time_min: int) -> None:
    """Transition RESERVED commitments to LOADING or DISPATCHED when current_time_min passes their timestamps."""
    for comm in ledger.commitments.values():
        if comm.state == CommitmentState.RESERVED and current_time_min >= comm.loading_start_min:
            # Move from reserved to loading/dispatched and incur cost once
            for alloc in comm.allocations:
                qty = alloc.quantity_units
                ledger.reserved_stock[alloc.batch_id] -= qty
                if current_time_min >= comm.departure_min:
                    ledger.in_transit_stock[alloc.batch_id] += qty
                else:
                    ledger.loading_stock[alloc.batch_id] += qty
            comm.state = (
                CommitmentState.DISPATCHED if current_time_min >= comm.departure_min else CommitmentState.LOADING
            )
            ledger.sunk_cost_paise += comm.incurred_fixed_cost_paise + comm.incurred_variable_cost_paise
        elif comm.state == CommitmentState.LOADING and current_time_min >= comm.departure_min:
            for alloc in comm.allocations:
                qty = alloc.quantity_units
                ledger.loading_stock[alloc.batch_id] -= qty
                ledger.in_transit_stock[alloc.batch_id] += qty
            comm.state = CommitmentState.DISPATCHED


def commit_plan_to_ledger(
    ledger: OperationalStateLedger,
    plan: PlanResult,
    expected_revision: int,
    idempotency_key: str,
    trip_ids_to_commit: List[str] | None = None,
    commit_state: CommitmentState = CommitmentState.RESERVED,
) -> OperationalStateLedger:
    """Atomically commit selected trips (or all trips) from a validated PlanResult into the ledger."""
    event_id = f"commit_{idempotency_key}"
    if event_id in ledger.applied_event_ids:
        return copy.deepcopy(ledger)
    if commit_state not in (CommitmentState.RESERVED, CommitmentState.LOADING, CommitmentState.DISPATCHED):
        raise ValueError("INVALID_COMMIT_STATE: Commit as reserved, loading, or dispatched.")
    if trip_ids_to_commit is not None and (not trip_ids_to_commit or set(trip_ids_to_commit) - {t.trip_id for t in plan.trips}):
        raise ValueError("INVALID_TRIP_SELECTION: Select existing trips or omit the selection for all trips.")

    if ledger.state_revision != expected_revision or plan.state_revision != ledger.state_revision:
        raise ValueError(
            f"STATE_REVISION_CONFLICT: expected revision {expected_revision}, actual {ledger.state_revision}"
        )

    if not plan.validation.is_valid:
        raise ValueError("INVALID_PLAN_COMMIT: Cannot commit a plan that failed independent validation.")

    next_ledger = copy.deepcopy(ledger)
    route_map = {r.id: r for r in next_ledger.scenario.routes}
    bucket_map = {(db.destination_id, db.commodity_id): db.id for db in next_ledger.scenario.demand_buckets}

    selected_trips = [
        t for t in plan.trips if (trip_ids_to_commit is None or t.trip_id in trip_ids_to_commit) and t.total_loaded_units > 0
    ]

    for pt in selected_trips:
        route = route_map[pt.route_id]
        comm_allocs: List[CommittedAllocation] = []
        var_cost = 0
        for alloc in pt.allocations:
            qty = alloc.quantity_units
            if next_ledger.warehouse_unreserved.get(alloc.batch_id, 0) < qty:
                raise ValueError(f"Insufficient unreserved warehouse stock for batch '{alloc.batch_id}' during commit.")
            next_ledger.warehouse_unreserved[alloc.batch_id] -= qty
            if commit_state == CommitmentState.RESERVED:
                next_ledger.reserved_stock[alloc.batch_id] += qty
            elif commit_state == CommitmentState.DISPATCHED:
                next_ledger.in_transit_stock[alloc.batch_id] += qty
            elif commit_state == CommitmentState.LOADING:
                next_ledger.loading_stock[alloc.batch_id] += qty

            comm_allocs.append(
                CommittedAllocation(
                    batch_id=alloc.batch_id,
                    commodity_id=alloc.commodity_id,
                    destination_id=alloc.destination_id,
                    tier_id=alloc.tier_id,
                    quantity_units=qty,
                )
            )
            unit_var = route.variable_transport_cost_paise_per_unit + next_ledger.scenario.cost_model.get_handling_cost(
                alloc.commodity_id
            )
            var_cost += qty * unit_var

            b_id = bucket_map.get((alloc.destination_id, alloc.commodity_id))
            if b_id:
                next_ledger.locked_tier_by_bucket[b_id] = alloc.tier_id

        comm_id = f"comm_{plan.plan_id}_{pt.trip_id}"
        comm = Commitment(
            id=comm_id,
            plan_id=plan.plan_id,
            trip_id=pt.trip_id,
            vehicle_id=pt.vehicle_id,
            route_id=pt.route_id,
            destination_id=pt.destination_id,
            departure_min=pt.departure_min,
            loading_start_min=pt.loading_start_min,
            arrival_min=pt.arrival_min,
            unloading_end_min=pt.unloading_end_min,
            ready_for_sale_min=pt.ready_for_sale_min,
            vehicle_release_min=pt.vehicle_release_min,
            state=commit_state,
            allocations=comm_allocs,
            effective_at_min=next_ledger.current_time_min,
            incurred_fixed_cost_paise=pt.fixed_trip_cost_paise,
            incurred_variable_cost_paise=var_cost,
        )
        if commit_state in (CommitmentState.LOADING, CommitmentState.DISPATCHED, CommitmentState.DELIVERED):
            next_ledger.sunk_cost_paise += comm.incurred_fixed_cost_paise + comm.incurred_variable_cost_paise
        next_ledger.commitments[comm_id] = comm

    next_ledger.applied_event_ids.add(event_id)
    next_ledger.state_revision += 1
    next_ledger.scenario.commitments = list(next_ledger.commitments.values())

    if not next_ledger.verify_conservation():
        raise RuntimeError("Stock conservation violated during plan commit.")

    return next_ledger
