"""Background job manager and worker execution loop (Section 9.1 & 9.5)."""
from __future__ import annotations
import datetime
import json
import uuid
from typing import Any, Dict
from app.evaluation.benchmark import run_benchmark_suite
from app.persistence.database import (
    get_connection,
    load_operational_ledger,
    load_plan_result,
    load_plan_and_snapshot,
    save_plan_result,
)
from app.planning.baselines import solve_fefo_greedy, solve_organizer_baseline
from app.planning.mip import solve_deterministic_mip
from app.planning.policy_selector import solve_scenario_selected_policy
from app.schemas.models import JobStatus, PolicyName
from app.simulation.inspection import rank_inspections_by_voi
from app.simulation.replay import replay_plan_in_world
from app.simulation.worlds import generate_simulation_world


def create_job_record(
    job_type: str,
    request_payload: Dict[str, Any],
    scenario_id: str | None = None,
    scenario_version_id: str | None = None,
    state_revision: int | None = None,
    status: JobStatus = JobStatus.QUEUED,
    db_path: str | None = None,
) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    job_id = f"job_{job_type}_{uuid.uuid4().hex[:8]}"
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO jobs
               (job_id, job_type, scenario_id, scenario_version_id, state_revision, status, phase, request_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                job_type,
                scenario_id,
                scenario_version_id,
                state_revision,
                status.value,
                "queued",
                json.dumps(request_payload),
                now,
                now,
            ),
        )
    finally:
        conn.close()
    return job_id


