"""Trip timing and eligibility checks (Section 7.3 & 7.5)."""
from __future__ import annotations
from typing import Dict, Tuple
from app.domain.units import is_batch_fresh_for_sale
from app.schemas.models import (
    Batch,
    BatchStatus,
    CandidateTrip,
    DemandBucket,
    Destination,
    Route,
    Vehicle,
)


def compute_trip_timing(
    route: Route,
    destination: Destination,
    departure_min: int,
    extra_outbound_delay_min: int = 0,
    extra_route_delay_min: int = 0,
) -> Dict[str, int]:
    """Compute canonical trip timestamps (Section 7.3):
    loading_start = departure - loading_duration
    arrival = departure + outbound_travel
    unloading_start = max(arrival, receiving_open)
    unloading_end = unloading_start + unloading_duration
    ready_for_sale = unloading_end + downstream_handling_duration
    vehicle_release = unloading_end + return_travel + turnaround_duration
    """
    outbound = route.outbound_travel_min + extra_outbound_delay_min + extra_route_delay_min
    loading_start = departure_min - route.loading_duration_min
    arrival = departure_min + outbound
    unloading_start = max(arrival, destination.receiving_open_min)
    unloading_end = unloading_start + route.unloading_duration_min
    ready_for_sale = unloading_end + route.downstream_handling_duration_min
    vehicle_release = unloading_end + route.return_travel_min + route.turnaround_duration_min
    return {
        "loading_start_min": loading_start,
        "departure_min": departure_min,
        "arrival_min": arrival,
        "unloading_start_min": unloading_start,
        "unloading_end_min": unloading_end,
        "ready_for_sale_min": ready_for_sale,
        "vehicle_release_min": vehicle_release,
    }


def intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    """Half-open interval overlap check for [start_a, end_a) and [start_b, end_b).
    A trip may start loading at the exact minute the previous trip releases the vehicle (Section 7.5).
    """
    return (int(start_a) < int(end_b)) and (int(start_b) < int(end_a))


def check_trip_feasibility(
    vehicle: Vehicle,
    route: Route,
    destination: Destination,
    timing: Dict[str, int],
    decision_at_min: int,
    evaluation_end_min: int,
) -> Tuple[bool, str]:
    """Check whether a candidate trip satisfies vehicle shift, decision time, and receiving window rules."""
    if vehicle.unavailable or vehicle.capacity_units <= 0:
        return False, "VEHICLE_UNAVAILABLE"
    if destination.closed:
        return False, "DESTINATION_CLOSED"
    if timing["loading_start_min"] < decision_at_min:
        return False, "LOADING_BEFORE_DECISION_TIME"
    if timing["loading_start_min"] < vehicle.available_from_min:
        return False, "BEFORE_VEHICLE_SHIFT"
    if timing["vehicle_release_min"] > vehicle.available_to_min:
        return False, "AFTER_VEHICLE_SHIFT"
    if timing["unloading_end_min"] > destination.receiving_close_min:
        return False, "UNLOADING_AFTER_RECEIVING_CLOSE"
    if timing["arrival_min"] > evaluation_end_min:
        return False, "ARRIVAL_AFTER_HORIZON"
    return True, "OK"


def check_batch_trip_bucket_eligibility(
    batch: Batch,
    vehicle: Vehicle,
    destination: Destination,
    bucket: DemandBucket,
    trip: CandidateTrip,
    decision_at_min: int,
    expiry_override_min: int | None = None,
) -> Tuple[bool, str]:
    """Check whether batch b can be allocated on trip r for demand bucket (d, c)."""
    if batch.status != BatchStatus.AVAILABLE:
        return False, f"BATCH_STATUS_{batch.status.value.upper()}"
    if batch.commodity_id != bucket.commodity_id:
        return False, "COMMODITY_MISMATCH"
    if trip.destination_id != destination.id or bucket.destination_id != destination.id:
        return False, "DESTINATION_MISMATCH"
    if destination.closed:
        return False, "DESTINATION_CLOSED"
    if vehicle.unavailable:
        return False, "VEHICLE_UNAVAILABLE"
    if vehicle.compatible_commodity_ids is not None and batch.commodity_id not in vehicle.compatible_commodity_ids:
        return False, "VEHICLE_COMMODITY_INCOMPATIBLE"

    est_expiry = expiry_override_min if expiry_override_min is not None else batch.estimated_expiry_nominal_min
    if est_expiry <= decision_at_min:
        return False, "BATCH_ALREADY_EXPIRED"

    if trip.loading_start_min < decision_at_min:
        return False, "LOADING_BEFORE_DECISION_TIME"
    if trip.unloading_end_min > destination.receiving_close_min:
        return False, "UNLOADING_AFTER_RECEIVING_CLOSE"
    if trip.ready_for_sale_min > bucket.sale_at_min:
        return False, "READY_AFTER_SALE_INSTANT"

    if not is_batch_fresh_for_sale(bucket.sale_at_min, bucket.freshness_buffer_min, est_expiry):
        return False, "INSUFFICIENT_FRESHNESS_AT_SALE"

    return True, "OK"
