# EduTA 论文评测材料 / Evaluation materials

本目录收录论文 *EduTA: Controlled LLM question answering over hierarchical school assessment data with typed tasks and registered tools* 的公开实验材料。学校评估原始数据库、由其计算的参考值、系统回答原文和运行日志属于保密数据，不在此发布：可以逐题核对各系统的判定，但不能在原数据上重跑。

This directory holds the public materials of the paper. The school assessment records, the reference values computed from them, the answer texts and the run logs are confidential and not included.

## 目录

| 路径 | 内容 |
|---|---|
| `test_bank_and_results/site_data_v3_public.json` | 100 道留出测试题（中文原题、应答/拒答/澄清、目标层级、口语指标映射、画图要求、规则提示）；7 个系统第 1 轮的签认状态，失败题的 M1–M4 分层、首次出错阶段、核心正确性、层级关系；EduTA、No-correction、Tool-calling、Tool-calling+chart 的三轮状态；汇总表 |
| `review_rules/` | 遮盖评审规则原文：各系统的端到端评审、分层归因（layers）、核心正确性（core）、层级混淆（level confusion）。规则中的本地目录已替换为 `<review_dir>` |
| `scoring/SCORING_DECISIONS.md` | 冻结记录、作者对边界题的决定及其对结果的影响（用评审原判时的成绩与检验）、R11 逐年未采集题项 |
| `scoring/SUPPLEMENTARY_METHODS.md` | 补充方法：数据与 R11 敏感性、基线配置、评审流程、外部专家抽查、数字可追溯性、token 明细、阶段归因、纠错消融边界、配对效果量、MAC-SQL 配置、R11 合理性检验（与论文正文同一版本）|
| `scoring/paired_effects.py` | 第 1 轮 EduTA 对各系统的配对效果量：四格计数、Newcombe 配对 95% 区间（未校正）、精确符号检验与 Holm 校正；只读公开数据，直接可运行 |
| `reference_computation/r11_plausibility.py`、`r11_ambiguous.py`、`r11_common_items.py` | R11 合理性检验：排除项的题号分布、零假设下“当年全部学校为 0”的概率，以及把 7 个无法判定的题×年份按真 0 计入时的全国均值与省份方向；共同题项（四年都采集的 23 题）口径下的跨年方向；需要保密数据，不能在公开环境运行 |
| `reference_computation/` | 独立于系统的参考值计算脚本：`01_school_scores.py`（学校分，R11）、`01b_topic_scores.py`（二级维度分）、`02_compute.py` + `specs_v6.py`（逐题参考事实）、`full_research_view.sql`。需要保密数据库才能运行 |
| `baselines/tool_calling/` | Tool-calling 基线（无图表函数）的系统提示与函数层 |
| `baselines/tool_calling_chart/` | Tool-calling+chart 基线（第十个函数为 EduTA 图表组件；地区函数也接受“全国”，与 EduTA 用同一全国口径）的系统提示、函数层、运行脚本、报告规则 |
| `test_bank_index/eduta_test_bank_index.html` | 单文件离线题库索引：主结果表、三轮结果表、按题型/系统/三轮表现筛选、逐题卡片、状态矩阵、两系统对比。直接用浏览器打开；不发起任何网络请求。`build_public_site.py` 把 `site_data_v3_public.json` 嵌入页面模板 `page_template.html` 重新生成，并检查输出中没有参考值、评审理由和本地路径 |
| `baselines/text_to_sql/` | Direct-SQL 与 MAC-SQL：运行脚本、MAC-SQL 适配器与相对上游的改动补丁、上游版本、两者共用的数据说明文档 |

## 系统名与代码配置

EduTA = G1（程序校正开启）；No-correction = G1（程序校正关闭）；Rule-based = G0；Tool-calling / Tool-calling+chart / Direct-SQL / MAC-SQL 为对照基线。Tool-calling+chart 的三轮是独立运行，不是 Tool-calling 的重复。所有模型调用为 DeepSeek-V4.1-Flash（官方 API，别名 `deepseek-flash`）。

## 判定口径

- 正确 = `PASS` 或 `CLARIFY_OK`（对应澄清题的合理澄清）；`NE`（基础设施故障）计为错误。
- 端到端状态是主结果；分层、首次出错阶段、核心正确性、层级关系是对第 1 轮失败答案的遮盖再分析，不改变主结果。
- 首次出错阶段：M1 错为“理解”，否则 M2 错为“计算”，否则为“输出”。
- 评审由 AI 在遮盖系统身份、可见参考值的条件下完成，边界题由作者按论文附录 B 的原则裁决；1 位外部专家抽查 112 份答案，与报告判定一致 92.8%（κ = 0.84）。

## 发布时的改动

仅替换本地路径：评审规则中的目录改为 `<review_dir>`；`baselines/text_to_sql/paths.py` 的未采集题项路径改为环境变量 `TE_UNCOLLECTED_ITEMS`（默认 `research_rules/UNCOLLECTED_ITEMS_v1.json`）；`baselines/tool_calling_chart/paths.py` 的项目根默认值改为 `.`；`reference_computation/common.py` 删去一条含本地路径的注释。代码逻辑、提示词和规则文字未改。题库索引页面由 OpenAI 编码助手按作者给定的规格编写，数据与 `site_data_v3_public.json` 逐字节一致。X06 的 EduTA 与 No-correction 答案在归因后经作者签认为正确，公开数据中不再附带其失败字段。
