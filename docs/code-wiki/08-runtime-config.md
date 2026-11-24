# 08 · 运行方式与配置

## 1. 环境准备

要求：**Python ≥ 3.10**、Git。前端无需构建。

```powershell
git clone <repo>
cd scientific-agent
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

运行测试还需：

```powershell
.\venv\Scripts\python.exe -m pip install pytest pytest-asyncio httpx
```

## 2. 启动模式

### 2.1 演示模式（离线可用，默认端口 8001）

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode demo --port 8001
```

- 数据目录：仓库根目录（读取 `projects/`）；
- 不调用真实模型/文献网络；
- 可体验两个预置案例（联邦学习、硫化物固态电解质）。

### 2.2 账号模式（默认端口 8077）

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode accounts --port 8077 --allow-registration
```

- 数据目录：`%LOCALAPPDATA%/ScientificAgent/account-workspace/`；
- 账号库：该目录下 `accounts.db`；
- 仅当显式 `--allow-registration` 才开放回环注册；
- `8001` 与仓库目录保留给 demo，账号模式使用其他端口/目录。

### 2.3 个人 demo 启用真实模型

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode demo --port 8125 `
  --data-root (Join-Path $env:LOCALAPPDATA 'ScientificAgent/live-demo') --allow-live-model
```

- `--allow-live-model` 会把 `SRA_DRY_RUN=false` 并加载 `.env`；
- 仅用于自用；费用与额度由模型服务管理；
- 文献网络需再显式 `--allow-live-sources`。

### 2.4 直接启动 FastAPI

```powershell
.\venv\Scripts\python.exe -m web.api
```

端口优先级：`SRA_WEB_PORT` → `$PORT` → `8001`；Host 取 `SRA_WEB_HOST`（默认 `127.0.0.1`）。`SRA_AUTH_MODE=local` 时 Host 必须是回环地址。

### 2.5 受控体验入口（验收/预算保护）

| 脚本 | 用途 | 关键参数 |
| --- | --- | --- |
| `tools/serve_budgeted_local.py` | 固定模型 `deepseek-v4.1-flash-1017` 的限额体验 | `--allow-live`、`--port`（默认 8077）、`--data-root` |
| `tools/serve_guided_experience.py` | 单轮模型调用守卫、费用/次数/字节/过期时间约束 | `--experience-id`、`--port`、`--model`、`--attempts`、`--cost-cny` 或 `--no-cost-cap`、`--expires-at` 等 |
| `tools/run_local_live_acceptance.py` | 真实调用验收 | `--allow-live`、`--output` |

这些入口用于内部验收，不建议作为日常开发入口。

## 3. 环境变量

### 3.1 运行与数据

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SRA_DRY_RUN` | `true` | `false/0/no/off` 才启用真实调用 |
| `SRA_DATA_ROOT` | 仓库根 | 数据根目录 |
| `SRA_PROJECTS_DIR` | `<root>/projects` | 项目目录 |
| `SRA_AUTH_MODE` | `local` | `local` / `accounts` |
| `SRA_ACCOUNTS_DB` | `<root>/accounts.db` | 账号库路径 |
| `SRA_ALLOW_LOCAL_REGISTRATION` | `0` | 仅回环且 accounts 模式生效 |
| `SRA_AUTH_INSECURE_HTTP` | 未设置 | 本地 HTTP 测试才设 `1` |
| `SRA_WEB_HOST` / `SRA_WEB_PORT` | `127.0.0.1` / `8001` | Web 绑定 |
| `SRA_TASKS_CONFIG_PATH` | `config/tasks.yaml` | 任务路由配置 |
| `SRA_SEED` | 未设置 | 全局随机种子（测试可复现） |

### 3.2 模型凭据

