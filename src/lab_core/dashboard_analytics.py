"""Pure, offline presentation adapters for advanced two-condition analytics.

Never import Streamlit or a provider SDK here. Select exact benchmark groups,
delegate calculation to the existing comparison engine, and preserve measured n.
"""

from __future__ import annotations

from typing import Any, Iterable

from lab_core.benchmark_report import build_benchmark
from lab_core.experiment_comparison import MATCH_KEYS, compare_benchmark

GROUP_ID_FIELDS = ("experiment", "source", "scenario", "model", "condition", "cost_basis")

METRIC_LABELS: dict[str, str] = {
    "success_rate": "Client / request success",
    "answer_accuracy": "Labeled answer accuracy",
    "total_tokens": "Mean total tokens",
    "estimated_cost_usd": "Mean estimated cost (USD)",
    "latency_ms": "Mean observed latency (ms)",
    "latency_p50_ms": "Latency p50 (ms)",
    "latency_p95_ms": "Latency p95 (ms)",
    "retry_count": "Mean simulated retries",
    "rate_limit_response_count": "Mean simulated HTTP 429 responses",
    "total_scheduled_wait_seconds": "Mean virtual scheduled wait (s)",
}


# Short presentation-only names and units; the saved output contract stays unchanged.
COMPACT_METRIC_LABELS = {
    "success_rate": "Request success",
    "answer_accuracy": "Answer accuracy",
    "total_tokens": "Mean tokens",
    "estimated_cost_usd": "Mean estimated cost",
    "latency_ms": "Mean latency",
    "latency_p50_ms": "Latency p50",
    "latency_p95_ms": "Latency p95",
    "retry_count": "Mean retries",
    "rate_limit_response_count": "Mean HTTP 429s",
    "total_scheduled_wait_seconds": "Mean virtual wait",
}

COMPACT_METRIC_UNITS = {
    "success_rate": "%",
    "answer_accuracy": "%",
    "total_tokens": "tokens/record",
    "estimated_cost_usd": "USD/record",
    "latency_ms": "ms/record",
    "latency_p50_ms": "ms",
    "latency_p95_ms": "ms",
    "retry_count": "retries/client",
    "rate_limit_response_count": "429s/client",
    "total_scheduled_wait_seconds": "s/client",
}

SIMULATION_METRIC_CATEGORIES = {
    "Reliability and retries": ("success_rate", "retry_count", "rate_limit_response_count"),
    "Virtual wait": ("total_scheduled_wait_seconds",),
}

PROVIDER_METRIC_CATEGORIES = {
    "Success and quality": ("success_rate", "answer_accuracy"),
    "Tokens and estimated cost": ("total_tokens", "estimated_cost_usd"),
    "Latency": ("latency_ms", "latency_p50_ms", "latency_p95_ms"),
}


