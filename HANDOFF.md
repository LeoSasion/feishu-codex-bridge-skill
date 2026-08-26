# Feishu Codex Bridge 开发迁移交接

更新时间：2026-08-26  
交接对象：在另一台计算机上的全新 Codex Desktop 项目与新会话  
迁移包基线：`feishu-codex-bridge` 源码 `4.2.0-alpha.30`

## 1. 给新会话的第一条指令

把经过发布 manifest 导出并验证的干净 `feishu-codex-bridge` 包放入新项目的
`skills/`，或安装到新电脑的 Codex Skills 目录。不要复制工作区的 `.codex/`、
`.agents/skills/`、`_retired/`、根 `AGENTS.md`、`skills-lock.json` 或任何运行态；
只保留发布清单明确列出的 `.agents/plugins/marketplace.json`。然后向新会话发送：

> 请先完整阅读 `skills/feishu-codex-bridge/HANDOFF.md` 和 `SKILL.md`，再按
> `references/architecture.md` 核对源码。当前只做静态检查和开发规划，不安装、
> 不启动 Listener、不创建 Gateway、不创建或启用自动化，也不要运行动态测试。
> 每个会影响 Codex Desktop 的动作都必须单独向我申请批准。

不要让新会话从聊天记录猜架构，也不要复制旧电脑的 `.codex/feishu-bridge/`、
Hooks、自动化、任务 ID、飞书登录缓存或项目 `AGENTS.md` Bridge 区块。

## 2. 迁移包是什么

迁移包是可公开复用的 Codex Skill 源码快照，包含：

- `SKILL.md` 与双语 `README.md`；
- `agents/openai.yaml`；
- 单任务 Desktop Gateway 的合同、scheduler 提示词、项目 allow-rule 模板与
  增量 `AGENTS.md` 规则资产；
- 仓库内 `feishu-codex-final-return` P0 插件、marketplace 条目及其精确
  `UserPromptSubmit` / `Stop` Hook；
- Listener、持久队列、飞书消息、绑定、项目路由和安装生命周期的完整 Python / 
  PowerShell 源码；
- 静态和动态测试源码；
- 架构、权限、Hooks 与本交接文档。

迁移包不是旧电脑运行目录的备份，也不是知识库项目快照。

## 3. 明确没有复制的内容

以下内容故意不进入迁移包：

- `.codex/feishu-bridge/` 运行版、备份、数据库、队列、响应和租约；
- `bridge.env`、`sessions.json`、`state.sqlite3`、`wake.sqlite3`；
- Feishu 消息正文、附件、用户/群/话题 ID、Codex 任务 ID；
- 飞书 App Secret、OAuth Token、CLI 登录缓存和机器人凭据；
- Listener 日志、Codex 日志、自动化历史和 Automation memory；
- 旧电脑绝对路径、allow rule、Hooks 信任记录和进程状态；
- Obsidian Vault、服装知识库、转写稿、媒体和任何业务资料；
- 为调研竞品而下载的临时仓库、旧发布目录和旧版打包目录。

新电脑必须重新登录飞书、重新审批权限、重新生成本机路径、重新创建 Gateway，
并重新建立飞书作用域到 Codex 任务的绑定。不要迁移旧会话映射。

## 4. 哪一份是源码真相

本迁移包中的源码是继续开发时的第一依据：

- 源码版本：`4.2.0-alpha.30`；
- 当前优先级：P0 是把目标 Codex turn 的最终回复可靠传回飞书；等待不同官方
  Desktop build 重验原生 final 字段和 scheduler 上限降为 P2，不阻塞独立 Hook
  transport 的开发与有限测试，也不删除已有 build marker；
- 控制面：一个固定的、现有 Codex Desktop Gateway 任务；
- Gateway scheduler heartbeat：直接附着到该 Gateway，只负责产生自动化回合；
  空轮只探测元数据，有工作时在同一
  automation-origin 回合完成 fenced claim、目标转发、等待与完成；
- active-work lease heartbeat：helper 的 `heartbeat` 子命令，仅在已领取工作时
  续租，不负责调度；调度 freshness 默认 300 秒并与工作 heartbeat TTL 分离；
- 诊断保留旧字段并新增 scheduler/work 别名；长任务工作租约仍新鲜时不再因
  scheduled probe 暂停而误报 Gateway 失活；
- Gateway canary 必须有界；Desktop 不能可靠执行硬次数上限时不得激活循环。
  `/init` 中的精确目标必须预先确定且用户在线完成选择、确认和普通消息；成功 canary 也不等于 always-on 同意，
  生产 recurrence 修改与后续激活仍须分开审批；
- 飞书控制面只支持精确 `/init`；其他斜杠输入统一拒绝且不转发，没有别名或旧命令
  兼容层。任务目录快照只在 Listener 内存保留十分钟，`sessions.json` 只写数字过期
  标记，不持久化任务标题、目录标签或未选项目路径；
- 公开 `bridge upgrade` 固定为纯 runtime 升级，不改 hooks、`bridge.env` 或 live
  `AGENTS.md`；旧 start hook 不支持 manifest 时，必须先停止 Listener 并在独立批准下
  运行 `bridge hooks`，再另行升级 runtime；
- 安装/升级写入绑定运行代码与 hooks 的完整性 manifest；手动启动还要求
  Skill 源码与安装副本一致，SessionStart 在创建 lease 前再次校验，异常即拒绝启动；
- 不再使用“独立 Sentinel 唤醒另一个 Router”的二跳架构；
- Python Listener 永远不启动 Codex/App Server，也不直接写目标任务。

公开仓库为：

- <https://github.com/LeoSasion/feishu-codex-bridge-skill>

交接时，公开仓库可能仍落后于本迁移包。旧电脑还存在一个指向
`LeoSasion/feishu-codex-bridge` 的历史临时克隆，它不是本次迁移的权威 remote，
也没有放进迁移包。发布前必须重新核对仓库、分支、完整 staged file list 和
敏感信息扫描；只允许发布 Skill 文件。

## 5. 当前源码、已安装运行面与兼容性记录

2026-08-26 的当前状态把源码、安装面和兼容性证据明确分开：

- 本目录活动源码是 `4.2.0-alpha.30`；本轮修改前只读检查显示已安装 runtime 与
  Listener 均为 `4.2.0-alpha.29`，Listener 正常运行且 Gateway automation 保持
  `PAUSED`。alpha.30 是尚未部署的 delegated-prompt Hook 关联修复，因此当前
  source/runtime parity 预期为 stale；不得在单独批准 upgrade 前重启 Listener，也不得
  把 alpha.29 外部证据当作 alpha.30 的 current-source 证明；
- `feishu-codex-final-return` 已安装/启用，installed runtime registration 与精确
  `UserPromptSubmit` / `Stop` Hook 信任已经完成。最近一次 one-ticket live 诊断证明目标
  turn 正常完成，但 receipt 在完整宽限期内始终停留在 `armed`；Desktop 保存的目标用户
  消息是带已注册 Gateway 来源和原始 input 的 `<codex_delegation>` 包装，而 alpha.29
  只比较整个 Hook prompt 与原始 input 的哈希。alpha.30 只接受精确 raw prompt 或该
  严格包装，并新增无正文 Hook-observation 诊断；runtime upgrade 与下一次 canary 仍保留
  各自审批边界；
- 当前源码的双组件只读发布审计已通过。`HANDOFF.md` 本身属于受审发布文件，因此不得
  把“当前 source manifest 哈希”写回本文件形成自引用；精确哈希必须取自最后一次未再
  修改源码的审计输出。alpha.27 尚不含 P0 registry 子命令，因此新版 dispatcher 的只读
  `bridge final-return-status` 会返回 `status=upgrade_required` 和安装版本，绝不再把旧
  helper 的 argparse 错误误判为插件或 Desktop build 故障；
- 项目 `.codex` 中的安装版本属于动态部署状态，以 `runtime-manifest.json` 和只读
  `bridge doctor` 的 source/runtime parity 为准；停止前留下的 health snapshot 可能
  显示旧版本，不得据此判断当前安装版本。历史停机测试曾正常清除 PID；此后 Listener
  已重新启动，当前 PID 3216 的命令行身份已由只读 `bridge status -Json` 验证为 Bridge，
  飞书 consumer 已连接，Gateway automation 仍保持 `PAUSED`；
- 停止脚本已在真实 Listener 上验证退出期 CIM 命令行暂时不可读的竞态修复：它只在
  进程消失后报告成功，未把“不可核验”当作强杀权限；
- alpha.25 的组合外部套件已通过：P0-B evidence SHA-256 为
  `fb0c36cca8d1612922c5faf70663ac50e7736764b81e99866a289528dbcf7672`，P3 evidence
  SHA-256 为 `f9b020d6e864d574ebd1afd0dc105182b73193b753afdda33d2169863222fbb1`，
  两者绑定的 `source_manifest_sha256` 均为
  `e4102ede33e6d43d2e7dca5bdd8308fa4cee93a1da13db94d170c5f79b85626c`；P3 为
  25 轮、固定十场景、250 个测试。alpha.26 修改了受审源码，必须重新取得自己的
  supervisor/validator 双门禁；

当前 build-keyed compatibility inventory 包含两个独立的官方 Desktop surface
结论：`26.818.5229.0` 的真实 automation-origin canary 终态为
`target_tool_unavailable`；`26.818.8289.0` 的 `COUNT=3` scheduler 实际运行四次，
标记为 `scheduler_cap_unenforced`。两者均保持 `PAUSED`，同一 build 不得因模型、
prompt、Gateway task、上下文或 scheduler 形态变化而重试；只有明确不同的官方
Desktop build/surface 才能重新做普通轮预检和有限 live canary。后者的目录请求还
暴露了旧源码 `limit=100` 与当前 Desktop `list_threads` 最大 50 的合同漂移，详见
第 15 节；修复该参数不能消除 scheduler 硬上限失效。

当前产品方向继续以 Codex Desktop 目标任务作为唯一对话核心；不把独立 SDK/Harness
线程作为飞书会话后端。用户已在当前 Bridge 项目中明确要求并创建预声明 canary
目标任务“刘学森”；精确 task ID 只保留在受控审批会话中，不写入发布包。该任务已
完成最小 bootstrap 并处于 idle，但尚无 Bridge binding。上一次
失败发生在 Gateway 的 `list_task_catalog` 阶段，早于任何目标读取、绑定或消息发送，
因此新目标不能作为同 build 重试理由；待明确不同的官方 Desktop build/surface 通过
新的普通轮预检后，才可在另行批准的 live `/init` canary 中选择此精确 ID。

