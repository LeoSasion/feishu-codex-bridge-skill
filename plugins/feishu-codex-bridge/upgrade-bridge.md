# Feishu Codex Bridge 演化、验证与发布手册

当前源码合同版本：`4.2.0-alpha.63`。本文只保存跨版本仍成立的开发规律，不记录单次事故、
测试函数名或历史发布流水。普通安装与使用见
[feishu-codex-bridge-skill.md](feishu-codex-bridge-skill.md)，当前状态只看工作区
`HANDOFF.md`。

> 当前只开放与历史路线隔离的 `beeper`：Bridge 先核销 durable local grant，
> 再用 exact Desktop-bundled CLI 向新 Beeper 发送一次 opaque page。它不是产品级 `run_once`；
> 若同一 page 在固定 grace 后仍为 `reserved`，只允许一次只含 exact Beeper UUID 的官方
> `codex://threads/` 冷加载；它不传 payload、不再次 queue。失败或领取超时先原子封口，且不重放。
> 启用前的终态消息不补发，退休 producer surface 永久不可执行。

## 1. 唯一架构

- 每个 installed Bridge namespace 恰好一个独立 Beeper。
- Beeper 只通过 Desktop task-coordination surface 与其他 Desktop threads 协调，不绑定飞书 scope，
  不成为业务 responder，不自投递，不执行用户业务，也不生成 authoritative final。`/init` 只允许它
  执行有界目录和 exact-task inspection；允许展示的 title/project label 只经 request/fence/snapshot
  绑定的 sealed ephemeral staging 由 Bridge 单次消费，绑定仍是 Bridge 的原子本地状态变更。
- 每个被选中的 Desktop responder task 独占自己的会话、项目、上下文、模型、工具、执行与 final。
- Bridge 只负责鉴权、持久收件/交付状态、稳定 binding 和有界附件元数据；它不是 Codex client，
  不启动 Codex/App Server，不读取数据库或 rollout。
- P0 Responder 目标始终是：由 Desktop Responder 自己通过一次性 MCP callback 提交 authoritative final，
  再原样返回飞书；Final Callback capability 不是产品 caller/turn attestation。

完整状态与恢复合同见 [architecture.md](references/architecture.md)，未来产品入口见
[beeper-run-once-candidate.md](references/beeper-run-once-candidate.md)。

## 2. 八条稳定规律

| ID | 不变量 | 开发结论 |
|---|---|---|
| R-AUTH | 内容新旧不等于执行权威 | 只有 canonical source、inventory、manifest 与 repo Marketplace route 共同确定发布源；runtime、cache、retained snapshot 都不可编辑。 |
| R-PRODUCER | 本地入口隔离，退休 surface 永久不可执行 | 每个新飞书事件直接触发一次 admission，不安装或依赖 recurring Codex automation；只允许新 Beeper + 独立 namespace + exact CLI + opaque page 的一次本地尝试。执行期只以 SQLite 中精确绑定 request/dial/fence/generation 的唯一 dial lease 为活性权威，不再并存 sidecar freshness。queue 已接受但仍未领取时，只能对同一 Beeper 做一次无 payload 冷加载；领取窗口使用 beeper 的唯一有界上限，超时仍先原子封口且不重放；旧实现细节不进入当前 schema、运行规则或测试矩阵。 |
| R-BEEPER | 单 Beeper 协调，responder 独占业务 | Beeper 只做 task coordination；普通消息只调用一个 responder，`/init` 只做有界 catalog/exact inspection。Read-only completion 与 receipt 保持 answer-free，display 只走一次性 sealed staging。Bridge、App Server、shell、UI、DB 和 rollout 都不能成为第二 responder client。唯一 deep link 只加载 Beeper，不接触 responder 或 final。 |
| R-REPLAY | 不确定结果永不重放 | 只有明确 `retryable=true` 且 `may_have_started=false` 才能进入新 generation；first terminal outcome 不可覆盖。 |
| R-FINAL | final 只来自 Responder-owned Final Callback | 必须绑定 request/fence/Beeper/responder/prompt/capability，只接受 `final_callback_source=final_callback`；Beeper 不得提交，native field、readback、UI、DB、OCR、clipboard 都不是 fallback。Bearer capability 不证明产品 caller/turn。 |
| R-READY | MVP 闭环与生产证明分层，低层不得冒充高层 | `mvp` 只由 current exact-source 的 responder-owned `final_callback` live E2E 与飞书明确送达闭合；production 另需 product `run_once`、task-tool/caller-turn attestation 和 answer-free runtime evidence。 |
| R-TEST | 测试按独立风险收敛 | 每个测试必须证明一个可区分的风险或提供不同的失败定位；producer 与诊断器共享的枚举/schema 必须有一条以真实 producer 形状驱动的跨表面合同测试。被当前实现取代的旧路线测试必须与对应可执行代码一起删除，不能只为保留历史而长期运行。收敛不追求某个 suite 数字，也不在稳定文档硬编码完整 suite 数量。 |
| R-DOC | 一条规律只有一个完整权威家 | 入口文档只给结论和链接；字段、schema、时序与测试 registry 由唯一 reference/code 管理。`HANDOFF.md` 只保存易变的本地当前状态，`bridge validate` 不读取或绑定其中的版本、数量、状态措辞。 |

