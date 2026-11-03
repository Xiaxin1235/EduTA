"""83 题数字化问卷指标目录与智能同义词匹配器。

将自然语言中的硬件配置、教学软件、人员培训等词汇映射到具体的问卷题号或学校属性。
支持：
- 精确同义词/关键词匹配
- 教师端与学生端区分（如教师平板 vs 学生平板）
- 复合指标定义（如生机比、师机比、终端总量）
- 题面通用全文检索（TF-IDF / 分词重合度）
"""

import re
from typing import Dict, List, Optional, Tuple, Any

# 双题/题项组合规则（用于问答题项对、差值与派生比率）
PAIRED_ITEM_RULES: List[Dict[str, Any]] = [
    # 1. Q80 & Q81: 专任与兼任数字化人员各有多少
    {
        "pattern": r"(?:专任|全职)(?:和|与|及|以及)(?:兼职|兼任)?数字化(?:专业)?(?:工作)?人员|(?:兼任|兼职)(?:和|与|及|以及)(?:专任|全职)?数字化(?:专业)?(?:工作)?人员|专做这件事和兼着做的|专任和兼任数字化专业人员",
        "qids": [80, 81],
        "name": "专任与兼任数字化专业人员数",
        "items": [
            {"type": "question", "qid": 80, "name": "学校专任数字化专业人员数量", "unit": "人"},
            {"type": "question", "qid": 81, "name": "学校兼任数字化专业人员数量", "unit": "人"},
        ],
        "derived": None,
    },
    # 2. Q13 & Q14: 人工智能开设形式与资源来源
    {
        "pattern": r"人工智能课程.*(?:开设形式|开设的形式).*资源来源|人工智能课程.*(?:形式.*来源|来源.*形式)",
        "qids": [13, 14],
        "name": "人工智能课程开设形式与教学资源来源",
        "items": [
            {"type": "question", "qid": 13, "name": "学校开设人工智能相关课程的形式", "unit": "种"},
            {"type": "question", "qid": 14, "name": "学校开展人工智能教育教学资源的主要来源", "unit": "种"},
        ],
        "derived": None,
    },
    # 3. Q49 & Q50: 机考学科数与班级数
    {
        "pattern": r"机考.*(?:学科数|学科).*班级数|机考或在线考试的学科数.*班级数",
        "qids": [49, 50],
        "name": "开展机考学科数与班级数",
        "items": [
            {"type": "question", "qid": 49, "name": "学校近一年开展机考或在线考试的学科数", "unit": "个学科"},
            {"type": "question", "qid": 50, "name": "参与机考的班级数量", "unit": "个班级"},
        ],
        "derived": None,
    },
    # 4. Q62 & Q61: 交互多媒体教室占班级教室的比例 (Q62 / Q61)
    {
        "pattern": r"(?:交互式多媒体设备教室数|多媒体设备教室数|交互多媒体教室)与(?:班级教室数|班级教室).*除以|(?:交互式多媒体设备教室数|多媒体教室)占(?:班级教室)?的?比例",
        "qids": [62, 61],
        "name": "交互多媒体教室占班级教室比例",
        "items": [
            {"type": "question", "qid": 62, "name": "含有交互式多媒体设备的教室数", "unit": "间"},
            {"type": "question", "qid": 61, "name": "学校班级教室数", "unit": "间"},
        ],
        "derived": {"operation": "divide_percent", "left_qid": 62, "right_qid": 61},
    },
    # 5. Q61 & Q62: 普通教室比交互多媒体教室多几间 (Q61 - Q62)
    {
        "pattern": r"(?:登记的班级教室数|班级教室数|普通教室).*带(?:电子白板|一体机|交互设备|交互式多媒体).*前一个.*减后一个|(?:班级教室数|普通教室)比(?:交互式多媒体|交互设备|多媒体教室)多几间",
        "qids": [61, 62],
        "name": "班级教室与交互多媒体教室差值",
        "items": [
            {"type": "question", "qid": 61, "name": "学校班级教室数", "unit": "间"},
            {"type": "question", "qid": 62, "name": "含有交互式多媒体设备的教室数", "unit": "间"},
        ],
        "derived": {"operation": "subtract", "left_qid": 61, "right_qid": 62},
    },
    # 6. Q41 & Q42: 校本培训次数比分学科培训多几次 (Q41 - Q42)
    {
        "pattern": r"(?:信息化校本培训次数|校本培训次数)和分学科(?:校本培训次数|培训次数)|校本培训次数比分学科培训多几次",
        "qids": [41, 42],
        "name": "校本培训与分学科培训次数差值",
        "items": [
            {"type": "question", "qid": 41, "name": "学校组织的信息化校本培训次数", "unit": "次"},
            {"type": "question", "qid": 42, "name": "学校组织的分学科信息化培训次数", "unit": "次"},
        ],
        "derived": {"operation": "subtract", "left_qid": 41, "right_qid": 42},
    },
    # 7. Q71 & Q72: 学生管理数字化内容比教师管理多几项 (Q71 - Q72)
    {
        "pattern": r"学生类.*教师类.*数字化管理工作内容|学生管理数字化内容比教师管理多几项",
        "qids": [71, 72],
        "name": "学生类与教师类数字化管理工作内容项数差值",
        "items": [
            {"type": "question", "qid": 71, "name": "学校实现学生类教育数字化管理的工作内容", "unit": "项"},
            {"type": "question", "qid": 72, "name": "学校实现教师类教育数字化管理的工作内容", "unit": "项"},
        ],
        "derived": {"operation": "subtract", "left_qid": 71, "right_qid": 72},
    },
    # 8. Q38 & Q39: 信息技术课程教师与兼任教师各有多少
    {
        "pattern": r"信息技术课程教师人数.*兼任信息技术课程教师|信息技术课程教师.*兼任教师",
        "qids": [38, 39],
        "name": "信息技术课程专任与兼任教师人数",
        "items": [
            {"type": "question", "qid": 38, "name": "信息技术课程教师人数", "unit": "人"},
            {"type": "question", "qid": 39, "name": "兼任信息技术课程教师人数", "unit": "人"},
        ],
        "derived": None,
    },
    # 9. Q70 & Q66: 教师用平板电脑比学生用多多少台 (Q70 - Q66)
    {
        "pattern": r"教师用.*与.*学生用平板电脑|教师用平板电脑比学生用多多少台|教师用数量减学生用数量",
        "qids": [70, 66],
        "name": "教师用与学生用平板电脑差值",
        "items": [
            {"type": "question", "qid": 70, "name": "学校统一配备的教师用平板电脑数量", "unit": "台"},
            {"type": "question", "qid": 66, "name": "学校统一配备的学生用平板电脑数量", "unit": "台"},
        ],
        "derived": {"operation": "subtract", "left_qid": 70, "right_qid": 66},
    },
    # 10. Q18 & Q19: 智能学习场所总数和类型分别是什么
    {
        "pattern": r"智能技术的学习场所.*(?:建了多少处.*多少种类型|总数.*类型|数量.*类型)|带智能技术的学习场所.*分清建了多少处",
        "qids": [18, 19],
        "name": "智能学习场所数量与类型",
        "items": [
            {"type": "question", "qid": 18, "name": "学校智能学习场所建设数量", "unit": "间"},
            {"type": "question", "qid": 19, "name": "学校智能学习场所建设类型", "unit": "项"},
        ],
        "derived": None,
    },
    # 11. Q7 & Q8: 网络学习空间有哪些功能？由什么平台支撑？
    {
        "pattern": r"学习空间功能有几种.*网络空间支撑平台有几个|网络学习空间.*功能.*支撑平台",
        "qids": [7, 8],
        "name": "网络学习空间功能与支撑平台",
        "items": [
            {"type": "question", "qid": 7, "name": "网络学习空间主要功能", "unit": "种"},
            {"type": "question", "qid": 8, "name": "网络学习空间支撑平台类型", "unit": "个"},
        ],
        "derived": None,
    },
    # 12. Q28 & Q29: 校级领导培训种类和累计人次是多少
    {
        "pattern": r"校领导参加数字化学习.*几类培训.*累计多少人次|校领导数字化培训.*种类.*人次",
        "qids": [28, 29],
        "name": "校级领导培训种类与累计人次",
        "items": [
            {"type": "question", "qid": 28, "name": "校级领导参加数字化培训种类", "unit": "类"},
            {"type": "question", "qid": 29, "name": "校级领导参加数字化培训累计人次", "unit": "人次"},
        ],
        "derived": None,
    },
    # 13. Q67 & Q69: 教师台式机和笔记本电脑总共有多少台 (Q67 + Q69)
    {
        "pattern": r"(?:台式机和笔记本|台式机与笔记本).*(?:分别多少台.*加起来多少台|总共|合计)",
        "qids": [67, 69],
        "name": "教师台式机与笔记本电脑总量",
        "items": [
            {"type": "question", "qid": 67, "name": "教师用台式计算机数量", "unit": "台"},
            {"type": "question", "qid": 69, "name": "教师用笔记本电脑数量", "unit": "台"},
        ],
        "derived": {"operation": "add", "left_qid": 67, "right_qid": 69},
    },
    # 14. Q77 & Q78: 过程性数据来自哪些渠道？主要用于哪些场景？
    {
        "pattern": r"过程数据.*从几类渠道取得.*用于几类场景|过程性数据.*渠道.*场景",
        "qids": [77, 78],
        "name": "过程性数据采集渠道与应用场景",
        "items": [
            {"type": "question", "qid": 77, "name": "过程性数据采集渠道来源种类", "unit": "类"},
            {"type": "question", "qid": 78, "name": "过程性数据应用场景种类", "unit": "类"},
        ],
        "derived": None,
    },
    # 15. Q54 & Q55: 物联网设备用在哪些场景？安防功能有哪些？
    {
        "pattern": r"物联感应设备用在几类场景.*校园安全系统有几类功能|物联网.*场景.*安防.*功能",
        "qids": [54, 55],
        "name": "物联网应用场景与校园安防功能",
        "items": [
            {"type": "question", "qid": 54, "name": "物联感应设备应用场景数", "unit": "类"},
            {"type": "question", "qid": 55, "name": "校园安全系统功能数", "unit": "类"},
        ],
        "derived": None,
    },
    # 16. Q4 & Q59: 能用技术上课的教师比例与经常用标准化分数的差值 (Q4 - Q59)
    {
        "pattern": r"会用技术上课.*已经经常用.*前项减后项|会用技术上课.*经常用.*差值",
        "qids": [4, 59],
        "name": "会用技术上课与经常用标准化分数差值",
        "items": [
            {"type": "question", "qid": 4, "name": "能够利用数字化终端开展教学的教师比例", "unit": "%"},
            {"type": "question", "qid": 59, "name": "经常使用标准化分数呈现考试成绩的教师比例", "unit": "%"},
        ],
        "derived": {"operation": "subtract", "left_qid": 4, "right_qid": 59},
    },
    # 17. Q63 & Q67: 学生台式机数量是教师台式机的几倍 (Q63 / Q67)
    {
        "pattern": r"学生.*台式电脑与.*老师.*台式电脑.*学生用台数除以教师用台数|学生台式机数量是教师台式机的几倍",
        "qids": [63, 67],
        "name": "学生台式机与教师台式机倍数",
        "items": [
            {"type": "question", "qid": 63, "name": "学校统一配备的学生用台式电脑数量", "unit": "台"},
            {"type": "question", "qid": 67, "name": "学校统一配备的教师用台式电脑数量", "unit": "台"},
        ],
        "derived": {"operation": "divide", "left_qid": 63, "right_qid": 67},
    },
    # 18. Q30 & Q51: 常态化应用措施和双减举措分别有哪些
    {
        "pattern": r"常用于教学而采取的措施有几种.*双减.*措施又有几种|常态化应用措施.*双减",
        "qids": [30, 51],
        "name": "常态化教学应用措施与双减举措",
        "items": [
            {"type": "question", "qid": 30, "name": "数字化常态化应用措施数", "unit": "种"},
            {"type": "question", "qid": 51, "name": "数字化助力双减落实举措数", "unit": "种"},
        ],
        "derived": None,
    },
]

