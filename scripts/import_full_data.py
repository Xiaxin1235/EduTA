"""全量导入教育数字化数据库到 SQLite。

数据源：
- 题目与学校字典：data/real/questions.csv, data/real/schools.csv
- 全量 3111.56 万作答事实：教育数字化数据库/.../dump-test_number-202605201442.sql

产物：
- data/real/education_digitization.sqlite（包含 83 题全部 374,887 所学校的完整数据）
"""

import csv
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_DIR = os.path.join(ROOT, "data", "real")
TARGET_DB = os.path.join(REAL_DIR, "education_digitization.sqlite")
TMP_DB = os.path.join(REAL_DIR, "education_digitization.sqlite.tmp")
BAK_DB = os.path.join(REAL_DIR, "education_digitization.sqlite.truncated.bak")


def find_dump_file():
    search_dir = os.path.join(ROOT, "教育数字化数据库")
    if not os.path.exists(search_dir):
        raise FileNotFoundError(f"未找到目录: {search_dir}")
    for root, _, files in os.walk(search_dir):
        for f in files:
            if "dump-test_number" in f and f.endswith(".sql"):
                return os.path.join(root, f)
    raise FileNotFoundError("未找到 dump-test_number-*.sql 文件！")


def run_import():
    dump_path = find_dump_file()
    print(f"[*] 发现全量 SQL Dump: {dump_path} ({os.path.getsize(dump_path)/(1024*1024):.1f} MB)")
    print(f"[*] 目标 SQLite 文件: {TARGET_DB}")

    if os.path.exists(TMP_DB):
        os.remove(TMP_DB)

    conn = sqlite3.connect(TMP_DB)
    conn.execute("PRAGMA synchronous = OFF;")
    conn.execute("PRAGMA journal_mode = MEMORY;")
    conn.execute("PRAGMA cache_size = 200000;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    cur = conn.cursor()

    print("[1/5] 创建数据表结构...")
    cur.executescript("""
    DROP TABLE IF EXISTS questions;
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

    DROP TABLE IF EXISTS question_min_max;
    CREATE TABLE question_min_max (
        question_id INTEGER PRIMARY KEY,
        min_val REAL,
        max_val REAL
    );

    DROP TABLE IF EXISTS schools;
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

    DROP TABLE IF EXISTS answers;
    CREATE TABLE answers (
        school_id INTEGER,
        question_id INTEGER,
        value REAL,
        normalized_value REAL
    );
    """)

    # 1. 导入 questions
    print("[2/5] 导入 questions.csv ...")
    q_csv = os.path.join(REAL_DIR, "questions.csv")
    with open(q_csv, encoding="utf-8") as f:
        q_rows = [
            (int(r["id"]), r["content"], float(r["level3_weight"] or 1),
             r["level1_name"], r["level1_description"], float(r["level1_weight"] or 1),
             r["level2_name"], r["level2_description"], float(r["level2_weight"] or 1))
            for r in csv.DictReader(f)
        ]
    cur.executemany("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)", q_rows)
    print(f"    已导入 {len(q_rows)} 道题目")

    # 2. 导入 schools
    print("[3/5] 导入 schools.csv (37.5万行) ...")
    s_csv = os.path.join(REAL_DIR, "schools.csv")
    s_batch = []
    s_count = 0
    with open(s_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            s_batch.append((
                int(r["id"]), r.get("province"), r.get("city"), r.get("district"),
                r.get("school_name"), r.get("year"), r.get("school_type"),
                r.get("school_area_type"), r.get("class_num"), r.get("teacher_num"),
                r.get("student_num"), int(r.get("data_mark") or 0), r.get("region"),
            ))
            if len(s_batch) >= 50000:
                cur.executemany("INSERT INTO schools VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", s_batch)
                s_count += len(s_batch)
                s_batch = []
        if s_batch:
            cur.executemany("INSERT INTO schools VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", s_batch)
            s_count += len(s_batch)
    print(f"    已导入 {s_count:,} 所学校主数据")

    # 3. 流式解析 SQL Dump 导入 question_min_max 与 school_answers
    print("[4/5] 流式解析 SQL Dump 并导入 answers (约 3111.56 万条) ...")
    t0 = time.time()
    ans_count = 0
    min_max_count = 0
    batch = []
    BATCH_SIZE = 100000

    with open(dump_path, "r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            if "INSERT INTO" in line and "question_min_max" in line:
                val_str = line[line.index("VALUES ") + 7:].rstrip(";\r\n")
                if val_str.startswith("(") and val_str.endswith(")"):
                    items = val_str[1:-1].split("),(")
                    mm_rows = []
                    for it in items:
                        parts = it.split(",")
                        mm_rows.append((int(parts[0]), float(parts[1]), float(parts[2])))
                    cur.executemany("INSERT OR REPLACE INTO question_min_max VALUES (?,?,?)", mm_rows)
                    min_max_count += len(mm_rows)
            elif "INSERT INTO" in line and "school_answers" in line:
                val_start = line.index("VALUES ") + 8
                val_end = line.rindex(")")
                val_str = line[val_start:val_end]
                items = val_str.split("),(")
                for item in items:
                    parts = item.split(",")
                    # parts: [id, school_id, question_id, value, normalized_value]
                    sid = int(parts[1])
                    qid = int(parts[2])
                    val = float(parts[3])
                    norm = float(parts[4]) if parts[4] != "NULL" else None
                    batch.append((sid, qid, val, norm))

                if len(batch) >= BATCH_SIZE:
                    cur.executemany("INSERT INTO answers VALUES (?,?,?,?)", batch)
                    ans_count += len(batch)
                    batch = []
                    elapsed = time.time() - t0
                    speed = ans_count / elapsed if elapsed > 0 else 0
                    percent = (ans_count / 31115621) * 100
                    print(f"    进度: {ans_count:,} / 31,115,621 ({percent:.1f}%) | 速度: {speed:,.0f} 行/秒 | 耗时: {elapsed:.1f}s", end="\r")

    if batch:
        cur.executemany("INSERT INTO answers VALUES (?,?,?,?)", batch)
        ans_count += len(batch)

    conn.commit()
    t_insert = time.time() - t0
    print(f"\n    作答事实表写入完成！共 {ans_count:,} 行，耗时 {t_insert:.1f}s")
    if min_max_count:
        print(f"    已导入 {min_max_count} 条指标归一化极值字典")

    # 4. 创建索引
    print("[5/5] 创建索引（提升检索效率）...")
    t_idx_start = time.time()
    cur.execute("CREATE INDEX idx_answers_school ON answers (school_id);")
    cur.execute("CREATE INDEX idx_answers_question ON answers (question_id);")
    cur.execute("CREATE INDEX idx_schools_name ON schools (school_name);")
    cur.execute("CREATE INDEX idx_schools_year ON schools (year);")
    cur.execute("CREATE INDEX idx_schools_name_year ON schools (school_name, year);")
    cur.execute("CREATE INDEX idx_questions_l1 ON questions (level1_name);")
    conn.commit()
    conn.close()
    t_idx = time.time() - t_idx_start
    print(f"    索引创建完成，耗时 {t_idx:.1f}s")

    # 5. 原子替换
    print("[*] 替换目标数据库...")
    if os.path.exists(TARGET_DB):
        if os.path.exists(BAK_DB):
            os.remove(BAK_DB)
        os.rename(TARGET_DB, BAK_DB)
        print(f"    旧库已备份为: {BAK_DB}")
    os.rename(TMP_DB, TARGET_DB)
    new_size_mb = os.path.getsize(TARGET_DB) / (1024 * 1024)
    print(f"    全量数据库成功上线！文件大小: {new_size_mb:.1f} MB (原约 121 MB)")
    print("[√] 全部导入工作圆满完成！")


if __name__ == "__main__":
    run_import()
