# Feishu Codex Bridge 使用手册

当前源码合同版本：`4.2.0-alpha.63`。本文只说明安装、配置和当前可用行为。
开发与版本适配见 [upgrade-bridge.md](upgrade-bridge.md)，当前工作区状态只看
`HANDOFF.md`。

## 1. 当前产品边界

产品目标是把飞书私聊、群聊 `@` 和话题消息交给真实、可见、可继续的 Codex Desktop
Responder，再把该 Responder 的 authoritative final 原样返回飞书。

当前版本使用一个与历史路线隔离的本地 producer：

```text
飞书 -> Bridge 鉴权与 durable inbox
     -> durable local grant（先核销）
     -> Desktop-bundled codex queue（只发 opaque page，最多尝试一次）
     -> 若固定 grace 后仍未领取，只打开 exact Beeper deep link 一次
     -> 新建的唯一 Beeper task
     -> Desktop task-to-task call
     -> Responder-owned Final Callback -> sealed outbox -> 飞书
```

每条新鉴权通过的飞书事件会直接触发这一次有界 queue 尝试。当前路线不安装、也不依赖 Codex
“已安排”中的 recurring automation、轮询 producer 或周期性 Beeper 唤醒；请求内的 active-work
lease 只用于本次尝试的 fence 与存活性判定，不是后台调度任务。

固定架构是：

- 每个 installed Bridge namespace 恰好一个独立 Beeper；
- Beeper 只与其他 Desktop threads 协调，不绑定飞书 scope，不成为业务 responder，不自投递，
  不执行用户业务，也不生成 final；
- 每个 responder task 独占自己的项目、上下文、模型、工具、执行与 final；
- Bridge 只获准启动一次精确校验过的 Desktop-bundled `codex queue`，且只能把 opaque page
  发给该 Beeper；若 queue 已接受但 page 仍为 `reserved`，可只用 exact Beeper UUID 冷加载
  一次，不传消息内容、不再次 queue；它不直连 responder、不启动 App Server、不读取数据库或 rollout；
- 退休 producer surface 永久不可执行，细节只见 architecture quarantine contract。

这条本地路线不是官方产品级 `run_once`：CLI 接受队列不等于能证明只启动一个模型回合。
非零退出、超时或结果不明都按终态处理，不自动重排；启用前的终态消息不会被接管或补发。
飞书成功绑定 Desktop task 时只提示一次：
极少数异常可能造成重复或漏执行，请不要用于转账、删除数据等不可撤销操作；提示不要求再次确认。
冷加载失败或 Beeper 未在有界时间领取时，Bridge 会先原子封住仍未领取的 page，再明确回复
“Responder 尚未开始”；迟到的 Beeper 不能继续领取该 Page。

当前可用性分成两个层级：

- `mvp`：current source/runtime 身份一致、当前加载的 Final Callback surface 实际参与，
  并由一条全新的、已绑定普通消息证明 Beeper 协调、Desktop responder 执行、responder-owned `submit_final_callback`、
  `final_callback_source=final_callback` 与飞书明确送达形成最简闭环。该结论接受上面的极低概率重复/漏执行
  风险，也接受 bearer capability 不能证明产品 caller/turn 的限制。
- production：除上述功能闭环外，还必须有产品级 pre-dispatch `run_once`、不可变 runtime receipt、
  task-tool provenance 与 caller/turn attestation。缺少其中任一项都保持 blocked。

因此，MVP ready 表示“当前受限路线已经真实可用”，不表示 exactly-once，也不适合转账、删除
数据等不可逆操作。当前 final 只观察 `final_callback`；Hook-based final observation 不属于现行协议。
项目 `SessionStart`/`SessionEnd` Hook 仍只管理 Bridge 生命周期。