# 常用预置指标规则：(正则或关键词列表, 目标题号, 单位, 维度说明, 关联题号)
PRESET_RULES: List[Dict[str, Any]] = [
    # 教师平板电脑
    {
        "keys": ["教师平板", "老师平板", "教师用平板", "老师的平板", "给老师配置.*平板", "教师.*ipad", "老师.*ipad"],
        "qid": 70,
        "unit": "台",
        "name": "学校统一配备的教师用平板电脑数量",
        "related": [67, 68, 69, 66],
    },
    # 学生平板电脑
    {
        "keys": ["学生平板", "学生用平板", "学生的平板", "给学生配置.*平板", "学生.*ipad"],
        "qid": 66,
        "unit": "台",
        "name": "学校统一配备的学生用平板电脑数量",
        "related": [63, 64, 65, 70],
    },
    # 教师台式机
    {
        "keys": ["教师台式", "老师台式", "教师用台式", "老师.*台式电脑", "教师电脑"],
        "qid": 67,
        "unit": "台",
        "name": "学校统一配备的教师用台式机数量",
        "related": [68, 69, 70],
    },
    # 学生台式机
    {
        "keys": ["学生台式", "学生用台式", "机房电脑", "计算机教室电脑", "学生电脑"],
        "qid": 63,
        "unit": "台",
        "name": "学校统一配备的学生用台式机数量",
        "related": [64, 65, 66],
    },
    # 教师笔记本电脑
    {
        "keys": ["教师笔记本", "老师笔记本", "教师用笔记本", "老师.*笔记本电脑"],
        "qid": 69,
        "unit": "台",
        "name": "学校统一配备的教师用笔记本电脑数量",
        "related": [67, 68, 70],
    },
    # 学生笔记本电脑
    {
        "keys": ["学生笔记本", "学生用笔记本"],
        "qid": 65,
        "unit": "台",
        "name": "学校统一配备的学生用笔记本数量",
        "related": [63, 64, 66],
    },
    # 教师移动终端
    {
        "keys": ["教师移动终端", "老师移动终端"],
        "qid": 68,
        "unit": "台",
        "name": "学校统一配备的教师用移动终端数量",
        "related": [67, 69, 70],
    },
    # 学生移动终端
    {
        "keys": ["学生移动终端"],
        "qid": 64,
        "unit": "台",
        "name": "学校统一配备的学生用移动终端数量",
        "related": [63, 65, 66],
    },
    # 网络带宽
    {
        "keys": ["带宽", "网速", "网络出口", "出口带宽", "网络总带宽", "校园网速", "mbps"],
        "qid": 60,
        "unit": "Mbps",
        "name": "学校网络出口总带宽",
        "related": [20],
    },
    # 无线网络覆盖
    {
        "keys": ["无线网络", "wifi", "wi-fi", "无线覆盖", "无线网"],
        "qid": 20,
        "unit": "",
        "name": "学校无线网络覆盖情况",
        "related": [60],
    },
    # 班级教室数与多媒体设备
    {
        "keys": ["交互式多媒体", "一体机", "电子白板", "多媒体设备教室", "多媒体教室数"],
        "qid": 62,
        "unit": "间",
        "name": "含有交互式多媒体设备的教室数",
        "related": [61, 17],
    },
    {
        "keys": ["班级教室数", "普通教室", "教室总数"],
        "qid": 61,
        "unit": "间",
        "name": "学校班级教室数",
        "related": [62],
    },
    {
        "keys": ["多媒体教室使用率", "多媒体使用率", "多媒体教室平均使用率"],
        "qid": 17,
        "unit": "%",
        "name": "多媒体教室平均使用率",
        "related": [62],
    },
    # 三个课堂
    {
        "keys": ["三个课堂", "专递课堂", "名师课堂", "名校网络课堂"],
        "qid": 5,
        "unit": "个",
        "name": "学校开展的三个课堂应用数量",
        "related": [6, 46, 47],
    },
    {
        "keys": ["专递课堂课时", "主讲教室课时", "专递课堂.*课时"],
        "qid": 46,
        "unit": "课时/周",
        "name": "专递课堂本学期平均每周主讲教室开课课时数",
        "related": [47, 5],
    },
    # 教师培训
    {
        "keys": ["校本培训", "信息化培训次数", "校本研修"],
        "qid": 41,
        "unit": "次",
        "name": "学校组织的信息化校本培训次数",
        "related": [42, 43, 56],
    },
    {
        "keys": ["培训覆盖率", "校本培训覆盖", "培训覆盖教师比例"],
        "qid": 43,
        "unit": "",
        "name": "学校组织的信息化校本培训覆盖教师比例",
        "related": [41, 56],
    },
    # 教师晒课
    {
        "keys": ["晒课", "一师一优课", "晒课数量", "云平台晒课"],
        "qid": 45,
        "unit": "堂",
        "name": "学校教师在教育云平台中晒课数量",
        "related": [1],
    },
    # 机考与在线考试
    {
        "keys": ["机考", "在线考试", "在线测评", "机考学科"],
        "qid": 49,
        "unit": "个",
        "name": "学校近一年已开展机考在线考试的学科数",
        "related": [50],
    },
    # 人工智能课程
    {
        "keys": ["人工智能课程", "ai课程", "人工智能课"],
        "qid": 13,
        "unit": "",
        "name": "学校人工智能课程开设形式",
        "related": [14],
    },
    # 一卡通
    {
        "keys": ["一卡通", "校园卡"],
        "qid": 25,
        "unit": "种",
        "name": "校园卡(含一卡通)已经实现的功能种数",
        "related": [26],
    },
    # 经费投入
    {
        "keys": ["经费投入", "信息化经费", "信息化投入占比", "经费占比"],
        "qid": 31,
        "unit": "%",
        "name": "信息化经费总计投入占同期教育总经费支出比例",
        "related": [32, 33, 34, 35, 36, 48],
    },
    # 信息技术教师
    {
        "keys": ["信息技术老师", "信息技术教师", "计算机老师", "微机老师"],
        "qid": 38,
        "unit": "人",
        "name": "学校信息技术课程教师人数",
        "related": [39, 80, 81],
    },
    # 数字化管理与专职技术人员
    {
        "keys": ["数字化工作人员", "数字化人员", "专任数字化", "网络管理员", "网管"],
        "qid": 80,
        "unit": "人",
        "name": "学校数字化工作部门专任工作人员总数",
        "related": [81, 38],
    },
    # 智能学习空间 / 智慧教室 / 录播教室
    {
        "keys": ["智能学习空间", "智慧教室", "录播教室", "创客教室", "录播室"],
        "qid": 18,
        "unit": "间",
        "name": "学校建有的智能学习空间总数",
        "related": [19, 53, 62],
    },
    # 校园安防监控
    {
        "keys": ["安防监控", "校园监控", "摄像头", "安全监控系统"],
        "qid": 27,
        "unit": "种",
        "name": "学校安全监控系统校园覆盖范围",
        "related": [55],
    },
    # 数字化管理规章制度
    {
        "keys": ["数字化制度", "规章制度", "管理办法", "管理制度"],
        "qid": 82,
        "unit": "",
        "name": "学校是否制定数字化工作和管理的相关制度规章",
        "related": [79, 80],
    },
    # 配套数字资源学科数
    {
        "keys": ["数字资源", "配套资源", "资源库", "教材配套"],
        "qid": 1,
        "unit": "个",
        "name": "拥有与教材完整配套数字教育资源的学科数量",
        "related": [9, 10],
    },
    # 教师教学常态化应用学科数
    {
        "keys": ["辅助课堂教学", "常态化应用", "信息技术教学"],
        "qid": 2,
        "unit": "个",
        "name": "利用信息技术辅助课堂教学实现常态化应用的学科数量",
        "related": [4, 59],
    },
    # 教师数字素养 / 开展教学教师比例
    {
        "keys": ["教师数字素养", "开展教学的教师比例", "开展教学的学科教师比例"],
        "qid": 4,
        "unit": "",
        "name": "能够利用信息技术开展教学的学科教师比例",
        "related": [56, 59],
    },
    # 资产类数字化管理 (第 83 题)
    {
        "keys": ["资产类.*数字化管理", "数字化管理的工作内容", "资产类教育数字化", "学校及资产类", "学校实现学校及资产类", "资产类.*工作内容"],
        "qid": 83,
        "unit": "项",
        "name": "学校实现学校及资产类教育数字化管理的工作内容",
        "related": [75, 76, 82],
    },
    # Q1: 配套数字资源学科数
    {
        "keys": ["教材完整配套", "完整配套数字教育资源", "配套数字教育资源", "教材配套数字资源", "配套数字教学资源"],
        "qid": 1,
        "unit": "个学科",
        "name": "学校拥有与教材完整配套数字教育资源的学科数量",
        "related": [9, 10],
    },
    # Q2: 辅助课堂教学常态化应用学科数
    {
        "keys": ["信息技术辅助课堂教学实现常态化应用", "辅助课堂教学实现常态化应用", "常态化应用的学科有多少个", "常态化应用的学科数"],
        "qid": 2,
        "unit": "个",
        "name": "利用信息技术辅助课堂教学实现常态化应用的学科数量",
        "related": [4, 59],
    },
    # Q9: 校本数字资源库建设模式
    {
        "keys": ["校本数字资源库.*建设模式", "数字资源库.*建设模式", "电子教学资料库.*建设办法", "校本数字资源库", "电子教学资料库", "资源库建设模式", "建库方式"],
        "qid": 9,
        "unit": "种",
        "name": "学校校本数字资源库建设模式有几个",
        "related": [1, 10],
    },
    # Q10: 数字教学资料渠道来源
    {
        "keys": ["数字教学资料.*渠道", "教学资料.*几类渠道", "数字教学资源来源", "教学资料主要从几类渠道"],
        "qid": 10,
        "unit": "类",
        "name": "学校数字教学资料来源种类",
        "related": [1, 9],
    },
    # Q24: 学校管理基础数据应用范围
    {
        "keys": ["管理基础数据被用在多少个方面", "管理基础数据.*应用", "学校管理基础数据", "管理基础数据.*方面"],
        "qid": 24,
        "unit": "个",
        "name": "已有学校管理基础数据应用方面数量",
        "related": [23, 25],
    },
    # Q25: 校园卡功能
    {
        "keys": ["校园卡已经实现的功能", "校园卡.*功能", "校园卡功能数量", "校园卡已实现"],
        "qid": 25,
        "unit": "种",
        "name": "校园卡已经实现的功能数量",
        "related": [24],
    },
    # Q28: 校领导数字化培训种类
    {
        "keys": ["校领导.*培训种类", "校级领导.*培训种类", "数字化培训种类"],
        "qid": 28,
        "unit": "类",
        "name": "学校组织校级领导参与数字化培训种类",
        "related": [29],
    },
    # Q29: 校领导数字化培训累计人次
    {
        "keys": ["校级领导.*培训.*人次", "校领导.*培训.*人次", "校领导参加数字化培训", "校级领导.*数字化培训.*人次", "校领导参与数字化培训的累计人次", "校领导参加数字化培训的总人次"],
        "qid": 29,
        "unit": "人次",
        "name": "学校校级领导参与数字化培训累计人次",
        "related": [28],
    },
    # Q44: 教师参加信息化教学比赛 / 采购途径
    {
        "keys": ["参加区县级及以上的信息技术教学比赛", "信息技术教学比赛", "课件评比", "教学比赛.*课件评比", "采购数字化教学资源", "采购教学资源渠道"],
        "qid": 44,
        "unit": "项",
        "name": "学校教师参加教学比赛或采购途径",
        "related": [41, 42],
    },
    # Q45: 教师微课数量 / 晒课数量
    {
        "keys": ["自主开发微课", "微课数量", "录了多少节微课", "提交展示课的登记数量", "教育云平台晒课", "展示课的登记数量", "微课数量上的差距"],
        "qid": 45,
        "unit": "门",
        "name": "学校教师在教育云平台晒课或自主开发微课数量",
        "related": [46, 5],
    },
    # Q46: 专递课堂主讲开课课时
    {
        "keys": ["专递课堂.*主讲.*课时", "专递课堂向外授课.*课时", "专递课堂主讲", "专递课堂本学期平均每周主讲", "专递课堂平均每周主讲多少课时", "专递课堂主讲教室每周平均开多少节课"],
        "qid": 46,
        "unit": "课时/周",
        "name": "专递课堂教学中本学期平均每周主讲教室开课课时数",
        "related": [5, 47],
    },
    # Q58: 学生参加信息素养实践活动
    {
        "keys": ["信息素养实践和展示活动", "信息素养实践.*人次", "学生参加信息素养实践", "信息素养实践"],
        "qid": 58,
        "unit": "人次",
        "name": "学生参加信息素养实践和展示活动人次",
        "related": [56, 57],
    },
    # Q59: 经常使用标准化分数呈现考试成绩的教师比例
    {
        "keys": ["达到常态化应用的学科教师比例", "老师已经把技术作为日常教学习惯", "标准化分数呈现考试成绩", "标准化分数呈现成绩", "经常使用标准化分数呈现考试成绩的教师比例", "采用标准化分数呈现考试结果的教师占比"],
        "qid": 59,
        "unit": "%",
        "name": "经常使用标准化分数呈现考试成绩的教师比例",
        "related": [4, 56],
    },
    # Q60: 网络出口总带宽
    {
        "keys": ["出口总带宽是多少mbps", "出口带宽总共有多大", "出口总带宽", "校园网络出口带宽"],
        "qid": 60,
        "unit": "Mbps",
        "name": "学校网络出口总带宽",
        "related": [20],
    },
    # Q65: 学生笔记本电脑
    {
        "keys": ["学校统一配备的学生用笔记本", "配给学生在校用的笔记本电脑", "学生用笔记本电脑", "学生笔记本电脑", "学生在校用的笔记本电脑"],
        "qid": 65,
        "unit": "台",
        "name": "学校统一配备的学生用笔记本电脑数量",
        "related": [63, 64, 66],
    },
]

