#!/usr/bin/env python3
"""
dashboard.py

Interactive Streamlit dashboard exposing the Part 2-4 analyses on top of
cell_counts.db. Run via `make dashboard` (equivalently:
`streamlit run dashboard.py`).

Design note: the dashboard reads live from cell_counts.db on every
interaction (queries are cheap at this data scale and cached with
st.cache_data) rather than from the static CSV outputs, so it always
reflects the current state of the database -- consistent with the
single-source-of-truth principle used throughout this project.
"""

import os
import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st
from scipy.stats import mannwhitneyu, ttest_ind
from statsmodels.stats.multitest import multipletests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "cell_counts.db")

POPULATIONS = ["b_cell", "cd8_t_cell", "cd4_t_cell", "nk_cell", "monocyte"]
POP_LABELS = {
    "b_cell": "B cell",
    "cd8_t_cell": "CD8 T cell",
    "cd4_t_cell": "CD4 T cell",
    "nk_cell": "NK cell",
    "monocyte": "Monocyte",
}

st.set_page_config(page_title="Loblaw Bio | Immune Cell Dashboard", layout="wide")


@st.cache_data
def get_connection_data():
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    full = pd.read_sql_query(
        """
        SELECT
            p.project_name  AS project,
            sub.subject_id  AS subject_id,
            sub.subject_name AS subject,
            sub.condition   AS condition,
            sub.age         AS age,
            sub.sex         AS sex,
            sub.treatment   AS treatment,
            sub.response    AS response,
            sa.sample_id    AS sample_id,
            sa.sample_name  AS sample,
            sa.sample_type  AS sample_type,
            sa.time_from_treatment_start AS time_from_treatment_start,
            cc.population   AS population,
            cc.count        AS count
        FROM cell_counts cc
        JOIN samples sa ON sa.sample_id = cc.sample_id
        JOIN subjects sub ON sub.subject_id = sa.subject_id
        JOIN projects p ON p.project_id = sub.project_id
        """,
        conn,
    )
    conn.close()
    totals = full.groupby("sample")["count"].transform("sum")
    full["total_count"] = totals
    full["percentage"] = full["count"] / full["total_count"] * 100
    return full


data = get_connection_data()

st.title("Loblaw Bio — Immune Cell Population Dashboard")
st.caption("Interactive exploration of miraclib trial cell-count data (source: cell_counts.db)")

if data is None:
    st.error(
        f"Database not found at {DB_PATH}. Run `python load_data.py` "
        "(or `make pipeline`) first."
    )
    st.stop()

tab1, tab2, tab3 = st.tabs(
    ["Part 2: Sample Frequencies", "Part 3: Responder vs Non-responder", "Part 4: Baseline Cohort"]
)

