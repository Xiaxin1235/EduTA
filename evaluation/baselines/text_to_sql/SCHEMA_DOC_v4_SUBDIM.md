# Text-to-SQL 共用公共数据说明

B1 原文读取本文件；B2 将完全相同的 UTF-8 文本作为 BIRD evidence。只含公共目录和通用计算规则，不含题库、参考答案、逐题提示或预聚合结果。

## 两张非聚合 TEMP VIEW

schools（学校年度记录）与 answers（作答）。视图来自冻结研究口径；下面所列是全部可查询字段。

### schools

|字段|类型|含义|
|---|---|---|
|id|INTEGER|学校年度记录主键，不是跨年学校身份|
|year|TEXT|记录年度|
|province|TEXT|规范省级字段|
|city|TEXT|规范城市全称|
|district|TEXT|规范区县|
|school_name|TEXT|源记录学校名|
|school_type|TEXT|学段原字段|
|school_area_type|TEXT|城乡原字段|
|class_num|TEXT|班级数原字段|
|teacher_num|TEXT|教师数原字段|
|student_num|TEXT|学生数原字段|
|data_mark|INTEGER|源记录标记|
|region|TEXT|源记录区域|
|raw_province|TEXT|原始省份|
|raw_city|TEXT|原始城市|
|raw_district|TEXT|原始区县|
|query_province|TEXT|等于 province|
|query_city|TEXT|等于 city|
|query_district|TEXT|等于 district|
|research_status|TEXT|v5 研究状态，非排除条件|
|metadata_source|TEXT|元数据来源|
|additional_format_rule|TEXT|v5 城市格式规则|

### answers

|字段|类型|含义|
|---|---|---|
|school_id|INTEGER|关联 schools.id|
|question_id|INTEGER|83 题的题号|
|value|REAL|原始数值，0 有效，NULL 缺失|
|normalized_value|REAL|已有归一化数值；分数乘 100|
|dimension|TEXT|题目所属一级维度|
|question_content|TEXT|原题目文本|

## 计算与选择规则
- 记录身份：schools.id 是学校年度登记 ID；连接 `answers.school_id=schools.id`，年度用 schools.year 字符串精确匹配。不同年度分别取样，不假定同名学校跨年身份相同。校名中出现地名不构成地区限定。
- 城市用下方字典的规范 city 全称与对应 province 同时精确匹配。显式省、市、区县、年度、学段、城乡限制取 AND 交集；排除条件也必须保留，不能用 province 样本替代 city。不要额外按 research_status 或 data_mark 排除记录。
- 有效性按所问值视图判断：原值用 `value IS NOT NULL`，标准分用 `normalized_value IS NOT NULL`。除下述 R11 当年未采集题项外，0 有效，不能用 >0 筛选；无值不是 0。视图本身不删除任一原值或标准分缺失行。
- 原始数量、原始比例、编码值与标准分分开。学校事实默认原值；只有题干要求得分或百分制归一值时才使用 normalized_value×100。原比例保持登记数值，不统一再乘/除 100；类别编码的含义以目录 coding_status/notes 为准，不能解释成真实类别含义或人数。
- 区域学校等权均值：先在每所学校内部对所问题项的有效值求均值，再对这些学校均值求均值。整体区域均分使用全部 83 题中当年已采集题项；单维仅用该维当年已采集题号；单题只用该题。区域总体均分不是学校模板综合分。
- 作答合并均值：把该范围内所问题项的有效作答放在一起 AVG，不先做学校等权。区域分数用 normalized_value×100，原值统计用 value。有效学校数是目标视图/题项范围内有有效值的 DISTINCT school_id 数；有效作答数是有效作答行数。
- 区域中位数、最高、最低基于上述每校有效值均值序列。偶数样本中位数是中间两值的均值；原始未舍入值用于统计、比较和排序，最终分值保留两位。
- 学校模板分：排除当年未采集题项后，每个已作答题 `max(0,min(100,normalized_value×100))`；每维对本维有效题等权平均，先四舍五入至两位，再按下表的模板维度权重加权，只按已覆盖维度的权重和重新归一化，最终两位。某维度当年没有任何已采集题项就不计入，其余维度的权重重新归一；已采集但本校无有效作答的维度也不计入。题权当前都是 1。维度无有效题时为 NULL，不补 0。覆盖题数/83 是覆盖率。单维学校得分就是该维得分。
- 学校事实的单题标准分（fact_engine）展示为 `round(normalized_value×100,1)`；学校模板中的单题分受上述 0–100 截断。两题原值加、减、除按题干顺序执行；百分比形式的除法乘 100。比值分母 0 不产生有效数值。
- 生机比=student_num/学生终端合计，学生终端合计用题 63、64、65、66 的原值；师均终端配置=教师终端合计/teacher_num，教师终端合计用题 67、68、69、70 原值。学校字段 class_num/teacher_num/student_num 是原字符串数值，计算时转换；复合指标必须各组成题全部有值，比值还须有正数分母；生机比的学生数也须正数。比值保留两位。
- 学校区域排名按所问学校均值降序、学校年度 ID 升序；倒数或升序问题按所问方向排序，仍用 ID 升序打破同分。第 N 名指排序后位置，Top N 返回 N 行。学校内六维名次用 `1+COUNT(其他维分数>目标分)`，相同得分并列。学校内某维题项极值基于未舍入的模板题项分，全部并列极值保留并按题号升序。
- 跨校/跨年/跨维分差遵守题干 A−B 方向；公共组合任务在先求各节点的两位值后减，并保留两位；区域纵向工具也保存未舍入均值差。先识别任务使用哪种计算定义，不将差值绝对值替代有方向差值。全国总体榜首先算学校均分再按省级地区取学校等权均值；题干只说省份/哪个省时限定行政省，说省级地区或含直辖市时才包含直辖市。

## 学段和城乡筛选（复现注册工具谓词）
|筛选|SQL 条件（列属于 schools）|
|---|---|
|小学|school_type LIKE '%小学%' OR school_type LIKE '1-6%' OR school_type LIKE '1-5%' OR school_type LIKE 'A.%'|
|初中|school_type LIKE '%初中%' OR school_type LIKE '%初级中学%' OR school_type LIKE '7-9%' OR school_type='1-9' OR school_type LIKE 'B.%'|
|高中|school_type LIKE '%高中%' OR school_type LIKE '%高级中学%' OR school_type LIKE '10-12%' OR school_type LIKE 'C.%'|
|九年一贯制|school_type LIKE '%九年一贯%' OR school_type='1-9' OR school_type LIKE 'D.%'|
|十二年一贯制|school_type LIKE '%十二年一贯%' OR school_type='1-12'|
|中职|school_type LIKE '%中职%' OR school_type LIKE '%中等职业%' OR school_type LIKE '%职业学校%'|
|幼儿园|school_type LIKE '%幼儿园%'|
|乡村、农村|school_area_type LIKE '%乡村%' OR school_area_type LIKE '%农村%'|
|镇区、城镇、城区、城市|school_area_type LIKE '%镇区%' OR school_area_type LIKE '%城镇%' OR school_area_type LIKE '%城区%' OR school_area_type LIKE '%城市%'|

每个 OR 组需要括号，不同属性 AND 相连。公办/民办没有可靠结构化字段，不能从学校名称推断。


## R11：逐年未采集题项
某年全国所有学校都为 0 的题项视为当年未采集，按缺失处理：不计入当年的整体、维度和学校模板得分；问到这类题项当年的数值时，应说明当年未采集，不能回答 0。
下列清单逐项取自 UNCOLLECTED_ITEMS_v1.json 的 items_by_year（版本 uncollected-items-v1-20261019），不从题库或参考答案推断，也不手工增删。视图保留原登记行，SQL 计算需按学校年度排除清单中的题号；已采集题项的真实 0 仍有效。
|年度|未采集题号|
|---|---|
|2020|46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83|
|2021|49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83|
|2022|3,5,8,9,11,14,15,17,23,25,26,45,46,47,48,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83|
|2023|1,3,5,6,7,8,11,12,14,15,16,17,23,24,25,26,27,41,42,43,45,46,47,48,50,55,56,57|

## 模板维度与别名

