# 本地注册与双入口交付（2026-10-27）

本轮依据用户新增授权，补充邮箱密码注册，将新密码下限从 12 改为 8 个字符，并运行两个相互分开的本地入口。此授权更新了上一轮“没有注册、不开 8001”的范围；没有开启公网部署、真实邮件或真实模型／文献调用。

## 当前可用入口

| 用途 | 地址 | 身份与数据 |
| --- | --- | --- |
| 旧项目演示 | `http://127.0.0.1:8001/` | 显式 local 模式，无需登录；使用原仓库 `projects/`，当前目录列表有 13 个项目 |
| 用户工作台 | `http://127.0.0.1:8077/` | accounts 模式，支持注册／登录；用户只能访问归属于自己的项目 |

两个进程均只监听 `127.0.0.1`。模式由显式启动配置决定，不根据登录失败或端口参数偷偷切换。账号启动器拒绝使用 8001，也拒绝把仓库原目录用作账号数据根。演示版显示“历史项目演示 · 无需登录”，不显示退出按钮。

免登录演示入口也拒绝其他网站 Origin／cross-site 请求提交的修改操作；本机免登录不等于接受外部网页替用户写旧项目。

账号版数据现位于 `C:/Users/czh05/AppData/Local/ScientificAgent/account-workspace`，日志目录为同级 `runtime`。当前用户刚建立的 **1 个账号**已通过 SQLite backup 保留到该目录：内部用户 ID、密码散列、邮箱未验证状态和现有会话不变。迁移前确认原体验目录没有研究项目，因而没有改写论文路径、方案、引用或材料指纹；旧体验目录保留，不删除。原来仓库的项目没有分配给该账号，也没有复制到账号目录。

Cookie 按浏览器的主机规则处理，端口本身不隔离 Cookie；演示进程忽略账号 Cookie，只有账号进程用独立账号库解析它。应用缓存、BroadcastChannel 和同源请求则按端口分属不同 origin；原有用户维度缓存和 CSRF 检查继续保留。

## 注册及密码规则

- 登录页有“注册新账号”，注册页包括邮箱、密码、确认密码和返回登录入口。注册成功后明确提示“邮箱尚未验证”，然后由用户用自己刚设置的密码登录；注册不会自动切换现有会话。
- 新密码为 **8–256 个字符**，最多 1024 个 UTF-8 字节，前后端都检查，不截断。允许大小写字母、数字、特殊符号及正常非 ASCII 字符；组合使用是建议，不强制每种字符必须出现。
- 小型离线常见弱密码名单和单一字符重复会被拒绝。界面建议避免常见、已泄露及跨站复用的密码。本轮**没有查询完整泄露密码库，也没有把密码发送给第三方**，不能声称能够识别所有已泄露密码。
- 沿用 Argon2id 和原不可变用户 ID、规范化邮箱唯一约束；邮箱去首尾空白并小写，保留点号和 `+` 别名。已有密码不重新验证、不重新散列、不强制重置，原账号仍可登录。
- `GET /api/auth/options` 公开最小的本地注册能力与密码边界；`POST /api/auth/register` 新建未验证账号。确认密码必须一致，额外的所有者／用户 ID 字段被拒绝。重复规范化邮箱不创建第二个账号。
- 注册需 `X-SRA-Login: 1` 自定义请求头及同源检查，沿用非开放 CORS、会话 CSRF、失效与禁用机制。所有错误响应都不回显密码或确认密码；登录凭证仍为 HttpOnly Cookie，不进 localStorage 或 URL。
- 注册按来源地址持久计数：15 分钟内最多 5 次进入注册处理的尝试，之后 429 并提供冷却说明；注册计数与原登录失败计数使用不同键，不属于模型预算。
- API 默认不开放注册；须显式设 `SRA_ALLOW_LOCAL_REGISTRATION=1`，且请求客户端和 Host 都是回环。即便启用此配置，远程客户端也没有注册能力。`tools.serve_local` 的账号入口通过 `--allow-registration` 启用本机注册。

没有新增邮件验证、验证码、找回密码或公开管理入口。之前临时使用的本机密码设置小窗已不再需要，已关闭对应的自建进程。

## 实现文件与启动方式

主要修改：`core/local_accounts.py`、`web/api.py`、`web/static/local-auth.js`、`index.html`、`style.css`；新增 `tools/serve_local.py` 及两个 PowerShell 启动脚本，更新 `.env.example` 和相应测试。

仓库根目录运行：

```powershell
# 演示入口：原来的项目目录，8001，免登录。
.\tools\start-local-demo.ps1
# 等价命令：
venv/Scripts/python.exe -m tools.serve_local --mode demo --port 8001

# 用户入口：独立持久目录，8077，可本地注册。
.\tools\start-local-accounts.ps1
# 等价命令：
venv/Scripts/python.exe -m tools.serve_local --mode accounts --port 8077 --allow-registration
```

每个命令在各自终端运行，Ctrl+C 停止对应实例；本次交付按用户要求保持两者运行。不要在已有端口监听时重复启动。后台启动命令曾被自动安全检查以 `blocked by policy` 拒绝，最终使用上述受控进程命令成功启动，没有更改安全策略或绕过认证。