# 复合计算指标定义（例如“生机比”、“师机比”、“终端总数”）
COMPOUND_METRICS: Dict[str, Dict[str, Any]] = {
    "生机比": {
        "name": "学生机生比（生均计算机台数）",
        "desc": "在校生总数与学生用各类计算机终端总数（台式+移动+笔记本+平板）的比值",
        "qids": [63, 64, 65, 66],
        "school_field": "student_num",
        "formula": "student_num / (Q63 + Q64 + Q65 + Q66)",
        "unit": "人/台",
    },
    "师机比": {
        "name": "教师机师比（师均计算机台数）",
        "desc": "专任教师用各类终端总数（台式+移动+笔记本+平板）与教师总数的比值",
        "qids": [67, 68, 69, 70],
        "school_field": "teacher_num",
        "formula": "(Q67 + Q68 + Q69 + Q70) / teacher_num",
        "unit": "台/人",
    },
    "学生终端总量": {
        "name": "学生用终端总数",
        "desc": "包括学生用台式机、移动终端、笔记本、平板电脑总和",
        "qids": [63, 64, 65, 66],
        "unit": "台",
    },
    "教师终端总量": {
        "name": "教师用终端总数",
        "desc": "包括教师用台式机、移动终端、笔记本电脑、平板电脑总和",
        "qids": [67, 68, 69, 70],
        "unit": "台",
    },
}

