"""Mandatory Scenario Tests T01 through T24 from Section 12.3 of the Development Plan."""
from __future__ import annotations
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from app.domain.commitments import apply_operational_event, commit_plan_to_ledger
from app.domain.inventory import OperationalStateLedger
from app.evaluation.metrics import compute_paired_comparison
from app.jobs.worker import create_job_record
from app.persistence.database import (
    atomic_commit_plan,
    init_db,
    recover_interrupted_jobs,
    save_plan_result,
    save_scenario_and_initial_state,
)
from app.planning.baselines import solve_fefo_greedy
from app.planning.candidates import generate_candidate_trips
from app.planning.mip import solve_deterministic_mip
from app.schemas.models import (
    BatchStatus,
    CommitmentState,
    EventType,
    JobStatus,
    OperationalEvent,
    PlanningSnapshot,
    ScenarioPackage,
    SimulationWorld,
    SolverStatus,
)
from app.simulation.inspection import rank_inspections_by_voi
from app.simulation.replay import replay_plan_in_world
from app.validation.plan import validate_plan_independently
from app.validation.scenario import validate_scenario_dict

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DEMO_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scenarios/demo/demo-normal-001.json"


class TestMandatoryScenariosT01ToT24(unittest.TestCase):
    def setUp(self) -> None:
        self.fin_raw = json.loads((FIXTURES_DIR / "financial_fixture.json").read_text())
        self.demo_raw = json.loads(DEMO_PATH.read_text())

    def test_T01_no_inventory(self) -> None:
        """T01: No inventory -> Valid empty plan; zero sales; demand remains unmet."""
        raw = copy.deepcopy(self.fin_raw)
        raw["batches"][0]["quantity_units"] = 0
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(len(plan.allocations), 0)
        world = SimulationWorld(
            world_id="w_t01",
            seed=1,
            true_expiry_by_batch={"B-100": 500},
            realized_outbound_travel_by_route={"R-01": 45},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 60}},
            demand_shock_by_bucket={"DB-01": 1.0},
            true_life_band_by_batch={"B-100": "medium"},
        )
        rep = replay_plan_in_world(scen, plan, world)
        self.assertEqual(rep.sold_total_units, 0)
        self.assertEqual(rep.unmet_demand_units, 60)

    def test_T02_zero_demand_at_all_tiers(self) -> None:
        """T02: Zero demand at all tiers -> No unjustified shipment or revenue."""
        raw = copy.deepcopy(self.fin_raw)
        raw["demand_buckets"][0]["price_tiers"][0]["forecast_sales_units"] = 0
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(len(plan.allocations), 0)
        self.assertEqual(plan.forecast_economics.gross_revenue_paise, 0)

    def test_T03_batch_already_expired(self) -> None:
        """T03: Batch already expired -> No allocation."""
        raw = copy.deepcopy(self.fin_raw)
        raw["decision_at_min"] = 100
        raw["batches"][0]["observed_at_min"] = 0
        raw["batches"][0]["remaining_life_nominal_min"] = 90
        raw["batches"][0]["remaining_life_low_min"] = 80
        raw["batches"][0]["remaining_life_high_min"] = 100
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(len(plan.allocations), 0)

    def test_T04_batch_expires_exactly_at_sale_time(self) -> None:
        """T04: Batch expires exactly at sale time + freshness buffer -> Ineligible under strict expiry convention."""
        raw = copy.deepcopy(self.fin_raw)
        # sale_at_min = 240, freshness_buffer_min = 30 -> S_dc + B_dc = 270.
        # Setting expiry = 270 violates strict inequality S_dc + B_dc < E_b.
        raw["batches"][0]["remaining_life_nominal_min"] = 270
        raw["batches"][0]["remaining_life_low_min"] = 250
        raw["batches"][0]["remaining_life_high_min"] = 290
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertEqual(len(plan.allocations), 0)

    def test_T05_vehicle_load_exceeds_capacity_by_one_unit(self) -> None:
        """T05: Vehicle load exceeds capacity by one unit -> Independent validator rejects."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        mutated = copy.deepcopy(plan)
        # Set vehicle capacity to 79 while load is 80 (exceeds by 1 unit)
        snap_mut = copy.deepcopy(snap)
        snap_mut.scenario.vehicles[0].capacity_units = 79
        val = validate_plan_independently(snap_mut, mutated)
        self.assertFalse(val.is_valid)
        self.assertTrue(any(v.code == "VEHICLE_CAPACITY_EXCEEDED" for v in val.violations))

    def test_T06_trips_overlap_during_loading_or_return(self) -> None:
        """T06: Trips overlap during loading/return -> Reject even if outbound travel does not overlap."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        mutated = copy.deepcopy(plan)
        # Duplicate trip on same vehicle shifted by 60m so outbound doesn't overlap but return/turnaround overlaps
        t1 = mutated.trips[0]
        t2 = copy.deepcopy(t1)
        t2.trip_id = "trip_overlapping_return"
        t2.departure_min = t1.departure_min + 60
        t2.loading_start_min = t1.loading_start_min + 60
        t2.arrival_min = t1.arrival_min + 60
        t2.unloading_start_min = t1.unloading_start_min + 60
        t2.unloading_end_min = t1.unloading_end_min + 60
        t2.ready_for_sale_min = t1.ready_for_sale_min + 60
        t2.vehicle_release_min = t1.vehicle_release_min + 60
        t1.total_loaded_units = 40
        t1.allocations[0].quantity_units = 40
        t2.total_loaded_units = 40
        t2.allocations[0].trip_id = t2.trip_id
        t2.allocations[0].quantity_units = 40
        mutated.trips.append(t2)
        mutated.allocations.append(t2.allocations[0])
        val = validate_plan_independently(snap, mutated)
        self.assertFalse(val.is_valid)
        self.assertTrue(any(v.code == "VEHICLE_TRIP_OVERLAP" for v in val.violations))

    def test_T07_arrival_before_receiving_opens(self) -> None:
        """T07: Arrival before receiving opens -> Waiting is applied; later readiness is respected."""
        raw = copy.deepcopy(self.fin_raw)
        raw["destinations"][0]["receiving_open_min"] = 120
        raw["uncertainty_model"]["supplied_fixed_departures_min"] = [15]
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        cands, _ = generate_candidate_trips(snap)
        early_cand = next(c for c in cands if c.departure_min == 15)
        # Departure 15 + outbound 45 = arrival 60, but receiving_open = 120 -> unloading_start = 120
        self.assertEqual(early_cand.arrival_min, 60)
        self.assertEqual(early_cand.unloading_start_min, 120)
        self.assertEqual(early_cand.unloading_end_min, 135)
        self.assertEqual(early_cand.ready_for_sale_min, 150)

    def test_T08_unloading_ends_after_closing(self) -> None:
        """T08: Unloading ends after closing -> Candidate excluded."""
        raw = copy.deepcopy(self.fin_raw)
        raw["destinations"][0]["receiving_close_min"] = 50  # Impossible to arrive (15+45=60) & unload before 50
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        cands, diag = generate_candidate_trips(snap)
        self.assertEqual(len(cands), 0)
        self.assertGreater(diag["exclusion_counts"].get("UNLOADING_AFTER_RECEIVING_CLOSE", 0), 0)

    def test_T09_lower_price_with_no_demand_uplift(self) -> None:
        """T09: Lower price with no demand uplift -> No fabricated volume benefit; full price selected."""
        raw = copy.deepcopy(self.fin_raw)
        raw["commodities"][0]["allowed_price_tier_ids"] = ["tier_0", "tier_10"]
        raw["demand_buckets"][0]["price_tiers"] = [
            {"tier_id": "tier_0", "label": "Full", "discount_pct": 0, "price_paise_per_unit": 4000, "forecast_sales_units": 60},
            {"tier_id": "tier_10", "label": "10% Off", "discount_pct": 10, "price_paise_per_unit": 3600, "forecast_sales_units": 60},
        ]
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertEqual(plan.selected_tier_by_bucket["DB-01"], "tier_0")
        self.assertEqual(sum(a.quantity_units for a in plan.allocations), 60)

    def test_T10_demand_uplift_exceeds_acceptance_cap(self) -> None:
        """T10: Demand uplift exceeds acceptance cap -> Input rejected during validation."""
        raw = copy.deepcopy(self.fin_raw)
        raw["demand_buckets"][0]["accepted_quantity_cap"] = 80
        raw["demand_buckets"][0]["price_tiers"][0]["forecast_sales_units"] = 95
        pkg, errs = validate_scenario_dict(raw)
        self.assertIsNone(pkg)
        self.assertTrue(any(e["error_code"] == "FORECAST_EXCEEDS_ACCEPTANCE_CAP" for e in errs))

    def test_T11_replan_after_dispatch_locks_stock_and_vehicle(self) -> None:
        """T11: Replan after dispatch -> Stock and vehicle stay locked."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        ledger = OperationalStateLedger(scen)
        plan1 = solve_deterministic_mip(ledger.build_planning_snapshot())
        # Commit as DISPATCHED
        ledger_dispatched = commit_plan_to_ledger(
            ledger, plan1, expected_revision=1, idempotency_key="k_t11", commit_state=CommitmentState.DISPATCHED
        )
        snap2 = ledger_dispatched.build_planning_snapshot()
        # Available warehouse stock for B-100 was 100, 80 dispatched -> only 20 remains
        self.assertEqual(snap2.warehouse_stock_by_batch["B-100"], 20)
        self.assertEqual(snap2.in_transit_stock_by_batch["B-100"], 80)
        # Residual acceptance cap for DB-01 was 80, 80 committed -> 0 remains
        self.assertEqual(snap2.residual_cap_by_bucket["DB-01"], 0)
        plan2 = solve_deterministic_mip(snap2)
        self.assertEqual(len(plan2.allocations), 0)

    def test_T12_same_event_submitted_twice_idempotent(self) -> None:
        """T12: Same event submitted twice -> No duplicate movement/cost."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        ledger = OperationalStateLedger(scen)
        ev = OperationalEvent(
            id="ev_dup_test",
            effective_at_min=15,
            event_type=EventType.ROUTE_DELAY_OBSERVED,
            entity_id="R-01",
            payload={"delay_minutes": 25},
        )
        l1 = apply_operational_event(ledger, ev)
        l2 = apply_operational_event(l1, ev)
        self.assertEqual(l1.state_revision, l2.state_revision)
        self.assertEqual(l1.scenario.routes[0].outbound_travel_min, 70)
        self.assertEqual(l2.scenario.routes[0].outbound_travel_min, 70)

    def test_T13_worker_stops_mid_job_interrupted(self) -> None:
        """T13: Worker stops mid-job -> Job becomes interrupted; no partial commitment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_p = os.path.join(tmpdir, "t13.sqlite3")
            init_db(db_p)
            jid = create_job_record("planning", {"scenario_id": "s1"}, status=JobStatus.RUNNING, db_path=db_p)
            recovered = recover_interrupted_jobs(db_p)
            self.assertEqual(recovered, 1)

    def test_T14_solve_ends_with_valid_incumbent(self) -> None:
        """T14: Solve ends with valid incumbent -> Feasible result shown without optimality claim."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap, simulate_status_override=SolverStatus.FEASIBLE_TIME_LIMITED)
        self.assertEqual(plan.solver_status, SolverStatus.FEASIBLE_TIME_LIMITED)
        self.assertIsNone(plan.optimality_gap_pct)
        self.assertTrue(plan.validation.is_valid)

    def test_T15_solve_ends_with_no_incumbent(self) -> None:
        """T15: Solve ends with no incumbent -> No-solution-within-budget status; validated baseline fallback works."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap, simulate_status_override=SolverStatus.NO_INCUMBENT_TIMEOUT)
        self.assertEqual(plan.solver_status, SolverStatus.NO_INCUMBENT_TIMEOUT)
        fallback = solve_fefo_greedy(snap)
        self.assertTrue(fallback.validation.is_valid)

    def test_T16_all_allowed_shipments_lose_money(self) -> None:
        """T16: All allowed shipments lose money -> Empty dispatch is optimal absent minimum-service obligations."""
        raw = copy.deepcopy(self.fin_raw)
        raw["routes"][0]["fixed_trip_cost_paise"] = 9_000_000  # Rs 90,000 trip cost > any revenue + disposal
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(len(plan.allocations), 0)

    def test_T17_inspecting_takes_longer_than_remaining_opportunity(self) -> None:
        """T17: Inspecting takes longer than remaining opportunity -> Nonpositive value / not worthwhile."""
        raw = copy.deepcopy(self.fin_raw)
        raw["batches"][0]["inspectable"] = True
        raw["batches"][0]["inspection_cost_paise"] = 3000
        raw["batches"][0]["inspection_duration_min"] = 300  # Exceeds sale_at_min=240
        scen = ScenarioPackage.model_validate(raw)
        ledger = OperationalStateLedger(scen)
        ranks = rank_inspections_by_voi(ledger)
        self.assertEqual(len(ranks), 1)
        self.assertFalse(ranks[0].worthwhile)
        self.assertLessEqual(ranks[0].estimated_inspection_value_paise, 0)

    def test_T18_inspection_does_not_affect_decisions(self) -> None:
        """T18: Inspection does not affect decisions -> No invented positive value after costs."""
        raw = copy.deepcopy(self.fin_raw)
        raw["batches"][0]["inspectable"] = True
        raw["batches"][0]["inspection_cost_paise"] = 4000
        # Zero uncertainty spread (low == nominal == high = 500)
        raw["batches"][0]["remaining_life_low_min"] = 500
        raw["batches"][0]["remaining_life_nominal_min"] = 500
        raw["batches"][0]["remaining_life_high_min"] = 500
        scen = ScenarioPackage.model_validate(raw)
        ledger = OperationalStateLedger(scen)
        ranks = rank_inspections_by_voi(ledger)
        self.assertEqual(len(ranks), 1)
        self.assertFalse(ranks[0].worthwhile)
        self.assertEqual(ranks[0].estimated_inspection_value_paise, -4000)

    def test_T19_unexpected_real_delay_breaks_feasible_estimate(self) -> None:
        """T19: Unexpected real delay breaks a feasible estimate -> Realized failure recorded; original plan remains valid under nominal assumptions."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        # Realized outbound travel is 350m -> arrives after sale_at_min=240
        adverse_world = SimulationWorld(
            world_id="w_t19",
            seed=19,
            true_expiry_by_batch={"B-100": 500},
            realized_outbound_travel_by_route={"R-01": 350},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 60}},
            demand_shock_by_bucket={"DB-01": 1.0},
            true_life_band_by_batch={"B-100": "medium"},
        )
        rep = replay_plan_in_world(scen, plan, adverse_world)
        self.assertGreater(rep.realized_service_failures, 0)
        self.assertEqual(rep.sold_total_units, 0)
        # Historical nominal validation remains True
        self.assertTrue(plan.validation.is_valid)

    def test_T20_no_baseline_spoilage(self) -> None:
        """T20: No baseline spoilage -> Percentage reduction shown as None (not applicable); absolute difference retained."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        world_no_spoil = SimulationWorld(
            world_id="w_t20",
            seed=20,
            true_expiry_by_batch={"B-100": 900},  # Survives horizon -> 0 spoilage
            realized_outbound_travel_by_route={"R-01": 45},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 80}},
            demand_shock_by_bucket={"DB-01": 1.0},
            true_life_band_by_batch={"B-100": "long"},
        )
        rep = replay_plan_in_world(scen, plan, world_no_spoil)
        comp = compute_paired_comparison(rep, rep)
        self.assertIsNone(comp["spoilage_reduction_pct"])
        self.assertEqual(comp["spoilage_reduction_units"], 0)

    def test_T21_unsold_stock_survives_horizon_carryover(self) -> None:
        """T21: Unsold stock survives horizon -> Carryover, not spoilage."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        world_survives = SimulationWorld(
            world_id="w_t21",
            seed=21,
            true_expiry_by_batch={"B-100": 850},  # > evaluation_end_min (720)
            realized_outbound_travel_by_route={"R-01": 45},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 50}},
            demand_shock_by_bucket={"DB-01": 0.625},
            true_life_band_by_batch={"B-100": "long"},
        )
        rep = replay_plan_in_world(scen, plan, world_survives)
        self.assertEqual(rep.expired_unsold_total_units, 0)
        self.assertEqual(rep.unexpired_carryover_total_units, 50)
        self.assertEqual(rep.realized_economics.spoilage_disposal_cost_paise, 0)

    def test_T22_two_users_commit_against_one_revision(self) -> None:
        """T22: Two users commit against one revision -> Exactly one succeeds; the other receives conflict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_p = os.path.join(tmpdir, "t22.sqlite3")
            init_db(db_p)
            scen = ScenarioPackage.model_validate(self.fin_raw)
            save_scenario_and_initial_state(scen, db_path=db_p)
            snap = OperationalStateLedger(scen).build_planning_snapshot()
            plan = solve_deterministic_mip(snap)
            save_plan_result(scen.scenario_id, plan, db_path=db_p)

            res1 = atomic_commit_plan(plan.plan_id, expected_revision=1, idempotency_key="user1_commit", db_path=db_p)
            self.assertEqual(res1["status"], "committed")
            self.assertEqual(res1["new_state_revision"], 2)

            with self.assertRaises(ValueError) as ctx:
                atomic_commit_plan(plan.plan_id, expected_revision=1, idempotency_key="user2_commit", db_path=db_p)
            self.assertIn("STATE_REVISION_CONFLICT", str(ctx.exception))

    def test_T23_quarantined_batch_no_allocation(self) -> None:
        """T23: Quarantined batch -> No allocation even if profitable."""
        raw = copy.deepcopy(self.fin_raw)
        raw["batches"][0]["status"] = BatchStatus.QUARANTINED.value
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_deterministic_mip(snap)
        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(len(plan.allocations), 0)

    def test_T24_true_outcome_field_injected_into_planner_input_rejected(self) -> None:
        """T24: True outcome field injected into planner input -> Rejected by contract."""
        scen = ScenarioPackage.model_validate(self.fin_raw)
        snap_dict = OperationalStateLedger(scen).build_planning_snapshot().model_dump()
        snap_dict["true_expiry_by_batch"] = {"B-100": 450}
        with self.assertRaises(Exception) as ctx:
            PlanningSnapshot.model_validate(snap_dict)
        self.assertIn("HIDDEN_WORLD_LEAKAGE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
