"""Baseline planning strategies: organizer_baseline and fefo_greedy (Section 9.3 & Step 9)."""
from __future__ import annotations
import time
import uuid
from typing import Dict, List, Tuple
from app.domain.economics import compute_forecast_economics
from app.planning.candidates import generate_candidate_trips
from app.planning.deadlines import compute_batch_deadlines
from app.planning.eligibility import (
    check_batch_trip_bucket_eligibility,
    intervals_overlap,
)
from app.schemas.models import (
    AllocationItem,
    PlanResult,
    PlanTrip,
    PlanningSnapshot,
    PolicyName,
    SolverStatus,
)
from app.validation.plan import validate_plan_independently


def solve_organizer_baseline(
    snapshot: PlanningSnapshot,
    config_id: str = "organizer_baseline_v1",
) -> PlanResult:
    """Organizer status-quo baseline:
    - Uses batch ID order (FIFO/arrival order rather than FEFO urgency optimization)
    - Uses full-price (0% discount) tier only
    - Dispatches on earliest available vehicle trip when gross revenue > trip & handling costs.
    """
    if not snapshot.scenario.cost_model.organizer_baseline_enabled:
        raise ValueError("ORGANIZER_BASELINE_UNAVAILABLE: Organizer baseline is not enabled for this scenario.")

    return _run_greedy_allocation(
        snapshot=snapshot,
        policy=PolicyName.ORGANIZER_BASELINE,
        config_id=config_id,
        sort_by_expiry=False,
        allow_discounts=False,
        include_avoided_disposal_in_margin=False,
    )


def solve_fefo_greedy(
    snapshot: PlanningSnapshot,
    config_id: str = "fefo_greedy_v1",
    allow_discounts: bool = True,
) -> PlanResult:
    """Competent FEFO greedy baseline (Step 9):
    - Sorts available batches by (estimated_expiry_nominal_min, batch.id)
    - Evaluates allowed price tiers using local net contribution (including fixed trip cost once and avoided disposal)
    - Respects vehicle non-overlap, capacity, single tier per bucket, and residual demand/acceptance caps.
    """
    return _run_greedy_allocation(
        snapshot=snapshot,
        policy=PolicyName.FEFO_GREEDY,
        config_id=config_id,
        sort_by_expiry=True,
        allow_discounts=allow_discounts,
        include_avoided_disposal_in_margin=True,
    )


