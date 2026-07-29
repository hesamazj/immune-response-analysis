#!/usr/bin/env python3
"""
stats_analysis.py

Part 3: Statistical Analysis

Answers Bob's question for his colleague Yah D'yada: which immune cell
populations differ between miraclib responders and non-responders in
melanoma patients, using PBMC samples only?

Workflow
--------
1. Pull the tidy relative-frequency table (sample, total_count, population,
   count, percentage) computed the same way as analysis.py (Part 2), but
   joined against subject-level metadata (condition, treatment, response)
   and sample_type so we can filter to the exact cohort Bob cares about:
       condition == 'melanoma'
       treatment == 'miraclib'
       sample_type == 'PBMC'
       response in {'yes', 'no'}
2. For each of the five populations, compare the percentage distributions
   between responders and non-responders using:
     - Mann-Whitney U test (primary; non-parametric, doesn't assume
       normality -- relative-frequency percentages are bounded [0, 100]
       and often skewed, so a t-test's normality assumption is shaky).
     - Welch's t-test (secondary; reported for comparison since many
       biologists expect it, and it doesn't assume equal variances).
3. Apply a Benjamini-Hochberg FDR correction across the 5 population
   p-values (testing 5 hypotheses at once inflates the false-positive
   rate, and this is exactly the situation that correction exists for).
4. Generate a boxplot (per population, responder vs non-responder) so
   Bob has a visual to pair with the stats.

Usage
-----
    python stats_analysis.py

Requires cell_counts.db (built by load_data.py) in the same directory.
Writes:
    stats_results.csv       -- per-population test results table
    responder_boxplots.png  -- boxplot figure
"""

import json
import os
import sqlite3
import sys

import pandas as pd
import plotly.express as px
from scipy.stats import mannwhitneyu, ttest_ind
from statsmodels.stats.multitest import multipletests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "cell_counts.db")
STATS_CSV = os.path.join(SCRIPT_DIR, "stats_results.csv")
BOXPLOT_PNG = os.path.join(SCRIPT_DIR, "responder_boxplots.png")

POPULATIONS = ["b_cell", "cd8_t_cell", "cd4_t_cell", "nk_cell", "monocyte"]
POP_LABELS = {
    "b_cell": "B cell",
    "cd8_t_cell": "CD8 T cell",
    "cd4_t_cell": "CD4 T cell",
    "nk_cell": "NK cell",
    "monocyte": "Monocyte",
}
ALPHA = 0.05


def load_cohort_frequencies(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Returns a tidy DataFrame restricted to melanoma / miraclib / PBMC
    samples with a non-null response, with columns:
        sample, population, count, total_count, percentage, response
    """
    query = """
        SELECT
            sa.sample_name AS sample,
            sub.condition  AS condition,
            sub.treatment  AS treatment,
            sub.response   AS response,
            sa.sample_type AS sample_type,
            cc.population  AS population,
            cc.count       AS count
        FROM samples sa
        JOIN subjects sub ON sub.subject_id = sa.subject_id
        JOIN cell_counts cc ON cc.sample_id = sa.sample_id
        WHERE sub.condition = 'melanoma'
          AND sub.treatment = 'miraclib'
          AND sa.sample_type = 'PBMC'
          AND sub.response IN ('yes', 'no')
    """
    raw = pd.read_sql_query(query, conn)

    totals = raw.groupby("sample")["count"].sum().rename("total_count")
    raw = raw.join(totals, on="sample")
    raw["percentage"] = raw["count"] / raw["total_count"] * 100
    return raw


def run_stat_tests(cohort_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pop in POPULATIONS:
        resp = cohort_df.loc[
            (cohort_df.population == pop) & (cohort_df.response == "yes"), "percentage"
        ]
        nonresp = cohort_df.loc[
            (cohort_df.population == pop) & (cohort_df.response == "no"), "percentage"
        ]

        u_stat, u_p = mannwhitneyu(resp, nonresp, alternative="two-sided")
        t_stat, t_p = ttest_ind(resp, nonresp, equal_var=False)

        rows.append(
            {
                "population": pop,
                "n_responder": len(resp),
                "n_nonresponder": len(nonresp),
                "mean_responder_pct": resp.mean(),
                "mean_nonresponder_pct": nonresp.mean(),
                "mean_diff_resp_minus_nonresp": resp.mean() - nonresp.mean(),
                "mannwhitney_p": u_p,
                "welch_t_p": t_p,
            }
        )

    result = pd.DataFrame(rows)
    result["mannwhitney_p_adj_BH"] = multipletests(
        result["mannwhitney_p"], method="fdr_bh"
    )[1]
    result["welch_t_p_adj_BH"] = multipletests(result["welch_t_p"], method="fdr_bh")[1]
    result["significant_BH_0.05"] = result["mannwhitney_p_adj_BH"] < ALPHA
    return result


def make_boxplot(cohort_df: pd.DataFrame, out_path: str) -> None:
    plot_df = cohort_df.copy()
    plot_df["Population"] = plot_df["population"].map(POP_LABELS)
    plot_df["Response"] = plot_df["response"].map(
        {"yes": "Responder", "no": "Non-responder"}
    )

    fig = px.box(
        plot_df,
        x="Population",
        y="percentage",
        color="Response",
        color_discrete_map={"Responder": "#3B82F6", "Non-responder": "#EF4444"},
        category_orders={"Population": [POP_LABELS[p] for p in POPULATIONS]},
    )
    fig.update_layout(
        title={
            "text": (
                "Cell frequencies: responders vs non-responders"
                "<br><span style='font-size: 16px; font-weight: normal;'>"
                "Melanoma, miraclib, PBMC samples"
                "</span>"
            )
        },
        # Legend placed below the plot (not above) to avoid overlapping the
        # two-line title -- a horizontal legend anchored near y=1.1 collides
        # with subtitle text once the title wraps to a second line.
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    fig.update_xaxes(title_text="Cell population")
    fig.update_yaxes(title_text="% of total cells")

    fig.write_image(out_path, scale=2)
    with open(out_path + ".meta.json", "w") as f:
        json.dump(
            {
                "caption": "Cell population frequencies: responders vs non-responders",
                "description": (
                    "Boxplots comparing relative frequency (%) of five immune cell "
                    "populations between miraclib responders and non-responders in "
                    "melanoma PBMC samples"
                ),
            },
            f,
        )


def main() -> None:
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found. Run load_data.py first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        cohort_df = load_cohort_frequencies(conn)
    finally:
        conn.close()

    if cohort_df.empty:
        print(
            "ERROR: no samples matched melanoma / miraclib / PBMC with a "
            "yes/no response. Check the database contents.",
            file=sys.stderr,
        )
        sys.exit(1)

    stats_df = run_stat_tests(cohort_df)
    stats_df.to_csv(STATS_CSV, index=False)
    make_boxplot(cohort_df, BOXPLOT_PNG)

    print(f"Stats results written to: {STATS_CSV}")
    print(f"Boxplot written to: {BOXPLOT_PNG}")
    print()
    print(stats_df.round(5).to_string(index=False))


if __name__ == "__main__":
    main()
