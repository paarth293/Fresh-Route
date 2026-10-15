"""SQLite persistence layer implementing all 12 tables, immutable planning snapshot persistence,
scenario_version_id + snapshot_hash commit checks, and scoped idempotency conflict detection (Section 9.5 & P0 Audit).
"""
from __future__ import annotations
import datetime
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from app.domain.commitments import apply_operational_event, commit_plan_to_ledger
from app.domain.inventory import OperationalStateLedger
from app.evaluation.statistics import compute_content_hash
from app.schemas.models import (
    CommitmentState,
    JobStatus,
    OperationalEvent,
    PlanningSnapshot,
    PlanResult,
    ScenarioPackage,
)


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS scenario_versions (
    scenario_version_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    scenario_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_states (
    scenario_id TEXT PRIMARY KEY,
    scenario_version_id TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    current_time_min INTEGER NOT NULL,
    ledger_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    state_revision_after INTEGER NOT NULL,
    effective_at_min INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    scenario_id TEXT,
    scenario_version_id TEXT,
    state_revision INTEGER,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    scenario_version_id TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    policy TEXT NOT NULL,
    config_id TEXT NOT NULL,
    solver_status TEXT NOT NULL,
    is_valid INTEGER NOT NULL,
    plan_json TEXT NOT NULL,
    snapshot_json TEXT,
    snapshot_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_trips (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    route_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    departure_min INTEGER NOT NULL,
    total_loaded_units INTEGER NOT NULL,
    fixed_trip_cost_paise INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    commodity_id TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    tier_id TEXT NOT NULL,
    quantity_units INTEGER NOT NULL,
    unit_price_paise INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
    commitment_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_hash TEXT,
    commitment_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inspection_observations (
    observation_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    observed_at_min INTEGER NOT NULL,
    observed_band TEXT NOT NULL,
    observation_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    report_id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_metrics (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    policy TEXT NOT NULL,
    mean_net_paise INTEGER NOT NULL,
    mean_spoilage_units REAL NOT NULL,
    mean_fulfillment_rate REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS run_manifests (
    manifest_id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_db_path() -> str:
    return os.environ.get("FRESHROUTE_DB_PATH", str(Path(__file__).resolve().parents[3] / "data" / "freshroute.sqlite3"))


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000;")
    return conn


def init_db(db_path: str | None = None) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        # Ensure new columns exist if opening an older DB file
        for alter_sql in [
            "ALTER TABLE plans ADD COLUMN snapshot_json TEXT;",
            "ALTER TABLE plans ADD COLUMN snapshot_hash TEXT;",
            "ALTER TABLE commitments ADD COLUMN request_hash TEXT;",
        ]:
            try:
                conn.execute(alter_sql)
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()


def serialize_ledger(ledger: OperationalStateLedger) -> str:
    data = {
        "scenario_version_id": ledger.scenario_version_id,
        "scenario": ledger.scenario.model_dump(),
        "state_revision": ledger.state_revision,
        "current_time_min": ledger.current_time_min,
        "applied_event_ids": sorted(ledger.applied_event_ids),
        "event_log": [e.model_dump() for e in ledger.event_log],
        "inspection_history": [o.model_dump() for o in ledger.inspection_history],
        "posterior_world_weights": ledger.posterior_world_weights,
        "initial_stock": ledger.initial_stock,
        "warehouse_unreserved": ledger.warehouse_unreserved,
        "reserved_stock": ledger.reserved_stock,
        "loading_stock": ledger.loading_stock,
        "in_transit_stock": ledger.in_transit_stock,
        "destination_stock": ledger.destination_stock,
        "sold_stock": ledger.sold_stock,
        "expired_stock": ledger.expired_stock,
        "commitments": {k: v.model_dump() for k, v in ledger.commitments.items()},
        "locked_tier_by_bucket": ledger.locked_tier_by_bucket,
        "sunk_cost_paise": ledger.sunk_cost_paise,
    }
    return json.dumps(data)


def deserialize_ledger(raw_json: str) -> OperationalStateLedger:
    from app.schemas.models import Commitment, InspectionObservation
    data = json.loads(raw_json)
    scen = ScenarioPackage.model_validate(data["scenario"])
    ledger = OperationalStateLedger(scen, scenario_version_id=data["scenario_version_id"])
    ledger.state_revision = int(data["state_revision"])
    ledger.current_time_min = int(data["current_time_min"])
    ledger.applied_event_ids = set(data.get("applied_event_ids", []))
    ledger.event_log = [OperationalEvent.model_validate(x) for x in data.get("event_log", [])]
    ledger.inspection_history = [InspectionObservation.model_validate(x) for x in data.get("inspection_history", [])]
    ledger.posterior_world_weights = data.get("posterior_world_weights")
    ledger.initial_stock = {k: int(v) for k, v in data["initial_stock"].items()}
    ledger.warehouse_unreserved = {k: int(v) for k, v in data["warehouse_unreserved"].items()}
    ledger.reserved_stock = {k: int(v) for k, v in data["reserved_stock"].items()}
    ledger.loading_stock = {k: int(v) for k, v in data["loading_stock"].items()}
    ledger.in_transit_stock = {k: int(v) for k, v in data["in_transit_stock"].items()}
    ledger.destination_stock = {k: int(v) for k, v in data["destination_stock"].items()}
    ledger.sold_stock = {k: int(v) for k, v in data["sold_stock"].items()}
    ledger.expired_stock = {k: int(v) for k, v in data["expired_stock"].items()}
    ledger.commitments = {k: Commitment.model_validate(v) for k, v in data.get("commitments", {}).items()}
    ledger.locked_tier_by_bucket = dict(data.get("locked_tier_by_bucket", {}))
    ledger.sunk_cost_paise = int(data.get("sunk_cost_paise", 0))
    return ledger


def save_scenario_and_initial_state(
    pkg: ScenarioPackage,
    version_number: int | None = None,
    db_path: str | None = None,
) -> Dict[str, Any]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    scen_json = json.dumps(pkg.model_dump(), sort_keys=True)
    chash = compute_content_hash(scen_json)

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        if version_number is None:
            row = conn.execute(
                "SELECT MAX(version_number) AS max_v FROM scenario_versions WHERE scenario_id = ?",
                (pkg.scenario_id,),
            ).fetchone()
            version_number = (int(row["max_v"]) + 1) if (row and row["max_v"] is not None) else 1

        sv_id = f"sv_{pkg.scenario_id}_v{version_number}_{chash[:6]}"
        ledger = OperationalStateLedger(pkg, scenario_version_id=sv_id)

        conn.execute(
            """INSERT OR REPLACE INTO scenario_versions
               (scenario_version_id, scenario_id, version_number, content_hash, scenario_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sv_id, pkg.scenario_id, version_number, chash, scen_json, now),
        )
        conn.execute(
            """INSERT OR REPLACE INTO operational_states
               (scenario_id, scenario_version_id, state_revision, current_time_min, ledger_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pkg.scenario_id, sv_id, ledger.state_revision, ledger.current_time_min, serialize_ledger(ledger), now),
        )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()

    return {
        "scenario_id": pkg.scenario_id,
        "scenario_version_id": sv_id,
        "version_number": version_number,
        "content_hash": chash,
        "state_revision": ledger.state_revision,
    }


def load_operational_ledger(scenario_id: str, db_path: str | None = None) -> OperationalStateLedger:
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT ledger_json FROM operational_states WHERE scenario_id = ?", (scenario_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Scenario '{scenario_id}' not found.")
        return deserialize_ledger(row["ledger_json"])
    finally:
        conn.close()


def save_plan_result(
    scenario_id: str,
    plan: PlanResult,
    snapshot: PlanningSnapshot | None = None,
    db_path: str | None = None,
) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if snapshot is None:
        try:
            ledger = load_operational_ledger(scenario_id, db_path=db_path)
            snapshot = ledger.build_planning_snapshot()
        except Exception:
            snapshot = None

    snap_json = json.dumps(snapshot.model_dump(), sort_keys=True) if snapshot is not None else None
    snap_hash = compute_content_hash(snap_json) if snap_json is not None else None

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """INSERT OR REPLACE INTO plans
               (plan_id, scenario_id, scenario_version_id, state_revision, policy, config_id, solver_status, is_valid, plan_json, snapshot_json, snapshot_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plan.plan_id,
                scenario_id,
                plan.scenario_version_id,
                plan.state_revision,
                plan.policy.value,
                plan.config_id,
                plan.solver_status.value,
                1 if plan.validation.is_valid else 0,
                json.dumps(plan.model_dump()),
                snap_json,
                snap_hash,
                now,
            ),
        )
        for pt in plan.trips:
            conn.execute(
                """INSERT OR REPLACE INTO plan_trips
                   (id, plan_id, trip_id, vehicle_id, route_id, destination_id, departure_min, total_loaded_units, fixed_trip_cost_paise)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{plan.plan_id}_{pt.trip_id}",
                    plan.plan_id,
                    pt.trip_id,
                    pt.vehicle_id,
                    pt.route_id,
                    pt.destination_id,
                    pt.departure_min,
                    pt.total_loaded_units,
                    pt.fixed_trip_cost_paise,
                ),
            )
        for idx, alloc in enumerate(plan.allocations):
            conn.execute(
                """INSERT OR REPLACE INTO allocations
                   (id, plan_id, trip_id, batch_id, commodity_id, destination_id, tier_id, quantity_units, unit_price_paise)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{plan.plan_id}_alloc_{idx}",
                    plan.plan_id,
                    alloc.trip_id,
                    alloc.batch_id,
                    alloc.commodity_id,
                    alloc.destination_id,
                    alloc.tier_id,
                    alloc.quantity_units,
                    alloc.unit_price_paise,
                ),
            )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        conn.close()


def load_plan_result(plan_id: str, db_path: str | None = None) -> Tuple[str, PlanResult]:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT scenario_id, plan_json FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError(f"Plan '{plan_id}' not found.")
        return row["scenario_id"], PlanResult.model_validate(json.loads(row["plan_json"]))
    finally:
        conn.close()


def load_plan_and_snapshot(
    plan_id: str, db_path: str | None = None
) -> Tuple[str, PlanResult, PlanningSnapshot, str | None]:
    """Load a PlanResult together with the immutable PlanningSnapshot that produced it."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT scenario_id, plan_json, snapshot_json, snapshot_hash FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Plan '{plan_id}' not found.")
        scen_id = row["scenario_id"]
        plan = PlanResult.model_validate(json.loads(row["plan_json"]))
        if row["snapshot_json"]:
            snap = PlanningSnapshot.model_validate(json.loads(row["snapshot_json"]))
            shash = row["snapshot_hash"]
        else:
            ledger = load_operational_ledger(scen_id, db_path=db_path)
            snap = ledger.build_planning_snapshot()
            shash = compute_content_hash(json.dumps(snap.model_dump(), sort_keys=True))
        return scen_id, plan, snap, shash
    finally:
        conn.close()


def atomic_commit_plan(
    plan_id: str,
    expected_revision: int,
    idempotency_key: str,
    trip_ids: Optional[List[str]] = None,
    commit_state: CommitmentState = CommitmentState.RESERVED,
    db_path: str | None = None,
) -> Dict[str, Any]:
    """Atomically verify scenario_version_id, state_revision, and snapshot_hash; check idempotency_key payload consistency;
    commit stock/capacity; and increment revision (Test T22 & P0 Version Identity).
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    req_sig = json.dumps(
        {
            "plan_id": plan_id,
            "expected_revision": expected_revision,
            "trip_ids": sorted(trip_ids) if trip_ids else None,
            "commit_state": commit_state.value,
        },
        sort_keys=True,
    )
    req_hash = compute_content_hash(req_sig)

    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        existing = conn.execute(
            "SELECT request_hash, commitment_json FROM commitments WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] and existing["request_hash"] != req_hash:
                conn.execute("ROLLBACK;")
                raise ValueError(
                    f"IDEMPOTENCY_KEY_CONFLICT: idempotency_key '{idempotency_key}' was already used with a different commit request payload."
                )
            conn.execute("COMMIT;")
            return json.loads(existing["commitment_json"])

        plan_row = conn.execute(
            "SELECT scenario_id, scenario_version_id, plan_json, snapshot_hash FROM plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if plan_row is None:
            conn.execute("ROLLBACK;")
            raise KeyError(f"Plan '{plan_id}' not found.")

        scenario_id = plan_row["scenario_id"]
        plan = PlanResult.model_validate(json.loads(plan_row["plan_json"]))

        state_row = conn.execute(
            "SELECT scenario_version_id, state_revision, ledger_json FROM operational_states WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
        if state_row is None:
            conn.execute("ROLLBACK;")
            raise KeyError(f"Scenario '{scenario_id}' state not found.")

        actual_rev = int(state_row["state_revision"])
        actual_sv_id = state_row["scenario_version_id"]

        if plan.scenario_version_id != 'sv_initial' and plan.scenario_version_id != actual_sv_id:
            conn.execute("ROLLBACK;")
            raise ValueError(
                f"STATE_REVISION_CONFLICT: Plan scenario_version_id '{plan.scenario_version_id}' does not match active scenario_version_id '{actual_sv_id}'."
            )

        if actual_rev != expected_revision or plan.state_revision != actual_rev:
            conn.execute("ROLLBACK;")
            raise ValueError(
                f"STATE_REVISION_CONFLICT: expected_revision={expected_revision}, plan_revision={plan.state_revision}, actual_revision={actual_rev}"
            )

        ledger = deserialize_ledger(state_row["ledger_json"])
        current_snap_hash = compute_content_hash(
            json.dumps(ledger.build_planning_snapshot().model_dump(), sort_keys=True)
        )
        if plan_row["snapshot_hash"] and plan_row["snapshot_hash"] != current_snap_hash:
            conn.execute("ROLLBACK;")
            raise ValueError(
                "STATE_REVISION_CONFLICT: Active snapshot hash does not match the snapshot hash that produced the plan."
            )

        updated_ledger = commit_plan_to_ledger(
            ledger=ledger,
            plan=plan,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            trip_ids_to_commit=trip_ids,
            commit_state=commit_state,
        )

        result_payload = {
            "status": "committed",
            "plan_id": plan_id,
            "scenario_id": scenario_id,
            "scenario_version_id": actual_sv_id,
            "prior_revision": actual_rev,
            "new_state_revision": updated_ledger.state_revision,
            "idempotency_key": idempotency_key,
            "committed_trip_count": len(updated_ledger.commitments),
        }

        conn.execute(
            """UPDATE operational_states
               SET state_revision = ?, current_time_min = ?, ledger_json = ?, updated_at = ?
               WHERE scenario_id = ?""",
            (
                updated_ledger.state_revision,
                updated_ledger.current_time_min,
                serialize_ledger(updated_ledger),
                now,
                scenario_id,
            ),
        )
        conn.execute(
            """INSERT INTO commitments
               (commitment_id, scenario_id, plan_id, trip_id, state_revision, idempotency_key, request_hash, commitment_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"c_{uuid.uuid4().hex[:10]}",
                scenario_id,
                plan_id,
                ",".join(trip_ids) if trip_ids else "ALL",
                updated_ledger.state_revision,
                idempotency_key,
                req_hash,
                json.dumps(result_payload),
                now,
            ),
        )
        conn.execute("COMMIT;")
        return result_payload
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def atomic_apply_event(
    scenario_id: str,
    event: OperationalEvent,
    db_path: str | None = None,
) -> OperationalStateLedger:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        state_row = conn.execute(
            "SELECT ledger_json FROM operational_states WHERE scenario_id = ?", (scenario_id,)
        ).fetchone()
        if state_row is None:
            conn.execute("ROLLBACK;")
            raise KeyError(f"Scenario '{scenario_id}' not found.")

        ledger = deserialize_ledger(state_row["ledger_json"])
        already_applied = event.id in ledger.applied_event_ids
        if already_applied:
            prev_ev = next((e for e in ledger.event_log if e.id == event.id), None)
            if prev_ev is not None and (
                prev_ev.event_type != event.event_type
                or prev_ev.entity_id != event.entity_id
                or prev_ev.effective_at_min != event.effective_at_min
                or prev_ev.payload != event.payload
            ):
                conn.execute("ROLLBACK;")
                raise ValueError(
                    f"IDEMPOTENCY_KEY_CONFLICT: event_id '{event.id}' was already used with a different event payload."
                )

        updated = apply_operational_event(ledger, event)

        if not already_applied:
            conn.execute(
                """UPDATE operational_states
                   SET state_revision = ?, current_time_min = ?, ledger_json = ?, updated_at = ?
                   WHERE scenario_id = ?""",
                (updated.state_revision, updated.current_time_min, serialize_ledger(updated), now, scenario_id),
            )
            conn.execute(
                """INSERT OR REPLACE INTO events
                   (event_id, scenario_id, state_revision_after, effective_at_min, event_type, entity_id, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    scenario_id,
                    updated.state_revision,
                    event.effective_at_min,
                    event.event_type.value,
                    event.entity_id,
                    json.dumps(event.payload),
                    now,
                ),
            )
        conn.execute("COMMIT;")
        return updated
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def recover_interrupted_jobs(db_path: str | None = None) -> int:
    """On worker startup/restart, mark any abandoned RUNNING jobs as INTERRUPTED (Test T13)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cur = conn.execute(
            """UPDATE jobs
               SET status = ?, phase = 'interrupted_on_worker_restart', error_code = 'WORKER_INTERRUPTED',
                   error_message = 'Worker restarted while job was running; no operational state was mutated.',
                   updated_at = ?
               WHERE status = ?""",
            (JobStatus.INTERRUPTED.value, now, JobStatus.RUNNING.value),
        )
        count = cur.rowcount
        conn.execute("COMMIT;")
        return count
    finally:
        conn.close()
