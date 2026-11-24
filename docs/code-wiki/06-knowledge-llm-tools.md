# 06 · 能力层：LLM / 知识库 / 工具 / Artifact / 状态机

## 1. LLM 适配层（`core/llm/`）

### 1.1 抽象接口（`base.py`）

| 类型 | 作用 |
| --- | --- |
| `LLMProvider` | 抽象基类，必须实现 `complete`、`embed`、`structured_output` |
| `LLMRequest` / `LLMResponse` | 文本/对话请求与响应（含 token 用量） |
| `EmbeddingRequest` / `EmbeddingResponse` | 向量化 |
| `StructuredOutputRequest` | 带 `output_schema` 的结构化输出请求 |
| `LLMError` / `LLMStructureError` / `LLMTruncatedError` | 错误分类 |
| `strip_think_tags` | 剥离 `<think>...</think>` 推理块，防止污染正文 |

接口设计与厂商解耦：`stages` 中的 Agent 只声明 `task_type`，不直接依赖某个厂商 SDK。

### 1.2 任务路由（`task_router.py` + `config/tasks.yaml`）

```yaml
tasks:
  ideation_brainstorm:
    provider: deepseek
    model: deepseek-v4.1-flash-1017
    temperature: 0.7
    max_tokens: 8192
    description: "..."
embedding:
  provider: openai
  model: text-embedding-3-small
  dim: 1536
```

`TaskRouter.load(path)` 解析为 `TaskConfig` / `EmbeddingConfig`；`get(task_type)` 未命中抛 `TaskNotFoundError`。

### 1.3 注册表（`registry.py`）

`LLMRegistry.from_config(config)` 按环境变量懒注册 provider：

| provider 名 | 环境变量 | 说明 |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | 官方或兼容协议 |
| `local` | `LOCAL_LLM_BASE_URL` | 本地 OpenAI 兼容服务 |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude |
| `minimax` | `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` | 兼容 OpenAI |
| `mimo` | `MIMO_API_KEY` / `MIMO_BASE_URL` | 小米 MiMo |
| `deepseek` | `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` | 当前主路由 |
| `spark` | `SPARK_API_KEY` / `SPARK_BASE_URL` | 讯飞 |
| `siliconflow` | `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` | 硅基流动 |

统一调用：

```python
registry.complete("paper_metadata_extract", prompt=...)
registry.structured_output("design_claim_extract", output_schema=ClaimList, prompt=...)
registry.embed(["text1", "text2"])
```

内置容错：

- `complete`：检测 `finish_reason=length`，把 `max_tokens` 翻倍重试一次（上限 32768）；仍截断则抛 `LLMTruncatedError`；
- `structured_output`：首次返回 schema 定义/不符合 schema/非 JSON 时，追加强提示重试一次；截断时同样放大预算重试一次；
- 所有调用经 `core/invocation_audit.py` 记录审计；验收模式下可设置失败即停止。

### 1.4 dry_run

`SRA_DRY_RUN=true`（默认）时：

- `Pipeline` 把 `DRY_RUN` 注入 context；
- 节点读取该开关，使用占位数据而不是真实调用；
- 本地启动脚本还会把 `LLMRegistry.from_config` 与来源请求替换为「不可用」实现（`tools/serve_local.disable_live_calls`），确保没有隐式网络调用。

## 2. 知识库（`core/knowledge/`）

### 2.1 `KnowledgeStore`（`store.py`）

每个项目一个 SQLite 文件（`projects/<id>/knowledge.db`），线程安全（每线程独立连接 + 写锁）。核心表：

| 表 | 内容 |
| --- | --- |
| `papers` | `Paper` JSON（标题、作者、年份、摘要、DOI 等） |
| `paper_chunks` | 论文分块（`chunk_id`、`paper_id`、`text`、`page`） |
| `ideas` | 研究思路 |
| `claims` | 论断（带状态、来源 idea） |
| `experiments` | 实验 |
| `artifacts` | 产物元数据 + 内容（`artifact_group`、`version`、`artifact_type`） |
| `relations` | 实体关系 DAG |
| `evidence_log` | 检索证据链（子查询、来源、片段、偏移、分数） |
| `materials` / `material_properties` / `material_synthesis` | 领域知识三元组 |
| `research_gaps` | 结构化研究缺口 |
| `research_conflicts` | 文献冲突与处置 |
| `kv_store` | 通用键值：工作台、流水线上下文、报告等 |

