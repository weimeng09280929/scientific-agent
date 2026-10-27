# 测试说明

测试主要使用临时项目、SQLite 和离线模型／来源夹具，不用真实用户项目验证写入。

## 快速回归

在安装项目依赖的虚拟环境中补充：

```bash
python -m pip install pytest pytest-asyncio httpx
python -m pytest tests/test_product_plan_boundary.py tests/test_project_conversations.py tests/test_library_audit_refresh.py -q
```

## 按能力选择

| 能力 | 用例 |
| --- | --- |
| DESIGN 上限与旧入口保护 | test_product_plan_boundary.py |
| 多会话、幂等与材料依据 | test_project_conversations.py、test_second_batch_workspace.py |
| 检索来源、报告范围与调用审计 | test_research_tasks.py、test_real_workflow_reliability.py |
| 三类方案与修订 | test_research_plan_templates.py |
| 交付检查、引用位置、只读与导出一致性 | test_plan_delivery_quality.py |
| 材料数值与条件线索、反例与导出一致性 | test_material_plan_quality.py |
| 运行配置只读检查、凭据保护与本地模型注册 | test_runtime_readiness.py、test_llm_registry.py |
| 模型异常安全分类、API 与导出隐私、历史诊断读取 | test_research_error_privacy.py |
| 排队领取、并发补跑与后台启动失败恢复 | test_research_retry_reliability.py、test_research_tasks.py |
| 草稿从生成切普通讨论、显式重新生成 | test_plan_discussion_intent.py、test_conversation_product.js |
| 实际 PDF 长文、表格与科学单位保真 | test_pdf_delivery_fidelity.py |
| 离线教学与进度 | test_research_teaching_cases.py、test_teaching_experience.py |
| 文献计数与检索审计展示 | test_navigation_counts.py、test_library_audit_refresh.py |

前端讨论准备、草稿保留、检查刷新失败与配置提示行为可用 `node tests/test_conversation_product.js` 验证，无额外依赖；布局和原生对话框仍需实际浏览器走查。

PDF 字节验证需要 Poppler `pdftotext` 位于 PATH；缺少时对应提取用例会明确跳过。科学字形回退用例验证 Windows Segoe UI 字体；缺少该字体会跳过，不能将跳过视为跨平台字体已验证。

完整测试集还包含保留的内部历史架构测试。内部完整流水线不是网页产品开放能力，不能根据测试名称认为产品会运行实验或生成论文。

## 主应用浏览器检查

浏览器脚本需要另装 Node.js、Playwright 与兼容浏览器。以文献审计专项为例：

```bash
npm install --no-save playwright
python tests/real_workflow_test_server.py --port 8069
```

另开终端执行 `node tests/library_audit_browser.cjs`。该隔离服务使用临时数据，替换的仅为外部模型和来源传输边界；脚本操作真实 FastAPI 接口及主应用。其默认浏览器为本机 Edge；没有 Edge 的环境需调整测试的 browser channel，或安装对应浏览器后运行。

结束后关闭隔离实例；不要让脚本指向真实用户项目。其他脚本的端口与输出目录见各自环境变量及文件头。

离线测试与浏览器检查不代表真实模型回答质量、科学正确性或公网发布验收。未经授权不要运行带 live 名称的脚本或真实服务调用。

## 方案交付检查接口

`GET /api/projects/{project_id}/workspace/plan-quality` 返回版本为 `1` 的结构化检查结果。指定 `item_id` 检查项目内的正式或历史方案；指定 `conversation_id` 和 `suggestion_id` 检查该会话的方案草案。两种目标不能混用，其他项目或会话的目标返回 404。

返回结构包含 `target_id`、`workspace_revision`、`status`、`checks`、`evidence`、`pending_conditions` 和 `review_questions`。`status` 为 `legacy`、`needs_attention` 或 `ready_for_discussion`，均不表示科学验证通过。`evidence.matched` 仅指原引文在当前摘要或已存正文片段中匹配且列入参考来源；`location` 和可空的 `page` 来自实际材料。读取不调用模型、不迁移数据库、不改写方案；修订和采纳仍使用原接口的版本及依据检查。
