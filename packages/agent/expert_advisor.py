from typing import Any, Dict, List, Optional

def generate_expert_advice(data: Dict[str, Any], question: str = "") -> str:
    pass
    obj_type = data.get("object_type") or "school"

    if obj_type == "school_compare":
        return _school_compare_advice(data)
    elif obj_type == "school_ranking":
        return _school_ranking_advice(data)
    elif obj_type == "region_compare":
        return _region_compare_advice(data)
    elif obj_type == "national_ranking":
        return _national_ranking_advice(data)
    elif obj_type == "school_fact":
        return _school_fact_advice(data)
    elif obj_type == "region":
        return _region_advice(data)
    else:
        return _school_eval_advice(data)

def _national_ranking_advice(d: Dict[str, Any]) -> str:
    year = d.get("year", "2023")
    dim = d.get("target_dimension")
    dim_label = f"「{dim}」细分维度" if dim else "综合数字化发展水平"
    large_champ = d.get("large_champion") or {}
    normal_champ = d.get("normal_champion") or {}
    large_rankings = d.get("large_scale_rankings") or []

    large_p1 = large_champ.get("province", "领跑省份")
    large_s1 = large_champ.get("score", 0)
    large_c1 = large_champ.get("school_count", 0)

    large_p2 = large_rankings[1].get("province", "跟跑省份") if len(large_rankings) > 1 else ""
    large_s2 = large_rankings[1].get("score", 0) if len(large_rankings) > 1 else 0
    large_c2 = large_rankings[1].get("school_count", 0) if len(large_rankings) > 1 else 0

    norm_p = normal_champ.get("province", "")
    norm_s = normal_champ.get("score", 0)

    adv = [
        f"【全国省域教育数字化格局宏观研判】",
        f"1. 规模化样本标杆格局：在 {year} 年度全国教育数字化监测大盘中（在册学校 ≥ 1000 所的规模样本省份），{large_p1} 以 {large_s1} 分荣登全国榜首，在册监测学校覆盖 {large_c1} 所，呈现全域高水准均衡普及的集约化优势；"
        + (f"{large_p2}（{large_s2} 分，监测 {large_c2} 所学校）紧随其后，形成东部沿海省域数字化协同引领的第一梯队。" if large_p2 else ""),
        f"2. 中等规模先行示范区：常态化样本省份（100~999 所）中，{norm_p}（{norm_s} 分）以高密度现代数字校园与创新教研模式打造了超大城市/重点省域样板。" if norm_p else "",
        "",
        f"【省域教育数字化协同与视导建议】",
        f"1. 机制借鉴与跨省结对：建议中西部和后发省份深入对标 {large_p1} 等领跑省份在数字教育资源普惠共享、数字校园集约化运维与教师数字化素养轮训机制等方面的成熟经验，加大薄弱区域专项扶持力度。",
        f"2. 破解区域发展数字鸿沟：依托国家智慧教育公共服务平台，加快推动跨省域优质数字名师课例、虚拟仿真实验教学空间资源互通互认，以教育数字化赋能教育优质均衡发展。",
        "",
        f"【统计监测规范与宏观视导提示】",
        f"各省份填报在册学校覆盖度存在客观阶段性差异。对在册样本极少的小微样本省份，其均分受单校偶发值影响波动较大，在制定全省教育现代化推进规划时，应立足全域普查常态化数据进行增值性复盘。"
    ]
    return "\n".join([line for line in adv if line])