|维度|模板权重|题号|别名|
|---|---|---|---|
|数字资源|0.1667|1,5,6,7,8,9,10,14,45,46,47|数字资源、数字化资源、数字教育资源、数字教学资源、资源数字化、数字内容、教学数字内容|
|教育教学|0.1667|2,3,11,12,13,49,50,51,52,59,77,78|教育教学、数字化教学、信息化教学、智慧教学、教学数字化|
|数字素养|0.1667|4,40,41,42,43,44,56,57,58|数字素养、数字能力、信息素养、数字化素养|
|基础设施|0.1667|15,16,17,18,19,20,53,60,61,62,63,64,65,66,67,68,69,70|基础设施、数字基建、信息化基础设施、数字化基础设施、信息化基础|
|教育治理|0.1667|21,22,23,24,25,26,27,48,54,55,71,72,73,74,75,76,83|教育治理、数字治理、信息化管理、数字化管理、治理数字化|
|保障机制|0.1665|28,29,30,31,32,33,34,35,36,37,38,39,79,80,81,82|保障机制、制度保障、支撑保障、数字化保障、保障体系|

## 二级维度（应用自定义粒度）

问卷有三级：6 个一级维度、13 个二级维度（下表；目录字段 topic 即二级维度）、83 个题项。评估方案只定义题项、一级维度和总体得分，二级维度得分是应用自定义的分数，口径如下：

- 学校的二级维度得分：该校在该二级维度所含题项上有效 normalized_value×100 的平均（排除 R11 当年未采集题项；0 有效，NULL 排除）。
- 地区的二级维度得分：各有效学校得分的学校等权平均；有效学校指在这些题项上至少有一个有效作答的学校，回答时给出有效学校数。
- 回答二级维度得分时须注明：这是该二级维度所含题项得分的均值，评估方案未单独定义此分数。
- 映射：只问一个二级维度 → 该二级维度；部分列出同一维度的几个二级维度 → 分别给分，不合成一个分数；全部列出 → 一级维度；具体数量（台数、带宽、人数、比例）→ 题项原值；“教师数字素养”→ 一级维度数字素养。单个泛词（如“设备”“安全”）不构成二级维度。
- 问金额（花多少钱）仍属无数据：财力保障是得分，不是金额。
- 某二级维度的题项在某年全部未采集时，应说明当年未采集，不能回答 0。全部未采集的组合：2023 年：资源应用；2020 年：教学评价；2021 年：教学评价；2023 年：网络安全。

|一级维度|二级维度|题号|别名|说明|
|---|---|---|---|---|
|数字资源|资源规模|1,9,10,14|数字资源规模、资源库规模||
|数字资源|资源应用|5,6,7,8,45,46,47|数字资源应用||
|教育教学|教学方式|2,3,11,12,13,51,52,59|教学方式应用、课堂教学方式||
|教育教学|教学评价|49,50,77,78|教学评估方式||
|数字素养|教师数字素养|4,40,41,42,43,44,56,57,58|老师们的数字素养、教师信息素养|按一级维度“数字素养”回答|
|基础设施|终端|15,16,17,63,64,65,66,67,68,69,70|终端设备、终端设备条件、终端设备配备||
|基础设施|教室|18,19,53,61,62|教室条件、教室设备条件||
|基础设施|网络|20,60|网络条件、网络设施||
|教育治理|学校治理|21,22,23,24,25,26,54,71,72,73,74,75,76,83|学校数字化治理、学校管理数字化||
|教育治理|网络安全|27,48,55|网络安全保障||
|保障机制|组织保障|28,29,30,79,82|组织机制保障、组织和人员保障、组织与人员保障||
|保障机制|财力保障|31,32,33,34,35,36,37|经费保障、资金保障、财力支持||
|保障机制|人力保障|38,39,80,81|人员保障、人员支持、组织和人员保障、组织与人员保障||

## 83 题完整语义目录

字段原样来自系统 load_semantic_catalogue()，含原题文、维度、主题、摘要、别名、单位、类型、时间窗口、量表与编码注释。

