"""Unit tests for canonical scenario validation (14 checks) and pure event reducer conservation."""
from __future__ import annotations
import copy
import json
import unittest
from pathlib import Path
from app.domain.commitments import apply_operational_event, commit_plan_to_ledger
from app.domain.inventory import OperationalStateLedger
from app.planning.mip import solve_deterministic_mip
from app.schemas.models import CommitmentState, EventType, OperationalEvent
from app.validation.scenario import validate_scenario_dict

SCEN_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scenarios/demo/demo-normal-001.json"


class TestScenarioValidationAndLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_raw = json.loads(SCEN_PATH.read_text())

    def test_valid_scenario_passes_all_14_checks(self) -> None:
        pkg, errs = validate_scenario_dict(self.valid_raw)
        self.assertEqual(errs, [])
        self.assertIsNotNone(pkg)

    def test_malformed_scenarios_rejected_with_field_paths(self) -> None:
        # Bad schema version
        bad1 = copy.deepcopy(self.valid_raw)
        bad1["schema_version"] = "2.0"
        _, errs1 = validate_scenario_dict(bad1)
        self.assertTrue(any(e["error_code"] == "UNSUPPORTED_SCHEMA_VERSION" for e in errs1))

        # Server file path forbidden (Check 13)
        bad2 = copy.deepcopy(self.valid_raw)
        bad2["server_path"] = "/etc/passwd"
        _, errs2 = validate_scenario_dict(bad2)
        self.assertTrue(any(e["error_code"] == "SERVER_PATH_FORBIDDEN" for e in errs2))

        # Unordered life estimates (Check 5)
        bad3 = copy.deepcopy(self.valid_raw)
        bad3["batches"][0]["remaining_life_low_min"] = 500
        bad3["batches"][0]["remaining_life_nominal_min"] = 300
        _, errs3 = validate_scenario_dict(bad3)
        self.assertTrue(any(e["error_code"] == "UNORDERED_LIFE_ESTIMATES" for e in errs3))

        # Discount increases price (Check 6)
        bad4 = copy.deepcopy(self.valid_raw)
        bad4["demand_buckets"][0]["price_tiers"][1]["price_paise_per_unit"] = 99999
        _, errs4 = validate_scenario_dict(bad4)
        self.assertTrue(any(e["error_code"] == "DISCOUNT_INCREASES_PRICE" for e in errs4))

        # Duplicate destination-commodity bucket (Check 14)
        bad5 = copy.deepcopy(self.valid_raw)
        dup_b = copy.deepcopy(bad5["demand_buckets"][0])
        dup_b["id"] = "DB-DUP-99"
        bad5["demand_buckets"].append(dup_b)
        _, errs5 = validate_scenario_dict(bad5)
        self.assertTrue(any(e["error_code"] == "DUPLICATE_DESTINATION_COMMODITY_BUCKET" for e in errs5))

    def test_ledger_conservation_across_commit_and_events(self) -> None:
        pkg, _ = validate_scenario_dict(self.valid_raw)
        assert pkg is not None
        ledger = OperationalStateLedger(pkg)
        self.assertTrue(ledger.verify_conservation())

        plan = solve_deterministic_mip(ledger.build_planning_snapshot())
        ledger2 = commit_plan_to_ledger(
            ledger, plan, expected_revision=1, idempotency_key="k1", commit_state=CommitmentState.RESERVED
        )
        self.assertTrue(ledger2.verify_conservation())
        self.assertEqual(ledger2.state_revision, 2)

        ev = OperationalEvent(
            id="ev_delay_1",
            effective_at_min=60,
            event_type=EventType.ROUTE_DELAY_OBSERVED,
            entity_id="R-NORTH",
            payload={"delay_minutes": 30},
        )
        ledger3 = apply_operational_event(ledger2, ev)
        self.assertTrue(ledger3.verify_conservation())
        self.assertEqual(ledger3.state_revision, 3)


if __name__ == "__main__":
    unittest.main()