| 变量 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | 当前主路由（`tasks.yaml` 使用） |
| `OPENAI_API_KEY` | OpenAI 官方 / 兼容协议 / embedding |
| `ANTHROPIC_API_KEY` | Claude |
| `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | MiniMax / 兼容网关 |
| `MIMO_API_KEY` / `MIMO_BASE_URL` | 小米 MiMo |
| `SPARK_API_KEY` / `SPARK_BASE_URL` | 讯飞 Spark |
| `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` | 硅基流动 |
| `LOCAL_LLM_BASE_URL` | 本地 OpenAI 兼容服务 |

### 3.3 文献与解析（可选）

| 变量 | 用途 |
| --- | --- |
| `SCIVERSE_API_TOKEN` | Sciverse 检索 |
| `MINERU_API_KEY` / `MINERU_BASE_URL` | MinerU PDF 解析（云端，注意隐私） |
| `MATERIALS_PROJECT_API_KEY` | Materials Project |

### 3.4 资源保护

| 变量 | 含义 |
| --- | --- |
| `SRA_RESOURCE_HEAVY_USER` / `_GLOBAL` | 单用户/全局重任务并发 |
| `SRA_RESOURCE_RECEIVE_USER` / `_GLOBAL` | 上传接收并发 |
| `SRA_RESOURCE_FILE_BYTES` | 单文件上限 |
| `SRA_RESOURCE_BODY_BYTES` | 请求体上限 |
| `SRA_RESOURCE_FIELD_BYTES` | 表单字段上限 |
| `SRA_RESOURCE_HEADER_BYTES` | 请求头上限 |
| `SRA_RESOURCE_SPOOL_BYTES` / `_CHUNK_BYTES` | 流式落盘参数 |
| `SRA_RESOURCE_RECEIVE_SECONDS` | 接收超时 |

### 3.5 `.env` 使用

`runtime/cli.py:_load_env()` 会手动解析根目录 `.env`（不覆盖已存在的系统环境变量）。`tools.serve_local` 在 `--allow-live-model` 时调用它。模板见 `.env.example`。

## 4. 任务路由（`config/tasks.yaml`）

结构：

```yaml
tasks:
  <task_type>:
    provider: deepseek
    model: deepseek-v4.1-flash-1017
    temperature: 0.2
    max_tokens: 8192
    description: "..."
embedding:
  provider: openai
  model: text-embedding-3-small
  dim: 1536
```

- `task_type` 与节点里的 `AgentNode.task_type` 对应；
- 换厂商需要**同时**改 `provider/model` 与提供对应环境变量；
- `TaskRouter` 无状态，可全局复用；未配置的 `task_type` 会抛 `TaskNotFoundError`。

## 5. CLI（`runtime/cli.py`）

```powershell
# dry_run 全流程（自动确认人工节点）
python -m runtime.cli run --topic "联邦学习中的公平激励机制"

# 真实调用
python -m runtime.cli run --topic "..." --no-dry-run

# 在写作前停止
python -m runtime.cli run --topic "..." --stop-before writing

# 恢复
python -m runtime.cli resume --project-id proj_001

# 状态
python -m runtime.cli status --project-id proj_001
```

注意：

- 当前产品策略下 `Pipeline` 默认 `ExecutionPolicy.PRODUCT_PLAN`，会在 design 后停止；`force_writing` 被拒绝；
- `--no-dry-run` 需要先在 `.env` 配置对应模型密钥（启动提示仍写 MiniMax，以 `tasks.yaml` 为准）；
- `status` 通过 `core/product_boundary.inspect_boundary` 读取磁盘快照，不修改项目。

## 6. 账号与备份工具

### 6.1 账号

```powershell
python -m tools.local_accounts --db <账号库路径> init
python -m tools.local_accounts --db <账号库路径> create <email>
```

（具体子命令以 `--help` 为准。）

### 6.2 备份/校验/恢复

```powershell
python -m tools.local_backup backup  --root <数据目录> --mode accounts|demo --destination <备份目录> --confirm-stopped
python -m tools.local_backup verify  --backup <备份目录>
python -m tools.local_backup restore --backup <备份目录> --destination <新目录>
```

- 备份前需确认服务已停止；
- 恢复只允许到**新目录**，通过 `.sra-restore.json` 记录布局；
- 账号库与 demo 数据不能混用恢复（`tools/serve_local.configure` 会校验）。

## 7. 项目数据目录

| 路径 | 内容 |
| --- | --- |
| `projects/<id>/knowledge.db` | 项目 SQLite（所有结构化数据） |
| `projects/<id>/artifacts/` | Artifact 正文 |
| `projects/<id>/snapshots/` | 阶段快照 |
| `projects/<id>/vectors/` | 向量库（可选） |
| `output/` | 临时/验收输出（非产品必需） |
| `runtime/` | 运行时代码 |

`core/config.ProjectPaths` 是唯一路径来源；测试通常把 `SRA_PROJECTS_DIR` 指向临时目录。
