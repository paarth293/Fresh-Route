"""2D parametric stress-test surface (Delay x Life Decay) and Risk-Preference Sensitivity View (Section 3.2 & P1 Audit)."""
from __future__ import annotations
from typing import Any, Dict, List
from app.schemas.models import PlanResult, ScenarioPackage, SimulationWorld
from app.simulation.replay import replay_plan_in_world


def compute_2d_stress_surface(
    scenario: ScenarioPackage,
    plan: PlanResult,
    delay_steps_min: List[int] | None = None,
    life_steps_min: List[int] | None = None,
) -> Dict[str, Any]:
    """Evaluate a plan across a 2D grid of (Uniform Route Delay Shock x Uniform Batch Shelf-Life Shock)."""
    d_steps = delay_steps_min or [0, 15, 30, 45, 60]
    l_steps = life_steps_min or [0, -20, -40, -60, -80]

    cells: List[Dict[str, Any]] = []
    for l_shock in l_steps:
        for d_shock in d_steps:
            true_exp = {
                b.id: max(0, b.estimated_expiry_nominal_min + l_shock) for b in scenario.batches
            }
            out_travel = {
                r.id: max(1, r.outbound_travel_min + d_shock) for r in scenario.routes
            }
            ret_travel = {r.id: r.return_travel_min for r in scenario.routes}
            dem_by_bt = {
                db.id: {ts.tier_id: ts.forecast_sales_units for ts in db.price_tiers}
                for db in scenario.demand_buckets
            }
            band_by_b = {b.id: ("short" if l_shock <= -40 else "medium") for b in scenario.batches}

            w = SimulationWorld(
                world_id=f"stress_d{d_shock}_l{abs(l_shock)}",
                seed=100,
                true_expiry_by_batch=true_exp,
                realized_outbound_travel_by_route=out_travel,
                realized_return_travel_by_route=ret_travel,
                realized_demand_by_bucket_tier=dem_by_bt,
                demand_shock_by_bucket={db.id: 1.0 for db in scenario.demand_buckets},
                true_life_band_by_batch=band_by_b,
            )
            rep = replay_plan_in_world(scenario, plan, w)
            cells.append(
                {
                    "delay_shock_min": d_shock,
                    "life_shock_min": l_shock,
                    "realized_net_paise": rep.realized_economics.net_outcome_paise,
                    "sold_units": rep.sold_total_units,
                    "spoilage_units": rep.expired_unsold_total_units,
                    "service_failures": rep.realized_service_failures,
                }
            )

    return {
        "plan_id": plan.plan_id,
        "delay_steps_min": d_steps,
        "life_steps_min": l_steps,
        "cells": cells,
    }


def compute_pareto_lambda_curve(
    plan_with_uncertainty: PlanResult,
) -> List[Dict[str, Any]]:
    """Risk-Preference Sensitivity View across candidate assumption profiles (lambda in 0.0..1.0).
    Filters to service-eligible candidates consistently and marks nondominated profiles on (E[Net], Min[Net]).
    """
    u_sum = plan_with_uncertainty.uncertainty_summary
    if not u_sum or not u_sum.get("candidate_profiles"):
        return []

    all_profiles = u_sum["candidate_profiles"]
    eligible_profiles = [p for p in all_profiles if p.get("meets_service_limit", True)]
    if not eligible_profiles:
        eligible_profiles = all_profiles

    # Identify nondominated candidate profiles in (mean_sampled_net_paise, min_sampled_net_paise)
    nondom_ids: set[str] = set()
    for p in eligible_profiles:
        dominated = any(
            (q["mean_sampled_net_paise"] >= p["mean_sampled_net_paise"] and q["min_sampled_net_paise"] > p["min_sampled_net_paise"])
            or (q["mean_sampled_net_paise"] > p["mean_sampled_net_paise"] and q["min_sampled_net_paise"] >= p["min_sampled_net_paise"])
            for q in eligible_profiles
            if q["profile_id"] != p["profile_id"]
        )
        if not dominated:
            nondom_ids.add(p["profile_id"])

    curve: List[Dict[str, Any]] = []
    for step in range(0, 11):
        lam = round(step * 0.1, 2)
        best_prof = max(
            eligible_profiles,
            key=lambda p: (
                (1.0 - lam) * p["mean_sampled_net_paise"] + lam * p["min_sampled_net_paise"],
                p["mean_sampled_net_paise"],
            ),
        )
        score = int(
            round((1.0 - lam) * best_prof["mean_sampled_net_paise"] + lam * best_prof["min_sampled_net_paise"])
        )
        curve.append(
            {
                "view_type": "risk_preference_sensitivity_view",
                "risk_lambda": lam,
                "selected_profile_id": best_prof["profile_id"],
                "selected_profile_label": best_prof["label"],
                "is_nondominated": best_prof["profile_id"] in nondom_ids,
                "risk_score_paise": score,
                "mean_sampled_net_paise": best_prof["mean_sampled_net_paise"],
                "min_sampled_net_paise": best_prof["min_sampled_net_paise"],
                "mean_spoilage_units": best_prof["mean_spoilage_units"],
            }
        )
    return curve
