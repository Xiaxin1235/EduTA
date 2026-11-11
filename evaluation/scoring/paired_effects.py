"""Paired effect sizes for run 1 (Claude, 2026-11-10): for EduTA against each system, the counts both correct / only EduTA /
only other / both wrong, the paired difference in accuracy (EduTA minus other) with Newcombe's hybrid-score 95% CI for
paired proportions (method 10, Newcombe 1998), the exact sign test, and Holm-adjusted p over the six comparisons.
Input: site_data_v3_public.json (signed run-1 statuses). Writes PAIRED_EFFECTS.json."""
import json, math
from pathlib import Path
S = Path(__file__).resolve().parent
D = json.loads((S.parent / "test_bank_and_results" / "site_data_v3_public.json").read_text("utf-8"))["questions"]
z = 1.959964

def wilson(k, n):
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h

def newcombe_paired(a, b, c, d):
    n = a + b + c + d; p1 = (a + b) / n; p2 = (a + c) / n; diff = p1 - p2
    l1, u1 = wilson(a + b, n); l2, u2 = wilson(a + c, n)
    A = (a + b) * (c + d) * (a + c) * (b + d)
    phi = 0 if A == 0 else (a * d - b * c) / math.sqrt(A)
    if a * d - b * c > 0: phi = max(0, (a * d - b * c - n / 2)) / math.sqrt(A) if A else 0
    dl = math.sqrt((p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2)
    du = math.sqrt((u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2)
    return diff, diff - dl, diff + du

def sign(x, y):
    n = x + y; return min(1, 2 * sum(math.comb(n, i) for i in range(min(x, y) + 1)) / 2 ** n) if n else 1

res = {}
for o in ["No-correction", "Rule-based", "Tool-calling", "Tool-calling+chart", "Direct-SQL", "MAC-SQL"]:
    e = [q["systems"]["EduTA"]["correct"] for q in D]; t = [q["systems"][o]["correct"] for q in D]
    a = sum(x and y for x, y in zip(e, t)); b = sum(x and not y for x, y in zip(e, t)); c = sum(y and not x for x, y in zip(e, t)); d = len(D) - a - b - c
    diff, lo, hi = newcombe_paired(a, b, c, d)
    res[o] = {"both": a, "only_EduTA": b, "only_other": c, "neither": d, "diff_pp": round(100 * diff, 1),
              "ci_pp": [round(100 * lo, 1), round(100 * hi, 1)], "p": sign(b, c)}
ps = sorted(res, key=lambda k: res[k]["p"]); m = len(ps); run = 0
for i, k in enumerate(ps):
    run = max(run, min(1, (m - i) * res[k]["p"])); res[k]["p_holm"] = run
for k in res: res[k]["p"] = round(res[k]["p"], 6); res[k]["p_holm"] = round(res[k]["p_holm"], 4)
(Path.cwd() / "PAIRED_EFFECTS.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
for k, v in res.items(): print(k, v)
