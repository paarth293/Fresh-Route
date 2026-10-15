"""FastAPI /api/v1 endpoints implementing Section 9.2–9.4."""
from __future__ import annotations
import datetime
import json
import uuid
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from pathlib import Path
from app.jobs.worker import create_job_record, execute_job_synchronously
from app.persistence.database import (
    atomic_apply_event,
    atomic_commit_plan,
    get_connection,
    init_db,
    load_operational_ledger,
    load_plan_and_snapshot,
    load_plan_result,
    save_scenario_and_initial_state,
)
from app.planning.explanations import (
    build_decision_receipt,
    compare_alternative_destination,
)
from app.schemas.models import (
    CommitmentState,
    JobStatus,
    OperationalEvent,
)
from app.simulation.inspection import execute_simulated_inspection
from app.simulation.worlds import generate_simulation_world
from app.validation.scenario import validate_scenario_dict

router = APIRouter()


def make_error_response(status_code: int, code: str, message: str, details: Dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": f"req_{uuid.uuid4().hex[:8]}",
            }
        },
    )


async def read_object(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    for field in ("scenario_id", "plan_id", "config_id", "policy", "prior_plan_id", "batch_id", "idempotency_key"):
        value = body.get(field)
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise HTTPException(status_code=400, detail=f"{field} must be a nonempty string of at most 256 characters.")
    for field in ("state_revision", "expected_revision", "world_seed", "planning_seed", "base_seed", "world_count"):
        if field in body:
            value = body[field]
            if isinstance(value, bool) or not isinstance(value, int) or abs(value) > 2**63 - 1:
                raise HTTPException(status_code=400, detail=f"{field} must be an integer within the supported range.")
    if "world_count" in body and not 1 <= body["world_count"] <= 100:
        raise HTTPException(status_code=400, detail="world_count must be between 1 and 100.")
    for field in ("scenario_ids", "trip_ids"):
        if field in body and (not isinstance(body[field], list) or not body[field] or len(body[field]) > 100 or any(not isinstance(item, str) or not item for item in body[field])):
            raise HTTPException(status_code=400, detail=f"{field} must be a nonempty list of identifiers (maximum 100).")
    return body


def integer_field(body: Dict[str, Any], field: str, default: int) -> int:
    value = body.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail=f"{field} must be an integer.")
    return value


async def execute_or_enqueue(request: Request, job_id: str):
    if "respond-async" in request.headers.get("prefer", "").lower():
        from app.jobs.queue import enqueue
        enqueue()
        response = get_job(job_id)
        response.status_code = 202
        response.headers["Preference-Applied"] = "respond-async"
        return response
    job = await run_in_threadpool(execute_job_synchronously, job_id)
    return JSONResponse(status_code=202, content=job)


@router.get("/api/v1/demo-scenarios")
def demo_scenarios():
    root = Path(__file__).resolve().parents[3] / "scenarios" / "demo"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.json"))]


@router.post("/api/v1/scenarios/validate")
async def validate_scenario_endpoint(request: Request):
    body = await read_object(request)
    _, errors = validate_scenario_dict(body)
    return {"is_valid": not errors, "errors": errors}


@router.get("/health")
def health_check() -> Dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {
        "status": "ok",
        "database": "ready",
        "worker": "ready",
        "solver_backend": "SCIP",
        "schema_version": "1.0",
    }


@router.post("/api/v1/scenarios", status_code=201)
async def create_scenario(request: Request) -> Response:
    raw_bytes = await request.body()
    try:
        raw_dict = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return make_error_response(400, "INVALID_JSON", "Request body is not valid UTF-8 JSON.")

    pkg, errors = validate_scenario_dict(raw_dict, raw_byte_length=len(raw_bytes))
    if errors or pkg is None:
        return make_error_response(
            422,
            "SCENARIO_VALIDATION_FAILED",
            "Scenario package failed canonical validation checks.",
            {"validation_errors": errors},
        )

    meta = save_scenario_and_initial_state(pkg, version_number=None)
    return JSONResponse(status_code=201, content={**meta, "warnings": []})


@router.get("/api/v1/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> Response:
    try:
        ledger = load_operational_ledger(scenario_id)
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))
    # Never expose hidden simulation truth in scenario endpoints (Section 9.2)
    return JSONResponse(status_code=200, content=ledger.scenario.model_dump())


