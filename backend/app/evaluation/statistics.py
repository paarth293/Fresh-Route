"""Paired statistical aggregation and bootstrap confidence intervals (Section 12.6)."""
from __future__ import annotations
import hashlib
from typing import Any, Dict, List
import numpy as np


def summarize_paired_series(values: List[float], seed: int = 1701, n_boot: int = 400) -> Dict[str, Any]:
    """Compute mean, median, std, min, max, unfavorable count, and 95% bootstrap CI over instance-level values."""
    if not values:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "std": 0.0,
            "min": 0.0,
            "max": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
            "unfavorable_count": 0,
        }

    arr = np.asarray(values, dtype=float)
    n = len(arr)
    mean_v = float(np.mean(arr))
    med_v = float(np.median(arr))
    std_v = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    min_v = float(np.min(arr))
    max_v = float(np.max(arr))
    unfavorable = int(np.sum(arr < 0))

    if n > 1:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=(n_boot, n))
        boot_means = np.mean(arr[idx], axis=1)
        ci_low = float(np.percentile(boot_means, 2.5))
        ci_high = float(np.percentile(boot_means, 97.5))
    else:
        ci_low = mean_v
        ci_high = mean_v

    return {
        "count": n,
        "mean": round(mean_v, 2),
        "median": round(med_v, 2),
        "std": round(std_v, 2),
        "min": round(min_v, 2),
        "max": round(max_v, 2),
        "ci95_low": round(ci_low, 2),
        "ci95_high": round(ci_high, 2),
        "unfavorable_count": unfavorable,
    }


def compute_content_hash(payload_str: str) -> str:
    return hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]
