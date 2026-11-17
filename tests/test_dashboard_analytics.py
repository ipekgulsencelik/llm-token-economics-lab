"""Regression tests for read-only dashboard analytics selection and plotting.

Keep all analytic computations independent of Streamlit and provider SDKs.
"""

from __future__ import annotations

import pytest

from lab_core.dashboard_analytics import (
    GROUP_ID_FIELDS,
    METRIC_LABELS,
    available_runs,
    available_scenarios,
    build_scoped_benchmark,
    chart_display_rows,
    chart_rows,
    compare_selected,
    comparison_display_rows,
    comparison_rows,
    compact_comparison_rows,
    format_display_value,
    metric_categories,
    group_id,
    groups_for_context,
    matching_contexts,
)


def record(**overrides):
    row = {
        "lab": "lab07", "experiment": "lab07_rate_limiting",
        "run_id": "pilot", "source": "raw_measurements.json",
        "scenario": "transient_failures", "model": "Simulation (no LLM)",
        "condition": "no_backoff", "cost_basis": "Unavailable",
        "simulated": True, "succeeded": True, "answer_correct": None,
        "retry_count": 1, "rate_limit_response_count": 1,
        "total_scheduled_wait_seconds": 0.0, "total_tokens": None,
        "estimated_cost_usd": None, "latency_ms": None,
    }
    return {**row, **overrides}


def data():
    return [
        record(succeeded=True, retry_count=1),
        record(succeeded=False, retry_count=2),
        record(condition="exponential_backoff", succeeded=True, retry_count=1,
               total_scheduled_wait_seconds=3.0),
        record(condition="exponential_backoff", succeeded=True, retry_count=1,
               total_scheduled_wait_seconds=5.0),
    ]


def test_run_and_scenario_discovery_does_not_mix_legacy_or_other_runs():
    rows = data() + [
        record(run_id="later", scenario="quota_pressure"),
        record(experiment="legacy", run_id="legacy"),
        record(lab="lab05", experiment="lab05_model_routing", run_id="saved",
               simulated=False),
    ]
    assert available_runs(rows) == [
        ("lab05", "saved"), ("lab07", "later"), ("lab07", "pilot"),
    ]
    assert available_scenarios(rows, lab="lab07", run_id="pilot") == ["transient_failures"]
    assert available_scenarios(rows, lab="lab07", run_id="later") == ["quota_pressure"]