@router.post("/api/v1/scenarios/{scenario_id}/versions", status_code=201)
async def create_scenario_version(scenario_id: str, request: Request) -> Response:
    raw_bytes = await request.body()
    raw_dict = await read_object(request)
    raw_dict["scenario_id"] = scenario_id
    pkg, errors = validate_scenario_dict(raw_dict, raw_byte_length=len(raw_bytes))
    if errors or pkg is None:
        return make_error_response(422, "SCENARIO_VALIDATION_FAILED", "Invalid edited scenario.", {"validation_errors": errors})

    meta = save_scenario_and_initial_state(pkg, version_number=None)
    return JSONResponse(status_code=201, content=meta)


@router.get("/api/v1/scenarios/{scenario_id}/state")
def get_operational_state(scenario_id: str) -> Response:
    try:
        ledger = load_operational_ledger(scenario_id)
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))
    snap = ledger.build_planning_snapshot()
    return JSONResponse(status_code=200, content=snap.model_dump())


@router.post("/api/v1/planning-jobs", status_code=202)
async def create_planning_job(request: Request) -> Response:
    body = await read_object(request)
    scenario_id = body.get("scenario_id")
    if not scenario_id:
        return make_error_response(400, "MISSING_SCENARIO_ID", "scenario_id is required.")
    try:
        ledger = load_operational_ledger(scenario_id)
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))

    expected_rev = body.get("state_revision")
    if expected_rev is not None and integer_field(body, "state_revision", ledger.state_revision) != ledger.state_revision:
        return make_error_response(
            409,
            "STATE_REVISION_CONFLICT",
            "The operational state changed. Refresh state before requesting a plan.",
            {"expected_revision": expected_rev, "actual_revision": ledger.state_revision},
        )

    job_id = create_job_record(
        job_type="planning",
        request_payload=body,
        scenario_id=scenario_id,
        scenario_version_id=ledger.scenario_version_id,
        state_revision=ledger.state_revision,
    )
    return await execute_or_enqueue(request, job_id)


@router.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> Response:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return make_error_response(404, "JOB_NOT_FOUND", f"Job '{job_id}' not found.")
        return JSONResponse(status_code=200, content=dict(row))
    finally:
        conn.close()


@router.post("/api/v1/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> Response:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return make_error_response(404, "JOB_NOT_FOUND", f"Job '{job_id}' not found.")
        if row["status"] not in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
            return make_error_response(409, "JOB_ALREADY_FINISHED", "Only queued or running jobs can be cancelled.")
        changed = conn.execute(
            "UPDATE jobs SET status = ?, phase = 'cancelled_by_operator', updated_at = ? WHERE job_id = ? AND status IN ('queued','running')",
            (JobStatus.CANCELLED.value, now, job_id),
        )
        if changed.rowcount != 1:
            return make_error_response(409, "JOB_ALREADY_FINISHED", "The job finished before cancellation could be applied.")
        from app.jobs.queue import cancel_process
        cancel_process(job_id)
        updated = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return JSONResponse(status_code=200, content=dict(updated))
    finally:
        conn.close()


@router.get("/api/v1/plans/{plan_id}")
def get_plan(plan_id: str) -> Response:
    try:
        _, plan = load_plan_result(plan_id)
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))
    return JSONResponse(status_code=200, content=plan.model_dump())


@router.get("/api/v1/plans/{plan_id}/receipts/{batch_id}")
def get_batch_receipt(plan_id: str, batch_id: str) -> Response:
    try:
        scen_id, plan, stored_snap, snap_hash = load_plan_and_snapshot(plan_id)
        latest_ledger = load_operational_ledger(scen_id)
        receipt = build_decision_receipt(stored_snap, plan, batch_id)
        payload = receipt.model_dump()
        payload["snapshot_hash"] = snap_hash
        payload["plan_state_revision"] = plan.state_revision
        payload["latest_state_revision"] = latest_ledger.state_revision
        payload["is_stale_vs_current_ledger"] = (
            plan.scenario_version_id != latest_ledger.scenario_version_id
            or plan.state_revision != latest_ledger.state_revision
        )
        return JSONResponse(status_code=200, content=payload)
    except (KeyError, ValueError) as exc:
        return make_error_response(404, "RECEIPT_TARGET_NOT_FOUND", str(exc))


