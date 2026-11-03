"""Generate fictional records from indicator definitions, without reading private data."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
YEARS = ("2020", "2021", "2022", "2023")

SCHEMA = """
CREATE TABLE questions (
 id INTEGER PRIMARY KEY, content TEXT, level3_weight REAL,
 level1_name TEXT, level1_description TEXT, level1_weight REAL,
 level2_name TEXT, level2_description TEXT, level2_weight REAL
);
CREATE TABLE schools (
 id INTEGER PRIMARY KEY, province TEXT, city TEXT, district TEXT,
 school_name TEXT, year TEXT, school_type TEXT, school_area_type TEXT,
 class_num TEXT, teacher_num TEXT, student_num TEXT, data_mark INTEGER, region TEXT
);
CREATE TABLE answers (school_id INTEGER, question_id INTEGER, value REAL, normalized_value REAL);
CREATE INDEX answers_school_id ON answers(school_id);
CREATE INDEX answers_question_id ON answers(question_id);
"""


def generate(output: Path, with_overlay: bool = False) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("Output directory is not empty; choose a new --output directory")
    output.mkdir(parents=True, exist_ok=True)
    template = yaml.safe_load((ROOT / "evaluation_templates/school_digitization_v1.yaml").read_text("utf-8"))
    metrics = {}
    for dimension in template["dimensions"]:
        for metric in dimension["metrics"]:
            metrics[int(metric["id"])] = (
                int(metric["id"]), metric["content"], float(metric.get("weight", 1)),
                dimension["name"], dimension.get("description", ""), float(dimension["weight"]),
                metric.get("level2", "示例分类"), "", 1.0,
            )
    source = output / "education_digitization.sqlite"
    with sqlite3.connect(source) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)", metrics.values())
        for year_index, year in enumerate(YEARS):
            for school_index in range(6):
                school_id = year_index * 100 + school_index + 1
                city = "示例市" if school_index < 3 else "演示市"
                number = "一二三四五六"[school_index]
                conn.execute("INSERT INTO schools VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    school_id, "示例省", city, "示例区", f"{city}示例区示例{number}小学", year,
                    "小学", "城区" if school_index % 2 == 0 else "乡村", "12", "36", "480", 1, "示例大区",
                ))
                for question_id in metrics:
                    raw = float(10 + question_id * 3 + school_index * 7 + year_index * 2)
                    normalized = ((question_id * 11 + school_index * 7 + year_index * 3) % 75 + 20) / 100
                    conn.execute("INSERT INTO answers VALUES (?,?,?,?)", (school_id, question_id, raw, normalized))
    result = {"kind": "SYNTHETIC", "schools": 6 * len(YEARS), "questions": len(metrics), "answers": 6 * len(YEARS) * len(metrics)}
    if with_overlay:
        base = output / "metadata.sqlite"
        with sqlite3.connect(base.as_uri() + "?mode=rwc", uri=True) as conn:
            conn.execute("ATTACH DATABASE ? AS source", (source.as_uri() + "?mode=ro",))
            conn.execute("CREATE TABLE school_records AS SELECT *, province AS normalized_province, city AS normalized_city, district AS normalized_district FROM source.schools")
            conn.execute("CREATE UNIQUE INDEX school_records_id_year ON school_records(id,year)")
        mapping = output / "mapping.sqlite"
        with sqlite3.connect(mapping) as conn:
            conn.executescript("""
            CREATE TABLE record_metadata (source_school_year_id INTEGER, year TEXT, normalized_province TEXT, normalized_city TEXT, normalized_district TEXT, PRIMARY KEY(source_school_year_id,year));
            CREATE TABLE format_extension_v5 (source_school_year_id INTEGER, year TEXT, new_value TEXT, rule_id TEXT, PRIMARY KEY(source_school_year_id,year));
            CREATE TABLE research_review_v5 (source_school_year_id INTEGER, year TEXT, status TEXT, PRIMARY KEY(source_school_year_id,year));
            """)
        names={'示例省':[dict(level='province',province='示例省',city=None,district=None)]}
        for city in ('示例市','演示市'):
            names[city]=[dict(level='city',province='示例省',city=city,district=None)]
            names[city[:-1]]=names[city]
        names['示例区']=[dict(level='district',province='示例省',city=c,district='示例区') for c in ('示例市','演示市')]
        registry = {"version": "geo-select-v5-full-synthetic", "application_years": list(YEARS),
            "parent_by_city": {"示例市": "示例省", "演示市": "示例省"}, "city_aliases": {'示例':'示例市','演示':'演示市'},
            "province_aliases": {'示例省':'示例省'}, "districts": {}, "city_parent_ties":{},"name_candidates":names,
            "district_parents":{'示例区':[dict(province='示例省',city=c,years=list(YEARS)) for c in ('示例市','演示市')]}}
        (output / "geography_registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg = {"version": "synthetic-overlay", "estimand": "synthetic-school-year", "data": {}, "registry": str(output/'geography_registry.json'), "application_years": list(YEARS)}
        for label, path in (("source", source), ("base_v1", base), ("mapping_v5", mapping)):
            cfg["data"][label] = {"path": str(path), "SHA256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        (output / "research_dataset.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        result["overlay_config"] = str(output / "research_dataset.json")
    (output / "SYNTHETIC_DATA.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/synthetic")
    parser.add_argument("--with-overlay", action="store_true")
    args = parser.parse_args()
    result = generate(args.output, args.with_overlay)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("All records are fictional. No private database was read.")
    if args.with_overlay:
        print("Set TEACHER_EVAL_RESEARCH_CONFIG to " + result["overlay_config"])


if __name__ == "__main__":
    main()