# 学校基础属性关键词映射
SCHOOL_FIELD_KEYWORDS: Dict[str, Tuple[str, str]] = {
    "教师人数": ("teacher_num", "人"),
    "老师人数": ("teacher_num", "人"),
    "专任教师": ("teacher_num", "人"),
    "学生人数": ("student_num", "人"),
    "在校生": ("student_num", "人"),
    "学生总数": ("student_num", "人"),
    "班级数": ("class_num", "个"),
    "班级总数": ("class_num", "个"),
    "城乡类型": ("school_area_type", ""),
    "办学性质": ("school_type", ""),
    "学段": ("school_type", ""),
}


def _search_indicator_fallback(text: str) -> Optional[Dict[str, Any]]:
    """若预置规则未命中，对 83 题题库进行语义分词倒排检索。"""
    try:
        from scripts.school_engine import connect
        conn = connect()
        # 提取有意义中文片段
        stop = {"学校", "老师", "学生", "多少", "具体", "配置", "建设", "情况", "水平", "怎么样", "如何", "数字化", "有无", "是否", "几个", "几种", "有没有"}
        tokens = [t for t in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text) if t not in stop]
        if not tokens:
            conn.close()
            return None
        rows = conn.execute("SELECT id, content, level1_name, level2_name FROM questions").fetchall()
        best_score = 0
        best_row = None
        for r in rows:
            c = (r["content"] or "") + " " + (r["level2_name"] or "")
            score = sum(len(t) * (2 if t in r["content"] else 1) for t in tokens if t in c)
            if score > best_score:
                best_score = score
                best_row = r
        conn.close()
        if best_row and best_score >= 4:
            c_text = best_row["content"] or ""
            unit = ""
            if any(u in c_text for u in ("多少种", "工作内容", "多少项", "功能", "措施", "模式", "应用")):
                unit = "项"
            elif any(u in c_text for u in ("多少人", "人数", "人次")):
                unit = "人"
            elif any(u in c_text for u in ("台数", "多少台", "平板", "电脑", "计算机")):
                unit = "台"
            elif "多少" in c_text:
                unit = "项"
            return {
                "type": "question",
                "qid": best_row["id"],
                "name": best_row["content"],
                "unit": unit,
                "related": [75, 76, 82] if best_row["id"] == 83 else [],
            }
    except Exception:
        pass
    return None


