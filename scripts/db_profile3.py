"""Profile #3: normalization semantics, weighting, duplicates, score feasibility."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")


def hr(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
q = lambda s, *a: con.execute(s, a).fetchall()

hr("A. NORMALIZATION DENOMINATOR: value/normalized ratio per question")
for r in q("""
    SELECT question_id,
           COUNT(*) n,
           COUNT(DISTINCT ROUND(value/normalized_value, 4)) n_ratios,
           MIN(ROUND(value/normalized_value, 4)) rmin,
           MAX(ROUND(value/normalized_value, 4)) rmax,
           MAX(value) vmax
    FROM answers WHERE normalized_value > 0
    GROUP BY question_id ORDER BY question_id LIMIT 12
"""):
    print(f"   q{r['question_id']:>3} n={r['n']:>8,} distinct_ratios={r['n_ratios']:>4} "
          f"ratio[{r['rmin']},{r['rmax']}] vmax={r['vmax']}")

print("-- q1/q2/q3/q4 ratio detail (top ratios) --")
for qid in (1, 2, 3, 4):
    rows = q("SELECT ROUND(value/normalized_value,6) rt, COUNT(*) n, "
             "MIN(value) vmn, MAX(value) vmx FROM answers "
             "WHERE question_id=? AND normalized_value>0 GROUP BY rt ORDER BY n DESC LIMIT 6", qid)
    print(f"   q{qid}: " + " | ".join(
        f"denom~{r['rt']} x{r['n']:,} (v {r['vmn']}-{r['vmx']})" for r in rows))

hr("B. NORMALIZED VALUE PRECISION (rounding provenance)")
for r in q("""
    SELECT question_id,
      SUM(CASE WHEN ABS(normalized_value*1000 - ROUND(normalized_value*1000)) < 1e-9
               THEN 1 ELSE 0 END) rounded3,
      COUNT(*) n
    FROM answers GROUP BY question_id ORDER BY question_id LIMIT 12
"""):
    print(f"   q{r['question_id']:>3} 3-decimal-rounded={r['rounded3']:>9,}/{r['n']:>9,} "
          f"({100*r['rounded3']/r['n']:5.1f}%)")
print("-- overall --")
r = q("""SELECT SUM(CASE WHEN ABS(normalized_value*1000 - ROUND(normalized_value*1000)) < 1e-9
          THEN 1 ELSE 0 END) rounded3, COUNT(*) n FROM answers""")[0]
print(f"   rounded-to-3dp: {r['rounded3']:,}/{r['n']:,} ({100*r['rounded3']/r['n']:.1f}%)")

hr("C. QUESTIONS WHOSE normalized_value IS DEGENERATE (all 0 or all 1)")
for r in q("""
    SELECT question_id, COUNT(*) n, COUNT(DISTINCT normalized_value) ndv,
           MIN(normalized_value) mn, MAX(normalized_value) mx,
           COUNT(DISTINCT value) vndv, MIN(value) vmn, MAX(value) vmx
    FROM answers GROUP BY question_id
    HAVING ndv = 1
    ORDER BY question_id
"""):
    print(f"   q{r['question_id']:>3} n={r['n']:>8,} norm all={r['mn']} "
          f"value ndv={r['vndv']} range[{r['vmn']},{r['vmx']}]")

hr("D. ALL-ZERO QUESTIONS (value == 0 for every respondent)")
for r in q("""
    SELECT question_id, COUNT(*) n, SUM(value=0) zeros, COUNT(DISTINCT value) ndv,
           MIN(value) mn, MAX(value) mx
    FROM answers GROUP BY question_id
    HAVING MAX(value)=0
    ORDER BY question_id
