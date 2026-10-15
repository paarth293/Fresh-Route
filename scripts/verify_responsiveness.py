"""Local concurrency smoke check during real native-solver work; not a capacity SLA."""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8765'


def request(path, body=None, asynchronous=False):
    headers = {'Content-Type': 'application/json'}
    if asynchronous:
        headers['Prefer'] = 'respond-async'
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def main():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8765))
    (ROOT / 'work').mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(FRESHROUTE_DB_PATH=str(ROOT / 'work/responsiveness.sqlite3'), FRESHROUTE_OPERATOR_PASSWORD='')
    job_id = None
    with (ROOT / 'work/responsiveness-server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1', '--port', '8765'], env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 20
            while True:
                if server.poll() is not None:
                    raise RuntimeError('Smoke-check server failed; see work/responsiveness-server.log')
                try:
                    request('/health')
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.1)
            scenario = json.loads((ROOT / 'scenarios/demo/demo-normal-001.json').read_text(encoding='utf-8'))
            request('/api/v1/scenarios', scenario)
            job = request('/api/v1/inspection-jobs', {'scenario_id': scenario['scenario_id']}, asynchronous=True)
            job_id = job['job_id']
            deadline = time.monotonic() + 15
            while request(f'/api/v1/jobs/{job_id}')['status'] == 'queued':
                if time.monotonic() > deadline:
                    raise RuntimeError('Solver worker did not start')
                time.sleep(.05)

            def read(index):
                start = time.perf_counter()
                path = '/health' if index % 2 else f"/api/v1/scenarios/{scenario['scenario_id']}/state"
                request(path)
                return (time.perf_counter() - start) * 1000

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                times = list(pool.map(read, range(100)))
            result = {'requests': 100, 'concurrency': 8, 'failures': 0, 'median_ms': round(statistics.median(times), 2), 'p95_ms': round(sorted(times)[94], 2), 'maximum_ms': round(max(times), 2), 'scope': 'Local responsiveness smoke check during a real inspection-ranking job; not a production capacity or SLA claim.'}
            (ROOT / 'reports/responsiveness-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result), flush=True)
        finally:
            if job_id:
                try:
                    if request(f'/api/v1/jobs/{job_id}')['status'] in ('queued', 'running'):
                        request(f'/api/v1/jobs/{job_id}/cancel', {})
                except OSError:
                    pass
            server.terminate()
            server.wait(timeout=15)


if __name__ == '__main__':
    main()