def _run_greedy_allocation(
    snapshot: PlanningSnapshot,
    policy: PolicyName,
    config_id: str,
    sort_by_expiry: bool,
    allow_discounts: bool,
    include_avoided_disposal_in_margin: bool,
) -> PlanResult:
    t0 = time.perf_counter()
    scenario = snapshot.scenario
    candidates, diag = generate_candidate_trips(snapshot, extra_route_delay_min=0, assumption_profile="nominal")

    veh_map = {v.id: v for v in scenario.vehicles}
    dest_map = {d.id: d for d in scenario.destinations}
    route_map = {r.id: r for r in scenario.routes}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}

    rem_batch_qty: Dict[str, int] = {
        b.id: snapshot.warehouse_stock_by_batch.get(b.id, b.quantity_units) for b in scenario.batches
    }
    selected_tier_by_bucket: Dict[str, str] = dict(snapshot.locked_tier_by_bucket)
    rem_bucket_demand: Dict[str, int] = {}

    # Pre-Select best tier per bucket for greedy or lock to full-price tier
    for db in scenario.demand_buckets:
        residual_cap = snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap)
        if db.id in selected_tier_by_bucket:
            locked_tid = selected_tier_by_bucket[db.id]
            ts = next((t for t in db.price_tiers if t.tier_id == locked_tid), db.price_tiers[0])
            rem_bucket_demand[db.id] = min(ts.forecast_sales_units, residual_cap)
        else:
            tiers = sorted(db.price_tiers, key=lambda t: t.discount_pct)
            if not allow_discounts:
                chosen = tiers[0]
            else:
                # Choose tier that maximizes potential bucket gross contribution given total available commodity stock
                avail_comm_stock = sum(
                    rem_batch_qty[b.id] for b in scenario.batches if b.commodity_id == db.commodity_id
                )
                best_tier = tiers[0]
                best_val = -10**15
                var_cost_approx = scenario.cost_model.get_handling_cost(db.commodity_id)
                for ts in tiers:
                    sellable = min(avail_comm_stock, ts.forecast_sales_units, residual_cap)
                    val = sellable * (ts.price_paise_per_unit - var_cost_approx)
                    if val > best_val:
                        best_val = val
                        best_tier = ts
                chosen = best_tier
            selected_tier_by_bucket[db.id] = chosen.tier_id
            rem_bucket_demand[db.id] = min(chosen.forecast_sales_units, residual_cap)

    # Track activated trips and their remaining capacities
    active_trips: Dict[str, CandidateTrip] = {}
    rem_trip_cap: Dict[str, int] = {}
    active_intervals_by_veh: Dict[str, List[Tuple[int, int]]] = {}
    allocations_by_trip_batch_bucket: Dict[Tuple[str, str, str], int] = {}

    if sort_by_expiry:
        ordered_batches = sorted(scenario.batches, key=lambda b: (b.estimated_expiry_nominal_min, b.id))
    else:
        ordered_batches = sorted(scenario.batches, key=lambda b: b.id)

    H = scenario.evaluation_end_min

    for b in ordered_batches:
        if rem_batch_qty.get(b.id, 0) <= 0:
            continue
        disposal_save = (
            scenario.cost_model.get_disposal_cost(b.commodity_id)
            if (include_avoided_disposal_in_margin and b.estimated_expiry_nominal_min <= H)
            else 0
        )

        # Evaluate candidate trips sorted by earliest departure, then highest net local contribution
        while rem_batch_qty[b.id] > 0:
            best_option: Tuple[int, int, CandidateTrip, str, int, int] | None = None
            for cand in sorted(candidates, key=lambda c: (c.departure_min, c.id)):
                dest = dest_map[cand.destination_id]
                veh = veh_map[cand.vehicle_id]
                db = dest_comm_to_bucket.get((dest.id, b.commodity_id))
                if db is None:
                    continue
                rem_dem = rem_bucket_demand.get(db.id, 0)
                if rem_dem <= 0:
                    continue

                ok, _ = check_batch_trip_bucket_eligibility(b, veh, dest, db, cand, snapshot.decision_at_min)
                if not ok:
                    continue

                # Check if trip is already active or can be activated without overlapping active trips
                already_active = cand.id in active_trips
                if already_active:
                    cap_avail = rem_trip_cap[cand.id]
                else:
                    conflict = False
                    for s_i, e_i in active_intervals_by_veh.get(veh.id, []):
                        if intervals_overlap(cand.loading_start_min, cand.vehicle_release_min, s_i, e_i):
                            conflict = True
                            break
                    if conflict:
                        continue
                    cap_avail = cand.capacity_units

                qty_possible = min(rem_batch_qty[b.id], rem_dem, cap_avail)
                if qty_possible <= 0:
                    continue

                tier_id = selected_tier_by_bucket[db.id]
                tier_spec = next(ts for ts in db.price_tiers if ts.tier_id == tier_id)
                route = route_map[cand.route_id]
                unit_var = route.variable_transport_cost_paise_per_unit + scenario.cost_model.get_handling_cost(
                    b.commodity_id
                )
                fixed_cost_to_charge = 0 if already_active else cand.fixed_trip_cost_paise
                local_gain = qty_possible * (tier_spec.price_paise_per_unit - unit_var + disposal_save) - fixed_cost_to_charge

                if local_gain <= 0:
                    continue

                # Score: for FEFO greedy, maximize local_gain then earliest departure;
                # for organizer_baseline, take earliest departure that has positive gain
                score_tuple = (
                    (local_gain, -cand.departure_min)
                    if sort_by_expiry
                    else (-cand.departure_min, local_gain)
                )
                if best_option is None or score_tuple > (best_option[0], best_option[1]):
                    best_option = (score_tuple[0], score_tuple[1], cand, db.id, qty_possible, int(already_active))

            if best_option is None:
                break

            _, _, chosen_cand, chosen_db_id, alloc_qty, was_active = best_option
            if not was_active:
                active_trips[chosen_cand.id] = chosen_cand
                rem_trip_cap[chosen_cand.id] = chosen_cand.capacity_units
                active_intervals_by_veh.setdefault(chosen_cand.vehicle_id, []).append(
                    (chosen_cand.loading_start_min, chosen_cand.vehicle_release_min)
                )

            rem_batch_qty[b.id] -= alloc_qty
            rem_bucket_demand[chosen_db_id] -= alloc_qty
            rem_trip_cap[chosen_cand.id] -= alloc_qty
            key = (chosen_cand.id, b.id, chosen_db_id)
            allocations_by_trip_batch_bucket[key] = allocations_by_trip_batch_bucket.get(key, 0) + alloc_qty

    return _assemble_plan_result(
        snapshot=snapshot,
        policy=policy,
        config_id=config_id,
        assumption_profile="nominal",
        solver_status=SolverStatus.HEURISTIC_SELECTED,
        solve_time_ms=(time.perf_counter() - t0) * 1000.0,
        active_trips=active_trips,
        allocations_by_trip_batch_bucket=allocations_by_trip_batch_bucket,
        selected_tier_by_bucket=selected_tier_by_bucket,
        rem_batch_qty=rem_batch_qty,
        candidates=candidates,
        candidate_diagnostics=diag,
    )


