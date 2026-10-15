"""Seeded, keyed hidden SimulationWorld generator (Section 8.1)."""
from __future__ import annotations
import hashlib
from typing import Dict, List, Literal
from app.schemas.models import ScenarioPackage, SimulationWorld


def keyed_uniform(seed: int, world_id: str, entity_id: str, category: str) -> float:
    """Deterministic uniform [0, 1) draw keyed by (seed, world_id, entity_id, category).
    Ensures policy evaluation order never alters exogenous random outcomes.
    """
    raw = f"{seed}:{world_id}:{entity_id}:{category}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    int_val = int.from_bytes(digest[:8], "big")
    return (int_val % 10_000_000) / 10_000_000.0


def generate_simulation_world(
    scenario: ScenarioPackage,
    world_id: str,
    seed: int,
    is_nominal_world: bool = False,
) -> SimulationWorld:
    """Generate a single hidden SimulationWorld for a scenario."""
    u_model = scenario.uncertainty_model
    true_expiry_by_batch: Dict[str, int] = {}
    true_life_band_by_batch: Dict[str, Literal["short", "medium", "long"]] = {}

    life_min_delta, life_max_delta = u_model.life_shock_min_range

    for b in scenario.batches:
        if is_nominal_world:
            true_exp = b.estimated_expiry_nominal_min
            band: Literal["short", "medium", "long"] = "medium"
        else:
            u = keyed_uniform(seed, world_id, b.id, "life_shock")
            low_exp = b.estimated_expiry_low_min if b.remaining_life_low_min is not None else (b.estimated_expiry_nominal_min + life_min_delta)
            high_exp = b.estimated_expiry_high_min if b.remaining_life_high_min is not None else (b.estimated_expiry_nominal_min + life_max_delta)
            span = max(0, high_exp - low_exp)
            true_exp = int(round(low_exp + u * span))
            if u < 0.33:
                band = "short"
            elif u < 0.67:
                band = "medium"
            else:
                band = "long"
        true_expiry_by_batch[b.id] = max(0, true_exp)
        true_life_band_by_batch[b.id] = band

    delay_low, delay_high = u_model.travel_delay_min_range
    realized_outbound_by_route: Dict[str, int] = {}
    realized_return_by_route: Dict[str, int] = {}

    for r in scenario.routes:
        if is_nominal_world:
            delay = 0
            ret_delay = 0
        else:
            u_out = keyed_uniform(seed, world_id, r.id, "outbound_delay")
            u_ret = keyed_uniform(seed, world_id, r.id, "return_delay")
            # Skewed delay distribution: 65% minor/zero delay, 35% moderate-to-high delay
            if u_out < 0.50:
                delay = int(round(delay_low + (u_out / 0.50) * max(0, (delay_high - delay_low) * 0.25)))
            else:
                delay = int(round(delay_low + ((u_out - 0.50) / 0.50) * max(0, delay_high - delay_low)))
            ret_delay = int(round(delay_low + u_ret * max(0, (delay_high - delay_low) * 0.4)))
        realized_outbound_by_route[r.id] = max(1, r.outbound_travel_min + delay)
        realized_return_by_route[r.id] = max(1, r.return_travel_min + ret_delay)

    dem_low, dem_high = u_model.demand_shock_multiplier_range
    realized_demand_by_bucket_tier: Dict[str, Dict[str, int]] = {}
    demand_shock_by_bucket: Dict[str, float] = {}

    for db in scenario.demand_buckets:
        if is_nominal_world:
            shock = 1.0
        else:
            u_dem = keyed_uniform(seed, world_id, db.id, "demand_shock")
            shock = dem_low + u_dem * (dem_high - dem_low)
        demand_shock_by_bucket[db.id] = round(shock, 4)
        tier_demands: Dict[str, int] = {}
        for tier in db.price_tiers:
            # Common demand shock links all price tiers in the same world (Section 6.3)
            realized_q = int(round(tier.forecast_sales_units * shock))
            realized_q = max(0, min(db.accepted_quantity_cap, realized_q))
            tier_demands[tier.tier_id] = realized_q
        realized_demand_by_bucket_tier[db.id] = tier_demands

    return SimulationWorld(
        world_id=world_id,
        seed=seed,
        true_expiry_by_batch=true_expiry_by_batch,
        realized_outbound_travel_by_route=realized_outbound_by_route,
        realized_return_travel_by_route=realized_return_by_route,
        realized_demand_by_bucket_tier=realized_demand_by_bucket_tier,
        demand_shock_by_bucket=demand_shock_by_bucket,
        true_life_band_by_batch=true_life_band_by_batch,
    )


def generate_world_ensemble(
    scenario: ScenarioPackage,
    count: int,
    base_seed: int = 1701,
    prefix: str = "world",
) -> List[SimulationWorld]:
    """Generate a reproducible ensemble of SimulationWorld instances."""
    worlds: List[SimulationWorld] = []
    for i in range(count):
        w_id = f"{prefix}_{i+1:03d}"
        worlds.append(generate_simulation_world(scenario, w_id, base_seed + i * 37, is_nominal_world=False))
    return worlds