@router.post("/api/v1/plans/{plan_id}/alternatives")
async def create_alternative_comparison(plan_id: str, request: Request) -> Response:
    body = await read_object(request)
    try:
        _, plan, stored_snap, _ = load_plan_and_snapshot(plan_id)
        comp = compare_alternative_destination(
            snapshot=stored_snap,
            base_plan=plan,
            batch_id=body["batch_id"],
            alternative_destination_id=body["alternative_destination_id"],
            forced_quantity_units=body.get("forced_quantity_units"),
        )
        return JSONResponse(status_code=200, content=comp.model_dump())
    except (KeyError, ValueError) as exc:
        return make_error_response(400, "ALTERNATIVE_COMPARISON_ERROR", str(exc))


@router.post("/api/v1/plans/{plan_id}/commit")
async def commit_plan(plan_id: str, request: Request) -> Response:
    body = await read_object(request)
    expected_rev = body.get("expected_revision")
    idem_key = body.get("idempotency_key")
    if expected_rev is None or not idem_key:
        return make_error_response(400, "MISSING_COMMIT_FIELDS", "expected_revision and idempotency_key are required.")
    state_str = body.get("commit_state", CommitmentState.RESERVED.value)
    try:
        res = atomic_commit_plan(
            plan_id=plan_id,
            expected_revision=integer_field(body, "expected_revision", 0),
            idempotency_key=str(idem_key),
            trip_ids=body.get("trip_ids"),
            commit_state=CommitmentState(state_str),
        )
        return JSONResponse(status_code=200, content=res)
    except (ValueError, TypeError) as exc:
        if "STATE_REVISION_CONFLICT" in str(exc):
            return make_error_response(
                409,
                "STATE_REVISION_CONFLICT",
                "The operational state or scenario version changed. Generate a new plan before committing.",
                {"error": str(exc)},
            )
        if "IDEMPOTENCY_KEY_CONFLICT" in str(exc):
            return make_error_response(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                str(exc),
            )
        return make_error_response(400, "COMMIT_REJECTED", str(exc))
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))


@router.post("/api/v1/scenarios/{scenario_id}/events")
async def post_operational_event(scenario_id: str, request: Request) -> Response:
    body = await read_object(request)
    try:
        ev = OperationalEvent.model_validate(body)
        if ev.event_type.value in ("PLAN_COMMITTED", "INSPECTION_EXECUTED"):
            return make_error_response(422, "INTERNAL_EVENT_TYPE", "Use the dedicated commit or inspection endpoint for this event.")
        updated_ledger = atomic_apply_event(scenario_id, ev)
        return JSONResponse(
            status_code=200,
            content={
                "scenario_id": scenario_id,
                "state_revision": updated_ledger.state_revision,
                "current_time_min": updated_ledger.current_time_min,
                "event_id": ev.id,
            },
        )
    except (ValueError, TypeError) as exc:
        if "STATE_REVISION_CONFLICT" in str(exc):
            return make_error_response(409, "STATE_REVISION_CONFLICT", str(exc))
        if "IDEMPOTENCY_KEY_CONFLICT" in str(exc):
            return make_error_response(409, "IDEMPOTENCY_KEY_CONFLICT", str(exc))
        return make_error_response(422, "INVALID_EVENT", str(exc))
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))


@router.post("/api/v1/inspection-jobs", status_code=202)
async def create_inspection_ranking_job(request: Request) -> Response:
    body = await read_object(request)
    scenario_id = body.get("scenario_id")
    if not scenario_id:
        return make_error_response(400, "MISSING_SCENARIO_ID", "scenario_id is required.")
    job_id = create_job_record("inspection_ranking", body, scenario_id=scenario_id)
    return await execute_or_enqueue(request, job_id)


