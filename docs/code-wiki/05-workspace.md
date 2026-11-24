# 05 · 工作台领域层（core/workspace）

这是网页产品的业务核心。它不依赖 `stages/`，而是围绕「项目 → 对话 → 材料 → 问题 → 方案」组织数据与交互。

## 1. 数据契约（`schema.py`）

所有模型继承 `Contract`（`extra="forbid"`、禁止 NaN/Inf），关键模型：

| 模型 | 作用 | 关键字段 |
| --- | --- | --- |
| `Workspace` | 单个项目的全部正式状态 | `project_id`、`revision`、`chat_revision`、`brief`、`brief_revision`、`focus`、`items`、`messages`、`suggestions` |
| `Brief` | 项目背景 | `goal`、`background`、`resources`、`time_budget`（各限长） |
| `ItemInput` / `WorkItem` | 正式记录（问题/方案/结果/判断/笔记/写作骨架） | `kind`、`title`、`content`、`paper_ids`、`related_ids`、`metrics`、`result_status`、`assessment`、`supersedes_id`、`plan` |
| `ResearchFocus` | 当前选中的问题与方案 | `question`、`plan` |
| `ResearchGap` / `GapSource` | 研究缺口与来源 | `kind`、`summary`、`why`、`uncertainty`、`sources` |
| `Suggestion` | 模型建议（未采纳前不是正式记录） | `suggestion_id`、`based_on_revision`、`gap_snapshot` |
| `Message` | 对话消息（含引用、建议、操作、阅读依据、请求状态） | `role`、`content`、`area`、`mode`、`cited_paper_ids`、`suggested_replies`、`actions`、`reading_citations`、`request_id`、`request_status` |
| `GuideDraft` / `GuideWireDraft` | 引导模型的结构化输出（对模型暴露的精简 schema 是 Wire 版本） | `reply`、`cited_paper_ids`、`questions`、`suggestions`、`research_gaps`、`reading_citations`、`suggested_replies`、`actions` |

`Area` 固定为：`overview`、`literature`、`question`、`study`、`writing`。
`ItemKind` 固定为：`note`、`question`、`plan`、`result`、`finding`、`outline`。

### 设计要点

- **建议 ≠ 正式记录**：模型输出先进入 `Suggestion`，用户明确采纳后才 `apply` 成 `WorkItem`。
- **依赖复核**：背景、问题、引用变化后，依赖它们的条目会置 `needs_review=True`（`save_brief`、`link_material` 等）。
- **版本替代**：`supersedes_id` 表达旧条目被新条目替代；`status=superseded`。
- **并发控制**：所有写操作带 `expected_revision`，不匹配抛 `RevisionConflict`。

## 2. `WorkspaceService`（`service.py`）

入口服务，构造参数为 `project_id` 与 `db_path`：

```python
WorkspaceService(project_id, db_path, initialize_store=True)
```

| 方法 | 作用 |
| --- | --- |
| `load(catalog=None, include_chat=True)` | 读取 `kv_store["research_workspace.v1"]`，校验 project_id，标记来源变化，按需合并旧聊天 |
| `catalog()` | 文献目录（`material_catalog`） |
| `workflow(workspace, execution)` | 调用 `build_workflow` 生成只读研究主线视图 |
| `save_focus(focus, expected_revision)` | 设置当前问题/方案，校验方案必须属于该问题 |
| `save_brief(brief, expected_revision)` | 保存项目背景；变更时把非笔记条目标记为需复核 |
| `validate_item(workspace, data)` | 校验条目类型、引用、关联、方案结构 |
| `add_item(data, expected_revision)` | 新增正式记录（可替代旧条目） |
| `link_material(...)` | 把材料关联到条目并处理复核 |
| `record_guide(...)` / `apply_suggestion(...)` | 记录引导结果 / 采纳建议 |
| `paper_summaries()` | 文献摘要视图 |
| `view(...)` | 组装页面读模型 |
| `export_markdown()` | 导出工作台 Markdown |

