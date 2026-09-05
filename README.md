# Feishu Codex Operator

[中文](#中文) | [English](#english)

## 中文

飞书 Codex 接线员把飞书会话映射到现有 Codex Desktop 任务：
Operator 管接收与回传，Beeper 做最小中继，Responder 执行业务。

插件名为 `feishu-codex-operator`，本次发布标签为 `v1.0.0`。
GitHub 仓库地址仍为 `LeoSasion/feishu-codex-bridge-skill`。

- [安装和使用](plugins/feishu-codex-operator/README.md)
- [开发 skill](plugins/feishu-codex-operator/skills/feishu-codex-operator/SKILL.md)
- [旧安装切换](plugins/feishu-codex-operator/upgrade-operator.md)
- 源码：`plugins/feishu-codex-operator`
- Marketplace：`.agents/plugins/marketplace.json`

在 Codex 中添加本仓库 Marketplace，并选择标签 `v1.0.0`，安装
`feishu-codex-operator@feishu-codex-operator`。开发未发布变更时使用本地 Marketplace。
新插件载入后使用 `$feishu-codex-operator` 配置项目。
运行态、缓存、数据库和本地交接记录不属于发布源码。

## English

Feishu Codex Operator connects Feishu scopes to existing Codex Desktop tasks.
The plugin is `feishu-codex-operator`, released as `v1.0.0`; the GitHub repository
address is unchanged. Add this repository's Marketplace at the `v1.0.0` ref and
install `feishu-codex-operator@feishu-codex-operator`.
Use the local Marketplace for unpublished development changes.
See the linked plugin README for setup and the upgrade guide for a coordinated
cutover from the previous name.

This route is not product-level exactly-once. Rare failures can omit or
duplicate execution; do not use it for irreversible actions.