## 2. 只读预检

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge preflight -ProjectRoot <project-root>
```

预检不安装、不写配置、不启动 Bridge。冷启动需要：

- Windows 10/11、PowerShell 5.1+、Python 3.10+；
- Node/npm/npx 与 `lark-cli`；
- Codex Desktop；
- 独立官方 `@openai/codex` CLI；
- 已启用机器人能力的飞书/Lark 自建应用。

WindowsApps 中 Desktop 包资源不是独立 CLI。不要修改 ACL 或复制 packaged binary。
开发按当前 CLI/Desktop capability 适配，不维护旧版本分支。

## 3. 飞书配置与权限

完整权限说明见
[permissions-and-hooks.md](references/permissions-and-hooks.md) 和
[openclaw-common-chat-permissions.md](references/openclaw-common-chat-permissions.md)。

推荐流程：

```powershell
npm install -g @larksuite/cli
npx -y skills add https://open.feishu.cn --skill -y
lark-cli config init --new
lark-cli auth login --recommend --no-wait --json
lark-cli auth status --json --verify
```

必须分别验证：

1. user OAuth；
2. Bot credential；
3. Bot tenant scopes。

用户 OAuth 不代替 Bot scope。二维码、OAuth、管理员批准或 tenant 页面要求的真人身份确认
是外部平台交互；其余项目内本地步骤连续自动执行。

## 4. 安装顺序

用户要求安装、升级或配置本项目后，Codex 自动完成所需本地 PowerShell、依赖、Hook、插件、
Schema 与生命周期事务，不要求逐步回复“同意/继续”。每步仍先解析精确路径、版本、进程身份和
恢复路径，并做 read-only postcondition check。

推荐顺序：

1. `bridge init -ProjectRoot <path>`：只合并 `AGENTS.md` 受管区块。
2. `bridge install`：原子安装 Bridge runtime、SessionStart/SessionEnd Hooks、初始
   `bridge.env` 与 integrity manifest。
3. `bridge access -AccessMode locked -OwnerOpenId <ou_...>`：至少一个已验证身份。
4. 从 repo Marketplace 安装并启用整体 `feishu-codex-bridge` 插件。
5. `bridge final-callback-register`：绑定 exact manifest-valid runtime。
6. 在可见 Hook review 中只逐项信任项目的 SessionStart、SessionEnd；callback 插件不再提供
   UserPromptSubmit/Stop Hook，禁止 `Trust all`。
7. 重启/重载 Codex，并在新任务核对实际插件、Skill、MCP 和 lifecycle Hooks。
8. 运行 status、doctor、validate 和 readiness。

新安装默认 locked；空 allowlist 拒绝全部事件。`compat` 只用于显式迁移，不是生产默认。
旧 lifecycle Hook 迁移必须先停 Bridge，再分别执行 `bridge hooks`、`bridge upgrade` 和重启。

自动执行只包括本文定义的受限本地 producer，不扩大为历史 producer、未请求的发布、跨项目修改
或凭据变更。

## 5. 整体插件与 final-callback

唯一源码是 `plugins/feishu-codex-bridge`。repo source、Marketplace route、installed runtime
与 versioned plugin cache 是四种不同角色；只编辑 source，不直接改 cache。

首次接入 repo Marketplace：

```powershell
codex plugin marketplace add <repo-root>
codex plugin add feishu-codex-bridge@feishu-codex-bridge
```

源码更新后按 [upgrade-bridge.md](upgrade-bridge.md) 先定稿代码、合同、测试、inventory 与 source
version，再更新一次 cachebuster 并冻结；对这些 exact bytes 完成所需 Gate A/Gate B/P3 后，才从
同一 Marketplace 重装并在新任务验证加载。旧任务可能保留创建时的旧 surface。

整体插件内部的 `feishu_final_callback` MCP key 和 runtime registration namespace 是当前协议。

常用命令：

| 目的 | 命令 |
|---|---|
| 查看注册 | `bridge final-callback-status` |
| 注册 runtime | `bridge final-callback-register` |
| 撤销注册 | `bridge final-callback-unregister` |

Final Callback MCP 是 transport，不是 Beeper。Beeper 通过 Final Callback tools claim 并 arm
一个 Page；Bridge 只保存一次性 Final Callback capability 的 digest，再把包含原始 `user_request` 与
capability 的 wrapped prompt 发给 exact Responder。Responder 用自己的上下文、工具和审批执行后，必须
调用 `submit_final_callback` 一次提交 exact final；Beeper 看得到 capability，但不得使用、泄露、
代交或根据 native answer 生成 final。completion 只接受 `final_callback_source=final_callback`。

这是一项受限 bearer capability：持有 token 可以提交，但普通 MCP 不能证明产品中的调用者或具体
turn，因此它不是 product attestation，也没有把本地 grant 变成 `run_once`。错误、过期、重复、
冲突或篡改提交 fail closed 且不重放。native field、`read_thread`、`wait_threads`、UI、DB、OCR 和
clipboard 不是 fallback。

final 以原始字符串为权威；空值检查不改变内容，oversize fail closed。Markdown、chunk 和
attachment omission 是显式 transform；第一笔发送前冻结 outbound plan，未知结果不重放。

`/init` 走独立的 `claim_readonly`/`complete_readonly` 合同：claim 只返回一个有界 catalog 或
exact inspection 请求，complete 与 receipt 保持 answer-free；允许展示的 title/project label 只经
密封的临时 staging 单次交给 Bridge，不能借 helper stdout 或普通 final 返回。

## 6. Beeper、`/init` 与恢复

退休 producer lifecycle/surface 不是安装或恢复步骤。Beeper 必须是新建任务，使用
`beeper` 独立 registration；任何既有记录都不允许恢复。理想产品
`run_once` 合同见
[beeper-run-once-candidate.md](references/beeper-run-once-candidate.md)。

Desktop 重启后不需要用户手动先打开 Beeper：普通消息的同一张已核销 page 在固定 grace 后仍
未领取时，Bridge 只打开该唯一 Beeper 一次。这个动作可能短暂把 Codex 前台切到 Beeper，
但不会打开业务 responder，也不会成为第二条消息或 final 通道。

`/init` 是唯一识别的飞书斜杠命令。它只开放非归档 bounded catalog、十分钟内存快照、
当前页数字选择、确认后的 exact read-only inspection，以及 Bridge 的原子本地绑定。向导绑定
发起人；title/project label 只存在于绑定 request/fence/snapshot 的 sealed ephemeral staging，并由
Bridge 验证后单次消费。持久 binding 只保存稳定 task/host/project ID 与有界 operation receipt，
不保存 display 或 path；receipt 只证明本地操作身份一致，不是产品 caller/turn attestation 或
`run_once`。成功绑定追加一次通俗风险提示，不增加第二次确认。新建 task/project、恢复、归档、
compact、解除连接与回复方式变更均不开放，其他斜杠输入统一拒绝。完整 UX 定义见
[feishu-command-ux.md](references/feishu-command-ux.md)。

当前 archived/not-found responder 对本次尝试是终态：不自动恢复、不创建替代任务、不重发。任何未来
mutation 都必须有独立的封闭 operation/result 合同，不能复用或拓宽当前 catalog lane。App Server、
temporary binding、shell、UI、数据库和 rollout 都不能绕过此边界。

## 7. 日常诊断

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge status -ProjectRoot <project-root> -Json
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge doctor -ProjectRoot <project-root> -Json
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge validate -ProjectRoot <project-root> -Json
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\feishu-codex-bridge.ps1 bridge readiness -ProjectRoot <project-root> -Json
```

