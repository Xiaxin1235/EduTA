"""Deep-dive profile #2: answer coverage patterns, normalization rule, data quality."""
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

hr("1. ANSWERS-PER-SCHOOL DISTRIBUTION")
for r in q("SELECT c, COUNT(*) schools FROM (SELECT school_id, COUNT(*) c FROM answers "
           "GROUP BY school_id) GROUP BY c ORDER BY c"):
    print(f"   answers/school = {r['c']:>3} -> {r['schools']:>10,} schools")

hr("2. ANSWER COVERAGE PER QUESTION (n, %of 374887)")
for r in q("SELECT q.id, COUNT(a.school_id) n, q.level1_name l1, substr(q.content,1,44) c "
           "FROM questions q LEFT JOIN answers a ON a.question_id=q.id "
           "GROUP BY q.id ORDER BY n DESC, q.id"):
    print(f"   q{r['id']:>3} n={r['n']:>8,} ({100*r['n']/374887:6.2f}%) "
          f"{r['l1']:<6} {r['c']}")

hr("3. WHO ARE THE SPARSELY-ANSWERED SCHOOLS? (schools with >4 answers)")
rows = q("SELECT a.school_id, s.province, s.city, s.district, s.school_name, s.year, "
         "s.school_type, s.school_area_type, COUNT(*) c, MIN(a.question_id) mn, MAX(a.question_id) mx "
         "FROM answers a JOIN schools s ON s.id=a.school_id "
         "GROUP BY a.school_id HAVING c>4 ORDER BY c DESC LIMIT 30")
for r in rows:
    print(f"   id={r['school_id']:<7} n={r['c']:>3} qid[{r['mn']}..{r['mx']}] "
          f"{r['province']}/{r['city']}/{r['district']} {r['school_name'][:28]} "
          f"yr={r['year']} type={r['school_type']} area={r['school_area_type']!r}")

hr("4. SCHOOLS WITH EXACTLY 4 ANSWERS: which question ids?")
for r in q("SELECT GROUP_CONCAT(question_id) ids, COUNT(*) schools FROM "
           "(SELECT school_id FROM answers GROUP BY school_id HAVING COUNT(*)=4) g "
           "JOIN answers a ON a.school_id=g.school_id GROUP BY g.school_id LIMIT 5"):
    print("   sample qid sets:", r['ids'])
for r in q("SELECT question_id, COUNT(*) n FROM answers WHERE school_id IN "
           "(SELECT school_id FROM answers GROUP BY school_id HAVING COUNT(*)=4) "
           "GROUP BY question_id ORDER BY n DESC"):
    print(f"   q{r['question_id']:>3} -> {r['n']:>10,}")

hr("5. NORMALIZATION RULE CHECK (value -> normalized per question)")
for r in q("SELECT question_id, MIN(value) vmin, MAX(value) vmax, "
           "MIN(normalized_value) nmin, MAX(normalized_value) nmax, COUNT(*) n "
           "FROM answers GROUP BY question_id ORDER BY question_id LIMIT 20"):
    print(f"   q{r['question_id']:>3} value[{r['vmin']},{r['vmax']}] "
          f"norm[{r['nmin']:.4f},{r['nmax']:.4f}] n={r['n']:,} "
          f"ratio_max={r['nmax']/r['vmax'] if r['vmax'] else float('nan'):.6f}")
print("   ... checking identity normalized == value/vmax for q1..q4")
for r in q("SELECT question_id, SUM(ABS(normalized_value - value/mx) < 1e-6) ok, COUNT(*) n "
           "FROM (SELECT question_id, value, normalized_value, "
           "MAX(value) OVER (PARTITION BY question_id) mx FROM answers) "
           "GROUP BY question_id ORDER BY question_id LIMIT 6"):
    print(f"   q{r['question_id']:>3} exact_match={r['ok']:,}/{r['n']:,}")

hr("6. DATA QUALITY: out-of-range / implausible values")
print("-- q4 (teacher percentage) --")
for r in q("SELECT CASE WHEN value>100 THEN '>100' WHEN value=100 THEN '=100' "
           "WHEN value=0 THEN '=0' ELSE '1-99' END b, COUNT(*) n FROM answers "
           "WHERE question_id=4 GROUP BY b ORDER BY n DESC"):
    print(f"   {r['b']:>6} {r['n']:>10,}")
