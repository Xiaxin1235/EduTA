"""Verification of a few report numbers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
q = lambda s, *a: con.execute(s, a).fetchone()

print("questions with degenerate normalized_value (ndv=1):",
      q("SELECT COUNT(*) FROM (SELECT question_id FROM answers GROUP BY question_id "
        "HAVING COUNT(DISTINCT normalized_value)=1)"))
print("questions with degenerate value (ndv=1):",
      q("SELECT COUNT(*) FROM (SELECT question_id FROM answers GROUP BY question_id "
        "HAVING COUNT(DISTINCT value)=1)"))
print("questions with all value=0:", q(
    "SELECT COUNT(*) FROM (SELECT question_id FROM answers GROUP BY question_id "
    "HAVING MAX(value)=0)"))
print("questions whose content contains '.' (escaped quotes):",
      q("SELECT COUNT(*) FROM questions WHERE content LIKE '%.%'"))
print("total '.' occurrences in content:",
      q("SELECT SUM(LENGTH(content)-LENGTH(REPLACE(content,'.',''))) FROM questions"))
print("questions with normalized_value rounded to 3dp (all rows):",
      q("SELECT COUNT(*) FROM (SELECT question_id FROM answers GROUP BY question_id "
        "HAVING SUM(ABS(normalized_value*1000-ROUND(normalized_value*1000))<1e-9)=COUNT(*))"))
print("q1 rows with exact (non-rounded) normalized:",
      q("SELECT SUM(ABS(normalized_value*1000-ROUND(normalized_value*1000))>=1e-9) FROM answers "
        "WHERE question_id=1"))
con.close()