def metric_categories(comparison: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Available metrics first, then context-specific groups and full audit view.

    A metric is available if at least one condition has an actual observation.
    The all-metrics option deliberately retains unavailable metrics for audit.
    """
    supported = tuple(key for key in METRIC_LABELS if key in comparison["metrics"])
    observed = tuple(
        key for key in supported
        if any(
            comparison["metrics"][key][f"{side}_observed"] > 0
            and comparison["metrics"][key][side] is not None
            for side in ("baseline", "candidate")
        )
    )
    choices = {"Available metrics": observed}
    groups = SIMULATION_METRIC_CATEGORIES if comparison["simulated"] else PROVIDER_METRIC_CATEGORIES
    for category, keys in groups.items():
        present = tuple(key for key in keys if key in observed)
        if present:
            choices[category] = present
    choices["All metrics (including N/A)"] = supported
    return choices


def compact_comparison_rows(
    comparison: dict[str, Any], metric_keys: Iterable[str],
) -> list[dict[str, str]]:
    """Narrow UI-only table preserving denominator coverage and undefined deltas."""
    result = []
    for key in metric_keys:
        if key not in COMPACT_METRIC_LABELS or key not in comparison["metrics"]:
            raise ValueError(f"Unsupported dashboard comparison metric: {key}")
        metric = comparison["metrics"][key]
        is_rate = metric["unit"] == "fraction"
        delta = metric["percentage_point_delta"] if is_rate else metric["absolute_delta"]
        if delta is None:
            delta_label = "N/A"
        else:
            delta_label = ("+" if delta > 0 else "") + format_display_value(delta)
            if is_rate:
                delta_label += " pp"
        relative = metric["relative_delta_percent"]
        relative_label = (
            "N/A" if relative is None
            else ("+" if relative > 0 else "") + format_display_value(relative) + "%"
        )
        result.append({
            "Metric": COMPACT_METRIC_LABELS[key],
            "Unit": COMPACT_METRIC_UNITS[key],
            "Baseline": format_display_value(metric["baseline"], fraction=is_rate),
            "Candidate": format_display_value(metric["candidate"], fraction=is_rate),
            "Delta": delta_label,
            "Relative delta": relative_label,
            "n (B/C)": f"{metric['baseline_observed']} / {metric['candidate_observed']}",
        })
    return result


def available_runs(records: Iterable[dict]) -> list[tuple[str, str]]:
    """Unique modern (lab, run) pairs with saved normalized metrics."""
    return sorted({
        (row["lab"], row["run_id"])
        for row in records if row.get("experiment") != "legacy"
    })


def available_scenarios(records: Iterable[dict], *, lab: str, run_id: str) -> list[str]:
    """One scenario must be selected before benchmarking mixed Lab07 runs."""
    return sorted({
        str(row["scenario"]) for row in records
        if row.get("lab") == lab and row.get("run_id") == run_id
        and row.get("experiment") != "legacy"
    })


def build_scoped_benchmark(
    records: Iterable[dict], *, lab: str, run_id: str, scenario: str,
) -> dict[str, Any]:
    """Aggregate only the user's chosen lab/run/scenario, never mixed runs."""
    if not scenario:
        raise ValueError("Select a specific scenario")
    return build_benchmark(records, lab=lab, run_id=run_id, scenario=scenario)


def matching_contexts(report: dict[str, Any]) -> list[tuple[str, ...]]:
    """Offer only contexts that contain at least two different conditions."""
    contexts: dict[tuple[str, ...], set[str]] = {}
    for group in report["groups"]:
        key = tuple(str(group[field]) for field in MATCH_KEYS)
        contexts.setdefault(key, set()).add(str(group["condition"]))
    return sorted(key for key, conditions in contexts.items() if len(conditions) > 1)


def group_id(group: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(group[field]) for field in GROUP_ID_FIELDS)


def groups_for_context(
    report: dict[str, Any], context: tuple[str, ...],
) -> list[tuple[str, ...]]:
    """Identify individual models and conditions without accidental pooling."""
    if len(context) != len(MATCH_KEYS):
        raise ValueError("Invalid comparison context")
    return sorted(
        group_id(group) for group in report["groups"]
        if tuple(str(group[field]) for field in MATCH_KEYS) == context
    )


def compare_selected(
    report: dict[str, Any],
    baseline_id: tuple[str, ...],
    candidate_id: tuple[str, ...],
    *,
    allow_model_difference: bool = False,
) -> dict[str, Any]:
    """Compare two exact groups, keeping all guardrails in compare_benchmark."""
    groups = {group_id(group): group for group in report["groups"]}
    if baseline_id not in groups or candidate_id not in groups:
        raise ValueError("Chosen condition group is not in the selected saved run")
    if baseline_id == candidate_id:
        raise ValueError("Baseline and candidate must be distinct")
    subset = {**report, "groups": [groups[baseline_id], groups[candidate_id]]}
    return compare_benchmark(
        subset,
        baseline_condition=groups[baseline_id]["condition"],
        candidate_condition=groups[candidate_id]["condition"],
        allow_model_difference=allow_model_difference,
    )


def comparison_rows(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    """Units, n-values and both absolute/relative deltas; no invented numbers."""
    return [
        {
            "Metric": METRIC_LABELS[key],
            "Unit": metric["unit"],
            "Baseline": metric["baseline"],
            "Baseline n": metric["baseline_observed"],
            "Candidate": metric["candidate"],
            "Candidate n": metric["candidate_observed"],
            "Delta": (
                metric["percentage_point_delta"] if metric["unit"] == "fraction"
                else metric["absolute_delta"]
            ),
            "Delta unit": "pp" if metric["unit"] == "fraction" else metric["unit"],
            "Relative delta (%)": metric["relative_delta_percent"],
        }
        for key, metric in comparison["metrics"].items()
        if key in METRIC_LABELS
    ]


def format_display_value(value: float | int | None, *, fraction: bool = False) -> str:
    """Format observed values for the UI, preserving missing values and zero."""
    if value is None:
        return "N/A"
    if fraction:
        return f"{value * 100:,.2f}".rstrip("0").rstrip(".") + "%"
    return f"{value:,.6g}"


def comparison_display_rows(comparison: dict[str, Any]) -> list[dict[str, Any]]:
    """Render-friendly strings, never used for calculations or downloads.

    Stringify all nullable numeric display columns so Streamlit/Arrow does not
    coerce a mixed string-and-numeric column or silently replace missing data.
    Observation counts remain integers.
    """
    result = []
    for row in comparison_rows(comparison):
        is_rate = row["Unit"] == "fraction"
        result.append({
            **row,
            "Unit": "percent (%)" if is_rate else row["Unit"],
            "Baseline": format_display_value(row["Baseline"], fraction=is_rate),
            "Candidate": format_display_value(row["Candidate"], fraction=is_rate),
            "Delta": format_display_value(row["Delta"]),
            "Relative delta (%)": format_display_value(row["Relative delta (%)"]),
        })
    return result


def chart_display_rows(comparison: dict[str, Any], metric_key: str) -> list[dict[str, Any]]:
    """Add visible bar labels without changing chart measurements or observed n."""
    fraction = comparison["metrics"][metric_key]["unit"] == "fraction"
    return [
        {**row, "Value label": format_display_value(row["Observed value"], fraction=fraction)}
        for row in chart_rows(comparison, metric_key)
    ]


def chart_rows(comparison: dict[str, Any], metric_key: str) -> list[dict[str, Any]]:
    """Side-by-side absolute values, preserving measured zero and omitting missing."""
    if metric_key not in METRIC_LABELS:
        raise ValueError("Unsupported comparison chart metric")
    metric = comparison["metrics"][metric_key]
    return [
        {"Group": label, "Observed value": metric[side], "Observed n": metric[f"{side}_observed"]}
        for side, label in (
            ("baseline", "Baseline"), ("candidate", "Candidate")
        )
        if metric[side] is not None and metric[f"{side}_observed"] > 0
    ]
