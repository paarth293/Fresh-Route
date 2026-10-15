"""Deterministic replay evaluator (Section 8.2, 8.3 & 12.2)."""
from __future__ import annotations
from typing import Any, Dict, List
from app.domain.economics import compute_realized_economics
from app.domain.units import is_batch_fresh_for_sale
from app.schemas.models import (
    AllocationItem,
    BatchLedgerReconciliation,
    PlanResult,
    PlanTrip,
    ReplayResult,
    ScenarioPackage,
    SimulationWorld,
)


def replay_plan_in_world(
    scenario: ScenarioPackage,
    plan: PlanResult,
    world: SimulationWorld,
    inspection_cost_paise: int = 0,
    cancellation_cost_paise: int = 0,
    sunk_committed_cost_paise: int = 0,
) -> ReplayResult:
    """Replay a proposed/committed PlanResult against a fixed hidden SimulationWorld (Section 8.2).
    Follows all 10 steps of the Replay Algorithm and verifies stock & economic conservation.
    """
    batch_map = {b.id: b for b in scenario.batches}
    route_map = {r.id: r for r in scenario.routes}
    dest_map = {d.id: d for d in scenario.destinations}
    bucket_map = {db.id: db for db in scenario.demand_buckets}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}
    H = scenario.evaluation_end_min

    # Track physical batch quantities at origin vs destination
    origin_rem: Dict[str, int] = {b.id: b.quantity_units for b in scenario.batches}
    dispatched_by_batch: Dict[str, int] = {b.id: 0 for b in scenario.batches}
    sold_by_batch: Dict[str, int] = {b.id: 0 for b in scenario.batches}
    dest_unsold_by_batch: Dict[str, int] = {b.id: 0 for b in scenario.batches}

    # Include any already-committed trips from scenario.commitments that aren't duplicated in plan.trips
    plan_trip_ids = {t.trip_id for t in plan.trips}
    all_trips: List[PlanTrip] = [t for t in plan.trips if t.total_loaded_units > 0]

    committed_cost_share = 0
    incurred_cancellations = scenario.cost_model.cancellation_cost_incurred_paise + int(cancellation_cost_paise)
    for comm in scenario.commitments:
        committed_cost_share += comm.incurred_fixed_cost_paise + comm.incurred_variable_cost_paise
        if comm.trip_id not in plan_trip_ids and comm.allocations:
            # Convert Commitment into a PlanTrip for unified replay execution
            c_allocs: List[AllocationItem] = []
            for ca in comm.allocations:
                db = dest_comm_to_bucket.get((ca.destination_id, ca.commodity_id))
                tier_spec = next((ts for ts in db.price_tiers if ts.tier_id == ca.tier_id), db.price_tiers[0]) if db else None
                c_allocs.append(
                    AllocationItem(
                        batch_id=ca.batch_id,
                        commodity_id=ca.commodity_id,
                        trip_id=comm.trip_id,
                        vehicle_id=comm.vehicle_id,
                        route_id=comm.route_id,
                        destination_id=ca.destination_id,
                        bucket_id=db.id if db else "",
                        tier_id=ca.tier_id,
                        discount_pct=tier_spec.discount_pct if tier_spec else 0,
                        unit_price_paise=tier_spec.price_paise_per_unit if tier_spec else 0,
                        quantity_units=ca.quantity_units,
                        departure_min=comm.departure_min,
                        ready_for_sale_min=comm.ready_for_sale_min,
                    )
                )
            all_trips.append(
                PlanTrip(
                    trip_id=comm.trip_id,
                    vehicle_id=comm.vehicle_id,
                    route_id=comm.route_id,
                    destination_id=comm.destination_id,
                    departure_min=comm.departure_min,
                    loading_start_min=comm.loading_start_min,
                    arrival_min=comm.arrival_min,
                    unloading_start_min=comm.arrival_min,
                    unloading_end_min=comm.unloading_end_min,
                    ready_for_sale_min=comm.ready_for_sale_min,
                    vehicle_release_min=comm.vehicle_release_min,
                    total_loaded_units=sum(x.quantity_units for x in c_allocs),
                    capacity_units=sum(x.quantity_units for x in c_allocs),
                    fixed_trip_cost_paise=comm.incurred_fixed_cost_paise,
                    allocations=c_allocs,
                )
            )

    # Sort trips by vehicle and planned departure time to propagate realized vehicle release delays (Step 4)
    trips_by_vehicle: Dict[str, List[PlanTrip]] = {}
    for t in all_trips:
        trips_by_vehicle.setdefault(t.vehicle_id, []).append(t)

    incurred_fixed_trip_cost = 0
    incurred_var_transport_cost = 0
    incurred_handling_cost = 0
    service_failures = 0
    failure_events: List[Dict[str, Any]] = []

    # Arrived deliveries ready for sale per bucket_id: list of dicts
    arrived_deliveries_by_bucket: Dict[str, List[Dict[str, Any]]] = {db.id: [] for db in scenario.demand_buckets}
    selected_tier_by_bucket: Dict[str, str] = dict(plan.selected_tier_by_bucket)

    for v_id, v_trips in sorted(trips_by_vehicle.items()):
        v_trips_sorted = sorted(v_trips, key=lambda x: (x.departure_min, x.trip_id))
        vehicle_available_at = 0
        for t in v_trips_sorted:
            route = route_map[t.route_id]
            dest = dest_map[t.destination_id]

            # Propagate delay if previous trip on the same vehicle released late
            actual_loading_start = max(t.loading_start_min, vehicle_available_at)
            actual_departure = actual_loading_start + route.loading_duration_min
            realized_outbound = world.realized_outbound_travel_by_route.get(route.id, route.outbound_travel_min)
            realized_return = world.realized_return_travel_by_route.get(route.id, route.return_travel_min)

            actual_arrival = actual_departure + realized_outbound
            actual_unloading_start = max(actual_arrival, dest.receiving_open_min)
            actual_unloading_end = actual_unloading_start + route.unloading_duration_min
            actual_ready = actual_unloading_end + route.downstream_handling_duration_min
            actual_release = actual_unloading_end + realized_return + route.turnaround_duration_min
            vehicle_available_at = actual_release

            # Execute departure & incur transport/handling costs once
            incurred_fixed_trip_cost += t.fixed_trip_cost_paise

            for alloc in t.allocations:
                qty = min(alloc.quantity_units, origin_rem.get(alloc.batch_id, 0))
                if qty <= 0:
                    continue
                origin_rem[alloc.batch_id] -= qty
                dispatched_by_batch[alloc.batch_id] += qty
                incurred_var_transport_cost += qty * route.variable_transport_cost_paise_per_unit
                incurred_handling_cost += qty * scenario.cost_model.get_handling_cost(alloc.commodity_id)

                db = bucket_map.get(alloc.bucket_id) or dest_comm_to_bucket.get((alloc.destination_id, alloc.commodity_id))
                if db is None:
                    dest_unsold_by_batch[alloc.batch_id] += qty
                    continue

                selected_tier_by_bucket[db.id] = alloc.tier_id

                # Check if destination closed or arrived after receiving close or ready after sale instant
                if dest.closed:
                    service_failures += 1
                    failure_events.append(
                        {
                            "trip_id": t.trip_id,
                            "batch_id": alloc.batch_id,
                            "destination_id": dest.id,
                            "reason": "DESTINATION_CLOSED",
                            "quantity_units": qty,
                        }
                    )
                    dest_unsold_by_batch[alloc.batch_id] += qty
                elif actual_unloading_end > dest.receiving_close_min:
                    service_failures += 1
                    failure_events.append(
                        {
                            "trip_id": t.trip_id,
                            "batch_id": alloc.batch_id,
                            "destination_id": dest.id,
                            "reason": "LATE_ARRIVAL_PAST_RECEIVING_CLOSE",
                            "actual_unloading_end_min": actual_unloading_end,
                            "receiving_close_min": dest.receiving_close_min,
                            "quantity_units": qty,
                        }
                    )
                    dest_unsold_by_batch[alloc.batch_id] += qty
                elif actual_ready > db.sale_at_min:
                    service_failures += 1
                    failure_events.append(
                        {
                            "trip_id": t.trip_id,
                            "batch_id": alloc.batch_id,
                            "destination_id": dest.id,
                            "reason": "READY_AFTER_SALE_INSTANT",
                            "actual_ready_min": actual_ready,
                            "sale_at_min": db.sale_at_min,
                            "quantity_units": qty,
                        }
                    )
                    dest_unsold_by_batch[alloc.batch_id] += qty
                else:
                    arrived_deliveries_by_bucket[db.id].append(
                        {
                            "batch_id": alloc.batch_id,
                            "trip_id": t.trip_id,
                            "tier_id": alloc.tier_id,
                            "unit_price_paise": alloc.unit_price_paise,
                            "quantity_units": qty,
                            "true_expiry_min": world.true_expiry_by_batch[alloc.batch_id],
                        }
                    )

    # Process sales at each bucket's sale_at_min (Steps 6–8 of Replay Algorithm)
    gross_revenue_paise = 0
    realized_tier_demand_total = 0
    reference_full_price_demand_total = 0
    reference_fulfilled_total = 0
    unmet_demand_total = 0

    for db in sorted(scenario.demand_buckets, key=lambda x: (x.sale_at_min, x.id)):
        full_tier = sorted(db.price_tiers, key=lambda ts: ts.discount_pct)[0]
        chosen_tier_id = selected_tier_by_bucket.get(db.id, full_tier.tier_id)
        realized_dem = world.realized_demand_by_bucket_tier.get(db.id, {}).get(chosen_tier_id, 0)
        ref_dem = world.realized_demand_by_bucket_tier.get(db.id, {}).get(full_tier.tier_id, 0)

        realized_tier_demand_total += realized_dem
        reference_full_price_demand_total += ref_dem

        deliveries = arrived_deliveries_by_bucket.get(db.id, [])
        # Issue FEFO by true_expiry_min, tie-breaking by batch_id
        deliveries_sorted = sorted(deliveries, key=lambda d: (d["true_expiry_min"], d["batch_id"]))

        rem_demand = realized_dem
        bucket_sold = 0

        for item in deliveries_sorted:
            b_id = item["batch_id"]
            qty = item["quantity_units"]
            true_exp = item["true_expiry_min"]

            # Check true expiry quality rule at sale instant: S_dc + B_dc < true_expiry
            if not is_batch_fresh_for_sale(db.sale_at_min, db.freshness_buffer_min, true_exp):
                service_failures += 1
                failure_events.append(
                    {
                        "trip_id": item["trip_id"],
                        "batch_id": b_id,
                        "destination_id": db.destination_id,
                        "reason": "BATCH_EXPIRED_OR_INSUFFICIENT_TRUE_LIFE_AT_SALE",
                        "true_expiry_min": true_exp,
                        "required_min": db.sale_at_min + db.freshness_buffer_min + 1,
                        "quantity_units": qty,
                    }
                )
                dest_unsold_by_batch[b_id] += qty
                continue

            sold_qty = min(qty, rem_demand)
            unsold_qty = qty - sold_qty
            if sold_qty > 0:
                sold_by_batch[b_id] += sold_qty
                bucket_sold += sold_qty
                rem_demand -= sold_qty
                gross_revenue_paise += sold_qty * item["unit_price_paise"]
            if unsold_qty > 0:
                dest_unsold_by_batch[b_id] += unsold_qty

        reference_fulfilled_total += min(bucket_sold, ref_dem)
        unmet_demand_total += max(0, realized_dem - bucket_sold)

    # Step 9 & 10: Horizon end classification (expired_unsold vs unexpired_carryover)
    batch_ledgers: List[BatchLedgerReconciliation] = []
    spoilage_disposal_cost_paise = 0
    initial_total = 0
    dispatched_total = 0
    sold_total = 0
    expired_total = 0
    carryover_total = 0
    all_reconciled = True

    for b in scenario.batches:
        b_id = b.id
        init_q = b.quantity_units
        disp_q = dispatched_by_batch[b_id]
        sold_q = sold_by_batch[b_id]
        orig_unsold = origin_rem[b_id]
        dest_unsold = dest_unsold_by_batch[b_id]
        true_exp = world.true_expiry_by_batch[b_id]

        if true_exp <= H:
            exp_orig = orig_unsold
            exp_dest = dest_unsold
            carry_orig = 0
            carry_dest = 0
        else:
            exp_orig = 0
            exp_dest = 0
            carry_orig = orig_unsold
            carry_dest = dest_unsold

        exp_tot = exp_orig + exp_dest
        carry_tot = carry_orig + carry_dest
        reconciled = (sold_q + exp_tot + carry_tot == init_q) and (disp_q + orig_unsold == init_q)
        if not reconciled:
            all_reconciled = False

        spoilage_disposal_cost_paise += exp_tot * scenario.cost_model.get_disposal_cost(b.commodity_id)

        initial_total += init_q
        dispatched_total += disp_q
        sold_total += sold_q
        expired_total += exp_tot
        carryover_total += carry_tot

        batch_ledgers.append(
            BatchLedgerReconciliation(
                batch_id=b_id,
                commodity_id=b.commodity_id,
                initial_quantity_units=init_q,
                dispatched_units=disp_q,
                sold_units=sold_q,
                expired_unsold_origin_units=exp_orig,
                expired_unsold_destination_units=exp_dest,
                expired_unsold_total_units=exp_tot,
                unexpired_carryover_origin_units=carry_orig,
                unexpired_carryover_destination_units=carry_dest,
                unexpired_carryover_total_units=carry_tot,
                reconciled=reconciled,
            )
        )

    spoilage_rate = (expired_total / initial_total) if initial_total > 0 else None
    demand_fulfillment_rate = (
        (sold_total / realized_tier_demand_total) if realized_tier_demand_total > 0 else None
    )
    reference_fulfillment_rate = (
        (reference_fulfilled_total / reference_full_price_demand_total)
        if reference_full_price_demand_total > 0
        else None
    )

    realized_econ = compute_realized_economics(
        gross_revenue_paise=gross_revenue_paise,
        fixed_trip_cost_paise=incurred_fixed_trip_cost,
        variable_transport_cost_paise=incurred_var_transport_cost,
        handling_cost_paise=incurred_handling_cost,
        spoilage_disposal_cost_paise=spoilage_disposal_cost_paise,
        inspection_cost_paise=inspection_cost_paise,
        cancellation_cost_paise=incurred_cancellations,
        sunk_committed_cost_paise=max(int(sunk_committed_cost_paise), committed_cost_share),
    )

    return ReplayResult(
        world_id=world.world_id,
        plan_id=plan.plan_id,
        policy=plan.policy,
        initial_total_units=initial_total,
        dispatched_total_units=dispatched_total,
        sold_total_units=sold_total,
        expired_unsold_total_units=expired_total,
        unexpired_carryover_total_units=carryover_total,
        spoilage_rate=spoilage_rate,
        realized_tier_demand_total_units=realized_tier_demand_total,
        reference_full_price_demand_total_units=reference_full_price_demand_total,
        demand_fulfillment_rate=demand_fulfillment_rate,
        reference_fulfillment_rate=reference_fulfillment_rate,
        unmet_demand_units=unmet_demand_total,
        realized_service_failures=service_failures,
        failure_events=failure_events,
        realized_economics=realized_econ,
        batch_ledgers=batch_ledgers,
        ledger_reconciled=all_reconciled,
    )
