# 数据接口

业务数据库只读打开。系统会另外保存自己的对话契约状态、生成图表和本机加密模型配置；这些文件属于运行输出，不写回业务数据。

## 基础 SQLite 接口

```sql
CREATE TABLE questions (
    id INTEGER PRIMARY KEY,
    content TEXT,
    level3_weight REAL,
    level1_name TEXT,
    level1_description TEXT,
    level1_weight REAL,
    level2_name TEXT,
    level2_description TEXT,
    level2_weight REAL
);
CREATE TABLE schools (
    id INTEGER PRIMARY KEY,
    province TEXT, city TEXT, district TEXT, school_name TEXT,
    year TEXT, school_type TEXT, school_area_type TEXT,
    class_num TEXT, teacher_num TEXT, student_num TEXT,
    data_mark INTEGER, region TEXT
);
CREATE TABLE answers (
    school_id INTEGER,
    question_id INTEGER,
    value REAL,
    normalized_value REAL
);
CREATE INDEX answers_school_id ON answers(school_id);
CREATE INDEX answers_question_id ON answers(question_id);
```

`schools.id` 表示学校年度观测，不能直接当成跨年的学校身份编码。`answers.school_id` 与之关联。`value` 为题项原始数值，`normalized_value` 为归一化值；缺失值使用 SQL NULL，有效的零值保留。题号和指标语义必须与 `evaluation_templates/` 中的定义一致。不同聚合口径不能相互替代。

## 可选清洗映射适配

复制 `config/research_dataset.example.json` 到自己的私有配置位置，填写路径和 SHA-256。设置 `TEACHER_EVAL_RESEARCH_CONFIG` 指向该文件。只有配置文件存在时，系统才使用三库适配器：

| 配置键 | 内容 |
|---|---|
| `source` | 含完整事实的基础快照 |
| `base_v1` | `school_records`：与 schools 对应的字段，以及 `normalized_province`、`normalized_city`、`normalized_district` |
| `mapping_v5` | 元数据映射、格式扩展及待核状态 |
| `registry` | 行政区登记配置，可使用绝对路径或相对配置文件的路径 |

映射数据库需要以下表，未应用的映射可使用空表：

```sql
CREATE TABLE record_metadata (
    source_school_year_id INTEGER, year TEXT,
    normalized_province TEXT, normalized_city TEXT, normalized_district TEXT,
    PRIMARY KEY (source_school_year_id, year)
);
CREATE TABLE format_extension_v5 (
    source_school_year_id INTEGER, year TEXT, new_value TEXT, rule_id TEXT,
    PRIMARY KEY (source_school_year_id, year)
);
CREATE TABLE research_review_v5 (
    source_school_year_id INTEGER, year TEXT, status TEXT,
    PRIMARY KEY (source_school_year_id, year)
);
```

映射按年度 ID 与年度关联。源字段不会被覆盖；统一视图同时保留规范值与原始值。行政区登记配置必须覆盖所用年份；没有登记的地区通过数据库元数据解析，歧义不会根据学校名称猜定。

可以执行 `python scripts/generate_synthetic_data.py --with-overlay` 生成一套完全虚构的三库样例，并按脚本输出的提示设置配置路径。该样例不包含真实清洗结果。

## 教师模块

教师模块读取 `data/demo/` 内的 CSV，字段接口定义位于 `contracts/data_contract_v0.1.json`。`python scripts/mock_transform.py` 只生成虚构记录，方便体验教师查询与评估。实际数据可以按同一字段接口自行准备。
