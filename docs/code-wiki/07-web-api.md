# 07 · Web 层与 API

## 1. 应用组装

`web/api.py` 是唯一 FastAPI 应用入口。

```python
app = FastAPI(title="科研 Agent 系统", version="0.1.0", lifespan=_instance_lifespan)  # L707
```

启动阶段（`_instance_lifespan`，`L693-704`）：

1. `core.instance_state.running_instance(...)` 获取单实例锁；
2. `recover_journals` 恢复未完成的会话移动；
3. `_scan_existing_projects()` 扫描数据目录下的项目，重建内存 `_PROJECTS`。

路由注册（`L4027-4035`）：

| 路由工厂 | 前缀/标签 | 负责模块 |
| --- | --- | --- |
| `build_home_router` | `/api`，`conversation-home` | 会话首页、阅读目标、首次开始、项目身份 |
| `build_exports_router` | `/api/projects`，`project-exports` | 材料、活动、导出、Artifact 下载 |
| `build_workspace_router` | `/api/projects`，`research-workspace` | 工作台、研究任务、会话 |
| `build_move_router` | `/api/projects`，`conversation-moves` | 会话移动 |
| `main` 内直接定义 | 无前缀 | 项目、状态、运行、论文、材料、Gap、冲突、证据、Claim、实验、报告、上传、账号、静态页 |

## 2. 鉴权与会话

| 机制 | 实现 |
| --- | --- |
| 模式 | `SRA_AUTH_MODE=local|accounts`（`web/api.py:301-327`） |
| 当前用户 | `_active_user()`：accounts 模式未登录返回 401 |
| 项目归属 | `_check_owner(project_id)`：校验路径安全与账号归属 |
| 账号库 | `core/local_accounts.py`，SQLite + argon2 密码哈希 |
| 注册 | `POST /api/auth/register`，仅当 `SRA_ALLOW_LOCAL_REGISTRATION=1` 且请求来自回环地址 |
| 登录/登出/当前用户 | `POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`、`GET /api/auth/options` |
| Cookie | HttpOnly、SameSite=Lax；本地 HTTP 测试需显式 `SRA_AUTH_INSECURE_HTTP=1` |
| 中间件 | `account_boundary` 统一拦截未授权项目访问；`teaching_execution_boundary` 限制教学案例执行范围 |
| 校验错误 | 登录/注册的 `RequestValidationError` 不回显密码（`private_login_validation`） |

`local` 模式只允许回环客户端与回环 Host；`python -m web.api` 在 local 模式下绑定非回环地址会直接报错（`L4294-4295`）。

## 3. 主要 API 分组

以下为代表性端点，完整列表可在 `/docs`（FastAPI 自动 OpenAPI）或 `web/*.py` 中查看。

### 3.1 项目与全局

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects` | 项目列表（按账号过滤） |
| DELETE | `/api/projects/{pid}` | 删除项目 |
| POST | `/api/projects/batch-delete` | 批量删除 |
| GET | `/api/projects/{pid}/status` | 项目状态、阶段、进度 |
| POST | `/api/projects/{pid}/run` | 启动流水线（后台线程） |
| POST | `/api/projects/{pid}/resume` | 恢复中断项目 |
| POST | `/api/projects/{pid}/run-topic-discovery` | 方向推荐 |
| GET | `/api/projects/{pid}/recommendations` | 推荐结果 |
| GET | `/api/conversations/recent` | 最近对话 |
| GET | `/api/data-sources` | 数据源披露 |
| GET | `/api/resource-limits` | 当前资源限额 |

### 3.2 工作台

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects/{pid}/workspace` | 工作台读模型 |
| GET | `/api/projects/{pid}/research-materials` | 研究材料 |
| PUT | `/api/projects/{pid}/workspace/brief` | 保存项目背景 |
| POST | `/api/projects/{pid}/workspace/items` | 新增正式记录 |
| PUT | `/api/projects/{pid}/workspace/focus` | 设置当前问题/方案 |
| POST | `/api/projects/{pid}/workspace/link-material` | 关联材料 |
| POST | `/api/projects/{pid}/workspace/guide` | 生成引导回复 |
| POST | `/api/projects/{pid}/workspace/suggestions/{sid}/apply` | 采纳建议 |
| GET | `/api/projects/{pid}/workspace/export` | 导出工作台 Markdown |