```json

{
  "version": "school-digitization-semantics-v1",
  "source_template": "school_digitization_v1.yaml",
  "item_count": 83,
  "items": [
    {
      "id": 1,
      "question": "学校拥有与教材完整配套数字教育资源的学科数量",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源规模",
      "summary": "教材配套数字教育资源覆盖的学科数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "学科",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "教材配套资源",
        "数字资源覆盖学科",
        "与教材完整配套数字教育资源",
        "教材完整配套数字教育资源"
      ],
      "confusable_with": [
        9,
        10
      ],
      "notes": ""
    },
    {
      "id": 2,
      "question": "学校利用信息技术辅助课堂教学实现常态化应用的学科数量",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "信息技术辅助课堂教学达到常态化应用的学科数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "学科",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "信息技术辅助课堂",
        "常态化应用学科",
        "辅助课堂教学实现常态化应用"
      ],
      "confusable_with": [
        4,
        59
      ],
      "notes": ""
    },
    {
      "id": 3,
      "question": "学校信息化教学系统常使用的功能有多少个",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "常用信息化教学系统功能数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "个",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        7,
        11
      ],
      "notes": ""
    },
    {
      "id": 4,
      "question": "学校能够利用信息技术开展教学的学科教师比例",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "能够利用信息技术开展教学的学科教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [
        "教师信息技术教学能力",
        "能够使用信息技术的教师",
        "会用技术上课"
      ],
      "confusable_with": [
        2,
        59
      ],
      "notes": ""
    },
    {
      "id": 5,
      "question": "学校开展的.三个课堂.应用的数量",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "已开展的三个课堂应用数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "个",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        6,
        46,
        47
      ],
      "notes": ""
    },
    {
      "id": 6,
      "question": "学校开展.三个课堂.教学的应用模式有多少个",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "三个课堂教学应用模式数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        5,
        46,
        47
      ],
      "notes": ""
    },
    {
      "id": 7,
      "question": "基于区.县级及以上教育云平台的学校网络学习空间常用功能有多少个",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "教育云平台网络学习空间常用功能数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "个",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        3,
        8
      ],
      "notes": ""
    },
    {
      "id": 8,
      "question": "学校最常使用的网络空间支持平台有多少",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "学校最常使用的网络空间支持平台数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "个",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        7
      ],
      "notes": ""
    },
    {
      "id": 9,
      "question": "学校校本数字资源库建设模式有几个",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源规模",
      "summary": "校本数字资源库建设模式数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "资源库建设模式",
        "建库模式",
        "校本数字资源库"
      ],
      "confusable_with": [
        1,
        10
      ],
      "notes": ""
    },
    {
      "id": 10,
      "question": "学校数字资源的主要来源有多少",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源规模",
      "summary": "学校数字资源主要来源数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        1,
        9,
        14
      ],
      "notes": ""
    },
    {
      "id": 11,
      "question": "学校在信息技术支持下展开的信息化教学应用种类有几种",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "信息技术支持的信息化教学应用种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        2,
        3,
        12
      ],
      "notes": ""
    },
    {
      "id": 12,
      "question": "学校采取的教学组织模式有多少",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "学校采用的教学组织模式数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        11,
        13
      ],
      "notes": ""
    },
    {
      "id": 13,
      "question": "学校人工智能课程开设的形式有多少",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "人工智能课程开设形式数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "AI课程开设形式",
        "人工智能课程怎么开"
      ],
      "confusable_with": [
        14
      ],
      "notes": ""
    },
    {
      "id": 14,
      "question": "学校开设人工智能课程的课程资源来源有多少",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源规模",
      "summary": "人工智能课程资源来源数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "AI课程资源来源",
        "人工智能课程资源从哪来"
      ],
      "confusable_with": [
        10,
        13
      ],
      "notes": ""
    },
    {
      "id": 15,
      "question": "教师在校教学用信息化终端设备的主要来源有多少种",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "教师校内教学终端设备来源种类",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        16,
        67,
        68,
        69,
        70
      ],
      "notes": ""
    },
    {
      "id": 16,
      "question": "学生在校学习用信息化终端设备的主要来源有多少",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学生校内学习终端设备来源种类",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        15,
        63,
        64,
        65,
        66
      ],
      "notes": ""
    },
    {
      "id": 17,
      "question": "多媒体教室平均使用率",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "多媒体教室平均使用率",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        62
      ],
      "notes": ""
    },
    {
      "id": 18,
      "question": "学校建有的智能学习空间共计有多少",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "教室",
      "summary": "已建智能学习空间总数",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "间",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        19,
        53
      ],
      "notes": ""
    },
    {
      "id": 19,
      "question": "学校建有的智能学习空间包括多少种",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "教室",
      "summary": "已建智能学习空间类型数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        18,
        53
      ],
      "notes": ""
    },
    {
      "id": 20,
      "question": "学校无线网络覆盖情况",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "网络",
      "summary": "无线网络覆盖类别",
      "subjects": [
        "school"
      ],
      "value_type": "category",
      "unit": "",
      "time_window": "record_year",
      "scale": "coded_category",
      "coding_status": "source_missing",
      "aliases": [
        "无线覆盖",
        "WiFi覆盖"
      ],
      "confusable_with": [
        60
      ],
      "notes": "原问卷编码表尚未取得，不能自行把数值解释为覆盖等级。"
    },
    {
      "id": 21,
      "question": "学校教学.管理类信息系统的主要部署方式有多少",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "教学和管理类信息系统部署方式数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [],
      "notes": ""
    },
    {
      "id": 22,
      "question": "学校常用管理信息系统间是否实现统一身份认证",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "常用管理信息系统是否实现统一身份认证",
      "subjects": [
        "school"
      ],
      "value_type": "boolean",
      "unit": "",
      "time_window": "record_year",
      "scale": "coded_category",
      "coding_status": "source_missing",
      "aliases": [],
      "confusable_with": [
        23
      ],
      "notes": "原问卷是非题，但编码表尚未取得。"
    },
    {
      "id": 23,
      "question": "学校信息管理系统实现数据统筹管理的包括多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "信息管理系统实现数据统筹管理的类别数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        22,
        24
      ],
      "notes": ""
    },
    {
      "id": 24,
      "question": "现有管理信息基础数据应用范畴有多少个",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "管理信息基础数据应用范畴数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "个",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        23
      ],
      "notes": ""
    },
    {
      "id": 25,
      "question": "校园卡.含一卡通.已经实现的功能有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "校园卡已经实现的功能种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "校园卡功能",
        "一卡通功能数量"
      ],
      "confusable_with": [
        26
      ],
      "notes": ""
    },
    {
      "id": 26,
      "question": "校园卡是否实现了一卡通",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "校园卡是否实现一卡通",
      "subjects": [
        "school"
      ],
      "value_type": "boolean",
      "unit": "",
      "time_window": "record_year",
      "scale": "coded_category",
      "coding_status": "source_missing",
      "aliases": [
        "是否一卡通",
        "校园卡一卡通"
      ],
      "confusable_with": [
        25
      ],
      "notes": "原问卷是非题，但编码表尚未取得。"
    },
    {
      "id": 27,
      "question": "学校安全监控系统校园覆盖范围有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "网络安全",
      "summary": "安全监控系统校园覆盖范围类别数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        55
      ],
      "notes": ""
    },
    {
      "id": 28,
      "question": "最近一年.校级领导参加的信息化相关培训有多少种",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "组织保障",
      "summary": "最近一年校级领导参加的信息化培训种类数量",
      "subjects": [
        "school_leader"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "previous_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        29
      ],
      "notes": ""
    },
    {
      "id": 29,
      "question": "最近一年.校级领导参加的信息化相关培训总计人次",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "组织保障",
      "summary": "最近一年校级领导参加信息化培训总人次",
      "subjects": [
        "school_leader"
      ],
      "value_type": "count",
      "unit": "人次",
      "time_window": "previous_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        28
      ],
      "notes": ""
    },
    {
      "id": 30,
      "question": "学校为促进信息技术在教育教学中的常态应用.采取了多少种措施",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "组织保障",
      "summary": "促进信息技术常态化教学应用的措施种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "常态化应用的措施"
      ],
      "confusable_with": [
        51
      ],
      "notes": ""
    },
    {
      "id": 31,
      "question": "最近一年学校信息化经费总计投入.含国家财政拨款.社会捐助.学校收入.占本校同期教育总经费.不含人员经费.支出的比例",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "最近一年信息化经费占同期教育总经费支出的比例",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "previous_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        32,
        33,
        34,
        35,
        36,
        37,
        48
      ],
      "notes": ""
    },
    {
      "id": 32,
      "question": "网络建设与设备购置.经费占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "网络建设与设备购置经费占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        33,
        34,
        35,
        36,
        37
      ],
      "notes": ""
    },
    {
      "id": 33,
      "question": "系统软件和应用软件经费占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "系统软件和应用软件经费占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        32,
        34,
        35,
        36,
        37
      ],
      "notes": ""
    },
    {
      "id": 34,
      "question": "教学平台和教学资源费用占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "教学平台和教学资源费用占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        32,
        33,
        35,
        36,
        37
      ],
      "notes": ""
    },
    {
      "id": 35,
      "question": "培训费用占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "信息化培训费用占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        32,
        33,
        34,
        36,
        37
      ],
      "notes": ""
    },
    {
      "id": 36,
      "question": "运行与维护费用占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "信息化运行与维护费用占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        32,
        33,
        34,
        35,
        37
      ],
      "notes": ""
    },
    {
      "id": 37,
      "question": "研究及其他费用占比",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "财力保障",
      "summary": "信息化研究及其他费用占比",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31,
        32,
        33,
        34,
        35,
        36
      ],
      "notes": ""
    },
    {
      "id": 38,
      "question": "学校信息技术课程教师人数",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "人力保障",
      "summary": "信息技术课程教师人数",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "人",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        39,
        80,
        81
      ],
      "notes": ""
    },
    {
      "id": 39,
      "question": "由其他学科教师兼任信息技术课程教师人数",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "人力保障",
      "summary": "其他学科教师兼任信息技术课程教师人数",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "人",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        38
      ],
      "notes": ""
    },
    {
      "id": 40,
      "question": "最近一年参加教育信息化专项培训.区县级.含.以上.的教师比例",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "最近一年参加区县级及以上教育信息化专项培训的教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "previous_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        43,
        56
      ],
      "notes": ""
    },
    {
      "id": 41,
      "question": "学校组织的信息化校本培训次数",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "学校组织的信息化校本培训次数",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "次",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        42,
        43,
        56
      ],
      "notes": ""
    },
    {
      "id": 42,
      "question": "分学科的信息化校本培训次数",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "分学科信息化校本培训次数",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "次",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        41,
        43
      ],
      "notes": ""
    },
    {
      "id": 43,
      "question": "学校组织的信息化校本培训覆盖教师比例",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "信息化校本培训覆盖教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        40,
        41,
        56
      ],
      "notes": ""
    },
    {
      "id": 44,
      "question": "最近一年学校参加各级.区县级.含.以上.教育信息技术相关比赛.评比活动.包括优质课.信息化教学设计.课件制作.微课制作.人工智能科创等.的教师人数",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "最近一年参加区县级及以上教育信息技术比赛评比的教师人数",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "人",
      "time_window": "previous_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "信息技术教学比赛",
        "课件评比"
      ],
      "confusable_with": [],
      "notes": ""
    },
    {
      "id": 45,
      "question": "学校教师在区.县级及以上教育云平台中晒课数量",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "教师在区县级及以上教育云平台晒课数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "堂",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "教育云平台晒课",
        "教育云提交展示课"
      ],
      "confusable_with": [],
      "notes": ""
    },
    {
      "id": 46,
      "question": "专递课堂教学中本学期平均每周主讲教室开课课时数",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "本学期专递课堂主讲教室平均每周开课课时数",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "课时/周",
      "time_window": "current_semester_weekly_average",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "主讲教室周课时",
        "专递课堂主讲课时",
        "专递课堂主讲教室",
        "专递课堂向外授课"
      ],
      "confusable_with": [
        5,
        47
      ],
      "notes": ""
    },
    {
      "id": 47,
      "question": "听课教室开课课时数",
      "dimension": "数字资源",
      "dimension_key": "digital_resource",
      "topic": "资源应用",
      "summary": "专递课堂听课教室开课课时数",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "课时",
      "time_window": "current_semester",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        5,
        46
      ],
      "notes": ""
    },
    {
      "id": 48,
      "question": "网络安全费用约占最近一年学校信息化经费总计投入",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "网络安全",
      "summary": "最近一年网络安全费用占学校信息化经费投入的比例",
      "subjects": [
        "school"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "previous_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        31
      ],
      "notes": ""
    },
    {
      "id": 49,
      "question": "学校近一年已开展机考.在线考试的学科包括多少个",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学评价",
      "summary": "最近一年开展机考或在线考试的学科数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "学科",
      "time_window": "previous_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        50
      ],
      "notes": ""
    },
    {
      "id": 50,
      "question": "参与机考的班级数量",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学评价",
      "summary": "参与机考的班级数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "班",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        49
      ],
      "notes": ""
    },
    {
      "id": 51,
      "question": "在落实.双减.工作中.学校采取了多少种措施",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "落实双减工作采取的措施种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "落实双减的措施"
      ],
      "confusable_with": [
        30
      ],
      "notes": ""
    },
    {
      "id": 52,
      "question": "学校信息技术相关课程种类有多少个",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "信息技术相关课程种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        13
      ],
      "notes": ""
    },
    {
      "id": 53,
      "question": "经常使用.每周2.3次.的智能学习空间有哪些",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "教室",
      "summary": "经常使用的智能学习空间项目数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "项",
      "time_window": "weekly_usage",
      "scale": "raw_count",
      "coding_status": "source_missing",
      "aliases": [],
      "confusable_with": [
        18,
        19
      ],
      "notes": "题面询问具体空间，但现存目录未提供选项明细；只能报告核实后的数值，不能编造名称。"
    },
    {
      "id": 54,
      "question": "学校使用物联网技术及各类智能感知设备的应用场景有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "物联网和智能感知设备应用场景种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        55
      ],
      "notes": ""
    },
    {
      "id": 55,
      "question": "学校校园安防具备的功能有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "网络安全",
      "summary": "校园安防具备的功能种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        27,
        54
      ],
      "notes": ""
    },
    {
      "id": 56,
      "question": "教师信息素养培训覆盖教师比例",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "教师信息素养培训覆盖教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        40,
        43,
        57
      ],
      "notes": ""
    },
    {
      "id": 57,
      "question": "网络安全培训覆盖教师比例",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "网络安全培训覆盖教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        56
      ],
      "notes": ""
    },
    {
      "id": 58,
      "question": "近一年学校学生参与信息素养提升实践和展示活动.包括电脑制作.创客.机器人等.的学生人次",
      "dimension": "数字素养",
      "dimension_key": "digital_literacy",
      "topic": "教师数字素养",
      "summary": "近一年学生参与信息素养提升实践和展示活动的人次",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "人次",
      "time_window": "previous_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "学生参加信息素养实践和展示活动",
        "学生参与信息素养提升实践和展示活动"
      ],
      "confusable_with": [
        44
      ],
      "notes": "原始level2仍为教师数字素养；本目录只纠正语义对象，不改评分归属。"
    },
    {
      "id": 59,
      "question": "达到常态化应用的学科教师比例",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学方式",
      "summary": "达到常态化应用水平的学科教师比例",
      "subjects": [
        "teacher"
      ],
      "value_type": "ratio",
      "unit": "%",
      "time_window": "record_year",
      "scale": "raw_ratio",
      "coding_status": "not_applicable",
      "aliases": [
        "常态化应用教师比例",
        "已经常态化使用",
        "已经经常用"
      ],
      "confusable_with": [
        2,
        4
      ],
      "notes": ""
    },
    {
      "id": 60,
      "question": "学校网络出口总带宽（Mbps）",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "网络",
      "summary": "学校网络出口总带宽",
      "subjects": [
        "school"
      ],
      "value_type": "bandwidth",
      "unit": "Mbps",
      "time_window": "record_year",
      "scale": "raw_bandwidth",
      "coding_status": "not_applicable",
      "aliases": [
        "网络出口总带宽"
      ],
      "confusable_with": [
        20
      ],
      "notes": ""
    },
    {
      "id": 61,
      "question": "学校班级教室数",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "教室",
      "summary": "学校班级教室总数",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "间",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "登记的班级教室数"
      ],
      "confusable_with": [
        62
      ],
      "notes": ""
    },
    {
      "id": 62,
      "question": "含有交互式多媒体设备.如电子白板.一体机等.的教室数",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "教室",
      "summary": "配有交互式多媒体设备的教室数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "间",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "带电子白板、一体机等交互设备的教室数",
        "带交互设备的教室数"
      ],
      "confusable_with": [
        17,
        61
      ],
      "notes": ""
    },
    {
      "id": 63,
      "question": "学校统一配备的学生用台式机数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的学生用台式机数量",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        64,
        65,
        66,
        67
      ],
      "notes": ""
    },
    {
      "id": 64,
      "question": "学校统一配备的学生用移动终端数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的学生用移动终端数量",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        63,
        65,
        66,
        68
      ],
      "notes": "移动终端是否包含笔记本或平板尚无来源依据，不得自行与Q65/Q66相加。"
    },
    {
      "id": 65,
      "question": "学校统一配备的学生用笔记本数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的学生用笔记本数量",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        63,
        64,
        66,
        69
      ],
      "notes": ""
    },
    {
      "id": 66,
      "question": "学校统一配备的学生用平板电脑数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的学生用平板电脑数量",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "学生平板",
        "学生用平板"
      ],
      "confusable_with": [
        63,
        64,
        65,
        70
      ],
      "notes": ""
    },
    {
      "id": 67,
      "question": "学校统一配备的教师用台式机数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的教师用台式机数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        63,
        68,
        69,
        70
      ],
      "notes": ""
    },
    {
      "id": 68,
      "question": "学校统一配备的教师用移动终端数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的教师用移动终端数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        64,
        67,
        69,
        70
      ],
      "notes": "移动终端是否包含笔记本或平板尚无来源依据，不得自行与Q69/Q70相加。"
    },
    {
      "id": 69,
      "question": "学校统一配备的教师用笔记本电脑数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的教师用笔记本电脑数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        65,
        67,
        68,
        70
      ],
      "notes": ""
    },
    {
      "id": 70,
      "question": "学校统一配备的教师用平板电脑数量",
      "dimension": "基础设施",
      "dimension_key": "infrastructure",
      "topic": "终端",
      "summary": "学校统一配备的教师用平板电脑数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "台",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "教师平板",
        "老师平板",
        "教师用平板"
      ],
      "confusable_with": [
        66,
        67,
        68,
        69
      ],
      "notes": ""
    },
    {
      "id": 71,
      "question": "学校实现学生类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "学生类教育数字化管理工作内容种类数量",
      "subjects": [
        "student"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "学生类数字化管理工作内容",
        "学生类教育数字化管理工作内容"
      ],
      "confusable_with": [
        72,
        73,
        74,
        75,
        76,
        83
      ],
      "notes": ""
    },
    {
      "id": 72,
      "question": "学校实现教师类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "教师类教育数字化管理工作内容种类数量",
      "subjects": [
        "teacher"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [
        "教师类数字化管理工作内容",
        "教师类教育数字化管理工作内容"
      ],
      "confusable_with": [
        71,
        73,
        74,
        75,
        76,
        83
      ],
      "notes": ""
    },
    {
      "id": 73,
      "question": "学校实现规划与决策类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "规划与决策类教育数字化管理工作内容种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        71,
        72,
        74,
        75,
        76,
        83
      ],
      "notes": ""
    },
    {
      "id": 74,
      "question": "学校实现教务类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "教务类教育数字化管理工作内容种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        71,
        72,
        73,
        75,
        76,
        83
      ],
      "notes": ""
    },
    {
      "id": 75,
      "question": "学校实现教学类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "教学类教育数字化管理工作内容种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        71,
        72,
        73,
        74,
        76,
        83
      ],
      "notes": ""
    },
    {
      "id": 76,
      "question": "学校实现总务类教育数字化管理的工作内容有多少种",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "总务类教育数字化管理工作内容种类数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        71,
        72,
        73,
        74,
        75,
        83
      ],
      "notes": ""
    },
    {
      "id": 77,
      "question": "学校掌握的教师和学生伴随式数据.如课堂行为数据.生理数据等.的来源有多少种",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学评价",
      "summary": "教师和学生伴随式数据来源种类数量",
      "subjects": [
        "teacher",
        "student"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        78
      ],
      "notes": ""
    },
    {
      "id": 78,
      "question": "学校掌握的教师和学生伴随式数据.如课堂行为数据.生理数据等.的应用场景有多少种",
      "dimension": "教育教学",
      "dimension_key": "teaching",
      "topic": "教学评价",
      "summary": "教师和学生伴随式数据应用场景种类数量",
      "subjects": [
        "teacher",
        "student"
      ],
      "value_type": "count",
      "unit": "种",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        77
      ],
      "notes": ""
    },
    {
      "id": 79,
      "question": "学校数字化主管校领导级别",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "组织保障",
      "summary": "学校数字化主管校领导级别",
      "subjects": [
        "school_leader"
      ],
      "value_type": "category",
      "unit": "",
      "time_window": "record_year",
      "scale": "coded_category",
      "coding_status": "source_missing",
      "aliases": [],
      "confusable_with": [
        80,
        82
      ],
      "notes": "原问卷编码表尚未取得，不能自行解释领导级别代码。"
    },
    {
      "id": 80,
      "question": "学校数字化工作部门的专任数字化工作人员总数",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "人力保障",
      "summary": "数字化工作部门专任数字化工作人员总数",
      "subjects": [
        "staff"
      ],
      "value_type": "count",
      "unit": "人",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        38,
        79,
        81
      ],
      "notes": ""
    },
    {
      "id": 81,
      "question": "兼任数字化工作人员总数",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "人力保障",
      "summary": "兼任数字化工作人员总数",
      "subjects": [
        "staff"
      ],
      "value_type": "count",
      "unit": "人",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "not_applicable",
      "aliases": [],
      "confusable_with": [
        39,
        80
      ],
      "notes": ""
    },
    {
      "id": 82,
      "question": "学校是否制定数字化工作和管理的相关制度.规章.办法等",
      "dimension": "保障机制",
      "dimension_key": "support",
      "topic": "组织保障",
      "summary": "学校是否制定数字化工作和管理制度",
      "subjects": [
        "school"
      ],
      "value_type": "boolean",
      "unit": "",
      "time_window": "record_year",
      "scale": "coded_category",
      "coding_status": "source_missing",
      "aliases": [],
      "confusable_with": [
        79,
        83
      ],
      "notes": "原问卷是非题，但编码表尚未取得。"
    },
    {
      "id": 83,
      "question": "学校实现学校及资产类教育数字化管理的工作内容",
      "dimension": "教育治理",
      "dimension_key": "governance",
      "topic": "学校治理",
      "summary": "学校及资产类教育数字化管理工作内容数量",
      "subjects": [
        "school"
      ],
      "value_type": "count",
      "unit": "项",
      "time_window": "record_year",
      "scale": "raw_count",
      "coding_status": "source_missing",
      "aliases": [
        "学校及资产数字化管理",
        "资产类数字化管理"
      ],
      "confusable_with": [
        71,
        72,
        73,
        74,
        75,
        76,
        82
      ],
      "notes": "题面询问工作内容，但现存目录未提供选项明细；只能报告核实后的数值，不能编造具体内容。"
    }
  ]
}

```

