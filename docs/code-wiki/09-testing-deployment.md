# 09 · 测试与部署

## 1. 测试体系

### 1.1 配置

`pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
```

`tests/conftest.py` 提供基于 `tmp_path` 的隔离 fixtures：

| fixture | 作用 |
| --- | --- |
| `tmp_project_paths` | 临时 `ProjectPaths` |
| `knowledge_store` | 临时 SQLite 知识库 |
| `artifact_manager` | 临时 Artifact 管理器 |
| `mock_tasks_yaml` | 最小任务配置 |
| `mock_llm_provider` / `mock_llm_registry` | 假 LLM Provider，避免真实调用 |
| `fake_embedding_fn` | 固定向量，用于向量库测试 |

原则：所有测试使用临时项目/临时 SQLite/离线夹具，不写真实用户项目。

### 1.2 运行

```powershell
# 全量
python -m pytest tests/ -q

# 架构与编排专项
python -m pytest tests/test_node_architecture.py tests/test_stages_graphs.py `
  tests/test_orchestration_context.py tests/test_orchestration_node.py tests/test_orchestration_graph.py -q

# 快速产品回归（README 推荐）
python -m pytest tests/test_product_plan_boundary.py tests/test_project_conversations.py `
  tests/test_library_audit_refresh.py -q
```

### 1.3 测试分组

| 主题 | 代表文件 |
| --- | --- |
| 编排引擎 | `test_orchestration_context.py`、`test_orchestration_node.py`、`test_orchestration_graph.py`、`test_stages_graphs.py` |
| 节点架构契约 | `test_node_architecture.py`（总数、阶段分布、I/O 契约、数据流完整性） |
| 产品边界 | `test_product_plan_boundary.py`、`test_unified_workflow.py` |
| 工作台/会话 | `test_project_conversations.py`、`test_second_batch_workspace.py`、`test_conversation_home.py`、`test_conversation_moves.py` |
| 论文阅读 | `test_reading_candidate.py`、`test_guided_paper_reading.py`、`test_question_guided_paper_reading.py`、`test_reading_grounded_explanations.py` |
| 方案 | `test_research_plan_templates.py`、`test_plan_generation_contract.py`、`test_plan_attribution_repair.py` |
| 研究任务/检索 | `test_research_tasks.py`、`test_real_workflow_reliability.py`、`test_research_fetch_fallback.py` |
| 知识库 | `test_knowledge_store.py`、`test_knowledge_schema.py`、`test_knowledge_retriever.py`、`test_knowledge_vector_store.py` |
| LLM 路由 | `test_llm_registry.py`、`test_llm_task_router.py` |
| 状态机 | `test_state_lifecycle.py`、`test_state_session.py`、`test_state_snapshot.py`、`test_state_transition.py` |
| Artifact/溯源 | `test_artifacts_version.py`、`test_artifacts_provenance.py` |
| 账号/资源/备份 | `test_local_account_isolation.py`、`test_local_registration.py`、`test_local_resource_protection.py`、`test_local_backup_restore.py` |
| 教学 | `test_research_teaching_cases.py`、`test_teaching_experience.py` |

> 注意：测试目录中保留了内部历史架构用例。网页产品不放开的实验/写作执行能力，不应据测试名称判断为产品功能。

### 1.4 浏览器测试

`tests/*.cjs` 是 Node + Playwright 脚本，配合 `tests/*_test_server.py` 隔离服务使用：

```powershell
npm install --no-save playwright
python tests/real_workflow_test_server.py --port 8069
# 另一个终端
node tests/library_audit_browser.cjs
```

- 隔离服务使用临时数据，仅替换外部模型/来源传输边界；
- 默认浏览器通常是本机 Edge；无 Edge 需调整脚本的 browser channel；
- 不要指向真实用户项目。

### 1.5 离线守卫

`tests/resource_offline_guard.py` 是可选的 pytest 插件：把所有非回环 socket 连接替换为断言失败，用于资源验收时确保没有外网调用。通过 `-p tests.resource_offline_guard` 或配置启用。

### 1.6 综合验证脚本

`tests/run_all.py`（本地验证入口，不是 pytest）：

```powershell
python tests/run_all.py            # 全部验证
python tests/run_all.py --quick    # 仅架构 + API
```

依次运行 `validate_arch`、`validate_pipeline`、`validate_outputs`、`validate_competition`、`validate_api`，输出终端报告与 `tests/last_full_report.json`。

## 2. 部署

### 2.1 Docker（Hugging Face Spaces）

`Dockerfile`：

- 基础镜像 `python:3.10-slim`；
- 安装 `requirements.txt` 与固定 `uvicorn==0.23.2`；
- 复制代码时排除 `projects/`、`.pytest_cache`、`*.db`、日志等；
- `EXPOSE 7860`；
- 启动 `python -m web.api`。

HF Spaces 通过 Secrets 注入模型密钥；端口由平台 `$PORT` 或 `SRA_WEB_PORT` 控制。

### 2.2 Render

`render.yaml`：

```yaml
services:
  - type: web
    name: scientific-research-agent
    runtime: python
    plan: free
    buildCommand: pip install -r requirements.txt
    startCommand: python -m web.api
```

- `PYTHON_VERSION=3.11.0`；
- 端口自动读取 Render 的 `$PORT`；
- 密钥在 Dashboard → Environment 配置，不提交到仓库。

### 2.3 Replit

`requirements-replit.txt` 与 `.replit` 提供 Replit 运行配置；入口同样是 `python -m web.api` 或本地启动脚本。

### 2.4 HF Space 辅助目录

- `hf-space-deploy/`：Space 部署配置/说明；
- `hf-static/`：静态前端资源镜像。

### 2.5 启动脚本

| 文件 | 作用 |
| --- | --- |
| `start_research_system.bat` | Windows 一键启动 |
| `tools/start-local-demo.ps1` | demo + 真实模型/来源 |
| `tools/start-local-accounts.ps1` | accounts 模式 8077 |

## 3. 发布注意

- 不要提交 `.env`、`projects/`、`*.db`、日志、私人文献；
- 产品处于「本地体验」阶段，尚未完成生产级 HTTPS、容量与分布式任务编排验收（见 `docs/current-status.md`）；
- 单进程、Windows 本机是当前验证环境；多 worker 部署需要重新评估 SQLite 锁、会话锁与单实例限制；
- 发布前建议至少运行：`pytest tests/ -q`、`python tests/run_all.py --quick`、一次 demo 模式的浏览器冒烟。
