"""Streamlit dashboard for saved experiment artifacts; no provider calls.

Run from repository root:
    uv run --with "streamlit>=1.40,<2" streamlit run dashboard/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from lab_core.dashboard_data import (
    EXPORT_COLUMNS, export_csv, filter_records, scan_results, summarize, summarize_lab07,
)

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"

st.set_page_config(page_title="LLM Token Economics | Dashboard", page_icon="📊", layout="wide")
st.title("LLM Token Economics")
st.caption("Offline experiment explorer · read-only · no provider requests")
st.info(
    "All numbers come from saved local experiment artifacts. "
    "Estimated USD costs are not invoices. Missing measurements are never "
    "treated as zero, and accuracy is shown only when explicitly evaluated.",
    icon="ℹ️",
)

records, warnings = scan_results(RESULTS_DIR)
with st.sidebar:
    st.header("Filters")
    st.caption(f"Results folder: {RESULTS_DIR}")
    if st.button("Refresh saved results", use_container_width=True):
        st.rerun()
    if records:
        labs = st.multiselect("Laboratory", sorted({r["lab"] for r in records}))
        lab_filtered = filter_records(records, labs=labs)
        runs = st.multiselect("Experiment run", sorted({r["run_id"] for r in lab_filtered}))
        run_filtered = filter_records(lab_filtered, runs=runs)
        models = st.multiselect("Model", sorted({r["model"] for r in run_filtered}))
        model_filtered = filter_records(run_filtered, models=models)
        conditions = st.multiselect(
            "Condition / strategy", sorted({r["condition"] for r in model_filtered}),
        )
        condition_filtered = filter_records(model_filtered, conditions=conditions)
        scenarios = st.multiselect(
            "Simulation scenario",
            sorted({r["scenario"] for r in condition_filtered if r["lab"] == "lab07"}),
        )
    else:
        labs, runs, models, conditions, scenarios = [], [], [], [], []

if warnings:
    with st.expander(f"{len(warnings)} unreadable artifact(s)", expanded=False):
        for warning in warnings:
            st.warning(warning)

if not records:
    st.warning("No supported experiment results were found in the local results/ folder.")
    st.markdown(
        "To create an API-free example, run the Lab07 simulator "
        "from the repository root:"
    )
    st.code("uv run lab-cli lab07 all", language="bash")
    st.caption(
        "The dashboard reads local files only. It does not generate "
        "synthetic metrics or run experiments automatically."
    )
    st.stop()

filtered = filter_records(
    records, labs=labs, runs=runs, models=models, conditions=conditions, scenarios=scenarios,
)
if not filtered:
    st.warning("No measurements match these filters. Adjust the sidebar selections.")
    st.stop()

known_cost = [r["estimated_cost_usd"] for r in filtered if r["estimated_cost_usd"] is not None]
known_latency = [r["latency_ms"] for r in filtered if r["latency_ms"] is not None]
known_quality = [r["answer_correct"] for r in filtered if r["answer_correct"] is not None]
known_tokens = [r["total_tokens"] for r in filtered if r["total_tokens"] is not None]
known_success = [r["succeeded"] for r in filtered if r["succeeded"] is not None]

a, b, c, d = st.columns(4)
a.metric("Measurements", f"{len(filtered):,}")
b.metric(
    "Estimated cost (observed)",
    "USD " + f"{sum(known_cost):.6f}" if known_cost else "N/A",
    help=f"Coverage: {len(known_cost)} / {len(filtered)} measurements",
)
c.metric(
    "Mean latency (observed)",
    f"{sum(known_latency)/len(known_latency):,.1f} ms" if known_latency else "N/A",
    help=f"Coverage: {len(known_latency)} / {len(filtered)} measurements",
)
d.metric(
    "Evaluated accuracy",
    f"{sum(known_quality)/len(known_quality)*100:.1f}%" if known_quality else "N/A",
    help=f"Coverage: {len(known_quality)} / {len(filtered)} measurements",
)

st.caption(
    f"Coverage — cost {len(known_cost)}/{len(filtered)}; "
    f"latency {len(known_latency)}/{len(filtered)}; "
    f"quality {len(known_quality)}/{len(filtered)}; "
    f"success {len(known_success)}/{len(filtered)}; "
    f"total tokens {len(known_tokens)}/{len(filtered)}."
)
overview, comparison, detail = st.tabs(
    ["Overview", "Model & condition comparison", "Measurements"]
)

with overview:
    st.subheader("Runs discovered")
    st.dataframe(
        summarize(filtered, group_keys=("lab", "experiment", "run_id")),
        use_container_width=True, hide_index=True,
    )
    st.caption(
        "Totals include known values only; coverage counts show how many "
        "measurements were available. Missing data is never filled in."
    )

with comparison:
    st.subheader("Descriptive comparison")
    st.caption(
        "Different labs, datasets and request conditions are not directly "
        "comparable. Filter to one lab and matched experiment run for "
        "meaningful model comparisons. These charts are not causal claims."
    )
    grouped = summarize(filtered, group_keys=("lab", "run_id", "model", "condition"))
    st.dataframe(grouped, use_container_width=True, hide_index=True)
    charts = (
        ("Estimated cost (known values)", "estimated_cost_usd", "Estimated USD"),
        ("Mean observed latency", "mean_latency_ms", "Mean latency (ms)"),
        ("Known total tokens", "total_tokens", "Total tokens"),
    )
    any_chart = False
    for title, key, y_label in charts:
        data = [
            {
                "Group": " / ".join(
                    (r["lab"], r["run_id"], r["model"], r["condition"])
                ),
                y_label: r[key],
            }
            for r in grouped if r[key] is not None
        ]
        if data:
            any_chart = True
            st.markdown(f"**{title}**")
            st.bar_chart(data, x="Group", y=y_label, horizontal=True)
    simulation = summarize_lab07(filtered)
    if simulation:
        st.subheader("Lab07 — simulated retry strategies")
        st.caption(
            "No LLM or real API requests were made. These virtual-clock "
            "measurements are not model accuracy, API cost, or actual latency."
        )
        st.dataframe(simulation, use_container_width=True, hide_index=True)
        simulation_charts = (
            ("Simulated success rate", "success_rate_percent", "Success (%)"),
            ("Total retries", "total_retries", "Retries"),
            ("Simulated HTTP 429 responses", "total_429_responses", "429 responses"),
            ("Scheduled virtual wait", "total_virtual_wait_seconds", "Virtual wait (seconds)"),
        )
        for title, field, axis_label in simulation_charts:
            chart_data = [
                {
                    "Scenario / strategy": " / ".join(
                        (r["run_id"], r["scenario"], r["strategy"])
                    ),
                    axis_label: r[field],
                }
                for r in simulation if r[field] is not None
            ]
            if chart_data:
                st.markdown(f"**{title}**")
                st.bar_chart(
                    chart_data, x="Scenario / strategy", y=axis_label, horizontal=True,
                )

    if not any_chart and not simulation:
        st.info("These files do not contain numeric values for the comparison charts.")

with detail:
    st.subheader("Normalized measurements")
    st.caption(
        "Only allowlisted metrics are displayed; raw prompts and "
        "model responses are never included."
    )
    st.dataframe(
        [{name: record[name] for name in EXPORT_COLUMNS} for record in filtered],
        use_container_width=True, hide_index=True,
    )
    st.download_button(
        label="Download filtered measurements (CSV)",
        data=export_csv(filtered).encode("utf-8-sig"),
        file_name="llm-token-economics-measurements.csv",
        mime="text/csv",
    )

st.divider()
st.caption(
    "Dashboard MVP — local-only and read-only. Pricing is estimated; "
    "latency definitions differ by laboratory."
)
