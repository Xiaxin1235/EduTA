"""Shared paths and helpers, copied from the held-out v3 reference package for the sub-dimension dev20 reference and reused for the v4 reference (2026-10-28); evidence/topic_scores_r11.sqlite is a hard link to
the dev20 package's file (same frozen inputs, same definition).
Independent of the product code (no imports from it). Per-school overall/dimension scores are read (read-only) from the v3 package's
evidence; per-school sub-dimension scores are computed here by 01_topic_scores.py."""
from pathlib import Path
import json, hashlib, sqlite3, datetime, sys
sys.stdout.reconfigure(encoding="utf-8")
PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]  # local root of the confidential data (not distributed)
V5TAG = "v5_research_acceptance_20261010_100402"
SNAP = ROOT / "source_snapshot/education_digitization_20261009.sqlite"
BASE = ROOT / "cleaned_data/school_metadata_format_v1.sqlite"
MAP = ROOT / "cleaned_data" / V5TAG / "school_metadata_research_v5.sqlite"
# v3 (like v2.1) uses definition R11: per-school scores recomputed without the year-specific uncollected items
SCORES = ROOT / "reference_packages/heldout100_v3_reference_20261020/evidence/school_scores_r11.sqlite"
TOPIC_SCORES = PACKAGE / "evidence/topic_scores_r11.sqlite"
YEARS = ["2020", "2021", "2022", "2023"]
LATEST = "2023"


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda: f.read(8388608), b""):
            h.update(b)
    return h.hexdigest()


def load(p):
    return json.loads(Path(p).read_text("utf-8-sig"))


def dump(p, x):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def now():
    return datetime.datetime.now().astimezone().isoformat()


def research():
    """Independently authored research view (copied from the reference-100 package) over the frozen inputs, read-only."""
    c = sqlite3.connect(":memory:", uri=True); c.row_factory = sqlite3.Row
    for name, p in [("base", BASE), ("mapping", MAP), ("facts", SNAP)]:
        c.execute("attach database ? as " + name, (p.as_uri() + "?mode=ro",))
    c.executescript((PACKAGE / "sql/full_research_view.sql").read_text("utf-8"))
    c.execute("pragma query_only=on")
    return c


CAT = {r["id"]: r for r in load(PACKAGE / "inputs/indicator_catalogue.json")}
POLICY = load(PACKAGE / "inputs/SCORING_POLICY_FROM_REFERENCE100.json")
DIMS = POLICY["school_template"]["dimensions"]
WEIGHTS = dict(zip(DIMS, POLICY["school_template"]["weights"]))
DIMIDS = {d: sorted(i for i, r in CAT.items() if r["level1_name"] == d) for d in DIMS}

UNCOLLECTED = {y: set(v) for y, v in load(PACKAGE / "inputs/UNCOLLECTED_ITEMS_v1.json")["items_by_year"].items()}