def execute_job_synchronously(job_id: str, db_path: str | None = None) -> Dict[str, Any]:
    """Execute a queued job outside any DB transaction, then persist the result."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Job '{job_id}' not found.")
        if row["status"] != JobStatus.QUEUED.value:
            conn.execute("COMMIT")
            return dict(row)
        conn.execute(
            "UPDATE jobs SET status = ?, phase = 'solving', updated_at = ? WHERE job_id = ?",
            (JobStatus.RUNNING.value, now, job_id),
        )
        job_type = row["job_type"]
        scenario_id = row["scenario_id"]
        req = json.loads(row["request_json"])
        conn.execute("COMMIT")
    finally:
        conn.close()

    try:
        result_id: str | None = None
        if job_type == "planning":
            ledger = load_operational_ledger(scenario_id, db_path=db_path)
            snapshot = ledger.build_planning_snapshot()
            if row["state_revision"] is not None and row["state_revision"] != snapshot.state_revision:
                raise ValueError("STATE_REVISION_CONFLICT: State changed before job execution.")
            if row["scenario_version_id"] and row["scenario_version_id"] != snapshot.scenario_version_id:
                raise ValueError("STATE_REVISION_CONFLICT: Scenario version changed before job execution.")
            policy_str = req.get("policy", PolicyName.SCENARIO_SELECTED.value)
            config_id = req.get("config_id", "balanced_v1")
            seed = int(req.get("planning_seed", 1701))

            valid_policies = {p.value for p in PolicyName}
            if policy_str not in valid_policies:
                raise ValueError(f"INVALID_POLICY: Unknown policy '{policy_str}'.")
            from app.planning.policy_selector import resolve_planning_config
            resolve_planning_config(config_id)

            if policy_str == PolicyName.ORGANIZER_BASELINE.value:
                plan = solve_organizer_baseline(snapshot, config_id=config_id)
            elif policy_str == PolicyName.FEFO_GREEDY.value:
                plan = solve_fefo_greedy(snapshot, config_id=config_id)
            elif policy_str == PolicyName.NOMINAL_MIP.value:
                plan = solve_deterministic_mip(snapshot, config_id=config_id)
            elif policy_str == PolicyName.INSPECTION_THEN_SCENARIO_SELECTED.value:
                raise ValueError("INSPECTION_REQUIRES_EXPLICIT_ACTION: Rank and execute an inspection first, then request scenario_selected. Planning must not silently mutate or invent observations.")
            else:
                plan = solve_scenario_selected_policy(snapshot, config_id=config_id, planning_seed=seed)

            save_plan_result(scenario_id, plan, snapshot=snapshot, db_path=db_path)
            result_id = plan.plan_id

        elif job_type == "inspection_ranking":
            ledger = load_operational_ledger(scenario_id, db_path=db_path)
            seed = int(req.get("planning_seed", 1701))
            rankings = rank_inspections_by_voi(ledger, planning_seed=seed)
            result_id = f"insp_rank_{uuid.uuid4().hex[:8]}"
            conn = get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO evaluation_runs (report_id, manifest_id, report_json, created_at) VALUES (?, ?, ?, ?)",
                    (result_id, "inspection_voi", json.dumps([r.model_dump() for r in rankings]), now),
                )
            finally:
                conn.close()

        elif job_type == "replay":
            plan_id = req["plan_id"]
            seed = int(req.get("world_seed", 2026))
            scen_id, plan, snapshot, _ = load_plan_and_snapshot(plan_id, db_path=db_path)
            world = generate_simulation_world(snapshot.scenario, f"replay_w_{seed}", seed, is_nominal_world=False)
            rep = replay_plan_in_world(
                snapshot.scenario, plan, world, sunk_committed_cost_paise=snapshot.sunk_cost_paise,
                inspection_cost_paise=sum(o.cost_paise for o in snapshot.inspection_history)
            )
            result_id = f"replay_{uuid.uuid4().hex[:8]}"
            conn = get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO evaluation_runs (report_id, manifest_id, report_json, created_at) VALUES (?, ?, ?, ?)",
                    (result_id, world.world_id, json.dumps(rep.model_dump()), now),
                )
            finally:
                conn.close()

        elif job_type == "benchmark":
            scen_ids = req.get("scenario_ids", [scenario_id] if scenario_id else [])
            scenarios = [load_operational_ledger(sid, db_path=db_path).scenario for sid in scen_ids]
            report = run_benchmark_suite(
                scenarios=scenarios,
                world_count_per_scenario=int(req.get("world_count", 5)),
                base_seed=int(req.get("base_seed", 2026)),
            )
            result_id = report["report_id"]
            conn = get_connection(db_path)
            try:
                conn.execute(
                    "INSERT INTO run_manifests (manifest_id, manifest_json, created_at) VALUES (?, ?, ?)",
                    (report["manifest"]["manifest_id"], json.dumps(report["manifest"]), now),
                )
                conn.execute(
                    "INSERT INTO evaluation_runs (report_id, manifest_id, report_json, created_at) VALUES (?, ?, ?, ?)",
                    (result_id, report["manifest"]["manifest_id"], json.dumps(report), now),
                )
            finally:
                conn.close()

        else:
            raise ValueError(f"UNKNOWN_JOB_TYPE: {job_type}")

        done_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = get_connection(db_path)
        try:
            # Respect cooperative cancellation if requested during execution
            cur_row = conn.execute("SELECT status FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if cur_row and cur_row["status"] == JobStatus.RUNNING.value:
                conn.execute(
                    "UPDATE jobs SET status = ?, phase = 'completed', result_id = ?, updated_at = ? WHERE job_id = ?",
                    (JobStatus.SUCCEEDED.value, result_id, done_time, job_id),
                )
            return dict(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())
        finally:
            conn.close()

    except Exception as exc:
        err_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = get_connection(db_path)
        try:
            conn.execute(
                """UPDATE jobs
                   SET status = ?, phase = 'failed', error_code = 'JOB_EXECUTION_ERROR', error_message = ?, updated_at = ?
                   WHERE job_id = ? AND status = 'running'""",
                (JobStatus.FAILED.value, str(exc), err_time, job_id),
            )
            return dict(conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone())
        finally:
            conn.close()
