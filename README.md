# Feishu Codex Operator

[中文](#中文) | [English](#english)

## 中文

飞书 Codex 接线员把飞书会话映射到现有 Codex Desktop 任务：
Operator 管接收与回传，Beeper 做最小中继，Responder 执行业务。

插件名为 `feishu-codex-operator`，下方公开安装示例固定到标签 `v1.1.0`。
GitHub 仓库地址仍为 `LeoSasion/feishu-codex-bridge-skill`。

- [安装和使用](plugins/feishu-codex-operator/README.md)
- [开发 skill](plugins/feishu-codex-operator/skills/feishu-codex-operator/SKILL.md)
- [旧安装切换](plugins/feishu-codex-operator/upgrade-operator.md)
- 源码：`plugins/feishu-codex-operator`
- Marketplace：`.agents/plugins/marketplace.json`

在 Codex 中添加本仓库 Marketplace，并选择标签 `v1.1.0`，安装
`feishu-codex-operator@feishu-codex-operator`。开发未发布变更时使用本地 Marketplace。
新插件载入后使用 `$feishu-codex-operator` 配置项目。
运行态、缓存、数据库和本地交接记录不属于发布源码。
`v1.1.0` 包含运行时源码 `4.2.0-alpha.122`；发布源码不代表本机已安装版本。
下方 alpha 说明记录功能引入的版本，历史验收也不能替代当前模型与适配版本的验证。
当前源码验证见[发布审计](plugins/feishu-codex-operator/references/release-audit.md)；
安装版本应通过只读 `operator status -Json` 与 runtime manifest 核对。

工具调用桥接的设计思路参考了
[miuuyy/codex-chatgpt-web](https://github.com/miuuyy/codex-chatgpt-web)，
感谢 miuuyy 帮助我们解决工具调用方案设计中的难题。
普通工具协议适配继续参考 [CC Switch](https://github.com/farion1231/cc-switch) 的
请求内工具映射与 Responses 调用还原。alpha.118 可显式选择完整 JSON 上游，
验证后输出标准 SSE，适配 Gemma 所在端点的流式参数快照差异；不会自动重试。
alpha.119 可显式检查“完成”响应是否包含回复或工具调用，识别只有思考内容的
空白结束；不从思考文本提取调用，不改变原生模型通路。
alpha.120 补齐 JSON 展开事件流的大小检查，超限时在首个事件前终止，并修复长回复误拒绝。
alpha.121 支持推理内容的合法空值，并拒绝异常历史和未完成的推理项，保留工具释放前的校验。
alpha.122 允许 Desktop 打开时更新验证标签，仍检查 Operator/路由器已停止、回调清空及证据一致性。
alpha.117 源码新增显式启用的
`additional_tools` 声明转换，沿用 Desktop 的执行与审批；详见
[工具兼容架构](plugins/feishu-codex-operator/references/responses-tool-compatibility-plan.md)。
新的 Desktop 搜索扩展已暂停，作为
[保留思路](plugins/feishu-codex-operator/references/retained-desktop-search-idea.md)
记录；未经所有者明确要求不得删除或自动恢复开发。

## English

Feishu Codex Operator connects Feishu scopes to existing Codex Desktop tasks.
The plugin is `feishu-codex-operator`, released as `v1.1.0`; the GitHub repository
address is unchanged. Add this repository's Marketplace at the `v1.1.0` ref and
install `feishu-codex-operator@feishu-codex-operator`.
Use the local Marketplace for unpublished development changes.
The release includes runtime source `4.2.0-alpha.122`. Alpha-version notes record
when features were introduced; neither source publication nor historical trials
prove that an installation has been upgraded or a current model has passed acceptance.
See the linked plugin README for setup and the upgrade guide for a coordinated
cutover from the previous name.

This route is not product-level exactly-once. Rare failures can omit or
duplicate execution; do not use it for irreversible actions.
