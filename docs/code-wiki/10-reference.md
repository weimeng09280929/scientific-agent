# 10 · 速查手册（关键类/函数/模型）

## 1. 编排与流水线

| 符号 | 位置 | 说明 |
| --- | --- | --- |
| `ContextKey<T>` | `core/orchestration/context.py:20-33` | 强类型上下文键 |
| `ExecutionContext.get/set/has/delete` | `core/orchestration/context.py:57-70` | 节点唯一数据通道读写 |
| `ExecutionContext.snapshot/restore` | `core/orchestration/context.py:97-144` | 快照与恢复（区分 system/checkpoint/域键） |
| `Node.run` | `core/orchestration/node.py:123-147` | 固定 `_build_input → _execute → _apply_output` |
| `AgentNode` / `HumanNode` / `ToolNode` / `CheckpointNode` | `core/orchestration/node.py:173-300` | 四种节点类型 |
| `HumanNode.continue_after_human` | `core/orchestration/node.py:231-260` | 处理 continue/abort/rollback |
| `Graph.add_node/add_edge/validate` | `core/orchestration/graph.py:52-127` | DAG 定义与校验（无环/可达） |
| `GraphRunner.start/start_at` | `core/orchestration/graph.py:183-211` | 首次执行 / 断点续跑 |
| `GraphRunner.resume_after_human` | `core/orchestration/graph.py:213-253` | 人工响应后继续 |
| `GraphRunner._step` | `core/orchestration/graph.py:290-328` | 主执行循环 |
| `GraphRunner._rollback_to_checkpoint` | `core/orchestration/graph.py:355-371` | 显式回滚 |
| `StageCheckpoint` | `stages/common.py:138-147` | 阶段检查点基类 |
| `Pipeline.start_project` | `runtime/pipeline.py:146-203` | 创建 session/context 并注入依赖 |
| `Pipeline.resume_project` | `runtime/pipeline.py:205-287` | 恢复项目与域数据 |
| `Pipeline.run_stage` | `runtime/pipeline.py:484-613` | 单阶段执行与人工阻塞 |
| `Pipeline.run_pipeline` | `runtime/pipeline.py:617-845` | 五阶段主流程、边界、实验后决策 |
| `Pipeline.run_discovery` | `runtime/pipeline.py:848-968` | 关系发现子流程 |
| `Pipeline.run_topic_discovery` | `runtime/pipeline.py:972-...` | 方向推荐子流程 |
| `build_*_graph()` | `stages/*/graph.py` | 六个阶段图构建 |
| 阶段节点数 | `tests/test_node_architecture.py:56-63` | research 10 / ideation 5 / design 6 / experiment 7 / writing 7 / discovery 6 |

## 2. 工作台

| 符号 | 位置 | 说明 |
| --- | --- | --- |
| `WorkspaceService` | `core/workspace/service.py:59` | 工作台入口服务 |
| `WorkspaceService.load` | `core/workspace/service.py:70-88` | 读取并校验工作台 |
| `WorkspaceService._mutate` | `core/workspace/service.py:102-137` | compare-and-save 事务 |
| `WorkspaceService.save_focus` | `core/workspace/service.py:139-155` | 设置当前问题/方案 |
| `WorkspaceService.save_brief` | `core/workspace/service.py:157-166` | 保存背景并标记复核 |
| `WorkspaceService.validate_item` | `core/workspace/service.py:168+` | 条目校验 |
| `WorkspaceService.add_item` | `core/workspace/service.py:316+` | 新增正式记录 |
| `WorkspaceService.export_markdown` | `core/workspace/service.py:591+` | 导出 |
| `Workspace` / `WorkItem` / `Message` / `GuideDraft` | `core/workspace/schema.py:299-312 / 57-64 / 268-296 / 159-201` | 工作台契约 |
| `ConversationService` | `core/workspace/conversations.py:127` | 多会话服务 |
| `ConversationService.send` | `core/workspace/conversations.py:1177+` | 发送与生成 |
| `ConversationService.apply` | `core/workspace/conversations.py:1720+` | 采纳建议 |
| `reading_query` | `core/workspace/paper_reading.py:95` | 阅读意图解析 |
| `single_paper_target` | `core/workspace/paper_reading.py:177` | 单篇目标判定 |
| `provided_paper` | `core/workspace/paper_reading.py:305+` | 组装阅读材料 |
| `resolve_passage` | `core/workspace/reading_session.py:101-138` | 选段校验 |
| `build_plan_context` | `core/workspace/plans.py:119-218` | 方案生成前置 |
| `validate_plan` | `core/workspace/plans.py:293-407` | 方案校验 |
| `prepare_plan` | `core/workspace/plans.py:541+` | 草案转方案 |
| `bind_plan_sources` | `core/workspace/plans.py:494+` | 绑定来源 |
| `build_guide_context` / `generate_guidance` | `core/workspace/guide.py:149 / 431` | 引导上下文与生成 |
| `rule_guidance` | `core/workspace/guide.py:328` | 无模型回退 |
| `workflow.analyze` | `core/workspace/workflow.py:11+` | 研究主线只读投影 |
| `ConversationStarter` | `core/workspace/start.py` | 首次发送幂等预留 |
| `ConversationService`（教学） | `core/workspace/teaching.py` | 教学进度与步骤 |

