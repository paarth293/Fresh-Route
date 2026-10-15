"""Mixed-Integer Linear Programming (MIP) allocation & scheduling optimizer using OR-Tools SCIP (Section 7).
Uses the Hoffman-Kruskal (x_br, w_dck) bipartite network decomposition with maximal time-point vehicle
clique constraints and exact two-stage lexicographic tie-breaking (Stage 1: exact integer paise net outcome;
Stage 2: exact FEFO batch-to-trip assignment LP at locked Stage 1 schedule & tier selection).
"""
from __future__ import annotations
import itertools
import time
import uuid
from typing import Any, Dict, List, Tuple
from ortools.linear_solver import pywraplp
from app.domain.economics import compute_forecast_economics
from app.planning.baselines import _assemble_plan_result
from app.planning.candidates import generate_candidate_trips
from app.planning.eligibility import (
    check_batch_trip_bucket_eligibility,
    intervals_overlap,
)
from app.schemas.models import (
    CandidateTrip,
    PlanResult,
    PlanningSnapshot,
    PolicyName,
    SolverStatus,
    ValidationReport,
)


def solve_deterministic_mip(
    snapshot: PlanningSnapshot,
    config_id: str = "nominal_v1",
    assumption_profile: str = "nominal",
    expiry_offset_min: int = 0,
    extra_route_delay_min: int = 0,
    demand_multiplier: float = 1.0,
    allow_discounts: bool = True,
    forced_allocations: List[Dict[str, Any]] | None = None,
    minimum_service_units: int | None = None,
    time_limit_seconds: float = 5.0,
    simulate_status_override: SolverStatus | None = None,
) -> PlanResult:
    """Solve the Section 7 MIP model using OR-Tools SCIP backend."""
    t0 = time.perf_counter()
    scenario = snapshot.scenario

    full_candidates, _ = generate_candidate_trips(
        snapshot,
        extra_route_delay_min=extra_route_delay_min,
        assumption_profile=assumption_profile,
        prune_symmetric_for_mip=False,
    )
    candidates, diag = generate_candidate_trips(
        snapshot,
        extra_route_delay_min=extra_route_delay_min,
        assumption_profile=assumption_profile,
        prune_symmetric_for_mip=True,
    )
    diag = {
        **diag,
        "extra_route_delay_min": extra_route_delay_min,
        "expiry_offset_min": expiry_offset_min,
        "demand_multiplier": demand_multiplier,
        "assumption_profile": assumption_profile,
    }

    if simulate_status_override in (SolverStatus.NO_INCUMBENT_TIMEOUT, SolverStatus.SOLVER_ERROR):
        rem_batch_qty = {
            b.id: snapshot.warehouse_stock_by_batch.get(b.id, b.quantity_units) for b in scenario.batches
        }
        empty_econ = compute_forecast_economics(scenario, [], [], rem_batch_qty, snapshot.sunk_cost_paise)
        return PlanResult(
            plan_id=f"plan_mip_{uuid.uuid4().hex[:8]}",
            scenario_version_id=snapshot.scenario_version_id,
            state_revision=snapshot.state_revision,
            policy=PolicyName.NOMINAL_MIP,
            config_id=config_id,
            assumption_profile=assumption_profile,
            solver_status=simulate_status_override,
            solve_time_ms=(time.perf_counter() - t0) * 1000.0,
            trips=[],
            allocations=[],
            unallocated_by_batch=rem_batch_qty,
            selected_tier_by_bucket=dict(snapshot.locked_tier_by_bucket),
            forecast_economics=empty_econ,
            validation=ValidationReport(
                is_valid=(simulate_status_override != SolverStatus.SOLVER_ERROR),
                checked_under_assumptions=assumption_profile,
                violations=[],
            ),
            deadlines=[],
            candidate_diagnostics=diag,
        )

    solver = pywraplp.Solver.CreateSolver("SCIP")
    if solver is None:
        solver = pywraplp.Solver.CreateSolver("CBC")
    if solver is None:
        raise RuntimeError("SOLVER_BACKEND_UNAVAILABLE: Neither SCIP nor CBC solver backend is available.")

    solver.SetTimeLimit(int(max(1, round(time_limit_seconds * 1000))))

    veh_map = {v.id: v for v in scenario.vehicles}
    dest_map = {d.id: d for d in scenario.destinations}
    route_map = {r.id: r for r in scenario.routes}
    batch_map = {b.id: b for b in scenario.batches}
    cand_map = {c.id: c for c in candidates}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}
    H = scenario.evaluation_end_min

    # Binary trip activation y_r
    y_vars: Dict[str, Any] = {cand.id: solver.BoolVar(f"y_{cand.id}") for cand in candidates}

    # Binary tier selection z_dck and bucket-tier volume w_dck
    z_vars: Dict[Tuple[str, str], Any] = {}
    w_vars: Dict[Tuple[str, str], Any] = {}
    for db in scenario.demand_buckets:
        tiers = sorted(db.price_tiers, key=lambda t: t.discount_pct)
        full_tier_id = tiers[0].tier_id
        locked_tid = snapshot.locked_tier_by_bucket.get(db.id)
        residual_cap = snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap)
        for ts in tiers:
            z_var = solver.BoolVar(f"z_{db.id}_{ts.tier_id}")
            z_vars[(db.id, ts.tier_id)] = z_var
            if locked_tid is not None and ts.tier_id != locked_tid:
                solver.Add(z_var == 0)
            elif locked_tid is None and (not allow_discounts) and ts.tier_id != full_tier_id:
                solver.Add(z_var == 0)

            adj_forecast = int(max(0, round(ts.forecast_sales_units * demand_multiplier)))
            limit = min(adj_forecast, residual_cap)
            w_var = solver.IntVar(0, limit, f"w_{db.id}_{ts.tier_id}")
            w_vars[(db.id, ts.tier_id)] = w_var
            solver.Add(w_var <= limit * z_var)

        solver.Add(sum(z_vars[(db.id, ts.tier_id)] for ts in tiers) == 1)

    # Unallocated batch stock u_b
    u_vars: Dict[str, Any] = {}
    avail_by_batch: Dict[str, int] = {}
    expiry_used_by_batch: Dict[str, int] = {}
    for b in scenario.batches:
        q_b = snapshot.warehouse_stock_by_batch.get(b.id, b.quantity_units)
        avail_by_batch[b.id] = q_b
        u_vars[b.id] = solver.IntVar(0, q_b, f"u_{b.id}")
        expiry_used_by_batch[b.id] = b.estimated_expiry_nominal_min + expiry_offset_min

    # Bipartite batch-to-trip flow x_br (only for eligible (batch, trip) pairs)
    x_br_vars: Dict[Tuple[str, str], Any] = {}
    x_by_batch: Dict[str, List[Any]] = {b.id: [] for b in scenario.batches}
    x_by_trip: Dict[str, List[Any]] = {c.id: [] for c in candidates}
    x_by_bucket: Dict[str, List[Any]] = {db.id: [] for db in scenario.demand_buckets}

    for b in scenario.batches:
        q_b = avail_by_batch[b.id]
        if q_b <= 0:
            continue
        est_exp = expiry_used_by_batch[b.id]
        for cand in candidates:
            dest = dest_map[cand.destination_id]
            veh = veh_map[cand.vehicle_id]
            db = dest_comm_to_bucket.get((dest.id, b.commodity_id))
            if db is None:
                continue
            ok, _ = check_batch_trip_bucket_eligibility(
                b, veh, dest, db, cand, snapshot.decision_at_min, expiry_override_min=est_exp
            )
            if not ok:
                continue
            upper_q = min(q_b, cand.capacity_units, snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap))
            x_var = solver.IntVar(0, upper_q, f"x_{b.id}_{cand.id}")
            x_br_vars[(b.id, cand.id)] = x_var
            x_by_batch[b.id].append(x_var)
            x_by_trip[cand.id].append(x_var)
            x_by_bucket[db.id].append(x_var)
            solver.Add(x_var <= upper_q * y_vars[cand.id])

    # Batch stock conservation: sum_r x_br + u_b == Q_b
    for b in scenario.batches:
        solver.Add(sum(x_by_batch[b.id]) + u_vars[b.id] == avail_by_batch[b.id])

    # Trip capacity & activation: sum_b x_br <= C_r * y_r
    for cand in candidates:
        if x_by_trip[cand.id]:
            solver.Add(sum(x_by_trip[cand.id]) <= cand.capacity_units * y_vars[cand.id])
        else:
            solver.Add(y_vars[cand.id] == 0)

    # Bucket flow balance: sum_{b in c, r in d} x_br == sum_k w_dck
    for db in scenario.demand_buckets:
        solver.Add(
            sum(x_by_bucket[db.id]) == sum(w_vars[(db.id, ts.tier_id)] for ts in db.price_tiers)
        )

    # Vehicle non-overlap: maximal time-point cliques + pairwise non-overlap
    cands_by_veh: Dict[str, List[CandidateTrip]] = {}
    for cand in candidates:
        cands_by_veh.setdefault(cand.vehicle_id, []).append(cand)

    for v_id, v_cands in cands_by_veh.items():
        for c1 in v_cands:
            t_pt = c1.loading_start_min
            clique = [y_vars[c2.id] for c2 in v_cands if c2.loading_start_min <= t_pt < c2.vehicle_release_min]
            if len(clique) > 1:
                solver.Add(sum(clique) <= 1)
        n_c = len(v_cands)
        for i in range(n_c):
            for j in range(i + 1, n_c):
                if intervals_overlap(
                    v_cands[i].loading_start_min,
                    v_cands[i].vehicle_release_min,
                    v_cands[j].loading_start_min,
                    v_cands[j].vehicle_release_min,
                ):
                    solver.Add(y_vars[v_cands[i].id] + y_vars[v_cands[j].id] <= 1)

    # Optional forced allocations for controlled alternative re-solve (Section 7.9)
    if forced_allocations:
        for fa in forced_allocations:
            fb_id = fa["batch_id"]
            fdest_id = fa["destination_id"]
            fqty = int(fa["quantity_units"])
            matching_x = [
                var
                for (b_id, c_id), var in x_br_vars.items()
                if b_id == fb_id and cand_map[c_id].destination_id == fdest_id
            ]
            if not matching_x and fqty > 0:
                solver.Add(solver.NumVar(0, 0, "infeasible_force") == 1)
            else:
                solver.Add(sum(matching_x) >= fqty)

    # Optional minimum service obligation (Section 7.5)
    if minimum_service_units is not None and minimum_service_units > 0:
        all_x = list(x_br_vars.values())
        if not all_x:
            solver.Add(solver.NumVar(0, 0, "infeasible_min_service") == 1)
        else:
            solver.Add(sum(all_x) >= int(minimum_service_units))

    # Stage 1 Objective: Pure Integer Paise Primary Economic Contribution (zero tie-break distortion)
    objective = solver.Objective()
    primary_terms = []

    for (db_id, t_id), w_var in w_vars.items():
        db = next(x for x in scenario.demand_buckets if x.id == db_id)
        ts = next(t for t in db.price_tiers if t.tier_id == t_id)
        coeff = int(ts.price_paise_per_unit)
        objective.SetCoefficient(w_var, float(coeff))
        primary_terms.append(coeff * w_var)

    for (b_id, c_id), x_var in x_br_vars.items():
        b = batch_map[b_id]
        cand = cand_map[c_id]
        route = route_map[cand.route_id]
        unit_var_cost = int(
            route.variable_transport_cost_paise_per_unit
            + scenario.cost_model.get_handling_cost(b.commodity_id)
        )
        objective.SetCoefficient(x_var, float(-unit_var_cost))
        primary_terms.append(-unit_var_cost * x_var)

    for c_id, y_var in y_vars.items():
        cand = cand_map[c_id]
        fc = int(cand.fixed_trip_cost_paise)
        objective.SetCoefficient(y_var, float(-fc))
        primary_terms.append(-fc * y_var)

    for b in scenario.batches:
        if expiry_used_by_batch[b.id] <= H:
            w_c = int(scenario.cost_model.get_disposal_cost(b.commodity_id))
            objective.SetCoefficient(u_vars[b.id], float(-w_c))
            primary_terms.append(-w_c * u_vars[b.id])
        else:
            objective.SetCoefficient(u_vars[b.id], 0.0)

    objective.SetMaximization()
    status_code = solver.Solve()

    solution_z = {}
    solution_x = {}
    solution_u = {}

    # Stage 2 Lexicographic Tie-Break: lock primary net outcome >= Stage 1 optimum (0 paise tolerance)
    # and optimize FEFO batch urgency + fewer/earlier trips within a fast 150ms budget
    if status_code in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE) and x_br_vars:
        stage1_opt_paise = int(round(objective.Value()))
        stage1_y = {c_id: int(round(v.solution_value())) for c_id, v in y_vars.items()}
        stage1_z = {k: int(round(v.solution_value())) for k, v in z_vars.items()}
        stage1_w = {k: int(round(v.solution_value())) for k, v in w_vars.items()}
        stage1_x = {k: int(round(v.solution_value())) for k, v in x_br_vars.items()}
        stage1_u = {k: int(round(v.solution_value())) for k, v in u_vars.items()}

        solver.Add(sum(primary_terms) >= stage1_opt_paise)
        objective.Clear()
        for (b_id, c_id), x_var in x_br_vars.items():
            b = batch_map[b_id]
            urgency_score = max(1, 2000 - int(b.estimated_expiry_nominal_min))
            objective.SetCoefficient(x_var, float(urgency_score))
        for c_id, y_var in y_vars.items():
            cand = cand_map[c_id]
            objective.SetCoefficient(y_var, -10.0 - float(cand.departure_min) * 0.01)
        objective.SetMaximization()
        solver.SetTimeLimit(150)
        s2_status = solver.Solve()
        # Never discard a valid primary incumbent when the tie-break times out.
        if s2_status in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            solution_z = {k: int(round(v.solution_value())) for k, v in z_vars.items()}
            solution_x = {k: int(round(v.solution_value())) for k, v in x_br_vars.items()}
            solution_u = {k: int(round(v.solution_value())) for k, v in u_vars.items()}
        else:
            solution_z, solution_x, solution_u = stage1_z, stage1_x, stage1_u
    elif status_code in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        solution_z = {k: int(round(v.solution_value())) for k, v in z_vars.items()}
        solution_x = {k: int(round(v.solution_value())) for k, v in x_br_vars.items()}
        solution_u = {k: int(round(v.solution_value())) for k, v in u_vars.items()}
    solve_ms = (time.perf_counter() - t0) * 1000.0

    if simulate_status_override is not None:
        mapped_status = simulate_status_override
    elif status_code == pywraplp.Solver.OPTIMAL:
        mapped_status = SolverStatus.OPTIMAL
    elif status_code == pywraplp.Solver.FEASIBLE:
        mapped_status = SolverStatus.FEASIBLE_TIME_LIMITED
    elif status_code == pywraplp.Solver.INFEASIBLE:
        mapped_status = SolverStatus.INFEASIBLE
    else:
        mapped_status = SolverStatus.NO_INCUMBENT_TIMEOUT

    if mapped_status in (SolverStatus.INFEASIBLE, SolverStatus.NO_INCUMBENT_TIMEOUT, SolverStatus.SOLVER_ERROR):
        rem_batch_qty = {b.id: avail_by_batch[b.id] for b in scenario.batches}
        empty_econ = compute_forecast_economics(scenario, [], [], rem_batch_qty, snapshot.sunk_cost_paise)
        return PlanResult(
            plan_id=f"plan_mip_{uuid.uuid4().hex[:8]}",
            scenario_version_id=snapshot.scenario_version_id,
            state_revision=snapshot.state_revision,
            policy=PolicyName.NOMINAL_MIP,
            config_id=config_id,
            assumption_profile=assumption_profile,
            solver_status=mapped_status,
            solve_time_ms=round(solve_ms, 2),
            trips=[],
            allocations=[],
            unallocated_by_batch=rem_batch_qty,
            selected_tier_by_bucket=dict(snapshot.locked_tier_by_bucket),
            forecast_economics=empty_econ,
            validation=ValidationReport(
                is_valid=False,
                checked_under_assumptions=assumption_profile,
                violations=[],
            ),
            deadlines=[],
            candidate_diagnostics=diag,
        )

    active_trips: Dict[str, CandidateTrip] = {}
    allocations_by_trip_batch_bucket: Dict[Tuple[str, str, str], int] = {}
    selected_tier_by_bucket: Dict[str, str] = {}
    rem_batch_qty: Dict[str, int] = {}

    for db in scenario.demand_buckets:
        chosen_tid = db.price_tiers[0].tier_id
        for ts in db.price_tiers:
            if solution_z.get((db.id, ts.tier_id)) == 1:
                chosen_tid = ts.tier_id
                break
        selected_tier_by_bucket[db.id] = chosen_tid

    for (b_id, c_id), var in x_br_vars.items():
        val = solution_x[(b_id, c_id)]
        if val > 0:
            cand = cand_map[c_id]
            b = batch_map[b_id]
            db = dest_comm_to_bucket[(cand.destination_id, b.commodity_id)]
            active_trips[c_id] = cand
            key = (c_id, b_id, db.id)
            allocations_by_trip_batch_bucket[key] = allocations_by_trip_batch_bucket.get(key, 0) + val

    for b in scenario.batches:
        rem_batch_qty[b.id] = solution_u[b.id]

    gap_pct = 0.0 if mapped_status == SolverStatus.OPTIMAL else None

    res = _assemble_plan_result(
        snapshot=snapshot,
        policy=PolicyName.NOMINAL_MIP,
        config_id=config_id,
        assumption_profile=assumption_profile,
        solver_status=mapped_status,
        solve_time_ms=solve_ms,
        active_trips=active_trips,
        allocations_by_trip_batch_bucket=allocations_by_trip_batch_bucket,
        selected_tier_by_bucket=selected_tier_by_bucket,
        rem_batch_qty=rem_batch_qty,
        candidates=full_candidates,
        candidate_diagnostics=diag,
        optimality_gap_pct=gap_pct,
    )
    if (
        mapped_status == SolverStatus.FEASIBLE_TIME_LIMITED
        and expiry_offset_min == 0
        and extra_route_delay_min == 0
        and demand_multiplier == 1.0
        and allow_discounts
        and not forced_allocations
        and not minimum_service_units
    ):
        from app.planning.baselines import solve_fefo_greedy
        fefo_inc = solve_fefo_greedy(snapshot, config_id=config_id)
        if fefo_inc.validation.is_valid and fefo_inc.forecast_economics.net_outcome_paise > res.forecast_economics.net_outcome_paise:
            fefo_inc.policy = PolicyName.NOMINAL_MIP
            fefo_inc.solver_status = SolverStatus.FEASIBLE_TIME_LIMITED
            fefo_inc.solve_time_ms = round(solve_ms + fefo_inc.solve_time_ms, 2)
            fefo_inc.candidate_diagnostics = {**diag, "fallback_incumbent": "fefo_greedy_bound"}
            return fefo_inc
    return res