设计权衡：不用 ORM；实体 JSON 存 `content` 字段，便于 schema 演化；关系单独表便于图查询。

### 2.2 Schema（`schema.py`）

核心枚举与模型：

- `EntityType`：Paper / Idea / Claim / Experiment / Artifact / Material / Property / Synthesis / Gap...
- `RelationType`：引用、派生、验证、支持/反驳等；
- `Paper`、`PaperChunk`、`Idea`、`Claim`（`ClaimStatus`）、`Experiment`（`ExperimentStatus`）、`Artifact`（`ArtifactType`）；
- 材料深度分析：`MaterialProfile`、`PropertyMechanism`、`TargetDecomposition`、`ComparisonCell`、`CandidateRanking` 等。

硬约束：Claim 进入写作前必须有 `evidence_refs`。

### 2.3 检索（`retriever.py` + `vector_store.py`）

`Retriever.search_papers(query, top_k, paper_id_filter)` 流程：

1. 用 embedding 函数把 query 向量化；
2. `VectorStore.query` 取 top-k（支持按 `paper_id` 过滤）；
3. 拼装 `PaperChunk`，关联 `Paper` / `Claim` / `Experiment`；
4. 返回 `RetrievalResult`。

`VectorStore` 是抽象基类；`ChromaVectorStore` 为默认实现（每个项目独立 collection，cosine 距离）。`chromadb` 缺失时构造会抛 `ImportError`，上层可选择不使用向量检索（关键词/结构检索仍可用）。

### 2.4 领域自适应（`domain_profile.py`）

`DOMAIN_PROFILES` 把「领域知识」外置为配置：

- 领域画像：研究对象、评价指标、方法/条件、抽取提示；
- `get_domain_profile(domain)` / `domain_label` / `build_domain_hint`；
- `build_extraction_system_prompt` 供材料抽取节点使用；
- `build_topic_recommend_hint` 供方向推荐使用。

同一套流水线因此可适配材料、化学、生命科学、AI、物理、能源等；空领域走 `GENERIC_PROFILE`。

其他知识模块：

| 文件 | 作用 |
| --- | --- |
| `normalize.py` | 实体/指标/单位归一化 |
| `material_analysis.py` | 材料深度分析（证据等级、机理、候选排序） |
| `innovation_playbook.py` | 创新点生成策略 |
| `reading_methodology.py` / `writing_methodology.py` | 阅读/写作方法论提示 |
| `paper_text.py` | 论文文本抽取辅助 |

## 3. 工具层（`core/tools/`）

`core/tools/__init__.py` 统一导出。按依赖分类：

| 工具 | 主要符号 | 外部依赖 | 需要 Key |
| --- | --- | --- | --- |
| arXiv 检索 | `search_arxiv`、`ArxivPaper` | arXiv API | 否 |
| Semantic Scholar | `search_semantic_scholar`、`S2Paper` | S2 API | 否（有限流） |
| OpenAlex | `search_openalex` | OpenAlex API | 否 |
| Crossref | `search_crossref` | Crossref API | 否 |
| Sciverse | `sciverse_agentic_search`、`sciverse_meta_search` | Sciverse | `SCIVERSE_API_TOKEN` |
| DOI/PDF 解析 | `resolve_doi_by_title`、`find_open_access_pdf`、`resolve_pdf_link` | 出版社/开放获取源 | 视来源 |
| MinerU 解析 | `parse_pdf_with_mineru`、`MinerUClient` | MinerU 云端 | `MINERU_API_KEY` |
| 期刊质量 | `lookup_journal_quality`、`enrich_paper_quality` | 内置数据 + 规则 | 否 |
| Materials Project | `mp_query_material`、`mp_cross_validate_discovery` | MP API | `MATERIALS_PROJECT_API_KEY` |
| OQMD | `query_oqmd_by_formula` | OQMD API | 否 |
| NOMAD | `query_nomad_by_formula`、`NOMADClient` | NOMAD API | 否 |
| 材料数据库缺口 | `detect_db_gaps`、`build_material_coverage` | 上述数据库 | 视数据库 |
| 构效搜索 | `MCTSSearcher`、`SurrogateModel`、`build_search_variables`、`build_literature_points`、`check_target_plausibility` | 本地计算 | 否 |
| 符号回归 | `run_symbolic_regression`、`SymbolicRegressor` | 本地计算（numpy 等） | 否 |
| 代码执行 | `run_python_code`、`run_python_code_remote`、`check_syntax` | 本地子进程/远程沙盒 | 视模式 |
| 文本切分 | `split_into_chunks` | 本地 | 否 |
| 数据披露 | `DATA_SOURCES`、`list_sources`、`data_sources_to_markdown` | 本地元数据 | 否 |
| HTTP | `core/tools/http.py` | requests | 否 |
| 搜索结果封装 | `core/tools/search_result.py` | — | — |

