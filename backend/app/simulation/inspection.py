"""Bayesian Value-of-Information (VoI) inspection engine (Section 8.7 & Step 18).
Fixes observation clock consistency: revised remaining life is always computed as
  max(0, revised_absolute_expiry_min - new_observed_at_min)
so elapsed time before inspection never inflates absolute expiry.
"""
from __future__ import annotations
import copy
import uuid
from typing import Any, Dict, List, Literal, Tuple
from app.domain.commitments import apply_operational_event
from app.domain.inventory import OperationalStateLedger
from app.planning.candidates import generate_candidate_trips
from app.planning.deadlines import compute_batch_deadlines
from app.planning.policy_selector import solve_scenario_selected_policy
from app.schemas.models import (
    Batch,
    BatchStatus,
    EventType,
    InspectionCandidateRank,
    InspectionObservation,
    OperationalEvent,
    PlanningSnapshot,
    SimulationWorld,
)
from app.simulation.worlds import generate_world_ensemble, keyed_uniform


def _updated_life_estimates_for_band(
    batch: Batch,
    observed_band: Literal["short", "medium", "long"],
    elapsed_duration_min: int,
    decision_at_min: int | None = None,
) -> Tuple[int, int, int]:
    """Compute updated (nominal, low, high) remaining life at new_obs_time = base_clock + elapsed_duration_min.
    When decision_at_min is provided, new_obs_time = max(batch.observed_at_min, decision_at_min) + elapsed_duration_min.
    """
    orig_low_abs = batch.estimated_expiry_low_min
    orig_nom_abs = batch.estimated_expiry_nominal_min
    orig_high_abs = batch.estimated_expiry_high_min

    base_clock = max(batch.observed_at_min, decision_at_min) if decision_at_min is not None else batch.observed_at_min
    new_obs_time = base_clock + int(elapsed_duration_min)

    span = max(0, orig_high_abs - orig_low_abs)
    if span == 0:
        nom_abs = orig_nom_abs
        low_abs = orig_low_abs
        high_abs = orig_high_abs
    elif observed_band == "short":
        nom_abs = orig_low_abs + int(round(0.20 * span))
        low_abs = orig_low_abs
        high_abs = orig_low_abs + int(round(0.45 * span))
    elif observed_band == "medium":
        nom_abs = orig_nom_abs
        low_abs = orig_low_abs + int(round(0.25 * span))
        high_abs = orig_high_abs - int(round(0.25 * span))
        if low_abs > nom_abs:
            low_abs = nom_abs
        if high_abs < nom_abs:
            high_abs = nom_abs
    else:
        nom_abs = orig_low_abs + int(round(0.80 * span))
        low_abs = orig_low_abs + int(round(0.55 * span))
        high_abs = orig_high_abs

    rem_nom = max(0, nom_abs - new_obs_time)
    rem_low = max(0, min(rem_nom, low_abs - new_obs_time))
    rem_high = max(rem_nom, high_abs - new_obs_time)
    return rem_nom, rem_low, rem_high


