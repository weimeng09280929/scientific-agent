# 02 · 目录与模块职责

## 1. 顶层目录

| 路径 | 职责 | 关键入口 |
| --- | --- | --- |
| `web/` | FastAPI 应用、API 路由、原生前端静态资源 | `web/api.py:app` |
| `core/workspace/` | 工作台领域层：项目、对话、论文阅读、研究问题、方案、教学 | `WorkspaceService`、`ConversationService` |
| `core/orchestration/` | 自研 DAG 编排引擎 | `GraphRunner`、`ExecutionContext` |
| `core/llm/` | LLM Provider 抽象、注册表、任务路由 | `LLMRegistry.from_config` |
| `core/knowledge/` | SQLite 知识库、向量检索、领域画像、材料分析 | `KnowledgeStore`、`Retriever` |
| `core/tools/` | 外部数据源与本地计算工具 | `search_arxiv`、`MCTSSearcher`、`run_python_code` |
| `core/artifacts/` | 产物版本管理与溯源校验 | `ArtifactManager`、`ProvenanceValidator` |
| `core/state/` | 生命周期状态机与快照 | `ProjectSession`、`LifecycleStage` |
| `core/research_tasks/` | 对话内「研究任务」（检索/整理/报告）子服务 | `ResearchTaskService` |
| `stages/` | 六个阶段子图与节点实现 | `build_*_graph()` |
| `runtime/` | 端到端流水线编排、CLI | `Pipeline`、`runtime.cli` |
| `tools/` | 本地启动、账号、备份、验收等运维脚本 | `tools.serve_local` |
| `tests/` | pytest 回归 + Playwright 浏览器脚本 | `tests/run_all.py` |
| `content/teaching-cases/` | 离线教学案例数据包 | 由 `core/workspace/teaching.py` 读取 |
| `config/tasks.yaml` | LLM 任务路由配置 | `TaskRouter.load` |
| `docs/` | 产品/验证/流程文档（本 Code Wiki 在 `docs/code-wiki/`） | — |
| `projects/` | 默认演示数据目录（git 忽略） | `ProjectPaths.projects` |
| `hf-space-deploy/`、`hf-static/` | Hugging Face Space 部署辅助 | — |

## 2. `core/` 根级模块

| 文件 | 职责 |
| --- | --- |
| `config.py` | `ProjectPaths` / `LLMConfig` / `GlobalConfig`；环境变量覆盖；`get_config()` 单例 |
| `product_boundary.py` | 只读产品边界检查；`ExecutionPolicy`、`ProductExecutionRestricted` |
| `local_accounts.py` | 账号注册/登录/会话（argon2 密码哈希、SQLite 账号库） |
| `local_backup.py` | 本地备份/恢复校验 |
| `instance_state.py` | 单实例锁与运行状态 |
| `resource_limits.py` | 单进程并发/字节限额 |
| `workflow_checkpoint.py` | 流水线 context 的 KV 持久化与恢复 |
| `project_catalog.py` / `project_archive.py` | 项目列表、归档 |
| `research_scope.py` | 九类开放研究方向与领域归一化 |
| `invocation_audit.py` | 模型/网络调用审计与预算中断 |
| `experiment_results.py` | 实验结果签名与「是否有真实指标」判断 |
| `storage_paths.py` / `document_math.py` / `writing_report.py` | 路径辅助、文档公式、写作报告 |

## 3. `core/workspace/` 文件职责

