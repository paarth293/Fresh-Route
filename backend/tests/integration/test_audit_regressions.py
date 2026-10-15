"""Regression coverage for issues discovered in the October 2026 build audit."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from app.main import create_app
from app.domain.inventory import OperationalStateLedger
from app.domain.commitments import apply_operational_event, commit_plan_to_ledger
from app.planning.mip import solve_deterministic_mip
from app.planning.policy_selector import resolve_planning_config
from app.planning.candidates import generate_candidate_trips
from app.schemas.models import ScenarioPackage, OperationalEvent, EventType, CommitmentState

ROOT = Path(__file__).resolve().parents[3]


class TestAuditRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = os.environ.get('FRESHROUTE_DB_PATH')
        os.environ['FRESHROUTE_DB_PATH'] = str(Path(self.tmp.name) / 'audit.sqlite3')
        self.client = TestClient(create_app())
        self.raw = json.loads((ROOT / 'scenarios/demo/demo-normal-001.json').read_text(encoding='utf-8'))
        self.sid = self.raw['scenario_id']
        self.client.post('/api/v1/scenarios', json=self.raw)

    def tearDown(self):
        self.client.close()
        if self.previous_db is None:
            os.environ.pop('FRESHROUTE_DB_PATH', None)
        else:
            os.environ['FRESHROUTE_DB_PATH'] = self.previous_db
        self.tmp.cleanup()

    def plan(self):
        revision = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()['state_revision']
        job = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'state_revision': revision, 'policy': 'nominal_mip'}).json()
        self.assertEqual(job['status'], 'succeeded', job)
        return self.client.get(f"/api/v1/plans/{job['result_id']}").json()

    def replay(self, plan):
        job = self.client.post('/api/v1/replay-jobs', json={'plan_id': plan['plan_id'], 'world_seed': 2026}).json()
        self.assertEqual(job['status'], 'succeeded', job)
        return self.client.get(f"/api/v1/reports/{job['result_id']}").json()

    def test_demo_and_validation_endpoints(self):
        self.assertGreater(len(self.client.get('/api/v1/demo-scenarios').json()), 0)
        self.assertTrue(self.client.post('/api/v1/scenarios/validate', json=self.raw).json()['is_valid'])

    def test_json_arrays_and_malformed_requests_are_client_errors(self):
        for path in ['/api/v1/planning-jobs', f'/api/v1/scenarios/{self.sid}/versions', '/api/v1/replay-jobs']:
            self.assertEqual(self.client.post(path, json=[]).status_code, 400)
            self.assertEqual(self.client.post(path, content='{bad').status_code, 400)

    def test_reimport_creates_new_version_and_rejects_old_commit(self):
        plan = self.plan()
        meta = self.client.post('/api/v1/scenarios', json=self.raw).json()
        self.assertNotEqual(meta['scenario_version_id'], plan['scenario_version_id'])
        response = self.client.post(f"/api/v1/plans/{plan['plan_id']}/commit", json={'expected_revision': 1, 'idempotency_key': 'stale-import'})
        self.assertEqual(response.status_code, 409)

    def test_replay_is_immutable_after_scenario_changes(self):
        plan = self.plan()
        before = self.replay(plan)
        changed = copy.deepcopy(self.raw)
        changed['routes'][0]['outbound_travel_min'] += 150
        self.client.post('/api/v1/scenarios', json=changed)
        after = self.replay(plan)
        self.assertEqual(before, after)

    def test_inspection_persists_history_cost_and_is_idempotent(self):
        batch = next(b for b in self.raw['batches'] if b['inspectable'])
        path = f'/api/v1/scenarios/{self.sid}/inspections'
        first = self.client.post(path, json={'batch_id': batch['id'], 'idempotency_key': 'inspection-a'})
        self.assertEqual(first.status_code, 200, first.text)
        state = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()
        self.assertEqual(len(state['inspection_history']), 1)
        self.assertEqual(state['sunk_cost_paise'], batch['inspection_cost_paise'])
        self.assertTrue(state['posterior_world_weights'])
        second = self.client.post(path, json={'batch_id': batch['id'], 'idempotency_key': 'inspection-a'})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()['state_revision'], first.json()['state_revision'])
        plan = self.plan()
        self.assertEqual(plan['forecast_economics']['inspection_cost_paise'], batch['inspection_cost_paise'])
        replay = self.replay(plan)
        self.assertEqual(replay['realized_economics']['inspection_cost_paise'], batch['inspection_cost_paise'])

    def test_initial_multibatch_commitment_cost_counted_once(self):
        ledger = OperationalStateLedger(ScenarioPackage.model_validate(self.raw))
        plan = solve_deterministic_mip(ledger.build_planning_snapshot())
        committed = commit_plan_to_ledger(ledger, plan, 1, 'initial', commit_state=CommitmentState.DISPATCHED)
        restored = OperationalStateLedger(committed.scenario)
        expected = sum(c.incurred_fixed_cost_paise + c.incurred_variable_cost_paise for c in committed.commitments.values())
        self.assertEqual(restored.sunk_cost_paise, expected)
        self.assertTrue(restored.verify_conservation())

    def test_status_only_batch_update_preserves_absolute_expiry(self):
        ledger = OperationalStateLedger(ScenarioPackage.model_validate(self.raw))
        b = ledger.scenario.batches[0]
        expiry = b.estimated_expiry_nominal_min
        updated = apply_operational_event(ledger, OperationalEvent(id='status', event_type=EventType.BATCH_ESTIMATE_UPDATED, effective_at_min=10, entity_id=b.id, payload={'status': 'quarantined'}))
        self.assertEqual(updated.scenario.batches[0].estimated_expiry_nominal_min, expiry)

    def test_unknown_event_target_and_negative_delay_rejected(self):
        for target, delay in [('unknown-route', 20), (self.raw['routes'][0]['id'], -20)]:
            result = self.client.post(f'/api/v1/scenarios/{self.sid}/events', json={'id': 'invalid', 'event_type': 'ROUTE_DELAY_OBSERVED', 'entity_id': target, 'effective_at_min': 0, 'payload': {'delay_minutes': delay}})
            self.assertEqual(result.status_code, 422)

    def test_config_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            resolve_planning_config('../scenarios/demo/demo-normal-001')

    def test_completed_job_cannot_be_cancelled(self):
        job = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'policy': 'nominal_mip'}).json()
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(self.client.post(f"/api/v1/jobs/{job['job_id']}/cancel").status_code, 409)

    def test_invalid_numeric_request_fields_are_client_errors(self):
        response = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'state_revision': 'wrong'})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f'/api/v1/scenarios/{self.sid}/inspections', json={'batch_id': 'B-TOM-01', 'world_seed': []})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f'/api/v1/scenarios/{self.sid}/coldchain-sim', json={'duration_min': -1})
        self.assertEqual(response.status_code, 400)

    def test_replan_distinguishes_existing_conflicts_from_new_allocations(self):
        plan = self.plan()
        committed = self.client.post(f"/api/v1/plans/{plan['plan_id']}/commit", json={'expected_revision': 1, 'idempotency_key': 'delay-test'})
        self.assertEqual(committed.status_code, 200)
        event = {'id': 'delay', 'event_type': 'ROUTE_DELAY_OBSERVED', 'entity_id': 'R-NORTH', 'effective_at_min': 0, 'payload': {'delay_minutes': 20}}
        self.assertEqual(self.client.post(f'/api/v1/scenarios/{self.sid}/events', json=event).status_code, 200)
        revised = self.plan()
        self.assertTrue(revised['validation']['is_valid'])
        self.assertTrue(any(w['code'] == 'EXISTING_COMMITMENT_OVERLAP' for w in revised['validation']['warnings']))

    def test_delivery_moves_stock_and_keeps_vehicle_locked_until_return(self):
        ledger = OperationalStateLedger(ScenarioPackage.model_validate(self.raw))
        plan = solve_deterministic_mip(ledger.build_planning_snapshot())
        trip = plan.trips[0]
        ledger = commit_plan_to_ledger(ledger, plan, 1, 'delivery', trip_ids_to_commit=[trip.trip_id])
        event = OperationalEvent(id='delivered', event_type=EventType.TRIP_DELIVERED, entity_id=trip.trip_id, effective_at_min=trip.unloading_end_min)
        delivered = apply_operational_event(ledger, event)
        self.assertEqual(next(iter(delivered.commitments.values())).state, CommitmentState.DELIVERED)
        self.assertTrue(delivered.verify_conservation())
        self.assertEqual(sum(delivered.destination_stock.values()), trip.total_loaded_units)
        candidates, _ = generate_candidate_trips(delivered.build_planning_snapshot())
        self.assertTrue(all(c.loading_start_min >= trip.vehicle_release_min for c in candidates if c.vehicle_id == trip.vehicle_id))
        repeated = apply_operational_event(delivered, event)
        self.assertEqual(repeated.state_revision, delivered.state_revision)

    def test_invalid_scenario_settings_and_oversubscribed_commitments_rejected(self):
        for changes in [{'planning_world_count': 0}, {'life_shock_min_range': [1]}]:
            raw = copy.deepcopy(self.raw)
            raw['uncertainty_model'].update(changes)
            self.assertEqual(self.client.post('/api/v1/scenarios', json=raw).status_code, 422)
        ledger = OperationalStateLedger(ScenarioPackage.model_validate(self.raw))
        plan = solve_deterministic_mip(ledger.build_planning_snapshot())
        ledger = commit_plan_to_ledger(ledger, plan, 1, 'invalid-import')
        raw = ledger.scenario.model_dump()
        raw['commitments'][0]['allocations'][0]['quantity_units'] = 999999
        self.assertEqual(self.client.post('/api/v1/scenarios', json=raw).status_code, 422)