print("   top q4 values:", [dict(x) for x in q(
    "SELECT value, COUNT(*) n FROM answers WHERE question_id=4 "
    "GROUP BY value ORDER BY n DESC LIMIT 8")])
print("   q4 sample >100:", [dict(x) for x in q(
    "SELECT school_id, value, normalized_value FROM answers WHERE question_id=4 "
    "AND value>100 ORDER BY value DESC LIMIT 8")])

print("-- percentage-like questions with value>100 --")
for r in q("SELECT question_id, COUNT(*) n_over, MAX(value) mx FROM answers "
           "WHERE value>100 GROUP BY question_id ORDER BY n_over DESC"):
    print(f"   q{r['question_id']:>3} n>100={r['n_over']:>8,} max={r['mx']}")

hr("7. VALUE TYPE PER QUESTION: discrete vs continuous")
for r in q("SELECT question_id, COUNT(DISTINCT value) ndv, COUNT(*) n, "
           "MIN(value) mn, MAX(value) mx, "
           "SUM(CASE WHEN value = CAST(value AS INTEGER) THEN 1 ELSE 0 END) ints "
           "FROM answers GROUP BY question_id ORDER BY question_id"):
    print(f"   q{r['question_id']:>3} ndv={r['ndv']:>6,} n={r['n']:>8,} "
          f"[{r['mn']},{r['mx']}] integers={100*r['ints']/r['n']:5.1f}%")

hr("8. SCHOOLS TABLE: text-field cleanliness")
for col in ("province", "city", "district", "school_name", "year", "school_type",
            "school_area_type", "region", "class_num", "teacher_num", "student_num"):
    r = q(f"SELECT COUNT(*) n, SUM(TRIM({col})='') empty, "
          f"SUM({col} LIKE '% %') spaces, SUM({col} GLOB '*[0-9]*') with_digit, "
          f"SUM({col} LIKE '%?%') qmark FROM schools")[0]
    print(f"   {col:<17} n={r['n']:,} empty={r['empty']:,} has_space={r['spaces']:,} "
          f"has_digit={r['with_digit']:,} has_qmark={r['qmark']:,}")

print("-- region vs province mapping sanity --")
for r in q("SELECT region, COUNT(DISTINCT province) np, GROUP_CONCAT(DISTINCT province) ps "
           "FROM schools GROUP BY region"):
    print(f"   {r['region']!r:<8} provinces={r['np']} {r['ps'][:110]}")

print("-- province list --")
print("  ", ", ".join(r[0] for r in q("SELECT DISTINCT province FROM schools ORDER BY province")))

hr("9. SCHOOLS: uniqueness / duplication")
for r in q("SELECT COUNT(*) rows, COUNT(DISTINCT school_name||'|'||year) uniq_name_year, "
           "COUNT(DISTINCT school_name) uniq_name FROM schools"):
    print(dict(r))
for r in q("SELECT school_name, year, COUNT(*) n FROM schools GROUP BY school_name, year "
           "HAVING n>1 ORDER BY n DESC LIMIT 10"):
    print(f"   dup (name,year): {r['school_name'][:40]!r} {r['year']} -> {r['n']}")
print("-- same school_name in multiple provinces --")
for r in q("SELECT COUNT(*) n FROM (SELECT school_name FROM schools "
           "GROUP BY school_name HAVING COUNT(DISTINCT province)>1)"):
    print("   names spanning >1 province:", f"{r['n']:,}")

hr("10. YEAR x COVERAGE cross-tab")
for r in q("SELECT year, COUNT(*) schools, SUM(CASE WHEN class_num!='' THEN 1 ELSE 0 END) with_class "
           "FROM schools GROUP BY year ORDER BY year"):
    print(f"   {r['year']} schools={r['schools']:>9,} with_class_num={r['with_class']:>9,}")

