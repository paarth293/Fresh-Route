"""Illustrative Q10 Arrhenius Cold-Chain Temperature Excursion Model (Section 7 Optional Extension & P1 Audit).
Models biological shelf-life consumption under a simulated or recorded temperature trace.
Explicitly discloses model provenance, illustrative coefficients, and non-certification boundary.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List
from app.schemas.models import ScenarioPackage


def compute_q10_acceleration_factor(
    actual_temp_celsius: float,
    reference_temp_celsius: float = 4.0,
    q10_coefficient: float = 2.2,
) -> float:
    """Compute biological aging acceleration factor relative to reference cold-chain temperature:
    factor = Q10 ^ ((T_actual - T_ref) / 10.0)
    """
    delta_t = float(actual_temp_celsius) - float(reference_temp_celsius)
    return round(math.pow(float(q10_coefficient), delta_t / 10.0), 4)


def simulate_coldchain_excursion_impact(
    scenario: ScenarioPackage,
    ambient_or_reefer_temp_celsius: float = 12.0,
    excursion_duration_min: int = 60,
    reference_temp_celsius: float = 4.0,
    q10_by_commodity: Dict[str, float] | None = None,
    sensor_source: str = "simulated_reefer_trace_v1",
) -> Dict[str, Any]:
    """Compute the impact of a cold-chain temperature trace on batch remaining life and destination eligibility."""
    if not math.isfinite(ambient_or_reefer_temp_celsius) or not -50 <= ambient_or_reefer_temp_celsius <= 100:
        raise ValueError("Temperature must be finite and between -50 and 100 degrees Celsius.")
    if excursion_duration_min < 0:
        raise ValueError("Excursion duration cannot be negative.")
    default_q10 = {"TOM": 1.8, "LGR": 2.4, "STR": 2.8}
    q10_map = q10_by_commodity or default_q10
    dest_comm_to_bucket = {(db.destination_id, db.commodity_id): db for db in scenario.demand_buckets}

    batch_impacts: List[Dict[str, Any]] = []
    total_lost_life_min = 0
    newly_ineligible_pairs = 0

    for b in scenario.batches:
        q10 = q10_map.get(b.commodity_id, 2.2)
        accel = compute_q10_acceleration_factor(ambient_or_reefer_temp_celsius, reference_temp_celsius, q10)
        extra_life_lost = int(round(excursion_duration_min * max(0.0, accel - 1.0)))
        total_lost_life_min += extra_life_lost

        orig_exp = b.estimated_expiry_nominal_min
        new_exp = max(0, orig_exp - extra_life_lost)

        lost_destinations: List[str] = []
        surviving_destinations: List[str] = []
        for d in scenario.destinations:
            db = dest_comm_to_bucket.get((d.id, b.commodity_id))
            if db is None:
                continue
            req_exp = db.sale_at_min + db.freshness_buffer_min + 1
            was_ok = orig_exp >= req_exp
            now_ok = new_exp >= req_exp
            if was_ok and not now_ok:
                lost_destinations.append(d.id)
                newly_ineligible_pairs += 1
            elif now_ok:
                surviving_destinations.append(d.id)

        batch_impacts.append(
            {
                "batch_id": b.id,
                "commodity_id": b.commodity_id,
                "quantity_units": b.quantity_units,
                "q10_coefficient": q10,
                "aging_acceleration_factor": accel,
                "original_expiry_min": orig_exp,
                "effective_expiry_after_excursion_min": new_exp,
                "biological_life_lost_min": extra_life_lost,
                "lost_destinations": lost_destinations,
                "surviving_destinations": surviving_destinations,
                "status_alert": "WINDOW_LOST" if lost_destinations else ("DEGRADED" if extra_life_lost > 30 else "STABLE"),
            }
        )

    return {
        "scenario_id": scenario.scenario_id,
        "model_version": "illustrative_q10_arrhenius_v1",
        "calibration_status": "illustrative_uncalibrated",
        "sensor_provenance": sensor_source,
        "food_safety_disclaimer": "Decision-support shelf-life estimate only; not a food-safety certification.",
        "reference_temp_celsius": reference_temp_celsius,
        "excursion_temp_celsius": ambient_or_reefer_temp_celsius,
        "excursion_duration_min": excursion_duration_min,
        "mean_life_lost_min": round(total_lost_life_min / max(1, len(scenario.batches)), 1),
        "newly_ineligible_batch_destination_pairs": newly_ineligible_pairs,
        "batch_impacts": batch_impacts,
    }
