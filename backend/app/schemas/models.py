"""Canonical data contracts and Pydantic schemas for FreshRoute: LastWindow."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel as _PydanticBaseModel, Extra, Field, root_validator


class BaseModel(_PydanticBaseModel):
    """Pydantic v1/v2 compatibility base model."""

    def model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)  # type: ignore[misc]
        return self.dict(*args, **kwargs)

    @classmethod
    def model_validate(cls, obj: Any) -> Any:
        if hasattr(super(), "model_validate"):
            return super().model_validate(obj)  # type: ignore[misc]
        return cls.parse_obj(obj)

    @classmethod
    def model_json_schema(cls) -> Dict[str, Any]:
        if hasattr(super(), "model_json_schema"):
            return super().model_json_schema()  # type: ignore[misc]
        return cls.schema()


class BatchStatus(str, Enum):
    AVAILABLE = "available"
    QUARANTINED = "quarantined"
    COMMITTED = "committed"
    DEPLETED = "depleted"


class CommitmentState(str, Enum):
    PROPOSED = "proposed"
    RESERVED = "reserved"
    LOADING = "loading"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    CLOSED = "closed"


class EventType(str, Enum):
    RESERVATION_RELEASED = "RESERVATION_RELEASED"
    RESERVATION_RESCHEDULED = "RESERVATION_RESCHEDULED"
    ROUTE_DELAY_OBSERVED = "ROUTE_DELAY_OBSERVED"
    VEHICLE_UNAVAILABLE = "VEHICLE_UNAVAILABLE"
    DEMAND_UPDATED = "DEMAND_UPDATED"
    BATCH_ESTIMATE_UPDATED = "BATCH_ESTIMATE_UPDATED"
    DESTINATION_CLOSED = "DESTINATION_CLOSED"
    PLAN_COMMITTED = "PLAN_COMMITTED"
    TRIP_LOADING_STARTED = "TRIP_LOADING_STARTED"
    TRIP_DISPATCHED = "TRIP_DISPATCHED"
    TRIP_DELIVERED = "TRIP_DELIVERED"
    INSPECTION_EXECUTED = "INSPECTION_EXECUTED"


class PolicyName(str, Enum):
    ORGANIZER_BASELINE = "organizer_baseline"
    FEFO_GREEDY = "fefo_greedy"
    NOMINAL_MIP = "nominal_mip"
    SCENARIO_SELECTED = "scenario_selected"
    INSPECTION_THEN_SCENARIO_SELECTED = "inspection_then_scenario_selected"


class SolverStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE_TIME_LIMITED = "FEASIBLE_TIME_LIMITED"
    INFEASIBLE = "INFEASIBLE"
    NO_INCUMBENT_TIMEOUT = "NO_INCUMBENT_TIMEOUT"
    SOLVER_ERROR = "SOLVER_ERROR"
    HEURISTIC_SELECTED = "HEURISTIC_SELECTED"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class StrictPlanningBase(BaseModel):
    """Base model that strictly forbids extra or hidden simulation truth fields."""
    class Config:
        extra = Extra.forbid


class Commodity(StrictPlanningBase):
    id: str
    name: str
    quantity_unit: str = "kg"
    allowed_price_tier_ids: List[str] = Field(default_factory=lambda: ["tier_0", "tier_10", "tier_20"])


class Batch(StrictPlanningBase):
    id: str
    commodity_id: str
    quantity_units: int
    observed_at_min: int = 0
    remaining_life_nominal_min: int
    remaining_life_low_min: Optional[int] = None
    remaining_life_high_min: Optional[int] = None
    status: BatchStatus = BatchStatus.AVAILABLE
    inspection_cost_paise: int = 5000
    inspection_duration_min: int = 15
    inspectable: bool = True

    @property
    def estimated_expiry_nominal_min(self) -> int:
        return self.observed_at_min + self.remaining_life_nominal_min

    @property
    def estimated_expiry_low_min(self) -> int:
        low = self.remaining_life_low_min if self.remaining_life_low_min is not None else self.remaining_life_nominal_min
        return self.observed_at_min + low

    @property
    def estimated_expiry_high_min(self) -> int:
        high = self.remaining_life_high_min if self.remaining_life_high_min is not None else self.remaining_life_nominal_min
        return self.observed_at_min + high


class Destination(StrictPlanningBase):
    id: str
    name: str
    receiving_open_min: int
    receiving_close_min: int
    x_coord: float = 0.0
    y_coord: float = 0.0
    closed: bool = False


class PriceTierSpec(StrictPlanningBase):
    tier_id: str
    label: str
    discount_pct: int
    price_paise_per_unit: int
    forecast_sales_units: int


class DemandBucket(StrictPlanningBase):
    id: str
    destination_id: str
    commodity_id: str
    sale_at_min: int
    accepted_quantity_cap: int
    freshness_buffer_min: int = 30
    price_tiers: List[PriceTierSpec]


class Vehicle(StrictPlanningBase):
    id: str
    name: str = ""
    capacity_units: int
    available_from_min: int = 0
    available_to_min: int = 720
    current_location: str = "ORIGIN_DC"
    compatible_commodity_ids: Optional[List[str]] = None
    unavailable: bool = False


class Route(StrictPlanningBase):
    id: str
    origin_id: str = "ORIGIN_DC"
    destination_id: str
    outbound_travel_min: int
    return_travel_min: int
    loading_duration_min: int = 15
    unloading_duration_min: int = 15
    downstream_handling_duration_min: int = 15
    turnaround_duration_min: int = 10
    fixed_trip_cost_paise: int
    variable_transport_cost_paise_per_unit: int = 0
    variable_handling_cost_paise_per_unit: int = 100
    observed_delay_min: int = 0


class CostModel(StrictPlanningBase):
    cancellation_cost_incurred_paise: int = Field(default=0, ge=0)
    included_categories: List[str] = Field(
        default_factory=lambda: [
            "fixed_trip_cost",
            "variable_transport_cost",
            "handling_cost",
            "spoilage_disposal_cost",
            "inspection_cost",
            "cancellation_cost",
        ]
    )
    disposal_cost_paise_per_unit_by_commodity: Dict[str, int] = Field(default_factory=dict)
    default_disposal_cost_paise_per_unit: int = 200
    handling_cost_paise_per_unit_by_commodity: Dict[str, int] = Field(default_factory=dict)
    default_handling_cost_paise_per_unit: int = 100
    terminal_carryover_value_paise_per_unit: int = 0
    cancellation_fee_paise_per_trip: int = 2500
    organizer_baseline_enabled: bool = True

    def get_disposal_cost(self, commodity_id: str) -> int:
        return self.disposal_cost_paise_per_unit_by_commodity.get(
            commodity_id, self.default_disposal_cost_paise_per_unit
        )

    def get_handling_cost(self, commodity_id: str) -> int:
        return self.handling_cost_paise_per_unit_by_commodity.get(
            commodity_id, self.default_handling_cost_paise_per_unit
        )


class ConfusionMatrixSpec(StrictPlanningBase):
    """Observation model P(observed_band | true_band) where rows sum to 1.0."""
    bands: List[str] = Field(default_factory=lambda: ["short", "medium", "long"])
    matrix: Dict[str, Dict[str, float]] = Field(
        default_factory=lambda: {
            "short": {"short": 0.80, "medium": 0.15, "long": 0.05},
            "medium": {"short": 0.15, "medium": 0.70, "long": 0.15},
            "long": {"short": 0.05, "medium": 0.15, "long": 0.80},
        }
    )


class UncertaintyModel(StrictPlanningBase):
    departure_grid_min: int = 15
    supplied_fixed_departures_min: List[int] = Field(default_factory=list)
    life_shock_min_range: List[int] = Field(default_factory=lambda: [-90, 60])
    travel_delay_min_range: List[int] = Field(default_factory=lambda: [0, 45])
    demand_shock_multiplier_range: List[float] = Field(default_factory=lambda: [0.75, 1.15])
    risk_lambda: float = 0.25
    planning_world_count: int = 20
    evaluation_world_count: int = 20
    observation_model: ConfusionMatrixSpec = Field(default_factory=ConfusionMatrixSpec)


class CommittedAllocation(StrictPlanningBase):
    batch_id: str
    commodity_id: str
    destination_id: str
    tier_id: str
    quantity_units: int


class Commitment(StrictPlanningBase):
    id: str
    plan_id: str
    trip_id: str
    vehicle_id: str
    route_id: str
    destination_id: str
    departure_min: int
    loading_start_min: int
    arrival_min: int
    unloading_end_min: int
    ready_for_sale_min: int
    vehicle_release_min: int
    state: CommitmentState = CommitmentState.RESERVED
    allocations: List[CommittedAllocation] = Field(default_factory=list)
    effective_at_min: int = 0
    incurred_fixed_cost_paise: int = 0
    incurred_variable_cost_paise: int = 0


class InspectionObservation(StrictPlanningBase):
    id: str
    batch_id: str
    observed_at_min: int
    observed_band: Literal["short", "medium", "long"]
    measurement_model_version: str = "confusion_v1"
    updated_remaining_life_nominal_min: int
    updated_remaining_life_low_min: int
    updated_remaining_life_high_min: int
    cost_paise: int
    duration_min: int


class OperationalEvent(StrictPlanningBase):
    id: str
    effective_at_min: int
    event_type: EventType
    entity_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: str = "operator"
    expected_prior_revision: Optional[int] = None


class ScenarioPackage(StrictPlanningBase):
    schema_version: str = "1.0"
    scenario_id: str
    name: str
    timezone: str = "Asia/Calcutta"
    start_at: str = "2026-10-15T06:00:00+05:30"
    decision_at_min: int = 0
    evaluation_end_min: int = 720
    quantity_unit: str = "kg"
    currency_minor_unit: str = "paise"
    provenance: str = "synthetic_demo"
    commodities: List[Commodity]
    batches: List[Batch]
    destinations: List[Destination]
    vehicles: List[Vehicle]
    routes: List[Route]
    demand_buckets: List[DemandBucket]
    cost_model: CostModel = Field(default_factory=CostModel)
    uncertainty_model: UncertaintyModel = Field(default_factory=UncertaintyModel)
    commitments: List[Commitment] = Field(default_factory=list)


class PlanningSnapshot(StrictPlanningBase):
    """Information available to the planner at decision_at_min.
    Strictly forbids hidden world fields (Test T24).
    """
    scenario_version_id: str
    state_revision: int
    decision_at_min: int
    evaluation_end_min: int
    scenario: ScenarioPackage
    warehouse_stock_by_batch: Dict[str, int]
    reserved_stock_by_batch: Dict[str, int]
    in_transit_stock_by_batch: Dict[str, int]
    locked_tier_by_bucket: Dict[str, str] = Field(default_factory=dict)
    residual_cap_by_bucket: Dict[str, int] = Field(default_factory=dict)
    sunk_cost_paise: int = 0
    inspection_history: List[InspectionObservation] = Field(default_factory=list)
    posterior_world_weights: Optional[Dict[str, float]] = None

    @root_validator(pre=True)
    def reject_hidden_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            forbidden = {
                "true_expiry_min",
                "true_expiry_by_batch",
                "realized_sales_demand_by_tier",
                "realized_demand_by_bucket_tier",
                "realized_outbound_travel_min",
                "realized_travel_by_route",
                "hidden_world",
                "simulation_world",
            }
            leaked = forbidden.intersection(set(values.keys()))
            if leaked:
                raise ValueError(f"HIDDEN_WORLD_LEAKAGE: Forbidden fields in PlanningSnapshot: {sorted(leaked)}")
        return values


class SimulationWorld(BaseModel):
    """Hidden simulation truth. Accessible ONLY to the replay/evaluation module."""
    world_id: str
    seed: int
    true_expiry_by_batch: Dict[str, int]
    realized_outbound_travel_by_route: Dict[str, int]
    realized_return_travel_by_route: Dict[str, int]
    realized_demand_by_bucket_tier: Dict[str, Dict[str, int]]
    demand_shock_by_bucket: Dict[str, float]
    true_life_band_by_batch: Dict[str, Literal["short", "medium", "long"]]


class CandidateTrip(StrictPlanningBase):
    id: str
    vehicle_id: str
    route_id: str
    destination_id: str
    departure_min: int
    loading_start_min: int
    arrival_min: int
    unloading_start_min: int
    unloading_end_min: int
    ready_for_sale_min: int
    vehicle_release_min: int
    capacity_units: int
    fixed_trip_cost_paise: int
    variable_transport_cost_paise_per_unit: int
    variable_handling_cost_paise_per_unit: int
    assumption_profile: str = "nominal"


class AllocationItem(StrictPlanningBase):
    batch_id: str
    commodity_id: str
    trip_id: str
    vehicle_id: str
    route_id: str
    destination_id: str
    bucket_id: str
    tier_id: str
    discount_pct: int
    unit_price_paise: int
    quantity_units: int
    departure_min: int
    ready_for_sale_min: int
    latest_feasible_departure_min: Optional[int] = None
    dispatch_slack_min: Optional[int] = None


class PlanTrip(StrictPlanningBase):
    trip_id: str
    vehicle_id: str
    route_id: str
    destination_id: str
    departure_min: int
    loading_start_min: int
    arrival_min: int
    unloading_start_min: int
    unloading_end_min: int
    ready_for_sale_min: int
    vehicle_release_min: int
    total_loaded_units: int
    capacity_units: int
    fixed_trip_cost_paise: int
    state: CommitmentState = CommitmentState.PROPOSED
    allocations: List[AllocationItem] = Field(default_factory=list)


class EconomicBreakdown(StrictPlanningBase):
    is_forecast: bool
    gross_revenue_paise: int
    fixed_trip_cost_paise: int
    variable_transport_cost_paise: int
    handling_cost_paise: int
    spoilage_disposal_cost_paise: int
    inspection_cost_paise: int = 0
    cancellation_cost_paise: int = 0
    sunk_committed_cost_paise: int = 0
    net_outcome_paise: int


class ViolationItem(StrictPlanningBase):
    code: str
    message: str
    entity_id: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ValidationReport(StrictPlanningBase):
    is_valid: bool
    checked_under_assumptions: str = "nominal_planning_snapshot"
    violations: List[ViolationItem] = Field(default_factory=list)
    warnings: List[ViolationItem] = Field(default_factory=list)


class BatchDeadlineInfo(StrictPlanningBase):
    batch_id: str
    destination_id: str
    is_eligible: bool
    analytical_latest_departure_min: Optional[int] = None
    scheduled_latest_departure_min: Optional[int] = None
    planned_departure_min: Optional[int] = None
    dispatch_slack_min: Optional[int] = None
    rejection_reason: Optional[str] = None


class DecisionReceipt(StrictPlanningBase):
    batch_id: str
    commodity_id: str
    available_quantity_units: int
    allocated_quantity_units: int
    unallocated_quantity_units: int
    estimated_expiry_min: int
    chosen_allocations: List[AllocationItem]
    deadlines_by_destination: List[BatchDeadlineInfo]
    binding_constraints: List[str]
    tier_comparison_summary: List[Dict[str, Any]]
    explanation_text: str
    model_version: str = "lastwindow_mip_v1.0"


class AlternativeComparison(StrictPlanningBase):
    plan_id: str
    batch_id: str
    alternative_destination_id: str
    forced_quantity_units: int
    is_feasible: bool
    infeasibility_reason: Optional[str] = None
    base_forecast_net_paise: int
    alternative_forecast_net_paise: Optional[int] = None
    net_delta_paise: Optional[int] = None
    affected_trips: List[str] = Field(default_factory=list)


class PlanResult(StrictPlanningBase):
    plan_id: str
    scenario_version_id: str
    state_revision: int
    policy: PolicyName
    config_id: str
    assumption_profile: str = "nominal"
    solver_status: SolverStatus
    solve_time_ms: float
    optimality_gap_pct: Optional[float] = None
    trips: List[PlanTrip]
    allocations: List[AllocationItem]
    unallocated_by_batch: Dict[str, int]
    selected_tier_by_bucket: Dict[str, str]
    forecast_economics: EconomicBreakdown
    validation: ValidationReport
    deadlines: List[BatchDeadlineInfo] = Field(default_factory=list)
    candidate_diagnostics: Dict[str, Any] = Field(default_factory=dict)
    uncertainty_summary: Optional[Dict[str, Any]] = None


class BatchLedgerReconciliation(BaseModel):
    batch_id: str
    commodity_id: str
    initial_quantity_units: int
    dispatched_units: int
    sold_units: int
    expired_unsold_origin_units: int
    expired_unsold_destination_units: int
    expired_unsold_total_units: int
    unexpired_carryover_origin_units: int
    unexpired_carryover_destination_units: int
    unexpired_carryover_total_units: int
    reconciled: bool


class ReplayResult(BaseModel):
    world_id: str
    plan_id: str
    policy: PolicyName
    initial_total_units: int
    dispatched_total_units: int
    sold_total_units: int
    expired_unsold_total_units: int
    unexpired_carryover_total_units: int
    spoilage_rate: Optional[float]
    realized_tier_demand_total_units: int
    reference_full_price_demand_total_units: int
    demand_fulfillment_rate: Optional[float]
    reference_fulfillment_rate: Optional[float]
    unmet_demand_units: int
    realized_service_failures: int
    failure_events: List[Dict[str, Any]] = Field(default_factory=list)
    realized_economics: EconomicBreakdown
    batch_ledgers: List[BatchLedgerReconciliation]
    ledger_reconciled: bool


class InspectionCandidateRank(StrictPlanningBase):
    batch_id: str
    commodity_id: str
    quantity_units: int
    uncertainty_spread_min: int
    inspection_cost_paise: int
    inspection_duration_min: int
    no_inspection_expected_net_paise: int
    expected_net_with_inspection_paise: int
    estimated_inspection_value_paise: int
    worthwhile: bool
    decision_change_probability: float
    branch_outcomes: Dict[str, Dict[str, Any]]
    recommendation_reason: str
