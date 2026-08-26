# Feishu Codex Bridge Skill

## 中文

这是一个可复用的 Codex Skill，把飞书/Lark 私聊、群聊 `@` 和群话题消息转发
到真实、可见、可继续的 Codex Desktop 项目任务。

它是服务台，不是第二个聊天机器人：Listener 负责接收、鉴权、绑定和持久排队；
两分钟 Gateway 调度 heartbeat 只负责在一个专用的 Desktop Gateway 现有任务中
产生自动化回合。只有存在工作时，Gateway 才在同一回合领取并调用目标 Codex
任务；Listener 最后把目标任务的最终答案回传飞书。

当前开发优先级：P0 是打通“目标 Codex turn 的最终回复 -> Listener -> 飞书”；
P2 才是等待不同官方 Desktop build 重新验证原生 final 字段与 scheduler 硬上限。
P2 不再阻塞 P0，但既有 build/surface 失败标记仍保留。

```text
飞书 -> Listener/SQLite -> durable queue
Gateway scheduler heartbeat（无飞书正文）
  -> 精确的现有 Gateway task
  -> sentinel-probe（仅数量/代次/registration/租约元数据）
  -> 空：DONT_NOTIFY
  -> 有：同一回合 fenced claim -> arm -> 目标任务
  -> UserPromptSubmit/Stop Hook 精确关联并暂存最终回复
  -> exact wait/status -> queue result
  -> Listener -> 飞书最终回复
```

### 为什么改成单任务 Gateway

旧架构由独立 Sentinel 通过 `send_message_to_thread` 唤醒另一个 Router。实测在
部分 Codex Desktop 运行面中，自动化来源回合拥有任务协调工具，而被二次唤醒的
Router 回合拿不到这些工具，导致“队列已唤醒但无法恢复、创建或发送目标任务”。

新版保留两边优点：

- 空轮只读队列元数据，不读取飞书正文；
- scheduler heartbeat 固定指向一个现有专用任务，不会每轮新建会话；
- 真有消息时才领取内容和调用模型工具；
- 探测、claim、目标转发和完成都在同一自动化来源回合；
- generation、wake lease、fencing token、幂等和未知结果不重放全部保留；
- 完成真实工作后仅做一次最长 20 秒 grace claim，兼顾突发消息与成本。

这里有两类不同的 heartbeat：scheduler heartbeat 产生 Gateway 回合；helper 的
`heartbeat` 子命令只在已领取工作时续租。当前两分钟 scheduler 同时承担主触发和
恢复扫描。只有 Codex 将来提供官方、精确指向现有任务且保留同等工具面的按需唤醒
后，才把无正文事件 wake 作为主路径，并将 scheduler 降为低频 watchdog；Gateway
与工作续租都保留。详见
[`references/codex-wake-strategy.md`](./references/codex-wake-strategy.md)。

首次激活只用于有限 canary：仅在 Desktop 能精确保存并回读三次上限时才可激活；
不支持硬上限就保持暂停，有人监督或承诺稍后暂停也不能替代硬上限。应先确定 `/init`
中将选择的精确目标任务，并只在用户在线、能于有限窗口内完成菜单选择与确认时启动。canary
成功不等于允许常驻；生产 always-on 必须先单独批准在暂停状态下修改 recurrence，
精确回读后，再用一次新的批准激活。

### 核心边界

- 目标任务独占上下文、项目、模型、推理、审批、Skills、插件、浏览器、
  Computer Use、文件和知识库。
- Gateway 只使用 Desktop 任务协调工具，不执行用户工作，不检索知识，不重建
  历史，不覆盖目标模型或推理。
- Python Listener 不查找/启动 `codex.exe`，不启动 App Server，不调用
  `thread/resume`/`turn/start`，不修改 Codex 数据库或 rollout。
- Desktop 工具可能延迟加载。Gateway 在 claim 后直接调用顶层
  `mcp__codex_app` 方法；禁止从 `functions.exec`、`ALL_TOOLS` 或动态别名
  间接调用。直接方法不可用时失败关闭，不使用兼容回退。
- 不再创建独立 Sentinel 和 Router，也不发送 `<feishu_router_wake>` 二跳消息。
- Obsidian/RAG 属于目标项目；Bridge 不安装、索引、查询或拼装知识库。

### 能力