`core/research_tasks/sources.py` 把 arXiv/S2/OpenAlex/Crossref 注册进 `SOURCE_FUNCS`，并用 `SourceOutcome` 区分：

- `ok`：成功且有结果；
- `zero`：成功但无结果；
- `error`：网络/限流/解析失败（带 `error_class`、`retryable`、`retry_after_seconds`）。

这是产品里「成功无结果 / 来源失败 / 筛选排除 / 部分完成」能够被准确展示的基础。

## 4. Artifact 与溯源（`core/artifacts/`）

### 4.1 `ArtifactManager`（`version.py`）

| 方法 | 作用 |
| --- | --- |
| `create_artifact(...)` | 新建产物组与 v1 |
| `new_version(...)` | 在已有组上追加版本 |
| `list_artifacts(type)` | 按类型列出产物 |
| `get(id)` / `list_versions(group)` / `latest_version(group)` | 查询 |
| `read_content(artifact)` | 读取正文 |

`ArtifactContentStore` 负责正文文件：按 `artifact_group/version` 分目录，支持文本、二进制、导入文件。元数据在 SQLite `artifacts` 表，大内容在磁盘。

### 4.2 溯源（`provenance.py`）

- `ProvenanceChain` / `ProvenanceNode` / `ProvenanceEdge`：构建 Artifact → Claim → Paper/Experiment 的 DAG；
- `ProvenanceValidator`：校验链条无断链（例如未验证 Claim、未完成 Experiment 不能进入写作）；
- `provenance_check` 节点（writing 阶段）调用它做硬校验。

## 5. 状态机（`core/state/`）

### 5.1 生命周期（`lifecycle.py`）

```
not_started → in_progress → pending_review → done
                   ↓              ↓
                 blocked ←────────┘
blocked → in_progress
```

`LEGAL_STATUS_TRANSITIONS` 是唯一合法流转表；`TransitionValidator`（`transition.py`）校验阶段内子状态、前进到下一阶段、回滚到前序阶段。

### 5.2 `ProjectSession`（`session.py`）

| 方法 | 作用 |
| --- | --- |
| `create(project_id, paths)` | 初始化 5 个阶段为 `NOT_STARTED`，写初始快照 |
| `load(project_id, paths)` | 从最新快照恢复 |
| `current_stage()` | 当前活跃阶段 |
| `status_of(stage)` / `is_stage_done(stage)` | 查询 |
| `start_stage` / `mark_pending_review` / `complete_stage` / `mark_blocked` / `unblock` / `advance` / `rollback_to` | 流转 |

每次流转都会生成快照（`core/state/snapshot.py` 的 `SnapshotStore`），存于 `projects/<id>/snapshots/index.json` + `<snapshot_id>.json`。

### 5.3 执行上下文持久化（`core/workflow_checkpoint.py`）

`save_context(ctx, store, dry_run)` / `restore_context(ctx, store, dry_run)` 把域数据写入 `kv_store["pipeline.context.v1"]`，用于服务重启后恢复流水线进度。与 `snapshots/` 的区别：

| 存储 | 内容 | 用途 |
| --- | --- | --- |
| `snapshots/` | 阶段状态与流转历史 | 审计、回滚、状态查询 |
| `kv_store[pipeline.context.v1]` | 流水线域数据 + 节点历史 | 断点续跑、页面展示进度 |
