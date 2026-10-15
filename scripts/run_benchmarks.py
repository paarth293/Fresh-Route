"""Run frozen benchmark evaluation and write reports to reports/ (Step 19 & 22)."""
import json
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.domain.units import paise_to_rupees_str
from app.evaluation.benchmark import run_benchmark_suite
from app.schemas.models import ScenarioPackage

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one scenario partition without mixing development and test data.")
    parser.add_argument("--partition", choices=["demo", "development", "validation", "test"], default="test")
    parser.add_argument("--world-count", type=int, default=6)
    args = parser.parse_args()
    if args.world_count < 1:
        parser.error("--world-count must be positive")
    scenario_paths = sorted((ROOT / "scenarios" / args.partition).glob("*.json"))
    scenarios = [ScenarioPackage.model_validate(json.loads(p.read_text())) for p in scenario_paths]
    report = run_benchmark_suite(scenarios=scenarios, world_count_per_scenario=args.world_count, base_seed=2026)
    report["manifest"]["partition"] = args.partition
    report["manifest"]["evidence_scope"] = "Synthetic fixtures; test partition has been inspected during development and is no longer an unseen holdout."

    (ROOT / "reports/benchmark_manifest_v1.json").write_text(json.dumps(report["manifest"], indent=2))
    (ROOT / "reports/benchmark_summary_v1.json").write_text(json.dumps(report, indent=2))

    # Also generate human-readable markdown report
    lines = [
        "# FreshRoute: LastWindow — Reproducible Benchmark & Ablation Report",
        "",
        f"Partition: **{args.partition}**. Synthetic fixtures; no claim of field validation or unseen holdout performance.",
        "The organizer_baseline policy is an illustrative FIFO baseline implemented in this repository; it is not an organizer-supplied system.",
        "The route-delay ablation is a pre-dispatch replanning comparison. It does not establish recovery of already dispatched trips.",
        "",
        f"- **Manifest ID:** `{report['manifest']['manifest_id']}`",
        f"- **Solver Backend:** `{report['manifest']['solver_backend']}`",
        f"- **Scenarios Evaluated:** `{report['manifest']['scenario_count']}` operational instances x `{report['manifest']['world_count_per_scenario']}` worlds (`{report['manifest']['total_replay_runs_per_policy']}` paired runs per policy)",
        f"- **Total Runtime:** `{report['manifest']['total_elapsed_ms']} ms`",
        "",
        "## 1. Policy Performance Summary (Paired Realized Replay Outcomes)",
        "",
        "| Policy | Mean Realized Net | Mean Spoilage (kg) | Mean Sold (kg) | Mean Carryover (kg) | Fulfillment Rate | Service Failures | Mean Solve (ms) | Net Gain vs Organizer |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p_key, p_sum in report["policy_summaries"].items():
        net_str = paise_to_rupees_str(p_sum["mean_net_paise"])
        delta_p = int(round(p_sum["paired_net_delta_vs_organizer_stats"]["mean"]))
        delta_str = ("+" if delta_p >= 0 else "") + paise_to_rupees_str(delta_p)
        lines.append(
            f"| `{p_key}` | {net_str} | {p_sum['mean_spoilage_units']:.1f} | {p_sum['mean_sold_units']:.1f} | "
            f"{p_sum['mean_carryover_units']:.1f} | {p_sum['mean_fulfillment_rate']*100:.1f}% | "
            f"{p_sum['mean_service_failures']:.2f} | {p_sum['mean_solve_time_ms']:.1f} | {delta_str} |"
        )

    lines.extend(
        [
            "",
            "## 2. Pre-dispatch Replanning Ablation (+40 min Route Delay)",
            "",
            "| Scenario ID | Static Plan Net | Replanned Net | Replanning Gain | Static Failures | Replanned Failures |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for ab in report["ablation_records"]:
        lines.append(
            f"| `{ab['scenario_id']}` | {paise_to_rupees_str(ab['static_net_paise'])} | "
            f"{paise_to_rupees_str(ab['replanned_net_paise'])} | "
            f"{paise_to_rupees_str(ab['replanning_gain_paise'])} | "
            f"{ab['static_service_failures']} | {ab['replanned_service_failures']} |"
        )

    lines.extend(
        [
            "",
            "## 3. Documented Trade-Off & Losing Cases (Transparency Audit)",
            "",
        ]
    )
    for lc in report["losing_and_tradeoff_cases"][:5]:
        lines.append(
            f"- **`{lc['scenario_id']}` / `{lc['world_id']}` (`{lc['candidate_policy']}` vs `{lc['comparison_policy']}`):** "
            f"Delta `{paise_to_rupees_str(lc['net_delta_paise'])}` — {lc['explanation']}"
        )

    (ROOT / "reports/benchmark_summary_v1.md").write_text("\n".join(lines) + "\n")
    print("Benchmark suite completed and saved to reports/benchmark_summary_v1.json and .md")


if __name__ == "__main__":
    main()
