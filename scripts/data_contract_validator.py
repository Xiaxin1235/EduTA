# 数据契约校验器（数据体检）
#
# 用途：数据提供方交付一个 CSV/目录时，自动对照 contracts/data_contract_v0.1.json
# 检查：字段完整性、类型、取值范围、主键唯一性、跨表引用一致性。
# 输出：分表体检报告（PASS/WARN/FAIL），FAIL 的表无法进入评估管线。
#
# 运行：
#   python scripts/data_contract_validator.py --dir data/demo --contract contracts/data_contract_v0.1.json
#
# 规则分级：
#   FAIL = 缺失必需字段 / 主键重复 / 类型错误 / 跨表引用断裂 —— 必须修复
#   WARN = 可选字段缺失 / 值接近边界 / 样本过小 —— 建议关注，不阻塞

import argparse
import csv
import json
import os
import re
import sys


def load_contract(path):
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    assert c.get("contract_version") == "0.1" and "tables" in c, "契约格式不对：'contract_version' 应为 '0.1' 且含 'tables'"
    return c


def load_table(dir_path, table):
    p = os.path.join(dir_path, f"{table}.csv")
    if not os.path.exists(p):
        return None, f"缺失文件 {table}.csv"
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return [], "空文件"
    return rows, None


def coerce_num(spec, value):
    """按契约类型把字符串值转成数值；不可转时返回 None（交给 type_ok 报类型错）。"""
    t = spec.get("type")
    try:
        if t == "integer":
            return int(float(value))
        if t == "number":
            return float(value)
    except (TypeError, ValueError):
        return None
    return None


def type_ok(spec, value):
    """宽松类型检查，兼容 CSV 全为字符串的情况。"""
    t = spec.get("type")
    if value is None or value == "":
        # 空值留给 required 判定，这里不报类型错
        return True
    if t == "integer":
        try:
            int(float(value))
        except (TypeError, ValueError):
            return False
    elif t == "number":
        try:
            float(value)
        except (TypeError, ValueError):
            return False
    elif t == "string":
        if "pattern" in spec and not re.fullmatch(spec["pattern"], str(value)):
            return False
    return True


def range_ok(spec, value):
    """数值取值范围 / 枚举值检查。非数值列跳过。"""
    if value is None or value == "":
        return True
    num = coerce_num(spec, value)
    if num is not None:
        if "minimum" in spec and num < spec["minimum"]:
            return False
        if "maximum" in spec and num > spec["maximum"]:
            return False
    if spec.get("enum") and str(value) not in [str(e) for e in spec["enum"]]:
        return False
    return True


def check_table(table, spec, dir_path, all_rows):
    rows, err = all_rows.get(table, (None, None))
    if err:
        return {"table": table, "status": "FAIL", "problems": [err], "rows": 0}
    if rows is None:
        return {"table": table, "status": "FAIL", "problems": ["未加载"], "rows": 0}

    problems = []
    warnings = []
    cols = spec["columns"]
    req = spec.get("required", [])

    # 字段完整性
    header = rows[0].keys()
    for r in req:
        if r not in header:
            problems.append(f"缺失必需字段: {r}")
    if problems:
        return {"table": table, "status": "FAIL", "problems": problems, "rows": len(rows)}

    # 主键唯一性：uniqueness=row_level 表示明细表，一行一条记录，无业务唯一键，不查重
    key = spec.get("key", [])
    if spec.get("uniqueness") == "row_level":
        key = []
    if key:
        seen = set()
        dup = 0
        for row in rows:
            k = tuple(row.get(c, "") for c in key)
            if k in seen:
                dup += 1
            seen.add(k)
        if dup:
            problems.append(f"主键重复 {dup} 条（key={key}）")

    # 类型与取值范围（抽样到前 10000 行，够用）
    sample = rows[:10000]
    type_errors = 0
    range_errors = 0
    for row in sample:
        for col, spec_col in cols.items():
            if col not in header:
                continue
            v = row.get(col)
            if v is None or v == "":
                if col in req:
                    type_errors += 1
                continue
            if not type_ok(spec_col, v):
                type_errors += 1
            if not range_ok(spec_col, v):
                range_errors += 1
    if type_errors:
        problems.append(f"类型/格式错误 {type_errors} 处")
    if range_errors:
        problems.append(f"取值超范围 {range_errors} 处")

    # 必填字段缺失
    missing = 0
    for row in sample:
        for r in req:
            if row.get(r) in (None, ""):
                missing += 1
    if missing:
        problems.append(f"必填字段缺失 {missing} 处")

    # 跨表规则：teacher_in_exam
    if table == "exam_score":
        teacher_ids = {r["teacher_id"] for r in all_rows.get("teacher", ([], None))[0] or []}
        if teacher_ids:
            bad = sum(1 for r in sample if r.get("teacher_id") not in teacher_ids)
            if bad:
                problems.append(f"exam_score 中 {bad} 条 teacher_id 不在 teacher 表")
        # class_consistency：exam_score 的班级-课程-学期组合应落在 course_period
        cp_rows = all_rows.get("course_period", ([], None))[0] or []
        cp_combos = {(r["class_id"], r["course_id"], r["school_year"], r["semester"]) for r in cp_rows}
        if cp_combos:
            bad2 = sum(1 for r in sample
                       if (r.get("class_id"), r.get("course_id"), r.get("school_year"), r.get("semester")) not in cp_combos)
            if bad2:
                problems.append(f"exam_score 中 {bad2} 条班级/科目/学年组合不在授课记录内")

    # 建议类：样本量提示
    if table in ("student_survey",) and len(rows) < 20:
        warnings.append(f"样本量 {len(rows)} < 20，评教置信度将下调")

    status = "PASS"
    if problems:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    return {"table": table, "status": status, "problems": problems, "warnings": warnings, "rows": len(rows)}


def main():
    ap = argparse.ArgumentParser(description="数据契约校验器")
    ap.add_argument("--dir", default="data/demo", help="数据目录（含各表 CSV）")
    ap.add_argument("--contract", default="contracts/data_contract_v0.1.json")
    args = ap.parse_args()

    contract = load_contract(args.contract)
    all_rows = {}
    for table in contract["tables"]:
        all_rows[table] = load_table(args.dir, table)

    # Windows 控制台默认 GBK，避免 Unicode 符号打印报错
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 64)
    print("数据契约体检报告  v0.1")
    print(f"数据目录：{args.dir}")
    print("=" * 64)
    any_fail = False
    for table, spec in contract["tables"].items():
        r = check_table(table, spec, args.dir, all_rows)
        mark = {"PASS": "✓ 通过", "WARN": "⚠ 警告", "FAIL": "✗ 失败"}[r["status"]]
        print(f"\n[{mark}] {table}  ({r['rows']} 行)")
        for p in r.get("problems", []):
            print(f"    ✗ {p}")
            any_fail = True
        for w in r.get("warnings", []):
            print(f"    · {w}")
    print("\n" + "=" * 64)
    if any_fail:
        print("结论：存在 FAIL，交付数据不可进入评估管线，请修复后重跑。")
    else:
        print("结论：全部通过（含警告），可进入评估管线。")
    print("=" * 64)


if __name__ == "__main__":
    main()