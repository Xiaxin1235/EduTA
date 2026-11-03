"""Profile #6: id space, and the missing-as-zero effect on 2022/2023 evaluations."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchone()

print("id space:")
print("  schools min/max id:", q("SELECT MIN(id), MAX(id) FROM schools"))
print("  schools count     :", q("SELECT COUNT(*) FROM schools")[0])
print("  missing ids in 1..max:",
      q("SELECT (SELECT MAX(id) FROM schools) - COUNT(*) FROM schools")[0])
print("  answers min/max school_id:", q("SELECT MIN(school_id), MAX(school_id) FROM answers"))
print("  answers min/max question_id:", q("SELECT MIN(question_id), MAX(question_id) FROM answers"))

print()
print("sample 2023 school ids (q1=q3=0):")
for r in con.execute("SELECT id, school_name, year FROM schools WHERE year='2023' LIMIT 3"):
    print("  ", dict(r) if hasattr(r, 'keys') else r)
sid = con.execute("SELECT id FROM schools WHERE year='2023' LIMIT 1").fetchone()[0]
con.row_factory = sqlite3.Row
print("  answers of school", sid, ":",
      [dict(x) for x in con.execute(
          "SELECT question_id, value, normalized_value FROM answers WHERE school_id=?", (sid,))])

sys.path.insert(0, r"D:\teacher evaluation")
from scripts import school_engine as se  # noqa: E402

res = se.evaluate_school(sid)
print()
print(f"engine output for 2023 school {sid} ({res.get('school_name')}):")
print("  total_score:", res.get("total_score"), "coverage:", res.get("data_coverage"),
      "answered:", res.get("answered"), "/", res.get("total_metrics"))
for d in res.get("dimensions", []):
    print(f"    {str(d['dimension']):<12} score={d['score']} conf={d['confidence']} "
          f"evidence={[e['raw'] for e in d['evidence']]}")

print()
print("distribution of schools by id-block (contiguity check):")
for lo in range(0, 400001, 50000):
    n = q("SELECT COUNT(*) FROM schools WHERE id>=? AND id<?", lo, lo + 50000)[0]
    print(f"  id [{lo:>7},{lo+50000:>7}) -> {n:>7,}")
con.close()
