"""Step 1: per school-year sums of valid normalized item points per sub-dimension (catalogue level2_name), same definitions as the
v3 package's 01_school_scores.py: valid = normalized_value IS NOT NULL (zeros kept), points = normalized_value*100, year-specific
uncollected items (R11) excluded. A school's sub-dimension score = s/n. Writes evidence/topic_scores_r11.sqlite (refuses to overwrite)."""
from common import *

if TOPIC_SCORES.exists():
    raise SystemExit("refuse to overwrite " + str(TOPIC_SCORES))
c = research()
values = ",".join(f"({i},'{r['level2_name']}')" for i, r in sorted(CAT.items()))
excl = ",".join(f"('{y}',{q})" for y, qs in sorted(UNCOLLECTED.items()) for q in sorted(qs))
rows = c.execute(f"""
  WITH item_topic(question_id, topic) AS (VALUES {values}), excluded(year, question_id) AS (VALUES {excl})
  SELECT a.school_id, t.topic, COUNT(*) AS n, SUM(a.normalized_value*100.0) AS s
  FROM facts.answers a JOIN item_topic t ON t.question_id = a.question_id
  JOIN base.school_records b ON b.id = a.school_id
  WHERE a.normalized_value IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM excluded e WHERE e.year = b.year AND e.question_id = a.question_id)
  GROUP BY a.school_id, t.topic""").fetchall()
out = sqlite3.connect(TOPIC_SCORES)
out.executescript("CREATE TABLE topic_stats(school_id INTEGER, topic TEXT, n INTEGER, s REAL, PRIMARY KEY(school_id, topic)); CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);")
out.executemany("INSERT INTO topic_stats VALUES(?,?,?,?)", [tuple(r) for r in rows])
out.executemany("INSERT INTO meta VALUES(?,?)", [("computed_at", now()), ("rows", str(len(rows))), ("snapshot_sha256", sha(SNAP))])
out.commit()
print(len(rows), "topic rows")
