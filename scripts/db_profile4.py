"""Profile #4: zero-pattern by year, exact normalization denominators, outlier impact."""
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

hr("1. ZEROS BY YEAR for q1..q5 (missing-as-zero check)")
for qid in (1, 2, 3, 4, 5):
    print(f"-- q{qid} --")
    for r in q("""SELECT s.year, COUNT(*) n, SUM(a.value=0) zeros,
                  100.0*SUM(a.value=0)/COUNT(*) pct, AVG(a.value) avg_v,
                  MIN(a.value) mn, MAX(a.value) mx
                 FROM answers a JOIN schools s ON s.id=a.school_id
                 WHERE a.question_id=? GROUP BY s.year ORDER BY s.year""", qid):
        print(f"   {r['year']} n={r['n']:>8,} zeros={r['zeros']:>8,} ({r['pct']:5.1f}%) "
              f"avg={r['avg_v']:.3f} range[{r['mn']},{r['mx']}]")

hr("2. YEAR x QUESTION: which questions exist per year")
for r in q("""SELECT s.year, a.question_id, COUNT(*) n
              FROM answers a JOIN schools s ON s.id=a.school_id
              GROUP BY s.year, a.question_id ORDER BY s.year, a.question_id"""):
    if r['n'] < 1000 or r['question_id'] <= 5:
        print(f"   {r['year']} q{r['question_id']:>3} n={r['n']:>8,}")

hr("3. EXACT NORMALIZATION DENOMINATOR per question (test norm==round(v/d,3))")
for r in q("SELECT question_id, MAX(value) vmax, COUNT(DISTINCT value) ndv FROM answers "
           "GROUP BY question_id ORDER BY question_id"):
    qid = r['question_id']
    best = []
    for d in (r['vmax'], 3, 4, 5, 6, 7, 8, 10, 11, 13, 21, 100, 1009):
        if d <= 0:
            continue
        hit = q("SELECT SUM(ABS(normalized_value - ROUND(value/? ,3))<1e-6) ok, COUNT(*) n "
                "FROM answers WHERE question_id=?", d, qid)[0]
        if hit['ok'] == hit['n']:
            best.append(d)
    print(f"   q{qid:>3} vmax={r['vmax']:<8} ndv={r['ndv']:<6} matching_denominators={best}")

hr("4. q4 OUTLIER IMPACT")
r = q("SELECT COUNT(*) n FROM answers WHERE question_id=4 AND value>100")[0]['n']
print("   rows with value>100:", r)
print("   q4 normalized stats: ", dict(q(
    "SELECT MIN(normalized_value) mn, MAX(normalized_value) mx, "
    "AVG(normalized_value) av FROM answers WHERE question_id=4")[0]))
print("   q4 normalized if denominator were 100:")
print("     ", dict(q("SELECT AVG(value/100.0) av, MIN(value/100.0) mn, MAX(value/100.0) mx "
                    "FROM answers WHERE question_id=4 AND value<=100")[0]))
print("   schools affected: all 374,887 (denominator is a single global max)")
print("   effect: value=100 scores 0.099 instead of 1.0 -> ~10x compression")
print("   q4 value=100 count:", q("SELECT COUNT(*) n FROM answers WHERE question_id=4 AND value=100")[0]['n'])

hr("5. SCORE (mean of q1..q4 normalized) BY YEAR / AREA / REGION")
for label, group in (("year", "s.year"), ("area", "s.school_area_type"),
                     ("region", "s.region"), ("type", "s.school_type")):
    print(f"-- by {label} --")
    for r in q(f"""SELECT {group} g, COUNT(*) schools, AVG(sc) avg_score
                   FROM (SELECT school_id, AVG(normalized_value) sc FROM answers
                         WHERE question_id BETWEEN 1 AND 4 GROUP BY school_id) t
                   JOIN schools s ON s.id=t.school_id
                   GROUP BY g ORDER BY avg_score DESC LIMIT 12"""):
        print(f"   {str(r['g'])[:22]:<22} schools={r['schools']:>8,} "
              f"avg_score={r['avg_score']:.4f}")

hr("6. school_name FIELD MISALIGNMENT (name looks like a place name)")
for pat, label in (("%县", "ends 县"), ("%区", "ends 区"), ("%市", "ends 市"),
                   ("%镇", "ends 镇"), ("%乡", "ends 乡")):
    n = q(f"SELECT COUNT(*) n FROM schools WHERE school_name LIKE ?", pat)[0]['n']
    print(f"   {label:<8} {n:>9,}")
print("   examples:")
for r in q("SELECT province, city, district, school_name FROM schools "
           "WHERE school_name LIKE '%县' LIMIT 6"):
    print(f"     {r['province']}/{r['city']}/{r['district']} -> name={r['school_name']!r}")

hr("7. PROVINCE COVERAGE vs 31 mainland provinces")
r = q("SELECT COUNT(DISTINCT province) n FROM schools")[0]['n']
print("   distinct province values:", r)
print("   non-province values:", [x['province'] for x in q(
    "SELECT DISTINCT province FROM schools WHERE province LIKE '%兵团%' "
    "OR province LIKE '%澳门%' OR province LIKE '%香港%' OR province LIKE '%台湾%'")])

hr("8. ANSWERS PER SCHOOL BY YEAR (did the questionnaire shrink?)")
for r in q("""SELECT s.year, AVG(c) avg_ans, MIN(c) mn, MAX(c) mx, COUNT(*) schools
              FROM (SELECT school_id, COUNT(*) c FROM answers GROUP BY school_id) t
              JOIN schools s ON s.id=t.school_id GROUP BY s.year ORDER BY s.year"""):
    print(f"   {r['year']} schools={r['schools']:>8,} avg_answers={r['avg_ans']:.2f} "
          f"min={r['mn']} max={r['mx']}")

hr("9. SCHOOLS WITHOUT ANY MEANINGFUL SCORE (all four core answers zero)")
print("   ", dict(q("""SELECT COUNT(*) n FROM (
   SELECT school_id FROM answers WHERE question_id BETWEEN 1 AND 4
   GROUP BY school_id HAVING MAX(value)=0)""")))
print("   schools with q1=q3=0:", q("""
   SELECT COUNT(*) n FROM (
     SELECT school_id FROM answers WHERE question_id IN (1,3)
     GROUP BY school_id HAVING SUM(value)=0)""")[0]['n'])

hr("10. DUPLICATE ROW CHECK on answers (no PK defined)")
print("   exact duplicate (school_id,question_id,value,normalized_value) rows:",
      q("SELECT COUNT(*) n FROM (SELECT school_id,question_id,value,normalized_value "
        "FROM answers GROUP BY 1,2,3,4 HAVING COUNT(*)>1)")[0]['n'])
print("   duplicate (school_id,question_id) pairs:",
      q("SELECT COUNT(*) n FROM (SELECT school_id,question_id FROM answers "
        "GROUP BY 1,2 HAVING COUNT(*)>1)")[0]['n'])

con.close()
