# Evaluation & Benchmark Protocol

## 1. Policies Compared
1. `organizer_baseline`: Status-quo FIFO full-price dispatch rules.
2. `fefo_greedy`: Competent First-Expiring-First-Out greedy heuristic with local net-contribution checks across allowed price tiers.
3. `nominal_mip`: Deterministic Mixed-Integer Linear Programming (OR-Tools SCIP) optimizer maximizing forecast contribution.
4. `scenario_selected`: Uncertainty-aware policy evaluating candidate plans across assumption profiles (`nominal`, `shorter_life`, `longer_travel`, `lower_demand`, `combined_stress`) on shared planning worlds using `score = (1 - lambda) * mean_net + lambda * min_sampled_net`.
5. `inspection_then_scenario_selected`: Value-of-Information (VoI) Bayesian inspection prioritization followed by conditional posterior replanning.

## 2. Fairness & Exogenous World Coupling
- All policies are evaluated on the exact same pre-generated `SimulationWorld` instances keyed by `(world_id, entity_id, time_bucket, event_type)`.
- Discount demand responses within the same world share a common underlying demand shock multiplier bounded by `accepted_quantity_cap`.
- Zero baseline spoilage (`spoilage_units == 0`) reports `spoilage_reduction_pct = null` (`N/A`) alongside exact absolute kilogram differences (`T20`).