# ---------------------------------------------------------------------------
# Part 2: relative frequency table, browsable per sample
# ---------------------------------------------------------------------------
with tab1:
    st.subheader("Relative frequency of each cell population per sample")
    samples = sorted(data["sample"].unique())
    selected_samples = st.multiselect(
        "Filter to specific sample(s) (leave empty to show all)", samples
    )
    freq_view = data[["sample", "total_count", "population", "count", "percentage"]].drop_duplicates()
    if selected_samples:
        freq_view = freq_view[freq_view["sample"].isin(selected_samples)]
    st.dataframe(freq_view.sort_values(["sample", "population"]), use_container_width=True)

    if selected_samples:
        chart_df = freq_view.copy()
        chart_df["Population"] = chart_df["population"].map(POP_LABELS)
        fig = px.bar(
            chart_df, x="Population", y="percentage", color="sample", barmode="group",
            category_orders={"Population": list(POP_LABELS.values())},
        )
        fig.update_layout(title="Cell population percentage by sample")
        fig.update_yaxes(title_text="% of total cells")
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Part 3: responder vs non-responder comparison, with configurable cohort
# ---------------------------------------------------------------------------
with tab2:
    st.subheader("Compare responders vs non-responders")
    col1, col2, col3 = st.columns(3)
    with col1:
        condition_sel = st.selectbox("Condition", sorted(data["condition"].unique()), index=0)
    with col2:
        treatment_sel = st.selectbox("Treatment", sorted(data["treatment"].unique()), index=0)
    with col3:
        sample_type_sel = st.selectbox("Sample type", sorted(data["sample_type"].unique()), index=0)

    cohort = data[
        (data.condition == condition_sel)
        & (data.treatment == treatment_sel)
        & (data.sample_type == sample_type_sel)
        & (data.response.isin(["yes", "no"]))
    ].drop_duplicates(subset=["sample", "population"])

    if cohort.empty:
        st.warning("No samples match this combination of filters.")
    else:
        plot_df = cohort.copy()
        plot_df["Population"] = plot_df["population"].map(POP_LABELS)
        plot_df["Response"] = plot_df["response"].map({"yes": "Responder", "no": "Non-responder"})

        fig = px.box(
            plot_df, x="Population", y="percentage", color="Response",
            color_discrete_map={"Responder": "#3B82F6", "Non-responder": "#EF4444"},
            category_orders={"Population": list(POP_LABELS.values())},
        )
        fig.update_layout(
            title=f"{condition_sel} / {treatment_sel} / {sample_type_sel}: responder vs non-responder",
            legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        )
        fig.update_yaxes(title_text="% of total cells")
        st.plotly_chart(fig, use_container_width=True)

        rows = []
        for pop in POPULATIONS:
            resp = cohort.loc[(cohort.population == pop) & (cohort.response == "yes"), "percentage"]
            nonresp = cohort.loc[(cohort.population == pop) & (cohort.response == "no"), "percentage"]
            if len(resp) < 2 or len(nonresp) < 2:
                continue
            _, u_p = mannwhitneyu(resp, nonresp, alternative="two-sided")
            _, t_p = ttest_ind(resp, nonresp, equal_var=False)
            rows.append({
                "population": POP_LABELS[pop],
                "n_responder": len(resp),
                "n_nonresponder": len(nonresp),
                "mean_responder_pct": round(resp.mean(), 2),
                "mean_nonresponder_pct": round(nonresp.mean(), 2),
                "mannwhitney_p": round(u_p, 5),
                "welch_t_p": round(t_p, 5),
            })
        if rows:
            stats_df = pd.DataFrame(rows)
            stats_df["mannwhitney_p_adj_BH"] = multipletests(stats_df["mannwhitney_p"], method="fdr_bh")[1].round(5)
            stats_df["significant_BH_0.05"] = stats_df["mannwhitney_p_adj_BH"] < 0.05
            st.dataframe(stats_df, use_container_width=True)
            n_sig = stats_df["significant_BH_0.05"].sum()
            if n_sig == 0:
                st.info("No population shows a statistically significant difference after FDR correction.")
            else:
                st.success(f"{n_sig} population(s) significant after FDR correction.")

# ---------------------------------------------------------------------------
# Part 4: baseline cohort explorer
# ---------------------------------------------------------------------------
with tab3:
    st.subheader("Baseline cohort explorer (time_from_treatment_start = 0)")
    col1, col2, col3 = st.columns(3)
    with col1:
        condition_sel2 = st.selectbox("Condition ", sorted(data["condition"].unique()), index=0, key="c2")
    with col2:
        treatment_sel2 = st.selectbox("Treatment ", sorted(data["treatment"].unique()), index=0, key="t2")
    with col3:
        sample_type_sel2 = st.selectbox("Sample type ", sorted(data["sample_type"].unique()), index=0, key="s2")

    baseline = data[
        (data.condition == condition_sel2)
        & (data.treatment == treatment_sel2)
        & (data.sample_type == sample_type_sel2)
        & (data.time_from_treatment_start == 0)
    ].drop_duplicates(subset="sample")

    if baseline.empty:
        st.warning("No baseline samples match this combination of filters.")
    else:
        subjects = baseline.drop_duplicates(subset="subject_id")
        c1, c2, c3 = st.columns(3)
        c1.metric("Samples", baseline["sample"].nunique())
        c2.metric("Subjects", subjects.shape[0])
        c3.metric("Projects", baseline["project"].nunique())

        col1, col2, col3 = st.columns(3)
        with col1:
            st.write("**Samples by project**")
            st.dataframe(baseline.groupby("project")["sample"].nunique().rename("n_samples"))
        with col2:
            st.write("**Subjects by response**")
            st.dataframe(subjects["response"].value_counts(dropna=False).rename("n_subjects"))
        with col3:
            st.write("**Subjects by sex**")
            st.dataframe(subjects["sex"].value_counts(dropna=False).rename("n_subjects"))

        st.write("**Full baseline cohort**")
        st.dataframe(
            baseline[["project", "subject", "sample", "response", "sex", "age"]],
            use_container_width=True,
        )