def _school_compare_advice(d: Dict[str, Any]) -> str:
    name_a = d.get("school_name_a") or "标的学校A"
    name_b = d.get("school_name_b") or "标的学校B"
    sc_a = float(d.get("total_score_a") or 0)
    sc_b = float(d.get("total_score_b") or 0)
    raw_diff = d.get("total_diff") if d.get("total_diff") is not None else (d.get("score_diff") if d.get("score_diff") is not None else (sc_a - sc_b))
    diff = float(raw_diff or 0)
    winner = d.get("winner", "A")

    dims = d.get("dimensions_comparison") or []
    a_leads = [x["dimension"] for x in dims if x.get("lead_tag") == "A"]
    b_leads = [x["dimension"] for x in dims if x.get("lead_tag") == "B"]

    abs_diff = abs(round(diff, 2))

    if winner in ("TIE", "tie") or abs_diff == 0:
        lead_school = name_a
        lead_score = sc_a
        lag_school = name_b
        lag_score = sc_b
        status_text = f"1. 整体发展态势：{name_a} 与 {name_b} 综合数字化得分持平（均为 {sc_a} 分），整体数字化办学水平不分伯仲。\n"
    else:
        lead_school = name_a if winner == "A" else name_b
        lead_score = sc_a if winner == "A" else sc_b
        lag_school = name_b if winner == "A" else name_a
        lag_score = sc_b if winner == "A" else sc_a
        status_text = f"1. 整体发展态势：{lead_school} 综合数字化得分 {lead_score} 分，相对 {lag_school}（{lag_score} 分）领先 {abs_diff} 分，整体数字化建设具备显著标杆引领优势。\n"

    lead_dims_text = "、".join(a_leads if winner == "A" else b_leads) or "综合各维度"
    lag_dims_text = "、".join(b_leads if winner == "A" else a_leads) or "部分关键指标"

    return (
        f"【综合水平与优势维度对比】\n"
        f"{status_text}"
        f"2. 领先领域剖析：{lead_school} 在「{lead_dims_text}」等维度占有明显相对优势，反映出该校在信息化顶层设计、常态化数字化教学应用以及师生数字素养培养上具备更为健全的机制支撑。\n\n"
        f"【短板攻坚与对标互鉴建议】\n"
        f"1. 定向补齐短板：建议 {lag_school} 重点对标先进经验，优先攻坚「{lag_dims_text}」短板，优化多媒体终端及网络环境配置标准，加快推动常态化录播与互动教学应用。\n"
        f"2. 提质深度融合：建议两校依托国家智慧教育平台，推动优质数字资源全覆盖共享，探索基于过程性数据的精准教学与个性化辅导模式。\n\n"
        f"【区域协同与视导工作建议】\n"
        f"建议所属教育行政主管部门建立两校数字化结对帮扶共同体，以标杆校优秀数字教研成果为抓手，开展跨校虚拟教研与联合集体备课，有效缩小校际数字化发展落差，赋能区域教育优质均衡发展。"
    )

def _school_ranking_advice(d: Dict[str, Any]) -> str:
    name = d.get("school_name") or "本校"
    rank = d.get("rank", 1)
    total = d.get("total_schools", 1)
    sc = d.get("total_score", 0)
    avg = d.get("region_avg", 0)
    diff = d.get("diff", 0)
    top_pct = d.get("top_pct", 1.0)
    beat_pct = d.get("beat_pct", 99.0)

    dims = d.get("dimensions_comparison") or []
    lead_dims = [x["dimension"] for x in dims if x.get("is_lead")]
    lag_dims = [x["dimension"] for x in dims if not x.get("is_lead")]

    lead_text = "、".join(lead_dims) or "综合基础"
    lag_text = "、".join(lag_dims) or "个别细节指标"

    diff_desc = f"高于全区均分 +{diff:.2f} 分" if diff >= 0 else f"低于全区均分 {abs(diff):.2f} 分"
    tier_desc = "第一梯队（标杆示范行列）" if rank <= max(3, total * 0.1) else "中坚骨干梯队"

    return (
        f"【全区排位与发展位次研判】\n"
        f"1. 核心位次核验：{name} 在全区 {total} 所监测学校中综合排位第 {rank} 名，综合得分 {sc} 分（{diff_desc}），位列全区前 {top_pct}%，超越全区 {beat_pct}% 的学校，稳居全区数字化{tier_desc}。\n"
        f"2. 维度优势分析：该校在「{lead_text}」维度全面高于区域基准线，具备成熟的数字化办学示范效应。\n\n"
        f"【精准攻坚与提升策略】\n"
        f"1. 靶向消减短板：针对「{lag_text}」等相对薄弱维度，建议对照教育部标准深化常态化数据采集，加强数字教学资源库的建设与校本化重组。\n"
        f"2. 教师素养赋能：完善教师数字化教学创新激励机制，开展人机协同精准教学技能专题轮训，促进信息技术与学科教学的深层次融合。\n\n"
        f"【学校治理与常态视导指引】\n"
        f"建议学校管理团队建立常态化数字化发展监测档案，每学期对照区域指标大盘进行增值性复盘，持续巩固示范引领地位。"
    )

