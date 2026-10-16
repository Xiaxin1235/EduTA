# School Digitization Analytics

学校数字化评价分析系统：用自然语言查询学校和地区的数字化指标，生成可追溯的数据卡片、统计结果、图表与解释。

Natural-language analytics for school digitization assessment, with deterministic calculations, traceable evidence, downloadable charts, and configurable answer checking.

## 功能

- 学校、地区、年度、指标、筛选条件与多轮上下文解析。
- 指标目录绑定、原问题需求校正与选择性澄清。
- 学校评估、题项事实、地区统计、比较、排名、跨年分析。
- SQLite 只读查询和确定性聚合；原始值与归一化得分分开处理。
- 数据卡片、表格、图表预览及 PNG 下载。
- OpenAI 兼容模型接口；模型负责解析与解释，程序负责计算。
- 可选执行核验、正文检查、一次受限修复，或保留正文并加提醒。
- 保留教师评价模块和配置式指标模板。

## 快速启动

建议使用 Windows 和 Python 3.11 / 3.12。模型配置页使用 Windows 当前用户的 DPAPI 加密保存密钥；本仓库不提供任何模型账号或密钥。

```powershell
git clone https://github.com/Xiaxin1235/school-digitization-analytics.git
cd school-digitization-analytics
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/generate_synthetic_data.py
python run_app.py
```

生成器完全由代码构造虚构学校和作答，不读取任何外部数据库。生成的数据仅供运行演示，位于被 Git 忽略的 `data/` 目录；仓库不包含预生成数据文件。教师演示 CSV 可用 `python scripts/mock_transform.py` 另外生成，内容同样完全虚构。

浏览器默认打开 `http://127.0.0.1:8600`。默认是智能语义识别：请在“模型密钥”页输入自己的 API 根地址、模型名和密钥。未配置模型时，可以在对话台明确选择“规则词面识别”体验本地查询。程序不会自动用规则结果冒充模型结果。

合成数据示例问题：

```text
示例市2023年的数字化水平怎么样？
示例市示例区示例一小学2023年的数字化水平怎么样？
示例市示例区示例一小学2023年给老师配置了多少台平板电脑？
```

只启动服务：`python run_app.py serve --no-browser --port 8600`。服务仅监听本机，正常会话和 CSRF 防护保持启用。

## 接入自己的数据

基础数据库包含 `schools`、`questions` 和 `answers` 三张表，见 [数据接口](docs/DATA.md)。将数据库路径设为环境变量后启动：

```powershell
$env:TEACHER_EVAL_DATA_DB = (Resolve-Path 'path/to/your-database.sqlite').Path
python run_app.py
```

还保留了“原始事实快照 + 格式规范元数据 + 清洗映射”的三库联合读取能力。配置示例位于 `config/research_dataset.example.json`，不会默认启用。清洗数据库、映射、行政区登记表及其文件校验值由数据持有者自行提供；相对路径以配置文件所在目录为基准解析。

## 目录

| 目录 | 内容 |
|---|---|
| `apps/web`、`apps/api` | 完整交互页面与 FastAPI 服务 |
| `packages/semantic_query` | 模型解析、需求校正、任务编译和答复检查 |
| `packages/analysis_contract` | 执行契约、工具能力和独立核验 |
| `packages/agent` | 路由、报告、工具与绘图 |
| `packages/llm_gateway` | 模型网关 |
| `packages/core` | 数据适配、凭据与本机会话 |
| `evaluation_templates`、`contracts` | 指标定义与数据接口定义 |
| `scripts` | 运行引擎、数据接口校验和合成数据生成器 |

## 数据与运行边界

此公开发行版提供运行代码与指标定义。真实学校记录、作答数据、清洗成果、模型凭据、聊天记录、论文、实验题目、参考答案、评分结果和开发历史均不包含在仓库中。指标定义属于程序的业务配置，不包含学校观测值。

模型服务会接收当前请求所需的问题、上下文及工具事实。接入数据与选择模型服务时，由部署者确定可发送的内容。合成数据仅用于检查运行流程，不代表研究结果或真实学校情况。

高级运行配置见 [运行方式](docs/RUNNING.md)。本项目采用 [Apache License 2.0](LICENSE)。依赖包按各自许可证提供，参见 [第三方依赖](THIRD_PARTY_NOTICES.md)。