## 3. 知识 / LLM / 工具 / Artifact

| 符号 | 位置 | 说明 |
| --- | --- | --- |
| `LLMProvider` | `core/llm/base.py:138-171` | `complete/embed/structured_output` 抽象 |
| `LLMRequest` / `LLMResponse` | `core/llm/base.py:60-91` | 请求/响应 |
| `LLMRegistry.from_config` | `core/llm/registry.py:56-166` | 按环境变量注册 provider |
| `LLMRegistry.complete` | `core/llm/registry.py:170-218` | 文本补全 + 截断重试 |
| `LLMRegistry.structured_output` | `core/llm/registry.py:220-321` | 结构化输出 + 一次修复重试 |
| `TaskRouter.load/get` | `core/llm/task_router.py:53-87` | 任务路由 |
| `KnowledgeStore` | `core/knowledge/store.py:189` | SQLite 知识库 |
| 表结构 `_SCHEMA_SQL` | `core/knowledge/store.py:56-186` | papers/chunks/ideas/claims/experiments/artifacts/relations/evidence/materials/gaps/conflicts/kv |
| `Retriever.search_papers` | `core/knowledge/retriever.py:43-126` | 语义检索 |
| `VectorStore` / `ChromaVectorStore` | `core/knowledge/vector_store.py:47-155` | 向量存储抽象与 Chroma 实现 |
| `get_domain_profile` | `core/knowledge/domain_profile.py` | 领域画像 |
| `ArtifactManager` | `core/artifacts/version.py:99+` | 产物创建/版本/读取 |
| `ArtifactContentStore` | `core/artifacts/version.py:37+` | 正文文件存储 |
| `ProvenanceValidator` | `core/artifacts/provenance.py` | 溯源链校验 |
| `run_python_code` | `core/tools/code_runner.py` | 沙盒代码执行 |
| `MCTSSearcher` | `core/tools/materials_search.py` | 构效搜索 |
| `run_symbolic_regression` | `core/tools/symbolic_regression.py` | 符号回归 |
| `search_arxiv` / `search_semantic_scholar` / `search_openalex` / `search_crossref` | `core/tools/*` | 文献源 |
| `SOURCE_FUNCS` / `SourceOutcome` | `core/research_tasks/sources.py` | 来源注册与审计状态 |

## 4. 状态与边界

| 符号 | 位置 | 说明 |
| --- | --- | --- |
| `LifecycleStage` | `core/state/lifecycle.py:20-56` | 五阶段枚举与顺序/回滚规则 |
| `StageStatus` / `LEGAL_STATUS_TRANSITIONS` | `core/state/lifecycle.py:59-76` | 子状态合法流转 |
| `ProjectSession` | `core/state/session.py:40` | 阶段状态入口 |
| `ProjectSession.create/load` | `core/state/session.py:49-94` | 创建/恢复 |
| `TransitionValidator` | `core/state/transition.py:37-131` | 流转校验 |
| `SnapshotStore` | `core/state/snapshot.py` | 快照存储 |
| `save_context` / `restore_context` | `core/workflow_checkpoint.py` | 流水线上下文持久化 |
| `ExecutionPolicy` | `core/product_boundary.py:12-18` | `PRODUCT_PLAN` / `INTERNAL_LEGACY` |
| `inspect_boundary` | `core/product_boundary.py:32-92` | 只读边界检查 |
| `ProductExecutionRestricted` | `core/product_boundary.py:107-111` | 边界异常 |
| `ProjectPaths` / `GlobalConfig` | `core/config.py:20-63 / 117-161` | 路径与全局配置 |
| `AccountStore` | `core/local_accounts.py` | 账号库 |
| `RESOURCES` | `core/resource_limits.py` | 并发/字节限额 |

## 5. Web

