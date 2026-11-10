"""Step 1 (definition R11: year-specific uncollected items excluded): per school-year sums of valid normalized item scores per dimension, for every school in the research view.

Definitions (same as the reference-100 package):
- valid = normalized_value IS NOT NULL (zeros kept); points = normalized_value * 100;
- regional per-school overall score = mean of all valid item points of that school-year;
- per-school dimension score (regional statistics) = mean of valid item points in that dimension;
- school template score (school-level questions) = per dimension mean of points clipped to [0,100],
  round(x,2) per dimension, weighted mean / sum of weights, round(x,2).
Writes evidence/school_scores.sqlite (refuses to overwrite).
"""
from common import *

if SCORES.exists():
    raise SystemExit("refuse to overwrite " + str(SCORES))
c = research()  # query_only: the item-to-dimension map is an inline VALUES table, nothing is written
values = ",".join(f"({i},'{r['level1_name']}')" for i, r in sorted(CAT.items()))
excl = ",".join(f"('{y}',{q})" for y, qs in sorted(UNCOLLECTED.items()) for q in sorted(qs))
t0 = datetime.datetime.now()
rows = c.execute(f"""
  WITH item_dim(question_id, dim) AS (VALUES {values}), excluded(year, question_id) AS (VALUES {excl})
  SELECT a.school_id, d.dim,
         COUNT(*) AS n,
         SUM(a.normalized_value*100.0) AS s,
         SUM(MIN(MAX(a.normalized_value*100.0, 0.0), 100.0)) AS s_clip
  FROM facts.answers a JOIN item_dim d ON d.question_id = a.question_id
  JOIN base.school_records b ON b.id = a.school_id
  WHERE a.normalized_value IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM excluded e WHERE e.year = b.year AND e.question_id = a.question_id)
  GROUP BY a.school_id, d.dim""").fetchall()
schools = c.execute("""SELECT id, year, school_name, query_province AS province, query_city AS city, query_district AS district,
                              school_type, school_area_type FROM research_schools""").fetchall()
out = sqlite3.connect(SCORES)
out.executescript("""
CREATE TABLE school(id INTEGER PRIMARY KEY, year TEXT, school_name TEXT, province TEXT, city TEXT, district TEXT, school_type TEXT, school_area_type TEXT);
CREATE TABLE dim_stats(school_id INTEGER, dim TEXT, n INTEGER, s REAL, s_clip REAL, PRIMARY KEY(school_id, dim));
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
""")
out.executemany("INSERT INTO school VALUES(?,?,?,?,?,?,?,?)", [tuple(r) for r in schools])
out.executemany("INSERT INTO dim_stats VALUES(?,?,?,?,?)", [tuple(r) for r in rows])
out.executemany("INSERT INTO meta VALUES(?,?)", [
    ("built_at", now()), ("seconds", str((datetime.datetime.now() - t0).total_seconds())),
    ("snapshot", str(SNAP)), ("base", str(BASE)), ("mapping", str(MAP)),
    ("view_sql_sha256", sha(PACKAGE / "sql/full_research_view.sql")),
    ("definition", __doc__), ("uncollected_items_version", "uncollected-items-v1-20261019")])
out.commit()
print(len(schools), "school-years;", len(rows), "school-dimension rows;", (datetime.datetime.now() - t0).total_seconds(), "s")