"""):
    print(f"   q{r['question_id']:>3} n={r['n']} zeros={r['zeros']} range[{r['mn']},{r['mx']}]")
print("   count of all-zero questions:",
      q("SELECT COUNT(*) n FROM (SELECT question_id FROM answers GROUP BY question_id "
        "HAVING MAX(value)=0)")[0]['n'])

hr("E. WEIGHTS: are they informative?")
for col in ("level1_weight", "level2_weight", "level3_weight"):
    r = q(f"SELECT COUNT(DISTINCT {col}) ndv, MIN({col}) mn, MAX({col}) mx, "
          f"SUM({col}) s, AVG({col}) av FROM questions")[0]
    print(f"   {col:<16} distinct={r['ndv']} min={r['mn']} max={r['mx']} "
          f"sum={r['s']} avg={r['av']}")

hr("F. SCORE FEASIBILITY: per-school mean of normalized_value on q1..q4")
r = q("""SELECT COUNT(*) schools, AVG(sc) avg_score, MIN(sc) mn, MAX(sc) mx,
         SUM(sc=0) zero_schools, SUM(sc=1) full_schools
         FROM (SELECT school_id, AVG(normalized_value) sc FROM answers
               WHERE question_id BETWEEN 1 AND 4 GROUP BY school_id)""")[0]
print(dict(r))
print("-- score histogram (deciles) --")
for r in q("""SELECT CAST(sc*10 AS INTEGER)/10.0 b, COUNT(*) n FROM
   (SELECT school_id, AVG(normalized_value) sc FROM answers
    WHERE question_id BETWEEN 1 AND 4 GROUP BY school_id)
   GROUP BY b ORDER BY b"""):
    print(f"   [{r['b']:.1f}, {r['b']+0.1:.1f}) {r['n']:>9,}")

hr("G. (school_name, year) DUPLICATES: same district or homonyms?")
r = q("""SELECT COUNT(*) n FROM (
   SELECT school_name, year FROM schools GROUP BY school_name, year HAVING COUNT(*)>1)""")[0]
print("   duplicated (name,year) groups:", f"{r['n']:,}")
r = q("""SELECT COUNT(*) n FROM (
   SELECT school_name, year FROM schools GROUP BY school_name, year
   HAVING COUNT(*)>1 AND COUNT(DISTINCT district)=1 AND COUNT(DISTINCT province)=1)""")[0]
print("   ... same province+district (likely true dupes):", f"{r['n']:,}")
print("   examples of same-province+district dupes:")
for r in q("""SELECT school_name, year, province, district, COUNT(*) n FROM schools
   GROUP BY school_name, year, province, district HAVING COUNT(*)>1
   ORDER BY n DESC LIMIT 8"""):
    print(f"     {r['school_name'][:34]!r} {r['year']} {r['province']}/{r['district']} x{r['n']}")

hr("H. CITY / DISTRICT FIELD NORMALITY")
print("   city values containing '市' vs not:", q(
    "SELECT SUM(city LIKE '%市') with_shi, SUM(city NOT LIKE '%市') without_shi, "
    "COUNT(DISTINCT city) ndv FROM schools")[0]['with_shi'])
for r in q("SELECT city, COUNT(*) n FROM schools GROUP BY city ORDER BY n DESC LIMIT 8"):
    print(f"     {r['city']!r:<20} {r['n']:>8,}")
print("   city == district cases:", q(
    "SELECT COUNT(*) n FROM schools WHERE city=district")[0]['n'])
print("   city empty examples:", [dict(x) for x in q(
    "SELECT province, city, district, school_name FROM schools WHERE TRIM(city)='' LIMIT 5")])

hr("I. ANSWER MATRIX SPARSITY SUMMARY")
r = q("SELECT COUNT(*) n FROM schools")[0]['n']
tot = q("SELECT COUNT(*) n FROM answers")[0]['n']
print(f"   schools={r:,} questions=83 cells={r*83:,} filled={tot:,} "
      f"density={100*tot/(r*83):.2f}%")
print(f"   of filled cells, q1-q4 share: "
      f"{100*q('SELECT COUNT(*) n FROM answers WHERE question_id<=4')[0]['n']/tot:.2f}%")

hr("J. CORE 4 QUESTIONS: joint distribution / correlation")
for r in q("""SELECT
   (SELECT COUNT(*) FROM answers a1 JOIN answers a2 ON a1.school_id=a2.school_id
     WHERE a1.question_id=1 AND a2.question_id=2 AND a1.value=0 AND a2.value=0) both0_q1q2,
   (SELECT COUNT(*) FROM answers WHERE question_id=1 AND value=0) q1_zero,
   (SELECT COUNT(*) FROM answers WHERE question_id=2 AND value=0) q2_zero,
   (SELECT COUNT(*) FROM answers WHERE question_id=3 AND value=0) q3_zero"""):
    print(dict(r))
print("-- q1 value distribution --")
for r in q("SELECT value, COUNT(*) n FROM answers WHERE question_id=1 GROUP BY value ORDER BY value"):
    print(f"   {int(r['value']):>3} {r['n']:>10,}")
print("-- q3 value distribution --")
for r in q("SELECT value, COUNT(*) n FROM answers WHERE question_id=3 GROUP BY value ORDER BY value"):
    print(f"   {int(r['value']):>3} {r['n']:>10,}")

hr("K. YEAR OVERLAP: schools appearing in multiple years")
for r in q("""SELECT ny, COUNT(*) schools FROM (
   SELECT school_name, COUNT(DISTINCT year) ny FROM schools GROUP BY school_name)
   GROUP BY ny ORDER BY ny"""):
    print(f"   appears in {r['ny']} year(s): {r['schools']:>9,} schools")

hr("L. WAL / integrity")
print("   integrity_check:", q("PRAGMA integrity_check")[0][0])
print("   quick_check:", q("PRAGMA quick_check")[0][0])
print("   foreign_key_check:", q("PRAGMA foreign_key_check"))
con.close()