## 公共学校别名规则（按以下顺序替换）

```json

[
  [
    "华中师大一附中",
    "华中师范大学第一附属中学"
  ],
  [
    "华师一附中",
    "华中师范大学第一附属中学"
  ],
  [
    "华中师大",
    "华中师范大学"
  ],
  [
    "华师大",
    "华中师范大学"
  ],
  [
    "北师大",
    "北京师范大学"
  ],
  [
    "华东师大",
    "华东师范大学"
  ],
  [
    "东北师大",
    "东北师范大学"
  ],
  [
    "陕师大",
    "陕西师范大学"
  ],
  [
    "南师大",
    "南京师范大学"
  ],
  [
    "湖南师大",
    "湖南师范大学"
  ],
  [
    "华南师大",
    "华南师范大学"
  ],
  [
    "北大附中",
    "北京大学附属中学"
  ],
  [
    "清华附中",
    "清华大学附属中学"
  ],
  [
    "人大附中",
    "中国人民大学附属中学"
  ],
  [
    "一附中",
    "第一附属中学"
  ],
  [
    "二附中",
    "第二附属中学"
  ],
  [
    "三附中",
    "第三附属中学"
  ],
  [
    "附中",
    "附属中学"
  ],
  [
    "附小",
    "附属小学"
  ],
  [
    "一中",
    "第一中学"
  ],
  [
    "二中",
    "第二中学"
  ],
  [
    "三中",
    "第三中学"
  ],
  [
    "四中",
    "第四中学"
  ],
  [
    "五中",
    "第五中学"
  ],
  [
    "六中",
    "第六中学"
  ],
  [
    "七中",
    "第七中学"
  ],
  [
    "八中",
    "第八中学"
  ],
  [
    "九中",
    "第九中学"
  ],
  [
    "十中",
    "第十中学"
  ]
]

```

