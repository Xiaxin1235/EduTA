# 生成模拟（脱敏）教师评估数据，供指标引擎原型演示。
# 注意：全部为虚构数据，仅用于验证评估管线，不含任何真实个人信息。
# 运行：python scripts/mock_transform.py

import csv
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data", "demo")
os.makedirs(DATA_DIR, exist_ok=True)

random.seed(42)  # 结果可复现

YEARS = ["2023-2024", "2024-2025", "2025-2026"]
SEMESTERS = ["春", "秋"]


def write(name, fieldnames, rows):
    path = os.path.join(DATA_DIR, name + ".csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {name}.csv ({len(rows)} rows)")


def main():
    print("[mock] generating demo data ...")

    # 教师主数据（姓名用匿名 ID，不出现真实姓名）
    teachers = [
        {"teacher_id": "T001", "name_hash": "h-T001", "dept_id": "数学组", "title": "高级", "hire_year": 2010, "is_active": "1"},
        {"teacher_id": "T002", "name_hash": "h-T002", "dept_id": "数学组", "title": "中级", "hire_year": 2016, "is_active": "1"},
        {"teacher_id": "T003", "name_hash": "h-T003", "dept_id": "语文组", "title": "初级", "hire_year": 2021, "is_active": "1"},
        {"teacher_id": "T004", "name_hash": "h-T004", "dept_id": "英语组", "title": "高级", "hire_year": 2005, "is_active": "1"},
    ]
    write("teacher", list(teachers[0].keys()), teachers)

    # 授课记录
    course_period = []
    for t in teachers:
        for y in YEARS:
            for s in SEMESTERS:
                course_period.append({
                    "period_id": f"P-{t['teacher_id']}-{y}-{s}",
                    "teacher_id": t["teacher_id"],
                    "course_id": "数学" if t["dept_id"] == "数学组" else ("语文" if t["dept_id"] == "语文组" else "英语"),
                    "school_year": y,
                    "semester": s,
                    "class_id": f"C{t['teacher_id']}-{y[-2:]}",
                    "student_count": str(random.randint(30, 45)),
                    "class_hours": str(random.randint(120, 180)),
                })
    write("course_period", list(course_period[0].keys()), course_period)

    # 教案（覆盖率差异：T001 高、T003 低；同一教师同学年聚合为一条）
    lesson_plan = []
    for t in teachers:
        for y in YEARS:
            rate = {"T001": 1.0, "T002": 0.9, "T003": 0.5, "T004": 0.95}[t["teacher_id"]]
            # 该教师该学年所有授课记录的总课时
            total_hours = sum(int(cp["class_hours"]) for cp in course_period
                              if cp["teacher_id"] == t["teacher_id"] and cp["school_year"] == y)
            lesson_plan.append({
                "teacher_id": t["teacher_id"],
                "school_year": y,
                "submitted_hours": str(round(total_hours * rate, 1)),
            })
    write("lesson_plan", list(lesson_plan[0].keys()), lesson_plan)

    # 听课记录（次数差异）
    class_observation = []
    for t in teachers:
        n = {"T001": 6, "T002": 4, "T003": 2, "T004": 5}[t["teacher_id"]]
        base = {"T001": 88, "T002": 82, "T003": 75, "T004": 90}[t["teacher_id"]]
        for i in range(n):
            y = YEARS[i % 2]
            class_observation.append({
                "teacher_id": t["teacher_id"],
                "school_year": y,
                "score": str(round(base + random.uniform(-5, 5), 1)),
            })
    write("class_observation", list(class_observation[0].keys()), class_observation)

    # 成绩明细（增值口径：T001 基准之上，T003 明显落后）
    # 学生匿名 ID 跨学期稳定（契约 anon_id_stable）：
    # 同一班级编号（class_index）的学生在不同学期共享同一 anon_id，
    # 用固定序列号 0..n_students-1，不用内置 id()。
    exam_score = []
    for cp in course_period:
        cohort_base = {"数学": 0.70, "语文": 0.72, "英语": 0.68}[cp["course_id"]]
        shift = {"T001": +0.10, "T002": +0.03, "T003": -0.12, "T004": +0.13}[cp["teacher_id"]]
        pass_rate = min(0.98, max(0.2, cohort_base + shift))
        n_students = int(cp["student_count"])
        for i in range(n_students):
            if random.random() < pass_rate:
                s = random.randint(60, 100)
            else:
                s = random.randint(30, 59)
            stable_anon = f"S-{cp['teacher_id']}-{i:03d}"  # 同教师同学号跨学期稳定
            exam_score.append({
                "school_year": cp["school_year"],
                "semester": cp["semester"],
                "class_id": cp["class_id"],
                "course_id": cp["course_id"],
                "teacher_id": cp["teacher_id"],
                "student_anon_id": stable_anon,
                "score_val": str(s),
                "exam_type": "期末",
            })
    write("exam_score", list(exam_score[0].keys()), exam_score)

    # 学生评教（样本量差异）
    student_survey = []
    for t in teachers:
        n = {"T001": 120, "T002": 100, "T003": 60, "T004": 110}[t["teacher_id"]]
        base = {"T001": 4.5, "T002": 4.2, "T003": 3.6, "T004": 4.7}[t["teacher_id"]]
        for y in YEARS:
            for _ in range(n // 2):
                student_survey.append({
                    "teacher_id": t["teacher_id"],
                    "school_year": y,
                    "score": str(round(min(5.0, max(1.0, base + random.uniform(-0.6, 0.6))), 1)),
                })
    write("student_survey", list(student_survey[0].keys()), student_survey)

    # 教研记录
    research_record = []
    for t in teachers:
        for level, cnt in [("school", 2), ("municipal", 1)]:
            research_record.append({
                "teacher_id": t["teacher_id"],
                "school_year": random.choice(YEARS),
                "level": level,
                "count": str(cnt),
            })
        if t["teacher_id"] in ("T001", "T004"):
            research_record.append({"teacher_id": t["teacher_id"], "school_year": YEARS[-1], "level": "provincial", "count": "1"})
    write("research_record", list(research_record[0].keys()), research_record)

    # 师德红线：T003 有一条已查实投诉+违规（触发红线），另加一条未查实投诉（不应触发，验证 verified 过滤）
    complaint_record = [
        {"teacher_id": "T003", "school_year": "2024-2025", "type": "投诉（模拟）", "verified": "1"},
        {"teacher_id": "T002", "school_year": "2024-2025", "type": "投诉未查实（模拟）", "verified": "0"},
    ]
    write("complaint_record", list(complaint_record[0].keys()), complaint_record)
    discipline_record = [
        {"teacher_id": "T003", "school_year": "2024-2025", "type": "违规记录（模拟）", "verified": "1"},
        {"teacher_id": "T002", "school_year": "2024-2025", "type": "违规未查实（模拟）", "verified": "0"},
    ]
    write("discipline_record", list(discipline_record[0].keys()), discipline_record)

    print("[mock] done. 使用方法：python scripts/indicator_engine.py --json-out")


if __name__ == "__main__":
    main()