| 符号 | 位置 | 说明 |
| --- | --- | --- |
| `app` | `web/api.py:707` | FastAPI 应用 |
| `_instance_lifespan` | `web/api.py:693-704` | 单实例、恢复、扫描 |
| `_active_user` / `_check_owner` | `web/api.py:314-331` | 账号与归属 |
| `_run_pipeline_thread` | `web/api.py:503+` | 后台流水线 |
| `_make_progress_callback` | `web/api.py:468+` | 节点进度回调 |
| 路由注册 | `web/api.py:4027-4035` | 四个子路由 |
| `build_workspace_router` | `web/workspace_api.py:84` | `/api/projects` 工作台 |
| `build_home_router` | `web/conversation_home_api.py:36` | `/api` 首页 |
| `build_exports_router` | `web/project_exports_api.py:41` | 导出 |
| `build_move_router` | `web/conversation_move_api.py:25` | 会话移动 |
| `static_files` | `web/api.py:4266-4283` | 静态文件安全入口 |
| `__main__` | `web/api.py:4288-4296` | 端口/Host 选择 |

## 6. 数据模型速查

### 6.1 项目状态

```text
Workspace
├── brief: Brief
├── focus: ResearchFocus(question, plan)
├── items: WorkItem[]        # note/question/plan/result/finding/outline
├── messages: Message[]      # 旧版兼容
├── suggestions: Suggestion[]
└── revision / chat_revision / brief_revision
```

### 6.2 知识实体

```text
Paper ──< PaperChunk
Idea ──> Claim ──> Artifact
         │
         └──> Experiment ──> Artifact
Relation(source_id, type, target_id)
Material ──< MaterialProperty / MaterialSynthesis
ResearchGap / ResearchConflict
```

### 6.3 流水线域键（节选）

| 阶段 | 键 |
| --- | --- |
| research | `research.topic`、`research.keywords`、`research.subqueries`、`research.paper_ids`、`research.evidence_chain`、`research.material_knowledge`、`research.cross_validation_report`、`research.gap_report` |
| ideation | `ideation.idea_ids`、`ideation.discussion_notes`、`ideation.validated_idea_ids`、`ideation.draft_claim_ids` |
| design | `design.atom_concepts`、`design.formula_code_map`、`design.method_content`、`design.claim_ids`、`design.method_artifact_id` |
| experiment | `experiment.configs`、`experiment.code`、`experiment.ids`、`experiment.anomaly_report`、`experiment.result_artifact_ids`、`experiment.outcome` |
| writing | `writing.style_profile`、`writing.outline`、`writing.sections`、`writing.draft_content`、`writing.review_notes`、`writing.paper_draft_artifact_id` |
| discovery | `discovery.hypotheses`、`discovery.search_space`、`discovery.candidates`、`discovery.relationships`、`discovery.report_artifact_id` |
| topic_discovery | `topic_discovery.interest`、`topic_discovery.trends`、`topic_discovery.analysis`、`topic_discovery.recommendations`、`topic_discovery.selected_topic` |
| 系统 | `system.llm_registry`、`system.knowledge_store`、`system.artifact_manager`、`system.provenance_validator`、`system.dry_run`、`system.project_root`、`system.project_dir` |

## 7. 常见扩展点

| 目标 | 步骤 |
| --- | --- |
| 新增阶段节点 | `io_schema.py` 定义 I/O → `agents.py` 实现类（唯一 `node_type`、`task_type`、`output_keys`）→ `graph.py` 连边 → `stages/common.py` 加键 → 跑架构测试 |
| 新增 API | 在对应 `web/*_api.py` 的 `build_*_router()` 添加 → 调 `WorkspaceService`/`ConversationService` → 前端 `fetch` 接入 |
| 换模型 | 改 `config/tasks.yaml` 的 `provider/model` → 在 `core/llm/registry.py` 注册 provider → 配环境变量 |
| 新增文献源 | `core/tools/new_source.py` → `core/research_tasks/sources.py:SOURCE_FUNCS` → `core/tools/data_provenance.py` 披露 |
| 新增导出格式 | `web/api.py` 的 `_md_to_*_response` 或 `web/project_exports_api.py` |
| 调整产品边界 | `core/product_boundary.py` + `Pipeline` 前置检查（不要散落到路由） |
| 新增领域画像 | `core/knowledge/domain_profile.py:DOMAIN_PROFILES` + `core/research_scope.py` |

## 8. 术语表

| 术语 | 含义 |
| --- | --- |
| DRY RUN | 不调用真实模型，用占位数据走通流程 |
| ContextKey | 强类型上下文键，节点间通信契约 |
| Checkpoint | 阶段检查点，节点执行前对上下文快照 |
| Human Gate | 人工决策节点（确认检索/讨论思路/审核方法/审核实验/确认终稿） |
| Claim | 可验证的论断，必须关联证据 |
| Gap | 研究缺口，research 阶段出口，供 ideation/discovery 使用 |
| Artifact | 版本化产物（方法文档、报告、稿件等） |
| Provenance | 从产物回溯到 Claim/Paper/Experiment 的证据链 |
| Product Boundary | 产品上限（design），服务端强制 |
| Scope（研究领域） | 材料/化学/AI/物理等，只影响提示语义，不影响流程结构 |
