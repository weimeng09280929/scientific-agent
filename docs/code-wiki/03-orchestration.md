# 03 · 编排引擎（DAG + ExecutionContext）

代码位置：`core/orchestration/`。这是整个流水线的骨架，理解它就理解了 Agent 如何协作。

## 1. 四个核心抽象

| 抽象 | 文件 | 作用 |
| --- | --- | --- |
| `ExecutionContext` | `context.py` | 节点间**唯一**数据通道；支持快照/恢复 |
| `ContextKey<T>` | `context.py` | 强类型键，避免散落字符串 |
| `Node` 及子类 | `node.py` | 节点执行协议与四种节点类型 |
| `Graph` / `GraphRunner` | `graph.py` | 静态 DAG 定义 + 动态执行引擎 |

## 2. 节点类型

| 类型 | 基类 | 行为 | 当前实例 |
| --- | --- | --- | --- |
| Agent 节点 | `AgentNode` | 调用 LLM/工具，返回结构化输出 | 28 个 |
| 人工节点 | `HumanNode` | 返回 `PENDING_HUMAN`，等待用户响应 | 5 个：`topic_confirm`、`idea_discuss`、`method_review`、`experiment_review`、`revise` |
| 工具节点 | `ToolNode` | 调用本地/外部工具 | 2 个：`experiment_run`、`provenance_check` |
| 检查点 | `CheckpointNode`（`StageCheckpoint`） | 对 context 做快照，供回滚 | 6 个，每阶段一个 |

### 2.1 节点执行协议

`Node.run(ctx)`（`core/orchestration/node.py:123-147`）固定三步：

```python
input_obj = self._build_input(ctx)      # 1. 从 context 组装 Pydantic 输入
result = self._execute(input_obj, ctx)  # 2. 业务逻辑，返回 NodeResult
if result.status == SUCCESS and result.output is not None:
    self._apply_output(result.output, ctx)  # 3. 按 output_keys 写回 context
```

异常统一捕获为 `NodeResult(status=FAILED, error=...)`，不会让单个节点异常击穿整张图。

节点类需要声明：

```python
class MyAgent(AgentNode):
    node_type = "my_agent"           # 全局唯一身份
    task_type = "ideation_brainstorm" # LLM 路由键
    input_schema = MyInput
    output_schema = MyOutput
    output_keys = {"ideas": IDEATION_IDEA_IDS}  # 输出字段 -> ContextKey
```

### 2.2 `NodeResult` 与状态

| 状态 | 含义 | GraphRunner 处理 |
| --- | --- | --- |
| `SUCCESS` | 成功，且 output 已写回 | 记录历史，前进到后继 |
| `FAILED` | 失败 | 标记 failed，停止当前阶段（等待重试/回滚） |
| `SKIPPED` | 跳过 | 记录后前进 |
| `PENDING_HUMAN` | 等待人工 | 设置 `pending_human_node_id`，暂停 |
| `BLOCKED` | 用户请求回滚 | 回滚到最近检查点 |

`HumanRequest`（提示、选项、是否允许自由文本、附加上下文）与 `HumanResponse`（文本、选项、`continue/abort/rollback`、附加 context）是人工交互的传输对象。

## 3. ExecutionContext

### 3.1 读写 API

```python
ctx.set(RESEARCH_PAPER_IDS, ["p1", "p2"])
ids = ctx.get(RESEARCH_PAPER_IDS, [])
ctx.has(RESEARCH_TOPIC)
ctx.delete(SOME_KEY)
ctx.keys()
```

内部是 `dict[str, Any]`，键名由 `ContextKey.name` 决定。

### 3.2 三类键空间

| 前缀 | 含义 | 快照行为 |
| --- | --- | --- |
| `system.*` | 运行时依赖（LLM 注册表、知识库、Artifact 管理器等），含连接/客户端 | **只保留引用**，不深拷贝 |
| `checkpoint.*` | 检查点快照 | 不纳入新快照；恢复时保留当前值 |
| 其他（域键） | 业务数据 | 深拷贝，参与回滚 |

`snapshot()` / `restore()` 实现在 `core/orchestration/context.py:97-144`，这是「失败不整体重来」的基础。

### 3.3 系统依赖注入

由 `runtime/pipeline.py:start_project()` 注入：

| 键 | 类型 |
| --- | --- |
| `system.llm_registry` | `LLMRegistry` |
| `system.knowledge_store` | `KnowledgeStore` |
| `system.artifact_manager` | `ArtifactManager` |
| `system.provenance_validator` | `ProvenanceValidator` |
| `system.dry_run` | `bool` |
| `system.project_root` / `system.project_dir` | `Path` |

节点内通过 `stages/common.py` 的常量读取，例如 `ctx.get(KNOWLEDGE_STORE)`。

