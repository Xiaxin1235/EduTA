"""离线预计算脚本：生成全国各省份历年教育数字化大盘预计算底座。

输出文件：data/real/province_summary.json
包含 2020~2023 年共 129 个省域宏观综合指标、六大维度得分、学校数量、样本量分级。
"""

import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "real", "education_digitization.sqlite")
OUT_PATH = os.path.join(ROOT, "data", "real", "province_summary.json")

STANDARD_DIMENSIONS = [
    "数字资源",
    "教育教学",
    "数字素养",
    "基础设施",
    "教育治理",
    "保障机制",
]


def precompute():
    t0 = time.time()
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"未找到数据库文件: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print(f"正在读取数据库: {DB_PATH}", flush=True)

    # 1. 计算每校综合得分及省域聚合
    print("1/3 正在计算各省综合均分与学校样本量...", flush=True)
    q_overall = """
    WITH school_means AS (
        SELECT s.province, s.year, s.id,
               AVG(a.normalized_value)*100 as school_score
        FROM schools s
        JOIN answers a ON s.id = a.school_id
        GROUP BY s.id
    )
    SELECT province, year,
           COUNT(*) as school_cnt,
           ROUND(AVG(school_score), 2) as avg_score,
           ROUND(MIN(school_score), 2) as min_score,
           ROUND(MAX(school_score), 2) as max_score
    FROM school_means
    GROUP BY province, year
    ORDER BY year, avg_score DESC
    """
    cursor.execute(q_overall)
    rows_overall = cursor.fetchall()
    print(f"综合均分计算完成，涉及 {len(rows_overall)} 个省域-年度组合。", flush=True)

    # 2. 计算各省六大维度均分
    print("2/3 正在计算各省六大维度细分指标...", flush=True)
    q_dims = """
    WITH school_dim_means AS (
        SELECT s.province, s.year, q.level1_name as dim, s.id,
               AVG(a.normalized_value)*100 as dim_score
        FROM schools s
        JOIN answers a ON s.id = a.school_id
        JOIN questions q ON a.question_id = q.id
        GROUP BY s.id, q.level1_name
    )
    SELECT province, year, dim,
           ROUND(AVG(dim_score), 2) as avg_dim_score
    FROM school_dim_means
    GROUP BY province, year, dim
    """
    cursor.execute(q_dims)
    rows_dims = cursor.fetchall()
    print(f"维度细分指标计算完成，共 {len(rows_dims)} 条维度记录。", flush=True)

    # 3. 组织结构化数据
    data_by_year = {}
    for p, y, cnt, avg_s, min_s, max_s in rows_overall:
        y_str = str(y).strip()
        if y_str not in data_by_year:
            data_by_year[y_str] = {}
        
        # 样本量分级
        if cnt >= 1000:
            tier = "规模化样本"
            tier_desc = "在册填报学校数量 ≥ 1000 所，具备充分的统计学代表性与宏观信度"
        elif cnt >= 100:
            tier = "常态化样本"
            tier_desc = "在册填报学校数量在 100~999 所之间，数据基本稳定"
        else:
            tier = "小微样本"
            tier_desc = "在册填报学校数量 < 100 所，易受个别单校作答偶然性影响，建议结合全域大盘审慎参考"

        data_by_year[y_str][p] = {
            "province": p,
            "year": y_str,
            "school_count": cnt,
            "avg_score": float(avg_s),
            "min_score": float(min_s),
            "max_score": float(max_s),
            "sample_tier": tier,
            "tier_desc": tier_desc,
            "dimensions": {},
        }

    for p, y, dim, dim_s in rows_dims:
        y_str = str(y).strip()
        if y_str in data_by_year and p in data_by_year[y_str]:
            data_by_year[y_str][p]["dimensions"][dim] = float(dim_s)

    # 为每个年份计算全国排序
    final_output = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "standard_dimensions": STANDARD_DIMENSIONS,
            "available_years": sorted(list(data_by_year.keys())),
            "total_records": len(rows_overall),
        },
        "by_year": {},
    }

    for y_str, prov_dict in data_by_year.items():
        # 全量排序
        all_sorted = sorted(prov_dict.values(), key=lambda x: x["avg_score"], reverse=True)
        for rank, item in enumerate(all_sorted, 1):
            item["rank_all"] = rank

        # 规模样本排序 (school_count >= 1000)
        large_sorted = [it for it in all_sorted if it["school_count"] >= 1000]
        for rank, item in enumerate(large_sorted, 1):
            item["rank_large"] = rank

        # 常态样本排序 (100 <= school_count < 1000)
        normal_sorted = [it for it in all_sorted if 100 <= it["school_count"] < 1000]
        for rank, item in enumerate(normal_sorted, 1):
            item["rank_normal"] = rank

        final_output["by_year"][y_str] = {
            "provinces": prov_dict,
            "ranking_all": [it["province"] for it in all_sorted],
            "large_champion": large_sorted[0]["province"] if large_sorted else None,
            "normal_champion": normal_sorted[0]["province"] if normal_sorted else None,
            "overall_top": all_sorted[0]["province"] if all_sorted else None,
        }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)

    elapsed = round(time.time() - t0, 2)
    print(f"3/3 全国省域大盘预计算完成并保存至: {OUT_PATH} (耗时 {elapsed}s)", flush=True)


if __name__ == "__main__":
    precompute()
