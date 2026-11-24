# Scientific Agent · Code Wiki

> 本目录是面向开发者的代码级说明，覆盖项目整体架构、模块职责、关键类与函数、依赖关系、数据模型、运行方式、测试与部署。
>
> 文档以**当前代码**为准（生成时仓库版本：41 个流水线节点 / 6 个阶段子图）。`docs/node_architecture.md` 仍保留历史口径「43 节点」，两者差异见 [04-stages.md](04-stages.md#节点数量口径说明)。

## 文档导航

| 文档 | 内容 | 适合读者 |
| --- | --- | --- |
| [01-architecture.md](01-architecture.md) | 系统定位、分层架构、两条主线、运行时组装、全局设计约束 | 所有人，先读 |
| [02-module-map.md](02-module-map.md) | 目录结构、模块职责表、模块依赖矩阵 | 需要快速定位代码 |
| [03-orchestration.md](03-orchestration.md) | 节点抽象、ExecutionContext、GraphRunner、人工节点、检查点回滚 | 修改流水线/新增节点 |
| [04-stages.md](04-stages.md) | 六阶段 + 方向推荐 + 关系发现的节点清单、契约与数据流 | 理解科研流程 |
| [05-workspace.md](05-workspace.md) | 工作台领域层：项目/对话/论文阅读/研究问题/方案版本/教学案例 | 修改产品功能 |
| [06-knowledge-llm-tools.md](06-knowledge-llm-tools.md) | 知识库、向量检索、LLM 路由、工具层、Artifact 与状态机 | 接入模型/数据源 |
| [07-web-api.md](07-web-api.md) | FastAPI 应用、鉴权、路由分组、前端静态资源、请求链路 | 前后端联调 |
| [08-runtime-config.md](08-runtime-config.md) | 启动模式、环境变量、任务路由、数据目录、CLI | 部署与本地运行 |
| [09-testing-deployment.md](09-testing-deployment.md) | pytest 体系、浏览器测试、Docker/Render/HF Space | 测试与发布 |
| [10-reference.md](10-reference.md) | 关键类/函数速查、数据模型、存储位置、常见扩展点 | 查阅手册 |

## 项目一句话

Scientific Agent 是一个**理工科科研方案工作台**：用户从兴趣、论文或研究问题出发，在同一个项目中保存背景、与 AI 讨论论文、确认研究问题、生成并审阅研究方案（Markdown/Word/PDF 导出）。后端同时保留一套完整的「调研 → 思路 → 方案 → 实验 → 写作」多阶段 Agent 流水线，但**当前产品入口最多推进到 DESIGN（研究方案）**，实验与写作执行受服务端边界保护。

## 快速事实

| 项目 | 值 |
| --- | --- |
| 语言 / Python | Python ≥ 3.10（`pyproject.toml`） |
| Web 框架 | FastAPI + Uvicorn，原生 HTML/CSS/JS 前端（无构建步骤） |
| 数据持久化 | 每项目一个 SQLite（`knowledge.db`）+ 本地文件（artifacts/snapshots） |
| LLM 接入 | 统一 `LLMRegistry`，按 `config/tasks.yaml` 的任务路由选 provider/model |
| 默认模型 | `deepseek-v4.1-flash-1017`（`config/tasks.yaml`） |
| 编排方式 | 自研 DAG 编排：`Node` / `Graph` / `GraphRunner` / `ExecutionContext` |
| 本地演示 | `python -m tools.serve_local --mode demo --port 8001` |
| 账号版 | `python -m tools.serve_local --mode accounts --port 8077 --allow-registration` |
| 直接启动 | `python -m web.api`（端口 `SRA_WEB_PORT` 或 `$PORT`，默认 8001） |
| 测试 | `python -m pytest tests/ -q`；浏览器脚本为 Node + Playwright |
| 部署 | `Dockerfile`（HF Spaces）、`render.yaml`（Render Blueprint） |

## 两条主线（先建立心智模型）

```
主线 A：Web 工作台（产品主入口）
浏览器 → FastAPI(web/api.py) → WorkspaceService/ConversationService
      → KnowledgeStore(SQLite) + core/workspace 领域逻辑 + LLMRegistry

主线 B：多阶段 Agent 流水线（内部引擎）
runtime.Pipeline → 阶段子图(Graph) → GraphRunner → 节点(Agent/Human/Tool/Checkpoint)
                → ExecutionContext(唯一数据通道) → KnowledgeStore/ArtifactManager
```

两条线共享 `core/knowledge`、`core/llm`、`core/artifacts`，并通过项目目录（`projects/<project_id>/`）中的 SQLite 与文件系统衔接。日常网页使用主要走主线 A；主线 B 由 `runtime/cli.py`、内部验收脚本或显式入口驱动。

## 关键设计约束（读代码前必须知道）

1. **产品边界**：`core/product_boundary.py` 与 `Pipeline.run_stage` 双重限制「最多到 design」。历史 experiment/writing 材料可查看、导出，但不新增执行。
2. **dry_run 默认开启**：`SRA_DRY_RUN=true` 时节点使用占位数据，不做真实模型调用；本地启动脚本默认关闭模型与文献网络调用。
3. **单一通信通道**：节点之间不直接调用，只通过 `ExecutionContext` 读写强类型 `ContextKey`。
4. **人在环**：5 个人工节点 + 6 个检查点，支持阻塞、恢复、中止、回滚。
5. **账号隔离**：`SRA_AUTH_MODE=accounts` 下项目按用户归属校验；`local` 模式仅允许回环访问。
6. **不伪造科研事实**：方案/结论必须由用户确认；实验成功与否决定是否进入写作；引用必须来自实际提供的材料。

## 建议阅读顺序

- 想跑起来：先看 [08-runtime-config.md](08-runtime-config.md)，再看 [07-web-api.md](07-web-api.md)。
- 想改业务流程：先看 [03-orchestration.md](03-orchestration.md) 与 [04-stages.md](04-stages.md)。
- 想改页面/接口：先看 [05-workspace.md](05-workspace.md) 与 [07-web-api.md](07-web-api.md)。
- 想换模型/数据源：先看 [06-knowledge-llm-tools.md](06-knowledge-llm-tools.md)。