新增经验只能合并进现有规则或专项 reference。不要为每次故障新增同义规则、长期兼容分支或新的
P 编号。

## 3. Latest-first 兼容策略

本项目面向用户当前安装的较新 Codex Desktop/CLI，而不是维护旧版本矩阵：

1. 先解析当前独立官方 CLI、Desktop package 与插件 runtime identity。
2. 重新生成当前 CLI 对应的 App Server Schema；旧 Schema 不复用。
3. 按 capability/shape 探测所需 surface，不以硬编码版本号推断能力。
4. 能力缺失或 shape 改变时 fail closed，并把它作为一个明确适配任务；不回退到旧 Beeper、
   App Server writer、UI、数据库或 shell。
5. 同一能力只保留一条当前实现。新实现通过后删除被替代 adapter，不长期维护双路线。
6. 版本号和 build identity 只进入机器 manifest、receipt 或当前 HANDOFF，不写成稳定业务规则。
7. 跨平台系统调用按目标平台语义实现：Windows 进程存活探测必须是只读查询，不能照搬
   POSIX 的 `kill(pid, 0)`；“仍存活”也永远不能替代可执行路径与命令行身份校验。

Schema 生成只证明协议来源，不授权启动 App Server 或连接 responder。当前产品工具目录没有通过闭合
证明的 `run_once`。因此 current source/runtime 身份一致、loaded Final Callback surface 实际参与、全新已绑定普通消息经
`final_callback_source=final_callback` 完成且飞书 API 明确确认发送成功后，可以声明
`mvp=ready`；该机器观测只在当前 Bridge 进程内有效，重启后必须用新消息重建。
production exactly-once readiness 仍保持 blocked。这个 MVP 结论接受极低概率重复/漏执行与 bearer
callback 无 caller/turn attestation 的风险；process marker 不独立证明一次 Beeper claim、一次 responder
call 或产品 no-replay，不得扩大为不可逆业务的安全保证。

当前 live-E2E 术语只使用 `final_callback_observed`。Hook-based final observation 属于已退役
final transport，不能出现在 current readiness 结论中；项目 lifecycle Hooks 只证明 Bridge
生命周期审阅，与 final 来源分开。

## 4. 基于变更影响选择验证

不要每改一行文档就重跑全部动态与 soak。先按实际影响选择最小充分门：

- 日常开发使用独立 **fast lane**：固定 56 项，分为 12 项 Smoke、25 项 Contract 和
  19 项当前 executable route 的代表性 Fault。它只给快速反馈，自动清理临时状态，不发布、复用或
  冒充 Gate B/P3 证据。