### 事务模型（`_mutate`，`service.py:102-137`）

```sql
BEGIN IMMEDIATE
  SELECT value FROM kv_store WHERE key='research_workspace.v1'
  -- Pydantic 解析
  -- 校验 revision == expected_revision
  -- change(workspace)
  -- workspace.revision += 1
  INSERT OR REPLACE INTO kv_store ...
```

- 使用 SQLite 写事务做 compare-and-save，跨线程/进程安全；
- **网络/模型调用期间不持锁**：先生成草案，再短事务写入；
- 冲突时返回 `RevisionConflict`，前端提示刷新后重新提交。

## 3. 多会话（`conversations.py`）

`ConversationService(project_id, db_path)` 管理项目内的多段对话：

| 方法 | 作用 |
| --- | --- |
| `create(request)` / `patch(cid, request)` | 新建/修改会话 |
| `list(limit, offset)` / `get(cid)` | 会话列表与详情 |
| `messages(cid, limit, after, before, tail)` | 分页拉取消息 |
| `suggestions(cid, ...)` | 拉取建议 |
| `send(cid, request, ...)` | 发送消息并生成回答（核心链路） |
| `retry(cid, rid, ...)` | 失败请求重试 |
| `restart(cid, rid, ...)` | 显式确认成本后重启请求 |
| `perform_action(...)` / `guidance_actions(...)` | 执行服务端绑定的操作 |
| `apply(cid, sid, ...)` | 采纳建议为正式记录 |
| `project_legacy(workspace)` | 兼容旧版单会话数据 |

配套模块：

- `conversation_lock.py`：项目级文件锁，串行化同一项目的写操作；
- `conversation_moves.py`：会话跨项目移动、journal 恢复（`recover_journals`）；
- `conversation_schema.py`：`ConversationCreate`、`ConversationPatch`、`GuidanceMode`、`EntryStage`、`DiscussionStep` 等请求模型。

### 一次发送的可靠性设计

- 用户消息先落库（`request_id` 幂等）；
- 请求状态可查询（`request_view`）：`pending/succeeded/failed/cancelled`；
- 迟到响应有 token 校验（`_finish` / `_fail`），避免覆盖新请求；
- 上传幂等与笔记草稿隔离在 `uploads.py`、`start.py` 中处理。

## 4. 论文阅读链路

| 环节 | 文件 / 函数 | 说明 |
| --- | --- | --- |
| 阅读意图解析 | `paper_reading.reading_query` | 从用户文本提取 intent、关键词、关注点、表格/章节信号 |
| 目标论文判定 | `paper_reading.single_paper_target` | 处理题名/DOI/指代/「只看这篇」，区分未匹配/多匹配 |
| 选段 | `paper_reading.provided_paper` | 组装实际提供给模型的选段、摘要、元数据 |
| 选段解析 | `reading_session.resolve_passage` | 校验历史快照/当前预览版本、位置与原文一致性 |
| 引文定位 | `paper_reading.resolve_quote` / `resolve_evidence` | 在材料中定位引用并给出偏移 |
| 阅读依据 | `paper_reading.reading_citations` | 从模型输出收集可核对的引用 |
| 覆盖度 | `reading_coverage.py` | 评估本轮阅读覆盖了哪些部分 |
| 分析 | `reading_analysis.py` | 阅读结果的结构化分析 |

核心原则：**模型只能引用本轮实际提供的材料**；无法定位的引用会被丢弃或标记，而不是静默接受。

## 5. 研究问题与方案

### 5.1 问题

- 用户通过 `ItemInput(kind="question")` 保存候选问题；
- 只有 `save_focus` 选中并采纳后，才成为当前研究问题；
- 关联的论文、Gap、条件变化会触发 `needs_review`。

### 5.2 方案（`plans.py` + `plan_generation.py` + `plan_schema.py`）

