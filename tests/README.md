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
| 离线教学与进度 | test_research_teaching_cases.py、test_teaching_experience.py |
| 文献计数与检索审计展示 | test_navigation_counts.py、test_library_audit_refresh.py |

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