源码现提供纯只读 `bridge canary-gate`：它识别当前 Desktop package build，并在任何
普通轮预检或 live canary 之前比对匿名 build 级 terminal marker。`blocked` 与
`unknown` 都停止；`pass` 只表示允许进入新版预检，不表示兼容或授权启动。若当前
执行面不能读取进程路径，可在单独披露的只读查询取得精确 build 后通过参数传入，
无需先启动 Listener 或消耗一条飞书消息。

同一 canary 窗口还终态化了一个更早遗留的 `create_thread` 请求：
`target_result_unknown`、`may_have_started=true`。它没有产生 Bridge binding，必须
保持不重放、不自动认领；若 Desktop 侧出现可能由它创建的任务，只能由用户显式
核对和处置。

更早的 Desktop `26.814.5167.0` / runner `0.148` surface 也曾留下同类
`target_tool_unavailable` 终态；它是独立的历史 marker，不应替代上述当前 build 的
实测证据。

因此迁移包只是源码开发快照，不是 production-ready 或端到端验收声明。

## 6. 架构不变量

```text
Feishu event
  -> Python Listener：鉴权、规范化、持久入队
  -> durable Desktop request queue
Gateway scheduler heartbeat（无飞书正文）
  -> exact existing Desktop Gateway task
  -> sentinel-probe：只读数量、generation、registration、lease
  -> empty：DONT_NOTIFY
  -> pending：同一回合 fenced claim
  -> Codex Desktop task tools
  -> canonical target task
  -> bounded wait
  -> target authoritative final
  -> Listener 发送到 Feishu
```

必须长期保持：

1. 目标 Codex 任务独占用户工作的上下文、项目、模型、推理、审批、Skills、
   插件、浏览器、Computer Use、文件和知识库。
2. Gateway 是服务台，不回答、不检索、不总结、不重建历史，也不覆盖目标设置。
3. Listener 不查找或启动 `codex.exe` / App Server，不调用 `thread/resume`、
   `turn/start`，不修改 Codex 数据库、rollout、锁、命名管道或界面。
4. 飞书聊天按稳定 chat/topic ID 绑定精确 Codex task ID，绝不按姓名或群名匹配。
5. 用户原始文本只进入目标任务一次；Bridge 不注入队列信封、日志、RAG 片段、
   本地摘要或重建历史。
6. `/compact` 原样交给目标任务；Bridge 不自行制作摘要。
7. 不确定目标是否已经开始时设置 `may_have_started=true`，永不自动重放。
8. 空 scheduled cycle 不读取飞书正文；有消息时同一回合探测、领取和路由，不唤醒
   第二个控制任务。
9. Obsidian 属于目标项目，只有用户明确提出知识库需求时才单独配置；Bridge
   自身没有知识库路径、索引器或检索器。

调度 heartbeat 与 helper 的 active-work lease heartbeat 是两个机制；后者只在
fenced claim 后续租，不负责唤醒。详细协议以 `references/architecture.md`、
`references/codex-wake-strategy.md` 和
`assets/desktop-gateway-task.md` 为准。

## 7. 为什么放弃独立 Sentinel + Router

二跳方案会把 automation-origin 工具资格丢在被唤醒的 Router 之外。4.2 因而把
metadata probe、fenced claim 与目标路由合并到同一个现有 Gateway 回合，同时保留
空轮只读、幂等、租约、未知结果不重放、一次 20 秒 grace claim 与每轮八条上限。
claim 前等待超时不是终态：queue request 与 Feishu event 继续用同一 idempotency
key 保持 retryable，控制命令不能把 durable pending 误报为已交付失败；
`/init` 中经确认的新建项目动作只允许同一 event 恢复其精确暂存目录，新 event 不得接管该目录或覆盖其暂存标记。
每个物理请求只有一个 first terminal outcome；retention 可以把过期答案正文不可逆地
降为 non-retryable unknown，但绝不能再变回成功或可重试。只有明确
`retryable=true`、`may_have_started=false` 的安全失败才推进同一逻辑操作的确定性
retry generation，目标生命周期失败和未知结果都不推进。
`inspect_thread` 是唯一只读例外：独立的 300 秒 abandonment TTL 到期后可写入
`router_read_claim_expired` 并安全推进；mutating claim 仍保留 7200 秒不确定性窗口。
这项清理由 Listener maintenance 完成，metadata-only `sentinel-probe` 不读取 claimed
payload。对应短 TTL 与 mutating 长 TTL 的对照测试已绑定到 P0-B F08。
终态 receipt 是不可由普通 retention 删除的幂等 tombstone；response、terminal
claimed 和 staging 只是可清理缓存。因此中途崩溃和长时间离线都不会丢失 retry ancestry，或把
同一 mutation 从 generation 0 自动重放。
完整理由只维护在 `references/architecture.md`；不得重新引入二跳 Router。

## 8. 已验证限制与剩余风险

### 8.1 Hooks 兼容性规则（已修复）

源码已固定为 BOM-less、array-shaped `SessionStart` / `SessionEnd` matcher groups，
并只替换 Bridge 自己的条目。`validate` / `doctor` 负责结构、唯一性、路径和状态诊断；
详细不变量见 `references/permissions-and-hooks.md`。Hook 内容变化会改变信任哈希，
必须在可见 `/hooks` 审查面逐项复核，不能把该命令发到普通任务输入框。

### 8.2 4.2 尚未完成独立动态验收

最新源码已通过静态验证时才能打包，但动态测试不得在 Codex Desktop 内启动。
新电脑必须在外部终端或 CI、Listener 停止且与正常 Desktop 工作隔离时运行。

### 8.3 Gateway 的工具可见性必须实测

普通轮能发现八个任务工具，不证明 automation-origin 回合可调用它们。首次候选
省略模型/推理覆盖，用 bootstrap 做普通轮预检；通过后仍须在不同、未终止的官方
surface 上完成有限 `/init` catalog-selection canary。工具缺失即失败关闭，不得回退 App Server、
Shell、数据库或 UI 自动化。

### 8.4 多模态仍是受控引用，不是原生 typed input

当前 `send_message_to_thread` 只有文本 `prompt`。附件会先被 Listener 下载并
校验，然后以有界、只读 manifest 附在原文后，由目标任务决定是否读取。不要宣传
为原生图片/音频/视频输入。输出媒体能力也必须经过项目路径、类型、大小和权限
校验后单独实现。

### 8.5 当前能力限制

- Gateway 不能跨任务直接中断目标；应提示用户在 Desktop 停止目标。
- `create_thread` 需要非空初始提示，故 `/init` 向导的新建任务动作先创建最小
  routing-ready bootstrap，再发送第一条真实请求。
- Desktop 侧栏刷新可能异步，禁止通过数据库、deep link 或 UI 自动化强制刷新。
- 首次 Gateway 候选必须省略 model/reasoning override。用户明确要求的模型变化只在
  已有 Gateway 保持暂停、完成 `REHYDRATE_EXISTING` 后走普通轮 model-preflight；
  它不能重试已被同一 Desktop build 终止的 automation-origin canary。
- 内部类名和协议字段仍含 `Router` / `router_thread_id` / `sentinel-probe`，这是
  schema v4 稳定字段，不代表仍有两个控制任务。
- Codex Desktop 的高优先级工具调用规则可能强制 Gateway 在工具前写一条内部
  commentary，scheduler 提示词无法可靠禁止。只允许不含正文、附件名、路径、ID、
  工具细节或推理的通用状态句；Listener 永不转发它，完整 final 仍必须严格为
  `DONT_NOTIFY`。
- 飞书 Electron 窗口在部分 Windows 主机上会让截图捕获报
  `SetIsBorderRequired ... 0x80004002`。刷新唯一窗口后仍失败时，只允许再做一次
  accessibility-text-only 观察；它可能暴露可见聊天列表，必须最小化读取且不得
  持久化无关聊天。仍失败则停止，禁止猜坐标或改用自制 UI 自动化。
- accessibility 能读到主工作区仍不代表可以安全输入；若编辑区点击返回
  `coordinate input geometry is unavailable` 或焦点仍在文档根节点，将状态标为
  “已登录、仅可观察”，禁止盲打，改由用户手动发送精确测试命令。
- Build-keyed Gateway 兼容性结论只使用第 5 节的 terminal marker；不要从标题、模型、
  prompt 或旧任务状态推断。命中 exact build 时保持暂停；不同官方 build 重新做有限
  `/init` catalog-selection canary。WindowsApps packaged `codex.exe` 仍不是独立 CLI，SDK/Harness
  也不是 Desktop fallback。

### 8.6 Harness-native 方向已移交独立 sibling Skill

Harness 不是 Desktop fallback；不得导入或重放 Desktop 请求。路由边界见
[references/harness-native-v2.md](references/harness-native-v2.md)，sibling 当前状态见
第 13 节，退休字节不属于发布包。

## 9. 新电脑的安全冷启动

### 阶段 A：只读与静态检查

1. 新建一个不含业务资料的专用开发项目。
2. 放入经过发布 manifest 导出的干净 Skill 包；显式排除 `.codex/`、
   `.agents/skills/`、`_retired/`、根 `AGENTS.md`、`skills-lock.json` 和旧运行态；
   保留 manifest 明确列出的 `.agents/plugins/marketplace.json`。
3. 核对 Windows、PowerShell 5.1+、Python 3.10+、Node/npm/npx、Codex Desktop，
   以及独立可运行的 Codex CLI；WindowsApps 下的 Desktop package resource 不算独立
   CLI，普通 preflight 只读 shim/package metadata，不启动 Codex 进程。
