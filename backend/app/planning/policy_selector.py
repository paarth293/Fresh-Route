"""Uncertainty-aware candidate policy selector (Section 8.4 & Step 17)."""
from __future__ import annotations
import copy
import time
import uuid
from typing import Any, Dict, List

import json
from pathlib import Path

CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs"


def resolve_planning_config(config_id: str) -> Dict[str, Any]:
    """Load and validate configuration file from configs/{config_id}.json."""
    if not config_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in config_id):
        raise ValueError("INVALID_CONFIGURATION: Invalid configuration identifier.")
    cfg_path = CONFIGS_DIR / f"{config_id}.json"
    if not cfg_path.exists():
        raise ValueError(f"INVALID_CONFIGURATION: Unknown config_id '{config_id}'.")
    return json.loads(cfg_path.read_text())

from app.planning.mip import solve_deterministic_mip
from app.schemas.models import (
    PlanResult,
    PlanningSnapshot,
    PolicyName,
    SimulationWorld,
    SolverStatus,
)
from app.simulation.replay import replay_plan_in_world
from app.simulation.worlds import generate_world_ensemble
from app.validation.plan import validate_plan_independently


ASSUMPTION_PROFILES: List[Dict[str, Any]] = [
    {
        "profile_id": "nominal",
        "label": "Nominal Estimates",
        "expiry_offset_min": 0,
        "extra_route_delay_min": 0,
        "demand_multiplier": 1.0,
        "allow_discounts": True,
    },
    {
        "profile_id": "shorter_life",
        "label": "Conservative Shelf-Life (-45m)",
        "expiry_offset_min": -45,
        "extra_route_delay_min": 0,
        "demand_multiplier": 1.0,
        "allow_discounts": True,
    },
    {
        "profile_id": "longer_travel",
        "label": "Route Delay Buffer (+20m)",
        "expiry_offset_min": 0,
        "extra_route_delay_min": 20,
        "demand_multiplier": 1.0,
        "allow_discounts": True,
    },
    {
        "profile_id": "lower_demand",
        "label": "Cautious Demand (85%)",
        "expiry_offset_min": 0,
        "extra_route_delay_min": 0,
        "demand_multiplier": 0.85,
        "allow_discounts": True,
    },
    {
        "profile_id": "combined_stress",
        "label": "Combined Stress (-30m life, +15m delay, 90% demand)",
        "expiry_offset_min": -30,
        "extra_route_delay_min": 15,
        "demand_multiplier": 0.90,
        "allow_discounts": True,
    },
    {
        "profile_id": "full_price_only",
        "label": "No-Discount Full Price",
        "expiry_offset_min": 0,
        "extra_route_delay_min": 0,
        "demand_multiplier": 1.0,
        "allow_discounts": False,
    },
]


