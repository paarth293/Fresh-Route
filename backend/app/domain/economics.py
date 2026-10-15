"""Independent forecast and realized economic calculators (Section 7.6, 8.3 & 12.2).
Uses a single whole-cycle accounting boundary: every physical trip, variable transport, handling,
disposal, inspection, and cancellation is charged exactly once across the cycle.
sunk_committed_cost_paise records the portion of total operational costs already locked by commitments
without double-subtracting committed trips that are already in the trip list.
"""
from __future__ import annotations
from typing import Dict, List
from app.schemas.models import (
    AllocationItem,
    CommitmentState,
    EconomicBreakdown,
    PlanTrip,
    ScenarioPackage,
)


def compute_forecast_economics(
    scenario: ScenarioPackage,
    trips: List[PlanTrip],
    allocations: List[AllocationItem],
    unallocated_by_batch: Dict[str, int],
    sunk_committed_cost_paise: int = 0,
    inspection_cost_paise: int = 0,
    cancellation_cost_paise: int = 0,
) -> EconomicBreakdown:
    """Compute whole-cycle forecast economics under planning assumptions (Section 7.6).
    Includes both newly proposed trips/allocations and any active committed trips from
    scenario.commitments (deduplicated by trip_id) so each physical trip is charged once.
    """
    route_map = {r.id: r for r in scenario.routes}
    batch_map = {b.id: b for b in scenario.batches}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}
    H = scenario.evaluation_end_min

    gross_rev = 0
    var_trans = 0
    handling = 0

    plan_trip_ids = {t.trip_id for t in trips if t.total_loaded_units > 0}
    fixed_trip = sum(t.fixed_trip_cost_paise for t in trips if t.total_loaded_units > 0)

    for alloc in allocations:
        qty = alloc.quantity_units
        gross_rev += qty * alloc.unit_price_paise
        r = route_map[alloc.route_id]
        var_trans += qty * r.variable_transport_cost_paise_per_unit
        handling += qty * scenario.cost_model.get_handling_cost(alloc.commodity_id)

    # Include any active committed trips from scenario.commitments not already in `trips`
    committed_portion_paise = 0
    incurred_cancellations = scenario.cost_model.cancellation_cost_incurred_paise + int(cancellation_cost_paise)
    for comm in scenario.commitments:
        comm_cost = comm.incurred_fixed_cost_paise + comm.incurred_variable_cost_paise
        committed_portion_paise += comm_cost
        if comm.trip_id not in plan_trip_ids and comm.allocations:
            fixed_trip += comm.incurred_fixed_cost_paise
            for ca in comm.allocations:
                r = route_map[comm.route_id]
                var_trans += ca.quantity_units * r.variable_transport_cost_paise_per_unit
                handling += ca.quantity_units * scenario.cost_model.get_handling_cost(ca.commodity_id)
            for ca in comm.allocations:
                db = dest_comm_to_bucket.get((ca.destination_id, ca.commodity_id))
                if db:
                    ts = next((t for t in db.price_tiers if t.tier_id == ca.tier_id), db.price_tiers[0])
                    gross_rev += ca.quantity_units * ts.price_paise_per_unit

    spoilage_disposal = 0
    for b_id, unalloc_qty in unallocated_by_batch.items():
        if unalloc_qty <= 0:
            continue
        b = batch_map[b_id]
        if b.estimated_expiry_nominal_min <= H:
            spoilage_disposal += unalloc_qty * scenario.cost_model.get_disposal_cost(b.commodity_id)

    effective_sunk = max(int(sunk_committed_cost_paise), committed_portion_paise + int(inspection_cost_paise))

    # Whole-cycle net: each physical cost category is subtracted once
    net = (
        gross_rev
        - fixed_trip
        - var_trans
        - handling
        - spoilage_disposal
        - int(inspection_cost_paise)
        - incurred_cancellations
    )

    return EconomicBreakdown(
        is_forecast=True,
        gross_revenue_paise=gross_rev,
        fixed_trip_cost_paise=fixed_trip,
        variable_transport_cost_paise=var_trans,
        handling_cost_paise=handling,
        spoilage_disposal_cost_paise=spoilage_disposal,
        inspection_cost_paise=int(inspection_cost_paise),
        cancellation_cost_paise=incurred_cancellations,
        sunk_committed_cost_paise=effective_sunk,
        net_outcome_paise=net,
    )


def compute_realized_economics(
    gross_revenue_paise: int,
    fixed_trip_cost_paise: int,
    variable_transport_cost_paise: int,
    handling_cost_paise: int,
    spoilage_disposal_cost_paise: int,
    inspection_cost_paise: int = 0,
    cancellation_cost_paise: int = 0,
    sunk_committed_cost_paise: int = 0,
) -> EconomicBreakdown:
    """Reconcile whole-cycle realized economics from actual sales and incurred physical events (Section 8.3).
    Note: fixed_trip_cost_paise, variable_transport_cost_paise, and handling_cost_paise already include
    all executed trips (both committed and uncommitted). sunk_committed_cost_paise records the locked
    committed share of those costs for auditability and is never subtracted a second time.
    """
    net = (
        int(gross_revenue_paise)
        - int(fixed_trip_cost_paise)
        - int(variable_transport_cost_paise)
        - int(handling_cost_paise)
        - int(spoilage_disposal_cost_paise)
        - int(inspection_cost_paise)
        - int(cancellation_cost_paise)
    )
    return EconomicBreakdown(
        is_forecast=False,
        gross_revenue_paise=int(gross_revenue_paise),
        fixed_trip_cost_paise=int(fixed_trip_cost_paise),
        variable_transport_cost_paise=int(variable_transport_cost_paise),
        handling_cost_paise=int(handling_cost_paise),
        spoilage_disposal_cost_paise=int(spoilage_disposal_cost_paise),
        inspection_cost_paise=int(inspection_cost_paise),
        cancellation_cost_paise=int(cancellation_cost_paise),
        sunk_committed_cost_paise=int(sunk_committed_cost_paise),
        net_outcome_paise=net,
    )
