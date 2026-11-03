"""Inspect the school digitization evaluation template: dimension weights vs DB weights."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

P = Path(r"D:\teacher evaluation\evaluation_templates\school_digitization_v1.yaml")
d = yaml.safe_load(P.read_text(encoding="utf-8"))

print("top-level keys:", list(d.keys()))
t = d.get("template") or {}
print("template meta:", t)
dims = d.get("dimensions") or []
print("dimensions:", len(dims))
tot = 0
for dim in dims:
    ms = dim.get("metrics") or []
    tot += len(ms)
    print(f"  {str(dim.get('name')):<24} weight={dim.get('weight')} "
          f"metrics={len(ms)} ids={[m.get('id') for m in ms]}")
print("total metrics in template:", tot)
