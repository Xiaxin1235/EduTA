"""Profile #5: final zero-pattern and usability checks."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchone()[0]

print("schools with q1..q4 all zero:", q(
    "SELECT COUNT(*) FROM (SELECT school_id FROM answers WHERE question_id BETWEEN 1 AND 4 "
    "GROUP BY school_id HAVING MAX(value)=0)"))
print("schools with q1=0 and q3=0:", q(
    "SELECT COUNT(*) FROM (SELECT school_id FROM answers WHERE question_id IN (1,3) "
    "GROUP BY school_id HAVING SUM(value)=0)"))
print("2023 schools with q1=0 and q3=0:", q(
    "SELECT COUNT(*) FROM schools s "
    "JOIN answers a1 ON a1.school_id=s.id AND a1.question_id=1 AND a1.value=0 "
    "JOIN answers a3 ON a3.school_id=s.id AND a3.question_id=3 AND a3.value=0 "
    "WHERE s.year='2023'"))
print("2022 schools with q3=0:", q(
    "SELECT COUNT(*) FROM schools s JOIN answers a3 ON a3.school_id=s.id "
    "AND a3.question_id=3 AND a3.value=0 WHERE s.year='2022'"))
print("duplicate exact answer rows:", q(
    "SELECT COUNT(*) FROM (SELECT school_id,question_id,value,normalized_value FROM answers "
    "GROUP BY 1,2,3,4 HAVING COUNT(*)>1)"))
print("duplicate (school,question) pairs:", q(
    "SELECT COUNT(*) FROM (SELECT school_id,question_id FROM answers GROUP BY 1,2 "
    "HAVING COUNT(*)>1)"))
print()
print("usable matrix (value>0 cells) by question block:")
for lo, hi, lab in ((1, 1, "q1"), (2, 2, "q2"), (3, 3, "q3"), (4, 4, "q4"),
                    (5, 5, "q5"), (6, 45, "q6-45"), (46, 83, "q46-83")):
    n, pos = con.execute(
        "SELECT COUNT(*), SUM(value>0) FROM answers WHERE question_id BETWEEN ? AND ?",
        (lo, hi)).fetchone()
    print(f"  {lab:<8} cells={n:>9,} value>0={pos:>9,}")
print()
print("score by year (mean of q1..q4 normalized):")
for y, n, s in con.execute(
        "SELECT s.year, COUNT(*), AVG(sc) FROM (SELECT school_id, AVG(normalized_value) sc "
        "FROM answers WHERE question_id BETWEEN 1 AND 4 GROUP BY school_id) t "
        "JOIN schools s ON s.id=t.school_id GROUP BY s.year ORDER BY s.year"):
    print(f"  {y} schools={n:>8,} avg={s:.4f}")
print()
print("score by year using ONLY questions actually asked that year (q2,q4 for all; q1 2020-22; q3 2020-21):")
for y, n, s in con.execute(
        "SELECT s.year, COUNT(*), AVG(sc) FROM (SELECT a.school_id, AVG(a.normalized_value) sc "
        "FROM answers a WHERE a.value>0 AND a.question_id BETWEEN 1 AND 4 GROUP BY a.school_id) t "
        "JOIN schools s ON s.id=t.school_id GROUP BY s.year ORDER BY s.year"):
    print(f"  {y} schools={n:>8,} avg={s:.4f}")
con.close()
