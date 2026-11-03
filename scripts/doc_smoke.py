#!/usr/bin/env python
# 零依赖冒烟检查：守护全仓库基本健康度。
# 运行：python scripts/doc_smoke.py

import json
import os
import py_compile
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_py(path):
    try:
        py_compile.compile(path, doraise=True)
        return True, ""
    except Exception as e:
        return False, str(e)


def smoke():
    ok = True
    problems = []

    def fail(msg):
        nonlocal ok
        ok = False
        problems.append(msg)

    # 1. 主文档 + README
    docs = ["docs/教师评估AI平台-项目大纲.md",
            "docs/需求确认单.md",
            "docs/系统设计文档-v0.1.md",
            "README.md"]
    for d in docs:
        p = os.path.join(ROOT, d)
        if not (os.path.isfile(p) and os.path.getsize(p) > 0):
            fail(f"文档缺失或为空: {d}")

    # 2. 数据契约可解析
    try:
        with open(os.path.join(ROOT, "contracts", "data_contract_v0.1.json"), encoding="utf-8") as f:
            contract = json.load(f)
        if contract.get("contract_version") != "0.1":
            fail("数据契约 contract_version != 0.1")
    except Exception as e:
        fail(f"数据契约解析失败: {e}")

    # 3. 登记表完整性
    try:
        with open(os.path.join(ROOT, "modules", "registry.json"), encoding="utf-8") as f:
            reg = json.load(f)
        ids = [m["id"] for m in reg["modules"]]
        if len(ids) != len(set(ids)):
            fail("registry.json 模块 id 重复")
        id_set = set(ids)
        for m in reg["modules"]:
            for dep in m.get("depends", []):
                if dep not in id_set:
                    fail(f"模块 {m['id']} 依赖不存在的 {dep}")
            if m["status"] not in ("done", "todo"):
                fail(f"模块 {m['id']} status 非法: {m['status']}")
            if m["status"] == "done":
                for prod in m.get("produces", []):
                    if not os.path.isfile(os.path.join(ROOT, prod)):
                        fail(f"模块 {m['id']} 标注 done 但产物缺失: {prod}")
    except Exception as e:
        fail(f"registry.json 解析失败: {e}")

    # 4. scripts/*.py 可编译
    scripts_dir = os.path.join(ROOT, "scripts")
    py_files = sorted(f for f in os.listdir(scripts_dir) if f.endswith(".py"))
    for fn in py_files:
        good, err = check_py(os.path.join(scripts_dir, fn))
        if not good:
            fail(f"脚本编译失败: scripts/{fn} -> {err}")

    # 5. 评估模板 YAML 可解析
    try:
        import yaml
        with open(os.path.join(ROOT, "evaluation_templates", "2025-2026_v1.yaml"), encoding="utf-8") as f:
            yaml.safe_load(f)
    except Exception as e:
        fail(f"评估模板 YAML 解析失败: {e}")

    # 6. data/demo 9 张模拟表
    demo = os.path.join(ROOT, "data", "demo")
    tables = ["teacher", "course_period", "exam_score", "student_survey",
              "class_observation", "lesson_plan", "research_record",
              "complaint_record", "discipline_record"]
    for t in tables:
        p = os.path.join(demo, t + ".csv")
        if not (os.path.isfile(p) and os.path.getsize(p) > 0):
            fail(f"模拟表缺失或为空: data/demo/{t}.csv")

    # 报告
    print("=" * 60)
    print("doc_smoke.py · 零依赖冒烟检查")
    print("=" * 60)
    if ok:
        print("全部检查通过")
        print("=" * 60)
        return 0
    for p in problems:
        print(f"✗ {p}")
    print("=" * 60)
    print(f"共 {len(problems)} 项失败")
    return 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(smoke())