## 冻结地理字典

本字典覆盖数据中的全部省级单位（31 个省级行政区加新疆生产建设兵团，兵团单列）和全部有足够证据的城市；城市一律用 parent_by_city 中的规范全称，并与其所属 province 同时精确匹配。简称先按 city_aliases / province_aliases 换成全称。区县不在此列出，可直接查询 schools.district（规范区县）；同名区县可能属于不同城市。city_parent_ties 中的城市在不同记录里挂在不同省份下，查询时须同时限定 province。

```json
{"version":"geo-full-v1 (from GEOGRAPHY_REGISTRY_FULL_v1.json, sha256 b6e6e2da9005612c)","application_years":["2020","2021","2022","2023"],"provinces":[{"name":"北京市","aliases":["北京","北京市"],"national_31":true,"separate_corps":false},{"name":"天津市","aliases":["天津","天津市"],"national_31":true,"separate_corps":false},{"name":"河北省","aliases":["河北","河北省"],"national_31":true,"separate_corps":false},{"name":"山西省","aliases":["山西","山西省"],"national_31":true,"separate_corps":false},{"name":"内蒙古自治区","aliases":["内蒙古","内蒙古自治区"],"national_31":true,"separate_corps":false},{"name":"辽宁省","aliases":["辽宁","辽宁省"],"national_31":true,"separate_corps":false},{"name":"吉林省","aliases":["吉林","吉林省"],"national_31":true,"separate_corps":false},{"name":"黑龙江省","aliases":["黑龙江","黑龙江省"],"national_31":true,"separate_corps":false},{"name":"上海市","aliases":["上海","上海市"],"national_31":true,"separate_corps":false},{"name":"江苏省","aliases":["江苏","江苏省"],"national_31":true,"separate_corps":false},{"name":"浙江省","aliases":["浙江","浙江省"],"national_31":true,"separate_corps":false},{"name":"安徽省","aliases":["安徽","安徽省"],"national_31":true,"separate_corps":false},{"name":"福建省","aliases":["福建","福建省"],"national_31":true,"separate_corps":false},{"name":"江西省","aliases":["江西","江西省"],"national_31":true,"separate_corps":false},{"name":"山东省","aliases":["山东","山东省"],"national_31":true,"separate_corps":false},{"name":"河南省","aliases":["河南","河南省"],"national_31":true,"separate_corps":false},{"name":"湖北省","aliases":["湖北","湖北省"],"national_31":true,"separate_corps":false},{"name":"湖南省","aliases":["湖南","湖南省"],"national_31":true,"separate_corps":false},{"name":"广东省","aliases":["广东","广东省"],"national_31":true,"separate_corps":false},{"name":"广西壮族自治区","aliases":["广西","广西壮族自治区"],"national_31":true,"separate_corps":false},{"name":"海南省","aliases":["海南","海南省"],"national_31":true,"separate_corps":false},{"name":"重庆市","aliases":["重庆","重庆市"],"national_31":true,"separate_corps":false},{"name":"四川省","aliases":["四川","四川省"],"national_31":true,"separate_corps":false},{"name":"贵州省","aliases":["贵州","贵州省"],"national_31":true,"separate_corps":false},{"name":"云南省","aliases":["云南","云南省"],"national_31":true,"separate_corps":false},{"name":"西藏自治区","aliases":["西藏","西藏自治区"],"national_31":true,"separate_corps":false},{"name":"陕西省","aliases":["陕西","陕西省"],"national_31":true,"separate_corps":false},{"name":"甘肃省","aliases":["甘肃","甘肃省"],"national_31":true,"separate_corps":false},{"name":"青海省","aliases":["青海","青海省"],"national_31":true,"separate_corps":false},{"name":"宁夏回族自治区","aliases":["宁夏","宁夏回族自治区"],"national_31":true,"separate_corps":false},{"name":"新疆维吾尔自治区","aliases":["新疆","新疆维吾尔自治区"],"national_31":true,"separate_corps":false},{"name":"新疆生产建设兵团","aliases":["兵团","新疆兵团","新疆生产建设兵团","生产建设兵团"],"national_31":false,"separate_corps":true}],"parent_by_city":{"七台河市":"黑龙江省","万宁市":"海南省","三亚市":"海南省","三明市":"福建省","三沙市":"海南省","三门峡市":"河南省","上海市":"上海市","上饶市":"江西省","东方市":"海南省","东莞市":"广东省","东营市":"山东省","中卫市":"宁夏回族自治区","中山市":"广东省","临汾市":"山西省","临沂市":"山东省","临沧市":"云南省","丹东市":"辽宁省","丽水市":"浙江省","丽江市":"云南省","乌兰察布市":"内蒙古自治区","乌海市":"内蒙古自治区","乌鲁木齐市":"新疆维吾尔自治区","乐山市":"四川省","九江市":"江西省","云浮市":"广东省","五指山市":"海南省","亳州市":"安徽省","仙桃市":"湖北省","任丘市":"河北省","伊宁市":"新疆维吾尔自治区","伊春市":"黑龙江省","伊犁哈萨克自治州":"新疆维吾尔自治区","伊犁州":"新疆维吾尔自治区","佛山市":"广东省","佳木斯市":"黑龙江省","保定市":"河北省","保山市":"云南省","保山市腾冲市":"云南省","信阳市":"河南省","儋州市":"海南省","克孜勒苏柯尔克孜自治州":"新疆维吾尔自治区","克拉玛依市":"新疆维吾尔自治区","六安市":"安徽省","六盘水市":"贵州省","兰州市":"甘肃省","兴安盟":"内蒙古自治区","内江市":"四川省","内蒙古兴安盟":"内蒙古自治区","内蒙古包头市":"内蒙古自治区","凉山州":"四川省","凉山彝族自治州":"四川省","包头市":"内蒙古自治区","北京市":"北京市","北海市":"广西壮族自治区","十堰市":"湖北省","南京市":"江苏省","南充市":"四川省","南宁市":"广西壮族自治区","南平市":"福建省","南昌市":"江西省","南通市":"江苏省","南阳市":"河南省","博尔塔拉蒙古自治州":"新疆维吾尔自治区","厦门市":"福建省","双鸭山市":"黑龙江省","台州市":"浙江省","合肥市":"安徽省","吉安市":"江西省","吉林市":"吉林省","吐鲁番市":"新疆维吾尔自治区","吕梁市":"山西省","吴忠市":"宁夏回族自治区","周口市":"河南省","呼伦贝尔市":"内蒙古自治区","呼和浩特市":"内蒙古自治区","和田地区":"新疆维吾尔自治区","和田市":"新疆维吾尔自治区","咸宁市":"湖北省","咸阳市":"陕西省","哈密市":"新疆维吾尔自治区","哈尔滨市":"黑龙江省","唐山市":"河北省","商丘市":"河南省","商洛市":"陕西省","喀什地区":"新疆维吾尔自治区","喀什市":"新疆维吾尔自治区","嘉兴市":"浙江省","嘉峪关市":"甘肃省","四平市":"吉林省","固原市":"宁夏回族自治区","图木舒克市":"新疆维吾尔自治区","塔城地区":"新疆维吾尔自治区","大兴安岭地区":"黑龙江省","大冶市":"湖北省","大同市":"山西省","大庆市":"黑龙江省","大理州":"云南省","大理市":"云南省","大理白族自治州":"云南省","大连市":"辽宁省","天水市":"甘肃省","天津市":"天津市","天门市":"湖北省","太原市":"山西省","威海市":"山东省","娄底市":"湖南省","孝感市":"湖北省","宁夏银川市":"宁夏回族自治区","宁德市":"福建省","宁波市":"浙江省","安庆市":"安徽省","安康市":"陕西省","安阳市":"河南省","安顺市":"贵州省","定州市":"河北省","宜宾市":"四川省","宜昌市":"湖北省","宜春市":"江西省","宝鸡市":"陕西省","宣城市":"安徽省","宿州市":"安徽省","宿迁市":"江苏省","山南市":"西藏自治区","岳阳市":"湖南省","崇左市":"广西壮族自治区","巴中市":"四川省","巴彦淖尔市":"内蒙古自治区","巴音郭楞蒙古自治州":"新疆维吾尔自治区","常宁市":"湖南省","常州市":"江苏省","常德市":"湖南省","平顶山市":"河南省","广元市":"四川省","广安市":"四川省","广州市":"广东省","广西南宁市":"广西壮族自治区","广西桂林市":"广西壮族自治区","广西梧州":"广西壮族自治区","广西梧州市":"广西壮族自治区","广西玉林市":"广西壮族自治区","广西贵港市":"广西壮族自治区","广西钦州":"广西壮族自治区","广西钦州市":"广西壮族自治区","廊坊市":"河北省","延安市":"陕西省","延边朝鲜族自治州":"吉林省","开封市":"河南省","张家口市":"河北省","张家界市":"湖南省","张掖市":"甘肃省","徐州市":"江苏省","德宏傣族景颇族自治州":"云南省","德宏州":"云南省","德宏州傣族景颇族自治州":"云南省","德州市":"山东省","德阳市":"四川省","忻州市":"山西省","怀化市":"湖南省","怒江傈僳族自治州":"云南省","怒江州":"云南省","恩施土家族苗族自治州":"湖北省","恩施州":"湖北省","恩施自治州":"湖北省","惠州市":"广东省","成都市":"四川省","扬州市":"江苏省","承德市":"河北省","抚州市":"江西省","抚顺市":"辽宁省","拉萨市":"西藏自治区","揭阳市":"广东省","攀枝花市":"四川省","文山壮族苗族自治州":"云南省","文山州":"云南省","文山市":"云南省","文山苗族壮族自治州":"云南省","文昌市":"海南省","新乡市":"河南省","新余市":"江西省","新疆伊犁哈萨克自治州":"新疆维吾尔自治区","新疆伊犁州":"新疆维吾尔自治区","新疆喀什地区":"新疆维吾尔自治区","新疆昌吉州":"新疆维吾尔自治区","新疆阿克苏地区":"新疆维吾尔自治区","无锡市":"江苏省","日喀则市":"西藏自治区","日照市":"山东省","昆明市":"云南省","昌吉回族自治州":"新疆维吾尔自治区","昌吉州":"新疆维吾尔自治区","昌都市":"西藏自治区","昭通市":"云南省","晋中市":"山西省","晋城市":"山西省","普洱市":"云南省","景德镇市":"江西省","曲靖市":"云南省","朔州市":"山西省","朝阳市":"辽宁省","本溪市":"辽宁省","来宾市":"广西壮族自治区","杭州市":"浙江省","松原市":"吉林省","林芝市":"西藏自治区","果洛藏族自治州":"青海省","枣庄市":"山东省","柳州市":"广西壮族自治区","株洲市":"湖南省","桂林市":"广西壮族自治区","梅州市":"广东省","梧州市":"广西壮族自治区","楚雄州":"云南省","楚雄彝族自治州":"云南省","榆林市":"陕西省","武威市":"甘肃省","武汉市":"湖北省","毕节市":"贵州省","永州市":"湖南省","汉中市":"陕西省","汕头市":"广东省","汕尾市":"广东省","江门市":"广东省","池州市":"安徽省","沈阳市":"辽宁省","沧州市":"河北省","河池市":"广西壮族自治区","河源市":"广东省","泉州市":"福建省","泰安市":"山东省","泰州市":"江苏省","泸州市":"四川省","洛阳市":"河南省","济南市":"山东省","济宁市":"山东省","济源市":"河南省","海东市":"青海省","海北藏族自治州":"青海省","海南州":"青海省","海南藏族自治州":"青海省","海口市":"海南省","海西州":"青海省","海西蒙古族藏族自治州":"青海省","淄博市":"山东省","淮北市":"安徽省","淮南市":"安徽省","淮安市":"江苏省","深圳市":"广东省","清远市":"广东省","温州市":"浙江省","渭南市":"陕西省","湖州市":"浙江省","湘潭市":"湖南省","湘西土家族苗族自治州":"湖南省","湘西州":"湖南省","湘西自治州":"湖南省","湛江市":"广东省","滁州市":"安徽省","滨州市":"山东省","漯河市":"河南省","漳州市":"福建省","潍坊市":"山东省","潜江市":"湖北省","潮州市":"广东省","濮阳市":"河南省","烟台市":"山东省","焦作市":"河南省","牡丹江市":"黑龙江省","玉林市":"广西壮族自治区","玉树藏族自治州":"青海省","玉溪市":"云南省","珠海市":"广东省","琼海市":"海南省","瑞安市":"浙江省","甘南藏族自治州":"甘肃省","甘孜州":"四川省","甘孜藏族自治州":"四川省","白城市":"吉林省","白山市":"吉林省","白银市":"甘肃省","百色市":"广西壮族自治区","益阳市":"湖南省","盐城市":"江苏省","盘锦市":"辽宁省","眉山市":"四川省","石嘴山市":"宁夏回族自治区","石家庄市":"河北省","神木市":"陕西省","福州市":"福建省","秦皇岛市":"河北省","红河哈尼族彝族自治州":"云南省","红河州":"云南省","绍兴市":"浙江省","绥化市":"黑龙江省","绵阳市":"四川省","聊城市":"山东省","肇庆市":"广东省","自贡市":"四川省","舟山市":"浙江省","芜湖市":"安徽省","苏州市":"江苏省","茂名市":"广东省","荆州市":"湖北省","荆门市":"湖北省","莆田市":"福建省","菏泽市":"山东省","萍乡市":"江西省","营口市":"辽宁省","葫芦岛市":"辽宁省","蒙自市":"云南省","蚌埠市":"安徽省","衡水市":"河北省","衡阳市":"湖南省","衢州市":"浙江省","襄阳市":"湖北省","西双版纳傣族自治州":"云南省","西双版纳州":"云南省","西宁市":"青海省","西安市":"陕西省","西昌市":"四川省","许昌市":"河南省","贵港市":"广西壮族自治区","贵阳市":"贵州省","贺州市":"广西壮族自治区","资阳市":"四川省","赣州市":"江西省","赤峰市":"内蒙古自治区","辛集市":"河北省","辽源市":"吉林省","辽阳市":"辽宁省","达州市":"四川省","运城市":"山西省","连云港市":"江苏省","迪庆藏族自治州":"云南省","通化市":"吉林省","通辽市":"内蒙古自治区","遂宁市":"四川省","遵义市":"贵州省","邓州市":"河南省","邢台市":"河北省","那曲市":"西藏自治区","邯郸市":"河北省","邵阳市":"湖南省","郑州市":"河南省","郴州市":"湖南省","都匀市":"贵州省","鄂尔多斯市":"内蒙古自治区","鄂州市":"湖北省","酒泉市":"甘肃省","重庆市":"重庆市","金华市":"浙江省","金昌市":"甘肃省","钦州市":"广西壮族自治区","铁岭市":"辽宁省","铜仁市":"贵州省","铜川市":"陕西省","铜陵市":"安徽省","银川市":"宁夏回族自治区","锡林郭勒盟":"内蒙古自治区","锦州市":"辽宁省","镇江市":"江苏省","长春市":"吉林省","长沙市":"湖南省","长治市":"山西省","阜新市":"辽宁省","阜阳市":"安徽省","防城港市":"广西壮族自治区","阳江市":"广东省","阳泉市":"山西省","阿克苏地区":"新疆维吾尔自治区","阿勒泰地区":"新疆维吾尔自治区","阿坝州":"四川省","阿坝藏族羌族自治州":"四川省","阿拉善盟":"内蒙古自治区","阿里地区":"西藏自治区","随州市":"湖北省","雅安市":"四川省","青岛市":"山东省","鞍山市":"辽宁省","韩城市":"陕西省","韶关市":"广东省","马鞍山市":"安徽省","驻马店市":"河南省","鸡西市":"黑龙江省","鹤壁市":"河南省","鹤岗市":"黑龙江省","黄冈市":"湖北省","黄南藏族自治州":"青海省","黄山市":"安徽省","黄石市":"湖北省","黑河市":"黑龙江省","黔东南州":"贵州省","黔东南苗族侗族自治州":"贵州省","黔南州":"贵州省","黔南布依族苗族自治州":"贵州省","黔西南州":"贵州省","黔西南布依族苗族自治州":"贵州省","黔西南布依苗族自治州":"贵州省","齐齐哈尔市":"黑龙江省","龙岩市":"福建省"},"city_parent_ties":{"周囗市":["河南省","湖南省"],"格尔木市":["内蒙古自治区","青海省"],"毕节市黔西市":["四川省","贵州省"],"石河子市":["新疆生产建设兵团","新疆维吾尔自治区"],"阿拉尔市":["新疆生产建设兵团","新疆维吾尔自治区"]},"city_aliases":{"七台河":"七台河市","万宁":"万宁市","三亚":"三亚市","三明":"三明市","三沙":"三沙市","三门峡":"三门峡市","上饶":"上饶市","东方":"东方市","东莞":"东莞市","中卫":"中卫市","临汾":"临汾市","临沂":"临沂市","临沧":"临沧市","丹东":"丹东市","丽水":"丽水市","丽江":"丽江市","乌兰察布":"乌兰察布市","乌海":"乌海市","乐山":"乐山市","九江":"九江市","云浮":"云浮市","五指山":"五指山市","亳州":"亳州市","仙桃":"仙桃市","伊春":"伊春市","佛山":"佛山市","佳木斯":"佳木斯市","保定":"保定市","保山":"保山市","保山市腾冲":"保山市腾冲市","信阳":"信阳市","儋州":"儋州市","六安":"六安市","六盘水":"六盘水市","兰州":"兰州市","内江":"内江市","内蒙古包头":"内蒙古包头市","包头":"包头市","北海":"北海市","十堰":"十堰市","南京":"南京市","南充":"南充市","南宁":"南宁市","南平":"南平市","南通":"南通市","南阳":"南阳市","厦门":"厦门市","双鸭山":"双鸭山市","台州":"台州市","合肥":"合肥市","吐鲁番":"吐鲁番市","吕梁":"吕梁市","吴忠":"吴忠市","周口":"周口市","呼伦贝尔":"呼伦贝尔市","呼和浩特":"呼和浩特市","咸宁":"咸宁市","咸阳":"咸阳市","哈密":"哈密市","哈尔滨":"哈尔滨市","唐山":"唐山市","商丘":"商丘市","商洛":"商洛市","喀什":"喀什市","嘉兴":"嘉兴市","嘉峪关":"嘉峪关市","四平":"四平市","固原":"固原市","图木舒克":"图木舒克市","大冶":"大冶市","大庆":"大庆市","大理":"大理市","大连":"大连市","天水":"天水市","天门":"天门市","太原":"太原市","威海":"威海市","孝感":"孝感市","宁夏银川":"宁夏银川市","宁德":"宁德市","宁波":"宁波市","安庆":"安庆市","安康":"安康市","安阳":"安阳市","安顺":"安顺市","定州":"定州市","宜宾":"宜宾市","宜昌":"宜昌市","宜春":"宜春市","宝鸡":"宝鸡市","宣城":"宣城市","宿州":"宿州市","宿迁":"宿迁市","山南":"山南市","崇左":"崇左市","巴中":"巴中市","巴彦淖尔":"巴彦淖尔市","常州":"常州市","常德":"常德市","平顶山":"平顶山市","广元":"广元市","广州":"广州市","广西南宁":"广西南宁市","广西桂林":"广西桂林市","广西玉林":"广西玉林市","广西贵港":"广西贵港市","廊坊":"廊坊市","延安":"延安市","开封":"开封市","张家口":"张家口市","张家界":"张家界市","张掖":"张掖市","徐州":"徐州市","德州":"德州市","德阳":"德阳市","忻州":"忻州市","怀化":"怀化市","惠州":"惠州市","成都":"成都市","扬州":"扬州市","抚州":"抚州市","拉萨":"拉萨市","揭阳":"揭阳市","攀枝花":"攀枝花市","文昌":"文昌市","新余":"新余市","无锡":"无锡市","日喀则":"日喀则市","日照":"日照市","昌都":"昌都市","昭通":"昭通市","晋中":"晋中市","晋城":"晋城市","普洱":"普洱市","景德镇":"景德镇市","曲靖":"曲靖市","朔州":"朔州市","来宾":"来宾市","杭州":"杭州市","松原":"松原市","林芝":"林芝市","枣庄":"枣庄市","柳州":"柳州市","株洲":"株洲市","桂林":"桂林市","梅州":"梅州市","梧州":"梧州市","榆林":"榆林市","武威":"武威市","武汉":"武汉市","毕节":"毕节市","永州":"永州市","汉中":"汉中市","汕头":"汕头市","汕尾":"汕尾市","江门":"江门市","池州":"池州市","沈阳":"沈阳市","河池":"河池市","河源":"河源市","泉州":"泉州市","泰安":"泰安市","泰州":"泰州市","泸州":"泸州市","洛阳":"洛阳市","济南":"济南市","济宁":"济宁市","济源":"济源市","海东":"海东市","海口":"海口市","淄博":"淄博市","淮北":"淮北市","淮南":"淮南市","深圳":"深圳市","清远":"清远市","温州":"温州市","渭南":"渭南市","湖州":"湖州市","湛江":"湛江市","滁州":"滁州市","滨州":"滨州市","漯河":"漯河市","漳州":"漳州市","潍坊":"潍坊市","潜江":"潜江市","潮州":"潮州市","烟台":"烟台市","焦作":"焦作市","牡丹江":"牡丹江市","玉林":"玉林市","玉溪":"玉溪市","珠海":"珠海市","琼海":"琼海市","瑞安":"瑞安市","白城":"白城市","白山":"白山市","百色":"百色市","益阳":"益阳市","盐城":"盐城市","盘锦":"盘锦市","眉山":"眉山市","石嘴山":"石嘴山市","石家庄":"石家庄市","神木":"神木市","福州":"福州市","秦皇岛":"秦皇岛市","绍兴":"绍兴市","绥化":"绥化市","绵阳":"绵阳市","聊城":"聊城市","肇庆":"肇庆市","自贡":"自贡市","舟山":"舟山市","苏州":"苏州市","茂名":"茂名市","荆门":"荆门市","莆田":"莆田市","菏泽":"菏泽市","萍乡":"萍乡市","营口":"营口市","葫芦岛":"葫芦岛市","蒙自":"蒙自市","蚌埠":"蚌埠市","衡水":"衡水市","衢州":"衢州市","襄阳":"襄阳市","西宁":"西宁市","西昌":"西昌市","许昌":"许昌市","贵港":"贵港市","贵阳":"贵阳市","贺州":"贺州市","赣州":"赣州市","赤峰":"赤峰市","辛集":"辛集市","辽源":"辽源市","辽阳":"辽阳市","达州":"达州市","运城":"运城市","连云港":"连云港市","通辽":"通辽市","遂宁":"遂宁市","邓州":"邓州市","那曲":"那曲市","郑州":"郑州市","郴州":"郴州市","都匀":"都匀市","鄂尔多斯":"鄂尔多斯市","鄂州":"鄂州市","酒泉":"酒泉市","金华":"金华市","金昌":"金昌市","钦州":"钦州市","铜仁":"铜仁市","铜川":"铜川市","铜陵":"铜陵市","银川":"银川市","锦州":"锦州市","镇江":"镇江市","长春":"长春市","长沙":"长沙市","阜新":"阜新市","阜阳":"阜阳市","防城港":"防城港市","阳江":"阳江市","阳泉":"阳泉市","随州":"随州市","雅安":"雅安市","青岛":"青岛市","鞍山":"鞍山市","韩城":"韩城市","韶关":"韶关市","马鞍山":"马鞍山市","驻马店":"驻马店市","鸡西":"鸡西市","鹤壁":"鹤壁市","鹤岗":"鹤岗市","黄冈":"黄冈市","黄石":"黄石市","黑河":"黑河市","齐齐哈尔":"齐齐哈尔市","龙岩":"龙岩市"},"province_aliases":{"上海":"上海市","上海市":"上海市","云南":"云南省","云南省":"云南省","兵团":"新疆生产建设兵团","内蒙古":"内蒙古自治区","内蒙古自治区":"内蒙古自治区","北京":"北京市","北京市":"北京市","吉林":"吉林省","吉林省":"吉林省","四川":"四川省","四川省":"四川省","天津":"天津市","天津市":"天津市","宁夏":"宁夏回族自治区","宁夏回族自治区":"宁夏回族自治区","安徽":"安徽省","安徽省":"安徽省","山东":"山东省","山东省":"山东省","山西":"山西省","山西省":"山西省","广东":"广东省","广东省":"广东省","广西":"广西壮族自治区","广西壮族自治区":"广西壮族自治区","新疆":"新疆维吾尔自治区","新疆兵团":"新疆生产建设兵团","新疆生产建设兵团":"新疆生产建设兵团","新疆维吾尔自治区":"新疆维吾尔自治区","江苏":"江苏省","江苏省":"江苏省","江西":"江西省","江西省":"江西省","河北":"河北省","河北省":"河北省","河南":"河南省","河南省":"河南省","浙江":"浙江省","浙江省":"浙江省","海南":"海南省","海南省":"海南省","湖北":"湖北省","湖北省":"湖北省","湖南":"湖南省","湖南省":"湖南省","甘肃":"甘肃省","甘肃省":"甘肃省","生产建设兵团":"新疆生产建设兵团","福建":"福建省","福建省":"福建省","西藏":"西藏自治区","西藏自治区":"西藏自治区","贵州":"贵州省","贵州省":"贵州省","辽宁":"辽宁省","辽宁省":"辽宁省","重庆":"重庆市","重庆市":"重庆市","陕西":"陕西省","陕西省":"陕西省","青海":"青海省","青海省":"青海省","黑龙江":"黑龙江省","黑龙江省":"黑龙江省"},"province_city_homonyms":{"上海":[{"city":"上海市","district":null,"level":"city","province":"上海市"},{"city":null,"district":null,"level":"province","province":"上海市"}],"上海市":[{"city":"上海市","district":null,"level":"city","province":"上海市"},{"city":null,"district":null,"level":"province","province":"上海市"}],"北京":[{"city":"北京市","district":null,"level":"city","province":"北京市"},{"city":null,"district":null,"level":"province","province":"北京市"}],"北京市":[{"city":"北京市","district":null,"level":"city","province":"北京市"},{"city":null,"district":null,"level":"province","province":"北京市"}],"吉林":[{"city":"吉林市","district":null,"level":"city","province":"吉林省"},{"city":null,"district":null,"level":"province","province":"吉林省"}],"天津":[{"city":"天津市","district":null,"level":"city","province":"天津市"},{"city":null,"district":null,"level":"province","province":"天津市"}],"天津市":[{"city":"天津市","district":null,"level":"city","province":"天津市"},{"city":null,"district":null,"level":"province","province":"天津市"}],"重庆":[{"city":"重庆市","district":null,"level":"city","province":"重庆市"},{"city":null,"district":null,"level":"province","province":"重庆市"}],"重庆市":[{"city":"重庆市","district":null,"level":"city","province":"重庆市"},{"city":null,"district":null,"level":"province","province":"重庆市"}]},"policy":{"city_min_evidence":{"years":2,"school_year_records":10},"city_evidence_rule":"at least two available years OR ten school-year records; otherwise registration anomaly, excluded from resolution candidates","parent":"unique maximum number of school-year records across all available years","tied_parent":"keep alternatives; explicit parent required; no alphabetical tie-break","minority":"exclude from the city query using exact province AND city; never update records","city_name_pattern":"2-10 Chinese characters followed by 市/州/盟/地区; no province/address/school markers","city_category_labels":["县级市","地市","地级市","城市","直辖市","省辖市","自治州"],"district_category_labels":["全区","区县","区域","县区","县辖区","地区","城区","市区","市辖区","本区","该区","辖区","郊区","镇区"],"non_city_values":"all preserved in GEOGRAPHY_CITY_VALUES_FULL_v1.csv; not used as place tokens","legacy_municipal_names":"default city scope for municipality name shared with its province; typed provincial tasks may select the provincial scope","legacy_city_short_names":["上海市","北京市","广州市","成都市","武汉市","长沙市"],"city_short_name_conflicts":"province, other city, and county names/aliases require clarification; retain the original six-city short-name interpretation","evidence":"registration-derived membership policy, not verified administrative identity"}}
```
