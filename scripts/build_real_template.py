# 从 data/real/questions.csv 生成学校数字化评估模板。
# 运行：python scripts/build_real_template.py

import csv
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build():
    path = os.path.join(ROOT, "data", "real", "questions.csv")
    dims = defaultdict(lambda: {"weight": 1.0, "description": "", "metrics": []})
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            l1 = r["level1_name"]
            dims[l1]["description"] = r["level1_description"]
            dims[l1]["weight"] = float(r["level1_weight"] or 1)
            dims[l1]["metrics"].append({
                "id": int(r["id"]),
                "content": r["content"],
                "level2": r["level2_name"],
                "weight": float(r["level3_weight"] or 1),
            })

    # 一级维度权重归一化（问卷里 level1_weight 全是 1.0，按维度均分）
    names = list(dims.keys())
    n = len(names) or 1
    equal = round(1.0 / n, 4)

    lines = [
        "# 学校数字化评估模板（由 questions.csv 自动生成）",
        "# 一级维度均分权重；题面归一化得分 0~1 ×100 后加权。",
        "template:",
        '  name: "学校数字化评估 v1（真实问卷）"',
        '  school_year: "2020-2023"',
        "  version: 1",
        "  object_type: school",
        "  weight_total: 1.0",
        "",
        "dimensions:",
    ]
    for i, name in enumerate(names):
        w = 1.0 - equal * (n - 1) if i == n - 1 else equal
        d = dims[name]
        key = {
            "数字资源": "digital_resource",
            "教育教学": "teaching",
            "数字素养": "digital_literacy",
            "基础设施": "infrastructure",
            "教育治理": "governance",
            "保障机制": "support",
        }.get(name, f"dim_{i+1}")
        lines.append(f"  - key: {key}")
        lines.append(f"    name: {name}")
        lines.append(f"    weight: {w:.4f}")
        lines.append(f"    description: {d['description']}")
        lines.append("    metrics:")
        for m in d["metrics"]:
            lines.append(f"      - id: {m['id']}")
            lines.append(f"        content: {m['content']}")
            lines.append(f"        level2: {m['level2']}")
            lines.append(f"        weight: {m['weight']}")
        lines.append("")

    out = os.path.join(ROOT, "evaluation_templates", "school_digitization_v1.yaml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {out}  dimensions={len(names)}")
    return out


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    build()
