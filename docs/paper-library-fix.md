# 文献库数量与摘要收放

2026-11-03。核对 `SearchLimits.max_candidates=20` 是单次检索任务候选处理上限；项目 papers 接口读取完整 list_papers，文献库不是固定 20 篇。保留单任务保护，页面补充总量与候选上限的区别。没有修改用户项目、自动检索或调用模型。

摘要无法收回的实际原因：原点击处理只切换 expanded 类，详情创建后没有被隐藏，CSS 也没有实现对应折叠。修复 `web/static/app.js`：增加原生按钮“展开摘要／收起摘要”（无摘要时使用资料详情文字），统一按钮、标题键盘和深链接的展开状态，并明确隐藏／显示详情。正文点击不会误折叠，其他内部下载／资料控件保持原功能。

`tests/paper_library_test_server.py` 使用临时账号与项目实际保存 25 篇样本，真实 API 返回 25 篇；`tests/paper_library_browser.cjs` 检查 25 个卡片、鼠标收放、正文点击、Enter／Space 和 390px 收放。全部通过，模型调用 0。截图实际查看，证据在 [results](paper-library-fix/results.json)、[桌面](paper-library-fix/desktop.png)、[窄屏](paper-library-fix/narrow.png)。临时 8099 实例已关闭，真实 8001／8077 未重启；静态界面刷新即可加载。没有提交、推送或部署。

没有进行真实大批量导入、真实来源再次检索或整个科研系统回归。此验证确认文献数量超过 20 仍可读，不意味着无限内存或存储承诺。
