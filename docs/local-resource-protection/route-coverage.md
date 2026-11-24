# 实际 OpenAPI 路由覆盖

| 方法 | 路由 | 资源规则 |
| --- | --- | --- |
| POST | `/api/projects` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/conversations/recent` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| DELETE | `/api/projects/{project_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/batch-delete` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/status` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/resume` | 统一重任务；已有运行返回／冲突；新后台工作申请名额 |
| POST | `/api/projects/{project_id}/run` | 统一重任务；已有运行返回／冲突；新后台工作申请名额 |
| POST | `/api/projects/{project_id}/run-topic-discovery` | 统一重任务；已有运行返回／冲突；新后台工作申请名额 |
| GET | `/api/projects/{project_id}/recommendations` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/papers` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/materials` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/gaps` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conflicts` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conflicts/{conflict_id}/adjudicate` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/evidence` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/unlinked-papers` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/papers/import` | 统一重任务；旧外部获取入口 |
| GET | `/api/projects/{project_id}/papers/{paper_id}/pdf` | 已有本地文件不占名额；外部获取占重任务，并按实际响应字节限制文件大小 |
| GET | `/api/projects/{project_id}/claims` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/experiments` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/regenerate-method-doc` | 统一重任务；已有运行返回／冲突；新后台工作申请名额 |
| GET | `/api/projects/{project_id}/method-doc-status` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/experiment-tasks` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/experiments/backfill` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/notes` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/notes` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/human-response` | 已有工作流控制；现有工作线程保持其原名额 |
| GET | `/api/projects/{project_id}/research-report` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/dashboard` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/download/{artifact_type}` | 既有 Markdown 下载不占名额；PDF／DOCX 转换占重任务 |
| POST | `/api/projects/{project_id}/upload-paper` | 接收名额＋multipart 字节限制；解析共用重任务 |
| GET | `/api/projects/{project_id}/upload-requests/{request_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/upload-topic` | 接收名额＋multipart 字节限制；解析共用重任务 |
| GET | `/api/teaching-cases` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/teaching-cases/start` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/teaching` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/teaching/choices` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/teaching/fork` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/teaching/export` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/conversation-capabilities` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/conversations/start` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| PATCH | `/api/projects/{project_id}/identity` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/workspace/materials` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/activities` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/exports` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/exports/items/{item_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/suggestions/{suggestion_id}/export` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/exports/artifacts/{artifact_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/research-tasks` | 统一重任务；任务幂等后，queued 前；后台结束释放 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/research-tasks` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}/cancel` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/research-tasks/{task_id}/retry` | 统一重任务；任务幂等后，queued 前；后台结束释放 |
| GET | `/api/projects/{project_id}/research-tasks/{task_id}/export` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/call-audit` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| PATCH | `/api/projects/{project_id}/conversations/{conversation_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/research-route` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/messages` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/messages` | 统一重任务；幂等及会话锁后，processing 前 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/suggestions` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/requests/{request_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/requests/{request_id}/retry` | 统一重任务；幂等及会话锁后，processing 前 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/requests/{request_id}/restart` | 统一重任务；幂等及会话锁后，processing 前 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/suggestions/{suggestion_id}/apply` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/conversations/{conversation_id}/messages/{message_id}/actions/{action_id}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/conversations/{conversation_id}/guidance-actions` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/workspace` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/research-materials` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| PUT | `/api/projects/{project_id}/workspace/brief` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/workspace/items` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| PUT | `/api/projects/{project_id}/workspace/focus` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/workspace/link-material` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/projects/{project_id}/workspace/guide` | 统一重任务；幂等及会话锁后，processing 前 |
| POST | `/api/projects/{project_id}/workspace/suggestions/{suggestion_id}/apply` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/projects/{project_id}/workspace/export` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/auth/options` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/auth/register` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/auth/me` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/auth/login` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| POST | `/api/auth/logout` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/resource-limits` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/workspace` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/api/data-sources` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/static/{file_path}` | 不新建重任务；保留原鉴权、采纳、版本、文件边界 |
| GET | `/__resource__/state` | 仅隔离测试控制，产品不注册 |
| POST | `/__resource__/hold` | 仅隔离测试控制，产品不注册 |
| GET | `/__resource__/entered` | 仅隔离测试控制，产品不注册 |
| POST | `/__resource__/release` | 仅隔离测试控制，产品不注册 |
| POST | `/__resource__/hold-source` | 仅隔离测试控制，产品不注册 |
| GET | `/__resource__/source-entered` | 仅隔离测试控制，产品不注册 |
| POST | `/__resource__/release-source` | 仅隔离测试控制，产品不注册 |
| POST | `/__resource__/shutdown` | 仅隔离测试控制，产品不注册 |
