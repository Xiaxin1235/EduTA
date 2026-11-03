"""Profile #7: id-range structure by year (how the CSV subset is laid out)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(r"D:\teacher evaluation\data\real\education_digitization.sqlite")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row

print("id range / contiguity per year:")
for r in con.execute("""SELECT year, COUNT(*) n, MIN(id) mn, MAX(id) mx,
                        MAX(id)-MIN(id)+1 span
                        FROM schools GROUP BY year ORDER BY year"""):
    print(f"  {r['year']} n={r['n']:>8,} id[{r['mn']:,}..{r['mx']:,}] span={r['span']:,} "
          f"density={100*r['n']/r['span']:.1f}%")

print()
print("gaps per year (missing ids inside range):")
for r in con.execute("""SELECT year, COUNT(*) n, MAX(id)-MIN(id)+1 span FROM schools
                        GROUP BY year ORDER BY year"""):
    print(f"  {r['year']} missing_in_range={r['span']-r['n']:,}")

print()
print("sample rows across the id space:")
for r in con.execute("SELECT id, year, province, school_name FROM schools "
                     "WHERE id IN (1, 115984, 115985, 500000, 1000000, 1500000, 1750636)"):
    print("  ", dict(r))

print()
print("max id / count check:")
print("  max id =", con.execute("SELECT MAX(id) FROM schools").fetchone()[0])
print("  distinct ids =", con.execute("SELECT COUNT(DISTINCT id) FROM schools").fetchone()[0])

print()
print("2020 sub-block structure (contiguous runs):")
prev = None
runs = []
for r in con.execute("SELECT id FROM schools ORDER BY id"):
    i = r['id']
    if prev is None:
        start = i
    elif i != prev + 1:
        runs.append((start, prev))
        start = i
    prev = i
runs.append((start, prev))
print(f"  total contiguous runs: {len(runs)}")
for s, e in runs[:15]:
    print(f"    {s:,} .. {e:,}  ({e-s+1:,} ids)")
con.close()
