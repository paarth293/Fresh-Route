# Canonical Data Contract & Validation Rules (Schema v1.0)

## 1. Scenario Envelope
Every scenario package conforms to `schema_version = "1.0"` and declares:
- `scenario_id`, `name`, `timezone`, `start_at`, `decision_at_min`, `evaluation_end_min`
- `quantity_unit` (`"kg"`), `currency_minor_unit` (`"paise"`), `provenance`
- `commodities`, `batches`, `destinations`, `vehicles`, `routes`, `demand_buckets`
- `cost_model`, `uncertainty_model`, `commitments`

## 2. Fourteen Mandatory Input Checks (Section 6.4)
1. **Unique IDs & Reference Resolution:** Every entity ID is unique within its collection, and all foreign keys (`commodity_id`, `destination_id`, `vehicle_id`, `route_id`, `tier_id`) resolve.
2. **Valid Types & Ranges:** All quantities, capacities, timestamps, and paise amounts are finite integers.
3. **Nonnegative Quantities & Positive Capacities:** Batch quantities `>= 0`; usable vehicle capacities `> 0`.
4. **Observation Timestamps:** `batch.observed_at_min <= decision_at_min`.
5. **Life Estimate Ordering:** When low/high life estimates are supplied, `remaining_life_low_min <= remaining_life_nominal_min <= remaining_life_high_min`.
6. **Monotonic Discount Tiers:** Tier prices are `>= 0` and non-increasing with higher discount percentage.
7. **Forecast Bounded by Acceptance Cap:** For every tier, `0 <= forecast_sales_units <= accepted_quantity_cap`.
8. **Ordered Time Windows:** `receiving_open_min < receiving_close_min` and `available_from_min < available_to_min`.
9. **Cycle Containment:** Every `sale_at_min` and scheduled arrival falls within `[0, evaluation_end_min]`.
10. **Fixed Batch Status Enum:** `status` in `{"available", "quarantined", "committed", "depleted"}`.
11. **Strict Schema Version & Extra-Field Rejection:** Only `schema_version == "1.0"` is accepted; unknown critical fields or leaked hidden-world keys are rejected.
12. **Explicit Route Requirement:** A destination without a defined route has no service (never 0-minute travel).
13. **Upload Safety:** Payloads exceeding size limits or containing server file path directives are rejected.
14. **Single Bucket per Destination–Commodity Pair:** Duplicate `(destination_id, commodity_id)` demand buckets are rejected.
