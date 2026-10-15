# Engineering Decision Log

| ID | Date | Decision | Rationale | Affected Tests |
|---|---|---|---|---|
| DEC-01 | 2026-10-15 | Use OR-Tools `SCIP` backend for integer MIP | Native binary/integer support with fast deterministic solve (<100ms on 40 batches) | `test_mandatory_scenarios_t01_t24.py` |
| DEC-02 | 2026-10-15 | Use integer minutes, integer kg, and integer paise everywhere | Eliminates floating-point rounding drift in ledger and cost reconciliation | `test_units_and_economics.py` |
| DEC-03 | 2026-10-15 | Enforce strict expiry inequality `S_dc + B_dc <= E_b - 1` | Prevents boundary sale of stock expiring at the exact sales instant | `T04` |
| DEC-04 | 2026-10-15 | Separate `unexpired_carryover` from `expired_unsold` at `H` | Prevents inflating spoilage metrics or fabricating terminal salvage revenue | `T21`, Financial Fixture |
| DEC-05 | 2026-10-15 | Secondary lexicographic MIP tie-break scaled `< 0.5` paise | Ensures deterministic trip/batch selection without changing any integer paise objective | Small Exhaustive Oracle |
