---
name: feishu-codex-bridge
description: "Configure, install, diagnose, and safely develop the Feishu/Lark-to-Codex Desktop bridge. Use for Feishu permissions, locked access, Bridge/plugin/Hook lifecycle, the isolated single-Beeper queue producer, Responder-owned Final Callback, and readiness evidence. The local grant and bearer capability are not product-level run_once or caller/turn attestation; Desktop responders retain ownership and no App Server fallback is allowed."
---

# Feishu Codex Bridge

当前源码合同版本：`4.2.0-alpha.64`。

## 按意图加载

- 普通安装、配置、权限、Bridge/插件启用、当前 hold、诊断或恢复：完整阅读
  [feishu-codex-bridge-skill.md](../../feishu-codex-bridge-skill.md)。
- 开发、协议/Schema、测试、迁移、审计、发布或 tag：完整阅读
  [upgrade-bridge.md](../../upgrade-bridge.md)，再按其路由读取相关 `references/`。
- 继续当前工作区的未完成事项才读取本地 `HANDOFF.md`；它不是安装快照或稳定合同。
- 实验只读取对应专项 reference，并记录到 local-only `EXPERIMENT-LOG.md`；不要加载或
  复述无关历史资产。
- 字段与时序分别以 `references/architecture.md`、scheduler run-once、permissions/Hooks
  专项 reference 和 closed schema 为准；不要从 AGENTS、缓存或历史 receipt 反向推导协议。

## 五条不可省略的 guard

- **权威与 producer：** 只认 canonical source-route 和当前版本能力；先检测最新 Desktop/CLI，
  不为旧 Desktop 保留可执行兼容分支。只允许隔离的 `beeper` 本地 grant +
  exact Desktop-bundled CLI queue 一次尝试；若同一 page 在固定 grace 后仍为 `reserved`，只允许
  一次无 payload 的 exact Beeper deep-link 冷加载，失败/领取超时先原子封口且不重放。历史
  namespace 永久不可执行，旧 hold 永不补发。
- **Beeper 与 ownership：** 每个 installed Bridge namespace 恰好一个非历史、
  不绑定飞书 scope、不成为业务 Responder、不自投递的 Beeper；它只用闭合的
  Desktop task-coordination surface。`/init` 只允许 bounded catalog 与 exact read-only inspection，
  确认后的 binding 由 Bridge 原子提交；其他 Desktop mutation 仍关闭。Bridge/App Server/CLI/DB/UI 等都不是第二目标客户端；
  唯一 deep link 只加载 Beeper，不打开业务 responder 或传 final；Desktop responder 独占上下文、
  执行、工具和 authoritative final。
- **身份与 no replay：** 使用稳定 scope/task ID、确定性 idempotency、同一 request/fence/owner
  身份和 first-terminal sealing。`may_have_started=true` 永不重放；retention 不删除未解决 claim
  或无权威 terminal receipt 的 Final Callback staging。成功绑定只追加一次通俗风险提示，不增加确认门。
- **Responder-owned final：** Bridge send 必须先绑定 exact request/fence/Beeper/responder/prompt/capability；
  Responder 用 `submit_final_callback` 提交自己的 exact final，只接受
  `final_callback_source=final_callback`。Beeper 不得提交；bearer capability 不证明产品 caller/turn。
  Native field、readback、App Server、shell、UI、DB、rollout、OCR、clipboard 均不是 fallback。
- **运维与证据：** 用户请求范围内的本地 install/upgrade/config/lifecycle/Hook/plugin/Schema
  工作自动完成，但不扩大到历史 producer 或未请求外部变更。locked access、PID/manifest/parity、
  精确 Hook trust 和 no-secrets 始终 fail closed；Bridge 停止且无 live Page 时可跑最小 focused
  tests，Gate B/soak 仍走受审外部 supervisor。MVP 可用不等于 product exactly-once readiness。
