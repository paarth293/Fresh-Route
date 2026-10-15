"""Validate, solve, independently check, and nominally replay every shipped scenario."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.domain.inventory import OperationalStateLedger
from app.planning.mip import solve_deterministic_mip
from app.validation.scenario import validate_scenario_dict
from app.validation.plan import validate_plan_independently
from app.simulation.worlds import generate_simulation_world
from app.simulation.replay import replay_plan_in_world


def main():
    records = []
    for path in sorted((ROOT / "scenarios").rglob("*.json")):
        record = {"file": str(path.relative_to(ROOT)), "passed": False}
        try:
            scenario, errors = validate_scenario_dict(json.loads(path.read_text(encoding="utf-8")))
            if errors:
                raise ValueError(json.dumps(errors))
            snapshot = OperationalStateLedger(scenario).build_planning_snapshot()
            plan = solve_deterministic_mip(snapshot)
            validation = validate_plan_independently(snapshot, plan)
            if not validation.is_valid:
                raise ValueError(json.dumps(validation.model_dump()))
            world = generate_simulation_world(scenario, "audit_nominal", 2026, is_nominal_world=True)
            replay = replay_plan_in_world(scenario, plan, world)
            if not replay.ledger_reconciled:
                raise ValueError("Replay stock did not reconcile")
            if replay.realized_economics.net_outcome_paise != plan.forecast_economics.net_outcome_paise:
                raise ValueError("Nominal replay economics differ from planning forecast")
            record.update(passed=True, solver_status=plan.solver_status.value, trips=len(plan.trips), net_paise=replay.realized_economics.net_outcome_paise)
        except Exception as exc:
            record["error"] = str(exc)
        records.append(record)
        print(json.dumps(record), flush=True)
    (ROOT / "reports" / "scenario-verification.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return 0 if records and all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
