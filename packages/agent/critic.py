import re
from typing import List

class RuleCritic:
    pass

    def __init__(self):
        self._findings: List[dict] = []

        self._numeric_re = re.compile(r"\b\d+(?:\.\d+)?%?\b")

    def _add(self, code, severity, message):
        self._findings.append({"code": code, "severity": severity, "message": message})

    def check_numeric_has_evidence(self, report: dict, evidence: List[dict]):
        pass
        narrative_parts = []
        if isinstance(report, dict):
            for key in ("narrative", "summary", "conclusions"):
                val = report.get(key)
                if isinstance(val, str):
                    narrative_parts.append(val)
        if not narrative_parts:
            return

        ev_raw = {str(e.get("raw")) for e in evidence if e.get("raw") is not None}
        ev_raw |= {str(e.get("value")) for e in evidence if e.get("value") is not None}
        for text in narrative_parts:
            for token in self._numeric_re.findall(text):
                if token and token not in ev_raw and len(token) >= 2:
                    self._add("C1", "medium", f"叙事文本出现无证据来源的数值: {token}")
                    return

    def check_contradiction(self, report: dict):
        pass
        dims = report.get("dimensions", []) if isinstance(report, dict) else []
        seen = {}
        for d in dims:
            name = d.get("dimension")
            sc = d.get("score")
            if name in seen and sc is not None and seen[name] != sc:
                self._add("C2", "high", f"维度 {name} 前后得分不一致: {seen[name]} vs {sc}")
            seen[name] = sc

    def check_small_sample_no_trend(self, report: dict):
        pass
        dims = report.get("dimensions", []) if isinstance(report, dict) else []
        for d in dims:
            if d.get("confidence") is not None and d.get("confidence", 1.0) < 0.5:
                if d.get("score") is not None:
                    self._add("C3", "medium",
                              f"维度 {d.get('dimension')} 置信度低({d.get('confidence')})，"
                              f"不应给出确定得分")

    def check_redline_neutral(self, report: dict):
        pass
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
    pass
    critic = RuleCritic()

    class _Wrap:
        def review(self, report: dict, evidence: List[dict]) -> dict:
            base = critic.review(report, evidence)
            if with_llm:

                base["llm_reviewed"] = False
            return base

    return _Wrap()

def review(report: dict, evidence: List[dict], with_llm: bool = False) -> dict:
    pass
    return make_critic(with_llm=with_llm).review(report, evidence)

if __name__ == "__main__":
    print(review(
        {"dimensions": [{"dimension": "教学", "score": 80.0, "confidence": 0.9}]},
        [{"raw": 80.0}]))
