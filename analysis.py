#!/usr/bin/env python3
"""
analysis.py

Part 2: Initial Analysis - Data Overview

For every sample, this script:
  1. Sums cell counts across all five populations to get total_count.
  2. Computes each population's relative frequency as a percentage of that
     sample's total_count.

Usage
-----
    python analysis.py

This reads cell_counts.db from the same directory as this script and
writes frequency_table.csv (the tidy relative-frequency table) to the same
directory. It also prints the table to stdout.
"""

import os
import sqlite3
import sys

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "cell_counts.db")
OUTPUT_CSV = os.path.join(SCRIPT_DIR, "frequency_table.csv")


def compute_relative_frequencies(conn):
    totals_query = """
        SELECT s.sample_name AS sample,
               SUM(cc.count)  AS total_count
        FROM samples s
        JOIN cell_counts cc ON cc.sample_id = s.sample_id
        GROUP BY s.sample_name
    """
    totals_df = pd.read_sql_query(totals_query, conn)

    counts_query = """
        SELECT s.sample_name AS sample,
               cc.population  AS population,
               cc.count       AS count
        FROM samples s
        JOIN cell_counts cc ON cc.sample_id = s.sample_id
    """
    counts_df = pd.read_sql_query(counts_query, conn)

    merged = counts_df.merge(totals_df, on="sample", how="left")
    merged["percentage"] = (merged["count"] / merged["total_count"] * 100).round(4)

    result = merged[["sample", "total_count", "population", "count", "percentage"]]
    result = result.sort_values(["sample", "population"]).reset_index(drop=True)
    return result


def main():
    if not os.path.exists(DB_PATH):
        print(f"ERROR: {DB_PATH} not found. Run load_data.py first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    try:
        freq_table = compute_relative_frequencies(conn)
    finally:
        conn.close()

    freq_table.to_csv(OUTPUT_CSV, index=False)

    print(f"Relative frequency table written to: {OUTPUT_CSV}")
    print(f"Rows: {len(freq_table)} (expected: n_samples x 5 populations)")
    print()
    print(freq_table.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