| 文件 | 职责 | 关键符号 |
| --- | --- | --- |
| `service.py` | 工作台入口服务；compare-and-save 事务；正式记录的增删改与校验 | `WorkspaceService`、`RevisionConflict` |
| `schema.py` | 工作台 Pydantic 契约：背景、条目、消息、建议、会话、指南草稿 | `Workspace`、`WorkItem`、`Message`、`GuideDraft` |
| `conversations.py` | 多会话：会话 CRUD、消息分页、请求状态、重试、动作执行、建议采纳 | `ConversationService` |
| `conversation_schema.py` | 会话创建/补丁/模式/步骤的请求模型 | `ConversationCreate`、`GuidanceMode` |
| `conversation_lock.py` | 项目级文件锁，避免并发写同一会话 | `hold()` |
| `conversation_moves.py` | 会话在项目间移动、日志恢复 | `recover_journals` |
| `paper_reading.py` | 论文阅读目标解析、选段、引文/证据定位与校验 | `provided_paper`、`resolve_quote` |
| `reading_analysis.py` / `reading_coverage.py` | 阅读覆盖与依据分析 | — |
| `reading_session.py` | 阅读会话快照与选段解析 | `resolve_passage` |
| `read_model.py` | 单篇论文的展示读模型 | — |
| `plans.py` | 方案上下文构建、证据绑定、规则方案、完整性检查 | `build_plan_context`、`prepare_plan` |
| `plan_generation.py` | 方案生成调用封装 | — |
| `plan_schema.py` | `ResearchPlan` 结构化方案模型 | `ResearchPlan` |
| `guide.py` | 科研引导：系统提示、上下文组装、规则回退、模型生成 | `build_guide_context`、`generate_guidance` |
| `guidance_policy.py` | 指南输出的策略校验与裁剪（引用/操作/建议上限） | — |
| `research_coach.py` | 只读研究路线投影与讨论步骤 | `DISCUSSION_STEPS` |
| `method_evidence.py` | 方法证据块与条件校验 | — |
| `evidence_refs.py` | 证据引用编号与解析 | — |
| `materials.py` | 文献目录、指纹、来源变更标记 | `material_catalog`、`fingerprint` |
| `uploads.py` | 论文/主题上传准备与解析 | `_prepare_upload_content`（在 `web/api.py`） |
| `start.py` | 首次发送的身份预留（幂等） | `ConversationStarter` |
| `teaching.py` / `teaching_packages.py` | 离线教学案例、进度、步骤回执 | — |
| `workflow.py` | 研究主线唯一只读投影（问题→方案→结果→判断） | `analyze` |

## 4. `stages/` 结构

每个阶段固定四件套：

```
stages/<stage>/
├── agents.py      # 节点类实现（AgentNode/HumanNode/ToolNode 子类）
├── graph.py       # build_<stage>_graph()：注册节点与边，validate()
├── io_schema.py   # 节点 input_schema / output_schema（Pydantic）
└── __init__.py
```

`stages/common.py` 是唯一的跨阶段契约中心：系统依赖键、域键、`StageCheckpoint`。

## 5. 模块依赖矩阵（简表）

| 模块 | 依赖 | 被谁依赖 |
| --- | --- | --- |
| `core/config` | 标准库 | 几乎所有模块 |
| `core/orchestration` | pydantic | `stages`、`runtime` |
| `core/knowledge` | sqlite3、chromadb（可选）、pydantic | `stages`、`core/workspace`、`runtime`、`web` |
| `core/llm` | openai、anthropic、pyyaml | `stages`、`core/workspace`、`web` |
| `core/artifacts` | `core/knowledge` | `stages`、`runtime` |
| `core/tools` | requests、arxiv 等 | `stages`、`core/research_tasks` |
| `core/state` | 标准库 | `runtime` |
| `core/workspace` | `core/knowledge`、`core/llm`、`core/artifacts`（间接） | `web` |
| `core/research_tasks` | `core/tools`、`core/knowledge`、`core/workspace` | `web` |
| `runtime` | `stages`、`core/*` | `tools`、`web`（部分入口） |
| `web` | 全部应用层 | 前端 |

## 6. 值得注意的重复/历史模块

- `core/state/` 与 `core/workflow_checkpoint.py` 都做持久化：前者存阶段状态快照（`snapshots/`），后者存执行上下文（`kv_store` 的 `pipeline.context.v1`），用途不同。
- `stages/*/io_schema.py` 是节点 I/O 契约；`core/workspace/plan_schema.py` 是产品方案的业务模型，二者不要混用。
- `docs/node_architecture.md` 是历史设计说明（43 节点），代码当前为 41 节点，详见 [04-stages.md](04-stages.md)。
- 根目录存在大量历史日志、验收脚本与临时目录（`.pytest-*`、`*.log`、`smoke_*.py`），不属于运行必需文件。
