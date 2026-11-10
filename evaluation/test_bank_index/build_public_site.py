"""Rebuild the public site from the single-file page template by replacing its embedded dataset with site_data_v3_public.json
(Claude, 2026-11-09). The page code is GPT's (page_template.html, written with an OpenAI coding assistant), unchanged; only the <script id="dataset"> block is replaced.
Checks: no reference values, review reasons, local paths or external URLs in the output; strict CSP kept."""
import json, re, sys
from pathlib import Path
S = Path(__file__).resolve().parent
page = (S / "page_template.html").read_text("utf-8")
data = json.loads((S.parent / "test_bank_and_results" / "site_data_v3_public.json").read_text("utf-8"))
OPEN = '<script id="dataset" type="application/json">'
i = page.index(OPEN) + len(OPEN); j = page.index("</script>", i)
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
out = page[:i] + blob + page[j:]
problems = [p for p in ["reference_facts", '"reason"', "teacher evaluation", "清洗版本"] if p in out]
problems += re.findall(r"[A-Za-z]:\\\\", out)
problems += [u for u in re.findall(r"https?://[^\s\"'<>]+", out)]
if "default-src 'none'" not in out:
    problems.append("CSP missing")
if problems:
    sys.exit(f"refused: {problems[:5]}")
(S / "eduta_test_bank_index.html").write_text(out, "utf-8")
print("ok", (S / "eduta_test_bank_index.html").stat().st_size, "bytes")
