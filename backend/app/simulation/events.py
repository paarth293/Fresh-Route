"""Simulation event ordering and helper structures (Section 8.2)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict


# Documented tie-break priority when timestamps are equal (Section 8.2):
# 1. physical arrivals & handling completion
# 2. observations
# 3. sales
# 4. planning/commit actions
# 5. departures
EVENT_PRIORITY = {
    "ARRIVAL_AND_HANDLING": 1,
    "OBSERVATION": 2,
    "SALE_INSTANT": 3,
    "PLAN_ACTION": 4,
    "DEPARTURE": 5,
    "HORIZON_END": 6,
}


@dataclass(order=True)
class ReplayQueueItem:
    timestamp_min: int
    priority: int
    sequence_id: int
    kind: str = field(compare=False)
    payload: Dict[str, Any] = field(compare=False, default_factory=dict)
