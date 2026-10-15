"""Single-host durable queue with one isolated, cancellable solver process per database.

Run one API server process. SQLite stores queued work across restarts; running
jobs are marked interrupted by startup recovery rather than silently retried.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from app.persistence.database import get_connection

log = logging.getLogger(__name__)
_managers = {}
_registry_lock = threading.Lock()


def database_path(db_path=None):
    if db_path is None:
        conn = get_connection()
        try:
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        finally:
            conn.close()
    return str(Path(db_path).resolve())


class LocalQueue:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.process = None
        self.job_id = None
        self.thread = threading.Thread(target=self.run, name="freshroute-job-queue", daemon=True)
        self.thread.start()

    def cancel(self, job_id):
        with self.lock:
            if self.job_id == job_id and self.process and self.process.poll() is None:
                self.process.terminate()
                self.process.wait(timeout=10)
        self.wake.set()

    def close(self):
        self.stopping.set()
        self.wake.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
        self.thread.join(timeout=15)

    def run(self):
        while not self.stopping.is_set():
            try:
                self.run_next()
            except Exception:
                log.exception("Local queue failed to execute work")
            self.wake.wait(0.2)
            self.wake.clear()

    def run_next(self):
        conn = get_connection(self.path)
        try:
            row = conn.execute("SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at, job_id LIMIT 1").fetchone()
        finally:
            conn.close()
        if not row:
            return
        job_id = row["job_id"]
        env = os.environ.copy()
        backend = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = backend + os.pathsep + env.get("PYTHONPATH", "")
        env["FRESHROUTE_DB_PATH"] = self.path
        with self.lock:
            if self.stopping.is_set():
                return
            self.job_id = job_id
            self.process = subprocess.Popen(
                [sys.executable, "-m", "app.jobs.runner", "--job-id", job_id, "--database", self.path, "--parent-pid", str(os.getpid())],
                cwd=backend, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            child = self.process
        child.wait()
        conn = get_connection(self.path)
        try:
            state = "interrupted" if self.stopping.is_set() else "failed"
            conn.execute(
                "UPDATE jobs SET status=?, phase='worker_process_exit', error_code='WORKER_EXITED', error_message='Worker exited before completing. Retry explicitly.' WHERE job_id=? AND status IN ('queued','running')",
                (state, job_id),
            )
        finally:
            conn.close()
        with self.lock:
            self.process = None
            self.job_id = None


def enqueue(db_path=None):
    path = database_path(db_path)
    with _registry_lock:
        manager = _managers.get(path)
        if manager is None:
            manager = _managers[path] = LocalQueue(path)
    manager.wake.set()
    return manager


def cancel_process(job_id, db_path=None):
    path = database_path(db_path)
    with _registry_lock:
        manager = _managers.get(path)
    if manager:
        manager.cancel(job_id)


def shutdown_queue(db_path=None):
    path = database_path(db_path)
    with _registry_lock:
        manager = _managers.pop(path, None)
    if manager:
        manager.close()