@router.post("/api/v1/scenarios/{scenario_id}/commitments/{commitment_id}/reschedule")
async def reschedule_reservation(scenario_id: str, commitment_id: str, request: Request):
    from app.domain.commitments import next_reserved_departure
    body = await read_object(request)
    try:
        ledger = load_operational_ledger(scenario_id)
        expected = integer_field(body, "expected_revision", -1)
        if expected < 1 or not isinstance(body.get("idempotency_key"), str) or not body["idempotency_key"]:
            return make_error_response(400, "MISSING_RESCHEDULE_FIELDS", "expected_revision and idempotency_key are required.")
        event_id = f"reschedule_{body['idempotency_key']}"
        previous = next((e for e in ledger.event_log if e.id == event_id), None)
        departure = integer_field(body, "departure_min", 0) if "departure_min" in body else previous.payload["departure_min"] if previous else next_reserved_departure(ledger, commitment_id)["departure_min"]
        event = OperationalEvent(id=event_id, event_type="RESERVATION_RESCHEDULED", entity_id=commitment_id, effective_at_min=previous.effective_at_min if previous else ledger.current_time_min, expected_prior_revision=expected, payload={"departure_min": departure})
        updated = atomic_apply_event(scenario_id, event)
        return {"state_revision": updated.state_revision, "departure_min": updated.commitments[commitment_id].departure_min}
    except KeyError as exc:
        return make_error_response(404, "RESERVATION_NOT_FOUND", str(exc))
    except ValueError as exc:
        return make_error_response(409 if "CONFLICT" in str(exc) else 422, "RESCHEDULE_REJECTED", str(exc))


@router.post("/api/v1/scenarios/{scenario_id}/inspections")
async def execute_inspection_endpoint(scenario_id: str, request: Request) -> Response:
    body = await read_object(request)
    batch_id = body.get("batch_id")
    seed = integer_field(body, "world_seed", 1701)
    idem_key = body.get("idempotency_key", f"insp_{scenario_id}_{batch_id}")
    try:
        ledger = load_operational_ledger(scenario_id)
        hidden_w = generate_simulation_world(ledger.scenario, f"hidden_insp_{seed}", seed)
        updated_ledger, obs = execute_simulated_inspection(
            ledger, batch_id=batch_id, hidden_world=hidden_w, idempotency_key=idem_key
        )
        # Persist the full inspection event, including cost, posterior, and history.
        last_ev = next(e for e in updated_ledger.event_log if e.event_type.value == "INSPECTION_EXECUTED" and e.entity_id == batch_id)
        saved_ledger = atomic_apply_event(scenario_id, last_ev)
        return JSONResponse(
            status_code=200,
            content={
                "scenario_id": scenario_id,
                "state_revision": saved_ledger.state_revision,
                "current_time_min": saved_ledger.current_time_min,
                "observation": obs.model_dump(),
            },
        )
    except (KeyError, ValueError) as exc:
        return make_error_response(400, "INSPECTION_ERROR", str(exc))


@router.post("/api/v1/scenarios/{scenario_id}/commitments/{commitment_id}/release")
async def release_reservation(scenario_id: str, commitment_id: str, request: Request):
    body = await read_object(request)
    try:
        ledger = load_operational_ledger(scenario_id)
        key = body.get('idempotency_key')
        if not key or 'expected_revision' not in body:
            return make_error_response(400, 'MISSING_RELEASE_FIELDS', 'expected_revision and idempotency_key are required.')
        event_id = f'release_{key}'
        previous = next((e for e in ledger.event_log if e.id == event_id), None)
        event = OperationalEvent(id=event_id, event_type='RESERVATION_RELEASED', entity_id=commitment_id, effective_at_min=previous.effective_at_min if previous else ledger.current_time_min, expected_prior_revision=integer_field(body, 'expected_revision', -1))
        updated = atomic_apply_event(scenario_id, event)
        return {'state_revision': updated.state_revision, 'cancellation_cost_incurred_paise': updated.scenario.cost_model.cancellation_cost_incurred_paise}
    except KeyError as exc:
        return make_error_response(404, 'RESERVATION_NOT_FOUND', str(exc))
    except ValueError as exc:
        return make_error_response(409 if 'CONFLICT' in str(exc) else 422, 'RELEASE_REJECTED', str(exc))


@router.post("/api/v1/replay-jobs", status_code=202)
async def create_replay_job(request: Request) -> Response:
    body = await read_object(request)
    job_id = create_job_record("replay", body, scenario_id=body.get("scenario_id"))
    return await execute_or_enqueue(request, job_id)


@router.post("/api/v1/benchmark-jobs", status_code=202)
async def create_benchmark_job(request: Request) -> Response:
    body = await read_object(request)
    job_id = create_job_record("benchmark", body, scenario_id=body.get("scenario_id"))
    return await execute_or_enqueue(request, job_id)


