"""Real process-queue, session, input, and reservation repair integration tests."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import create_app
from app.persistence.database import get_connection, load_operational_ledger
from app.jobs.queue import enqueue, shutdown_queue
from app.jobs.worker import create_job_record

ROOT = Path(__file__).resolve().parents[3]


class TestHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tmp.name) / 'hardening.sqlite3')
        self.env = patch.dict(os.environ, {'FRESHROUTE_DB_PATH': self.database, 'FRESHROUTE_OPERATOR_PASSWORD': ''})
        self.env.start()
        self.client = TestClient(create_app())
        self.raw = json.loads((ROOT / 'scenarios/demo/demo-normal-001.json').read_text(encoding='utf-8'))
        self.sid = self.raw['scenario_id']
        self.client.post('/api/v1/scenarios', json=self.raw)

    def tearDown(self):
        shutdown_queue(self.database)
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def wait_job(self, job_id):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = self.client.get(f'/api/v1/jobs/{job_id}').json()
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.05)
        self.fail('Queued job did not terminate within 60 seconds')

    def test_async_job_returns_immediately_and_persists_real_plan(self):
        started = time.monotonic()
        response = self.client.post('/api/v1/planning-jobs', headers={'Prefer': 'respond-async'}, json={'scenario_id': self.sid, 'policy': 'nominal_mip'})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers['Preference-Applied'], 'respond-async')
        self.assertLess(time.monotonic() - started, 2)
        result = self.wait_job(response.json()['job_id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertTrue(self.client.get(f"/api/v1/plans/{result['result_id']}").json()['validation']['is_valid'])

    def test_running_cancellation_terminates_child_and_preserves_ledger(self):
        before = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()
        response = self.client.post('/api/v1/inspection-jobs', headers={'Prefer': 'respond-async'}, json={'scenario_id': self.sid})
        job_id = response.json()['job_id']
        manager = enqueue(self.database)
        deadline = time.monotonic() + 10
        child = None
        while time.monotonic() < deadline:
            with manager.lock:
                if manager.job_id == job_id and manager.process:
                    child = manager.process
            if self.client.get(f'/api/v1/jobs/{job_id}').json()['status'] == 'running':
                break
            time.sleep(.05)
        self.assertIsNotNone(child)
        cancelled = self.client.post(f'/api/v1/jobs/{job_id}/cancel')
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(self.wait_job(job_id)['status'], 'cancelled')
        child.wait(timeout=5)
        self.assertIsNotNone(child.poll())
        after = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()
        self.assertEqual(before, after)

    def test_queued_work_is_resumed_on_application_startup(self):
        job_id = create_job_record('planning', {'policy': 'nominal_mip'}, scenario_id=self.sid)
        with TestClient(create_app()) as client:
            self.assertEqual(self.wait_job(job_id)['status'], 'succeeded')
        # Starlette 0.25 leaves its lifespan memory channels open on exit.
        # Close both halves explicitly so the test harness owns its resources.
        for stream in (client.stream_send, client.stream_receive):
            stream.send_stream.close()
            stream.receive_stream.close()

    def test_cancelled_job_cannot_restart(self):
        job_id = create_job_record('planning', {'policy': 'nominal_mip'}, scenario_id=self.sid)
        self.assertEqual(self.client.post(f'/api/v1/jobs/{job_id}/cancel').status_code, 200)
        from app.jobs.worker import execute_job_synchronously
        self.assertEqual(execute_job_synchronously(job_id)['status'], 'cancelled')

    def test_orphan_worker_exits_without_executing_queued_job(self):
        job_id = create_job_record('planning', {'policy': 'nominal_mip'}, scenario_id=self.sid)
        child = subprocess.run([sys.executable, '-m', 'app.jobs.runner', '--job-id', job_id, '--database', self.database, '--parent-pid', '1073741824'], cwd=str(ROOT / 'backend'), capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 75, child.stderr.decode(errors='replace'))
        self.assertEqual(self.client.get(f'/api/v1/jobs/{job_id}').json()['status'], 'queued')

    def test_queue_serializes_work_and_health_remains_responsive(self):
        headers = {'Prefer': 'respond-async'}
        first = self.client.post('/api/v1/inspection-jobs', headers=headers, json={'scenario_id': self.sid}).json()
        second = self.client.post('/api/v1/planning-jobs', headers=headers, json={'scenario_id': self.sid, 'policy': 'nominal_mip'}).json()
        started = time.monotonic()
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.client.post(f"/api/v1/jobs/{first['job_id']}/cancel").status_code, 200)
        self.assertEqual(self.wait_job(first['job_id'])['status'], 'cancelled')
        self.assertEqual(self.wait_job(second['job_id'])['status'], 'succeeded')

    def test_reservation_conflict_can_be_repaired_without_moving_locked_stock(self):
        job = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'policy': 'nominal_mip'}).json()
        plan = self.client.get(f"/api/v1/plans/{job['result_id']}").json()
        self.client.post(f"/api/v1/plans/{plan['plan_id']}/commit", json={'expected_revision': 1, 'idempotency_key': 'repair'})
        self.client.post(f'/api/v1/scenarios/{self.sid}/events', json={'id': 'delay', 'event_type': 'ROUTE_DELAY_OBSERVED', 'effective_at_min': 0, 'entity_id': 'R-NORTH', 'payload': {'delay_minutes': 20}})
        before = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()
        target = next(c for c in before['scenario']['commitments'] if c['route_id'] == 'R-EAST' and c['vehicle_id'] == 'V-TRUCK-01')
        endpoint = f"/api/v1/scenarios/{self.sid}/commitments/{target['id']}/release"
        body = {'expected_revision': before['state_revision'], 'idempotency_key': 'move-1'}
        moved = self.client.post(endpoint, json=body)
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()['cancellation_cost_incurred_paise'], self.raw['cost_model']['cancellation_fee_paise_per_trip'])
        again = self.client.post(endpoint, json=body)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json(), moved.json())
        after = self.client.get(f'/api/v1/scenarios/{self.sid}/state').json()
        self.assertGreater(sum(after['warehouse_stock_by_batch'].values()), sum(before['warehouse_stock_by_batch'].values()))
        self.assertEqual(after['sunk_cost_paise'] - before['sunk_cost_paise'], self.raw['cost_model']['cancellation_fee_paise_per_trip'])
        self.assertEqual(len(before['scenario']['commitments']) - 1, len(after['scenario']['commitments']))
        repaired_job = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'policy': 'nominal_mip'}).json()
        repaired = self.client.get(f"/api/v1/plans/{repaired_job['result_id']}").json()
        self.assertTrue(repaired['validation']['is_valid'])
        self.assertFalse(repaired['validation']['warnings'])
        fee = self.raw['cost_model']['cancellation_fee_paise_per_trip']
        self.assertEqual(repaired['forecast_economics']['cancellation_cost_paise'], fee)
        replay_job = self.client.post('/api/v1/replay-jobs', json={'plan_id': repaired['plan_id']}).json()
        replay = self.client.get(f"/api/v1/reports/{replay_job['result_id']}").json()
        self.assertEqual(replay['realized_economics']['cancellation_cost_paise'], fee)
        locked = next(c for c in after['scenario']['commitments'] if c['state'] == 'loading')
        response = self.client.post(f"/api/v1/scenarios/{self.sid}/commitments/{locked['id']}/reschedule", json={'expected_revision': after['state_revision'], 'idempotency_key': 'locked', 'departure_min': locked['departure_min'] + 30})
        self.assertEqual(response.status_code, 422)

    def test_reservation_move_preserves_stock_and_is_idempotent(self):
        from app.domain.commitments import next_reserved_departure
        job = self.client.post('/api/v1/planning-jobs', json={'scenario_id': self.sid, 'policy': 'nominal_mip'}).json()
        self.client.post(f"/api/v1/plans/{job['result_id']}/commit", json={'expected_revision': 1, 'idempotency_key': 'reschedule-test'})
        ledger = load_operational_ledger(self.sid)
        moved = False
        for commitment in ledger.commitments.values():
            try:
                timing = next_reserved_departure(ledger, commitment.id)
            except ValueError:
                continue
            self.assertGreater(timing['departure_min'], commitment.departure_min)
            endpoint = f'/api/v1/scenarios/{self.sid}/commitments/{commitment.id}/reschedule'
            body = {'expected_revision': ledger.state_revision, 'idempotency_key': 'move-once'}
            response = self.client.post(endpoint, json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.post(endpoint, json=body).json(), response.json())
            after = load_operational_ledger(self.sid)
            self.assertEqual(after.warehouse_unreserved, ledger.warehouse_unreserved)
            self.assertEqual(after.scenario.cost_model, ledger.scenario.cost_model)
            # The event clock can start other trips whose loading is due now;
            # rescheduling itself must preserve every committed trip cost.
            self.assertEqual(sum(c.incurred_fixed_cost_paise + c.incurred_variable_cost_paise for c in after.commitments.values()), sum(c.incurred_fixed_cost_paise + c.incurred_variable_cost_paise for c in ledger.commitments.values()))
            moved = True
            break
        self.assertTrue(moved, 'At least one demo reservation should have a feasible later slot')

    def test_invalid_input_shapes_and_probability_models_are_rejected(self):
        for body in [{'scenario_id': []}, {'scenario_id': self.sid, 'policy': []}, {'scenario_id': self.sid, 'world_count': 0}, {'scenario_ids': 'all'}]:
            self.assertEqual(self.client.post('/api/v1/planning-jobs', json=body).status_code, 400)
        raw = copy.deepcopy(self.raw)
        raw['uncertainty_model']['observation_model'] = {'matrix': {'short': {'short': 1.5}}}
        self.assertEqual(self.client.post('/api/v1/scenarios', json=raw).status_code, 422)

    def test_password_sessions_protect_api_and_logout_revokes_cookie(self):
        with patch.dict(os.environ, {'FRESHROUTE_OPERATOR_PASSWORD': 'test-workspace-password'}):
            secured = TestClient(create_app())
        self.assertEqual(secured.get('/api/v1/demo-scenarios').status_code, 401)
        self.assertEqual(secured.get('/openapi.json').status_code, 401)
        self.assertEqual(secured.post('/api/v1/auth/login', json={'password': 'wrong'}).status_code, 401)
        login = secured.post('/api/v1/auth/login', json={'password': 'test-workspace-password'})
        self.assertEqual(login.status_code, 200)
        cookie = secured.cookies.get('freshroute_session')
        self.assertIn('HttpOnly', login.headers['set-cookie'])
        self.assertIn('SameSite=strict', login.headers['set-cookie'])
        self.assertEqual(secured.get('/api/v1/demo-scenarios').status_code, 200)
        self.assertEqual(secured.post('/api/v1/scenarios', headers={'Origin': 'https://attacker.invalid'}, json=self.raw).status_code, 403)
        secured.post('/api/v1/auth/logout', json={})
        secured.cookies.set('freshroute_session', cookie)
        self.assertEqual(secured.get('/api/v1/demo-scenarios').status_code, 401)
        secured.close()

    def test_login_rate_limit_and_short_password_rejected(self):
        with patch.dict(os.environ, {'FRESHROUTE_OPERATOR_PASSWORD': 'test-workspace-password'}):
            secured = TestClient(create_app())
        for _ in range(5):
            self.assertEqual(secured.post('/api/v1/auth/login', json={'password': 'wrong'}).status_code, 401)
        self.assertEqual(secured.post('/api/v1/auth/login', json={'password': 'wrong'}).status_code, 429)
        secured.close()
        with patch.dict(os.environ, {'FRESHROUTE_OPERATOR_PASSWORD': 'short'}):
            with self.assertRaises(ValueError):
                create_app()

    def test_cross_origin_writes_blocked_even_without_login(self):
        response = self.client.post('/api/v1/scenarios', headers={'Origin': 'https://attacker.invalid'}, json=self.raw)
        self.assertEqual(response.status_code, 403)