def match_indicator(text: str) -> Optional[Dict[str, Any]]:
    """从文本中检测用户询问的具体指标。

    返回值结构：
    - type: "question" | "question_set" | "compound" | "school_field"
    - qid: int (单题时)
    - qids: list (复合题时)
    - items: list (题项组时)
    - field: str (学校属性时)
    - name: str
    - unit: str
    - related: list
    """
    clean = text.lower().replace("？", "").replace("?", "").strip()

    # Explicit ids and exact catalogue phrases precede broad historical keywords.
    from packages.semantic_query.catalogue import extract_explicit_question_ids,question_metric,load_semantic_catalogue
    explicit=extract_explicit_question_ids(text)
    if len(explicit)==1 and explicit[0] in range(1,84):return question_metric(explicit[0])
    hits=[]
    for item in load_semantic_catalogue()['items']:
        phrases=[item.get('question'),item.get('summary'),*(item.get('aliases') or [])]
        hits.extend((len(v),item['id']) for v in phrases if isinstance(v,str) and len(v)>=4 and v.lower() in clean)
    if hits:
        longest=max(n for n,_ in hits);ids={i for n,i in hits if n==longest}
        if len(ids)==1:return question_metric(ids.pop())
    # 0. 优先检查双题/题项组合规则
    for p in PAIRED_ITEM_RULES:
        if re.search(p["pattern"], clean):
            return {
                "type": "question_set",
                "qids": p["qids"],
                "name": p["name"],
                "items": p["items"],
                "derived": p.get("derived"),
                "unit": p.get("unit", ""),
            }

    # 1. 检查复合指标
    for c_name, c_conf in COMPOUND_METRICS.items():
        if c_name in clean:
            return {"type": "compound", "compound_key": c_name, **c_conf}

    # 2. 检查基础属性
    for k, (f, u) in SCHOOL_FIELD_KEYWORDS.items():
        if k in clean:
            if k == "学段" and (
                any(p in clean for p in ("不纳入", "不包括", "不含", "排除", "其他学段", "小学样本", "初中样本", "高中样本", "学段样本"))
                or not any(w in clean for w in ("什么学段", "哪个学段", "属于学段", "学段是", "查询学段"))
            ):
                continue
            return {"type": "school_field", "field": f, "name": k, "unit": u}

    # 3. 检查预置规则（正则/关键词）
    for r in PRESET_RULES:
        for k in r["keys"]:
            if re.search(k, clean):
                return {
                    "type": "question",
                    "qid": r["qid"],
                    "name": question_metric(r["qid"])["name"],
                    "unit": question_metric(r["qid"])["unit"],
                    "related": r.get("related", []),
                }

    # 4. 泛化：若提到“平板电脑”但未区分师生，优先给教师平板，附带学生平板
    if "平板" in clean or "ipad" in clean:
        return {
            "type": "question",
            "qid": 70,
            "name": "学校统一配备的教师用平板电脑数量",
            "unit": "台",
            "related": [66, 67, 69],
        }

    # 5. 全库 83 题智能语义检索兜底
    fallback = _search_indicator_fallback(clean)
    if fallback:
        return fallback

    return None
