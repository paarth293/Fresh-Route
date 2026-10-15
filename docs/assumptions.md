# Scope, Operating Model & Modeling Assumptions

## 1. Operating Model Defaults
| Dimension | Default Specification |
|---|---|
| Network | 1 distribution center (`ORIGIN_DC`) and 6–10 regional destinations |
| Inventory | 20–40 batches across 3 perishable commodities (e.g., Tomatoes, Leafy Greens, Strawberries) |
| Vehicles | 3–5 refrigerated/ambient trucks with finite integer kilogram load capacity |
| Routing | Pre-generated single-destination round trips on a configurable departure grid (default 15 min) |
| Planning Horizon | Single dispatch cycle (`0` to `evaluation_end_min = 720`), with event-triggered residual replanning |
| Quantity Unit | Integer kilograms (`kg`) |
| Time Unit | Integer minutes (`min`) relative to scenario `start_at` (`decision_at_min >= 0`) |
| Monetary Unit | Integer `paise` per kilogram (`100 paise = Rs 1.00`) |
| Price Tiers | Finite configured tiers per commodity/destination bucket (0%, 10%, 20% discount) |
| Demand Bucket | At most 1 sales/acceptance bucket per `(destination_id, commodity_id)` pair in Schema v1.0 |

## 2. Sales, Expiry & Horizon Conventions
1. **Absolute Estimated Expiry:** Computed once per observation at `observed_at_min`:
   `estimated_expiry_min = observed_at_min + remaining_life_min`.
   Remaining life never resets during replay or replanning.
2. **Strict Expiry Inequality:** A batch is eligible for sale at `sale_at_min` (`S_dc`) with required freshness buffer `B_dc` if and only if:
   `S_dc + B_dc < E_b` (implemented in integer minutes as `S_dc + B_dc <= E_b - 1`).
   Inventory whose expiry equals `S_dc + B_dc` is strictly ineligible.
3. **Carryover vs. Spoilage at Horizon (`evaluation_end_min`):**
   - **Sold:** Inventory sold up to `realized_sales_demand_by_tier[selected_tier]` at `S_dc` (issued FEFO by true expiry, tie-broken by `batch.id`).
   - **Expired Unsold (Spoiled):** Unsold inventory (at origin or destination) with `true_expiry_min <= evaluation_end_min`. Incurs `disposal_cost_paise_per_unit`.
   - **Unexpired Carryover:** Unsold inventory with `true_expiry_min > evaluation_end_min`. Tracked separately with zero terminal salvage value and zero disposal cost at `evaluation_end_min`.

## 3. Information Boundary & Anti-Leakage Rules
- `PlanningSnapshot` contains only operator-observed estimates at `decision_at_min`.
- `SimulationWorld` contains hidden true expiry (`true_expiry_by_batch`), realized outbound/return travel durations (`realized_travel_by_route`), and realized demand (`realized_demand_by_bucket_tier`).
- Passing any hidden field into `PlanningSnapshot` raises a hard schema validation error (`T24`).
