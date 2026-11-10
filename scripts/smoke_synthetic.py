"""Runnability check on synthetic data (no real data, no model, no network).

Generates a fresh fictional database with scripts/generate_synthetic_data.py, points the application at it, and sends example
questions through the application's own chat entry point in rule mode (the rule parser, Rule-based in the paper). For each
question it checks that the registered tools executed and returned values, and that requested charts were written as PNG files.
Security middleware is not touched: the entry point is called in-process, as the web route does.

    python -X utf8 scripts/smoke_synthetic.py            # writes output/smoke_synthetic/<timestamp>/SMOKE_REPORT.json
"""
import json, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT = ROOT / "output" / "smoke_synthetic" / STAMP
DATA = ROOT / "data" / f"synthetic_smoke_{STAMP}"
QUESTIONS = [
    "示例市2023年基础设施平均分是多少？",
    "示例市示例区示例一小学2023年的整体得分是多少？",
    "示例市示例区示例一小学2023年第9题原始值是多少？",
    "示例市和演示市2023年教育教学平均分比较。",
    "示例市示例区示例一小学2023年六个维度得分雷达图",
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    gen = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/generate_synthetic_data.py"), "--output", str(DATA), "--with-overlay"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if gen.returncode != 0:
        sys.exit("synthetic data generation failed:\n" + gen.stderr[-2000:])
    os.environ["TEACHER_EVAL_RESEARCH_CONFIG"] = str(DATA / "research_dataset.json")
    os.environ["TEACHER_EVAL_CONTRACT_DB"] = str(OUT / "contract.sqlite")
    os.environ["TEACHER_EVAL_CHARTS_DIR"] = str(OUT / "charts")
    (OUT / "charts").mkdir(exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from apps.api.main import chat  # noqa: E402

    rows = []
    for i, q in enumerate(QUESTIONS, 1):
        t0 = time.perf_counter()
        try:
            r = chat(q, session_id=f"smoke-{STAMP}-{i}", query_mode="rule")
            err = None
        except Exception as exc:  # recorded, not hidden
            r, err = {}, f"{type(exc).__name__}: {exc}"
        text = json.dumps(r, ensure_ascii=False, default=str)
        receipt = r.get("execution_receipt") or {}
        charts = r.get("charts") or []
        pngs = sorted(p.name for p in (OUT / "charts").rglob("*.png"))
        ok = err is None and r.get("business_status") == "ANSWER" and bool(receipt.get("execution_id")) and bool(r.get("markdown"))
        rows.append({"question": q, "error": err, "elapsed_s": round(time.perf_counter() - t0, 2), "ok": ok,
                     "business_status": r.get("business_status"), "execution_id": receipt.get("execution_id"),
                     "dataset_version": receipt.get("dataset_version"), "answer_head": str(r.get("markdown") or "")[:300],
                     "charts": [str(c)[:200] for c in charts], "png_files": pngs})
        (OUT / f"response_{i}.json").write_text(text, "utf-8")
    chart_ok = bool(rows[-1]["charts"]) or bool(rows[-1]["png_files"])
    report = {"generated_data": str(DATA.relative_to(ROOT)), "results": rows, "chart_ok": chart_ok,
              "passed": all(r["ok"] for r in rows) and chart_ok}
    (OUT / "SMOKE_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    for r in rows:
        print("OK " if r["ok"] else "ERR", r["question"], "|", r["business_status"], "|", (r["error"] or r["answer_head"][:90]).replace("\n", " "))
    print("chart:", rows[-1]["charts"][:1], rows[-1]["png_files"][:3]); print("PASSED" if report["passed"] else "FAILED", "->", OUT.relative_to(ROOT))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