@router.get("/api/v1/reports/{report_id}")
def get_report(report_id: str) -> Response:
    conn = get_connection()
    try:
        row = conn.execute("SELECT report_json FROM evaluation_runs WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            return make_error_response(404, "REPORT_NOT_FOUND", f"Report '{report_id}' not found.")
        return JSONResponse(status_code=200, content=json.loads(row["report_json"]))
    finally:
        conn.close()


@router.get("/api/v1/plans/{plan_id}/export")
def export_plan(plan_id: str) -> Response:
    try:
        scen_id, plan = load_plan_result(plan_id)
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))
    export_bundle = {
        "schema_version": "1.0",
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scenario_id": scen_id,
        "plan": plan.model_dump(),
    }
    return JSONResponse(status_code=200, content=export_bundle)


@router.get("/api/v1/plans/{plan_id}/fragility")
def get_plan_fragility(plan_id: str) -> Response:
    from app.planning.fragility import analyze_plan_fragility
    try:
        _, plan, snap, _ = load_plan_and_snapshot(plan_id)
        frag = analyze_plan_fragility(snap, plan)
        return JSONResponse(status_code=200, content=frag)
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))


@router.get("/api/v1/plans/{plan_id}/sensitivity")
def get_plan_sensitivity(plan_id: str) -> Response:
    from app.evaluation.sensitivity import compute_2d_stress_surface, compute_pareto_lambda_curve
    try:
        _, plan, snap, _ = load_plan_and_snapshot(plan_id)
        surface = compute_2d_stress_surface(snap.scenario, plan)
        pareto = compute_pareto_lambda_curve(plan)
        return JSONResponse(status_code=200, content={"stress_surface": surface, "pareto_lambda_curve": pareto})
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))


@router.post("/api/v1/scenarios/{scenario_id}/coldchain-sim")
async def simulate_coldchain_endpoint(scenario_id: str, request: Request) -> Response:
    from app.domain.coldchain import simulate_coldchain_excursion_impact
    body = await read_object(request)
    try:
        ledger = load_operational_ledger(scenario_id)
        res = simulate_coldchain_excursion_impact(
            scenario=ledger.scenario,
            ambient_or_reefer_temp_celsius=float(body.get("temp_celsius", 12.0)),
            excursion_duration_min=int(body.get("duration_min", 60)),
        )
        return JSONResponse(status_code=200, content=res)
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))
    except (ValueError, TypeError) as exc:
        return make_error_response(400, "INVALID_COLDCHAIN_PARAMETERS", str(exc))


@router.get("/api/v1/plans/{plan_id}/copilot")
def get_plan_copilot(plan_id: str) -> Response:
    from app.planning.copilot import generate_copilot_briefing_and_qa
    from app.simulation.inspection import rank_inspections_by_voi
    from app.simulation.replay import replay_plan_in_world
    from app.simulation.worlds import generate_simulation_world
    try:
        _, plan, snapshot, _ = load_plan_and_snapshot(plan_id)
        # A briefing remains tied to the observations that produced this plan.
        brief = generate_copilot_briefing_and_qa(snapshot, plan, None, [])
        return JSONResponse(status_code=200, content=brief)
    except KeyError as exc:
        return make_error_response(404, "PLAN_NOT_FOUND", str(exc))


@router.post("/api/v1/scenarios/{scenario_id}/recovery-options")
async def post_recovery_options(scenario_id: str, request: Request) -> Response:
    from app.planning.recovery import compare_recovery_interventions
    body = await read_object(request) if await request.body() else {}
    try:
        ledger = load_operational_ledger(scenario_id)
        prior_plan_id = body.get("prior_plan_id")
        prior_plan = None
        if prior_plan_id:
            try:
                _, prior_plan = load_plan_result(prior_plan_id)
            except KeyError:
                prior_plan = None
        res = compare_recovery_interventions(
            snapshot=ledger.build_planning_snapshot(),
            prior_plan=prior_plan,
            planning_seed=integer_field(body, "planning_seed", 1701),
        )
        return JSONResponse(status_code=200, content=res)
    except KeyError as exc:
        return make_error_response(404, "SCENARIO_NOT_FOUND", str(exc))
