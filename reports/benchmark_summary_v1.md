# FreshRoute: LastWindow — Reproducible Benchmark & Ablation Report

Partition: **test**. Synthetic fixtures; no claim of field validation or unseen holdout performance.
The organizer_baseline policy is an illustrative FIFO baseline implemented in this repository; it is not an organizer-supplied system.
The route-delay ablation is a pre-dispatch replanning comparison. It does not establish recovery of already dispatched trips.

- **Manifest ID:** `manifest_09aa44f5d2`
- **Solver Backend:** `OR-Tools SCIP`
- **Scenarios Evaluated:** `2` operational instances x `2` worlds (`4` paired runs per policy)
- **Total Runtime:** `39195.82 ms`

## 1. Policy Performance Summary (Paired Realized Replay Outcomes)

| Policy | Mean Realized Net | Mean Spoilage (kg) | Mean Sold (kg) | Mean Carryover (kg) | Fulfillment Rate | Service Failures | Mean Solve (ms) | Net Gain vs Organizer |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `organizer_baseline` | Rs 8,699.68 | 324.0 | 191.0 | 0.0 | 67.0% | 0.75 | 4.8 | +Rs 0.00 |
| `fefo_greedy` | Rs 7,146.52 | 291.2 | 223.8 | 0.0 | 50.5% | 2.50 | 5.4 | -Rs 1,553.15 |
| `nominal_mip` | Rs 6,340.42 | 316.8 | 198.2 | 0.0 | 49.2% | 3.00 | 247.6 | -Rs 2,359.25 |
| `scenario_selected` | Rs 13,130.32 | 222.8 | 292.2 | 0.0 | 85.0% | 0.00 | 1228.7 | +Rs 4,430.65 |
| `inspection_then_scenario_selected` | Rs 13,085.32 | 222.8 | 292.2 | 0.0 | 85.0% | 0.00 | 2253.3 | +Rs 4,385.65 |

## 2. Pre-dispatch Replanning Ablation (+40 min Route Delay)

| Scenario ID | Static Plan Net | Replanned Net | Replanning Gain | Static Failures | Replanned Failures |
|---|---:|---:|---:|---:|---:|
| `test-heldout-001` | Rs 4,483.10 | Rs 11,963.90 | Rs 7,480.80 | 3 | 1 |
| `test-heldout-002` | Rs 6,494.60 | Rs 13,823.55 | Rs 7,328.95 | 3 | 1 |

## 3. Documented Trade-Off & Losing Cases (Transparency Audit)

- **`test-heldout-002` / `eval_w_002` (`scenario_selected` vs `nominal_mip`):** Delta `Rs 5,836.20` — Smallest or neutral margin vs nominal MIP: when realized travel and shelf life closely match nominal estimates, uncertainty buffering adds no additional gain over the nominal MIP.