def test_scoped_comparison_keeps_one_run_scenario_and_observed_denominators():
    rows = data() + [
        record(run_id="other", retry_count=900),
        record(scenario="quota_pressure", retry_count=900),
    ]
    report = build_scoped_benchmark(
        rows, lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    assert report["measurement_count"] == 4
    assert report["group_count"] == 2
    contexts = matching_contexts(report)
    assert len(contexts) == 1
    group_options = groups_for_context(report, contexts[0])
    assert len(group_options) == 2
    assert all(len(option) == len(GROUP_ID_FIELDS) for option in group_options)
    assert {option[4] for option in group_options} == {
        "no_backoff", "exponential_backoff",
    }
    left = next(x for x in group_options if x[4] == "no_backoff")
    right = next(x for x in group_options if x[4] == "exponential_backoff")
    compared = compare_selected(report, left, right)
    assert compared["metrics"]["success_rate"]["percentage_point_delta"] == 50
    assert compared["metrics"]["retry_count"]["absolute_delta"] == -0.5
    assert compared["metrics"]["retry_count"]["baseline_observed"] == 2
    assert compared["metrics"]["retry_count"]["candidate_observed"] == 2
    assert compared["metrics"]["total_scheduled_wait_seconds"]["absolute_delta"] == 4
    assert compared["metrics"]["total_scheduled_wait_seconds"]["relative_delta_percent"] is None

    table = comparison_rows(compared)
    assert len(table) == len(METRIC_LABELS)
    by_metric = {row["Metric"]: row for row in table}
    assert by_metric["Client / request success"]["Delta"] == 50
    assert by_metric["Client / request success"]["Delta unit"] == "pp"
    assert by_metric["Mean simulated retries"]["Delta"] == -0.5
    assert by_metric["Mean simulated retries"]["Baseline n"] == 2
    assert by_metric["Mean estimated cost (USD)"]["Baseline"] is None

    values = chart_rows(compared, "total_scheduled_wait_seconds")
    assert values == [
        {"Group": "Baseline", "Observed value": 0.0, "Observed n": 2},
        {"Group": "Candidate", "Observed value": 4.0, "Observed n": 2},
    ]
    assert chart_rows(compared, "estimated_cost_usd") == []


def test_match_context_requires_two_distinct_conditions():
    rows = data()[:2]
    report = build_scoped_benchmark(
        rows, lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    assert matching_contexts(report) == []
    with pytest.raises(ValueError, match="specific scenario"):
        build_scoped_benchmark(rows, lab="lab07", run_id="pilot", scenario="")
    with pytest.raises(ValueError, match="Invalid comparison context"):
        groups_for_context(report, ("only_one",))


def test_separate_sources_and_cost_basis_never_become_matched_context():
    rows = [
        record(condition="baseline", source="a.json", cost_basis="Estimated"),
        record(condition="candidate", source="b.json", cost_basis="Estimated"),
        record(condition="candidate", source="a.json", cost_basis="Batch discounted estimate"),
    ]
    report = build_scoped_benchmark(
        rows, lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    assert matching_contexts(report) == []


def test_selector_rejects_unknown_duplicate_and_unmatched_contexts():
    report = build_scoped_benchmark(
        data(), lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    groups = report["groups"]
    left, right = [group_id(g) for g in groups]
    with pytest.raises(ValueError, match="must be distinct"):
        compare_selected(report, left, left)
    with pytest.raises(ValueError, match="not in the selected saved run"):
        compare_selected(report, left, ("missing",))
    report["groups"][1]["source"] = "another_source.json"
    new_right = group_id(report["groups"][1])
    with pytest.raises(ValueError, match="source differs"):
        compare_selected(report, left, new_right)


def test_exploratory_model_difference_requires_explicit_acknowledgement():
    rows = [
        record(lab="lab05", experiment="lab05_model_routing",
               source="routing_measurements.json", simulated=False,
               model="small", condition="rule", cost_basis="Estimated",
               total_tokens=10, latency_ms=100, estimated_cost_usd=0.01),
        record(lab="lab05", experiment="lab05_model_routing",
               source="routing_measurements.json", simulated=False,
               model="large", condition="cascade", cost_basis="Estimated",
               total_tokens=20, latency_ms=200, estimated_cost_usd=0.02),
    ]
    report = build_scoped_benchmark(rows, lab="lab05", run_id="pilot",
                                    scenario="transient_failures")
    contexts = matching_contexts(report)
    assert len(contexts) == 1
    left, right = groups_for_context(report, contexts[0])
    with pytest.raises(ValueError, match="Model identifiers differ"):
        compare_selected(report, left, right)
    result = compare_selected(report, left, right, allow_model_difference=True)
    assert result["model_difference_acknowledged"]
    assert result["metrics"]["total_tokens"]["absolute_delta"] in (-10, 10)


def test_mixed_validity_preserves_coverage_and_chart_zero():
    rows = data() + [
        record(condition="exponential_backoff", retry_count=None,
               total_scheduled_wait_seconds=None),
    ]
    report = build_scoped_benchmark(
        rows, lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    groups = {g["condition"]: group_id(g) for g in report["groups"]}
    result = compare_selected(report, groups["no_backoff"], groups["exponential_backoff"])
    retry = result["metrics"]["retry_count"]
    assert retry["baseline_observed"] == 2 and retry["candidate_observed"] == 2
    assert result["candidate"]["requests"] == 3
    with pytest.raises(ValueError, match="Unsupported"):
        chart_rows(result, "not_a_metric")


def test_no_direct_streamlit_dependency_in_pure_analytics_layer():
    import sys

    import lab_core.dashboard_analytics as analytics

    assert "streamlit" not in analytics.__dict__
    assert sys.modules.get("lab_core.dashboard_analytics") is analytics


def test_display_table_shows_na_without_changing_numeric_comparison_contract():
    report = build_scoped_benchmark(
        data(), lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    groups = {group["condition"]: group_id(group) for group in report["groups"]}
    compared = compare_selected(
        report, groups["no_backoff"], groups["exponential_backoff"],
    )
    rendered = {row["Metric"]: row for row in comparison_display_rows(compared)}

    assert compared["simulated"] is True
    assert rendered["Mean estimated cost (USD)"]["Baseline"] == "N/A"
    assert rendered["Mean estimated cost (USD)"]["Candidate"] == "N/A"
    assert rendered["Mean estimated cost (USD)"]["Baseline n"] == 0
    assert rendered["Mean virtual scheduled wait (s)"]["Baseline"] == "0"
    assert rendered["Mean virtual scheduled wait (s)"]["Relative delta (%)"] == "N/A"
    assert rendered["Client / request success"]["Baseline"] == "50%"
    assert rendered["Client / request success"]["Candidate"] == "100%"
    assert rendered["Client / request success"]["Delta"] == "50"
    assert rendered["Client / request success"]["Delta unit"] == "pp"
    assert rendered["Client / request success"]["Unit"] == "percent (%)"
    # Calculations, exported JSON and the pre-existing CLI contract stay numeric.
    raw = {row["Metric"]: row for row in comparison_rows(compared)}
    assert raw["Mean estimated cost (USD)"]["Baseline"] is None
    assert raw["Mean virtual scheduled wait (s)"]["Baseline"] == 0
    assert raw["Client / request success"]["Baseline"] == 0.5
    assert compared["metrics"]["total_scheduled_wait_seconds"]["relative_delta_percent"] is None


def test_chart_labels_show_observed_zero_and_never_plot_missing_values():
    report = build_scoped_benchmark(
        data(), lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    groups = {group["condition"]: group_id(group) for group in report["groups"]}
    compared = compare_selected(
        report, groups["no_backoff"], groups["exponential_backoff"],
    )
    assert chart_display_rows(compared, "estimated_cost_usd") == []
    assert chart_display_rows(compared, "total_scheduled_wait_seconds") == [
        {"Group": "Baseline", "Observed value": 0.0, "Observed n": 2, "Value label": "0"},
        {"Group": "Candidate", "Observed value": 4.0, "Observed n": 2, "Value label": "4"},
    ]
    assert chart_display_rows(compared, "success_rate") == [
        {"Group": "Baseline", "Observed value": 0.5, "Observed n": 2, "Value label": "50%"},
        {"Group": "Candidate", "Observed value": 1.0, "Observed n": 2, "Value label": "100%"},
    ]
    assert format_display_value(None) == "N/A"
    assert format_display_value(0) == "0"
    assert format_display_value(0, fraction=True) == "0%"
    assert format_display_value(0.0000012345) != "0"


def test_compact_simulation_categories_are_exclusive_and_auditable():
    report = build_scoped_benchmark(
        data(), lab="lab07", run_id="pilot", scenario="transient_failures",
    )
    ids = {g["condition"]: group_id(g) for g in report["groups"]}
    compared = compare_selected(report, ids["no_backoff"], ids["exponential_backoff"])

    choices = metric_categories(compared)
    assert choices["Available metrics"] == (
        "success_rate", "retry_count", "rate_limit_response_count",
        "total_scheduled_wait_seconds",
    )
    assert choices["Reliability and retries"] == (
        "success_rate", "retry_count", "rate_limit_response_count",
    )
    assert choices["Virtual wait"] == ("total_scheduled_wait_seconds",)
    assert "total_tokens" in choices["All metrics (including N/A)"]
    assert len(choices["All metrics (including N/A)"]) == len(METRIC_LABELS)

    compact = compact_comparison_rows(compared, choices["Available metrics"])
    assert len(compact) == 4
    by_metric = {row["Metric"]: row for row in compact}
    assert by_metric["Request success"] == {
        "Metric": "Request success",
        "Unit": "%",
        "Baseline": "50%",
        "Candidate": "100%",
        "Delta": "+50 pp",
        "Relative delta": "+100%",
        "n (B/C)": "2 / 2",
    }
    assert by_metric["Mean virtual wait"]["Baseline"] == "0"
    assert by_metric["Mean virtual wait"]["Delta"] == "+4"
    assert by_metric["Mean virtual wait"]["Relative delta"] == "N/A"
    assert by_metric["Mean retries"]["Delta"] == "-0.5"

    all_rows = compact_comparison_rows(compared, choices["All metrics (including N/A)"])
    assert any(r["Metric"] == "Mean tokens" and r["Baseline"] == "N/A"
               and r["n (B/C)"] == "0 / 0" for r in all_rows)
    with pytest.raises(ValueError, match="Unsupported dashboard comparison metric"):
        compact_comparison_rows(compared, ("invalid_field",))
    # The underlying comparison remains numeric, unchanged and complete.
    assert compared["metrics"]["total_scheduled_wait_seconds"]["baseline"] == 0
    assert compared["metrics"]["total_scheduled_wait_seconds"]["relative_delta_percent"] is None


def test_compact_real_provider_groups_and_missing_quality():
    rows = [
        record(lab="lab05", experiment="lab05_model_routing",
               source="routing_measurements.json", simulated=False,
               model="model-a", condition="control", cost_basis="Estimated",
               succeeded=True, answer_correct=None, total_tokens=12,
               latency_ms=120, estimated_cost_usd=0.000012,
               retry_count=None, rate_limit_response_count=None,
               total_scheduled_wait_seconds=None),
        record(lab="lab05", experiment="lab05_model_routing",
               source="routing_measurements.json", simulated=False,
               model="model-a", condition="candidate", cost_basis="Estimated",
               succeeded=False, answer_correct=None, total_tokens=15,
               latency_ms=130, estimated_cost_usd=0.000020,
               retry_count=None, rate_limit_response_count=None,
               total_scheduled_wait_seconds=None),
    ]
    report = build_scoped_benchmark(rows, lab="lab05", run_id="pilot",
                                    scenario="transient_failures")
    ids = {g["condition"]: group_id(g) for g in report["groups"]}
    compared = compare_selected(report, ids["control"], ids["candidate"])
    choices = metric_categories(compared)
    assert choices["Success and quality"] == ("success_rate",)
    assert choices["Tokens and estimated cost"] == ("total_tokens", "estimated_cost_usd")
    assert choices["Latency"] == ("latency_ms", "latency_p50_ms", "latency_p95_ms")
    assert "retry_count" not in choices["Available metrics"]
    assert "answer_accuracy" not in choices["Available metrics"]
    assert "answer_accuracy" in choices["All metrics (including N/A)"]

    compact = compact_comparison_rows(compared, choices["Tokens and estimated cost"])
    assert [x["Metric"] for x in compact] == ["Mean tokens", "Mean estimated cost"]
    assert compact[0]["Unit"] == "tokens/record"
    assert compact[0]["n (B/C)"] == "1 / 1"
    quality_row = next(
        row for row in compact_comparison_rows(compared, choices["All metrics (including N/A)"])
        if row["Metric"] == "Answer accuracy"
    )
    assert quality_row["Baseline"] == "N/A"
    assert quality_row["Delta"] == "N/A"