这两个本地启动器关闭真实模型和文献网络调用，使用原有 dry-run 行为和已有教学内容；实时来源被明确拒绝，不向旧项目注入测试论文或伪装成功。它们适合查看、演示和离线体验，不代表新提交的真实检索／生成已获得授权。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 新注册专项及原账号隔离 | `tests/test_local_registration.py` 6 项 + `test_local_account_isolation.py` 10 项，**16 passed**，见 [XML](local-account-isolation/registration-results.xml)。覆盖 8 位密码、字符支持、常见弱密码、确认一致、邮箱唯一、未验证状态、限流持久化、CSRF、默认关闭／远程不开放、新用户空项目列表，以及原越权、会话、下载与任务归属 |
| 受影响既有回归 | `test_conversation_home.py`、`test_project_conversations.py`、`test_p1_recovery_attribution.py`：**77 passed**，见 [XML](local-account-isolation/registration-regression-results.xml)。不与前轮的 249 项重复累计 |
| 注册实际浏览器 | [结果](local-registration-dual-entry/browser-results.json)：两个独立上下文分别注册并登录，密码恰好 8 位；B 无权读取 A 项目；重复规范化邮箱拒绝。桌面与 390px、零脚本异常、零浏览器外部请求，未保存截图中的密码或会话令牌 |
| 实际运行入口 | [结果](local-registration-dual-entry/live-entry-results.json)：8001 匿名项目列表成功，原项目数为 13；8077 匿名读取项目为 401，注册按钮可见，下限为 8。此脚本只读，没有在实际账号库中注册测试账号 |
| 旧数据及账号保留 | [数据核对](local-registration-dual-entry/data-check.json)：启动与读取验证前后，旧项目 77 个 DB／JSON／Markdown／文本等文件的 SHA-256 完全一致，变化数为 0；原 1 个账号的 ID、散列和未验证状态一致。详细文件清单仅放在本机 runtime 目录，未公开项目内容 |
| 静态检查 | Python 相关模块编译及 local-auth.js 的 `node --check` 通过 |

专项测试环境均为临时 SQLite／文件和离线边界。测试用 8087 服务已正常关闭并清理自己的临时目录；不向 8001 的原项目或 8077 的实际账号库写入测试记录。已有账号测试中两条原密码边界断言按新需求调整为 7 位拒绝和 256 位有效密码接受，未删除断言或取消服务端校验。

复跑命令：

```powershell
venv/Scripts/python.exe -m pytest tests/test_local_registration.py tests/test_local_account_isolation.py -q --disable-warnings
venv/Scripts/python.exe -m tests.local_account_test_server --port 8087 --allow-registration
# 另一个终端，需本机 Playwright / Edge：
node tests/local_registration_browser.cjs
# 只读核对运行中的双入口：
node tests/local_dual_entry_browser.cjs
```

本机 Playwright 使用既有运行时 `NODE_PATH=C:/Users/czh05/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`。8087 测试结束后调用测试模块的 `/__account__/shutdown`；产品的 8001／8077 不包含该测试关闭接口。

## 截图与边界

- [登录页注册入口](local-registration-dual-entry/login-with-register-desktop.png)
- [注册桌面](local-registration-dual-entry/register-desktop.png) · [390px](local-registration-dual-entry/register-narrow.png)
- [8001 实际旧项目演示](local-registration-dual-entry/live-demo-8001-desktop.png)
- [8077 实际注册页面](local-registration-dual-entry/live-account-8077-register-narrow.png)

上述截图已实际打开查看。当前仅本机可用，不是公网注册服务或安全认证结论。尚未进行真实邮箱验证、完整泄露密码库核验、账号找回、生产部署和真实移动设备验证。

本轮真实模型调用、真实文献来源调用、真实邮件调用均为 **0**；未提交、推送或部署。8002 未启动。8001／8077 按此次明确请求继续运行，分别用于旧项目演示和用户账号工作台。

## 密码可见性按钮补充

按用户追加要求，在登录密码、注册密码和确认密码旁加入独立眼睛按钮。默认隐藏，由用户点击切换显示／隐藏；按钮不提交表单、不改变密码值、不保存可见状态。切换登录／注册页面和提交完成后恢复隐藏。提供明确的显示／隐藏可访问名称、键盘操作和 44px 点击区域。

实际 Edge 验证了鼠标／键盘切换、两字段独立、内容不变、切换表单恢复隐藏，以及桌面和 390px 无横向溢出；只使用未绑定账号的合成输入，未读取或展示用户密码，API 写入为 0。见 [结果](local-registration-dual-entry/password-eye-results.json)、[桌面](local-registration-dual-entry/password-eye-desktop.png)、[390px](local-registration-dual-entry/password-eye-narrow.png)。JS 语法检查通过。只修改静态前端，无需重启两个服务；刷新账号页面后生效。

### 登录与注册页公开展示密码要求（2026-10-28）

登录、注册及返回登录时均直接显示密码要求：8–256 个字符，支持大小写字母、数字和特殊符号；建议组合使用，避免常见、已泄露或重复使用的密码；说明部分常见弱密码及全相同字符密码无法注册。保留现有后端校验，未声称已实现完整泄露密码库检测。密码与确认密码通过 aria-describedby 关联说明，保留眼睛按钮。

验证：针对运行中的 8077 服务，以独立匿名浏览器上下文检查 1440px、390px 下的登录、注册、返回登录，共 6 个页面状态。说明均可见、无横向溢出、密码默认隐藏；JavaScript 语法检查通过。浏览器禁止所有写请求，本次 API 写入为 0，未创建账号或输入真实密码，未重启服务。实际查看桌面登录与窄屏注册截图。

证据：`local-registration-dual-entry/password-rules-results.json` 及同目录 `password-rules-*.png`。本次仅界面说明验证，不与此前账号安全回归重复累计。
