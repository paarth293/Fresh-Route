"""Inventory ledger and operational state representation (Section 4 & 8.3)."""
from __future__ import annotations
import copy
from typing import Dict, List
from app.schemas.models import (
    BatchStatus,
    Commitment,
    CommitmentState,
    InspectionObservation,
    OperationalEvent,
    PlanningSnapshot,
    ScenarioPackage,
)


class OperationalStateLedger:
    """Tracks physical stock across warehouse, reserved, loading, in-transit, and destination buckets."""

    def __init__(self, scenario: ScenarioPackage, scenario_version_id: str = "sv_initial"):
        self.scenario_version_id = scenario_version_id
        self.scenario: ScenarioPackage = copy.deepcopy(scenario)
        self.state_revision: int = 1
        self.current_time_min: int = scenario.decision_at_min
        self.applied_event_ids: set[str] = set()
        self.event_log: List[OperationalEvent] = []
        self.inspection_history: List[InspectionObservation] = []
        self.posterior_world_weights: Dict[str, float] | None = None

        # Non-overlapping stock buckets per batch_id
        self.initial_stock: Dict[str, int] = {b.id: b.quantity_units for b in self.scenario.batches}
        self.warehouse_unreserved: Dict[str, int] = {b.id: b.quantity_units for b in self.scenario.batches}
        self.reserved_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}
        self.loading_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}
        self.in_transit_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}
        self.destination_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}
        self.sold_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}
        self.expired_stock: Dict[str, int] = {b.id: 0 for b in self.scenario.batches}

        self.commitments: Dict[str, Commitment] = {}
        self.locked_tier_by_bucket: Dict[str, str] = {}
        self.sunk_cost_paise: int = 0

        # Apply any initial commitments embedded in the scenario
        for comm in self.scenario.commitments:
            self._register_initial_commitment(comm)

    def _register_initial_commitment(self, comm: Commitment) -> None:
        if comm.state == CommitmentState.PROPOSED:
            raise ValueError("Initial commitments must be reserved or physically committed.")
        self.commitments[comm.id] = copy.deepcopy(comm)
        if comm.state in (CommitmentState.LOADING, CommitmentState.DISPATCHED, CommitmentState.DELIVERED, CommitmentState.CLOSED):
            self.sunk_cost_paise += comm.incurred_fixed_cost_paise + comm.incurred_variable_cost_paise
        bucket_map = {(db.destination_id, db.commodity_id): db.id for db in self.scenario.demand_buckets}
        for alloc in comm.allocations:
            qty = alloc.quantity_units
            if self.warehouse_unreserved.get(alloc.batch_id, 0) < qty:
                raise ValueError(f"Initial commitment '{comm.id}' exceeds batch '{alloc.batch_id}' stock.")
            self.warehouse_unreserved[alloc.batch_id] -= qty
            if comm.state == CommitmentState.RESERVED:
                self.reserved_stock[alloc.batch_id] += qty
            elif comm.state == CommitmentState.LOADING:
                self.loading_stock[alloc.batch_id] += qty
            elif comm.state == CommitmentState.DISPATCHED:
                self.in_transit_stock[alloc.batch_id] += qty
            elif comm.state in (CommitmentState.DELIVERED, CommitmentState.CLOSED):
                self.destination_stock[alloc.batch_id] += qty

            b_id = bucket_map.get((alloc.destination_id, alloc.commodity_id))
            if b_id:
                self.locked_tier_by_bucket[b_id] = alloc.tier_id

    def verify_conservation(self) -> bool:
        """Verify that for every batch, sum of non-overlapping buckets == initial_quantity."""
        for b_id, init_q in self.initial_stock.items():
            total = (
                self.warehouse_unreserved[b_id]
                + self.reserved_stock[b_id]
                + self.loading_stock[b_id]
                + self.in_transit_stock[b_id]
                + self.destination_stock[b_id]
                + self.sold_stock[b_id]
                + self.expired_stock[b_id]
            )
            if total != init_q:
                return False
            if any(
                val < 0
                for val in (
                    self.warehouse_unreserved[b_id],
                    self.reserved_stock[b_id],
                    self.loading_stock[b_id],
                    self.in_transit_stock[b_id],
                    self.destination_stock[b_id],
                    self.sold_stock[b_id],
                    self.expired_stock[b_id],
                )
            ):
                return False
        return True

    def build_planning_snapshot(self) -> PlanningSnapshot:
        """Build an immutable PlanningSnapshot of observed state for residual optimization."""
        residual_cap: Dict[str, int] = {}
        bucket_map = {(db.destination_id, db.commodity_id): db for db in self.scenario.demand_buckets}
        for db in self.scenario.demand_buckets:
            residual_cap[db.id] = db.accepted_quantity_cap

        # Subtract locked commitments (LOADING, DISPATCHED, DELIVERED, CLOSED, or active RESERVED)
        # Note: During replanning, RESERVED commitments that haven't started loading can be released if configured,
        # or kept locked if committed. By Section 8.6, loading/dispatched/delivered are strictly fixed,
        # and reserved commitments lock stock unless explicitly cancelled. Here committed trips lock stock/cap.
        for comm in self.commitments.values():
            if comm.state in (
                CommitmentState.RESERVED,
                CommitmentState.LOADING,
                CommitmentState.DISPATCHED,
                CommitmentState.DELIVERED,
                CommitmentState.CLOSED,
            ):
                for alloc in comm.allocations:
                    db = bucket_map.get((alloc.destination_id, alloc.commodity_id))
                    if db:
                        residual_cap[db.id] = max(0, residual_cap[db.id] - alloc.quantity_units)

        snap_scenario = copy.deepcopy(self.scenario)
        snap_scenario.decision_at_min = self.current_time_min
        snap_scenario.commitments = list(self.commitments.values())

        return PlanningSnapshot(
            scenario_version_id=self.scenario_version_id,
            state_revision=self.state_revision,
            decision_at_min=self.current_time_min,
            evaluation_end_min=self.scenario.evaluation_end_min,
            scenario=snap_scenario,
            warehouse_stock_by_batch=dict(self.warehouse_unreserved),
            reserved_stock_by_batch=dict(self.reserved_stock),
            in_transit_stock_by_batch=dict(self.in_transit_stock),
            locked_tier_by_bucket=dict(self.locked_tier_by_bucket),
            residual_cap_by_bucket=residual_cap,
            sunk_cost_paise=self.sunk_cost_paise,
            inspection_history=list(self.inspection_history),
            posterior_world_weights=dict(self.posterior_world_weights) if self.posterior_world_weights else None,
        )