- `im.message.receive_v1` 常驻监听，支持私聊、群聊 `@`、群话题隔离。
- SQLite inbox/outbox、同作用域 FIFO、跨作用域并发、回复重试和崩溃恢复。
- `/init` 是唯一飞书斜杠命令：按项目列出任务名称和完整 ID，并对话式完成选择、
  新建、归档视图、压缩、解除连接、回复设置与受控项目创建；旧命令不执行。
- 目标被归档/删除且明确未送达时，在同作用域同项目最多自动替代一次；未知结果
  永不重放，也不会形成重复新建风暴。
- 新建、恢复和压缩只把 Gateway 明确成功归档的请求内任务计为已归档；缺失、失败、
  未请求或当前目标 ID 都不从请求列表推断成功。
- `/compact` 原样送入目标任务；Bridge 不生成替代摘要。
- 多行 Markdown 通过逐行 Feishu `post` JSON 发送。
- 图片、音频、视频、文件以受控只读本地引用交给目标任务检查；当前 Desktop
  文本发送接口不等于原生 typed-media。
- `bridge init` 只增量合并 Skill 管理的 `AGENTS.md` 区块。

### 安装来源状态

当前公开仓库 `main` 仍是旧 App Server/Obsidian 架构，不能作为本版单 Gateway Skill
的安装源。发布前必须先同步完整发布清单，验证无凭据、运行态和本机路径，再创建不可变
tag/commit；只有该版本通过验证后，README 才会恢复远程安装命令。

在此之前，只能使用经过本地 `bridge validate` 和发布清单检查的源码包；不要把当前
源码与公开仓库 `main` 混装，也不要复制 `.codex`、日志、队列、会话映射或附件。

### 冷启动前提

- Windows 10/11、Codex Desktop、PowerShell 5.1+、Python 3.10+、Node/npm/npx，
  以及可独立运行的官方 Codex CLI（例如 npm 安装产生的 `codex.cmd`）。WindowsApps
  中 Desktop 包内的 `codex.exe` 不算独立 CLI；不要修改其 ACL 或复制二进制。
- Desktop 能提供任务协调和 automation 工具；缺少时只能排队，不能宣称端到端
  Gateway 已可用。
- 一个启用机器人能力的飞书/Lark 自建应用，由用户亲自批准权限、事件和登录。

首次使用时，Skill 应先运行只读 `bridge preflight`。如果缺少 Python、Node/npm/npx、
可独立运行的官方 Codex CLI 或 `lark-cli`，应列出缺失项、官方来源、安装范围、可能的 PATH/重启影响和随后一次
`feishu configure`，并请求一次明确的 onboarding 授权。用户同意后，Skill 自动安装
本次列出的缺失依赖并重新运行 preflight；验证成功后无需再次询问，后台启动一次
`lark-cli config init --new`，原样展示官方配置链接并生成 PNG 二维码，由用户亲自完成
PersonalAgent 创建。随后按
[`openclaw-common-chat`](./references/openclaw-common-chat-permissions.md)
发起一次 `--recommend` 常用用户权限扫码；由用户亲自批准，Skill 在用户回复完成后
续上对应 device code。二维码不能完全替代 Bot tenant scope 的后台声明/管理员审批。
`--recommend` 会随 CLI 版本变化，可能包含大量跨域写 scope；用户以飞书授权页
展示的准确清单为准。用户不想用 JSON 时优先走后台界面逐项配置。该授权
不是 Skill 自我同意，也不包含上述范围外权限、Bot 租户权限修改、管理员/浏览器代办、
应用发布、`bridge init`、Listener 挂载、Codex 重启、Gateway 或 scheduler heartbeat。