- `status`：核对 PID 指向 exact installed `bridge.py`。
- `doctor`：安装、lifecycle Hook、access、environment 与 source/runtime parity。
- `validate`：静态只读门禁，不启动子进程。
- `readiness`：在同一个 answer-free 输出中分开报告 `mvp` 与 production。
  `mvp` 只在 current exact-source 的 `final_callback` 终态、飞书 API 明确确认发送成功、
  首次 inbox claim 与本地 outbox 清理均已观察时 ready；该 answer-free 观测只在当前 Bridge 进程
  存活，重启后归零，且不独立证明单次 Beeper claim、单次 responder call 或产品 no-replay。
  production 还要求 product `run_once`、task-tool/caller-turn attestation 与其他生产门。
  MVP ready 不会把 production 改成 ready，也不得声称 exactly-once。

这些 `-Json` 命令只输出一个 compact answer-free object，不含消息、答案、凭据、身份列表、
task IDs 或本机路径。健康安装不等于生产可用。

`bridge.pid` 只是非可信引用；命令行身份不匹配时不得停止进程或启动第二个 Bridge。
## 8. 命令速查

| 目的 | 命令 |
|---|---|
| 飞书 CLI | `feishu install/configure/login/doctor` |
| 项目 policy | `bridge init` |
| runtime | `bridge install/hooks/upgrade` |
| Bridge | `bridge start/stop/restart` |
| 只读 | `bridge preflight/status/doctor/readiness/logs/validate` |
| access | `bridge access -AccessMode locked ...` |

动态测试选择和外部 supervisor 规则只见
[upgrade-bridge.md](upgrade-bridge.md) 与
[release-audit.md](references/release-audit.md)。普通开发按影响运行最小 focused gate；Gate B
是 release gate，Soak 只在 concurrency/persistence/retry/transport 或最终 release candidate 运行。
不得在 Desktop shell 直接跑 raw unittest/pytest。

飞书 Windows 客户端只有在用户明确要求时才读取
[feishu-desktop-client.md](references/feishu-desktop-client.md)。Obsidian/RAG 属于 responder project，
普通 Bridge 安装不连接知识库。不得发布凭据、Token、日志、queue、session、附件、runtime state
或本机路径。
