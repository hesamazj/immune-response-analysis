#!/usr/bin/env python3
"""
load_data.py

Schema
------
projects        (project_id PK, project_name UNIQUE)
subjects        (subject_id PK, project_id FK, subject_name, condition,
                  age, sex, treatment, response)
samples         (sample_id PK, subject_id FK, sample_name UNIQUE, sample_type,
                  time_from_treatment_start)
cell_counts     (id PK, sample_id FK, population, count)

Design notes
------------
- `subjects` holds attributes that are constant per subject (condition, age,
  sex, treatment, response) rather than repeating them once per sample row,
  eliminating redundancy present in the flat CSV.
- `samples` holds attributes that vary per sample (sample_type,
  time_from_treatment_start).
- `cell_counts` is stored in long/tidy format (one row per sample x
  population) rather than one column per population. This makes the schema
  extensible -- new immune populations can be added without altering table
  structure -- and simplifies aggregate queries (e.g. relative frequencies)
  that Bob will need downstream.
- Foreign keys enforce referential integrity between the four tables.

Usage
-----
    python load_data.py

This creates (or overwrites) `cell_counts.db` in the same directory as this
script, using `cell-count.csv` (expected in the same directory) as the data
source.
"""

# NOTE: This script is the single point of ingestion from CSV into SQLite.
# All downstream analysis (see analysis.py) reads exclusively from the
# resulting cell_counts.db, never from the raw CSV again. This keeps one
# source of truth: if a count is corrected here, every analysis picks it up
# automatically instead of drifting out of sync with a stale CSV copy.

import csv
import os
import sqlite3
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(SCRIPT_DIR, "cell-count.csv")
DB_PATH = os.path.join(SCRIPT_DIR, "cell_counts.db")

POPULATIONS = ["b_cell", "cd8_t_cell", "cd4_t_cell", "nk_cell", "monocyte"]

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS cell_counts;
DROP TABLE IF EXISTS samples;
DROP TABLE IF EXISTS subjects;
DROP TABLE IF EXISTS projects;

CREATE TABLE projects (
    project_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name    TEXT NOT NULL UNIQUE
);

CREATE TABLE subjects (
    subject_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(project_id),
    subject_name    TEXT NOT NULL,
    condition       TEXT NOT NULL,
    age             INTEGER,
    sex             TEXT,
    treatment       TEXT,
    response        TEXT,
    UNIQUE (project_id, subject_name)
);

CREATE TABLE samples (
    sample_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id                 INTEGER NOT NULL REFERENCES subjects(subject_id),
    sample_name                TEXT NOT NULL UNIQUE,
    sample_type                TEXT NOT NULL,
    time_from_treatment_start  INTEGER
);

CREATE TABLE cell_counts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id   INTEGER NOT NULL REFERENCES samples(sample_id),
    population  TEXT NOT NULL,
    count       INTEGER NOT NULL,
    UNIQUE (sample_id, population)
);

CREATE INDEX idx_subjects_project ON subjects(project_id);
CREATE INDEX idx_samples_subject ON samples(subject_id);
CREATE INDEX idx_cellcounts_sample ON cell_counts(sample_id);
CREATE INDEX idx_cellcounts_population ON cell_counts(population);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def load_csv(conn: sqlite3.Connection, csv_path: str) -> None:
    cur = conn.cursor()

    project_cache = {}
    subject_cache = {}

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        project_name = row["project"]
        if project_name not in project_cache:
            cur.execute(
                "INSERT OR IGNORE INTO projects (project_name) VALUES (?)",
                (project_name,),
            )
            cur.execute(
                "SELECT project_id FROM projects WHERE project_name = ?",
                (project_name,),
            )
            project_cache[project_name] = cur.fetchone()[0]
        project_id = project_cache[project_name]

        subject_key = (project_id, row["subject"])
        if subject_key not in subject_cache:
            cur.execute(
                """
                INSERT OR IGNORE INTO subjects
                    (project_id, subject_name, condition, age, sex, treatment, response)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    row["subject"],
                    row["condition"],
                    int(row["age"]) if row["age"] else None,
                    row["sex"],
                    row["treatment"],
                    row["response"] if row["response"] else None,
                ),
            )
            cur.execute(
                "SELECT subject_id FROM subjects WHERE project_id = ? AND subject_name = ?",
                (project_id, row["subject"]),
            )
            subject_cache[subject_key] = cur.fetchone()[0]
        subject_id = subject_cache[subject_key]

        cur.execute(
            """
            INSERT OR IGNORE INTO samples
                (subject_id, sample_name, sample_type, time_from_treatment_start)
            VALUES (?, ?, ?, ?)
            """,
            (
                subject_id,
                row["sample"],
                row["sample_type"],
                int(row["time_from_treatment_start"])
                if row["time_from_treatment_start"] != ""
                else None,
            ),
        )
        cur.execute(
            "SELECT sample_id FROM samples WHERE sample_name = ?",
            (row["sample"],),
        )
        sample_id = cur.fetchone()[0]

        for population in POPULATIONS:
            cur.execute(
                """
                INSERT OR IGNORE INTO cell_counts (sample_id, population, count)
                VALUES (?, ?, ?)
                """,
                (sample_id, population, int(row[population])),
            )

    conn.commit()


def main() -> None:
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: could not find {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        load_csv(conn, CSV_PATH)

        cur = conn.cursor()
        n_projects = cur.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        n_subjects = cur.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
        n_samples = cur.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        n_counts = cur.execute("SELECT COUNT(*) FROM cell_counts").fetchone()[0]

        print(f"Database created at: {DB_PATH}")
        print(f"  projects:    {n_projects}")
        print(f"  subjects:    {n_subjects}")
        print(f"  samples:     {n_samples}")
        print(f"  cell_counts: {n_counts}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