def _assemble_plan_result(
    snapshot: PlanningSnapshot,
    policy: PolicyName,
    config_id: str,
    assumption_profile: str,
    solver_status: SolverStatus,
    solve_time_ms: float,
    active_trips: Dict[str, CandidateTrip],
    allocations_by_trip_batch_bucket: Dict[Tuple[str, str, str], int],
    selected_tier_by_bucket: Dict[str, str],
    rem_batch_qty: Dict[str, int],
    candidates: List[CandidateTrip],
    candidate_diagnostics: Dict[str, object],
    optimality_gap_pct: float | None = None,
) -> PlanResult:
    scenario = snapshot.scenario
    batch_map = {b.id: b for b in scenario.batches}
    bucket_map = {db.id: db for db in scenario.demand_buckets}

    all_allocs: List[AllocationItem] = []
    allocs_by_trip: Dict[str, List[AllocationItem]] = {t_id: [] for t_id in active_trips}

    for (t_id, b_id, db_id), qty in sorted(allocations_by_trip_batch_bucket.items()):
        if qty <= 0:
            continue
        cand = active_trips[t_id]
        b = batch_map[b_id]
        db = bucket_map[db_id]
        tier_id = selected_tier_by_bucket[db_id]
        tier_spec = next(ts for ts in db.price_tiers if ts.tier_id == tier_id)

        item = AllocationItem(
            batch_id=b_id,
            commodity_id=b.commodity_id,
            trip_id=t_id,
            vehicle_id=cand.vehicle_id,
            route_id=cand.route_id,
            destination_id=cand.destination_id,
            bucket_id=db_id,
            tier_id=tier_id,
            discount_pct=tier_spec.discount_pct,
            unit_price_paise=tier_spec.price_paise_per_unit,
            quantity_units=qty,
            departure_min=cand.departure_min,
            ready_for_sale_min=cand.ready_for_sale_min,
        )
        all_allocs.append(item)
        allocs_by_trip.setdefault(t_id, []).append(item)

    deadlines = compute_batch_deadlines(snapshot, candidates, allocations=all_allocs)
    deadline_lookup = {
        (dl.batch_id, dl.destination_id): dl for dl in deadlines if dl.is_eligible
    }

    for item in all_allocs:
        dl = deadline_lookup.get((item.batch_id, item.destination_id))
        if dl and dl.scheduled_latest_departure_min is not None:
            item.latest_feasible_departure_min = dl.scheduled_latest_departure_min
            item.dispatch_slack_min = dl.scheduled_latest_departure_min - item.departure_min

    plan_trips: List[PlanTrip] = []
    for t_id, cand in sorted(active_trips.items(), key=lambda kv: (kv[1].departure_min, kv[0])):
        t_allocs = allocs_by_trip.get(t_id, [])
        loaded = sum(a.quantity_units for a in t_allocs)
        if loaded <= 0:
            continue
        plan_trips.append(
            PlanTrip(
                trip_id=t_id,
                vehicle_id=cand.vehicle_id,
                route_id=cand.route_id,
                destination_id=cand.destination_id,
                departure_min=cand.departure_min,
                loading_start_min=cand.loading_start_min,
                arrival_min=cand.arrival_min,
                unloading_start_min=cand.unloading_start_min,
                unloading_end_min=cand.unloading_end_min,
                ready_for_sale_min=cand.ready_for_sale_min,
                vehicle_release_min=cand.vehicle_release_min,
                total_loaded_units=loaded,
                capacity_units=cand.capacity_units,
                fixed_trip_cost_paise=cand.fixed_trip_cost_paise,
                allocations=t_allocs,
            )
        )

    forecast_econ = compute_forecast_economics(
        scenario=scenario,
        trips=plan_trips,
        allocations=all_allocs,
        unallocated_by_batch=rem_batch_qty,
        sunk_committed_cost_paise=snapshot.sunk_cost_paise,
        inspection_cost_paise=sum(o.cost_paise for o in snapshot.inspection_history),
    )

    plan_id = f"plan_{policy.value}_{uuid.uuid4().hex[:8]}"
    provisional_plan = PlanResult(
        plan_id=plan_id,
        scenario_version_id=snapshot.scenario_version_id,
        state_revision=snapshot.state_revision,
        policy=policy,
        config_id=config_id,
        assumption_profile=assumption_profile,
        solver_status=solver_status,
        solve_time_ms=round(solve_time_ms, 2),
        optimality_gap_pct=optimality_gap_pct,
        trips=plan_trips,
        allocations=all_allocs,
        unallocated_by_batch=rem_batch_qty,
        selected_tier_by_bucket=selected_tier_by_bucket,
        forecast_economics=forecast_econ,
        validation=validate_plan_independently(
            snapshot,
            PlanResult(
                plan_id=plan_id,
                scenario_version_id=snapshot.scenario_version_id,
                state_revision=snapshot.state_revision,
                policy=policy,
                config_id=config_id,
                assumption_profile=assumption_profile,
                solver_status=solver_status,
                solve_time_ms=0.0,
                trips=plan_trips,
                allocations=all_allocs,
                unallocated_by_batch=rem_batch_qty,
                selected_tier_by_bucket=selected_tier_by_bucket,
                forecast_economics=forecast_econ,
                validation={"is_valid": True, "violations": []},
                candidate_diagnostics=candidate_diagnostics,
            ),
            checked_under_assumptions=(
                "nominal_planning_snapshot"
                if assumption_profile == "nominal"
                else f"assumption_profile:{assumption_profile}"
            ),
        ),
        deadlines=deadlines,
        candidate_diagnostics=candidate_diagnostics,
    )
    return provisional_plan
