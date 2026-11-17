"""Streamlit-only, read-only advanced analytics view.

This module only presents local saved measurements; comparison/validation math
lives in independently tested shared code, not Streamlit callbacks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import altair as alt
import streamlit as st

from lab_core.dashboard_analytics import (
    METRIC_LABELS,
    available_runs,
    available_scenarios,
    build_scoped_benchmark,
    chart_display_rows,
    chart_rows,
    compare_selected,
    compact_comparison_rows,
    groups_for_context,
    metric_categories,
    matching_contexts,
)
from lab_core.experiment_comparison import render_markdown
from lab_core.experiment_validation import validate_run


def _group_label(group_id: tuple[str, ...]) -> str:
    """Readable condition label with explicit model, from saved measurements."""
    _, _, _, model, condition, _ = group_id
    return f"{condition} · model: {model}"


def _context_label(context: tuple[str, ...]) -> str:
    experiment, source, scenario, cost_basis = context
    return f"{experiment} · {scenario} · {source} · {cost_basis}"


def render_advanced_analytics(records: list[dict], results_dir: Path) -> None:
    """Render one strictly scoped comparison without ever triggering an LLM."""
    st.subheader("Advanced analytics · matched-condition comparison")
    st.caption(
        "Compare two saved conditions in ONE lab, run, scenario, source and cost basis. "
        "Results are descriptive, not paired-request effects or statistical significance. "
        "This view makes no provider calls."
    )
    run_options = available_runs(records)
    if not run_options:
        st.info("No modern saved experiment run matches the current sidebar filters.")
        return

    lab, run_id = st.selectbox(
        "Lab and saved run", run_options, key="analytics_run",
        format_func=lambda value: f"{value[0].upper()} · {value[1]}",
    )
    scenarios = available_scenarios(records, lab=lab, run_id=run_id)
    if not scenarios:
        st.info("No recorded scenarios found for this run.")
        return
    scenario = st.selectbox(
        "Scenario", scenarios, key="analytics_scenario",
    )
    try:
        report = build_scoped_benchmark(records, lab=lab, run_id=run_id, scenario=scenario)
    except ValueError as exc:
        st.warning(str(exc))
        return

    contexts = matching_contexts(report)
    if not contexts:
        st.info(
            "No matched context contains two different conditions. "
            "Choose a different run/scenario or clear sidebar condition filters."
        )
        return

    context = st.selectbox(
        "Matched measurement context", contexts,
        format_func=_context_label, key="analytics_context",
        help="One experiment family, raw metric source, scenario and cost basis.",
    )
    group_options = groups_for_context(report, context)
    baseline_id = st.selectbox(
        "Baseline condition", group_options, key="analytics_baseline",
        format_func=_group_label,
    )
    candidates = [group for group in group_options if group[4] != baseline_id[4]]
    if not candidates:
        st.info("Choose a baseline with at least one distinct candidate condition.")
        return
    candidate_id = st.selectbox(
        "Candidate condition", candidates, key="analytics_candidate",
        format_func=_group_label,
    )

    different_models = baseline_id[3] != candidate_id[3]
    if different_models:
        pair_key = hashlib.sha256(
            repr((lab, run_id, context, baseline_id, candidate_id)).encode("utf-8")
        ).hexdigest()[:16]
        allow_different_models = st.checkbox(
            "Acknowledge exploratory comparison of different models",
            value=False, key=f"analytics_ack_models_{pair_key}",
            help="This is not evidence of equal prompts, price assumptions or model quality.",
        )
        if not allow_different_models:
            st.warning(
                "These conditions use different model identifiers. "
                "Explicit acknowledgement is required before comparing them."
            )
            return
    else:
        allow_different_models = False

    # Gate the selected saved input on the current offline validator. Never
    # mutate or auto-repair the run; warn on legacy/unversioned input.
    try:
        validation = validate_run(
            results_dir, experiment=context[0], run_id=run_id,
        )
    except ValueError as exc:
        st.error(f"Unable to validate selected saved run: {exc}")
        return
    status = validation.to_report()
    if not status["valid"]:
        st.error(
            "The saved run failed the offline validation checks; "
            "comparisons are blocked until the input is corrected."
        )
        with st.expander("Validation diagnostics", expanded=False):
            st.dataframe(status["diagnostics"], use_container_width=True, hide_index=True)
        return
    if status["warnings"]:
        st.warning(
            f"Input validation passed with {status['warnings']} warning(s) "
            f"(compatibility: {status['compatibility']}). Review the validation report."
        )
    else:
        st.caption(
            f"Input validation: {status['compatibility']} · "
            f"{status['records_checked']} saved records checked · no warnings"
        )

    try:
        comparison = compare_selected(
            report, baseline_id, candidate_id,
            allow_model_difference=allow_different_models,
        )
    except ValueError as exc:
        st.warning(str(exc))
        return

    comparison["validation"] = {
        "compatibility": status["compatibility"],
        "warnings": status["warnings"],
        "files_checked": status["files_checked"],
        "records_checked": status["records_checked"],
    }
    if comparison["simulated"]:
        st.info(
            "OFFLINE SIMULATION · These are synthetic client outcomes and virtual "
            "wait times. No LLM was called. Real token usage, estimated API cost, "
            "observed API latency and answer accuracy are unavailable (N/A)."
        )
    else:
        st.info(
            "RECORDED API MEASUREMENTS · Values come from saved provider runs. "
            "Costs are estimates, not invoices; missing measurements are N/A. "
            "Comparisons are descriptive, not causal."
        )
    st.caption(
        f"Baseline: **{comparison['baseline']['condition']}** "
        f"({comparison['baseline']['requests']} saved records) · "
        f"Candidate: **{comparison['candidate']['condition']}** "
        f"({comparison['candidate']['requests']} saved records)"
    )
    st.markdown("**Measured values and differences**")
    categories = metric_categories(comparison)
    selected_category = st.selectbox(
        "Metric category", list(categories), key="analytics_metric_category",
        help="Choose a focused metric group, or show all metrics including unavailable ones.",
    )
    visible_metrics = categories[selected_category]
    st.dataframe(
        compact_comparison_rows(comparison, visible_metrics),
        use_container_width=True, hide_index=True,
        column_config={
            "Metric": st.column_config.TextColumn("Metric", width="medium"),
            "Unit": st.column_config.TextColumn("Unit", width="small"),
            "Baseline": st.column_config.TextColumn("Baseline", width="small"),
            "Candidate": st.column_config.TextColumn("Candidate", width="small"),
            "Delta": st.column_config.TextColumn("Delta", width="small"),
            "Relative delta": st.column_config.TextColumn("Relative delta", width="small"),
            "n (B/C)": st.column_config.TextColumn("n (B/C)", width="small"),
        },
    )
    st.caption(
        "Delta = candidate − baseline; rate deltas use percentage points (pp). "
        "n (B/C) is the observed count for baseline/candidate, not necessarily "
        "total requests. N/A means missing or undefined, never zero. "
        "The all-metrics category shows unavailable measurements for auditing."
    )

    metrics_with_values = [
        key for key in visible_metrics if chart_rows(comparison, key)
    ]
    if metrics_with_values:
        selected_metric = st.selectbox(
            "Metric to visualize", metrics_with_values,
            format_func=lambda key: METRIC_LABELS[key], key="analytics_chart_metric",
        )
        values = chart_display_rows(comparison, selected_metric)
        metric = comparison["metrics"][selected_metric]
        is_rate = metric["unit"] == "fraction"
        source = alt.InlineData(values=values)
        axis = alt.Axis(format=".0%" if is_rate else "~g")
        x = alt.X(
            "Observed value:Q",
            title="Observed rate" if is_rate else metric["unit"],
            scale=alt.Scale(domain=[0, 1.08] if is_rate else None, zero=True),
            axis=axis,
        )
        y = alt.Y("Group:N", sort=["Baseline", "Candidate"], title=None)
        tooltips = [
            alt.Tooltip("Group:N"),
            alt.Tooltip("Value label:N", title="Observed value"),
            alt.Tooltip("Observed n:Q", title="Measured n"),
        ]
        bars = alt.Chart(source).mark_bar(size=30).encode(
            x=x, y=y, color=alt.Color("Group:N", legend=None), tooltip=tooltips,
        )
        labels = alt.Chart(source).mark_text(
            align="left", baseline="middle", dx=6,
        ).encode(x=x, y=y, text=alt.Text("Value label:N"))
        st.altair_chart(
            (bars + labels).properties(height=150, padding={"right": 65}),
            use_container_width=True,
        )
        st.caption(
            f"Baseline: {comparison['baseline']['condition']} · "
            f"Candidate: {comparison['candidate']['condition']} · "
            f"Unit: {'percent (%)' if is_rate else metric['unit']}. "
            "Bar labels show observed group values, including zero; hover for "
            "measured n. These are descriptive values, not confidence intervals."
        )
    else:
        st.info("No observed numeric metric is available to chart for this comparison.")

    with st.expander("Interpretation and limitations", expanded=False):
        for note in comparison["notes"]:
            st.markdown(f"- {note}")

    json_content = json.dumps(comparison, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    md_content = render_markdown(comparison)
    col_a, col_b = st.columns(2)
    with col_a:
        st.download_button(
            "Download comparison JSON",
            data=json_content.encode("utf-8"),
            file_name="llm-token-economics-comparison.json",
            mime="application/json",
            key="analytics_download_json",
            use_container_width=True,
        )
    with col_b:
        st.download_button(
            "Download comparison Markdown",
            data=md_content.encode("utf-8"),
            file_name="llm-token-economics-comparison.md",
            mime="text/markdown",
            key="analytics_download_md",
            use_container_width=True,
        )
