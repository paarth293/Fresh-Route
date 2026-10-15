"""Unit tests for integer units, expiry boundaries, and Section 12.2 Hand-Calculated Financial Fixture."""
from __future__ import annotations
import json
import unittest
from pathlib import Path
from app.domain.inventory import OperationalStateLedger
from app.domain.units import (
    compute_absolute_expiry_min,
    compute_remaining_life_at_min,
    is_batch_fresh_for_sale,
    paise_to_rupees_str,
    rupees_to_paise,
)
from app.planning.mip import solve_deterministic_mip, solve_exhaustive_oracle
from app.schemas.models import ScenarioPackage, SimulationWorld
from app.simulation.replay import replay_plan_in_world

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class TestUnitsAndEconomics(unittest.TestCase):
    def test_decimal_paise_conversion_and_expiry_math(self) -> None:
        self.assertEqual(rupees_to_paise("40"), 4000)
        self.assertEqual(rupees_to_paise("19.99"), 1999)
        self.assertEqual(paise_to_rupees_str(204000), "Rs 2,040.00")

        exp = compute_absolute_expiry_min(observed_at_min=15, remaining_life_min=300)
        self.assertEqual(exp, 315)
        self.assertEqual(compute_remaining_life_at_min(exp, current_time_min=115), 200)

        # Strict inequality: S_dc (180) + B_dc (30) = 210 < E_b
        self.assertFalse(is_batch_fresh_for_sale(180, 30, 210))
        self.assertTrue(is_batch_fresh_for_sale(180, 30, 211))

    def test_section_12_2_hand_calculated_financial_fixture(self) -> None:
        """Verify exact numbers from Section 12.2:
        Batch: 100 kg; true expiry before evaluation end.
        Accepted allocation cap: 80 kg. Dispatch: 80 kg. Realized sales demand: 60 kg.
        Selling price: Rs 40/kg (4,000 paise/kg).
        One dispatched trip: Rs 200 (20,000 paise).
        Handling: Rs 1/kg (100 paise/kg).
        Disposal: Rs 2/kg (200 paise/kg).
        Expected:
          Sold = 60 kg
          Expired unsold = 40 kg (20 at destination + 20 at origin)
          Carryover = 0 kg
          Revenue = Rs 2,400 (240,000 paise)
          Trip cost = Rs 200 (20,000 paise)
          Handling = Rs 80 (8,000 paise)
          Disposal = Rs 80 (8,000 paise)
          Net = Rs 2,040 (204,000 paise)
        """
        raw = json.loads((FIXTURES_DIR / "financial_fixture.json").read_text())
        scen = ScenarioPackage.model_validate(raw)
        ledger = OperationalStateLedger(scen)
        snap = ledger.build_planning_snapshot()
        plan = solve_deterministic_mip(snap)

        self.assertTrue(plan.validation.is_valid)
        self.assertEqual(sum(a.quantity_units for a in plan.allocations), 80)

        world_expired = SimulationWorld(
            world_id="w_sec12_2_expired",
            seed=1,
            true_expiry_by_batch={"B-100": 500},  # <= H (720)
            realized_outbound_travel_by_route={"R-01": 45},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 60}},
            demand_shock_by_bucket={"DB-01": 0.75},
            true_life_band_by_batch={"B-100": "medium"},
        )
        rep = replay_plan_in_world(scen, plan, world_expired)
        self.assertTrue(rep.ledger_reconciled)
        self.assertEqual(rep.sold_total_units, 60)
        self.assertEqual(rep.expired_unsold_total_units, 40)
        self.assertEqual(rep.batch_ledgers[0].expired_unsold_destination_units, 20)
        self.assertEqual(rep.batch_ledgers[0].expired_unsold_origin_units, 20)
        self.assertEqual(rep.unexpired_carryover_total_units, 0)

        econ = rep.realized_economics
        self.assertEqual(econ.gross_revenue_paise, 240_000)
        self.assertEqual(econ.fixed_trip_cost_paise, 20_000)
        self.assertEqual(econ.handling_cost_paise, 8_000)
        self.assertEqual(econ.spoilage_disposal_cost_paise, 8_000)
        self.assertEqual(econ.net_outcome_paise, 204_000)

        # Second part of Section 12.2: true expiry after horizon (800 > 720) -> 40 kg carryover, 0 disposal
        world_carryover = SimulationWorld(
            world_id="w_sec12_2_carryover",
            seed=2,
            true_expiry_by_batch={"B-100": 800},  # > H (720)
            realized_outbound_travel_by_route={"R-01": 45},
            realized_return_travel_by_route={"R-01": 45},
            realized_demand_by_bucket_tier={"DB-01": {"tier_0": 60}},
            demand_shock_by_bucket={"DB-01": 0.75},
            true_life_band_by_batch={"B-100": "long"},
        )
        rep_carry = replay_plan_in_world(scen, plan, world_carryover)
        self.assertEqual(rep_carry.sold_total_units, 60)
        self.assertEqual(rep_carry.expired_unsold_total_units, 0)
        self.assertEqual(rep_carry.unexpired_carryover_total_units, 40)
        self.assertEqual(rep_carry.realized_economics.spoilage_disposal_cost_paise, 0)
        self.assertEqual(rep_carry.realized_economics.net_outcome_paise, 212_000)

    def test_small_exhaustive_oracle_matches_mip(self) -> None:
        """Step 10 & Section 12.1: MIP optimal objective matches small exhaustive enumeration oracle."""
        raw = json.loads((FIXTURES_DIR / "tiny_oracle_fixture.json").read_text())
        scen = ScenarioPackage.model_validate(raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()

        mip_plan = solve_deterministic_mip(snap)
        oracle_net, _ = solve_exhaustive_oracle(snap)
        self.assertTrue(mip_plan.validation.is_valid)
        self.assertEqual(mip_plan.forecast_economics.net_outcome_paise, oracle_net)



    def test_plan_fragility_and_domino_cascade(self) -> None:
        from app.planning.fragility import analyze_plan_fragility
        from app.planning.policy_selector import solve_scenario_selected_policy
        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_scenario_selected_policy(snap, planning_seed=1701)
        frag = analyze_plan_fragility(snap, plan)
        self.assertEqual(frag["plan_id"], plan.plan_id)
        self.assertEqual(len(frag["trip_fragility"]), len(plan.trips))
        self.assertEqual(frag["delay_cliff_curve"][0]["surviving_trips"], len(plan.trips))

    def test_q10_arrhenius_coldchain_excursion_model(self) -> None:
        from app.domain.coldchain import compute_q10_acceleration_factor, simulate_coldchain_excursion_impact
        self.assertEqual(compute_q10_acceleration_factor(4.0, 4.0, 2.2), 1.0)
        self.assertAlmostEqual(compute_q10_acceleration_factor(14.0, 4.0, 2.2), 2.2, places=3)
        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        impact = simulate_coldchain_excursion_impact(scen, ambient_or_reefer_temp_celsius=16.0, excursion_duration_min=90)
        self.assertEqual(len(impact["batch_impacts"]), len(scen.batches))
        self.assertGreater(impact["mean_life_lost_min"], 0)

    def test_2d_stress_surface_and_pareto_lambda_curve(self) -> None:
        from app.evaluation.sensitivity import compute_2d_stress_surface, compute_pareto_lambda_curve
        from app.planning.policy_selector import solve_scenario_selected_policy
        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_scenario_selected_policy(snap, planning_seed=1701)
        surface = compute_2d_stress_surface(scen, plan, delay_steps_min=[0, 30, 60], life_steps_min=[0, -40, -80])
        self.assertEqual(len(surface["cells"]), 9)
        self.assertGreaterEqual(surface["cells"][0]["realized_net_paise"], surface["cells"][-1]["realized_net_paise"])
        pareto = compute_pareto_lambda_curve(plan)
        self.assertEqual(len(pareto), 11)

    def test_operational_copilot_briefing_and_qa(self) -> None:
        from app.planning.copilot import generate_copilot_briefing_and_qa
        from app.planning.policy_selector import solve_scenario_selected_policy
        from app.simulation.inspection import rank_inspections_by_voi
        from app.simulation.worlds import generate_simulation_world
        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        ledger = OperationalStateLedger(scen)
        snap = ledger.build_planning_snapshot()
        plan = solve_scenario_selected_policy(snap, planning_seed=1701)
        w = generate_simulation_world(scen, "w_cop", 2026)
        rep = replay_plan_in_world(scen, plan, w)
        ranks = rank_inspections_by_voi(ledger)
        cop = generate_copilot_briefing_and_qa(snap, plan, rep, ranks)
        self.assertEqual(len(cop["qa_pairs"]), 5)



    def test_audit_defect1_committed_cost_not_double_charged_in_replay(self) -> None:
        """Audit Defect #1: Committing an unchanged FEFO plan must not double-subtract sunk committed cost in replay."""
        from app.domain.commitments import commit_plan_to_ledger
        from app.planning.baselines import solve_fefo_greedy
        from app.schemas.models import CommitmentState
        from app.simulation.worlds import generate_simulation_world

        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        ledger = OperationalStateLedger(scen)
        snap = ledger.build_planning_snapshot()
        fefo_plan = solve_fefo_greedy(snap)
        nom_world = generate_simulation_world(scen, "nom_w", 2026, is_nominal_world=True)

        rep_before = replay_plan_in_world(ledger.scenario, fefo_plan, nom_world, sunk_committed_cost_paise=ledger.sunk_cost_paise)
        self.assertEqual(rep_before.realized_economics.net_outcome_paise, 1895875)

        committed_ledger = commit_plan_to_ledger(
            ledger, fefo_plan, expected_revision=1, idempotency_key="idem_d1", commit_state=CommitmentState.DISPATCHED
        )
        self.assertEqual(committed_ledger.sunk_cost_paise, 178205)

        rep_after = replay_plan_in_world(
            committed_ledger.scenario, fefo_plan, nom_world, sunk_committed_cost_paise=committed_ledger.sunk_cost_paise
        )
        self.assertEqual(rep_after.realized_economics.net_outcome_paise, 1895875)
        self.assertEqual(rep_after.realized_economics.sunk_committed_cost_paise, 178205)

    def test_audit_defect2_inspection_clock_preserves_absolute_expiry_and_is_idempotent(self) -> None:
        """Audit Defect #2: Inspecting at t=100 for 15m on a batch expiring at t=320 with medium band keeps expiry at 320 (rem=205), not 420."""
        from app.simulation.inspection import _updated_life_estimates_for_band, execute_simulated_inspection

        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        b_tom1 = next(b for b in scen.batches if b.id == "B-TOM-01")
        self.assertEqual(b_tom1.estimated_expiry_nominal_min, 320)

        rem_nom, rem_low, rem_high = _updated_life_estimates_for_band(
            b_tom1, "medium", elapsed_duration_min=15, decision_at_min=100
        )
        self.assertEqual(rem_nom, 205)
        self.assertEqual(115 + rem_nom, 320)

        # Verify idempotency of execute_simulated_inspection
        ledger = OperationalStateLedger(scen)
        ledger.current_time_min = 100
        ledger1, obs1 = execute_simulated_inspection(ledger, "B-TOM-01", seed=2026)
        sunk1 = ledger1.sunk_cost_paise
        rev1 = ledger1.state_revision
        ledger2, obs2 = execute_simulated_inspection(ledger1, "B-TOM-01", seed=9999)
        self.assertEqual(ledger2.sunk_cost_paise, sunk1)
        self.assertEqual(ledger2.state_revision, rev1)
        self.assertEqual(obs1.id, obs2.id)

    def test_audit_defect3_buffered_candidate_passes_assumption_aware_validation(self) -> None:
        """Audit Defect #3: Buffered candidate (+20m route delay) is accepted under its declared assumption contract and all 6 profiles compete."""
        from app.planning.policy_selector import solve_scenario_selected_policy
        from app.validation.plan import validate_plan_independently

        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        snap = OperationalStateLedger(scen).build_planning_snapshot()

        buffered_plan = solve_deterministic_mip(
            snap, config_id="conservative_v1", assumption_profile="longer_travel", extra_route_delay_min=20
        )
        self.assertTrue(buffered_plan.validation.is_valid)
        val_rep = validate_plan_independently(snap, buffered_plan, checked_under_assumptions="profile:longer_travel")
        self.assertTrue(val_rep.is_valid)

        scen_plan = solve_scenario_selected_policy(snap, config_id="balanced_v1", planning_seed=1701)
        self.assertEqual(len(scen_plan.uncertainty_summary["candidate_profiles"]), 6)

    def test_audit_defect4_cross_field_allocation_reference_mismatch_rejected(self) -> None:
        """Audit Defect #4: Mutating an allocation destination or unit price is rejected by the independent validator."""
        import copy
        from app.planning.baselines import solve_fefo_greedy
        from app.validation.plan import validate_plan_independently

        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_fefo_greedy(snap)
        self.assertTrue(plan.validation.is_valid)

        bad_dest_plan = copy.deepcopy(plan)
        bad_dest_plan.allocations[0].destination_id = "D-UNKNOWN-999"
        rep1 = validate_plan_independently(snap, bad_dest_plan)
        self.assertFalse(rep1.is_valid)
        codes1 = {v.code for v in rep1.violations}
        self.assertTrue("UNKNOWN_ALLOCATION_DESTINATION" in codes1 or "ALLOCATION_TRIP_DESTINATION_MISMATCH" in codes1)

        bad_price_plan = copy.deepcopy(plan)
        bad_price_plan.allocations[0].unit_price_paise += 500
        rep2 = validate_plan_independently(snap, bad_price_plan)
        self.assertFalse(rep2.is_valid)
        self.assertIn("TAMPERED_UNIT_PRICE", {v.code for v in rep2.violations})

    def test_audit_adversarial_reservation_exhaustive_oracle_matches_two_stage_mip(self) -> None:
        """P1 Audit: Adversarial tiny instance where flexible batch B-FLEX must be reserved for D-FAR so fragile B-URGENT can serve D-NEAR."""
        adv_raw = json.loads((FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json").read_text())
        adv_raw["scenario_id"] = "test-adv-oracle"
        adv_raw["uncertainty_model"] = {"departure_grid_min": 180, "supplied_fixed_departures_min": [15]}
        adv_raw["batches"] = [
            {
                "id": "B-URGENT",
                "commodity_id": "TOM",
                "quantity_units": 50,
                "observed_at_min": 0,
                "remaining_life_nominal_min": 210,
                "status": "available",
            },
            {
                "id": "B-FLEX",
                "commodity_id": "TOM",
                "quantity_units": 50,
                "observed_at_min": 0,
                "remaining_life_nominal_min": 500,
                "status": "available",
            },
        ]
        adv_raw["destinations"] = [
            {"id": "D-NEAR", "name": "Near Buyer", "receiving_open_min": 0, "receiving_close_min": 300, "closed": False},
            {"id": "D-FAR", "name": "Far Buyer", "receiving_open_min": 0, "receiving_close_min": 400, "closed": False},
        ]
        adv_raw["vehicles"] = [
            {"id": "V-01", "name": "Truck 1", "capacity_units": 50, "available_from_min": 0, "available_to_min": 600},
            {"id": "V-02", "name": "Truck 2", "capacity_units": 50, "available_from_min": 0, "available_to_min": 600},
        ]
        adv_raw["routes"] = [
            {
                "id": "R-NEAR",
                "origin_id": "ORIGIN_DC",
                "destination_id": "D-NEAR",
                "outbound_travel_min": 30,
                "return_travel_min": 30,
                "loading_duration_min": 15,
                "unloading_duration_min": 15,
                "downstream_handling_duration_min": 15,
                "turnaround_duration_min": 10,
                "fixed_trip_cost_paise": 5000,
                "variable_transport_cost_paise_per_unit": 0,
                "variable_handling_cost_paise_per_unit": 100,
            },
            {
                "id": "R-FAR",
                "origin_id": "ORIGIN_DC",
                "destination_id": "D-FAR",
                "outbound_travel_min": 90,
                "return_travel_min": 90,
                "loading_duration_min": 15,
                "unloading_duration_min": 15,
                "downstream_handling_duration_min": 15,
                "turnaround_duration_min": 10,
                "fixed_trip_cost_paise": 5000,
                "variable_transport_cost_paise_per_unit": 0,
                "variable_handling_cost_paise_per_unit": 100,
            },
        ]
        adv_raw["demand_buckets"] = [
            {
                "id": "DB-NEAR",
                "destination_id": "D-NEAR",
                "commodity_id": "TOM",
                "sale_at_min": 150,
                "accepted_quantity_cap": 50,
                "freshness_buffer_min": 30,
                "price_tiers": [{"tier_id": "tier_0", "label": "Full", "discount_pct": 0, "price_paise_per_unit": 4200, "forecast_sales_units": 50}],
            },
            {
                "id": "DB-FAR",
                "destination_id": "D-FAR",
                "commodity_id": "TOM",
                "sale_at_min": 270,
                "accepted_quantity_cap": 50,
                "freshness_buffer_min": 30,
                "price_tiers": [{"tier_id": "tier_0", "label": "Full", "discount_pct": 0, "price_paise_per_unit": 4000, "forecast_sales_units": 50}],
            },
        ]
        scen = ScenarioPackage.model_validate(adv_raw)
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        oracle_net, _ = solve_exhaustive_oracle(snap)
        mip_plan = solve_deterministic_mip(snap)
        self.assertEqual(mip_plan.forecast_economics.net_outcome_paise, oracle_net)
        self.assertEqual(sum(a.quantity_units for a in mip_plan.allocations), 100)

    def test_audit_feature_a_b_c_fragility_replay_boundary_and_recovery_options(self) -> None:
        """Features A, B, C: Replay-verified failure boundary and bounded recovery comparator."""
        from app.planning.fragility import analyze_plan_fragility
        from app.planning.policy_selector import solve_scenario_selected_policy
        from app.planning.recovery import compare_recovery_interventions

        demo_p = FIXTURES_DIR.parent.parent.parent / "scenarios/demo/demo-normal-001.json"
        scen = ScenarioPackage.model_validate(json.loads(demo_p.read_text()))
        snap = OperationalStateLedger(scen).build_planning_snapshot()
        plan = solve_scenario_selected_policy(snap, planning_seed=1701)

        frag = analyze_plan_fragility(snap, plan)
        for tf in frag["trip_fragility"]:
            self.assertTrue(tf["replay_verified"])

        rec = compare_recovery_interventions(snap, prior_plan=plan, planning_seed=1701)
        self.assertEqual(len(rec["options"]), 4)
        self.assertIn("recommended_option_id", rec)


if __name__ == "__main__":
    unittest.main()
