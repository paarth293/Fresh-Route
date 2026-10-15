"""Candidate trip and departure grid generator (Section 7.3 & Step 8)."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
from app.planning.eligibility import (
    check_trip_feasibility,
    compute_trip_timing,
    intervals_overlap,
)
from app.schemas.models import (
    CandidateTrip,
    CommitmentState,
    PlanningSnapshot,
)


def generate_candidate_trips(
    snapshot: PlanningSnapshot,
    extra_route_delay_min: int = 0,
    assumption_profile: str = "nominal",
    prune_symmetric_for_mip: bool = False,
) -> Tuple[List[CandidateTrip], Dict[str, Any]]:
    """Generate feasible candidate trips across vehicles, routes, and departure grid + boundary instants."""
    scenario = snapshot.scenario
    grid = max(1, scenario.uncertainty_model.departure_grid_min)
    dest_map = {d.id: d for d in scenario.destinations}
    buckets_by_dest: Dict[str, List[int]] = {}
    for db in scenario.demand_buckets:
        buckets_by_dest.setdefault(db.destination_id, []).append(db.sale_at_min)

    # Locked vehicle intervals from commitments
    locked_intervals_by_vehicle: Dict[str, List[Tuple[int, int]]] = {}
    for comm in scenario.commitments:
        if comm.state in (
            CommitmentState.RESERVED,
            CommitmentState.LOADING,
            CommitmentState.DISPATCHED,
            CommitmentState.DELIVERED,
        ):
            locked_intervals_by_vehicle.setdefault(comm.vehicle_id, []).append(
                (comm.loading_start_min, comm.vehicle_release_min)
            )

    candidates: List[CandidateTrip] = []
    exclusion_counts: Dict[str, int] = {}

    for v in sorted(scenario.vehicles, key=lambda x: x.id):
        for r in sorted(scenario.routes, key=lambda x: x.id):
            dest = dest_map.get(r.destination_id)
            if dest is None:
                continue

            outbound = r.outbound_travel_min + extra_route_delay_min
            # Collect grid departures plus supplied fixed departures plus critical boundary departures
            dep_set: set[int] = set(scenario.uncertainty_model.supplied_fixed_departures_min)
            min_dep = max(snapshot.decision_at_min + r.loading_duration_min, v.available_from_min + r.loading_duration_min)
            max_dep = min(v.available_to_min, scenario.evaluation_end_min)

            # Grid points starting from 0 aligned to grid
            g_dep = 0
            while g_dep <= max_dep:
                if g_dep >= min_dep:
                    dep_set.add(g_dep)
                g_dep += grid
            dep_set.add(min_dep)

            # Add exact boundary departures for receiving_close and bucket sale_at_min
            latest_unload_for_close = dest.receiving_close_min - r.unloading_duration_min - outbound
            if min_dep <= latest_unload_for_close <= max_dep:
                dep_set.add(latest_unload_for_close)

            for sale_min in buckets_by_dest.get(dest.id, []):
                latest_dep_for_sale = (
                    sale_min
                    - r.downstream_handling_duration_min
                    - r.unloading_duration_min
                    - outbound
                )
                if min_dep <= latest_dep_for_sale <= max_dep:
                    dep_set.add(latest_dep_for_sale)

            for dep in sorted(dep_set):
                timing = compute_trip_timing(r, dest, dep, extra_outbound_delay_min=extra_route_delay_min)
                ok, reason = check_trip_feasibility(
                    v, r, dest, timing, snapshot.decision_at_min, scenario.evaluation_end_min
                )
                if not ok:
                    exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1
                    continue

                # Check conflict with locked commitments on this vehicle
                conflict = False
                for l_start, l_end in locked_intervals_by_vehicle.get(v.id, []):
                    if intervals_overlap(timing["loading_start_min"], timing["vehicle_release_min"], l_start, l_end):
                        conflict = True
                        break
                if conflict:
                    exclusion_counts["LOCKED_COMMITMENT_OVERLAP"] = (
                        exclusion_counts.get("LOCKED_COMMITMENT_OVERLAP", 0) + 1
                    )
                    continue

                # Check that at least one bucket at this destination has sale_at_min >= ready_for_sale_min
                dest_sale_times = buckets_by_dest.get(dest.id, [])
                if dest_sale_times and all(timing["ready_for_sale_min"] > st for st in dest_sale_times):
                    exclusion_counts["READY_AFTER_ALL_SALES"] = exclusion_counts.get("READY_AFTER_ALL_SALES", 0) + 1
                    continue

                cand_id = f"cand_{v.id}_{r.id}_t{dep}"
                candidates.append(
                    CandidateTrip(
                        id=cand_id,
                        vehicle_id=v.id,
                        route_id=r.id,
                        destination_id=dest.id,
                        departure_min=timing["departure_min"],
                        loading_start_min=timing["loading_start_min"],
                        arrival_min=timing["arrival_min"],
                        unloading_start_min=timing["unloading_start_min"],
                        unloading_end_min=timing["unloading_end_min"],
                        ready_for_sale_min=timing["ready_for_sale_min"],
                        vehicle_release_min=timing["vehicle_release_min"],
                        capacity_units=v.capacity_units,
                        fixed_trip_cost_paise=r.fixed_trip_cost_paise,
                        variable_transport_cost_paise_per_unit=r.variable_transport_cost_paise_per_unit,
                        variable_handling_cost_paise_per_unit=r.variable_handling_cost_paise_per_unit,
                        assumption_profile=assumption_profile,
                    )
                )

    unpruned_count = len(candidates)
    if prune_symmetric_for_mip and len(candidates) > 12:
        # Retain non-dominated Wave 1 (earliest departure per (vehicle, route))
        # plus Wave 2 (earliest departure per (vehicle, route) compatible with each Wave 1 release on that vehicle)
        grouped: Dict[Tuple[str, str], List[CandidateTrip]] = {}
        for c in candidates:
            grouped.setdefault((c.vehicle_id, c.route_id), []).append(c)
        wave1_releases_by_veh: Dict[str, List[int]] = {}
        picked_map: Dict[str, CandidateTrip] = {}
        for (vid, _), grp in sorted(grouped.items()):
            grp_sorted = sorted(grp, key=lambda x: x.departure_min)
            w1 = grp_sorted[0]
            picked_map[w1.id] = w1
            wave1_releases_by_veh.setdefault(vid, []).append(w1.vehicle_release_min)
        for (vid, _), grp in sorted(grouped.items()):
            grp_sorted = sorted(grp, key=lambda x: x.departure_min)
            rels = sorted(set(wave1_releases_by_veh.get(vid, [])))
            if rels:
                w2 = next((x for x in grp_sorted if x.loading_start_min >= rels[0]), None)
                if w2 is not None:
                    picked_map[w2.id] = w2
        candidates = sorted(picked_map.values(), key=lambda x: (x.vehicle_id, x.departure_min, x.route_id))

    diagnostics = {
        "total_candidates": len(candidates),
        "candidate_scope": "bounded_two_wave" if len(candidates) < unpruned_count else "full_departure_grid",
        "optimality_scope": "selected candidate set only",
        "unpruned_grid_candidates": unpruned_count,
        "exclusion_counts": exclusion_counts,
        "departure_grid_min": grid,
        "extra_route_delay_min": extra_route_delay_min,
        "assumption_profile": assumption_profile,
    }
    return candidates, diagnostics
