import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

@dataclass
class Question:
    objects: List[str] = field(default_factory=list)
    years: List[str] = field(default_factory=list)
    intent: str = "comprehensive"
    missing: List[str] = field(default_factory=list)
    original: str = ""
    object_type: str = "unknown"

    @property
    def clarify_needed(self) -> bool:
        return bool(self.objects) is False or bool(self.missing)

@dataclass
class Task:
    id: str
    dimension: str
    depends: List[str] = field(default_factory=list)
    is_redline: bool = False
    note: str = ""

_TEACHER_RE = re.compile(r"\b(T\d{2,})\b")
_YEAR_RE = re.compile(r"20\d{2}-20\d{2}")
_CAL_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_SCHOOL_RE = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]{2,40}(?:小学|中学|学校|幼儿园|学院|高中)")
_SCHOOL_ID_RE = re.compile(r"\b(?:S|学校)(\d{1,8})\b", re.I)

_NEAR_YEARS_RE = re.compile(r"(?:近|最近|过去)?\s*(?P<num>[\d一二两三四五六七八九十]{1,3})?\s*年")
_CURRENT_YEAR_HINT = re.compile(r"(?:今年|本学年|最近一学年|当前学年)")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

def _cn2num(s: str) -> int:
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s, 0) or 2

def _expand_near_years(text: str, current_year: int = 2025) -> List[str]:
    pass
    m = _CURRENT_YEAR_HINT.search(text)
    if m:
        return [f"{current_year}-{current_year + 1}"]
    n_m = _NEAR_YEARS_RE.search(text)
    if not n_m:
        return []
    n = _cn2num(n_m.group("num") or "2")
    n = max(1, min(n, 6))

    start = current_year - (n - 1)
    return [f"{y}-{y + 1}" for y in range(start, current_year + 1)]

def _calendar_years(text: str) -> List[str]:
    pass
    ranges = _YEAR_RE.findall(text)
    occupied = set()
    for r in ranges:
        occupied.update(r.split("-"))
    out = []
    for y in _CAL_YEAR_RE.findall(text):
        if y not in occupied:
            out.append(y)
    return sorted(set(out))

def parse_question(text: str) -> Question:
    pass
    q = Question(original=text)
    teachers = sorted(set(_TEACHER_RE.findall(text)))
    schools = sorted(set(_SCHOOL_RE.findall(text)))
    school_ids = _SCHOOL_ID_RE.findall(text)

    q.years = sorted(set(_YEAR_RE.findall(text)))
    cal = _calendar_years(text)

    if teachers:
        q.object_type = "teacher"
        q.objects = teachers
        if not q.years:
            q.years = _expand_near_years(text)
    elif schools or school_ids:
        q.object_type = "school"
        q.objects = schools or [f"id:{i}" for i in school_ids]
        if cal:
            q.years = cal
        elif q.years:
            q.years = [y.split("-")[0] for y in q.years]
        else:
            near = _expand_near_years(text, current_year=2023)
            q.years = [y.split("-")[0] for y in near] if near else []
    else:
        q.object_type = "unknown"
        if not q.years:
            q.years = _expand_near_years(text)

    if any(k in text for k in ("趋势", "变化", "对比", "进步", "退步")):
        q.intent = "trend"
    elif any(k in text for k in ("哪方面", "最弱", "单项", "评教", "课堂", "教学效果", "教案",
                                 "数字资源", "基础设施", "数字素养", "教育治理", "保障")):
        q.intent = "single_dimension"

    if not q.objects:
        q.missing.append("object: 未识别到教师或学校（请给出教师ID如 T001，或学校全名）")
    elif q.object_type == "teacher" and len(q.objects) > 1:
        q.missing.append(f"object: 检测到多个教师（{','.join(q.objects)}），请指定单个教师")
    if not q.years:
        q.missing.append("years: 未识别学年（默认最近一学年需确认）")
    return q

def _year_suffix(y: str) -> str:
    pass
    parts = y.split("-")
    return f"{parts[0][2:]}-{parts[1][2:]}" if len(parts) == 2 else y

def plan(question: Question, template: dict) -> dict:
    pass
    tasks = []
    for dim in template.get("dimensions", []):
        if dim.get("is_redline"):
            tasks.append(Task(
                id=f"t_{dim.get('key', 'redline')}",
                dimension=dim.get("name", "红线"),
                depends=[],
                is_redline=True,
                note="红线检查，独立判定，不参与加权",
            ))
        else:
            tasks.append(Task(
                id=f"t_{dim.get('key', 'dim')}",
                dimension=dim.get("name", "维度"),
                depends=[],
                is_redline=False,
            ))
    return {
        "question": asdict(question),
        "tasks": [asdict(t) for t in tasks],
        "parallel": True,
    }

if __name__ == "__main__":
    q = parse_question("示例教师一近三年教学水平怎么样？")
    print("objects:", q.objects, "years:", q.years, "intent:", q.intent, "missing:", q.missing)
