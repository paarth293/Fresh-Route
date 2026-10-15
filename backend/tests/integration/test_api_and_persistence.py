"""End-to-end FastAPI endpoint integration tests covering Section 9.2 routes."""
from __future__ import annotations
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

DEMO_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scenarios/demo/demo-normal-001.json"


class TestFastAPIEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["FRESHROUTE_DB_PATH"] = os.path.join(self.tmpdir.name, "api_test.sqlite3")
        self.app = create_app()
        self.client = TestClient(self.app)
        self.demo_raw = json.loads(DEMO_PATH.read_text())

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_full_operator_lifecycle_via_api(self) -> None:
        # 1. Health check
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        self.assertEqual(r_health.json()["status"], "ok")

        # 2. Import scenario
        r_create = self.client.post("/api/v1/scenarios", json=self.demo_raw)
        self.assertEqual(r_create.status_code, 201)
        scen_id = r_create.json()["scenario_id"]
        self.assertEqual(scen_id, "demo-normal-001")

        # 3. Read scenario & state
        r_scen = self.client.get(f"/api/v1/scenarios/{scen_id}")
        self.assertEqual(r_scen.status_code, 200)
        self.assertNotIn("true_expiry_by_batch", r_scen.json())

        r_state = self.client.get(f"/api/v1/scenarios/{scen_id}/state")
        self.assertEqual(r_state.status_code, 200)
        self.assertEqual(r_state.json()["state_revision"], 1)

        # 4. Submit planning job (scenario_selected)
        r_job = self.client.post(
            "/api/v1/planning-jobs",
            json={
                "scenario_id": scen_id,
                "state_revision": 1,
                "policy": "scenario_selected",
                "config_id": "balanced_v1",
                "planning_seed": 1701,
            },
        )
        self.assertEqual(r_job.status_code, 202)
        job_data = r_job.json()
        self.assertEqual(job_data["status"], "succeeded")
        plan_id = job_data["result_id"]

        # 5. Read plan, batch receipt, controlled alternative, and export
        r_plan = self.client.get(f"/api/v1/plans/{plan_id}")
        self.assertEqual(r_plan.status_code, 200)
        plan_json = r_plan.json()
        self.assertTrue(plan_json["validation"]["is_valid"])

        r_rec = self.client.get(f"/api/v1/plans/{plan_id}/receipts/B-TOM-01")
        self.assertEqual(r_rec.status_code, 200)
        self.assertEqual(r_rec.json()["batch_id"], "B-TOM-01")

        r_alt = self.client.post(
            f"/api/v1/plans/{plan_id}/alternatives",
            json={"batch_id": "B-TOM-01", "alternative_destination_id": "D-MANDI-EAST", "forced_quantity_units": 30},
        )
        self.assertEqual(r_alt.status_code, 200)
        self.assertIn("is_feasible", r_alt.json())

        r_exp = self.client.get(f"/api/v1/plans/{plan_id}/export")
        self.assertEqual(r_exp.status_code, 200)
        self.assertEqual(r_exp.json()["plan"]["plan_id"], plan_id)

        # 6. Commit plan with idempotency key, then test stale conflict
        r_comm = self.client.post(
            f"/api/v1/plans/{plan_id}/commit",
            json={"expected_revision": 1, "idempotency_key": "idem_001", "commit_state": "reserved"},
        )
        self.assertEqual(r_comm.status_code, 200)
        self.assertEqual(r_comm.json()["new_state_revision"], 2)

        # Retry with same idempotency_key succeeds idempotently
        r_comm_retry = self.client.post(
            f"/api/v1/plans/{plan_id}/commit",
            json={"expected_revision": 1, "idempotency_key": "idem_001", "commit_state": "reserved"},
        )
        self.assertEqual(r_comm_retry.status_code, 200)

        # Second commit with different idempotency_key and stale revision 1 fails with 409
        r_comm_conflict = self.client.post(
            f"/api/v1/plans/{plan_id}/commit",
            json={"expected_revision": 1, "idempotency_key": "idem_002", "commit_state": "reserved"},
        )
        self.assertEqual(r_comm_conflict.status_code, 409)
        self.assertEqual(r_comm_conflict.json()["error"]["code"], "STATE_REVISION_CONFLICT")

        # 7. Post disruption event & execute inspection
        r_ev = self.client.post(
            f"/api/v1/scenarios/{scen_id}/events",
            json={
                "id": "ev_api_delay_1",
                "effective_at_min": 30,
                "event_type": "ROUTE_DELAY_OBSERVED",
                "entity_id": "R-EAST",
                "payload": {"delay_minutes": 20},
            },
        )
        self.assertEqual(r_ev.status_code, 200)
        self.assertEqual(r_ev.json()["state_revision"], 3)

        r_insp_job = self.client.post("/api/v1/inspection-jobs", json={"scenario_id": scen_id, "planning_seed": 1701})
        self.assertEqual(r_insp_job.status_code, 202)
        insp_rep_id = r_insp_job.json()["result_id"]
        r_insp_rep = self.client.get(f"/api/v1/reports/{insp_rep_id}")
        self.assertEqual(r_insp_rep.status_code, 200)

        # 8. Replay job
        r_rep_job = self.client.post(
            "/api/v1/replay-jobs", json={"scenario_id": scen_id, "plan_id": plan_id, "world_seed": 2026}
        )
        self.assertEqual(r_rep_job.status_code, 202)
        rep_id = r_rep_job.json()["result_id"]
        r_rep = self.client.get(f"/api/v1/reports/{rep_id}")
        self.assertEqual(r_rep.status_code, 200)
        self.assertTrue(r_rep.json()["ledger_reconciled"])



    def test_version_identity_historical_receipts_and_idempotency_conflicts(self) -> None:
        """P0 Audit: Old plan cannot commit after scenario replacement at rev 1; historical receipts stay immutable; conflicting idempotency reuse returns 409."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["FRESHROUTE_DB_PATH"] = os.path.join(tmpdir, "ver_id.sqlite3")
            app = create_app()
            client = TestClient(app)
            raw = json.loads(DEMO_PATH.read_text())

            # 1. Import version 1 and generate plan_v1
            r1 = client.post("/api/v1/scenarios", json=raw)
            self.assertEqual(r1.status_code, 201)
            r_job = client.post(
                "/api/v1/planning-jobs",
                json={"scenario_id": "demo-normal-001", "state_revision": 1, "policy": "fefo_greedy"},
            )
            plan_v1_id = r_job.json()["result_id"]

            # Fetch historical receipt before any events
            rec_before = client.get(f"/api/v1/plans/{plan_v1_id}/receipts/B-TOM-01").json()
            self.assertFalse(rec_before["is_stale_vs_current_ledger"])

            # 2. Apply an event that mutates B-TOM-01 life and advances revision to 2
            ev_payload = {
                "id": "ev_mut_1",
                "event_type": "BATCH_ESTIMATE_UPDATED",
                "effective_at_min": 20,
                "entity_id": "B-TOM-01",
                "payload": {"remaining_life_nominal_min": 190},
            }
            r_ev1 = client.post("/api/v1/scenarios/demo-normal-001/events", json=ev_payload)
            self.assertEqual(r_ev1.status_code, 200)

            # Re-submitting identical event is idempotent (200)
            r_ev_same = client.post("/api/v1/scenarios/demo-normal-001/events", json=ev_payload)
            self.assertEqual(r_ev_same.status_code, 200)

            # Re-submitting same event_id with different payload is rejected with 409 IDEMPOTENCY_KEY_CONFLICT
            ev_conflict = dict(ev_payload, payload={"remaining_life_nominal_min": 140})
            r_ev_conf = client.post("/api/v1/scenarios/demo-normal-001/events", json=ev_conflict)
            self.assertEqual(r_ev_conf.status_code, 409)
            self.assertEqual(r_ev_conf.json()["error"]["code"], "IDEMPOTENCY_KEY_CONFLICT")

            # Historical receipt for plan_v1 still reflects original snapshot (estimated_expiry_min == 320), marked stale vs current ledger
            rec_after = client.get(f"/api/v1/plans/{plan_v1_id}/receipts/B-TOM-01").json()
            self.assertEqual(rec_after["estimated_expiry_min"], rec_before["estimated_expiry_min"])
            self.assertEqual(rec_after["snapshot_hash"], rec_before["snapshot_hash"])
            self.assertTrue(rec_after["is_stale_vs_current_ledger"])

            # 3. Replace scenario with a modified version (new scenario_version_id, resets state_revision to 1)
            raw_v2 = copy.deepcopy(raw)
            raw_v2["batches"][0]["quantity_units"] = 85
            r2 = client.post("/api/v1/scenarios", json=raw_v2)
            self.assertEqual(r2.status_code, 201)
            self.assertEqual(r2.json()["state_revision"], 1)

            # Attempting to commit plan_v1 (which has state_revision=1 but old scenario_version_id) must fail with 409!
            r_commit_old = client.post(
                f"/api/v1/plans/{plan_v1_id}/commit",
                json={"expected_revision": 1, "idempotency_key": "idem_old_v1"},
            )
            self.assertEqual(r_commit_old.status_code, 409)


if __name__ == "__main__":
    unittest.main()
