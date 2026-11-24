# 01 · 整体架构

## 1. 系统定位

项目包含两层能力，**不要混为一谈**：

| 层 | 入口 | 当前是否对网页用户开放 | 说明 |
| --- | --- | --- | --- |
| 科研方案工作台 | `web/api.py` + `core/workspace/` | 是 | 项目、对话、论文阅读、研究问题、方案生成/修订/采纳/导出、教学案例 |
| 多阶段 Agent 流水线 | `runtime/pipeline.py` + `stages/` | 受产品边界限制 | research → ideation → design → experiment → writing；另有两个旁路子流程 |

`core/product_boundary.py` 把产品上限固定为 **design**：流水线可以完成调研、思路与方案，实验与写作阶段在 `ExecutionPolicy.PRODUCT_PLAN` 下会被 `restricted` 拒绝（历史材料仍可读、可导出）。

## 2. 分层架构

```
┌──────────────────────────────────────────────────────────────────────┐
│ 表现层  web/static/*.html / *.js / *.css（原生前端，无构建）           │
├──────────────────────────────────────────────────────────────────────┤
│ 接口层  web/api.py（主应用 + 大量 /api 路由）                          │
│         web/workspace_api.py / conversation_api.py /                   │
│         conversation_home_api.py / conversation_move_api.py /          │
│         project_exports_api.py / research_task_api.py /                │
│         teaching_case_api.py / bounded_uploads.py                      │
├──────────────────────────────────────────────────────────────────────┤
│ 应用/领域层                                                            │
│   工作台：core/workspace/*（项目、对话、阅读、问题、方案、教学、引导）  │
│   流水线：runtime/pipeline.py + stages/*（阶段子图与节点）              │
├──────────────────────────────────────────────────────────────────────┤
│ 能力层  core/llm（Provider/任务路由） core/knowledge（SQLite/向量/检索）│
│         core/tools（文献/材料/代码执行） core/artifacts（版本/溯源）    │
│         core/orchestration（DAG/上下文/节点） core/state（生命周期）    │
├──────────────────────────────────────────────────────────────────────┤
│ 基础设施 core/config.py（路径/配置） core/local_accounts.py（账号）     │
│         core/local_backup.py（备份恢复） core/resource_limits.py（限流）│
└──────────────────────────────────────────────────────────────────────┘
```

## 3. 运行时组装（从进程启动到一次请求）

### 3.1 启动

1. `python -m tools.serve_local --mode demo|accounts` 或 `python -m web.api`。
2. `tools/serve_local.py` 的 `configure()` 写入环境变量：
   `SRA_AUTH_MODE`、`SRA_DATA_ROOT`、`SRA_PROJECTS_DIR`、`SRA_ACCOUNTS_DB`、`SRA_ALLOW_LOCAL_REGISTRATION`、`SRA_DRY_RUN`、`SRA_WEB_HOST/PORT`。
3. `web/api.py` 模块导入时创建 `FastAPI` 应用（`web/api.py:707`），注册：
   - `lifespan`：单实例锁、恢复会话移动日志、扫描已有项目；
   - 异常处理器（登录/注册校验错误不回显密码）；
   - 账号边界中间件、教学执行边界中间件、资源容量异常处理器；
   - 业务路由器（`web/api.py:4027-4035`）；
   - `/`、`/workspace`、`/static/{path}` 静态入口。
4. `uvicorn.run` 启动（`web/api.py:4288-4296`）：端口取 `SRA_WEB_PORT` → `$PORT` → `8001`；`local` 模式强制绑定回环地址。

### 3.2 一次典型请求（生成方案草案）

```
POST /api/projects/{pid}/workspace/guide
  → web/workspace_api.py
  → WorkspaceService.load()（读取 kv_store 中的 research_workspace.v1）
  → ConversationService.send()/generate_guidance()
  → core/workspace/guide.py:build_guide_context + generate_guidance
  → LLMRegistry.structured_output(task_type=...)
  → 写入消息/建议（SQLite 事务 + revision 校验）
  → 返回 GuideDraft 视图给前端
```

### 3.3 一次流水线阶段执行

