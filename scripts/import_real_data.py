# 把 data/real/*.csv 导入 SQLite，供学校数字化评估按校/按年快速检索。
# 运行：python scripts/import_real_data.py
#
# 产物：data/real/education_digitization.sqlite
# 不读取 zip 内 747MB/973MB 的 SQL dump（CSV 已覆盖全量学校与 83 题答案）。

import csv
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = os.path.join(ROOT, "data", "real")
DB = os.path.join(REAL, "education_digitization.sqlite")


def _utf8_stdout():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def import_all():
    if not os.path.isfile(os.path.join(REAL, "schools.csv")):
        raise SystemExit("缺少 data/real/schools.csv，请先从 zip 解压")

    if os.path.exists(DB):
        os.remove(DB)

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE questions (
        id INTEGER PRIMARY KEY,
        content TEXT,
        level3_weight REAL,
        level1_name TEXT,
        level1_description TEXT,
        level1_weight REAL,
        level2_name TEXT,
        level2_description TEXT,
        level2_weight REAL
    );
    CREATE TABLE schools (
        id INTEGER PRIMARY KEY,
        province TEXT,
        city TEXT,
        district TEXT,
        school_name TEXT,
        year TEXT,
        school_type TEXT,
        school_area_type TEXT,
        class_num TEXT,
        teacher_num TEXT,
        student_num TEXT,
        data_mark INTEGER,
        region TEXT
    );
    CREATE TABLE answers (
        school_id INTEGER,
        question_id INTEGER,
        value REAL,
        normalized_value REAL
    );
    """)

    print("导入 questions ...")
    with open(os.path.join(REAL, "questions.csv"), encoding="utf-8") as f:
        rows = [
            (int(r["id"]), r["content"], float(r["level3_weight"] or 1),
             r["level1_name"], r["level1_description"], float(r["level1_weight"] or 1),
             r["level2_name"], r["level2_description"], float(r["level2_weight"] or 1))
            for r in csv.DictReader(f)
        ]
    cur.executemany("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    print(f"  {len(rows)} 题")

    print("导入 schools ...")
    n = 0
    batch = []
    with open(os.path.join(REAL, "schools.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            batch.append((
                int(r["id"]), r.get("province"), r.get("city"), r.get("district"),
                r.get("school_name"), r.get("year"), r.get("school_type"),
                r.get("school_area_type"), r.get("class_num"), r.get("teacher_num"),
                r.get("student_num"), int(r.get("data_mark") or 0), r.get("region"),
            ))
            if len(batch) >= 20000:
                cur.executemany(
                    "INSERT INTO schools VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                n += len(batch)
                batch = []
                print(f"  {n} ...")
        if batch:
            cur.executemany(
                "INSERT INTO schools VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
            n += len(batch)
    print(f"  {n} 所学校记录")

    print("导入 answers ...")
    n = 0
    batch = []
    for i in range(1, 6):
        path = os.path.join(REAL, f"school_answers_{i}.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                nv = r.get("normalized_value")
                v = r.get("value")
                try:
                    nvf = float(nv) if nv not in (None, "") else None
                except ValueError:
                    nvf = None
                try:
                    vf = float(v) if v not in (None, "") else None
                except ValueError:
                    vf = None
                batch.append((int(r["school_id"]), int(r["question_id"]), vf, nvf))
                if len(batch) >= 50000:
                    cur.executemany(
                        "INSERT INTO answers(school_id,question_id,value,normalized_value) VALUES (?,?,?,?)",
                        batch)
                    n += len(batch)
                    batch = []
                    print(f"  {n} ...")
    if batch:
        cur.executemany(
            "INSERT INTO answers(school_id,question_id,value,normalized_value) VALUES (?,?,?,?)",
            batch)
        n += len(batch)
    print(f"  {n} 条答案")

    print("建索引 ...")
    cur.executescript("""
    CREATE INDEX idx_schools_name ON schools(school_name);
    CREATE INDEX idx_schools_year ON schools(year);
    CREATE INDEX idx_schools_name_year ON schools(school_name, year);
    CREATE INDEX idx_answers_school ON answers(school_id);
    CREATE INDEX idx_questions_l1 ON questions(level1_name);
    """)
    conn.commit()
    conn.close()
    print(f"完成：{DB}")


if __name__ == "__main__":
    _utf8_stdout()
    import_all()
