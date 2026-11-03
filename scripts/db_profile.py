"""Profile the education_digitization.sqlite database: schema, volume, distributions, quality."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    hr("FILE / STORAGE")
    print("path        :", DB)
    print("size bytes  :", DB.stat().st_size)
    print("size MiB    : %.1f" % (DB.stat().st_size / 1024 / 1024))
    print("sqlite ver  :", sqlite3.sqlite_version)
    for pragma in ("page_size", "page_count", "freelist_count", "encoding",
                   "journal_mode", "auto_vacuum", "user_version", "application_id"):
        print(f"{pragma:12}:", con.execute(f"PRAGMA {pragma}").fetchone()[0])

    hr("OBJECTS")
    for r in con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ):
        print(f"[{r['type']:6}] {r['name']:28} -> {r['tbl_name']}")

    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("tables:", tables)

    for t in tables:
        hr(f"TABLE {t}")
        cols = con.execute(f"PRAGMA table_info({t})").fetchall()
        print("-- columns --")
        for c in cols:
            print(f"  {c['cid']:>2} {c['name']:<22} {c['type']:<8} "
                  f"notnull={c['notnull']} pk={c['pk']} dflt={c['dflt_value']}")
        print("-- indexes --")
        for r in con.execute(f"PRAGMA index_list({t})"):
            icols = [x[2] for x in con.execute(f"PRAGMA index_info({r['name']})")]
            print(f"  {r['name']:28} unique={r['unique']} cols={icols}")
        print("-- foreign keys --")
        fks = con.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        print("  none" if not fks else "")
        for fk in fks:
            print("  ", dict(fk))

        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"-- row count: {n:,} --")

        print("-- sample rows (3) --")
        for r in con.execute(f"SELECT * FROM {t} LIMIT 3"):
            print("  ", dict(r))

        print("-- null / distinct per column --")
        for c in cols:
            col = c["name"]
            q = (f"SELECT SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS nulls, "
                 f"COUNT(DISTINCT {col}) AS ndv, COUNT(*) AS n FROM {t}")
            row = con.execute(q).fetchone()
            print(f"  {col:<22} nulls={row['nulls']:>12,} distinct={row['ndv']:>10,} n={row['n']:>12,}")

    # ---- domain-specific profiling ----
    hr("QUESTIONS: hierarchy + weights")
    for row in con.execute(
        "SELECT level1_name, COUNT(*) n, "
        "COUNT(DISTINCT level2_name) l2, "
        "COUNT(DISTINCT id) qs, "
        "MIN(level1_weight) w1min, MAX(level1_weight) w1max "
        "FROM questions GROUP BY level1_name ORDER BY n DESC"
    ):
        print(f"  L1={row['level1_name']!r:<40} n={row['n']:>5} L2={row['l2']:>4} "
              f"w1=[{row['w1min']}, {row['w1max']}]")

    print("-- L2 breakdown --")
    for row in con.execute(
        "SELECT level1_name, level2_name, COUNT(*) n, "
        "MIN(level2_weight) w2min, MAX(level2_weight) w2max, "
        "SUM(level3_weight) w3sum, MIN(level3_weight) w3min, MAX(level3_weight) w3max "
        "FROM questions GROUP BY level1_name, level2_name ORDER BY level1_name, level2_name"
    ):
        print(f"  {row['level1_name'][:18]:<18} | {row['level2_name'][:26]:<26} "
              f"n={row['n']:>4} w2=[{row['w2min']},{row['w2max']}] "
              f"w3sum={row['w3sum']:.4g} w3=[{row['w3min']},{row['w3max']}]")

    hr("QUESTIONS: full listing (id, L1, L2, w3)")
    for row in con.execute(
        "SELECT id, level1_name, level2_name, level3_weight, substr(content,1,60) c "
        "FROM questions ORDER BY id"
    ):
        print(f"  {row['id']:>4} | {row['level1_name'][:14]:<14} | "
              f"{row['level2_name'][:24]:<24} | w3={row['level3_weight']!r:<8} | {row['c']}")

    hr("SCHOOLS: categorical distributions")
    for col in ("year", "school_type", "school_area_type", "data_mark", "region",
                "province"):
        print(f"-- {col} --")
        for row in con.execute(
            f"SELECT {col} v, COUNT(*) n FROM schools GROUP BY {col} "
            f"ORDER BY n DESC LIMIT 25"
        ):
            print(f"   {str(row['v'])[:40]:<40} {row['n']:>10,}")
        ndv = con.execute(f"SELECT COUNT(DISTINCT {col}) FROM schools").fetchone()[0]
        print(f"   (distinct={ndv})")

    print("-- numeric-ish text columns --")
    for col in ("class_num", "teacher_num", "student_num"):
        rows = con.execute(
            f"SELECT {col} v, COUNT(*) n FROM schools GROUP BY {col} "
            f"ORDER BY n DESC LIMIT 12"
        ).fetchall()
        print(f"  {col}: " + ", ".join(f"{r['v']!r}x{r['n']}" for r in rows))

    print("-- duplicate school names --")
    for row in con.execute(
        "SELECT school_name, COUNT(*) n FROM schools GROUP BY school_name "
        "ORDER BY n DESC LIMIT 10"
    ):
        print(f"   {row['school_name']!r} -> {row['n']}")

    hr("ANSWERS: value distribution")
    tot = con.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    print("rows:", f"{tot:,}")
    for row in con.execute(
        "SELECT COUNT(*) n, SUM(value IS NULL) nulls, "
        "SUM(normalized_value IS NULL) norm_nulls, "
        "MIN(value) vmin, MAX(value) vmax, AVG(value) vavg, "
        "MIN(normalized_value) nmin, MAX(normalized_value) nmax, "
        "AVG(normalized_value) navg FROM answers"
    ):
        print(dict(row))

    print("-- value histogram (top 20 exact values) --")
    for row in con.execute(
        "SELECT value v, COUNT(*) n FROM answers GROUP BY value ORDER BY n DESC LIMIT 20"
    ):
        print(f"   {row['v']!r:<14} {row['n']:>12,}")

    print("-- normalized_value histogram (top 20) --")
    for row in con.execute(
        "SELECT normalized_value v, COUNT(*) n FROM answers GROUP BY normalized_value "
        "ORDER BY n DESC LIMIT 20"
    ):
        print(f"   {row['v']!r:<14} {row['n']:>12,}")

    print("-- coverage --")
    print("distinct school_id :", f"{con.execute('SELECT COUNT(DISTINCT school_id) FROM answers').fetchone()[0]:,}")
    print("distinct question_id:", f"{con.execute('SELECT COUNT(DISTINCT question_id) FROM answers').fetchone()[0]:,}")
    print("orphan school_id   :", f"{con.execute('SELECT COUNT(*) FROM answers a LEFT JOIN schools s ON a.school_id=s.id WHERE s.id IS NULL').fetchone()[0]:,}")
    print("orphan question_id :", f"{con.execute('SELECT COUNT(*) FROM answers a LEFT JOIN questions q ON a.question_id=q.id WHERE q.id IS NULL').fetchone()[0]:,}")
    print("schools w/o answers:", f"{con.execute('SELECT COUNT(*) FROM schools s LEFT JOIN answers a ON a.school_id=s.id WHERE a.school_id IS NULL').fetchone()[0]:,}")
    print("questions w/o answers:", f"{con.execute('SELECT COUNT(*) FROM questions q LEFT JOIN answers a ON a.question_id=q.id WHERE a.question_id IS NULL').fetchone()[0]:,}")

    print("-- duplicate (school_id, question_id) pairs --")
    for row in con.execute(
        "SELECT COUNT(*) n FROM (SELECT school_id, question_id FROM answers "
        "GROUP BY school_id, question_id HAVING COUNT(*)>1)"
    ):
        print("  duplicate pairs:", f"{row['n']:,}")

    print("-- answers per school (stats) --")
    for row in con.execute(
        "SELECT MIN(c) mn, MAX(c) mx, AVG(c) av FROM "
        "(SELECT school_id, COUNT(*) c FROM answers GROUP BY school_id)"
    ):
        print(dict(row))

    hr("ANSWERS: per-question aggregates (first 40)")
    for row in con.execute(
        "SELECT q.id, substr(q.content,1,40) c, COUNT(a.value) n, "
        "AVG(a.value) avg_v, MIN(a.value) mn, MAX(a.value) mx, "
        "AVG(a.normalized_value) avg_n, q.level3_weight w3 "
        "FROM questions q LEFT JOIN answers a ON a.question_id=q.id "
        "GROUP BY q.id ORDER BY q.id LIMIT 40"
    ):
        print(f"  {row['id']:>4} n={row['n']:>8,} avg={row['avg_v']!r:<20} "
              f"[{row['mn']},{row['mx']}] norm_avg={row['avg_n']!r:<20} w3={row['w3']!r} | {row['c']}")

    con.close()


if __name__ == "__main__":
    main()
