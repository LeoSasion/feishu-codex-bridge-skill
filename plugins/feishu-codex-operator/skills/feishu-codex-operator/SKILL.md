---
name: feishu-codex-operator
description: Configure, install, diagnose, and develop Feishu Codex Operator, the Feishu/Lark-to-Codex Desktop integration.
---

# Feishu Codex Operator

只修改仓库 `plugins/feishu-codex-operator`。项目 runtime 与插件 cache 是安装产物，
不按时间或版本号提升为源码。开始变更前核实 Git、现有改动、精确进程和 pending callback。

## 工作边界

- Operator 管接收、持久路由和回传；固定 Beeper 只发送一次；Responder 独占业务执行与 final。
- Final Callback 的 `request_id` 仅关联请求，不是身份认证。不要恢复 Page/capability/claim。
- queue 接受或结果不确定后不重放；只有明确的 Spark 额度拒绝允许一次 Luna 兜底。
- 未配置或留空时默认 Luna/low；可显式选择本地 `beeper`/low、Spark/medium 或
  Luna/low。本地 provider 随运行时安装，协议外输入只返回已确认的固定身份声明。
  目录使用 `visibility: list`，但 Desktop 下拉框及固定任务默认 provider 的接入仍待验证；
  不得以目录可见性代替路由证据，也不能替换原生 provider/catalog 条目而影响 Spark/Luna。
  可选 Python Responses 路由器仅追加目录项，原生请求仍送原生后端；不依赖
  LiteLLM，不转换 Chat Completions。涉及此功能先读 `../../references/model-router.md`。
  registry v2 按明确能力适配外部工具，v1/null 保留透传；代码与调用 ID 不改写，
  工具调用仅在成功终态完整校验后交付。探测、隔离 CLI 执行和真实 Desktop 验收分别记录。
  能力复用、注册前检查和多轮评测再读 `../../references/responses-acceptance.md`；
  私有 profile/receipt 不随发布复制。只读补丁计划与实际文件写入审批分开验收。
  全局入口启用与安装分开；常驻生命周期、原生辅助工具及真实 Desktop 下拉/默认值
  尚未验收，不能根据隔离测试自动启用。
  此处“全局入口”指模型请求路由；可恢复的桌面/开始菜单快捷方式初始化不代表启用模型路由。
  本地 provider 失败或不确定时不兜底。
  所有 Operator 添加给 Spark 的外层指令、内层回调/附件说明
  均用简洁结构化英文，飞书原句不翻译。附件元数据用无损 JSON 转义，不改真实路径。
  显式 Spark 的明确额度拒绝才允许一次 Luna/low 备选；中文控制模板仅可供 Luna 显式诊断，Spark 始终英文。
  Spark/low、high 仅供显式受控诊断；不覆盖 Responder 设置，也不构成重试理由.
- 保留 `wake lease` 名称和行为。wake-up signal 是动作，deep link 是当前实现，可能导航
  Desktop；只针对 Beeper，不能据此认定执行成功。`itemsView=notLoaded` 不是驻留状态。
- App Server 只用于目录、额度、无正文生命周期观察，不能接管 Desktop 任务或运输最终答案。
- 用户授权只覆盖其请求；本地安装和生命周期可自动执行，发布、凭据、跨项目变更不自动扩大。

## 按任务读取资料

- 改架构、配置或投递逻辑：先读 [Architecture](../../references/architecture.md)。
- 改 Beeper 提示、模型、推理强度、wake-up、观察器或真实 E2E：必须再读
  [Beeper E2E lessons](../../references/beeper-e2e-lessons.md)。保留失败样本，不重放；
  成功必须是同一飞书消息经 Final Callback 收到精确关联回复，不能用 Desktop 输出替代。
- 安装、迁移或升级：读 [README](../../README.md) 和 [Upgrade](../../upgrade-operator.md)。
- 初始化入口或卸载：读 [Installation and removal](../../references/installation-and-removal.md)。
  首次写入前说明入口改动、原件备份、任务栏手动固定和安全卸载顺序。初始化请求已覆盖
  说明后的当前用户入口配置，不重复索要许可；不自动启用未经配置的模型路由。
  用户要求移除插件时，先预览并执行项目安全卸载，成功后才移除 Desktop 插件。
  不把 Desktop 直接删除插件说成自动恢复，不覆盖后续用户修改，也不伪造旧安装原件记录。
- Hook 审核：读 [Permissions and Hooks](../../references/permissions-and-hooks.md)。
  首选 Desktop“设置 → 钩子”；Desktop 没有 `/hooks`，Windows CMD 启动 CLI 的动态路径命令仅作备选。
- 命名变更：读 [Terminology](../../references/terminology.md)。新执行面只用 Operator，
  旧名称仅出现在一次性迁移识别、退役状态和未更名的 GitHub 地址中。
- 飞书客户端安装或权限问题：分别读 [Desktop client](../../references/feishu-desktop-client.md)
  或 [Authentication and permissions](../../references/feishu-auth.md)，不要默认加载。

## 开发与验证

精确服务已停止、无 pending callback 后，使用 README 的隔离单元测试与发布审核命令。
测试和诊断不发真实消息；真实 E2E 按当前 AGENTS 授权核对目标与身份。

同步源码、安装清单、MCP、测试、文档和规则镜像。根 `AGENTS.md` 与
`assets/AGENTS.feishu-codex-operator.md` 保持 byte-identical。安装只能通过项目脚本；
不要手改 runtime/cache。除非用户要求，不提交、不推送、不发布。
