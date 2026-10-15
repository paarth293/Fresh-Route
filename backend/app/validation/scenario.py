"""Canonical scenario package validation implementing all 14 checks (Section 6.4)."""
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import math
from pydantic import ValidationError
from app.schemas.models import BatchStatus, CommitmentState, ScenarioPackage


class ScenarioValidationErrorItem( dict ):
    pass


def validate_scenario_dict(
    raw_data: Dict[str, Any],
    max_upload_bytes: int = 5_242_880,
    raw_byte_length: int | None = None,
) -> Tuple[ScenarioPackage | None, List[Dict[str, Any]]]:
    """Validate raw JSON dictionary against all 14 checks in Section 6.4.
    Returns (ScenarioPackage or None, list of structured error dicts).
    """
    errors: List[Dict[str, Any]] = []

    def add_err(field_path: str, entity_id: str, code: str, message: str, repair: str) -> None:
        errors.append(
            {
                "field_path": field_path,
                "entity_id": entity_id,
                "error_code": code,
                "message": message,
                "repair_guidance": repair,
            }
        )

    # Check 13: Upload size & server path injection check
    if raw_byte_length is not None and raw_byte_length > max_upload_bytes:
        add_err(
            "root",
            "package",
            "PAYLOAD_TOO_LARGE",
            f"Scenario payload ({raw_byte_length} bytes) exceeds limit ({max_upload_bytes} bytes).",
            "Reduce scenario size or split into smaller packages.",
        )
        return None, errors

    if not isinstance(raw_data, dict):
        add_err("root", "package", "INVALID_JSON_OBJECT", "Scenario must be a JSON object.", "Provide a valid JSON dict.")
        return None, errors

    for forbidden_path_key in ("file_path", "server_path", "import_path", "source_uri"):
        if forbidden_path_key in raw_data:
            add_err(
                forbidden_path_key,
                "package",
                "SERVER_PATH_FORBIDDEN",
                f"Field '{forbidden_path_key}' is not permitted in scenario uploads.",
                "Remove server path references and inline scenario entities directly.",
            )

    # Check 11: Schema version check
    schema_version = raw_data.get("schema_version")
    if schema_version != "1.0":
        add_err(
            "schema_version",
            str(raw_data.get("scenario_id", "unknown")),
            "UNSUPPORTED_SCHEMA_VERSION",
            f"Unsupported schema_version '{schema_version}'. Expected '1.0'.",
            "Set 'schema_version' to '1.0'.",
        )

    # Parse with Pydantic first (Check 2, Check 10 enum, Check 11 extra fields)
    try:
        pkg = ScenarioPackage.model_validate(raw_data)
    except ValidationError as exc:
        for err in exc.errors():
            loc_str = ".".join(str(x) for x in err.get("loc", []))
            add_err(
                loc_str,
                str(raw_data.get("scenario_id", "package")),
                "SCHEMA_VALIDATION_ERROR",
                err.get("msg", "Invalid field"),
                "Fix the field type or remove unknown fields according to Schema v1.0.",
            )
        return None, errors

    # Check 1: Unique IDs within each collection
    def check_unique(items: List[Any], collection_name: str) -> set[str]:
        seen: set[str] = set()
        for idx, item in enumerate(items):
            if item.id in seen:
                add_err(
                    f"{collection_name}[{idx}].id",
                    item.id,
                    "DUPLICATE_ENTITY_ID",
                    f"Duplicate ID '{item.id}' in {collection_name}.",
                    f"Ensure every entity in {collection_name} has a unique 'id'.",
                )
            seen.add(item.id)
        return seen

    comm_ids = check_unique(pkg.commodities, "commodities")
    batch_ids = check_unique(pkg.batches, "batches")
    dest_ids = check_unique(pkg.destinations, "destinations")
    veh_ids = check_unique(pkg.vehicles, "vehicles")
    route_ids = check_unique(pkg.routes, "routes")
    bucket_ids = check_unique(pkg.demand_buckets, "demand_buckets")
    check_unique(pkg.commitments, "commitments")

    uncertainty = pkg.uncertainty_model
    for name in ("life_shock_min_range", "travel_delay_min_range", "demand_shock_multiplier_range"):
        values = getattr(uncertainty, name)
        if len(values) != 2 or any(not math.isfinite(v) for v in values) or values[0] > values[1] or (name != "life_shock_min_range" and values[0] < 0):
            add_err(f"uncertainty_model.{name}", "package", "INVALID_UNCERTAINTY_RANGE", "Provide two ordered range bounds; delays and demand multipliers must be nonnegative.", "Correct the uncertainty range.")
    if uncertainty.departure_grid_min <= 0 or uncertainty.planning_world_count <= 0 or uncertainty.evaluation_world_count <= 0 or not 0 <= uncertainty.risk_lambda <= 1:
        add_err("uncertainty_model", "package", "INVALID_UNCERTAINTY_PARAMETER", "Grid and world counts must be positive; risk_lambda must be between 0 and 1.", "Correct the uncertainty settings.")
    observation = uncertainty.observation_model
    if pkg.cost_model.cancellation_fee_paise_per_trip < 0:
        add_err("cost_model.cancellation_fee_paise_per_trip", "package", "NEGATIVE_CANCELLATION_FEE", "Cancellation fee must be nonnegative.", "Set a nonnegative fee.")
    bands = {"short", "medium", "long"}
    if set(observation.bands) != bands or len(observation.bands) != 3 or set(observation.matrix) != bands:
        add_err("uncertainty_model.observation_model", "package", "INVALID_OBSERVATION_MODEL", "Observation bands must be short, medium, and long.", "Use the canonical three bands.")
    for band, row in observation.matrix.items():
        if set(row) != bands or any(not math.isfinite(v) or not 0 <= v <= 1 for v in row.values()) or abs(sum(row.values()) - 1) > 1e-6:
            add_err(f"uncertainty_model.observation_model.matrix.{band}", "package", "INVALID_OBSERVATION_PROBABILITIES", "Each observation row must contain all bands with probabilities in [0,1] summing to 1.", "Correct and normalize the observation probabilities.")

    # Check 9: Decision time and evaluation_end_min ordering
    if pkg.decision_at_min < 0 or pkg.evaluation_end_min <= pkg.decision_at_min:
        add_err(
            "evaluation_end_min",
            pkg.scenario_id,
            "INVALID_HORIZON_WINDOW",
            f"decision_at_min ({pkg.decision_at_min}) must be >= 0 and < evaluation_end_min ({pkg.evaluation_end_min}).",
            "Set evaluation_end_min strictly greater than decision_at_min.",
        )

    # Validate batches (Checks 1, 3, 4, 5, 10)
    for idx, b in enumerate(pkg.batches):
        if b.inspection_cost_paise < 0 or b.inspection_duration_min < 0:
            add_err(f"batches[{idx}]", b.id, "NEGATIVE_INSPECTION_PARAMETER", "Inspection cost and duration must be nonnegative.", "Set nonnegative inspection values.")
        if b.commodity_id not in comm_ids:
            add_err(
                f"batches[{idx}].commodity_id",
                b.id,
                "UNRESOLVED_COMMODITY_REF",
                f"Batch '{b.id}' references unknown commodity_id '{b.commodity_id}'.",
                "Reference a valid commodity ID defined in 'commodities'.",
            )
        if b.quantity_units < 0:
            add_err(
                f"batches[{idx}].quantity_units",
                b.id,
                "NEGATIVE_BATCH_QUANTITY",
                f"Batch '{b.id}' has negative quantity_units ({b.quantity_units}).",
                "Set quantity_units >= 0.",
            )
        if b.observed_at_min > pkg.decision_at_min:
            add_err(
                f"batches[{idx}].observed_at_min",
                b.id,
                "FUTURE_OBSERVATION_TIMESTAMP",
                f"Batch '{b.id}' observed_at_min ({b.observed_at_min}) is after decision_at_min ({pkg.decision_at_min}).",
                "Ensure batch observation timestamps do not lie in the future.",
            )
        low = b.remaining_life_low_min if b.remaining_life_low_min is not None else b.remaining_life_nominal_min
        high = b.remaining_life_high_min if b.remaining_life_high_min is not None else b.remaining_life_nominal_min
        if not (low <= b.remaining_life_nominal_min <= high):
            add_err(
                f"batches[{idx}].remaining_life_nominal_min",
                b.id,
                "UNORDERED_LIFE_ESTIMATES",
                f"Batch '{b.id}' life estimates must satisfy low ({low}) <= nominal ({b.remaining_life_nominal_min}) <= high ({high}).",
                "Order remaining_life_low_min <= remaining_life_nominal_min <= remaining_life_high_min.",
            )
        if b.status == BatchStatus.DEPLETED and b.quantity_units > 0:
            add_err(
                f"batches[{idx}].status",
                b.id,
                "DEPLETED_BATCH_NONZERO_QTY",
                f"Batch '{b.id}' is marked depleted but has quantity_units={b.quantity_units}.",
                "Set quantity_units=0 for depleted batches or use status='available'.",
            )

    # Validate destinations (Check 8)
    for idx, d in enumerate(pkg.destinations):
        if d.receiving_open_min < 0 or d.receiving_close_min <= d.receiving_open_min:
            add_err(
                f"destinations[{idx}].receiving_close_min",
                d.id,
                "UNORDERED_RECEIVING_WINDOW",
                f"Destination '{d.id}' receiving window [{d.receiving_open_min}, {d.receiving_close_min}] is invalid.",
                "Ensure 0 <= receiving_open_min < receiving_close_min.",
            )

    # Validate vehicles (Checks 1, 3, 8)
    for idx, v in enumerate(pkg.vehicles):
        if not v.unavailable and v.capacity_units <= 0:
            add_err(
                f"vehicles[{idx}].capacity_units",
                v.id,
                "NONPOSITIVE_VEHICLE_CAPACITY",
                f"Usable vehicle '{v.id}' must have capacity_units > 0 (got {v.capacity_units}).",
                "Set capacity_units to a positive integer.",
            )
        if v.available_from_min < 0 or v.available_to_min <= v.available_from_min:
            add_err(
                f"vehicles[{idx}].available_to_min",
                v.id,
                "UNORDERED_VEHICLE_SHIFT",
                f"Vehicle '{v.id}' shift [{v.available_from_min}, {v.available_to_min}] is invalid.",
                "Ensure 0 <= available_from_min < available_to_min.",
            )
        if v.compatible_commodity_ids is not None:
            for cid in v.compatible_commodity_ids:
                if cid not in comm_ids:
                    add_err(
                        f"vehicles[{idx}].compatible_commodity_ids",
                        v.id,
                        "UNRESOLVED_COMMODITY_REF",
                        f"Vehicle '{v.id}' references unknown commodity '{cid}'.",
                        "Use valid commodity IDs.",
                    )

    # Validate routes (Checks 1, 2, 12)
    for idx, r in enumerate(pkg.routes):
        if r.destination_id not in dest_ids:
            add_err(
                f"routes[{idx}].destination_id",
                r.id,
                "UNRESOLVED_DESTINATION_REF",
                f"Route '{r.id}' references unknown destination_id '{r.destination_id}'.",
                "Reference a valid destination ID.",
            )
        if r.outbound_travel_min <= 0 or r.return_travel_min <= 0:
            add_err(
                f"routes[{idx}].outbound_travel_min",
                r.id,
                "NONPOSITIVE_ROUTE_DURATION",
                f"Route '{r.id}' must have positive outbound and return travel durations.",
                "Set outbound_travel_min > 0 and return_travel_min > 0.",
            )
        if (
            r.loading_duration_min < 0
            or r.unloading_duration_min < 0
            or r.downstream_handling_duration_min < 0
            or r.turnaround_duration_min < 0
            or r.fixed_trip_cost_paise < 0
            or r.variable_transport_cost_paise_per_unit < 0
            or r.variable_handling_cost_paise_per_unit < 0
        ):
            add_err(
                f"routes[{idx}]",
                r.id,
                "NEGATIVE_ROUTE_PARAMETER",
                f"Route '{r.id}' has negative duration or cost parameters.",
                "Ensure all durations and costs are nonnegative integers.",
            )

    # Validate demand buckets (Checks 1, 6, 7, 9, 14)
    seen_dest_comm: set[Tuple[str, str]] = set()
    comm_map = {c.id: c for c in pkg.commodities}
    for idx, db in enumerate(pkg.demand_buckets):
        if db.destination_id not in dest_ids:
            add_err(
                f"demand_buckets[{idx}].destination_id",
                db.id,
                "UNRESOLVED_DESTINATION_REF",
                f"Demand bucket '{db.id}' references unknown destination '{db.destination_id}'.",
                "Reference a valid destination ID.",
            )
        if db.commodity_id not in comm_ids:
            add_err(
                f"demand_buckets[{idx}].commodity_id",
                db.id,
                "UNRESOLVED_COMMODITY_REF",
                f"Demand bucket '{db.id}' references unknown commodity '{db.commodity_id}'.",
                "Reference a valid commodity ID.",
            )
        pair = (db.destination_id, db.commodity_id)
        if pair in seen_dest_comm:
            add_err(
                f"demand_buckets[{idx}]",
                db.id,
                "DUPLICATE_DESTINATION_COMMODITY_BUCKET",
                f"Duplicate demand bucket for destination '{db.destination_id}' and commodity '{db.commodity_id}'.",
                "Provide at most one demand bucket per (destination_id, commodity_id) pair in Schema v1.0.",
            )
        seen_dest_comm.add(pair)

        if db.sale_at_min <= 0 or db.sale_at_min > pkg.evaluation_end_min:
            add_err(
                f"demand_buckets[{idx}].sale_at_min",
                db.id,
                "SALE_INSTANT_OUTSIDE_HORIZON",
                f"Bucket '{db.id}' sale_at_min ({db.sale_at_min}) must lie within (0, {pkg.evaluation_end_min}].",
                "Set sale_at_min within the evaluation cycle.",
            )
        if db.accepted_quantity_cap < 0:
            add_err(
                f"demand_buckets[{idx}].accepted_quantity_cap",
                db.id,
                "NEGATIVE_ACCEPTANCE_CAP",
                f"Bucket '{db.id}' accepted_quantity_cap ({db.accepted_quantity_cap}) cannot be negative.",
                "Set accepted_quantity_cap >= 0.",
            )
        if not db.price_tiers:
            add_err(
                f"demand_buckets[{idx}].price_tiers",
                db.id,
                "EMPTY_PRICE_TIERS",
                f"Bucket '{db.id}' has no configured price_tiers.",
                "Provide at least one allowed PriceTierSpec.",
            )

        sorted_tiers = sorted(db.price_tiers, key=lambda t: t.discount_pct)
        prev_price: int | None = None
        allowed_tiers = set(comm_map[db.commodity_id].allowed_price_tier_ids) if db.commodity_id in comm_map else set()
        for t_idx, tier in enumerate(sorted_tiers):
            if allowed_tiers and tier.tier_id not in allowed_tiers:
                add_err(
                    f"demand_buckets[{idx}].price_tiers[{t_idx}].tier_id",
                    db.id,
                    "DISALLOWED_PRICE_TIER",
                    f"Tier '{tier.tier_id}' is not in commodity '{db.commodity_id}' allowed_price_tier_ids.",
                    "Use only permitted tier IDs for this commodity.",
                )
            if tier.price_paise_per_unit < 0:
                add_err(
                    f"demand_buckets[{idx}].price_tiers[{t_idx}].price_paise_per_unit",
                    db.id,
                    "NEGATIVE_PRICE",
                    f"Tier '{tier.tier_id}' in bucket '{db.id}' has negative price.",
                    "Set price_paise_per_unit >= 0.",
                )
            if prev_price is not None and tier.price_paise_per_unit > prev_price:
                add_err(
                    f"demand_buckets[{idx}].price_tiers[{t_idx}].price_paise_per_unit",
                    db.id,
                    "DISCOUNT_INCREASES_PRICE",
                    f"Tier '{tier.tier_id}' ({tier.discount_pct}% discount) has higher price ({tier.price_paise_per_unit}) than a lower-discount tier ({prev_price}).",
                    "Ensure discount tiers never increase unit price.",
                )
            prev_price = tier.price_paise_per_unit
            if tier.forecast_sales_units < 0:
                add_err(
                    f"demand_buckets[{idx}].price_tiers[{t_idx}].forecast_sales_units",
                    db.id,
                    "NEGATIVE_FORECAST_SALES",
                    f"Tier '{tier.tier_id}' has negative forecast_sales_units.",
                    "Set forecast_sales_units >= 0.",
                )
            if tier.forecast_sales_units > db.accepted_quantity_cap:
                add_err(
                    f"demand_buckets[{idx}].price_tiers[{t_idx}].forecast_sales_units",
                    db.id,
                    "FORECAST_EXCEEDS_ACCEPTANCE_CAP",
                    f"Tier '{tier.tier_id}' forecast_sales_units ({tier.forecast_sales_units}) exceeds accepted_quantity_cap ({db.accepted_quantity_cap}).",
                    "Cap forecast_sales_units at accepted_quantity_cap.",
                )

    # Validate commitments if present
    committed_by_batch: Dict[str, int] = {}
    for idx, c in enumerate(pkg.commitments):
        if c.state == CommitmentState.PROPOSED:
            add_err(f"commitments[{idx}].state", c.id, "UNCOMMITTED_INITIAL_TRIP", "Initial commitments must be reserved or physically committed.", "Remove proposed trips from commitments.")
        if c.vehicle_id not in veh_ids:
            add_err(f"commitments[{idx}].vehicle_id", c.id, "UNRESOLVED_VEHICLE_REF", "Unknown vehicle_id", "Use valid vehicle_id.")
        if c.route_id not in route_ids:
            add_err(f"commitments[{idx}].route_id", c.id, "UNRESOLVED_ROUTE_REF", "Unknown route_id", "Use valid route_id.")
        if c.destination_id not in dest_ids:
            add_err(f"commitments[{idx}].destination_id", c.id, "UNRESOLVED_DESTINATION_REF", "Unknown destination_id", "Use valid destination_id.")
        for a_idx, alloc in enumerate(c.allocations):
            committed_by_batch[alloc.batch_id] = committed_by_batch.get(alloc.batch_id, 0) + alloc.quantity_units
            if alloc.quantity_units <= 0:
                add_err(f"commitments[{idx}].allocations[{a_idx}].quantity_units", c.id, "NONPOSITIVE_COMMITTED_QUANTITY", "Committed quantity must be positive.", "Set a positive quantity.")
            if alloc.batch_id not in batch_ids:
                add_err(
                    f"commitments[{idx}].allocations[{a_idx}].batch_id",
                    c.id,
                    "UNRESOLVED_BATCH_REF",
                    f"Commitment '{c.id}' references unknown batch '{alloc.batch_id}'.",
                    "Use a valid batch_id.",
                )

    for batch in pkg.batches:
        if committed_by_batch.get(batch.id, 0) > batch.quantity_units:
            add_err("commitments", batch.id, "COMMITMENTS_EXCEED_STOCK", "Initial commitments exceed available batch quantity.", "Reduce committed quantities or correct stock.")

    if errors:
        return None, errors
    return pkg, []