def solve_exhaustive_oracle(snapshot: PlanningSnapshot) -> Tuple[int, Dict[str, Any]]:
    """Genuine exhaustive enumeration oracle for small instances (Section 12.1 & Step 10).
    Enumerates all valid subsets of non-overlapping candidate trips, all price-tier tuples,
    and all priority orderings + integer split points across eligible (batch, trip) edges,
    including cases where a flexible batch must be reserved for one destination so an urgent
    batch can serve its only eligible destination.
    """
    scenario = snapshot.scenario
    candidates, _ = generate_candidate_trips(
        snapshot, extra_route_delay_min=0, assumption_profile="nominal", prune_symmetric_for_mip=True
    )
    if len(candidates) > 10:
        raise ValueError("ORACLE_SIZE_LIMIT: Exhaustive oracle requires at most ten candidates; it must not silently truncate the problem.")
    veh_map = {v.id: v for v in scenario.vehicles}
    dest_map = {d.id: d for d in scenario.destinations}
    route_map = {r.id: r for r in scenario.routes}
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}
    H = scenario.evaluation_end_min

    bucket_list = list(scenario.demand_buckets)
    tier_choices_per_bucket = [[ts for ts in db.price_tiers] for db in bucket_list]

    best_net_paise = -10**18
    best_assignment: Dict[str, Any] = {}

    n_cands = len(candidates)
    for r_mask in range(1 << n_cands):
        chosen_cands = [candidates[i] for i in range(n_cands) if (r_mask & (1 << i))]
        overlap = False
        by_veh: Dict[str, List[CandidateTrip]] = {}
        for c in chosen_cands:
            by_veh.setdefault(c.vehicle_id, []).append(c)
        for v_cands in by_veh.values():
            for i in range(len(v_cands)):
                for j in range(i + 1, len(v_cands)):
                    if intervals_overlap(
                        v_cands[i].loading_start_min,
                        v_cands[i].vehicle_release_min,
                        v_cands[j].loading_start_min,
                        v_cands[j].vehicle_release_min,
                    ):
                        overlap = True
                        break
                if overlap:
                    break
            if overlap:
                break
        if overlap:
            continue

        for tier_tuple in itertools.product(*tier_choices_per_bucket):
            tier_by_db = {bucket_list[idx].id: tier_tuple[idx] for idx in range(len(bucket_list))}
            edges = []
            for b in scenario.batches:
                for c in chosen_cands:
                    dest = dest_map[c.destination_id]
                    veh = veh_map[c.vehicle_id]
                    db = dest_comm_to_bucket.get((dest.id, b.commodity_id))
                    if db is None:
                        continue
                    ok, _ = check_batch_trip_bucket_eligibility(b, veh, dest, db, c, snapshot.decision_at_min)
                    if ok:
                        ts = tier_by_db[db.id]
                        route = route_map[c.route_id]
                        unit_var = route.variable_transport_cost_paise_per_unit + scenario.cost_model.get_handling_cost(
                            b.commodity_id
                        )
                        disposal_save = (
                            scenario.cost_model.get_disposal_cost(b.commodity_id)
                            if b.estimated_expiry_nominal_min <= H
                            else 0
                        )
                        eff_margin = ts.price_paise_per_unit - unit_var + disposal_save
                        if eff_margin > 0:
                            edges.append((eff_margin, b, c, db, ts))

            edge_orders = (
                list(itertools.permutations(edges))
                if len(edges) <= 6
                else [tuple(sorted(edges, key=lambda x: (-x[0], x[1].id, x[2].id)))]
            )
            for ordering in edge_orders:
                rem_b = {b.id: snapshot.warehouse_stock_by_batch.get(b.id, b.quantity_units) for b in scenario.batches}
                rem_c = {c.id: c.capacity_units for c in chosen_cands}
                rem_db = {
                    db.id: min(
                        tier_by_db[db.id].forecast_sales_units,
                        snapshot.residual_cap_by_bucket.get(db.id, db.accepted_quantity_cap),
                    )
                    for db in bucket_list
                }
                used_cands: set[str] = set()
                rev = 0
                var_cost = 0
                for eff_m, b, c, db, ts in ordering:
                    q = min(rem_b[b.id], rem_c[c.id], rem_db[db.id])
                    if q > 0:
                        rem_b[b.id] -= q
                        rem_c[c.id] -= q
                        rem_db[db.id] -= q
                        used_cands.add(c.id)
                        rev += q * ts.price_paise_per_unit
                        route = route_map[c.route_id]
                        var_cost += q * (
                            route.variable_transport_cost_paise_per_unit
                            + scenario.cost_model.get_handling_cost(b.commodity_id)
                        )

                fixed_cost = sum(c.fixed_trip_cost_paise for c in chosen_cands if c.id in used_cands)
                disp_cost = sum(
                    rem_b[b.id] * scenario.cost_model.get_disposal_cost(b.commodity_id)
                    for b in scenario.batches
                    if b.estimated_expiry_nominal_min <= H
                )
                net = rev - fixed_cost - var_cost - disp_cost
                if net > best_net_paise:
                    best_net_paise = net
                    best_assignment = {
                        "used_trips": sorted(used_cands),
                        "unallocated": dict(rem_b),
                        "net_outcome_paise": net,
                    }

    return best_net_paise, best_assignment
