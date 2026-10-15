"""Structured Evidence-Grounded Operational Briefing (Section 3.3 P3, Section 8 & P1 Audit).
Provides deterministic, template-structured answers from planning snapshot data without
conflating planning forecasts with hidden-world simulation replay outcomes.
"""
from __future__ import annotations
from typing import Any, Dict, List
from app.domain.units import paise_to_rupees_str
from app.planning.fragility import analyze_plan_fragility
from app.schemas.models import (
    InspectionCandidateRank,
    PlanResult,
    PlanningSnapshot,
    ReplayResult,
)


def generate_copilot_briefing_and_qa(
    snapshot: PlanningSnapshot,
    plan: PlanResult,
    replay: ReplayResult | None,
    inspection_ranks: List[InspectionCandidateRank],
) -> Dict[str, Any]:
    """Generate structured operational briefing cards separating planning forecasts from post-hoc simulation replay."""
    frag = analyze_plan_fragility(snapshot, plan)
    total_stock = sum(b.quantity_units for b in snapshot.scenario.batches)
    disp_stock = sum(a.quantity_units for a in plan.allocations)
    unalloc_stock = sum(plan.unallocated_by_batch.values())

    discount_allocs = [a for a in plan.allocations if a.discount_pct > 0]
    discount_vol = sum(a.quantity_units for a in discount_allocs)

    top_insp = inspection_ranks[0] if inspection_ranks else None
    insp_summary = (
        f"Top candidate is {top_insp.batch_id} (Estimated VoI: {paise_to_rupees_str(top_insp.estimated_inspection_value_paise)}, "
        f"decision change prob: {top_insp.decision_change_probability*100:.0f}%, worthwhile: {top_insp.worthwhile}). "
        f"Selective inspection should be declined when VoI <= 0."
        if top_insp
        else "No eligible batches require inspection."
    )

    replay_note = (
        f"In evaluation replay (seed world '{replay.world_id}'), {replay.sold_total_units} kg were sold, "
        f"{replay.expired_unsold_total_units} kg expired unsold, and {replay.unexpired_carryover_total_units} kg "
        f"carried over for a Simulated Replay Net of {paise_to_rupees_str(replay.realized_economics.net_outcome_paise)}."
        if replay is not None
        else "Simulation replay has not been revealed yet."
    )

    qa_pairs: List[Dict[str, str]] = [
        {
            "question": "Why not always ship the oldest stock first (pure FEFO)?",
            "answer": (
                f"Pure FEFO greedy dispatches earliest-expiring stock without coordinating multi-wave vehicle turnarounds or "
                f"price-tier margins. Under current planning observations, this plan forecasts a Net Contribution of "
                f"{paise_to_rupees_str(plan.forecast_economics.net_outcome_paise)} by matching fragile lots to short corridors "
                f"while preserving long-life stock for distant buyers."
            ),
            "evidence_tag": "Planning Forecast | MVP-02 / MVP-03",
        },
        {
            "question": "What is the single most fragile assignment in the current plan?",
            "answer": (
                f"Trip '{frag['most_fragile_trip_id'] or 'N/A'}' has a replay-verified single-route failure boundary of "
                f"+{frag['min_breakpoint_delay_min']} minutes before violating a dock closing window or sales readiness instant."
            ),
            "evidence_tag": f"Replay-Verified Boundary = +{frag['min_breakpoint_delay_min']}m",
        },
        {
            "question": "Why did the planner recommend discounts on selected buckets instead of full price?",
            "answer": (
                f"{discount_vol} kg of the {disp_stock} kg dispatched use a permitted 10% or 20% markdown tier because the "
                f"additional demand absorbed within the buyer's acceptance cap yields higher forecast net contribution after "
                f"transport and avoided origin disposal costs than leaving surplus stock unassigned."
            ),
            "evidence_tag": f"{discount_vol} kg discounted / {unalloc_stock} kg unassigned",
        },
        {
            "question": "Should the operator pause dispatch to inspect an uncertain batch right now?",
            "answer": insp_summary,
            "evidence_tag": "Bayesian VoI Queue (Declinable)",
        },
        {
            "question": "How are planning forecasts separated from simulated replay outcomes?",
            "answer": (
                f"Planning uses only observed estimates ({disp_stock} kg dispatched, Forecast Net "
                f"{paise_to_rupees_str(plan.forecast_economics.net_outcome_paise)}). {replay_note}"
            ),
            "evidence_tag": "Strict Forecast vs. Simulation Separation",
        },
    ]

    return {
        "plan_id": plan.plan_id,
        "briefing_type": "structured_template_briefing_v1",
        "headline": (
            f"Dispatched {disp_stock}/{total_stock} kg across {len(plan.trips)} trips | "
            f"Replay-Verified Failure Boundary: +{frag['min_breakpoint_delay_min']}m | "
            f"Forecast Net: {paise_to_rupees_str(plan.forecast_economics.net_outcome_paise)}"
        ),
        "qa_pairs": qa_pairs,
    }
