"""M07 bench：示例宽泛问题走完整 pipeline 的演示入口（有/无模型都能跑）。

用法：
  python packages/agent/bench.py "张老师近三年教学水平怎么样？"
默认纯规则 Critic（无模型）；READ API key 环境变量存在时尝试 LLM 复核（掉链则回退）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from packages.agent.planner import parse_question, plan
from packages.agent.critic import make_critic
from packages.agent.composer import compose
from packages.indicator_engine.calibration import attach_calibration  # noqa: F401
from scripts.indicator_engine import evaluate_teacher, load_yaml, METRIC_FUNCS


def run_pipeline(question_text: str):
    q = parse_question(question_text)
    if q.clarify_needed:
        return {
            "clarify": True,
            "question": q.__dict__,
            "next_prompt": f"请补充：{', '.join(q.missing)}",
        }

    template = load_yaml("2025-2026_v1")
    dag = plan(q, template)
    teacher_id = q.objects[0]
    # 直接复用 M02 引擎算全维度（生产版把 evaluate_teacher 拆成单个 retriever 调用）
    result = evaluate_teacher(teacher_id, q.years, template)
    attach_calibration(result, group_col="dept_id")

    # 证据块：从 result.dimensions 抽取（M02 语义，M07 的 retriever 也消费同一结构）
    evidence = []
    for d in result.get("dimensions", []):
        evidence.extend(d.get("evidence", []))

    # Critic 校验（规则 + 可选 LLM）
    has_llm = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("QWEN_API_KEY"))
    verdict = make_critic(with_llm=has_llm).review(result, evidence)

    # M08 合成报告（人类可读叙事，供 Golden 的 must_mention / 幻觉检查用）
    report = compose(result)

    return {
        "question": q.__dict__,
        "dag": dag,
        "result": result,
        "evidence": evidence,
        "critic": verdict,
        "report": report,
    }


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    text = sys.argv[1] if len(sys.argv) > 1 else "张老师近三年教学水平怎么样？"
    out = run_pipeline(text)
    if out.get("clarify"):
        print("[需澄清]", out["next_prompt"])
    else:
        r = out["result"]
        print(f"教师={r['teacher_id']} 综合={r['total_score']} 覆盖率={r['data_coverage']} 红线={r['redline_flagged']}")
        print(f"Critic 判定：{out['critic']['verdict']}")
        for f_ in out["critic"].get("findings", []):
            print(f"  - [{f_['severity']}] {f_['code']}: {f_['message']}")