4. 运行只读 `bridge preflight`，一次汇总本机依赖、源码完整性与挂载状态；
   缺少前提时，Skill 应列明本次缺失依赖、官方来源和随后一次
   `feishu configure`，取得一次明确的 onboarding 授权后自动安装并重新
   preflight。验证成功后不再询问，后台启动一次 `lark-cli config init --new`，
   原样转发官方 URL 并用 `lark-cli auth qrcode` 生成 PNG 二维码，由用户完成
   PersonalAgent 创建。随后按 `openclaw-common-chat` 发起一次官方
   `auth login --recommend` 常用用户权限扫码；由用户亲自批准，Skill 在用户回复后
   续上对应 device code。二维码不能平替 Bot tenant scope 的后台声明/管理员审批；
   `--recommend` 可能包含大量、跨域且含写权限的动态 scope，准确范围以飞书授权页为准。
   用户不想用 JSON 时走后台界面逐项配置。该授权不是 Skill 自我
   同意，也不允许上述范围外权限、Bot 租户权限修改、管理员/浏览器代办或挂载
   Listener；若用户不同意、安装验证失败、配置/授权失败或过期，应停止在此阶段。
   独立安装器与下载归档只能进入系统临时目录，验证、拒绝、失败或取消后清理，
   不得在 Skill 或目标项目留下 `.codex-bootstrap`。
   Windows 的 `.cmd` shim 若误解析 URL 中的 `&`，应改用已验证的 `node.exe`
   和同一已安装官方 CLI 的 JavaScript 入口传递真实参数数组，只重试二维码，
   不得重启配置流程或重写 URL。
   一次性二维码必须放在系统临时目录的独立子目录，配置结束后清理，不得写入
   Skill 或目标项目。
   普通 preflight 不检查飞书 Windows 客户端。只有用户明确询问接管飞书前端、
   操控客户端或真实会话自动测试时，才读取
   `references/feishu-desktop-client.md` 并运行 `feishu desktop-status`。
   若缺失且用户批准，使用 `feishu desktop-install -DesktopInstallConsent` 从
   飞书官网当前元数据指向的官方 CDN 下载，校验哈希和 Authenticode 签名、静默
   安装并清理临时包；随后启动一次并在二维码/账号登录页停下。客户端安装或运行
   不能证明登录，用户本人完成认证，发送测试消息前仍需新的精确批准。
5. 完整阅读本文件、`SKILL.md`、`references/architecture.md` 和
   `references/permissions-and-hooks.md`。
6. 运行 `bridge validate`，并用语言解析器静态解析 PowerShell/Python；不得导入
   Bridge 模块、启动 child process 或运行动态测试。
7. 检查源码中不存在 `.env`、SQLite、日志、PID、凭据、真实 ID 和旧电脑路径。
8. 检查新 Desktop 是否列出所需项目与八个任务协调工具。

### 阶段 B：外部动态测试

在 Codex Desktop 外部终端或 CI 中执行，且 Listener 必须停止：

```powershell
$env:FEISHU_BRIDGE_EXTERNAL_TEST_RUNNER = '1'
powershell -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge test -RunTests -ExternalTestRunnerAcknowledged -ProjectRoot <isolated-project-root>
```

测试结束后清除该进程环境变量。不要把测试运行态复制回 Skill，也不要在正常
知识库项目中运行。

### 阶段 C：重新配置飞书

1. 按官方流程安装/检查 `@larksuite/cli`。
2. 在新电脑用官方二维码重新创建/登录；不要复制旧电脑 Token 或 CLI 私有目录。
3. 首次权限按 `references/openclaw-common-chat-permissions.md` 完成常用用户权限
   扫码。用户 OAuth 不等于 Bot 租户 scope；若 Bot 缺权限，转发 CLI 返回的
   `console_url`，由用户/管理员在后台声明审批；用户不想用 JSON 时走界面逐项配置。
   不自动提交管理员审批，也不重复创建应用。加急、撤回、成员/管理员写入、禁言等操作即使 scope
   已授予，仍需单独精确批准。
   user OAuth、Bot 凭据和 Bot tenant scopes 必须分别验证；Bot scope 审计使用显式
   `lark-cli api GET /open-apis/application/v6/scopes --as bot`。
4. 确认机器人 owner、允许的用户和群，使用 locked allowlist。
5. 若旧电脑不再使用，建议在飞书侧撤销旧设备登录或轮换敏感凭据。

### 阶段 D：逐项挂载

以下按顺序执行；只读验证无需批准，所有写入、信任和生命周期动作必须各自单独批准，
不能因为用户口头说“全部同意”就合并：

1. `bridge init`：只增量合并包内最新 `AGENTS.md` Bridge 区块；
2. 首次 `bridge install`：作为一个预先完整披露的 bootstrap，安装 runtime、两个
   lifecycle hooks、初始 `bridge.env`、manifest 与 Bridge hook 注册；不改项目规则；
3. 另行批准 `bridge access -AccessMode locked -OwnerOpenId <ou_...>` 并只加入用户
   明确选择的 ID；新安装默认 locked，空 allowlist 和显式 legacy `compat` 均不得进入
   canary 或生产；
4. 只读验证 `.codex/hooks.json` 是无 BOM UTF-8 的 matcher-group 数组，且
   `SessionStart` / `SessionEnd` 各只有一个精确 Bridge handler；再经单独批准，在
   可见 Codex hooks 审查面逐项信任两个准确 Bridge hash；
5. 写入固定 Python、queue helper 和 runtime 路径的精确 allow rule；
6. 单独批准重启 Codex 以加载 rule；
7. 不指定模型/推理覆盖，使用 `assets/desktop-gateway-bootstrap.md` 创建专用
   Gateway 候选；普通首轮须通过八名称检查与一次有界 `list_threads` 调用；
8. 以 `CONTRACT_TURN_MODE=INITIAL_MOUNT` 渲染并发送
   `assets/desktop-gateway-task.md`，只注册，不 probe/claim；若已注册 Gateway 因模型
   切换或压缩需要保留，只能在 scheduler 暂停、registration exact-match 和另行批准后
   用 `REHYDRATE_EXISTING` 复水，绝不重复注册；
9. 创建指向该 exact existing task 的两分钟 scheduler heartbeat，初始保持暂停并
   完整回读；
10. 仅当 exact official Desktop build 没有 terminal marker，才在用户在线且已准备好
   `/init` 目录选择、确认和一条普通消息时，单独批准一次有硬上限的 canary；
11. 验证 exact-scope binding、目标 final、飞书只看到 final，随后保持暂停/完成；
12. 生产 recurrence 需要两个后续批准：先在暂停状态修改并回读，再激活。

公开 `bridge upgrade` 固定只改 Listener/runtime。若现有 start hook 尚不支持
manifest，先在 Listener 已停止后单独批准 `bridge hooks`；它更新 Bridge hooks 与
注册、使旧 manifest 失效但不签新 runtime。随后另行批准 `bridge upgrade` 生成匹配
manifest。runtime 配置、规则同步、hook 信任审查与 Listener restart 都是独立动作。
`bridge init` 在更新已有 live `AGENTS.md` 前必须把原件备份到 Bridge 的时间戳
备份目录，再只替换标记区块。

## 10. 验收清单

### 控制面

- [ ] scheduler heartbeat 指向一个现有 Gateway，不是新聊天，也不是目标任务；
- [ ] 空队列只执行一次 metadata probe；
- [ ] 非空时在同一回合 claim 并调用目标；
- [ ] overlapping scheduled cycles 看到 `wake_inflight` 后退出；
- [ ] stale fence 不能 complete/fail/release；
- [ ] 不确定是否送达的请求不重放；
- [ ] create/restore/compact 只回报请求内且 Desktop 明确成功的归档 ID，缺失字段或
      请求列表回显不能算成功；
- [ ] 归档/缺失目标最多进行一次 exact-scope replacement。

### 会话与上下文

- [ ] 私聊、群聊、两个同名群、两个 topic 均有不同稳定 scope；
- [ ] `/init` 只从有界快照连接 exact task ID；
- [ ] 新建任务始终显式选择并确认，旧任务不删除、不归档；
- [ ] 压缩动作只作用于当前 exact 任务，不生成 Bridge summary；
- [ ] 目标保留自己的模型、推理、项目、插件、Skills、浏览器和知识库；
- [ ] Bridge 不保存第二份可重放聊天历史。

### 飞书与展示

- [ ] 私聊与群 @ 都有事件；topic 不串线；
- [ ] 富文本逐行显示，不把 JSON 当正文；
- [ ] 最终回复不含 commentary、reasoning、工具链、队列 ID 或本地路径；
- [ ] 长文本不静默截断；
- [ ] 附件 count/type/size/quota/path/TTL 校验生效；
- [ ] 未授权用户和群不能创建队列或 Codex 任务。

### 生命周期与安全

- [ ] 一个 SessionEnd 不会停止仍有其他 lease 的 Listener；
- [ ] Listener 停止、scheduler 暂停、Desktop 关闭时消息状态可解释；
- [ ] 安装、规则、重启、Gateway、自动化、激活均有独立审批；
- [ ] 动态测试只在外部隔离环境运行；
- [ ] 发布清单只含 Skill，不含 Vault、运行态、凭据或本机路径。

## 11. 下一步开发路线（2026-08-22）

