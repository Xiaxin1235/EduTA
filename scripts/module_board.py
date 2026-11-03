#!/usr/bin/env python
# 模块看板：一条命令告诉 agent "哪些模块完成、下一步该做哪个"
# 运行：python scripts/module_board.py

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG = os.path.join(ROOT, "modules", "registry.json")


def load_registry():
    with open(REG, encoding="utf-8") as f:
        return json.load(f)


def board(reg):
    mods = reg["modules"]
    by_id = {m["id"]: m for m in mods}
    done = {m["id"] for m in mods if m["status"] == "done"}
    lines = []
    lines.append("=" * 62)
    lines.append("模块看板  （done=已完成 / todo=待做 / blocked=依赖未完成）")
    lines.append("=" * 62)
    for m in mods:
        if m["status"] == "done":
            mark = "[done]    "
        elif all(dep in done for dep in m.get("depends", [])):
            mark = "[todo]    "
        else:
            mark = "[blocked] "
        deps = ",".join(m.get("depends", [])) or "-"
        lines.append(f"{mark} {m['id']}  {m['name']:<30} 依赖: {deps}")
    ready = [m for m in mods if m["status"] == "todo" and all(d in done for d in m.get("depends", []))]
    lines.append("-" * 62)
    if ready:
        nxt = ready[0]
        lines.append(f"下一步建议：{nxt['id']} {nxt['name']}")
        lines.append(f"  读 {nxt['spec']} 后开工，交付：{', '.join(nxt['produces'])}")
    else:
        lines.append("没有可开始的 todo 模块（全部完成或被依赖阻塞）。")
    lines.append("=" * 62)
    return "\n".join(lines), ready


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    reg = load_registry()
    text, ready = board(reg)
    print(text)
    if ready:
        print("ready_ids=" + ",".join(m["id"] for m in ready))
    return 0


if __name__ == "__main__":
    sys.exit(main())