def rank_inspections_by_voi(
    ledger: OperationalStateLedger,
    planning_seed: int = 1701,
    planning_worlds: List[SimulationWorld] | None = None,
) -> List[InspectionCandidateRank]:
    """Rank inspectable available batches by Bayesian Value of Information (Section 8.7)."""
    snapshot = ledger.build_planning_snapshot()
    scenario = snapshot.scenario
    u_model = scenario.uncertainty_model
    if planning_worlds is None:
        planning_worlds = generate_world_ensemble(
            scenario, count=u_model.planning_world_count, base_seed=planning_seed, prefix="plan_world"
        )

    prior_weights: Dict[str, float] = {}
    if snapshot.posterior_world_weights:
        tot = sum(snapshot.posterior_world_weights.get(w.world_id, 0.0) for w in planning_worlds)
        if tot > 0:
            for w in planning_worlds:
                prior_weights[w.world_id] = snapshot.posterior_world_weights.get(w.world_id, 0.0) / tot
    if not prior_weights:
        unif = 1.0 / max(1, len(planning_worlds))
        for w in planning_worlds:
            prior_weights[w.world_id] = unif

    base_plan = solve_scenario_selected_policy(
        snapshot=snapshot,
        planning_seed=planning_seed,
        planning_worlds=planning_worlds,
    )
    no_insp_expected_net = int(
        base_plan.uncertainty_summary["mean_sampled_net_paise"]
        if base_plan.uncertainty_summary
        else base_plan.forecast_economics.net_outcome_paise
    )
    base_alloc_sig = sorted(
        (a.batch_id, a.destination_id, a.tier_id, a.quantity_units) for a in base_plan.allocations
    )

    conf_matrix = u_model.observation_model.matrix
    obs_bands: List[Literal["short", "medium", "long"]] = ["short", "medium", "long"]

    rankings: List[InspectionCandidateRank] = []

    for b in scenario.batches:
        avail_q = snapshot.warehouse_stock_by_batch.get(b.id, 0)
        if b.status != BatchStatus.AVAILABLE or not b.inspectable or avail_q <= 0:
            continue

        low_l = b.remaining_life_low_min if b.remaining_life_low_min is not None else b.remaining_life_nominal_min
        high_l = b.remaining_life_high_min if b.remaining_life_high_min is not None else b.remaining_life_nominal_min
        spread = max(0, high_l - low_l)

        new_time = max(snapshot.decision_at_min, b.observed_at_min) + b.inspection_duration_min

        # Case 1 (Test T18): Zero uncertainty spread -> inspection cannot change estimate
        if spread == 0:
            rankings.append(
                InspectionCandidateRank(
                    batch_id=b.id,
                    commodity_id=b.commodity_id,
                    quantity_units=avail_q,
                    uncertainty_spread_min=0,
                    inspection_cost_paise=b.inspection_cost_paise,
                    inspection_duration_min=b.inspection_duration_min,
                    no_inspection_expected_net_paise=no_insp_expected_net,
                    expected_net_with_inspection_paise=no_insp_expected_net - b.inspection_cost_paise,
                    estimated_inspection_value_paise=-b.inspection_cost_paise,
                    worthwhile=False,
                    decision_change_probability=0.0,
                    branch_outcomes={},
                    recommendation_reason="Batch has zero shelf-life uncertainty spread; inspection cannot improve dispatch decisions.",
                )
            )
            continue

        # Case 2 (Test T17): Check if inspecting takes longer than remaining dispatch opportunity
        temp_ledger = copy.deepcopy(ledger)
        temp_ledger.current_time_min = new_time
        temp_snap = temp_ledger.build_planning_snapshot()
        cands_after, _ = generate_candidate_trips(temp_snap)
        dls_after = compute_batch_deadlines(temp_snap, cands_after)
        batch_dls = [dl for dl in dls_after if dl.batch_id == b.id and dl.is_eligible]
        if not batch_dls:
            rankings.append(
                InspectionCandidateRank(
                    batch_id=b.id,
                    commodity_id=b.commodity_id,
                    quantity_units=avail_q,
                    uncertainty_spread_min=spread,
                    inspection_cost_paise=b.inspection_cost_paise,
                    inspection_duration_min=b.inspection_duration_min,
                    no_inspection_expected_net_paise=no_insp_expected_net,
                    expected_net_with_inspection_paise=no_insp_expected_net - b.inspection_cost_paise,
                    estimated_inspection_value_paise=-b.inspection_cost_paise,
                    worthwhile=False,
                    decision_change_probability=0.0,
                    branch_outcomes={},
                    recommendation_reason="Inspection duration exceeds remaining dispatch window; batch would miss all viable departures.",
                )
            )
            continue

        # Step 2: Evaluate conditional branches for o in {short, medium, long}
        branch_outcomes: Dict[str, Any] = {}
        expected_gross_after_insp = 0.0
        decision_change_prob = 0.0

        for obs in obs_bands:
            p_obs = 0.0
            unnorm_post: Dict[str, float] = {}
            for w in planning_worlds:
                true_band = w.true_life_band_by_batch.get(b.id, "medium")
                likelihood = conf_matrix.get(true_band, {}).get(obs, 0.3333)
                pw = prior_weights[w.world_id] * likelihood
                unnorm_post[w.world_id] = pw
                p_obs += pw

            if p_obs <= 1e-9:
                continue

            post_weights = {wid: val / p_obs for wid, val in unnorm_post.items()}
            rem_nom, rem_low, rem_high = _updated_life_estimates_for_band(
                b, obs, b.inspection_duration_min, decision_at_min=snapshot.decision_at_min
            )

            branch_ledger = copy.deepcopy(ledger)
            branch_ledger.current_time_min = new_time
            for bb in branch_ledger.scenario.batches:
                if bb.id == b.id:
                    bb.observed_at_min = new_time
                    bb.remaining_life_nominal_min = rem_nom
                    bb.remaining_life_low_min = rem_low
                    bb.remaining_life_high_min = rem_high
            branch_ledger.posterior_world_weights = post_weights
            branch_snap = branch_ledger.build_planning_snapshot()

            branch_plan = solve_scenario_selected_policy(
                snapshot=branch_snap,
                planning_seed=planning_seed,
                planning_worlds=planning_worlds,
                profile_subset_ids=["nominal", "shorter_life", "longer_travel"],
            )
            cond_net = int(
                branch_plan.uncertainty_summary["mean_sampled_net_paise"]
                if branch_plan.uncertainty_summary
                else branch_plan.forecast_economics.net_outcome_paise
            )
            branch_sig = sorted(
                (a.batch_id, a.destination_id, a.tier_id, a.quantity_units) for a in branch_plan.allocations
            )
            changed = branch_sig != base_alloc_sig
            if changed:
                decision_change_prob += p_obs

            expected_gross_after_insp += p_obs * cond_net
            branch_outcomes[obs] = {
                "probability": round(p_obs, 4),
                "updated_remaining_life_nominal_min": rem_nom,
                "updated_absolute_expiry_min": new_time + rem_nom,
                "conditional_expected_net_paise": cond_net,
                "selected_profile_id": (
                    branch_plan.uncertainty_summary["selected_profile_id"]
                    if branch_plan.uncertainty_summary
                    else "nominal"
                ),
                "changes_decision": changed,
            }

        if decision_change_prob <= 1e-6:
            net_with_insp = no_insp_expected_net - b.inspection_cost_paise
            voi_paise = -b.inspection_cost_paise
        else:
            net_with_insp = int(round(expected_gross_after_insp)) - b.inspection_cost_paise
            voi_paise = net_with_insp - no_insp_expected_net

        worthwhile = (voi_paise > 0) and (decision_change_prob > 0.0)
        reason = (
            f"Positive VoI (+Rs {voi_paise/100:.2f}); inspection changes dispatch allocation with {decision_change_prob*100:.0f}% probability."
            if worthwhile
            else f"Inspection not currently worthwhile (net VoI Rs {voi_paise/100:.2f} after Rs {b.inspection_cost_paise/100:.2f} fee and {b.inspection_duration_min}m delay)."
        )

        rankings.append(
            InspectionCandidateRank(
                batch_id=b.id,
                commodity_id=b.commodity_id,
                quantity_units=avail_q,
                uncertainty_spread_min=spread,
                inspection_cost_paise=b.inspection_cost_paise,
                inspection_duration_min=b.inspection_duration_min,
                no_inspection_expected_net_paise=no_insp_expected_net,
                expected_net_with_inspection_paise=net_with_insp,
                estimated_inspection_value_paise=voi_paise,
                worthwhile=worthwhile,
                decision_change_probability=round(decision_change_prob, 4),
                branch_outcomes=branch_outcomes,
                recommendation_reason=reason,
            )
        )

    rankings.sort(key=lambda r: (-r.estimated_inspection_value_paise, r.batch_id))
    return rankings


