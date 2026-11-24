# 自用入口文献来源禁用修复

2026-11-03。用户实际检索全部来源 unexpected_error。原因是 8001 使用 `--allow-live-model` 启动后，启动器仍替换 SOURCE_FUNCS 并阻断 requests；抛出的 RuntimeError 被归类为 unexpected_error，不是查询无结果。

新增独立 `--allow-live-sources`，只有明确选择 demo 才可启用；账号离线模式保持原状。禁用状态改为稳定 local_calls_disabled，明确提示检查启动配置，不建议反复网络重试。原来源预算、超时、任务确认与并发保护未放宽。

8001 自用真实入口：

```powershell
venv/Scripts/python.exe -m tools.serve_local --mode demo --port 8001 --allow-live-model --allow-live-sources
```

`tools/start-local-demo.ps1` 已同步此命令，避免脚本下次又启动成离线来源。启动本身不检索或调用模型。8077 保持原离线入口。

验证：`tests/test_local_source_launcher.py` 四种模型／来源开关组合全部通过，网络离线守卫启用；禁用来源明确分类、不可直接重试，启用来源保留原适配器。未改写已有失败任务、项目或材料，没有自动补跑检索。

已重启本次 8001 实例加载修复。只做两个最小只读公网来源探针：OpenAlex works 查询返回 HTTP 200、1 条记录；arXiv API 返回 HTTP 200、1 条 entry。探针使用简短英文主题，不写入任何项目，不做综合生成。真实模型调用为零。这证明本机当时两来源可连接，不代表用户原三条查询已成功完成、全部语义相关或以后公网始终可用。

修改：`tools/serve_local.py`、`tools/start-local-demo.ps1`、`core/tools/search_result.py`、`web/static/conversation-home.js`；新增上述四项专项测试。未提交、推送或部署。
