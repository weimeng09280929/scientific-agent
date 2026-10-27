# 安装与配置指南

[返回项目首页](../README.md) · [使用指南](user-guide.md)

本文面向首次搭建 Scientific Agent 的使用者。可以自己安装，也可以将安装任务交给编程 Agent。

[人工安装](#方式一自己安装) · [Agent 辅助安装](#方式二交给编程-agent-安装) · [数据与更新](#数据保存更新与服务器运行) · [安装后自查](#安装后自查)

准备 **Git、Python 3.10 或以上版本**，以及一个你有权使用、且余额或额度可用的模型服务账号。首次使用建议先在本机单用户环境运行；前端直接由后端提供，不需要安装 Node.js 或单独构建前端。

### 方式一：自己安装

**第一步：下载代码并创建独立环境。**

```bash
git clone https://github.com/weimeng09280929/scientific-agent.git
cd scientific-agent
python -m venv venv
```

如果电脑上的命令是 `python3`，请用它代替这里的 `python` 创建环境。后续命令都在仓库根目录执行。

Windows PowerShell：

```powershell
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

如果 PowerShell 拒绝运行激活脚本，不需要修改系统策略。直接将后续命令中的 `python` 替换成 `.\venv\Scripts\python.exe` 即可。

Linux / macOS：

```bash
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

如需提取可访问 PDF 的文本，可补装 `python -m pip install pypdf`。扫描件不等于已经取得正文，可能仍需额外解析或 OCR。

**第二步：配置自己的模型。**

用文本编辑器打开 `.env`。要进行真实模型调用，至少配置：

```dotenv
SRA_DRY_RUN=false
DEEPSEEK_API_KEY=填写你自己的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

上面是仓库现有 `deepseek` 路由对应的配置格式；如果使用兼容网关，必须填该网关给出的地址和密钥，不能混用不同服务商的凭据。

同时打开 `config/tasks.yaml`，核对各任务的 `provider` 与 `model`。当前生成任务配置为 `deepseek / deepseek-v4.1-flash-1017`，**这是本地路由标识，不是对官方 API 或任意网关可用性的保证**。示例地址不能保证支持这个标识，请配置你实际服务的地址、密钥和可用模型；不要仅凭复制默认配置就认为模型可用，也不要依据文件顶部的旧注释判断当前路由。

单个任务的配置形式如下，**不要用这个片段覆盖整个文件**：

```yaml
tasks:
  research_subquery_decompose:
    provider: deepseek
    model: 你的服务实际支持的模型标识
    temperature: 0.2
```

切换模型时，应检查科研指导、检索规划／整理和方案等相关任务，而不只改一个任务。旧内部阶段的路由保留不代表网页开放实验或写作。切换厂商时，还要填写对应凭据并修改 `provider`；具体支持的配置可查看 `.env.example` 与 `core/llm/registry.py`。

| 配置 | 是否必需 | 用途 |
| --- | --- | --- |
| `SRA_DRY_RUN=false` | 真实使用时需要 | 关闭部分节点的占位演示；真实调用可能产生费用 |
| 任务对应的模型密钥、地址与模型标识 | 真实使用时需要 | 为模型生成与科研指导提供服务 |
| `SCIVERSE_API_TOKEN` | 可选 | 启用需授权的文献检索源；未配置时可尝试其他源 |
| `OPENAI_API_KEY` | 可选 | 供当前 OpenAI 嵌入路由使用，不等同于聊天模型凭据 |

`SRA_DRY_RUN=true` 只能用于架构演示，不能验证真实模型或科研结果；它也不代表所有外部网络请求都已禁用。文献源是否可用仍取决于网络、授权和限流。已有系统环境变量优先于 `.env`，修改配置后需要重启服务。

不要把 `.env`、真实密钥、私人论文或项目数据提交到 Git。使用云端模型与解析服务时，相应内容可能发送给服务商，请先确认权限与隐私要求。

**第三步：启动并打开工作台。**

```bash
python -m uvicorn web.api:app --host 127.0.0.1 --port 8001 --workers 1
```

保持这个终端运行，在浏览器打开：

- 工作台：[http://127.0.0.1:8001/workspace](http://127.0.0.1:8001/workspace)
- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)

看到页面只代表网页已启动，不代表模型和文献源都已验证。第一次请用一个小范围主题测试，检查“任务进度”和“待确认事项”，确认拿到了实际来源和有效回复。

工作台输入区可展开 **运行配置与离线体验**；也可读取 `GET /api/runtime-status`。检查仅针对当前科研指导、材料阅读、问题讨论、方案与检索主流程，区分规则模式、缺少配置和配置存在但未验证连接，不请求模型、不回传密钥或服务地址。填写其他服务商密钥不会自动切换任务路由。修改服务端配置后重启，再点击“重新读取配置”；无需模型的体验可直接选择离线教学案例。

使用本地 OpenAI 兼容服务时，把相关任务的 `provider` 改为 `local` 并设置 `LOCAL_LLM_BASE_URL`；不要求同时配置官方 OpenAI 密钥。具体模型标识仍需与本地服务一致。

停止服务时，在启动终端按 `Ctrl+C`。以后重新使用时，进入仓库、激活环境，再运行同一启动命令即可，**不需要重新安装或重新创建项目**。

请使用单 worker，暂不增加进程数。若端口已被占用，先确认是不是本项目已在运行，不要直接结束不认识的进程。需要换端口时，在 `.env` 设置 `SRA_WEB_PORT=8002`，并将启动命令改为 `--port 8002`，访问地址也相应调整。

### 方式二：交给编程 Agent 安装

这里的“Agent 安装”指让具备终端与文件操作能力的编程助手完成部署，**不是把本项目安装成某个聊天工具的插件**。先给助手指定一个空目录或现有仓库，再复制下面的指令：

```text
请帮我安装 Scientific Agent：
https://github.com/weimeng09280929/scientific-agent

1. 先检查当前目录、Python 版本及是否已有仓库；已有代码则保留改动，
   不要覆盖文件、删除数据或强制重置 Git。
2. 阅读 README、requirements.txt、.env.example 和 config/tasks.yaml，
   创建 venv，并在该环境安装依赖；不需要安装前端构建工具。
3. 仅在 .env 不存在时从 .env.example 创建它。
   告诉我需要填写哪些模型凭据，不要读取或打印我已有的真实密钥，
   不要将 .env、projects/ 或私人文件提交到仓库。
4. 核对实际任务的 provider/model 与我提供的服务是否匹配；
   缺少凭据或模型标识时先问我，不要编造可用模型。
5. 经我确认真实调用模式及可能的费用后，再设置 SRA_DRY_RUN=false。
   未经确认不要调用收费 API、创建真实研究任务或运行实验。
6. 检查端口占用；不要杀死未知进程。以单 worker 启动：
   python -m uvicorn web.api:app --host 127.0.0.1 --port 8001 --workers 1
7. 验证 /workspace 和 /docs 可以访问，说明这仅验证网页启动，
   不代表真实模型与文献检索已经通过；后续真实调用另行征求同意。
8. 最后告诉我访问地址、启动与停止方式、配置了哪些文件，
   以及尚未验证的事项。不要把本地开发服务直接暴露到公网。
```

安装完成后，研究问题与方案的选择、来源核对和正式采纳仍需你参与。编程助手完成环境搭建，不等于科研 Agent 已经替你完成研究。本版止于方案，不执行实验或论文生成。

### 数据保存、更新与服务器运行

科研项目保存在仓库下的 `projects/`，模型凭据在 `.env`，任务路由在 `config/tasks.yaml`。换机器前应备份这些内容；共享备份时注意其中可能包含私人材料与密钥。

更新代码前，先停止服务并备份数据，使用 `git status` 检查本地改动。工作区干净时可执行 `git pull --ff-only`，再重新安装依赖并核对配置；有未提交改动时先保存或合并，不要用强制覆盖解决冲突。

服务器上也可按相同步骤搭建，但当前版本应先用于受控的小范围验证。**不要只把启动地址改成 0.0.0.0 就当作完成公网部署。** 面向公众开放前，还需要登录与项目权限隔离、HTTPS、调用限额、文件访问保护和备份恢复验收；当前单进程任务状态也不能靠增加 worker 直接扩容。

### 安装后自查

- 工作台和 API 文档能打开：网页服务启动成功。
- 实际任务获得模型回复：模型路由、凭据和额度基本可用；仅打开网页不能证明这一点。
- 检索结果有可访问的来源：本次检索获得了可核对的材料；有元数据不代表一定能下载全文。
- 离开页面后能重新打开原项目：检查项目编号、材料和当前步骤是否对应。
- 停止再启动后能找到项目：检查数据目录是否保留；中断任务仍需要单独检查和恢复。

开发者可另行安装测试依赖并运行回归测试：

```bash
python -m pip install pytest pytest-asyncio httpx
python -m pytest tests/test_product_plan_boundary.py tests/test_project_conversations.py tests/test_library_audit_refresh.py -q
```

这些测试不等同于真实模型、外部检索和整条科研流程的端到端验收。


## 常见安装问题

**依赖安装失败怎么办？**

确认正在使用虚拟环境中的 Python，版本不低于 3.10，并保留完整报错。检查网络、包下载情况和平台兼容性；不要反复重装整个项目或删除项目数据。

**网页能打开，但生成报错？**

网页服务与模型服务是两部分。检查 `SRA_DRY_RUN`、任务的 `provider/model`、对应密钥和服务地址，以及账号额度。密钥不要粘贴进公开 Issue 或日志。

**更换配置后为什么没有生效？**

先检查系统环境变量是否覆盖了 `.env`，再重启服务。只改另一家服务商的密钥，不会自动改变任务路由。

**端口占用怎么办？**

先检查是否已有本项目实例在运行；若是，直接访问原服务或在其终端正常停止。不要使用跳过实例检查作为日常启动方式，也不要强制结束未知进程。换端口需同时修改 `SRA_WEB_PORT` 和启动参数。

配置完成后，按[使用指南](user-guide.md)创建第一个研究项目。
