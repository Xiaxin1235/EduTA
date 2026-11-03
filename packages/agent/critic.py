"""M07 · Critic：LLM-as-judge 校验（原型阶段=规则判定 + 可选 LLM 复核）。

检查器集：
  C1 数字无证据来源：报告中的数值必须在证据块中有出处。
  C2 维度间矛盾：同名/相关维度结论冲突。
  C3 小样本下趋势断言：低置信/小样本维度不允许给硬结论。
  C4 红线表述中性化：红线不得人格化评价（如"该教师师德败坏"）。
"""

import re
from typing import List


class RuleCritic:
    """纯规则校验器（无模型可用时启用）。"""

    def __init__(self):
        self._findings: List[dict] = []
        # 非捕获组：避免 findall 返回捕获组内容（经典 Python re 坑）
        self._numeric_re = re.compile(r"\b\d+(?:\.\d+)?%?\b")

    def _add(self, code, severity, message):
        self._findings.append({"code": code, "severity": severity, "message": message})

    def check_numeric_has_evidence(self, report: dict, evidence: List[dict]):
        """C1：只检查叙事文本（narrative/summary）里的数字是否有证据出处。

        纯结构化 result（含 total_score 等派生值）不触发 C1——综合分/覆盖率
        是引擎按权重公式算出的派生值，其"证据"是权重公式，不是幻觉数字。
        报告正文的结论句数字才必须能在证据块中找到。
        """
        narrative_parts = []
        if isinstance(report, dict):
            for key in ("narrative", "summary", "conclusions"):
                val = report.get(key)
                if isinstance(val, str):
                    narrative_parts.append(val)
        if not narrative_parts:
            return  # 无叙事文本，跳过 C1

        ev_raw = {str(e.get("raw")) for e in evidence if e.get("raw") is not None}
        ev_raw |= {str(e.get("value")) for e in evidence if e.get("value") is not None}
        for text in narrative_parts:
            for token in self._numeric_re.findall(text):
                if token and token not in ev_raw and len(token) >= 2:
                    self._add("C1", "medium", f"叙事文本出现无证据来源的数值: {token}")
                    return

    def check_contradiction(self, report: dict):
        """C2：维度间结论不能自相矛盾（原型做最粗粒度：同维度得分与概述）。"""
        dims = report.get("dimensions", []) if isinstance(report, dict) else []
        seen = {}
        for d in dims:
            name = d.get("dimension")
            sc = d.get("score")
            if name in seen and sc is not None and seen[name] != sc:
                self._add("C2", "high", f"维度 {name} 前后得分不一致: {seen[name]} vs {sc}")
            seen[name] = sc

    def check_small_sample_no_trend(self, report: dict):
        """C3：小样本（置信度低）维度不允许趋势断言。"""
        dims = report.get("dimensions", []) if isinstance(report, dict) else []
        for d in dims:
            if d.get("confidence") is not None and d.get("confidence", 1.0) < 0.5:
                if d.get("score") is not None:
                    self._add("C3", "medium",
                              f"维度 {d.get('dimension')} 置信度低({d.get('confidence')})，"
                              f"不应给出确定得分")

    def check_redline_neutral(self, report: dict):
        """C4：红线表述中性化。"""
        txt = str(report)
        for bad in ("师德败坏", "品德恶劣", "有问题教师", "差劲"):
            if bad in txt:
                self._add("C4", "high", f"红线表述含人格化/负面定性词: {bad}")
                return

    def review(self, report: dict, evidence: List[dict]) -> dict:
        self._findings = []
        self.check_numeric_has_evidence(report, evidence)
        self.check_contradiction(report)
        self.check_small_sample_no_trend(report)
        self.check_redline_neutral(report)
        verdict = "pass" if not self._findings else "needs_revision"
        return {"verdict": verdict, "findings": self._findings}


def make_critic(with_llm: bool = True):
    """构造 Critic：有模型时包装 LLM 复核（M06 网关），否则纯规则。"""
    critic = RuleCritic()

    class _Wrap:
        def review(self, report: dict, evidence: List[dict]) -> dict:
            base = critic.review(report, evidence)
            if with_llm:
                # 可选：调用 M06 cross_check 做二次复核（门控在 M07 bench）
                # 原型阶段这里不强依赖模型，保留接口。
                base["llm_reviewed"] = False
            return base

    return _Wrap()


def review(report: dict, evidence: List[dict], with_llm: bool = False) -> dict:
    """顶层便捷函数（SPEC 验收需要 `from packages.agent.critic import review`）。"""
    return make_critic(with_llm=with_llm).review(report, evidence)


if __name__ == "__main__":
    print(review(
        {"dimensions": [{"dimension": "教学", "score": 80.0, "confidence": 0.9}]},
        [{"raw": 80.0}]))