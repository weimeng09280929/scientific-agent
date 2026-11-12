# Scientific Agent · 科研方案工作台

Scientific Agent 帮助理工科学生和研究者从一个兴趣、一篇论文或已有的研究问题出发，形成一份**有来源、可修订、可下载的研究方案**。在同一个项目里，可以保存研究背景、和 AI 讨论论文、确认研究问题，并审阅每次方案修订。

[快速开始](#快速开始) · [安装与模型配置](docs/getting-started.md) · [使用指南](docs/user-guide.md) · [当前版本与边界](docs/current-status.md)

![Scientific Agent 工作台界面示意](docs/images/workbench.png)

## 从一篇论文开始

1. **保存研究背景。** 点击「新研究」，说明想研究什么，以及已知的数据、设备和时间。在项目背景中确认并保存这些条件。暂时不知道的条件可以留待核实。
2. **导入资料，带着问题读。** 上传 PDF、TXT 或 Markdown，也可以在配置好文献源后明确发起检索。选定论文后直接提问，或用「带我读这篇」从研究目的、方法、实验或局限开始。
3. **打开引文核对。** 在回答下展开「本轮论文依据」，查看系统实际提供给模型的选段。点击「查看来源」定位到原文快照；有页码时可回到 PDF 对照。来源面板也支持选段翻译和通俗解释。
4. **确认问题，再生成方案。** 候选问题由你明确确认。选择计算研究、理论研究或实验研究设计后，生成方法草案，核对比较条件、评价方法和未确认事项。
5. **修订、采纳、下载。** 从原草案发起修订，保留原稿与修订关系。审阅后点击「采纳为正式方案」，从「下载当前产物」下载对应版本。Markdown 为主要导出格式，其他格式以页面显示为准。

同一个项目可以有多段独立对话，共享已保存的背景、论文和正式记录。回答的引用、方案的版本和请求失败状态可以回查。已确认的问题、条件或来源变化时，依赖它们的记录会提示复核。

## 快速开始

以下命令适用于 Windows PowerShell。需要 **Git 和 Python 3.10+**。前端由后端直接提供，无需单独构建。

```powershell
git clone https://github.com/weimeng09280929/scientific-agent.git
cd scientific-agent
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m tools.serve_local --mode demo --port 8001
```

打开 [本机工作台](http://127.0.0.1:8001/)，点击「案例体验 · 离线可用」。联邦学习和硫化物固态电解质两个预置案例都能练习查看依据、确认问题、修改和下载方案，无需模型密钥。

如果想体验注册、登录和账号间项目隔离，在仓库根目录的另一个终端运行：

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode accounts --port 8077 --allow-registration
```

打开 [本机账号版](http://127.0.0.1:8077/)。演示版读取仓库下的 `projects/`，账号版默认使用 Windows 的 `%LOCALAPPDATA%/ScientificAgent/account-workspace/`。两个命令都默认关闭真实模型和文献网络调用。在对应终端按 `Ctrl+C` 停止服务。

### 配置真实生成

复制 [`.env.example`](.env.example) 为本机的 `.env`，填写自己有权使用的服务地址和密钥，再核对 [`config/tasks.yaml`](config/tasks.yaml) 的 `provider` 与 `model`。当前配置的生成任务使用 `deepseek-v4.1-flash-1017`，所选服务须实际支持该标识，或将任务配置为该服务支持的模型。

自用演示可在独立数据目录启用模型。此入口仅绑定本机，可能产生费用，费用和额度由模型服务管理。

```powershell
.\venv\Scripts\python.exe -m tools.serve_local --mode demo --port 8125 --data-root (Join-Path $env:LOCALAPPDATA 'ScientificAgent/live-demo') --allow-live-model
```

打开 [本机真实生成演示](http://127.0.0.1:8125/)。在线文献检索另需显式启用并配置来源。真实账号体验的启动方式见[安装与模型配置](docs/getting-started.md)。

## 已验证的范围

在 Windows 本机的隔离账号版中，使用真实模型走通了 [SimCSE](https://aclanthology.org/2021.emnlp-main.552/) 单篇论文案例。两次基础问答的答案和引文都已核对。随后通过网页确认研究问题、生成草案、修订并采纳，刷新和重登后恢复同一记录，下载的 Markdown 与页面中的正式版本一致。

当前工作流止于**研究设计**。引文只能覆盖实际提供的材料，长论文、复杂公式和扫描件可能需要手动核对。研究问题和方案须由用户审阅和确认，数据、权重或设备的可用性须另行核实。Windows 本地单进程是本次验证环境。详见[当前版本与使用边界](docs/current-status.md)。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| [`web/`](web/) | FastAPI 路由与原生 Web 界面 |
| [`core/workspace/`](core/workspace/) | 项目与对话、论文阅读、研究问题、方案与版本 |
| [`core/llm/`](core/llm/) 与 [`config/`](config/) | 模型接入、任务路由与配置 |
| [`content/teaching-cases/`](content/teaching-cases/) | 版本化的离线教学案例 |
| [`tools/`](tools/) 与 [`tests/`](tests/) | 本地启动、账号与备份工具，以及回归测试 |

项目数据以 SQLite 和本地文件保存。公开仓库提供源码、配置模板、示例和测试。真实密钥、账号、私人文献与项目数据请留在本机。