## 4. Graph 与校验

`Graph`（`core/orchestration/graph.py:35-127`）包含：

- `nodes: dict[node_id, Node]`
- `edges: list[(source, target)]`
- `entry_node` / `exit_node`（`add_node` 自动把第一个设为入口、最后一个设为出口）
- `stage`：生命周期阶段名

`validate()` 执行三项检查：

1. 入口/出口存在；
2. **无环**（Kahn 拓扑排序）；
3. 从入口可达所有节点（BFS）。

`successors()` / `predecessors()` 用于推进与静态数据流分析。

## 5. GraphRunner 执行引擎

### 5.1 状态

```python
GraphRunnerState(
  current_node_id, pending_human_node_id, last_checkpoint_node_id,
  completed_nodes: set, failed_nodes: set, aborted: bool
)
```

### 5.2 主循环 `_step()`（`graph.py:290-328`）

```
while current_node_id is not None:
    node = graph.nodes[current_node_id]
    notify_node_started(node_id, successors)     # UI 实时进度
    if isinstance(node, CheckpointNode):
        last_checkpoint_node_id = node_id
    result = node.run(ctx)
    SUCCESS      -> completed.add; record; _advance()
    PENDING_HUMAN-> pending_human_node_id = node_id; return（暂停）
    SKIPPED      -> record; _advance()
    FAILED       -> failed.add; record; _handle_failure(); return
```

`_advance()` 取后继：单后继直接前进；多后继时取第一个未完成的（复杂分支应由专门的决策节点处理，当前图均为线性）。

### 5.3 人工节点恢复

`resume_after_human(response)`（`graph.py:213-253`）：

1. 取出 `pending_human_node_id` 对应节点，调用 `continue_after_human`；
2. `SUCCESS` → 记录并继续 `_step()`；
3. `FAILED + abort` → 标记 `aborted`；
4. `FAILED`（非 abort）→ 进入失败处理；
5. `PENDING_HUMAN`（例如审核意见要求重写）→ 保持暂停，等待再次确认；
6. `BLOCKED` → `_rollback_to_checkpoint()` 后继续。

### 5.4 失败与回滚

- `_handle_failure()` 当前实现：**保留失败节点与已完成材料，停止推进**，回滚只由用户显式触发（`HumanResponse(action="rollback")`）。
- `_rollback_to_checkpoint()`：读取 `checkpoint.<last_checkpoint_node_id>` 快照 → `ctx.restore()` → 当前节点设为该检查点 → 清空失败/等待状态。
- 若没有检查点，直接 `aborted`。

### 5.5 断点续跑

`start_at(node_id)`（`graph.py:189-211`）：

- 校验节点属于当前图；
- 重建 `completed_nodes` 与最近检查点（从 `ctx.history()`）；
- 客户端依赖不落盘，恢复后重新执行检查点节点重建内存回滚点；
- 若 `node_id=None`，视为阶段末尾已完成。

`Pipeline.run_stage` 会根据 `ctx.history()` 的最后一条记录决定从哪个节点恢复。

## 6. 节点注册表

`core/orchestration/registry.py` 提供 `NodeRegistry` 与 `register_node_type` 装饰器，用于按名称查找节点类型/构造节点。当前阶段图主要直接实例化节点类，注册表更多用于扩展与测试。

## 7. 数据流完整性保障

`tests/test_node_architecture.py` 通过 AST 静态分析：

- 收集每个节点类源码中的 `ctx.get(CONST)` / `ctx.set(CONST)`；
- 与 `output_keys` 声明合并，构建生产者/消费者集合；
- 断言每个被消费的域键都有生产者，或属于入口注入白名单（`project.domain`、`research.topic`、`research.search_prefs`）；
- 断言业务 Agent/Tool 的产出键全局唯一。

这比运行时测试更强：**新增节点忘记声明 output_keys 会直接在单测失败**。

## 8. 新增节点的推荐步骤

1. 在 `stages/<stage>/io_schema.py` 定义 `XxxInput(NodeInput)` / `XxxOutput(NodeOutput)`；
2. 在 `agents.py` 实现类，声明 `node_type`（全局唯一）、`task_type`、`input_schema`、`output_schema`、`output_keys`；
3. 在 `graph.py` `add_node` + `add_edge`，保持线性或明确分支；
4. 若需要新的跨阶段数据，在 `stages/common.py` 添加 `ContextKey`（命名遵循 `<stage>.<field>`）；
5. 运行 `python -m pytest tests/test_node_architecture.py tests/test_stages_graphs.py tests/test_orchestration_*.py -q`；
6. 若节点数变化，同步更新测试中的 `EXPECTED_NODE_COUNTS` 与本文档。
