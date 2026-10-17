# EduTA 运行配置

普通启动使用 `python run_app.py`。模型凭据通过网页配置，存入本机 `output/secure_config/` 的用户绑定加密容器。请勿复制他人的加密容器；每个使用者自行配置模型。完整模型配置界面目前需要 Windows DPAPI。

| 环境变量 | 用途 |
|---|---|
| `TEACHER_EVAL_DATA_DB` | 自有基础 SQLite 数据库路径 |
| `TEACHER_EVAL_RESEARCH_CONFIG` | 可选三库数据适配配置路径 |
| `TEACHER_EVAL_QUERY_MODE` | 默认识别方式，`semantic` 或 `rule` |
| `TEACHER_EVAL_CONTRACT_DB` | 运行契约状态库输出路径 |
| `TEACHER_EVAL_CHARTS_DIR` | 图表输出目录 |
| `TEACHER_EVAL_CREDENTIAL_STORE_PATH` | 加密凭据容器的绝对路径 |

更名后继续使用原有的 `TEACHER_EVAL_*`、`TE_*` 环境变量、会话标识和凭据格式，已有部署可沿用原配置。

## 可选处理配置

论文中的 EduTA 核心方法对应 G1：模型解析和解释、对照原问题的程序校正、注册工具计算及数据卡片。运行代码还保留规则配置与几种可选检查策略，它们使用同一套前端、业务工具与数据适配器：

| 标识 | 行为 |
|---|---|
| G0 | 规则解析、确定性计算和规则正文 |
| G1 | EduTA 核心配置：模型解析、程序校正、确定性计算和模型解释 |
| G2 | 在模型路径上启用契约、执行和正文核验；必要时修复一次 |
| G2NR | G2 关闭答复修复 |
| G2W | 检查后保留正文并添加提醒，不调用修复 |

普通启动沿用现有智能语义模式及其检查流程。要选择表中的方法配置，需要指定模式、运行 ID、业务库和独立状态库；模型模式还需要绑定自己的本机配置 ID。G1 的模式值为 `g1_llm_unconstrained`，G2 为 `g2_llm_constrained`，G0 为 `g0_rule`。以下示例选择 G2 的提醒策略：

```powershell
$env:TEACHER_EVAL_EXPERIMENT_MODE_ENABLED = '1'
$env:TEACHER_EVAL_EXPERIMENT_MODE = 'g2_llm_constrained'
$env:TEACHER_EVAL_EXPERIMENT_RUN_ID = 'local-run'
$env:TEACHER_EVAL_DATA_DB = (Resolve-Path 'data/synthetic/education_digitization.sqlite').Path
$env:TEACHER_EVAL_CONTRACT_DB = Join-Path (Get-Location) 'output/local-run/state.sqlite'
$env:TEACHER_EVAL_EXPERIMENT_PROFILE_ID = 'your-local-profile-id'
# G2W:
$env:TE_DELIVERY_POLICY = 'warn'
python run_app.py
```

G2NR 使用 `TE_ABLATION_ANSWER_REPAIR=0`；G1 可用 `TE_ABLATION_REQUIREMENT_PRESERVATION=0` 关闭原问题驱动的校正。除非正在研究处理机制，保持默认校正开启。大模型语义 checker 默认关闭。

## 本机会话

服务仅监听 `127.0.0.1`。页面通过 `/api/v1/local/session` 获取会话 cookie 与 CSRF token，再访问业务 API。命令行调用 API 时也应保持相同的 cookie、Origin 和 `X-TeacherEval-CSRF`，无需关闭这些控制。