### 3.3 会话

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET/POST | `/api/projects/{pid}/conversations` | 列表/新建 |
| GET/PATCH | `/api/projects/{pid}/conversations/{cid}` | 详情/修改 |
| GET | `/api/projects/{pid}/conversations/{cid}/messages` | 消息分页 |
| POST | `/api/projects/{pid}/conversations/{cid}/messages` | 发送消息 |
| GET | `/api/projects/{pid}/conversations/{cid}/requests/{rid}` | 请求状态 |
| POST | `.../requests/{rid}/cancel-passage` | 取消本轮阅读请求 |
| POST | `.../requests/{rid}/retry`、`.../restart` | 重试/重启 |
| POST | `.../suggestions/{sid}/apply` | 采纳建议 |
| GET | `/api/projects/{pid}/conversations/{cid}/guidance-actions` | 可用操作 |
| GET | `/api/projects/{pid}/call-audit` | 调用审计 |
| GET | `/api/conversation-capabilities` | 会话能力 |
| POST | `/api/conversations/reading-target` | 阅读目标解析 |
| POST | `/api/conversations/start` | 首次开始（幂等） |
| PATCH | `/api/projects/{pid}/identity` | 修改项目显示名 |

### 3.4 文献、材料、证据

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects/{pid}/papers` | 文献列表 |
| GET | `/api/projects/{pid}/papers/{paper_id}/pdf` | 下载/获取 PDF |
| POST | `/api/projects/{pid}/papers/import` | 导入未关联论文 |
| GET | `/api/projects/{pid}/unlinked-papers` | 未关联论文 |
| GET | `/api/projects/{pid}/materials` | 领域材料知识 |
| GET | `/api/projects/{pid}/gaps` | 研究缺口 |
| GET | `/api/projects/{pid}/conflicts` | 文献冲突 |
| POST | `/api/projects/{pid}/conflicts/{cid}/adjudicate` | 冲突裁决 |
| GET | `/api/projects/{pid}/evidence` | 证据链分页 |
| POST | `/api/projects/{pid}/upload-paper` | 上传论文（有界、幂等） |
| GET | `/api/projects/{pid}/upload-requests/{rid}` | 上传状态 |
| POST | `/api/projects/{pid}/upload-topic` | 上传主题文件 |

### 3.5 方案、实验、报告、导出

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects/{pid}/claims` | Claim 列表 |
| GET | `/api/projects/{pid}/experiments` | 实验列表 |
| POST | `/api/projects/{pid}/regenerate-method-doc` | 重新生成方法文档 |
| GET | `/api/projects/{pid}/method-doc-status` | 方法文档状态 |
| GET | `/api/projects/{pid}/experiment-tasks` | 实验任务包 |
| POST | `/api/projects/{pid}/experiments/backfill` | 回填实验结果 |
| POST | `/api/projects/{pid}/human-response` | 提交人工节点响应 |
| GET | `/api/projects/{pid}/research-report` | 调研报告 |
| GET | `/api/projects/{pid}/dashboard` | 仪表盘 |
| GET | `/api/projects/{pid}/download/{artifact_type}` | 下载产物（md/docx/pdf） |
| POST | `/api/projects/{pid}/notes`、GET `.../notes` | 笔记 |

### 3.6 导出子路由（`project_exports_api.py`）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/projects/{pid}/workspace/materials` | 工作台材料 |
| GET | `/api/projects/{pid}/activities` | 活动流 |
| GET | `/api/projects/{pid}/exports` | 可导出条目 |
| GET | `/api/projects/{pid}/exports/items/{item_id}` | 导出条目 |
| GET | `/api/projects/{pid}/exports/artifacts/{artifact_id}` | 导出 Artifact |