def _region_compare_advice(d: Dict[str, Any]) -> str:
    name_a = d.get("region_name_a") or "区域A"
    name_b = d.get("region_name_b") or "区域B"
    sc_a = float(d.get("total_score_a") or 0)
    sc_b = float(d.get("total_score_b") or 0)
    raw_diff = d.get("total_diff") if d.get("total_diff") is not None else (d.get("score_diff") if d.get("score_diff") is not None else (sc_a - sc_b))
    diff = abs(round(float(raw_diff or 0), 2))
    winner = d.get("winner", "A")

    if winner in ("tie", "TIE") or diff == 0:
        lead_reg = name_a
        lag_reg = name_b
        comp_desc = f"{name_a} 与 {name_b} 监测大盘均分完全持平（均为 {sc_a} 分），两地在宏观整体数字化水平上处于同一发展梯队。"
    else:
        lead_reg = name_a if winner == "A" else name_b
        lag_reg = name_b if winner == "A" else name_a
        comp_desc = f"{name_a} 监测大盘均分为 {sc_a} 分，{name_b} 为 {sc_b} 分，两地综合差距为 {diff} 分，{lead_reg} 在宏观整体数字化水平上更具规模化先发优势。"

    return (
        f"【区域宏观大盘对比研判】\n"
        f"1. 大盘均分测度：{comp_desc}\n"
        f"2. 结构分布特征：两区域在基础设施普及、数字素养培育及机制保障等关键维度呈现不同程度的发展特色。\n\n"
        f"【区域互鉴与统筹推进建议】\n"
        f"1. 机制与保障互鉴：建议 {lag_reg} 汲取 {lead_reg} 在数字校园集约化运维与政策倾斜机制方面的成功经验，加大薄弱学段数字化专项扶持。\n"
        f"2. 资源互通互认：推动两地优质数字化名师课例、数字孪生教学空间等资源共建互通，全面提升区域整体教育现代化水平。"
    )

def _school_fact_advice(d: Dict[str, Any]) -> str:
    name = d.get("school_name") or "该校"
    metric = d.get("metric_name") or "核查指标"
    val = d.get("target_value", "-")
    unit = d.get("unit") or "台"

    if d.get("fact_data_status") == "NO_DATA" or val is None:
        return (
            f"【客观指标事实核验】\n"
            f"{name} 当前关于「{metric}」的必要数据不完整，无法形成可核验数值；"
            "页面中的缺口说明应作为本次结论，不应将缺失值解释为0。\n\n"
            "【后续核查建议】\n请补齐该指标所需的原始题项或学校规模字段后再计算，当前不作达标判断。"
        )

    fact_line = d.get("direct_answer") or f"经底层数据库比对，{name} 当前「{metric}」的可核验值为 {val} {unit}。"
    return (
        f"【客观指标事实核验】\n"
        f"{fact_line}\n\n"
        f"【证据边界】\n"
        f"该数值仅反映当前数据版本中的登记事实；未提供适用阈值或规范条款，因此不据此判断是否达标。"
    )

def _region_advice(d: Dict[str, Any]) -> str:
    name = d.get("region_name") or "本区域"
    sc = d.get("total_score", 0)
    count = d.get("school_count", 0)
    strong = d.get("strongest_dim") or "基础环境"
    weak = d.get("weakest_dim") or "机制保障"

    return (
        f"【区域宏观发展总览】\n"
        f"{name} 辖区内共纳入监测学校 {count} 所，区域数字化大盘综合均分为 {sc} 分。整体呈现以标杆校为牵引、多层次骨干学校梯队发展的良性格局。\n\n"
        f"【长短板剖析与视导建议】\n"
        f"1. 相对优势：该区在「{strong}」维度成效显著，校园基础联通与智能终端配置具备较高水准。\n"
        f"2. 主要攻坚点：相对薄弱项集中在「{weak}」，建议区教育局重点健全常态化经费保障制度与校级数据安全治理规程。"
    )

def _school_eval_advice(d: Dict[str, Any]) -> str:
    name = d.get("school_name") or "学校"
    sc = d.get("total_score", 0)
    cov = d.get("data_coverage", 1.0)
    return (
        f"【整校数字化全景诊断】\n"
        f"{name} 本周期数字化综合评分为 {sc} 分（指标数据覆盖度 {round(cov*100, 1)}%）。\n\n"
        f"【视导发展规划建议】\n"
        f"建议学校以数字化赋能新课程新教材实施为主线，强化常态化数字资源应用与课堂教学深度重构，促进全校师生数字素养的阶梯式跃升。"
    )