- Gate B 仍执行完整 test discovery。其 19 项 required mapping 是对完整发现集的必要语义约束，
  必须始终映射当前 executable route；架构切换时，mapping、实现与测试必须一起更新，不能继续由
  已退役路线代为“通过”。Gate B evidence schema v2 还要求三次验证 Bridge、active/dial/outbox
  与 `beeper` beeper 全部 idle，并把这些 capture 绑定进证据；任何 skip 都失败。
- P3 保留 10 个相互独立的 invariant slots。正式发布/夜间证据强制至少 25 iterations，即至少
  250 次场景执行；开发期不运行 P3，而用 fast lane 或受影响的 focused checks。

| 变更 | 开发循环 | 发布候选 |
|---|---|---|
| Markdown、链接、注释且不改变可执行 policy | Gate A 文档/链接/UTF-8/静态审计 | Gate A；无需 P3 |
| Skill、AGENTS、plugin metadata、inventory policy | Gate A + 对应 contract tests | Gate A；重装插件并在新任务核对 |
| schema、parser、capability adapter、Codex surface | Gate A + focused Contract | Gate B；重新生成/绑定当前 Schema |
| Bridge、queue、Final Callback、lifecycle Hook、outbox、transport | Gate A + focused Smoke/Fault | Gate B；涉及耐久 transport 时加 Soak |
| concurrency、persistence、retry/no-replay、fencing | Gate A + focused Fault | Gate B + Soak |
| 最终可发布 runtime/plugin | 全部受影响门 | Gate A + Gate B；涉及耐久边界时加 Soak；最后 readiness |

旧脚本名中的 `p0b` 和 `p3` 只是兼容文件名；人类语义分别是 Gate B 和 Soak。
fast lane 不是发布门；Gate B/Soak 是发布证据，不是日常思考循环，也不证明 live Desktop/飞书能力。

完整外部门禁见 [release-audit.md](references/release-audit.md)，Soak 见
[p3-bounded-soak.md](references/p3-bounded-soak.md)。

## 5. 开发循环

1. 从 `HANDOFF.md` 读取一个当前 blocker，确认 canonical source 与 Bridge 状态。
2. 写出本次唯一目标、受影响 surface、成功条件和不允许触碰的边界。
3. 只修改 `plugins/feishu-codex-bridge`；同步代码、唯一 reference、测试和必要镜像。
4. 运行 fast lane 或更小的 focused validation；失败先归类为 source、test-contract、environment 或
   product-capability。日常开发不运行 P3。
5. 相同字节、相同输入和相同失败原因不重复运行同一 gate。
6. 代码、合同、inventory 与 source version 定稿后只更新一次 cachebuster；随后冻结源码，运行
   Gate A、所需 fresh Gate B，并只在 concurrency/persistence/retry/fencing/outbox/transport 受影响时
   运行 P3；正式 P3 不得低于 25 iterations。
7. 证据通过后才执行 runtime upgrade 与 plugin reinstall；最后在新任务核对加载 surface 并读取
   `bridge readiness -Json`，分别报告 `mvp` 与 production，只保留各自真实存在的
   blocker。MVP ready 不能清除 production 的 product `run_once` 或 caller/turn blocker。

两阶段 Hook→runtime 发布中，Hook-only refresh 会故意删除旧 manifest，使启动保持 fail-closed；
Gate B/Soak 的唯一可接受 warning 形状及其严格校验见 `references/release-audit.md`。

### 停止死循环规则

- 只允许在“命令尚未执行”的 quoting/transport 失败上自动修正并重试。
- 动作可能已开始时立即按 R-REPLAY 终态，不换入口重放。
- 同一 hypothesis 最多一次受审实验；新增信息改变 hypothesis 后才允许新实验。
- 测试失败必须先修源码或测试合同；不得用重复运行获取偶然绿色。
- 产品能力缺失时只保留一个有明确风险边界的实验替代，不叠加第二套模拟或重试路线。
- 完成的经验进入本手册；原始细节留在 local-only `EXPERIMENT-LOG.md`，不加载进普通任务。

## 6. Source、runtime 与插件发布