```
runtime.Pipeline.run_stage(session, ctx, stage)
  → _preflight()（产品边界检查）
  → _build_stage_graph(stage)（stages/<stage>/graph.py）
  → GraphRunner.start()/start_at()
  → node.run(ctx): _build_input → _execute → _apply_output
  → 每个节点完成 save_context(ctx, KnowledgeStore)（可恢复）
  → 人工节点 PENDING_HUMAN → human_callback → resume_after_human
  → session.complete_stage()
```

## 4. 依赖方向

- `web → core/workspace → core/knowledge / core/llm / core/artifacts`
- `web → runtime/pipeline → stages → core/orchestration / core/knowledge / core/llm / core/artifacts / core/tools`
- `core/*` 之间**不反向依赖** `web` 或 `stages`。
- `stages/*` 之间不直接 import 其他阶段的 `agents`，只通过 `stages/common.py` 的 `ContextKey` 交换数据。
- `runtime/pipeline.py` 延迟导入各阶段 `graph`，避免循环依赖。

## 5. 全局设计约束

| 约束 | 实现位置 | 影响 |
| --- | --- | --- |
| 产品止于研究方案 | `core/product_boundary.py`、`Pipeline._preflight`、`run_stage` | experiment/writing 返回 `restricted`；网页不提供新增结果/写作入口 |
| dry_run 默认 | `core/config.py`（`SRA_DRY_RUN`）、`Pipeline.start_project` 注入 `DRY_RUN` | 节点可用占位数据走通架构；真实调用需显式关闭 |
| 单实例 | `web/api.py` 的 `_guard_single_instance`、`core/instance_state.py` | 同一数据目录只允许一个服务进程 |
| 账号隔离 | `core/local_accounts.py`、`web/api.py` 的 `_active_user/_check_owner` | 项目按 owner 校验；注册仅回环且显式开启 |
| 资源保护 | `core/resource_limits.py`、`web/bounded_uploads.py` | 并发/上传/字段/请求体有上限 |
| 节点唯一通道 | `core/orchestration/context.py` | 禁止节点间直接调用；便于快照回滚 |
| 可恢复 | `core/workflow_checkpoint.py`、`core/state/snapshot.py` | 节点历史与域数据落 SQLite，重启后可续跑 |
| 证据可追溯 | `core/workspace/evidence_refs.py`、`core/artifacts/provenance.py` | 引用必须能回到实际材料；Artifact 版本保留 |

## 6. 存储布局

```
<data_root>/
├── accounts.db                     # 账号库（accounts 模式）
├── projects/
│   └── <project_id>/
│       ├── knowledge.db            # 项目 SQLite：论文/知识/工作台/对话/任务/KV
│       ├── artifacts/              # Artifact 正文（按 artifact_group/version 分目录）
│       ├── snapshots/              # 项目阶段快照 index.json + <snapshot_id>.json
│       └── vectors/                # ChromaDB 持久化目录（可选）
```

`core/config.py` 的 `ProjectPaths` 统一生成这些路径，禁止业务代码硬编码。

## 7. 扩展点速览

| 想做什么 | 推荐改动点 |
| --- | --- |
| 新增一个流水线节点 | 在对应 `stages/<stage>/agents.py` 定义节点类 → 在 `graph.py` 注册节点与边 → 在 `io_schema.py` 声明输入输出 → 如需跨阶段数据，在 `stages/common.py` 加 `ContextKey` |
| 新增前端接口 | 在 `web/workspace_api.py` 等路由工厂加路由 → 调用 `WorkspaceService`/`ConversationService` → 在 `web/static/*.js` 接入 |
| 换模型/厂商 | 修改 `config/tasks.yaml` 的 `provider/model` → 在 `core/config.py` 与 `core/llm/registry.py` 补环境变量与 provider 注册 |
| 新增数据源 | 在 `core/tools/` 实现函数/客户端 → 在 `core/research_tasks/sources.py` 的 `SOURCE_FUNCS` 注册 → 更新 `core/tools/data_provenance.py` 披露信息 |
| 新增导出格式 | `web/api.py` 的 `_md_to_docx_response/_md_to_pdf_response` 或 `web/project_exports_api.py` |
| 调整产品边界 | 只改 `core/product_boundary.py` + `Pipeline` 的前置检查，不要散落在路由里 |