普通 `bridge preflight` 不检查飞书 Windows 客户端。只有用户明确询问接管飞书
前端、操控客户端或真实会话自动测试时，才读取
[`references/feishu-desktop-client.md`](./references/feishu-desktop-client.md)
并运行 `feishu desktop-status`。若缺失且用户批准，运行
`feishu desktop-install -DesktopInstallConsent`：从
[飞书官方下载页](https://www.feishu.cn/download)的当前元数据获取安装包，只接受
官方 CDN，校验官网哈希和 Authenticode 签名，静默安装并清理临时包。随后启动一次
客户端，在二维码/账号登录页停下，由用户本人完成认证。安装/登录不等于同意发送
测试消息。

独立安装器和下载归档只允许暂存在系统临时目录的独立子目录；验证、拒绝、失败或
取消后立即清理，不能写入 Skill 或目标项目。

Windows 下若 `lark-cli.cmd` 遇到含 `&` 的官方 URL，不能把 URL 继续交给该 shim；
应使用已验证的 `node.exe` 和同一已安装官方 CLI 的 JavaScript 入口，以真实参数数组
生成二维码，避免 shell 改写链接。
二维码属于一次性运行态，应放在系统临时目录的独立子目录中，配置结束后删除，不能
写入 Skill 或目标项目。

### 安装与挂载

先按照
[飞书 CLI 官方安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md)
配置 CLI：

```powershell
npm install -g @larksuite/cli
npx -y skills add https://open.feishu.cn --skill -y
lark-cli config init --new
lark-cli auth login --recommend --no-wait --json
lark-cli auth status --json --verify
```

首次权限使用
[`openclaw-common-chat`](./references/openclaw-common-chat-permissions.md)
QR-first 档：PersonalAgent 扫码创建 + CLI 常用用户权限。`auth login` 是用户 OAuth，
不等于 Bot 租户权限；即使再加 `--domain im` 也不能平替 Bot 后台 scope 配置。
Bot 缺 scope 时使用 CLI 返回的 `console_url`；用户不想用 JSON 时走后台界面。运行时仍
默认 locked allowlist 与群 @ 提及门禁。即使 OAuth scope 已授予，加急、撤回、
成员/管理员/禁言等高影响操作仍需逐次确认。
验证时分别报告 user OAuth、Bot 凭据和 Bot tenant scopes；后者使用显式
`lark-cli api GET /open-apis/application/v6/scopes --as bot`，不能拿 user scope 输出代替。

CLI 安装不等于同意常驻监听。明确同意挂载后按以下顺序执行；只读验证不需审批，
后续写入、信任和生命周期动作逐项审批：

审批交互按“一个精确动作、一次询问、自动验收”压缩：先完成所有只读发现、路径解析和
源码准备；一次同意自动包含该动作的命令渲染、等待、进度更新及只读 status/doctor/hash/
manifest/队列或交易核验。源码编辑、文档同步、AST、静态验证和外部命令准备不再逐步询问。
多检查点流程会预告后续必需审批，但当前回复只授权一个明确动作；升级与启动、停止与回滚、
Gateway/自动化生命周期以及每张一次性人工票据等既有隔离边界不会合并。

1. `bridge init` 增量合并项目规则；
2. 首次 `bridge install` 作为一次完整披露的不可拆 bootstrap，安装 runtime、两个
   lifecycle hooks、初始 `bridge.env`、manifest 与 Bridge hook 注册；不改项目规则；
3. 另行批准并运行 `bridge access -AccessMode locked -OwnerOpenId <ou_...>`，只加入
   用户明确选择的管理员、用户和群 ID；新安装显式写入 locked，缺失 access key
   仍按 locked 处理，非法或空的已知布尔、枚举或整数值会让启动失败；空 allowlist
   拒绝全部事件，显式 legacy `compat` 不得用于 canary 或生产；
4. 另行批准安装并启用仓库内 `feishu-codex-final-return` 插件，再另行运行
   `bridge final-return-register`，把插件限定到当前完整性 manifest 有效的已安装 runtime；
5. 只读验证 `.codex/hooks.json` 是无 BOM UTF-8 的 matcher-group 数组，且
   `SessionStart` / `SessionEnd` 各只有一个精确 Bridge handler；再经单独批准，在
   可见 Codex hooks 审查面逐项信任两个准确 Bridge lifecycle hash，以及插件准确的
   `UserPromptSubmit` / `Stop` Hook；不得使用 Trust all；
6. 渲染 [`assets/feishu-router.rules.template`](./assets/feishu-router.rules.template)
   为固定路径 allow rule；
7. 单独批准重启 Codex 以加载规则和插件；
8. 不覆盖模型和推理设置，使用
   [`assets/desktop-gateway-bootstrap.md`](./assets/desktop-gateway-bootstrap.md)
   创建一个专用 Gateway 候选；首轮只检查八个任务工具并以不大于 50 的显式上限
   调用一次 `list_threads`，不得注册或碰队列；
9. 渲染并发送
   [`assets/desktop-gateway-task.md`](./assets/desktop-gateway-task.md)，注册该
   task/host；挂载回合不得探测或领取队列；
10. 创建指向该现有任务、每 2 分钟运行、初始暂停的 scheduler heartbeat，短提示词来自
   [`assets/desktop-gateway-heartbeat.md`](./assets/desktop-gateway-heartbeat.md)；
11. 仅当该官方 Desktop surface 没有已知终止标记，才单独批准一次有硬上限的
   canary；用户须在窗口内完成 `/init`、选择并确认预先声明的精确目标，再发送一条
   普通测试消息；以 exact-scope binding 和目标 final 判定成功，随后保持暂停/完成。进入生产还需两次后续批准：
   先在暂停状态修改并回读 recurrence，再激活已核对的 recurrence。

不要仅为速度给 Gateway 强制指定轻量模型。若某个明确 build 的真实
automation-origin canary 已以 `target_tool_unavailable` 终止，该 build 必须保持暂停；
改 prompt、模型、任务或 scheduler 形态都不构成新 surface。只有明确不同的官方
Desktop build/surface 才能重新执行普通 turn 预检和有限的真实 `/init` 选择 canary。
如果用户当前只想验证绑定，可让 scheduler 继续暂停，改用
[`assets/desktop-gateway-manual-cycle.md`](./assets/desktop-gateway-manual-cycle.md)
的一次性票据通道：控制任务每轮单独取得批准并签发绑定 task/host/operation 的短期
票据，Gateway 最多领取一个匹配请求，不追单，完成后释放。它不会清除 build 标记、
刷新 scheduler freshness 或证明生产兼容性。
只返回列表、未知斜杠命令提示和 aggregate `completed` 均不能证明绑定成功；以
exact-scope binding 和普通消息的目标 final 为准。具体历史证据只保存在
[`HANDOFF.md`](./HANDOFF.md)。

创建、挂载、注册、自动化创建/改目标、激活、部署、重启都属于不同审批点。
安装器不会暗中创建 Desktop 任务或自动化。

### 飞书对话式设置

`/init` 是唯一支持的飞书斜杠命令。它在 Listener 内存中创建十分钟的有界快照，
磁盘只保留数字过期标记，不保存任务标题或本机路径：owner/admin 可查看
Desktop 各项目的非归档任务；其他授权作用域只看本作用域曾使用的精确任务 ID。
首页显示项目、任务名、完整 ID，并提供“新建任务”“查看归档”“设置回复”
“查看状态”，已连接时再提供“压缩当前任务”“解除连接”。所有会影响客户端的
选择都需再回复“确认”。除 `/init` 外的斜杠输入统一拒绝，既不执行也不转发到
目标任务；没有别名或旧命令兼容层。完整契约见
[`references/feishu-command-ux.md`](./references/feishu-command-ux.md)。

Desktop 当前要求 `create_thread` 有非空初始提示词。向导新建任务使用最小“路由就绪”
bootstrap，第一条真实飞书任务作为下一轮；旧任务保留，不强制刷新侧栏。

向导内“新建项目”默认关闭，且必须精确匹配 Desktop `list_projects`。未注册时返回
`project_not_registered`，只回收刚创建的空目录，不退回默认项目。

### 诊断、测试与迁移

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge preflight -ProjectRoot <project-root>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge validate -ProjectRoot <project-root>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge doctor -ProjectRoot <project-root>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge status -ProjectRoot <project-root>
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge status -ProjectRoot <project-root> -Json
```

`bridge preflight` 只读汇总 PowerShell、Python、Node/npm/npx、`lark-cli`、
Skill 静态完整性和本地挂载状态；它不会安装、写配置或启动 Listener。缺少必需
前提时返回非零退出码，并提示 Codex 在取得依赖引导授权后自动补齐依赖，便于新
环境和 CI 准确识别阻塞项。

`bridge status`、`bridge doctor` 与 `bridge validate` 可追加 `-Json`。每次只输出一个
压缩的 `schema_version=1` 对象，并保留默认人类文本输出。机器合同将已安装 manifest
版本与可能过期的 Listener health snapshot 版本分开；只暴露状态、计数、稳定 issue
code 和必要版本，不输出飞书/Codex 任务 ID、消息/提示/答案、凭据、allowlist 值或
本机路径。`doctor -Json` 或 `validate -Json` 的 `status=fail` 返回退出码 2；
`validate -Json` 仍只做静态门禁，不启动测试或其他子进程。

安装或升级会生成运行时完整性 manifest，绑定已安装 Python 文件与两个 lifecycle
hook。`bridge start` 还会要求当前 Skill 源码与已安装副本一致；SessionStart 在创建
lease 前再次验证 manifest。缺失、过期或哈希不一致时一律拒绝启动。`bridge access`
只更新 `bridge.env` 中的访问模式和显式传入的 allowlist，不安装代码、不改 hooks、
不合并项目规则；应用访问策略所需的重启仍须单独审批。

首次 `bridge install` 是唯一明确披露的不可拆 bootstrap：runtime、两个 lifecycle
hook、初始 `bridge.env`、完整性 manifest 与 Bridge 自己的 `hooks.json` 注册；它不
合并项目规则。此后公开 `bridge upgrade` 只升级 runtime。旧 start hook 尚不认识
manifest 时，先在 Listener 已停止且单独批准后运行 `bridge hooks`；它只更新 Bridge
hooks/注册并使旧 manifest 失效，不签署新 runtime。随后另行批准 `bridge upgrade`；
hook 信任审查和重启仍各自独立。

禁止从 Codex Desktop 内运行动态 Bridge 测试。只可在 Listener 已停止的外部终端
或 CI 中执行。

外部 P0-B 必须使用 `scripts\invoke-external-p0b-once.ps1`，并同时取得 supervisor
envelope 与独立 semantic validator 的 `pass`。P3 只能在同版 P0-B 通过后运行：使用
`scripts\invoke-external-p3-soak-once.ps1` 绑定该 P0-B 文件与 SHA-256，默认重复固定
十个并发/恢复/投递场景 25 轮，硬超时 300 秒。P3 复用 P0-B retained snapshot，禁止
子进程与 Desktop/Feishu live contact；它验证本地耐久性，不代表当前 Desktop build
已经具备 target final 回传能力，也不授权启动 Listener 或激活 Gateway。完整合同见
[`references/p3-bounded-soak.md`](./references/p3-bounded-soak.md)。P0-B 还会从 supervisor
与独立 validator 中提取路径链 helper，分别用普通文件和目录做回归探针，避免 P3 到场
后才发现 `FileInfo`/`DirectoryInfo` 父级遍历差异。P0-B 还会在隔离解释器中确认子进程
guard 保持 `Popen` 的类语义、允许 Python 3.13 `asyncio` 导入，并在构造阶段、真正创建
进程前拒绝调用和计数。独立 validator 的首次 pinned-handle 调用也由 P0-B 用空
`List[FileStream]` 与零字节文件做真实绑定回归，避免 PowerShell 将合法初始空集合拒绝。
时间关系门禁同时兼容 PowerShell 将 JSON date-time 保留为字符串或预先转换成 `DateTime`
的两种行为，直接保留 7 位小数 ticks，不再经过会丢精度的文化字符串格式化。
若要连续完成两个门禁，使用 `scripts\invoke-external-p0b-p3-once.ps1`；它只在 P0-B
supervisor 与 validator 都通过后启动 P3，成功只输出一个汇总 JSON，失败时保留子阶段
work/evidence 路径和完整诊断，不再由外层命令覆盖成单独的退出码。

`4.2.0-alpha.26` 是当前控制面、飞书命令体验、机器可读诊断与 P3 有界 soak 契约。旧的独立 Sentinel/Router 在线架构不会因
源码更新自动改变。迁移必须分别审批：暂停旧自动化、选择或创建 Gateway、挂载并
注册、创建或改指向且保持暂停、执行一次有界 `/init` 选择 canary 并再次暂停；只有
成功后，才可另行审批生产 recurrence 的修改/readback 与激活。

### 发布边界

只发布 Skill 文件。不得提交知识库正文、服装资料、Obsidian vault、运行状态、
日志、队列、会话映射、附件、凭据、Token、租户 ID 或本机路径。

## English

This reusable Codex Skill forwards Feishu/Lark direct messages, group mentions,
and group topics into real, visible, continuable Codex Desktop project tasks.

It is a service desk, not a second agent. The Listener receives, authorizes,
binds, and durably queues work. A two-minute Gateway scheduler heartbeat creates
a cycle in one existing dedicated Desktop Gateway task. Empty cycles inspect
metadata only; non-empty cycles claim, route to the canonical target, wait, and
finalize in that same automation-origin turn. The Listener relays only the
target's final answer.

### Single-task Gateway design

The former separate Sentinel-to-Router wake added a delegated hop. Some Desktop
surfaces expose task-coordination tools to the automation-origin turn but not to
the Router turn woken through `send_message_to_thread`. The new design removes
that unsupported hop while preserving:

- metadata-only idle probes;
- one existing control task instead of one new task per run;
- durable generation, wake lease, fencing, idempotency, and no replay after an
  uncertain target start;
- irreversible per-request replay decisions plus a deterministic next generation only for
  an explicit safe failure (`retryable=true`, `may_have_started=false`);
- archive results limited to explicitly requested task IDs whose Desktop archive
  calls succeeded; missing or echoed request IDs never imply success;
- a zero-wait first claim and one bounded 20-second burst grace claim.

The scheduler heartbeat and the helper subcommand `heartbeat` are different:
the former creates Gateway cycles; the latter only renews an active-work lease.
Today the two-minute scheduler is both primary trigger and recovery sweep. Only
after Codex exposes an official payload-free wake for the exact existing task
with the same tool surface should that wake become primary and the scheduler
become a low-frequency watchdog. The Gateway and work-lease heartbeat remain.
See [`references/codex-wake-strategy.md`](./references/codex-wake-strategy.md).

The first activation is a finite canary. Activate a three-run cap only when
Desktop preserves and reads it back exactly; otherwise keep the scheduler
paused. Then count the actual automation-origin turns; any fourth run proves
`scheduler_cap_unenforced` and blocks that exact build/surface. Supervision or a
promised later pause is not a substitute for a hard cap. Predeclare the exact
target for the `/init` catalog-and-selection flow and
start only while the owner can finish its selection and confirmation inside the
finite window. Canary success is not
always-on consent. Production requires one approval to change the recurrence
while paused, exact readback, and a new approval to activate it, with cadence
and model/context-cost disclosure before either action.

When the immediate goal is functional binding rather than scheduler release
qualification, keep the scheduler paused and use the owner-present prompt in
[`assets/desktop-gateway-manual-cycle.md`](./assets/desktop-gateway-manual-cycle.md).
Each approved, task/host/operation-bound ticket permits at most one matching
request, no grace claim, and an explicit release. This does not clear a build
marker, refresh scheduler freshness, or certify production compatibility.

After two separately approved deliveries on one exact Desktop build prove
intact Unicode input and same-target context continuity but expose neither a
`latestAssistantMessage` nor any `read_thread.items`, record
`target_final_readback_unavailable` and stop repeating native
`latestAssistantMessage` diagnostics on that build. Only a positively different
official Desktop build may repeat that native canary. The marker does not block
one separately approved canary of the materially different exact-turn Hook
transport after same-source P0-B/P3, runtime deployment, plugin enablement,
runtime registration, and exact Hook trust pass. That canary uses the one-ticket
manual lane, leaves the scheduler paused, and does not clear the build verdict.
UI, database, transcript, rollout, App Server, OCR, and clipboard extraction are
not reply paths.

`4.2.0-alpha.30` keeps exact reply return as a separate P0 transport and moves
Desktop task coordination to top-level direct `mcp__codex_app` calls. The Gateway
takes one zero-time exact-target `wait_threads` snapshot, arms the fenced claim,
then sends once. The plugin's structured `UserPromptSubmit` Hook binds only the
matching task/turn and either the raw prompt hash or a strict Desktop delegation
wrapper from the Gateway pinned at arm time whose inner input has that hash;
`Stop` captures only that bound turn's latest
final into fenced staging. After exact turn completion, the Gateway accepts a
matching Hook receipt, or a native same-turn `latestAssistantMessage` only after
`final-return-native` fences a late Hook. It repeats only exact wait/status for
at most 20 seconds and never re-sends. The send result, baseline message,
`read_thread`, transcript, and another task's final are never reply sources.

Targets remain authoritative for context, project, model/reasoning, approvals,
Skills, plugins, browser, Computer Use, files, and knowledge. The Gateway only
uses Desktop task tools and never performs user work or retrieval. The Listener
never launches Codex/App Server or mutates a target via RPC. Obsidian/RAG remains
entirely within the target project.

### Installation source status

The public repository's current `main` branch still describes the legacy App
Server/Obsidian design and is not an installation source for this single-Gateway
version. Before remote installation is documented again, publish the complete
reviewed manifest, scan out credentials/runtime state/machine paths, and create
an immutable verified tag or commit.

Until then, use only a reviewed local/exported source bundle that passes
`bridge validate`. Maintainers must also run the explicit two-component audit
in [`references/release-audit.md`](./references/release-audit.md); it derives
the path count from `assets/release-inventory.json`, emits relative paths plus
raw SHA-256, and refuses unknown files, local IDs/paths, runtime artifacts, or
source mutation during the audit. The exact top-level `plugins` directory and
exact root `.tmp` directory are explicit coexistence boundaries for independent
workspace projects and local tooling: their contents are not part of the Bridge
manifest and are not certified by this audit. Never mix the bundle with public `main` or copy `.codex`, logs,
queues, session maps, or attachments. The host also needs Windows 10/11, Codex
Desktop, PowerShell 5.1+, Python 3.10+, Node/npm/npx, and an independently
runnable official Codex CLI such as the npm `codex.cmd` shim. The packaged
WindowsApps `codex.exe` is not that CLI; do not alter its ACL or copy it.

Automation may append `-Json` to `bridge status`, `bridge doctor`, or
`bridge validate`. Each invocation emits one compact `schema_version=1` object;
the human-readable output remains the default. The contract separates the
installed manifest version from a possibly stale health-snapshot version and
contains only operational state, counts, stable issue codes, and required
versions—never messages, task/Feishu IDs, credentials, allowlist values, or
local paths. A JSON doctor/validate failure exits with code 2; JSON validation
remains a static in-process gate and reports `child_process_started=false`.

Dynamic P0-B evidence is produced only outside Codex Desktop by the audited
clean-PowerShell supervisor. A JSON Schema pass is insufficient: acceptance also
requires the independent semantic validator documented in the release-audit
reference. It rehashes retained TestResult/captures/snapshot and the current
hooks/toolchain/runtime. Both scripts accept only ordinary local-drive paths and
resolve physical Win32 paths before isolation checks. Their output is current-
environment evidence, not a signature or cryptographic attestation.

Use `scripts\invoke-external-p0b-once.ps1` from that external terminal to avoid
partial copy/paste runs: it creates unique work/evidence roots, stops at the
first failure, and invokes the semantic validator only after a valid supervisor
envelope. Its successful first JSON line preserves the supervisor fields and
adds the exact `evidence_path` for a later P3 invocation.

When both gates are required consecutively, prefer
`scripts\invoke-external-p0b-p3-once.ps1`. It keeps each child stderr stream
separate from JSON stdout, starts P3 only after both P0-B outputs pass their
handoff contract, and emits one combined JSON on success. Call the wrapper
directly; do not surround it with `2>&1` capture and a bare exit-code throw.

After the same source has a fresh independently validated P0-B receipt, use
`scripts\invoke-external-p3-soak-once.ps1` for the stopped bounded P3 soak. It
reuses P0-B's retained source snapshot, runs the fixed ten local scenarios for
25 iterations by default under a 300-second hard timeout, forbids child
processes and live Desktop/Feishu contact, and emits a create-new receipt plus
an independent semantic validation. See
[`references/p3-bounded-soak.md`](./references/p3-bounded-soak.md). A pass is
local durability evidence only; it is not a Desktop final-return canary and
authorizes no Listener start or Gateway activation.

The stopped alpha.2 to alpha.4 migration rehearsal is documented in
[`references/p1-isolated-migration.md`](./references/p1-isolated-migration.md).
On a disposable VM it defaults to a create-new project under the current
Windows user; it never creates another account without asking. Fixture
preparation, hook-only refresh, runtime-only upgrade, observation, and
quarantine-based rollback remain separate checkpoints. Run the lab tool only
from an independent terminal, and never let the rehearsal start a Listener,
Gateway, scheduler, or Feishu data-plane request.

Configure the official Feishu CLI and use the exact QR-first
[`openclaw-common-chat`](./references/openclaw-common-chat-permissions.md)
authorization profile for the first permission pass. CLI installation is not
resident-listener consent.

On first use, the Skill runs read-only `bridge preflight`. If Python 3.10+,
Node/npm/npx, an independently runnable official Codex CLI, or `lark-cli` is
missing, it lists the exact missing prerequisites,
official sources, and the following configure step, then asks for one explicit
onboarding approval. It installs only those items, reruns preflight, and, after
successful verification, launches `lark-cli config init --new` once without a
second question. It forwards any official URL unchanged and generates a PNG QR
code with `lark-cli auth qrcode`; the user completes browser actions. It then
runs one split OAuth scan with `auth login --recommend` for the CLI's common
user set and resumes its device code only after the user reports that scan
complete. The recommended set is version-dependent and may include many
cross-domain write scopes; the user reviews the exact Feishu approval page.
User OAuth does not replace Bot tenant-scope declaration or admin
approval in the developer console; the console UI can be used without JSON. This is
not Skill self-consent and does not include browser/admin actions, Bot tenant
scope changes, extra OAuth scopes, app publication, project changes, Listener
mounting, Codex restart, Gateway creation, or scheduler heartbeat activation.

Stage standalone installers and downloaded archives only in a dedicated
system-temp directory. Remove them after verification, refusal, failure, or
cancellation; never write them into the Skill or target project.

On Windows, do not pass an opaque URL containing `&` through `lark-cli.cmd`.
Invoke the same installed official CLI JavaScript entry with the verified Node
executable and a true argument array so the shell cannot rewrite the URL.
Store the one-time QR under a dedicated system-temp directory and remove it
when configuration finishes; never write it into the Skill or target project.

After explicit mount consent, follow this order. Read-only validation needs no
approval; obtain a fresh, separate approval for each write, trust, or lifecycle
action: initialize the project policy; perform the disclosed indivisible first
Listener bootstrap
(runtime, both hooks, initial env, manifest, and Bridge hook registration; no
project-rule merge); separately configure locked access with at least one
validated `ou_...` or `oc_...` identity—fresh installs write `locked`, a
missing access key remains locked, and malformed or empty recognized boolean,
enum, or integer values refuse startup; explicit legacy `compat` is not a
production setting;
 install and enable the repo-local `feishu-codex-final-return` plugin under its
own approval, then separately register the exact manifest-valid installed
runtime with `bridge final-return-register`; read-only validate
`.codex/hooks.json` as a BOM-less UTF-8 matcher-group array
with exactly one precise Bridge `SessionStart` handler and one precise Bridge
`SessionEnd` handler, then, under a separate approval, trust each exact Bridge
lifecycle Hook plus the plugin's exact `UserPromptSubmit` and `Stop` Hooks
through the visible Codex hooks review surface; render the exact
project rule; restart Codex separately;
create one dedicated Gateway candidate with model/reasoning overrides omitted
and the read-only first-turn prompt from
[`assets/desktop-gateway-bootstrap.md`](./assets/desktop-gateway-bootstrap.md);
require one direct `mcp__codex_app.list_threads` call with an explicit limit no
greater than 50 and one direct `mcp__codex_app.list_projects` call; then
mount and register
[`assets/desktop-gateway-task.md`](./assets/desktop-gateway-task.md); create a
paused two-minute existing-task scheduler heartbeat from
[`assets/desktop-gateway-heartbeat.md`](./assets/desktop-gateway-heartbeat.md);
only on a surface without a terminal incompatibility marker, separately
activate a finite canary while the owner completes `/init`, selects and confirms
the predeclared exact target, and sends one ordinary test message; verify the exact binding and target final, then
leave it paused/completed. Production recurrence change/readback and activation
require two later approvals. Never create a second Sentinel/Router or target a
new-chat automation destination.

Do not force a lightweight Gateway model merely for speed. Once a specific
official Desktop build has terminalized a genuine automation-origin canary as
`target_tool_unavailable`, or exceeded its declared finite scheduler count, keep
that build paused: a different prompt, model, task, or scheduler shape is not a
new surface. Only a positively different
official build/surface may start a fresh ordinary-turn check and finite live
`/init` catalog-and-selection canary. Historical evidence is kept in
[`HANDOFF.md`](./HANDOFF.md); Gateway settings never override targets.

### Safety and limitations

- Desktop tools are invoked directly through the top-level `mcp__codex_app`
  server after a fenced claim; missing tools fail closed without
  App Server/shell/database/UI fallback.
- Desktop task send is currently text-only. Media crosses as bounded validated
  local read-only references, not native typed input.
- Wizard-created tasks use a minimal non-empty routing-ready bootstrap because
  current task creation requires an initial prompt.
- Old slash commands cannot interrupt or mutate another task; use `/init`.
- Wizard project creation requires an exact project returned by `list_projects`.
- Dynamic bridge tests must never run inside Codex Desktop; use external CI or
  a terminal with the Listener stopped.
- First `bridge install` is the disclosed indivisible runtime/hooks/initial-env
  bootstrap and never merges project rules. Later `bridge upgrade` is
  runtime-only. A pre-manifest hook migration uses separately approved
  `bridge hooks`, which invalidates the old manifest before the runtime upgrade
  signs a new one. Manual start also requires source/runtime parity; SessionStart
  fails closed on a missing, stale, or mismatched manifest. `bridge access`
  edits only the validated access mode and explicitly supplied access IDs.
- `4.2.0-alpha.30` requires an explicitly approved live migration; source edits
  do not retarget or restart existing automation.

### Publication boundary

Publish only reusable Skill files. Never publish vault notes, fashion-industry
material, runtime state, logs, queues, session maps, attachments, credentials,
tokens, tenant identifiers, or machine-specific paths.