产品决策保持不变：Desktop Bridge 是现有主线，因为它路由到用户真正拥有的 Desktop
任务；Harness 是独立研发线，只拥有自己的 SDK thread，绝不是 Desktop fallback。
官方当前同时提供 [Python/TypeScript Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)；
Python SDK 通过 JSON-RPC 控制其 pinned runtime 提供的 local App Server，而
[App Server](https://learn.chatgpt.com/docs/app-server) 公开 thread/turn、steer、interrupt
与 client-owned approval 协议。[Scheduled task](https://learn.chatgpt.com/docs/automations)
也明确支持附着到 existing chat。以上能力证明两条路线均可继续
研发，但不证明当前 Windows build 的 automation-origin Desktop task tools 可用。

### P0-A：source-only 可复现发布基线（源码实现完成）

1. 把现有静态检查固化成 release audit：锁定 explicit relative-path + SHA-256 manifest；
   49/7 只是当前派生计数。继续检查两个 Skill、Markdown 链接、AGENTS 镜像、Harness 冻结
   哈希，并拒绝凭据、真实 ID、非样例本机绝对路径、运行态和 `_retired`。
2. 把 crash/race 矩阵写成测试合同：每行都记录 fault injection point、expected durable
   state、expected replay decision 与 test ID。至少覆盖 claim/receipt 发布窗口、终态写入
   中断、SQLite 锁、并发 producer 内容冲突、retry generation、pending project marker
   和三入口 env 校验。
3. 定义 versioned JSON evidence schema，至少绑定 `source_manifest_sha256`、audited path
   set、exclusion results、OS/PowerShell/Python、exact command/exit、Listener stopped
   receipt 与 `bounded_control_files_unchanged=true`。

出口：干净导出包通过 release audit 与语言解析；本阶段不启动测试或 live runtime。

2026-08-22 已新增唯一的 `assets/release-inventory.json` 路径真相、两组件
source-only audit、P0-B JSON Schema 与 F01-F12 故障合同；发布计数和 manifest hash
均在最后一次审计时派生，不写回源码以避免自引用。`pending` 现为稳定 immutable
canonical request，claim 以完整 fence 排他发布，producer/consumer 重叠时不会重新腾出
同一 request id。动态测试始终只在 Desktop 外运行；P0-A 仍须在每次源码编辑后重新审计。

### P0-B：外部动态验收

只在 Codex Desktop 外、Listener stopped 的隔离终端或 CI 中运行完整测试，并按 P0-A
schema 生成 create-new、hash-bound evidence receipt，再通过独立语义 validator；测试状态
不得复制回 Skill。

`scripts/run-external-p0b.ps1` 已作为 source-only supervisor 加入 inventory：它只接受 clean
PowerShell 7.4+ `-File` 入口，清除继承的 Python/Codex Bridge/飞书/Lark 环境，固定 PSF 签名
Python executable 并以 `-I -S -B` 运行。当前用户与项目共同派生的跨 Windows session
lifecycle mutex 覆盖 pre/post/final 三次 stopped/runtime 检查；完整 P0-A 源码、audited
snapshot、installed start/stop hooks 与 `hooks.json` 都在窗口内以禁止写删的只读句柄固定。
两端只接受普通本地 DOS drive 路径，拒绝 UNC/device、SUBST、mapped drive 与 reparse alias，
并通过 Win32 file handle 解析 existing prefix 的最终物理路径，因此 8.3 等价路径不能绕过隔离。
测试由 audited `external_p0b_test_runner.py` 从真实 `unittest.TestResult` 生成带 nonce 的
create-new 结构化结果，不再解析可伪造的 `ok` 文本；每个子进程进入 KILL_ON_JOB_CLOSE
Windows Job，并有 bounded timeout。`scripts/validate-external-p0b-evidence.ps1` 会独立重算
保留快照、captures、F01-F12 evidence hash、当前 P0-A、hooks、toolchain 和 bounded runtime
关系。JSON Schema 只证明形状；validator 的 pass 也不是签名或密码学 attestation，并信任
本机 PowerShell 安装与 Python 标准库。Job 在 `Process.Start` 到 assign 之间仍有一个很小的
已披露竞态，管理命令或直接启动 `bridge.py` 也不受 lifecycle mutex 约束。

`scripts/invoke-external-p0b-once.ps1` 是人工外部 P0-B 验收入口：自动使用唯一
work/evidence 目录，supervisor 失败立即停止并保留现场，只有合法 envelope 才调用
semantic validator。它本身也不得从 Codex Desktop 内运行。
`scripts/invoke-external-p0b-p3-once.ps1` 是连续验收入口：保持子进程 stderr 与 JSON
stdout 分离，只有 P0-B supervisor 与独立 validator 都通过才传递精确 evidence path/SHA
给 P3；成功只输出一个汇总 JSON，失败直接保留并呈现子阶段诊断。

2026-08-22 已在独立 Windows Terminal 完成一次 P0-B：108/108 tests 通过，create-new
receipt（ID 不写入发布源码）的 SHA-256 为
`8f2d08dcc6317cfb9f3d2e7510c7e3015278d3aee4e7cbebe192dcd4fbe6af65`，
独立 semantic validator 对 source manifest
`f97d23917709bd38db67a1555cb69d23b45f9e65d09a3f9682b6219f77a84837`
返回 `status=pass`、`semantic_relations_validated=true`、
`retained_artifacts_pinned=true` 与
`current_environment_revalidated=true`。它明确
`cryptographic_attestation=false`。随后又有三次 current-source P0-B/validator
通过：manifest `d9aaccab12039d1ca90256822da34e7efe4d7ec2b74b7ebfb0160bce28884739`
对应 receipt SHA-256
`c0036b30c649777a301f89a013b36bb46d93a1239ca117be931ac5b8fb267077`；
修复 isolated Python import 与 staging publish 后，manifest
`6ef7d171e9f137b59f051e7f6ef70660460f28849e3e32024634e450e7d6573a`
对应 receipt SHA-256
`309869c13b3774ef9271c07e26fca5b32f3eac3213d0a73bf3ae1d9ffab75aed`。
加入 single-use rollback guard 后，manifest
`943264bc766994289211e59a4a3e6b0905a5eb2d82e03383d4a8cd6131083e36`
对应 receipt SHA-256
`7fd65523834d7a234ba45a3306f2ca2914f34bacd5b3fce3cdad680055ff650d`。
三次 validator 都确认 semantic relations、retained artifacts 与 current environment，
并明确不是 cryptographic attestation。写入最终 P1 结果会再次改变源码，所以这些是
精确绑定各自 manifest 的历史证据；最终源码仍需最后一次门禁。

出口：P0-A manifest 与外部 receipt 精确匹配，完整测试通过，live runtime 未改变。

### P1：`alpha.2 -> alpha.4` 隔离迁移演练

在可丢弃 VM 中验证 stopped Listener 的 hook-only refresh、runtime-only upgrade、manifest
签名、source/runtime parity、locked access、queue/binding 保留与显式 rollback。每个实际
管理动作仍单独批准，演练不得自动 restart、创建 Gateway 或启用 scheduler。

当前 source-only `scripts/external-p1-migration-lab.ps1` 已实现
`prepare`、只读 `observe` 与 quarantine-based `rollback`，并由
`bridge validate` 检查 external origin、PSF Python、stopped Listener、隔离路径、
state canaries、baseline manifest、无内嵌管理阶段和无删除命令。alpha.2 runtime baseline 位于项目本地备份
`20260822-194423`，legacy hooks baseline 位于 `20260822-185523`。默认在同一
Windows 用户下创建独立项目；是否另建 Windows 用户只在用户需要更强 OS 隔离时询问，
Skill 不擅自创建。详细阶段合同见
[references/p1-isolated-migration.md](references/p1-isolated-migration.md)。

首次 prepare 揭示 `python -I` 不会自动把 helper 目录加入 `sys.path`；未创建
wake DB/PID/baseline，半成品被保留且未采信。runner 已改为固定
`runpy.run_path` bootstrap，只显式加入 pinned runtime，并在
`<lab>.preparing` 完整构建后同父目录原子发布。r2 随后的
`prepared`、`hooks_refreshed`、`upgraded` 均返回 pass；manifest、
source/runtime parity、locked access、unrelated hook、唯一 Bridge hook、pending queue
与 binding canaries 都符合合同。

r2 rollback 恢复了精确 alpha.2 baseline，但返回
`rollback_quarantine_count=2`。只读取证确认第一个 quarantine 是带有效 manifest 的
alpha.4，第二个是再次被隔离的 alpha.2，说明 rollback 被顺序重复执行；该结果不作为
最终 rollback pass。源码已改为只允许从精确 clean `upgraded` 状态执行一次，并在
任何 copy/move 前 create-new `rollback-intent.json`。成功必须同时满足 intent 存在
且 quarantine 恰好为一；replay、并发、partial failure、多 quarantine 或已恢复状态均
fail closed。r2 和首次半成品均保留，未清理；后续复验使用全新 r3，而不是修饰或
删除 r2 证据。

r3 已在 manifest `943264bc...83e36` 的 P0-B/validator pass 后完整复验。
`prepared`、`hooks_refreshed`、`upgraded` 与
`rolled_back` 四个外部 observation 均为 pass；baseline manifest 为
`f6f8b32c3dd0aa0e36e488487153bbca44433dfa83343e8a12689245a378eefe`。
升级阶段确认 alpha.4、完整 manifest、current parity、唯一 Bridge hooks 和所有 canary；
回滚阶段确认 alpha.2/legacy hooks 精确恢复、guard 存在且 quarantine 恰好为一。
quarantine 内仍是带 manifest 的 alpha.4，Listener 全程 stopped、无 PID。r3、r2、
首次半成品及 quarantine 都保留，清理仍是未来的独立破坏性审批。P1 动态出口已达到；
剩余工作只是对这次 HANDOFF 更新后的最终源码重跑 P0-A/P0-B。

出口：可复查的 before/after manifest 与 `bridge doctor` 结果，且失败路径可恢复到已停止态。

### P2：不同官方 Desktop surface 的有限 canary

先用只读官方包元数据生成 product/channel/build/runner surface key；same 或 unknown key
立即停止，不创建候选或 automation。只有 positively different official surface 才做
ordinary-turn 七工具 registry + bounded `list_threads` 预检，再创建无 model/reasoning
override 的 Gateway、直接注册合同，并创建暂停的 existing-chat scheduler。canary 前必须
回读三次以内的完整运行上限，最后才用全新飞书作用域进入 `/init`、选择预声明目标、
确认并发送普通消息，完成后立即暂停并回读。

有效出口有两种：PASS 是 exact-scope binding、目标 authoritative final、飞书只收到
final、scheduler 已暂停；FAIL-CLOSED 是 build-keyed terminal marker、
`may_have_started=false`、无 binding、scheduler 已暂停。两者都禁止同 surface 改
prompt/model/task 后重试。

### P3：可靠性与可运维性

alpha.19 已完成机器可读诊断合同。alpha.20 在源码中加入 stopped/external-only 的
P3 有界 soak；alpha.21 修复文件与目录父级遍历，alpha.22 修复 Python 3.13 下子进程
guard 的类语义，alpha.23 修复首次 pin 的空集合绑定，alpha.24 修复时间精度，alpha.25
修复同工作区独立插件共存和连续 P0-B/P3 的错误透传。P3 绑定同版且经独立
validator 通过的 P0-B retained snapshot，固定覆盖
scheduler overlap、相同/冲突 producer、terminal finalizer、长任务 lease/retention、
Listener 模型调用前后恢复、飞书限流/断网重试及撤回消息终态十个场景；默认 25 轮、
300 秒硬上限、禁止子进程以及 Desktop/Feishu live contact。supervisor 发布 create-new
receipt，独立 validator 重算场景映射、`iterations * 10`、retained hashes 和 current P0。
当前已完成源码合同、Desktop-safe 静态门禁、文件/目录 helper、隔离 child-guard、首次
空 pin 集合及时间精度探针，仍需 alpha.25 外部 P0-B 后再执行 P3。
P3 通过也不是 Desktop final-return canary，不改变 build-keyed terminal marker。

### P4：通过 P0-P3 后的产品能力

只按官方 surface 实际暴露的能力逐项评估原生 typed media、文件回传与长文本分片。
每项单独做数据边界和权限设计；若 `send_message_to_thread` 仍是 text-only，就保留
manifest transport，不用 UI、数据库或 App Server 假装原生输入。

### H0-H3：独立 Harness 研发线（本轮不实施）

1. H0：解析当时最新 stable `openai-codex`，按届时公开 surface 新建并独立审查一套
   版本化 compatibility probe、hashes 与 evidence；不得只替换版本号，也绝不改写
   `0.147.0` 冻结基线。出口同时绑定 SDK、pinned runtime、dependency closure 与源码哈希。
   全局 npm CLI 或 Desktop package 不能替代 SDK pinned runtime；`codex queue` 也不属于
   SDK worker 的提交/唤醒面。
2. H1：若继续当前专用 VM 路线，先复核 public account surface，再 source-only 实现默认
   禁用的 current-user diagnostic：只调用一次 `Codex.account(refresh_token=False)`，不
   login/thread/turn、不创建 Windows 用户，外部运行另设审批；结果始终
   `formal_gate_eligible=false`，`requires_openai_auth=true` 也不得自动开始登录。当前已
   加入 supervisor/child 候选，但两者 `SOURCE_ENABLED=false`，supervisor 的合法审批
   receipt authenticator 也故意未实现并锁死；下一步是独立源码审查和设计真实签发/验签
   权威，不是切换常量、伪造 JSON 或启动进程。
3. H2：若 canary 需要认证，先在 H1 后另设并完成一次明确登录审批；随后在 exact stable
   SDK 的 thread lifecycle 与每次 turn 上显式设置 deny-all +
   read-only，并验证 effective state，不继承 `auto_review` 或 ambient defaults。先做
   client/thread/single-turn canary，记录 exact thread ID、turn ID 与 final；通过后再扩展
   event stream、interrupt、read/resume 与 crash-window 矩阵。read-only 不等于无上游
   网络，只能证明没有已批准工具/文件写入或 agent 网络扩权。若 submission 进入不确定区
   前拿不到 turn ID，或 read/resume 不能可靠确认同一 turn，就在 H2 停止。还必须验证
   resume/fork 不会回退 sandbox、approval/reviewer、workspace、model/reasoning 或 capability
   profile。当前已检查的 `openai-codex 0.147.0` 高层 `Codex()` 不暴露 caller approval
   handler，而内部默认 handler 会接受 command/file approval；因此任意意外 approval
   request 都是硬失败，tool-enabled 与交互审批模式保持禁用，不能用 private client 绕过。
4. H3：先在无飞书条件下实现仅覆盖 H2 capability envelope 的 owner-locked worker、独立
   queue/receipt 与 fault tests。tool-enabled/remote approval 是另一硬门：官方 App Server
   的 approval RPC 不等于 Python SDK 已暴露可应答 surface；无法实证时只保留 read-only
   MVP，绝不转 direct App Server。最后才接独立测试 app/consumer，ingress 与 worker 分离
   secret，并且不共享 Desktop auth/state/ID/app/queue。

当前主线已完成 P0-A 源码实现；P0-B 只接收外部 evidence receipt。P1-P4 依次由各自出口解锁。
H0-H3 是独立排期，出口不能替代 Desktop 证据，也不得跨过自身前置门。

## 12. 发布与安全边界

公开发布前：

1. 明确仓库根和 remote；
2. 查看完整 staged file list；
3. 只允许本 Skill 的文件；
4. 扫描 App Secret、Token、`cli_` / `ou_` / `oc_`、任务 UUID、绝对路径、
   `.env`、SQLite、日志和附件；
5. 静态验证通过；
6. 动态测试结果必须来自外部隔离环境；
7. 不把旧运行目录作为“方便调试”的样例提交；
8. 不自动部署、重启、创建 Gateway 或激活自动化。

迁移包的目标是让另一台电脑安全地继续开发，而不是让复制完成后立即常驻运行。

## 13. Harness sibling current status（2026-08-22）

Harness 后端已经从 Desktop Bridge 中拆成独立的
`feishu-codex-harness-bridge` Skill。本文件只保留两者的交接边界，不再保存
Harness 的逐轮实验日志、旧源码哈希、已完成审批或被后文取代的“下一步”。

在 sibling 根目录 `feishu-codex-harness-bridge/` 内，当前活动 Skill 只包含：

- sibling 的 `SKILL.md` 与 `agents/openai.yaml`；
- sibling `references/architecture.md`：产品所有权、队列、审批与发布边界；
- sibling `references/external-lab.md`：外部兼容性实验协议；
- sibling `references/current-user-diagnostic.md`：专用 VM 当前用户诊断的未实现合同；
- sibling `scripts/run_sdk_surface_probe.py` 与
  `scripts/sdk_surface_probe.py`：冻结的非实例化兼容性探针。

旧 auth-state v1 runner 的 receipt 没有合法签发者，owner/WFP 文件也只是无调用者的
空壳，均已退出活动 Skill。经明确授权，旧 `_retired` 档案的 42 个文件已全部永久删除，
没有保留兼容层、回退实现或可恢复发布源。未来正式 runner 必须基于届时公开的 Codex
surface 重新命名、设计、hash、审查和审批，不能从聊天记录重建旧实现。

当前实验机的 lab-local 选择是
`dedicated_vm_current_user_diagnostic`：这是专用/可丢弃测试虚拟机，不创建、修改、
禁用、启用或删除 Windows 用户。这个选择不是可复用默认值，也不授权依赖安装、SDK
进程、认证、网络、策略、thread、turn、Feishu consumer 或清理动作。以后每个新环境仍
必须先询问用户选择模式；选择独立账户也不等于授权创建账户。

保留的历史兼容性基线为：

```text
compatibility-probe-openai-codex-0.147.0.json
  sha256=5e4bc4778e022bb0c8f82d630ad07a039e9d47bf5af579baaec1aaf174e22d97
scripts/run_sdk_surface_probe.py
  sha256=108fe9f88bf248036cc9ac18eba37888e33dae29d79b32a6e84f17a9b7d63ee2
scripts/sdk_surface_probe.py
  sha256=38282e9ae66470c973cff94d0c82b92bacb6f1af76025cf0df1e722229202dcf
```

该 JSON 没有打包进 Skill；只有用户另行提供 exact bytes 时才能复核。它只证明冻结
`0.147.0` compatibility schema 的 package/public-surface 与路径前提，不是正式证据，
也不能替代新 venv、新 SDK 版本、auth-state、approval、turn 或 release 测试。

当前 VM 路径已经 source-only 加入新的
`run_current_user_auth_diagnostic.py` /
`current_user_auth_diagnostic.py` 候选。它们默认禁用，只设计一次
`Codex.account(refresh_token=False)` 观察，输出始终 non-formal /
downstream-ineligible；未启动、未动态测试。supervisor 和 child 的 literal source gate
均为 false，且 supervisor 的合法审批 receipt authenticator 仍明确未实现，因此当前没有
任何可批准的运行命令。下一项是独立静态审查该候选，并设计真实的 receipt 签发/验签与
撤销/no-replay 权威；不得用文件哈希、聊天文本或操作者自建 JSON 冒充签发者。之后的实际
进程启动仍需新的精确批准，并且只能由用户在 Codex Desktop 外部运行；supervisor 还必须
把 exact Desktop Bridge absent/stopped 的新鲜只读 receipt 绑定进审批与结果，不能只信
环境变量或口头状态。

## 14. Read-only Gateway result invariant（2026-08-24）

历史绑定 canary 在 single-cell Gateway 修复后完成了两个真实调度周期，但最近两个
`inspect_thread` receipt 均被错误写成 `target_result_unknown`、
`may_have_started=true`，且 `sessions.json` 中没有目标绑定。飞书因此收到通用的
“未能通过 Desktop 路由核验”回复。这个结果证明 scheduler、helper claim 和 terminal
delivery 已工作，不证明 task ID 错误，也不是 `target_tool_unavailable`。

修复后的合同只把 `codex_app__read_thread` 返回值作为原生对象或单次 JSON parse
处理，要求 `thread.id` 精确等于 claimed target，并把缺失、不可解析或错 ID 的返回写成
non-retryable `invalid_gateway_result`、`may_have_started=false`。`inspect_thread` 不会
启动或修改 target；Gateway 永远不得为它使用 `target_result_unknown
--may-have-started`。队列 helper 也必须拒绝任何 read-only operation 的
`may_have_started=true` failure，防止模型合同漂移再次制造永久未知结果。当前 runtime
和已挂载 Gateway 合同仍是旧版本；源码通过完整静态门禁、外部 P0-B、runtime-only
upgrade、Gateway rehydrate、paused automation prompt update 及新 live canary 之前，
不得宣称修复已部署或绑定已通过。

## 15. Desktop 26.818.8289.0 `/init` canary 结论（2026-08-25）

新版 Desktop ordinary-turn registry 检查列出八个任务工具，且一次有界
`list_threads` 预检成功。真实 automation-origin `/init` canary 随后领取了只读
`list_task_catalog` 请求，但以 `invalid_gateway_result`、`retryable=false`、
`may_have_started=false` 终止；未启动用户目标、未建立 binding。根因不是会话 ID、
飞书权限或目标任务，而是 Bridge 源码仍请求 `limit=100`，当前 Desktop
`codex_app__list_threads` 参数合同明确只接受最多 50。

`4.2.0-alpha.11` 把 producer、Gateway 合同、普通轮预检和 heartbeat prompt 的目录
上限统一为 50，并加入回归断言，防止再把 schema 漂移交给真实飞书会话发现。
这项修复尚未自动部署；必须依次通过静态门禁、外部 P0-B、另行批准的 runtime-only
upgrade、Gateway rehydrate 和 paused prompt update。

同一 live window 还观察到更高优先级的 scheduler 缺陷：automation 配置完整保留并
回读 `COUNT=3`，但实际产生了四个 automation-origin 回合。人工随后暂停不能替代硬
次数上限。因此 build `26.818.8289.0` 的
`windows_desktop_heartbeat_automation_origin` surface 已登记为
`scheduler_cap_unenforced` 并由 `bridge canary-gate` 阻断；不能因为修复目录上限、
更换 prompt/model/task 或人工值守而重试。只有明确不同的官方 Desktop build 或真正
能强制硬上限的新 wake surface，才可重新进入普通轮预检和有限 live canary。

截至本记录写入时，Gateway automation 已回读为 `PAUSED`；Listener 是否运行属于
部署态，应由新的只读 `bridge status`/进程身份核验确定，不从本交接文档推断。

## 16. `4.2.0-alpha.12` 一次性人工诊断通道（2026-08-25）

连续三个 Desktop surface 的 scheduler/tool 兼容性实验不再阻塞飞书绑定功能本身。
源码新增一个只在 scheduler 保持 `PAUSED` 时使用的 owner-present 诊断通道：控制任务
每次在新鲜用户批准下调用未加入 allow rule 的 `manual-authorize`，签发绑定到精确
Gateway task、host、预期 operation 且最长十分钟有效的一次性票据；Gateway 收到
`assets/desktop-gateway-manual-cycle.md` 的精确渲染后，只能通过现有 allowlisted
`sentinel-probe --manual-ticket` 原子消费该票据。

人工 probe 只读取待处理项的 request ID 和 operation 名称，选择最早的匹配请求，并把
wake fence 固定到该 request/operation。随后最多 claim 一个请求，不做 20 秒 grace
claim，终态后显式 release。票据在首次尝试时即消费，过期、重放、跨 task/host、operation
不匹配全部在目标动作前失败。它不更新 `last_probe_at`，不改变
`scheduler_cap_unenforced` 结论，也不构成 automation-origin 或 production 证明。

该通道的目标是逐轮完成 `/init` 目录、选择后的 `inspect_thread`、精确 binding 和一条
普通消息回传。每个 Feishu event/operation 仍需单独的 owner approval 和新票据；Gateway
不能调用 `manual-authorize`，controller 也不能直接 claim、读取 payload 或写 binding。

## 17. `4.2.0-alpha.13` Desktop catalog 字段映射（2026-08-25）

alpha.12 的第一张真实人工票据按设计只领取一个 `list_task_catalog`，没有刷新
scheduler freshness，也没有联系目标；但 Gateway 把结果终止为
`invalid_gateway_result`、`may_have_started=false`。控制面对照只保留 API 类型、字段名
和数组长度，确认当前 Desktop 的两个目录 API 都返回“一层 JSON 字符串”：项目字段为
`projectId/label/path/hostId/projectKind`，任务字段为
`id/title/projectId/hostId/status/updatedAt`，且列表中允许 `projectId=null` 的
projectless 项。

alpha.13 将精确映射写进 Gateway 合同：`path -> root`、`projectKind -> kind`、
`id -> thread_id`、`updatedAt -> updated_at`；忽略 additive envelope 元数据，省略
projectless 或引用未知项目的 task，禁止读取或复制 `summary/cwd`。这修复的是模型
编排合同，不改变 scheduler 的 build marker，也不允许 controller 或 Listener 代替
Gateway 读取任务目录。该源码变更需重新通过静态门禁和外部 P0-B，再做 runtime-only
upgrade 与现有 Gateway rehydrate；旧失败事件不可重放，后续使用新的 `/init` event。

第一次 alpha.13 外部 P0-B 的 119 个测试中仅新增 prompt-contract 断言失败：测试要求
`one permitted JSON parse`，合同使用语义相同的 `one JSON parse only`。现统一为前者，
并把该测试使用的目录映射、projectless 过滤、禁止字段和 envelope 标记同步加入
`bridge validate`，以后先由 Desktop-safe 快速门禁发现测试/合同措辞漂移，再交给外部
P0-B，避免重复的两分钟全量运行。

## 18. `4.2.0-alpha.14` 确定性 Desktop catalog 归一化（2026-08-25）

alpha.13 的真实人工 `list_task_catalog` 再次以 `invalid_gateway_result`、
`may_have_started=false` 终止；Listener 因而只返回通用失败，没有绑定或目标动作。
控制面只保留匿名结构统计，确认当前 Desktop 数据并不畸形：3 个项目均合法，10 个
Codex 条目中 2 个为应省略的 projectless 条目，1 个为必须排除的 Gateway，剩余 7 个
均满足 Bridge schema。问题是合同仍让模型自行重写映射算法，而不是字段或用户数据。

alpha.14 在 Gateway 合同中内嵌 source-exact `normalizeActiveDesktopCatalog` JavaScript：
它在同一个 `functions.exec` cell 中一次解析两个 Desktop envelope，只访问允许字段，
确定性过滤 projectless/unknown-project/Gateway 条目并生成唯一 catalog schema；模型
不得改写算法。异常只暂存不含标题、ID、路径或工具输出的稳定阶段码，再以
`invalid_gateway_result` 安全终止。快速 `bridge validate` 与 prompt-contract 测试共享
这些函数/阶段码标记，外部 P0-B 前还要直接执行该代码块的匿名计数自检。

当前安装且运行的 Listener 仍是 alpha.13；alpha.14 仅为源码，未获批准前不得停止、
升级或复水 Gateway。上一条失败 `/init` 已终态化，升级后必须发送新事件。

## 19. `4.2.0-alpha.15` 自包含人工 operation 合同（2026-08-25）

alpha.14 的合同内 JavaScript 已在控制面直接从 Markdown 提取并对真实 Desktop
envelope 匿名执行成功（3 个项目、7 个可展示 task、无 Gateway/未知项目引用），但真实
人工 Gateway 回合仍只产生通用 `invalid_gateway_result`，没有任何 alpha.14 稳定阶段码。
这证明后台 task-to-task 回合不能可靠依赖先前 rehydrate 长提示的逐字内容；失败发生在
source-exact normalizer 被执行之前，而不是 normalizer 本身。

alpha.15 新增纯渲染器 `scripts/render_gateway_manual_cycle.py`。控制面拿到一次性票据后，
由它把人工模板、精确 operation 段和共享 `## Complete or fail` 段组合为一个自包含 prompt；
只允许已知 operation，校验 task ID/票据，拒绝未解析 placeholder。人工回合不再使用
“mounted contract memory”作为执行依据，也不允许模型重写 operation 逻辑。release
inventory、`bridge validate` 和外部 prompt-contract 测试同时固定该渲染路径。

在 alpha.15 开发当时，安装 Listener 仍为 alpha.14。随后 alpha.15 已通过外部 P0-B、
单独升级并用于一次受控临时绑定诊断；该历史状态不代表当前 alpha.16 已获部署批准。

## 20. `4.2.0-alpha.16` Windows Unicode 控制面修复（2026-08-25）

一次 owner 批准的临时绑定把飞书私聊精确连接到预声明 Desktop 目标。绑定、持久入队、
一次性人工票据、单次 claim、目标执行和禁止重放均按合同工作，但真实中文消息在
Python helper stdout 经 PowerShell/Desktop 工具边界时逐字符变为 Unicode 替换字符 `�`。
目标界面截图显示七个输入字符全部损坏；同时目标任务已完成，但 `wait_threads` 没有
`latestAssistantMessage`，随后 `read_thread` 对该真实 turn 返回空 `items`。终态因此正确为
`target_result_unknown`, `may_have_started=true`, `retryable=false`，Listener 向飞书返回安全
降级提示且没有重放。

alpha.16 把 `router_queue.py` stdout 固定为 ASCII-only JSON wire，非 ASCII 提示通过标准
`\uXXXX`（包括 emoji surrogate pair）承载。Gateway 合同要求恰好一次 JSON parse，再原样
转发恢复后的 Unicode；禁止转发 raw escape 或二次 code-page 转换。外部 P0-B 新增中文标点
与 emoji 往返门禁。该修复只解决输入损坏；当前 Desktop surface 不暴露 task-to-task turn
最终 items 的限制仍独立存在，严禁用 UI、数据库或 rollout 回读替代。

## 21. `4.2.0-alpha.17` build 级最终回读门禁（2026-08-25）

alpha.16 安装运行时随后通过第二次受控临时绑定完成两轮真实中文诊断。两条消息都进入
同一个“刘学森”Desktop 目标任务，界面中的中文完整无乱码；第二轮正确回答第一轮保存的
测试代号“蓝鲸42”，证明 ASCII-only JSON 输入修复与同任务上下文连续性均有效。两轮中
目标 turn 都完成，但 Gateway 的 `wait_threads` 均没有 `latestAssistantMessage`，随后
`read_thread` 均返回空 `items`。飞书因此只收到禁止重放的安全失败提示。

当前官方 build `26.818.8289.0` 已据此增加
`target_final_readback_unavailable` 能力结论；它原有的
`scheduler_cap_unenforced` 结论继续保留。alpha.17 的只读 canary gate 会显示输入已验证、
两轮上下文已验证、最终回传不可用，并把 `send_message_to_thread` 列为该 build 上禁止重复的
人工诊断操作。模型、prompt、Gateway/目标任务、上下文或等待时间变化都不是重新测试条件；
只有明确不同的官方 Desktop build/surface 才能进行一次新的有限最终回传 canary。

诊断结束后 Listener 已正常停止，第二次临时绑定交易已通过维护脚本精确回滚，飞书作用域
恢复未绑定，队列无 pending/claimed 请求；目标任务及两轮上下文保留。alpha.17 随后通过
自己的外部 P0-B 与独立语义验证，并在单独批准下完成 runtime-only 升级；Listener 未启动。

## 22. `4.2.0-alpha.18` 审批交互压缩（2026-08-25）

对本控制任务最近 200 个回合的有界回读发现 104 个简短授权回合，其中 101 次为精确
“同意”；存在 27 段长度至少为 2 的连续授权，最长为 5。该证据只用于确认交互负担，
不写入运行时状态，也不把历史同意解释成长期授权。

alpha.18 在 Skill 与托管 AGENTS 规则中落实“一个精确动作、一次询问、自动验收”：询问前
完成只读发现、身份/路径解析、源码准备和风险分析；一次授权包含同一动作的确定性命令渲染、
有界等待、进度更新与只读 status/doctor/hash/manifest/队列/交易核验。源码编辑、文档同步、
AST、静态验证和单条外部命令准备连续执行，不再询问泛化的“继续/下一步”。只有在已披露的
可执行文件或 helper 确实没有运行时，才可在同一授权下修正 shell quoting/transport 语法；
可执行文件、路径、目标、scope、subcommand、风险或恢复路径变化仍需新检查点。

多检查点流程应预先简要列出剩余强制审批，让用户知道还会停在哪里，但每次回复只授权当前
命名动作。首次 bootstrap 以外的 runtime、hook/config/rule、upgrade、start/stop/restart、
Gateway 与 scheduler 生命周期、临时绑定各阶段、Codex 调用及每张一次性人工票据继续保持
原有独立边界。alpha.18 随后通过外部 P0-B 与独立语义 validator，并在单独批准下完成
runtime-only 升级；Listener 未启动。

## 23. `4.2.0-alpha.19` P3 机器可读诊断合同（2026-08-25）

alpha.19 为 `bridge status`、`bridge doctor` 与 `bridge validate` 增加可选 `-Json`，
默认人类文本保持兼容。三条命令每次只输出一个压缩 JSON 对象，公共头为
`schema_version=1`、稳定 `command` 与 `status`。消费者必须按 schema version 拒绝未知
版本，不能回退到解析英文/中文输出。

`status` 分开报告进程身份状态、已安装完整性 manifest 与最近一次 health snapshot；
Listener 停止后旧 snapshot 的 `bridge_version` 不再冒充已安装版本。`doctor` 只报告
逻辑 artifact 名、固定 issue code、计数、访问策略布尔状态、源码/runtime parity 和
AGENTS 托管区状态；不输出飞书或 Codex 任务 ID、消息/提示/答案、凭据、allowlist 值或
本机路径。`validate -Json` 仍在当前 PowerShell 进程内完成静态门禁，显式返回
`child_process_started=false`；`doctor`/`validate` 的机器合同为 `status=fail` 时退出码 2。

本轮只做源码、Skill、AGENTS、双语 README、release inventory、AST 与只读诊断验证，
没有启动 Listener、改 Gateway/scheduler 或运行 Desktop 内动态测试。alpha.19 随后通过
外部 P0-B supervisor 与独立 semantic validator，并在单独批准下完成 runtime-only
升级；Listener 仍停止。

## 24. `4.2.0-alpha.20` P3 有界 soak 合同（2026-08-25）

alpha.20 新增 `scripts/external_p3_soak_runner.py`、clean-PowerShell supervisor、独立
semantic validator、one-shot wrapper 与 schema/reference。P3 必须先绑定 exact same-source
P0-B evidence 文件与 SHA-256，复用其 retained `source-snapshot`，并在 soak 前后重新执行
P0 validator。固定十场景映射同时由 runner、validator、`bridge validate` 和静态测试钉住。

runner 不允许启动子进程，stdout 必须为空，进度留在 retained stderr；supervisor 用
KILL_ON_JOB_CLOSE Job 和硬 timeout containment，receipt 只能 create-new。P3 不接触 live
Listener、Desktop、Gateway、scheduler 或 Feishu。源码完成后必须先重新执行 alpha.20
P0-B；只有该 supervisor 和独立 validator 均通过，才能把新 P0 receipt 交给 P3 wrapper。
P3 的 envelope/schema 不能单独作为验收，必须同时取得独立 P3 validator 的 pass。
P0 one-shot 的成功 envelope 会在原字段之外追加 `evidence_path`，后续 P3 直接使用该值，
不再通过时间戳目录或 `Get-ChildItem` 猜测 receipt 位置。

## 25. `4.2.0-alpha.21` P3 路径链预检修复（2026-08-25）

alpha.20 的外部 P0-B supervisor 与独立 validator 已通过，证据 SHA-256 为
`23a1299fe374f85fa8dd6278f26bdda1e57edb5d0768ca3f246a8ebd24dc2b43`，对应
`source_manifest_sha256=aaff26e1eac78a86fd1c09fab2cc58d9c9d20c2b2430aebb04f7b7cdd3613543`。
随后第一次 P3 在最早的路径链预检停止：两份 `Assert-NoReparsePathChain` 都对
`FileInfo` 读取了仅目录对象提供的 `.Parent`。失败的 work/evidence 目录为空，runner、
P0 pre-validator 和 P3 evidence publication 均未发生。

alpha.21 在 supervisor 与独立 validator 中显式使用 `FileInfo.Directory` 和
`DirectoryInfo.Parent`，并在 `bridge validate` 固定这四个合同标记。外部 P0-B 测试还会
通过 PowerShell AST 单独提取两份 helper，各自用一个普通文件和一个普通目录执行真实
回归探针。由于该修复改变了受审源码，alpha.20 receipt 只能作为历史证据；进入 P3 前
必须先取得 alpha.21 的新 P0-B envelope 与独立 semantic-validator pass。

## 26. `4.2.0-alpha.22` P3 Python 3.13 子进程 guard 修复（2026-08-25）

alpha.21 的外部 P0-B supervisor 与独立 validator 已通过，证据 SHA-256 为
`540897b9bccf0d3cf9d6f357653c2570fa9f5356d2e4da7c93e88308c6c29290`，对应
`source_manifest_sha256=49c0e194664157208325030b2f6d59645c27bce4c42b6aa04f3cc6139977f0c5`。
随后 P3 的 P0 pre-validator 再次通过，但十场景尚未开始：runner 将
`subprocess.Popen` 替换成普通函数，Python 3.13 导入 `asyncio.windows_utils` 时试图继承
它，因而在测试模块加载阶段报 `TypeError`。结构化结果确认 `iterations_completed=0`、
`total_tests_run=0`、`child_process_attempts=0`，不是场景失败或真实子进程尝试。

alpha.22 改用继承原始 `Popen` 的 `ForbiddenPopen`；它保持模块导入所需的可继承类语义，
但构造函数只增加尝试计数并立即拒绝，从不调用原始进程构造器。外部 P0-B 新增隔离解释器
回归：安装 guard 后必须成功导入 `asyncio`，`Popen` 必须仍是类，构造尝试必须在进程创建
前失败且计数恰为一。由于该修复再次改变受审源码，alpha.21 receipt 只能作为历史证据；
进入 P3 前必须取得 alpha.22 的新 P0-B 与独立 validator pass。

## 27. `4.2.0-alpha.23` P3 validator 首次 pin 修复（2026-08-25）

alpha.22 的外部 P0-B supervisor 与独立 validator 已通过，证据 SHA-256 为
`dff75328b3c3a0357a9e8bcc93182eecd753cc0ac2f219b429935ac78d85f291`，对应
`source_manifest_sha256=2679730d2be9f236c6c182e6e63bf5db65282a62090fd661f6e01d7fbef45b7a`。
P3 随后完整通过 25 轮固定十场景，共 250 个测试，最长单轮 2.336233 秒；前后 P0
validator 均通过，`child_process_attempts=0`，没有 live Desktop/Feishu contact，且
create-new P3 receipt 已发布。该结果证明 runner 行为，但尚不能作为正式 P3 pass，因为
独立 validator 在第一次调用 `Add-PinnedReadHandle` 时退出。

根因是新建的 `List[FileStream]` 在加入第一个句柄前必然为空，而 PowerShell 对 Mandatory
集合参数默认拒绝空集合。alpha.23 为 `Pins` 增加 `[AllowEmptyCollection()]`；外部 P0-B
新增 AST 提取回归，向 helper 传入新空列表与零字节文件，要求产生恰好一个可读、长度为零
的 pinned handle 并最终释放。alpha.22 的 P3 receipt 与 P0 receipt 仍绑定旧源码 manifest，
只能作为历史诊断；alpha.23 必须重新取得同版 P0-B，并由 P3 supervisor 与独立 validator
共同通过后才能正式接受。

## 28. `4.2.0-alpha.24` P3 validator 时间精度修复（2026-08-25）

alpha.23 的外部 P0-B supervisor 与独立 validator 已通过，证据 SHA-256 为
`e5d7dd9cb58698e4d9e783ede71eb984957c58fef038a4935a1896ff18326543`，对应
`source_manifest_sha256=9a81ba06d1c2857b49081587d4dd1f75c404e7d8a4725db99675c7e2514f503c`。
P3 再次完整通过 25 轮、250 个测试；本次 runner monotonic duration 为 44.051356 秒，
supervisor 的高精度墙钟跨度实际为 44.4059086 秒，因此真实关系有效。

独立 validator 失败是因为 PowerShell `ConvertFrom-Json` 已将 ISO date-time 转成带完整
ticks 的本地 `DateTime`，随后代码先强制转为普通 `[string]`，文化格式化把 7 位小数和
偏移丢掉，再解析得到整秒跨度 44.0 秒；0.01 秒严格容差因此错误拒绝 0.051356 秒差值。
alpha.24 新增类型感知 `ConvertTo-P3DateTimeOffset`：`DateTimeOffset` 直接返回，带 Kind 的
`DateTime` 直接保留 ticks，字符串才按 invariant RoundtripKind 解析，并拒绝
`DateTimeKind.Unspecified`。P0-B 的 AST 提取探针对 JSON-deserialized object 与原始字符串
同时核对同一 7 位小数 UTC ticks；严格 0.01 秒容差保持不变。alpha.23 证据仍绑定旧源码，
alpha.24 需重新取得同版 P0-B 与完整 P3 双 pass。

## 29. `4.2.0-alpha.25` 工作区插件共存与外部套件诊断（2026-08-25）

alpha.24 的一次外部 P0-B 已运行完 136/136 项动态测试，失败/错误/跳过均为零，Listener
pre/post 状态也一致；但未生成 evidence。保留目录和重新执行只读发布审计证明，P0-B 前置
审计之后、测试后审计之前，工作区新增了独立的
`plugins/human-authorization-relay` 插件原型。它有自己的插件 manifest 和 Skill，Bridge
源码没有引用它；旧发行清单却把任何未知顶层目录视为 Bridge 源码漂移，因此 post-audit
以 `UNEXPECTED_DIRECTORY:desktop_bridge:plugins` 中止。临时合并命令又把详细子错误并入
变量并用 `P0-B failed: 1` 覆盖，造成诊断信息丢失。

alpha.25 在 `release-inventory.json` 中只排除精确顶层 `plugins` 树与精确根 `.tmp` 本地
工具树，和 `.codex`、`.agents` 等工作区共存边界一样不进入 Bridge snapshot；该排除既不
删除、修改或认证独立插件/临时依赖，也不允许其他未知根目录。新增
`invoke-external-p0b-p3-once.ps1` 作为正式连续入口：分离每个
子包装器的 stdout/stderr，P0-B 两个 JSON 门禁均通过后才将精确 evidence path/SHA 交给
P3，任一阶段失败即携带有界的原始诊断停止，成功只输出一个汇总 JSON。P0-B 回归同时锁定
精确排除规则、stderr 分离和 P0→P3 handoff。由于这些是新的受审源码，仍需取得 alpha.25
同版 P0-B supervisor/validator 与 P3 supervisor/validator 全部 pass 后才能验收。

## 30. `4.2.0-alpha.26` 精确 `wait_threads` 最终回传合同（2026-08-25）

在不发送新消息、不启动 Gateway、不读取回答正文的只读检查中，对已经完成的“刘学森”
目标任务执行一次 `wait_threads(timeoutMs=0)`。当前原生结果包含精确目标 task/host、
`latestTurn.status=completed`、非空 `latestAssistantMessage.text`、message/turn 关联和 cursor。
这证明当前任务工具至少能对一个既有完成回合暴露最终消息形状，因而后续返回链可以在
Gateway 编排中确定性提取；它不是一次新的发送，也不证明历史失败消息可回放。

alpha.26 把普通非 steer 发送固定为 stale-final-safe 流程：发送前先对唯一精确目标做
零等待快照，只保留 baseline cursor 并忽略旧 final；只发送一次；之后每次
`wait_threads` 都携带该 baseline 或下一 exact poll cursor。原生对象直接读取，字符串只
允许一次 JSON parse。只有 exact target 的新 `latestTurn.status=completed`，且
`latestAssistantMessage.turnId` 等于该 turn ID、`phase=final_answer`、text 非空时才把原文
不改写地 staged/complete。send 返回值、baseline 旧消息、`read_thread`、其他 task、UI、
数据库、rollout、OCR 与剪贴板都不能作为 answer。新 turn 已完成却缺少匹配 final 时仍为
`target_result_unknown`、`may_have_started=true`，禁止重放。

该源码修复不清除 `26.818.8289.0` 的 `scheduler_cap_unenforced`，也不推翻两次历史发送
形成的 build/surface 终态记录；当前 scheduler 继续保持 `PAUSED`。alpha.26 需要自己的
外部 P0-B/P3 双门禁、runtime-only upgrade、分离 restart，以及在明确不同的官方
Desktop build/surface 上重新做一次有限 final-return canary。

## 31. `4.2.0-alpha.27` completed/final 可见性宽限（2026-08-26）

在同一官方 Desktop build 上再次对既有完成的“刘学森”任务做零等待只读快照，原生
`wait_threads` 仍可返回 `latestTurn=completed`、同一 turn 的
`latestAssistantMessage`、`phase=final_answer` 和非空正文。这仍不是一次新发送，也不清除
既有 build/surface 终态标记；但它暴露了 alpha.26 的确定性时序缺口：合同一看到
completed 却尚无 matching final 就立即失败，没有给 Desktop 的 final 消息视图短暂落后于
turn 状态留下空间。

alpha.27 在不扩大回复来源的前提下增加一次有界 final-materialization grace。首次 exact
completed poll 缺 final 时，Gateway 固定该 turn ID 与 cursor、续租 active-work lease，
继续 exact-target `wait_threads` 最多 20 秒；期间绝不重发，只接受该固定 turn 的
same-turn `final_answer` 原文。不同 turn、无效结果或宽限耗尽仍以
`target_result_unknown`、`may_have_started=true` 终止并禁止重放。send 结果、baseline 旧
消息、`read_thread`、UI、数据库、rollout、OCR 与剪贴板仍不是答案来源。

当时该源码修复仍不允许在 `26.818.8289.0` 的同一 automation-origin surface 上追加
第三次原生发送诊断；真正飞书回传 canary 仍等待可被正面识别为不同的官方 Desktop
build/surface。后续 P0/P2 决策与新 transport 见第 32 节。

## 32. `4.2.0-alpha.28` P0 exact-turn Hook 回复回传（2026-08-26）

用户将“等待新 Desktop build”降为 P2，并把“目标 Codex 回复可靠回传飞书”设为 P0。
这不是清除 `26.818.8289.0` 的 `scheduler_cap_unenforced` 或
`target_final_readback_unavailable`：它们继续禁止 scheduler 重新激活和第三次原生
`latestAssistantMessage` 诊断。P0 改用一个结构上不同、仍由相同 queue fence 约束的
精确 Hook transport；当前 build 只允许在 scheduler 暂停时通过一次性票据做有限 canary。

源码新增 repo-local `plugins/feishu-codex-final-return` 与
`.agents/plugins/marketplace.json`。Gateway 在一次发送前先执行 `final-return-arm`；插件的
隐藏 MCP `UserPromptSubmit` Hook 只能把匹配的 task/session、实际 turn 和 prompt hash
绑定到该 arm，`Stop` Hook 只能把该 exact turn 的最新非空 final 写入原 fenced staging。
同一 turn 被其他 Stop hook 继续时，后续 Stop 覆盖 provisional answer。所有未 arm、错
task、错 prompt、错 turn、过期、冲突或已 native-fenced 的事件均忽略或失败关闭。

Gateway 仍以发送前的零等待 cursor 和 exact completed turn 为边界。completed 后先查
`final-return-status`；若 Hook 已 capture，Gateway 不读、不改 staging，直接由 `complete`
消费。若 native wait 恰好提供 matching `latestAssistantMessage/final_answer`，先用
`final-return-native` 封住晚到 Hook，再原样 staging。两者都没有时只对固定 turn 做最多
20 秒 exact wait/status，不重发；超时仍是 `target_result_unknown` 与
`may_have_started=true`。send result、baseline、`read_thread`、transcript、UI、数据库、
rollout、OCR 与剪贴板都不是回复来源。

Desktop-safe AST/JSON/PowerShell parse、AGENTS 镜像、`bridge validate -Json` 和完整双组件
release audit 已通过。官方 `quick_validate.py`/`validate_plugin.py` 在现有两个 Python
运行时中都因其自身缺少 `PyYAML` 而未启动；没有为这项辅助检查临时安装依赖，也不把它
写成 pass。插件安装/启用、`bridge final-return-register`、逐项信任
`UserPromptSubmit` 与 `Stop`、Codex restart、Listener stop、外部 P0-B/P3、runtime-only
upgrade 和最终 live canary 都尚未执行。下一步是在独立终端、Listener stopped 条件下
取得 alpha.28 同版 P0-B supervisor/validator 与 P3 supervisor/validator 双 pass；只有
这些门禁通过，才依次请求后续精确行政动作的批准。

## 33. `4.2.0-alpha.29` 顶层 direct MCP Gateway 修复（2026-08-26）

在官方 Desktop build `26.820.7780.0` 上，alpha.28 的一次 owner-approved、one-ticket
`send_message_to_thread` canary 在目标发送前终止。队列只产生一个终态
`invalid_gateway_result`，没有 target thread/turn/cursor；“刘学森”目标任务没有出现新
turn，飞书得到通用失败回复。随后在普通 Codex 回合中对旧路径做无副作用能力探针：
`functions.exec` 的 `ALL_TOOLS` 仍列出 `codex_app__wait_threads` 与
`codex_app__send_message_to_thread`，但实际从 `tools[...]` 调用时只返回“该 app tool
不再通过 dynamic tools 提供，请使用 codex_app MCP server”。同一批
`mcp__codex_app.*` 顶层直接调用则可用。因此这次失败发生在 pre-send baseline
`wait_threads`，没有触发 `final-return-arm`、目标发送或 Hook；不能据此评价 Hook
transport。

alpha.29 删除“一个长 `functions.exec` cell 同时调用 helper 与 Desktop app tools”的
假设。新合约以一个 Gateway model turn 作为原子边界：固定 queue helper 使用多个短小、
有界的 `functions.exec` cell；Desktop 协调只允许顶层 direct
`mcp__codex_app` 调用。成功 claim 仍是 commit point，同一 model turn 必须继续到唯一
terminal `complete`/`fail` 与 wake release；每次 direct bounded wait 前及至少每 60 秒
续租 active-work heartbeat。`ALL_TOOLS`/`tools[...]` 只作为明确禁止的旧路径保留在文档和
静态守卫中，不再作为能力证据。

候选与 model-change preflight 也改为真实调用两个无副作用 direct 方法：有界
`mcp__codex_app.list_threads` 和 `mcp__codex_app.list_projects`。它们只证明普通回合 direct
MCP server 可达，不宣称其余六个方法、automation-origin surface 或最终回传已经兼容。
下一次 live canary 前必须先取得 alpha.29 同源 P0-B/P3 外部双验证，再分别批准 runtime
upgrade、现有 Gateway 合约 `REHYDRATE_EXISTING`、新版 ordinary-turn direct-MCP
  preflight，以及新的临时绑定/one-ticket Hook canary；scheduler 继续保持 `PAUSED`。

## 34. `4.2.0-alpha.30` Desktop delegation Hook 关联修复（2026-08-26）

一次已批准、单票、单次发送的真实飞书 canary 证明：普通消息进入持久队列，Gateway
完成 baseline、arm 和一次 direct target send，目标任务产生并完成新 turn；但 native
`latestAssistantMessage` 仍为空，`final-return-status` 在完整 20 秒 materialization grace
中一直为 `state=armed`，最终按不重放边界返回 `target_result_unknown`。这证明故障位于
`UserPromptSubmit` 绑定之前，而不是飞书入站、目标执行、Unicode 传输或队列领取。

Desktop 对 task-to-task send 的持久用户消息使用严格 `<codex_delegation>` 包装，并在
`<source_thread_id>` 中记录来源 Gateway、在 `<input>` 中保留原始 prompt。alpha.29
把整个 Hook `${prompt}` 直接与原始 input 哈希比较，因此合法包装会被静默忽略。
alpha.30 在 arm 时固定当时注册的 Gateway ID；绑定时仍优先接受 raw prompt，或仅解析
完整 wrapper、要求来源等于该固定 Gateway 且 inner input 的 UTF-8 SHA-256 与原始 prompt
完全一致。错误来源、畸形包装、正文不匹配和错误 turn 仍被忽略。

为了避免以后反复实发猜测，armed receipt 只增加 answer-free 观察字段：Hook 是否出现、
是否为被查询 turn、匹配模式和枚举拒绝原因；不保存或输出 raw/wrapped prompt。下一步先
完成 alpha.30 Desktop-safe AST/`bridge validate`，再在 Listener 停止后取得同源外部
P0-B/P3；部署和新 one-ticket canary 分别审批，绝不重放本次已开始的消息。

## 35. `4.2.0-alpha.30` 飞书最终回复与连续上下文实测通过（2026-08-27）

alpha.30 的同源外部 P0-B/P3、runtime-only 部署、插件 runtime registration 与精确 Hook
信任完成后，在 scheduler 保持 `PAUSED`、临时绑定仍指向“刘学森”目标任务的条件下，执行了
两次各自独立批准、单票、单请求的普通消息诊断。第一轮要求目标记住随机测试代号
“青岚73”并只回复固定确认；飞书收到确认。第一轮终态后，第二轮要求只返回刚才的代号；
飞书准确收到“青岚73”。两轮之间没有重发、没有 controller-side target send，也没有
App Server、`read_thread`、UI、数据库、rollout、OCR 或剪贴板回复回退。

有界运行状态同时显示 `completed` 依次从 17 增至 18、再增至 19，既有
`terminal_failed=1` 未增加，且两轮结束后 `reply_pending=0`。结合飞书端的唯一代号回显，
这已黑盒证明当前 source/runtime/Hook/Desktop 配置下的飞书入站、同一绑定目标上下文延续、
严格 delegation wrapper 关联、exact-turn Hook capture 与 Listener 最终回复回传全部打通。

这个 pass 不清除既有 native final readback 或 scheduler hard-cap build marker，不验证
`/init`，也不授权 production recurrence；scheduler 继续暂停。不要在未改变的 surface 上
继续重复相同实发猜测。实测完成后已经按三个各自独立批准的动作停止已核验 Listener、用
原 transaction 精确恢复该 scope 的未绑定基线，并重新启动完整性有效的 alpha.30
Listener。回滚后 helper 状态为 `absent`；恢复后的 Listener 身份已核验、Feishu consumer
为 true、队列为空，scheduler 仍保持 `PAUSED`。不要从本节复制瞬时 PID、票据或 transaction
作为以后操作依据；每次维护都重新做只读身份和状态核对。