唯一编辑根为 `plugins/feishu-codex-bridge`。状态角色严格分开：

| 角色 | 权限 |
|---|---|
| canonical source | 唯一开发和发布输入 |
| repo Marketplace entry | 只解析 source route |
| installed runtime | 已签名 Bridge/Hook 字节，只能通过 upgrade 更新 |
| versioned plugin cache | 安装快照，只诊断、不直接编辑 |
| external retained snapshot | 测试证据，只验证、不执行开发命令 |

发布顺序：

1. 完成代码、合同、测试、inventory 与 source version，并运行影响范围内的 focused checks。
2. 更新一次 cachebuster，然后冻结 canonical source。
3. 对冻结字节运行 Gate A 与 fresh Gate B；只有 concurrency/persistence/retry/fencing/outbox/transport
   受影响时才运行 P3。
4. Bridge 停止态 runtime-only upgrade。
5. 从同一 Marketplace 安装插件并比较 canonical/installed manifest。
6. 在新任务核对实际加载 Skill/MCP 和 lifecycle Hooks。
7. 单独完成 lifecycle-Hook trust review，读取 readiness 中彼此独立的 `mvp` 与
   production 结论。
8. 只有用户明确要求才 commit、push 或 tag。

任何受审可执行/contract 字节变化都会使对应 exact-source 动态证据失效；纯文档变化是否需要动态门
由第 4 节影响矩阵决定，不再机械触发 Soak。

## 7. 数据与 final 不变量

- scope 和 responder 只用稳定 ID，不按标题或显示名匹配。
- `/init` 的 title/project label 只可在绑定 exact request/fence/snapshot 的 sealed ephemeral staging 中
  存在到 Bridge 单次消费；此后十分钟快照只在内存并绑定发起人。project root/path 从不 admitted。
- 持久 binding 只保存稳定 task/host/project ID 与有界 operation receipt，不保存 display 或 path；
  receipt 只证明本地 selection/inspection/binding 身份一致，不是产品 caller/turn attestation 或 `run_once`。
- 当前只允许 catalog、已有非归档 task 选择、exact read-only inspection 与原子绑定；创建、恢复、
  归档、compact、解除连接和回复方式变更都不存在。未来 mutation 必须使用新的封闭合同，不复用当前 lane。
- operation/event idempotency、claim、fence、stage、terminal 与 release 必须绑定同一身份。
- retention 只清理 terminal state；未解决 claim、active read-only/Final Callback staging 与 sealed outbox
  不是普通垃圾。
- authoritative final 保持原始字符串；trim 只用于拒绝空值，oversize fail closed。
- 第一笔发送前冻结 outbound piece plan；不确定附件/发送结果不重放。
- helper stdout、health 与 receipt 保持 answer-free；Unicode 只 JSON escape/parse 一次，不经过插值 shell。
- terminal/crash reconciliation 必须 scrub answer、digest、length 和 pending plan。

细节只见 [architecture.md](references/architecture.md) 与
[permissions-and-hooks.md](references/permissions-and-hooks.md)。

## 8. 文档职责

- `skills/feishu-codex-bridge/SKILL.md`：intent guide 与最小 stop conditions。
- `feishu-codex-bridge-skill.md`：普通安装、配置、诊断和当前 hold 行为。
- 本文：稳定演化规律、验证选择和发布顺序。
- `references/architecture.md`：所有权、状态、恢复与 marker registry。
- `references/beeper-run-once-candidate.md`：未来产品 `run_once` 语义。
- `references/release-audit.md`：Gate A/B 与 evidence 合同。
- `references/p3-bounded-soak.md`：Soak 合同。
- `HANDOFF.md`：当前版本、当前证据、当前 blocker 和唯一下一步。
- `EXPERIMENT-LOG.md`：local-only 原始实验记录，不进入发布包。

专项实现细节只存在一个 reference。文档达到稳定结论后，从 HANDOFF 删除流水；不要把实验过程复制到
Skill、AGENTS、README 和 upgrade 四处。