### 3.7 研究任务（`research_task_api.py`）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `.../conversations/{cid}/research-tasks` | 创建检索/整理任务 |
| GET | `.../conversations/{cid}/research-tasks` | 任务列表 |
| GET | `.../research-tasks/{task_id}` | 任务详情 |
| POST | `.../research-tasks/{task_id}/cancel` | 取消 |
| POST | `.../research-tasks/{task_id}/retry` | 重试 |
| GET | `/api/projects/{pid}/research-tasks/{task_id}/export` | 导出报告 |

### 3.8 会话移动与教学

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| （`conversation_move_api.py`） | `/api/projects/...` | 会话移动、预检、恢复 |
| （`teaching_case_api.py`） | `/api/...` | 教学案例列表、开始、进度 |

## 4. 长任务与并发模型

- 网页启动流水线时，`web/api.py` 为项目创建后台线程（`_run_pipeline_thread`，`L503`），把进度写入内存 `ProjectState` 与 `kv_store`；
- 节点级进度通过 `on_progress` / `on_node_started` 回调更新（`_make_progress_callback`，`L468`），前端轮询 `/status` 获取；
- `_PROJECTS` 字典 + `_LOCK` 保护运行态；`core/workspace/conversation_lock.py` 保护单项目会话写；
- 资源保护：`core/resource_limits.py` 限制重任务/接收并发、请求体、字段、文件字节；`web/bounded_uploads.py` 处理有界上传。

## 5. 上传与导出

### 5.1 上传

`POST /api/projects/{pid}/upload-paper`（`L3736-3770`）与 `upload-topic`：

1. 通过 `bounded_uploads` 做大小/字段限制与流式落盘；
2. `_prepare_upload` / `_prepare_upload_content` 解析 PDF/TXT/Markdown；
3. 生成稳定 `paper_id`，重复上传幂等；
4. 解析结果写入 `KnowledgeStore`；
5. 上传请求状态可通过 `upload-requests/{rid}` 查询。

### 5.2 导出

`GET /api/projects/{pid}/download/{artifact_type}?format=md|docx|pdf`：

- Markdown 为主格式；
- `_md_to_docx_response`（python-docx）与 `_md_to_pdf_response`（fpdf2）做转换；
- 公式优先走前端渲染/打印路径，Word/PDF 转换有排版限制（见 `docs/current-status.md`）。

## 6. 前端组织（`web/static/`）

无构建步骤，后端直接托管静态文件；`/static/{path}` 有路径穿越校验（`L4266-4283`）。

| 文件 | 作用 |
| --- | --- |
| `index.html` + `app.js` + `style.css` | 主工作台/旧版单页 |
| `workspace.html` + `workspace.js` + `workspace.css` | 新版工作台壳 |
| `conversation-home.js/.css` | 会话首页、项目导航 |
| `project-library.js` / `project-management.js` / `project-background.js` | 项目库、项目管理、项目背景 |
| `research-guide.js/.css` | 科研引导与讨论步骤 |
| `research-navigation.js` / `research-flow.js` | 研究导航与流程视图 |
| `research-document.js/.css` | 文档/方案展示 |
| `ui-experience.css` | 全局 UI 规范 |
| `teaching-cases.js/.css` | 教学案例 |
| `local-auth.js` / `resource-protection.js` | 本地登录、资源保护提示 |
| `vendor/mathjax`、`vendor/marked` | 公式与 Markdown 渲染 |

页面与 API 的对应关系：页面脚本直接 `fetch('/api/...')`，没有前端路由框架；状态以服务端读模型为准，前端不做业务规则推断。

## 7. 修改建议

- 新增路由优先放进对应 `web/*_api.py` 的 `build_*_router()`，避免继续膨胀 `web/api.py`；
- 所有写接口必须带 `expected_revision` 或幂等键；
- 不要在前端拼业务规则（如「是否可下载」「是否可采纳」），这些应由 `WorkspaceService` / `workflow.analyze` 返回；
- 新增静态文件后确认没有绕过 `/static/{path}` 的安全校验。