hr("11. NUMERIC STATS for class/teacher/student (empty excluded)")
for col in ("class_num", "teacher_num", "student_num"):
    for r in q(f"SELECT COUNT(*) n, MIN(CAST({col} AS INTEGER)) mn, "
               f"MAX(CAST({col} AS INTEGER)) mx, AVG(CAST({col} AS INTEGER)) av "
               f"FROM schools WHERE {col}<>'' AND {col} GLOB '[0-9]*'"):
        print(f"   {col:<12} n={r['n']:>9,} min={r['mn']} max={r['mx']} avg={r['av']:.1f}")
print("   non-numeric values in those columns:")
for col in ("class_num", "teacher_num", "student_num"):
    rows = q(f"SELECT {col} v, COUNT(*) n FROM schools WHERE {col}<>'' "
             f"AND {col} NOT GLOB '[0-9]*' GROUP BY {col} ORDER BY n DESC LIMIT 5")
    print(f"   {col}: {[(r['v'], r['n']) for r in rows]}")

hr("12. STUDENT/TEACHER RATIO plausibility (where both present)")
for r in q("SELECT COUNT(*) n, MIN(ratio) mn, MAX(ratio) mx, AVG(ratio) av FROM "
           "(SELECT CAST(student_num AS REAL)/NULLIF(CAST(teacher_num AS REAL),0) ratio "
           "FROM schools WHERE student_num GLOB '[0-9]*' AND teacher_num GLOB '[0-9]*' "
           "AND student_num<>'' AND teacher_num<>'' AND CAST(teacher_num AS INTEGER)>0)"):
    print(f"   n={r['n']:,} ratio min={r['mn']:.2f} max={r['mx']:.2f} avg={r['av']:.2f}")
print("   extreme ratios:")
for r in q("SELECT school_name, student_num, teacher_num, "
           "ROUND(CAST(student_num AS REAL)/CAST(teacher_num AS REAL),2) ratio FROM schools "
           "WHERE student_num GLOB '[0-9]*' AND teacher_num GLOB '[0-9]*' "
           "AND CAST(teacher_num AS INTEGER)>0 "
           "ORDER BY ratio DESC LIMIT 5"):
    print(f"   {r['ratio']:>9} stu={r['student_num']:>6} tea={r['teacher_num']:>5} {r['school_name'][:40]}")

hr("13. ANSWERS: value==0 prevalence by question (possible missing-as-zero)")
for r in q("SELECT question_id, COUNT(*) n, SUM(value=0) zeros, "
           "100.0*SUM(value=0)/COUNT(*) pct FROM answers GROUP BY question_id "
           "ORDER BY pct DESC LIMIT 15"):
    print(f"   q{r['question_id']:>3} n={r['n']:>8,} zeros={r['zeros']:>8,} ({r['pct']:5.1f}%)")

hr("14. SCHOOL_TYPE code vs label families")
for r in q("SELECT CASE WHEN school_type GLOB '[0-9]*' THEN 'code(1-6 style)' "
           "WHEN school_type GLOB '[A-Z].*' THEN 'label(A.小学 style)' ELSE 'other' END f, "
           "COUNT(*) n, COUNT(DISTINCT school_type) ndv FROM schools GROUP BY f"):
    print(f"   {r['f']:<24} n={r['n']:>9,} distinct={r['ndv']}")
print("   label-style values:")
for r in q("SELECT school_type, COUNT(*) n FROM schools WHERE school_type GLOB '[A-Z].*' "
           "GROUP BY school_type ORDER BY n DESC"):
    print(f"     {r['school_type']!r:<24} {r['n']:>8,}")
print("   code-style top 20:")
for r in q("SELECT school_type, COUNT(*) n FROM schools WHERE school_type GLOB '[0-9]*' "
           "GROUP BY school_type ORDER BY n DESC LIMIT 20"):
    print(f"     {r['school_type']!r:<10} {r['n']:>8,}")

hr("15. INDEX EFFECTIVENESS / query plan")
for sql in ("SELECT COUNT(*) FROM answers WHERE school_id=1",
            "SELECT COUNT(*) FROM answers WHERE question_id=4",
            "SELECT COUNT(*) FROM schools WHERE year='2020'"):
    plan = q("EXPLAIN QUERY PLAN " + sql)
    print(f"   {sql}")
    for p in plan:
        print("      ", p[3])

con.close()