def solve_scenario_selected_policy(
    snapshot: PlanningSnapshot,
    config_id: str = "balanced_v1",
    planning_seed: int = 1701,
    planning_worlds: List[SimulationWorld] | None = None,
    risk_lambda_override: float | None = None,
    max_service_failure_rate: float | None = None,
    profile_subset_ids: List[str] | None = None,
) -> PlanResult:
    """Generate deterministic candidate plans under assumption profiles, validate against universal hard constraints,
    evaluate across shared planning worlds, and select by risk score:
      score = (1 - lambda) * mean_net + lambda * minimum_sampled_net
    """
    t0 = time.perf_counter()
    scenario = snapshot.scenario
    u_model = scenario.uncertainty_model
    cfg = resolve_planning_config(config_id)
    cfg_lam = cfg.get("risk_lambda")
    lam = (
        risk_lambda_override
        if risk_lambda_override is not None
        else (float(cfg_lam) if cfg_lam is not None else u_model.risk_lambda)
    )
    if max_service_failure_rate is None and cfg.get("max_service_failure_rate") is not None:
        max_service_failure_rate = float(cfg["max_service_failure_rate"])

    if planning_worlds is None:
        planning_worlds = generate_world_ensemble(
            scenario, count=u_model.planning_world_count, base_seed=planning_seed, prefix="plan_world"
        )

    # Normalize posterior weights if available from an inspection update
    weights_by_world: Dict[str, float] = {}
    if snapshot.posterior_world_weights:
        total_w = sum(snapshot.posterior_world_weights.get(w.world_id, 0.0) for w in planning_worlds)
        if total_w > 0:
            for w in planning_worlds:
                weights_by_world[w.world_id] = snapshot.posterior_world_weights.get(w.world_id, 0.0) / total_w
    if not weights_by_world:
        unif = 1.0 / max(1, len(planning_worlds))
        for w in planning_worlds:
            weights_by_world[w.world_id] = unif

    candidate_plans: List[PlanResult] = []
    profile_evaluations: List[Dict[str, Any]] = []

    active_profiles = [p for p in ASSUMPTION_PROFILES if (profile_subset_ids is None or p['profile_id'] in profile_subset_ids)]
    for prof in active_profiles:
        cand_plan = solve_deterministic_mip(
            snapshot=snapshot,
            config_id=config_id,
            assumption_profile=prof["profile_id"],
            expiry_offset_min=prof["expiry_offset_min"],
            extra_route_delay_min=prof["extra_route_delay_min"],
            demand_multiplier=prof["demand_multiplier"],
            allow_discounts=prof["allow_discounts"],
        )
        # Validate against universal hard constraints on the nominal snapshot
        nom_val = validate_plan_independently(snapshot, cand_plan, checked_under_assumptions=f"profile:{prof['profile_id']}")
        cand_plan.validation = nom_val
        if not nom_val.is_valid:
            continue

        # Evaluate across planning_worlds
        weighted_net = 0.0
        min_net = 10**18
        weighted_spoilage = 0.0
        weighted_sold = 0.0
        weighted_failures = 0.0

        for w in planning_worlds:
            wt = weights_by_world[w.world_id]
            if wt <= 0.0:
                continue
            rep = replay_plan_in_world(
                scenario=scenario,
                plan=cand_plan,
                world=w,
                sunk_committed_cost_paise=snapshot.sunk_cost_paise,
                inspection_cost_paise=sum(o.cost_paise for o in snapshot.inspection_history),
            )
            net_p = rep.realized_economics.net_outcome_paise
            weighted_net += wt * net_p
            if net_p < min_net:
                min_net = net_p
            weighted_spoilage += wt * rep.expired_unsold_total_units
            weighted_sold += wt * rep.sold_total_units
            weighted_failures += wt * (1.0 if rep.realized_service_failures > 0 else 0.0)

        if min_net == 10**18:
            min_net = cand_plan.forecast_economics.net_outcome_paise

        risk_score = (1.0 - lam) * weighted_net + lam * float(min_net)
        meets_limit = True
        if max_service_failure_rate is not None and weighted_failures > max_service_failure_rate:
            meets_limit = False

        profile_evaluations.append(
            {
                "profile_id": prof["profile_id"],
                "label": prof["label"],
                "plan_id": cand_plan.plan_id,
                "forecast_net_paise": cand_plan.forecast_economics.net_outcome_paise,
                "mean_sampled_net_paise": int(round(weighted_net)),
                "min_sampled_net_paise": int(min_net),
                "risk_score_paise": int(round(risk_score)),
                "mean_spoilage_units": round(weighted_spoilage, 2),
                "mean_sold_units": round(weighted_sold, 2),
                "sampled_failure_rate": round(weighted_failures, 4),
                "meets_service_limit": meets_limit,
                "active_trip_count": len(cand_plan.trips),
                "total_dispatched_units": sum(a.quantity_units for a in cand_plan.allocations),
            }
        )
        candidate_plans.append(cand_plan)

    if not candidate_plans:
        # Fallback to nominal MIP
        fallback = solve_deterministic_mip(snapshot=snapshot, config_id=config_id)
        fallback.policy = PolicyName.SCENARIO_SELECTED
        return fallback

    # Filter by service limit if configured; do not silently relax if none meet it (Section 8.4)
    eligible_indices = [
        idx for idx, ev in enumerate(profile_evaluations) if ev["meets_service_limit"]
    ]
    service_limit_violated_all = False
    if not eligible_indices:
        service_limit_violated_all = True
        eligible_indices = list(range(len(profile_evaluations)))

    best_idx = max(
        eligible_indices,
        key=lambda i: (
            profile_evaluations[i]["risk_score_paise"],
            profile_evaluations[i]["mean_sampled_net_paise"],
            -profile_evaluations[i]["active_trip_count"],
        ),
    )

    chosen = copy.deepcopy(candidate_plans[best_idx])
    chosen_eval = profile_evaluations[best_idx]
    chosen.plan_id = f"plan_scen_{uuid.uuid4().hex[:8]}"
    chosen.policy = PolicyName.SCENARIO_SELECTED
    chosen.solver_status = SolverStatus.HEURISTIC_SELECTED
    chosen.solve_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    chosen.uncertainty_summary = {
        "selection_method": "bounded_candidate_profile_selection",
        "risk_lambda": lam,
        "planning_world_count": len(planning_worlds),
        "selected_profile_id": chosen_eval["profile_id"],
        "selected_profile_label": chosen_eval["label"],
        "mean_sampled_net_paise": chosen_eval["mean_sampled_net_paise"],
        "min_sampled_net_paise": chosen_eval["min_sampled_net_paise"],
        "risk_score_paise": chosen_eval["risk_score_paise"],
        "mean_spoilage_units": chosen_eval["mean_spoilage_units"],
        "mean_sold_units": chosen_eval["mean_sold_units"],
        "sampled_failure_rate": chosen_eval["sampled_failure_rate"],
        "service_limit_violated_all": service_limit_violated_all,
        "candidate_profiles": profile_evaluations,
    }
    return chosen