| 函数 | 作用 |
| --- | --- |
| `build_plan_context(...)` | 校验问题/版本/方法类型/关联论文，构建生成上下文 |
| `validate_plan(plan, context, text)` | 校验方案中的条件、依据与用户陈述 |
| `prepare_plan(draft, context, text)` | 把模型草案转成可保存的 `ResearchPlan` |
| `rule_plan(context)` | 无模型时的规则方案 |
| `bind_plan_sources(plan, context)` | 绑定来源（用户事实/文献证据） |
| `completeness(plan)` | 方案完整度检查 |
| `render_plan` / `render_plan_evidence` | 渲染正文与依据 |

方案类型覆盖：计算研究、理论研究、实验研究设计。方案有 `draft → revised → adopted` 的版本关系（由 `WorkItem.plan_version`、`supersedes_id` 表达），导出时取正式版本。

## 6. 科研引导（`guide.py` + `guidance_policy.py`）

```python
build_guide_system(...)      # 系统提示（角色、规则、禁止项）
build_guide_context(...)     # 组装本轮上下文：背景、问题、材料、阅读依据、步骤
rule_guidance(area, text, context)  # 无模型/规则模式回退
generate_guidance(...)       # 调用 LLM 生成 GuideDraft
```

- `GuideWireDraft` 只向模型暴露可选择的引用编号（`evidence_id`），不暴露完整 schema，降低伪造引用风险；
- `guidance_policy.py` 对输出做二次校验：引用必须存在、建议/操作数量受限、操作必须是服务端绑定的安全类型；
- 模型不可用时使用 `rule_guidance`，保证离线可用。

## 7. 研究主线只读视图（`workflow.py`）

`analyze(workspace, papers)` 产出唯一的只读投影：

```
问题(question) → 方案(plan) → 结果(result) → 判断(finding)
```

- `usable()` 递归检查条目是否 `active`、`needs_review=False`、引用的论文仍存在；
- 结果只有具备真实指标（`has_real_metrics`）或明确失败/不确定时才计入；
- 问题/方案/结果/判断的「已保存 / 待复核 / 缺失」状态由服务端统一给出，前端不自行推断。

## 8. 首次发送与教学案例

| 模块 | 作用 |
| --- | --- |
| `start.py` | `ConversationStarter`：首次发送的幂等身份预留，不运行模型/研究 |
| `teaching.py` | 离线教学案例的进度、步骤回执；复用既有会话与工作台 |
| `teaching_packages.py` | 读取 `content/teaching-cases/` 的版本化案例包 |
| `research_coach.py` | 讨论步骤定义（条件→探索→阅读→综合→问题→设计）与只读路线提示 |

教学案例的数据与真实项目隔离：进度存 `kv_store`，正式条目不因教学操作自动产生。

## 9. 与 `core/research_tasks` 的关系

`core/research_tasks/` 是「对话内研究任务」子服务（`ResearchTaskService`）：

- `sources.py`：arXiv / Semantic Scholar / OpenAlex / Crossref 来源注册与审计状态；
- `planner.py`、`detect.py`、`coverage.py`、`ranking.py`：任务规划、意图识别、覆盖度、排序；
- `synthesis.py`：报告综合；
- `report_sources.py`、`report_status.py`：报告来源与状态；
- `service.py`：任务生命周期（创建、执行、取消、重试、导出）。

它由 `web/research_task_api.py` 暴露，**独立于 `stages/research`**；后者是流水线内部的文献调研节点。

## 10. 常见改动位置

| 需求 | 位置 |
| --- | --- |
| 新增正式记录类型 | `schema.py` 的 `ItemKind` + `service.validate_item` + 前端 |
| 调整方案字段 | `plan_schema.py` + `plans.validate_plan` + `GuideWireDraft` schema |
| 改引导语气/规则 | `guide.build_guide_system`、`guidance_policy.py` |
| 改阅读选段策略 | `paper_reading.provided_paper`、`reading_coverage.py` |
| 新增会话能力 | `conversations.py` + `conversation_api.py` |
| 改教学案例 | `content/teaching-cases/` + `teaching.py` |
