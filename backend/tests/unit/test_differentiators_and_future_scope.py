"""Unit and API tests for Plan Fragility, 2D Sensitivity Heatmap, Pareto Curve, Q10 Cold-Chain, and Copilot."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.domain.coldchain import (
    compute_q10_acceleration_factor,
    simulate_coldchain_excursion_impact,
)
from app.domain.inventory import OperationalStateLedger
from app.evaluation.sensitivity import (
    compute_2d_stress_surface,
    compute_pareto_lambda_curve,
)
from app.main import create_app
from app.planning.copilot import generate_copilot_briefing_and_qa
from app.planning.fragility import analyze_plan_fragility
from app.planning.policy_selector import solve_scenario_selected_policy
from app.schemas.models import ScenarioPackage
from app.simulation.inspection import rank_inspections_by_voi
from app.simulation.replay import replay_plan_in_world
from app.simulation.worlds import generate_simulation_world

DEMO_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scenarios/demo/demo-normal-001.json"


class TestDifferentiatorsAndFutureScope(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = json.loads(DEMO_PATH.read_text())
        self.scen = ScenarioPackage.model_validate(self.raw)
        self.ledger = OperationalStateLedger(self.scen)
        self.snap = self.ledger.build_planning_snapshot()
        self.plan = solve_scenario_selected_policy(self.snap, planning_seed=1701)

    def test_plan_fragility_and_domino_cascade_analyzer(self) -> None:
        frag = analyze_plan_fragility(self.snap, self.plan)
        self.assertEqual(frag["plan_id"], self.plan.plan_id)
        self.assertEqual(len(frag["trip_fragility"]), len(self.plan.trips))
        self.assertEqual(len(frag["delay_cliff_curve"]), 7)
        # At 0 extra delay, all trips survive
        self.assertEqual(frag["delay_cliff_curve"][0]["surviving_trips"], len(self.plan.trips))

    def test_q10_arrhenius_coldchain_excursion_model(self) -> None:
        # At reference 4°C, acceleration factor is 1.0
        self.assertEqual(compute_q10_acceleration_factor(4.0, 4.0, 2.2), 1.0)
        # At 14°C (+10°C above 4°C), acceleration factor equals Q10 (2.2)
        self.assertAlmostEqual(compute_q10_acceleration_factor(14.0, 4.0, 2.2), 2.2, places=3)

        impact = simulate_coldchain_excursion_impact(self.scen, ambient_or_reefer_temp_celsius=16.0, excursion_duration_min=90)
        self.assertEqual(len(impact["batch_impacts"]), len(self.scen.batches))
        self.assertGreater(impact["mean_life_lost_min"], 0)

    def test_2d_stress_surface_and_pareto_lambda_curve(self) -> None:
        surface = compute_2d_stress_surface(self.scen, self.plan, delay_steps_min=[0, 30, 60], life_steps_min=[0, -40, -80])
        self.assertEqual(len(surface["cells"]), 9)
        # Net outcome at (0, 0) should be >= net outcome at (+60m delay, -80m life)
        net_00 = surface["cells"][0]["realized_net_paise"]
        net_worst = surface["cells"][-1]["realized_net_paise"]
        self.assertGreaterEqual(net_00, net_worst)

        pareto = compute_pareto_lambda_curve(self.plan)
        self.assertEqual(len(pareto), 11)

    def test_operational_copilot_briefing_and_qa(self) -> None:
        w = generate_simulation_world(self.scen, "w_cop", 2026)
        rep = replay_plan_in_world(self.scen, self.plan, w)
        ranks = rank_inspections_by_voi(self.ledger)
        cop = generate_copilot_briefing_and_qa(self.snap, self.plan, rep, ranks)
        self.assertEqual(len(cop["qa_pairs"]), 5)
        self.assertIn("Forecast Net", cop["headline"])

    def test_differentiator_api_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["FRESHROUTE_DB_PATH"] = os.path.join(tmpdir, "diff_api.sqlite3")
            app = create_app()
            client = TestClient(app)
            client.post("/api/v1/scenarios", json=self.raw)
            r_job = client.post(
                "/api/v1/planning-jobs",
                json={"scenario_id": "demo-normal-001", "state_revision": 1, "policy": "scenario_selected"},
            )
            plan_id = r_job.json()["result_id"]

            r_frag = client.get(f"/api/v1/plans/{plan_id}/fragility")
            self.assertEqual(r_frag.status_code, 200)
            self.assertIn("delay_cliff_curve", r_frag.json())

            r_sens = client.get(f"/api/v1/plans/{plan_id}/sensitivity")
            self.assertEqual(r_sens.status_code, 200)
            self.assertIn("stress_surface", r_sens.json())

            r_cc = client.post(
                "/api/v1/scenarios/demo-normal-001/coldchain-sim",
                json={"temp_celsius": 14.0, "duration_min": 60},
            )
            self.assertEqual(r_cc.status_code, 200)
            self.assertGreater(r_cc.json()["mean_life_lost_min"], 0)

            r_cop = client.get(f"/api/v1/plans/{plan_id}/copilot")
            self.assertEqual(r_cop.status_code, 200)
            self.assertEqual(len(r_cop.json()["qa_pairs"]), 5)


if __name__ == "__main__":
    unittest.main()