def execute_simulated_inspection(
    ledger: OperationalStateLedger,
    batch_id: str,
    true_world: SimulationWorld | None = None,
    seed: int = 2026,
    planning_seed: int = 1701,
    planning_worlds: List[SimulationWorld] | None = None,
    hidden_world: SimulationWorld | None = None,
    idempotency_key: str | None = None,
) -> Tuple[OperationalStateLedger, InspectionObservation]:
    """Execute a physical or simulated inspection on batch_id at current decision_at_min (Section 8.7).
    Idempotent: if batch_id was already inspected at the current revision/session, returns the existing observation
    without charging a second inspection fee or fabricating a new observation.
    """
    existing_obs = next((o for o in ledger.inspection_history if o.batch_id == batch_id), None)
    if existing_obs is not None:
        return ledger, existing_obs

    batch = next((b for b in ledger.scenario.batches if b.id == batch_id), None)
    if batch is None:
        raise ValueError(f"Unknown batch '{batch_id}'.")
    if not batch.inspectable or batch.status != BatchStatus.AVAILABLE:
        raise ValueError(f"Batch '{batch_id}' is not eligible for inspection.")

    if ledger.warehouse_unreserved.get(batch_id, 0) <= 0:
        raise ValueError("No uncommitted stock is available for inspection.")
    if ledger.current_time_min + batch.inspection_duration_min > ledger.scenario.evaluation_end_min:
        raise ValueError("Inspection would finish after the operating horizon.")
    u_model = ledger.scenario.uncertainty_model
    conf_matrix = u_model.observation_model.matrix

    if true_world is None and hidden_world is not None:
        true_world = hidden_world
    if true_world is not None:
        true_band = true_world.true_life_band_by_batch.get(batch_id, "medium")
    else:
        u_band = keyed_uniform(seed, "true_band", ledger.scenario.scenario_id, batch_id)
        true_band = "short" if u_band < 0.30 else ("medium" if u_band < 0.75 else "long")

    probs = conf_matrix.get(true_band, {"short": 0.15, "medium": 0.70, "long": 0.15})
    u_obs = keyed_uniform(seed, "sensor_obs", ledger.scenario.scenario_id, batch_id)
    cum = 0.0
    observed_band: Literal["short", "medium", "long"] = "medium"
    for band_name in ("short", "medium", "long"):
        cum += probs.get(band_name, 0.0)
        if u_obs <= cum:
            observed_band = band_name  # type: ignore[assignment]
            break

    new_time = max(ledger.current_time_min, batch.observed_at_min) + batch.inspection_duration_min
    rem_nom, rem_low, rem_high = _updated_life_estimates_for_band(
        batch, observed_band, batch.inspection_duration_min, decision_at_min=ledger.current_time_min
    )

    if planning_worlds is None:
        planning_worlds = generate_world_ensemble(
            ledger.scenario, count=u_model.planning_world_count, base_seed=planning_seed, prefix="plan_world"
        )
    prior_w = ledger.posterior_world_weights or {
        w.world_id: 1.0 / max(1, len(planning_worlds)) for w in planning_worlds
    }
    unnorm = {}
    tot_p = 0.0
    for w in planning_worlds:
        tb = w.true_life_band_by_batch.get(batch_id, "medium")
        lk = conf_matrix.get(tb, {}).get(observed_band, 0.3333)
        val = prior_w.get(w.world_id, 1.0 / len(planning_worlds)) * lk
        unnorm[w.world_id] = val
        tot_p += val
    post_w = {k: round(v / tot_p, 6) for k, v in unnorm.items()} if tot_p > 0 else prior_w

    obs_record = InspectionObservation(
        id=f"obs_{batch_id}_t{new_time}",
        batch_id=batch_id,
        observed_at_min=new_time,
        observed_band=observed_band,
        updated_remaining_life_nominal_min=rem_nom,
        updated_remaining_life_low_min=rem_low,
        updated_remaining_life_high_min=rem_high,
        cost_paise=batch.inspection_cost_paise,
        duration_min=batch.inspection_duration_min,
    )
    ev = OperationalEvent(
        id=f"ev_insp_{ledger.scenario_version_id}_{batch_id}",
        event_type=EventType.INSPECTION_EXECUTED,
        effective_at_min=new_time,
        entity_id=batch_id,
        expected_prior_revision=ledger.state_revision,
        payload={"observation": obs_record.model_dump(), "posterior_world_weights": post_w},
    )
    return apply_operational_event(ledger, ev), obs_record
