# 保留思路：统一工具映射与 Desktop 搜索桥接

状态：**Desktop 搜索替换方案暂停，原思路继续保留；普通工具协议研发已恢复。**

2026-09-09，项目所有者明确要求暂停这一开发方向，将方案记录为 Markdown，
并在没有明确删除要求时保留。常规整理、升级、重构、合并或清理未完成工作，
均不得自动删除本文或使其内容丢失；也不得据此自动恢复开发、启用配置或部署。
只有所有者明确要求删除时，才可以删除这一保留思路。

同日后续，所有者明确要求参考 CC Switch 开始研发。普通 function/custom、namespace、
客户端工具发现的可逆协议转换据此继续开发；这不撤销本文的保留要求。
新增 `additional_tools` 输入声明适配复用原 Router，不启用下文的托管搜索替换或
通用 `codex_tool_call` MCP 执行桥。当前进度见[工具兼容架构](responses-tool-compatibility-plan.md)。

2026-09-09，所有者进一步明确授权清理 ChatGPT Web 相关废弃代码，并要求保留
致谢和简洁的架构流程。原私有草稿代码、补丁及其归档清单已清理；本文的设计记录、
下方架构流程和 README 致谢继续保留。清理不改变当前 Router 实现或搜索方案的暂停状态。

## 来源与致谢

工具声明转换、保存工具原始身份，以及把调用交回当前 Codex 任务执行的设计，
参考了 [miuuyy/codex-chatgpt-web](https://github.com/miuuyy/codex-chatgpt-web)。
感谢 miuuyy 分享这一实现思路，帮助我们解决工具调用方案设计中的难题。
核对源码时参考的版本为 `e85e3693fdb4e3e033348c08df0298c20fcdb612`。

本项目使用 LM Studio 的 Responses API；参考项目为 ChatGPT 网页提供的
`codex_tool_call` MCP 入口不必整体移植。搜索 MCP 若用于本方案，应由 Desktop
配置和执行。LM Studio 服务端 MCP 是另一条执行通道，未在本方案中启用。

## 拟采用的流程

```text
Codex 原始 tools
      ↓
Router：转换声明，保存原始类型、名称、namespace 与映射
      ↓
LM Studio / Qwen：接收工具定义和用法说明，产生工具调用
      ↓
Router：校验完整响应，在执行前恢复真实 Desktop 调用
function_call / custom_tool_call / tool_search_call
      ↓
当前 Codex 任务：权限审批、工具执行
      ↓
真实工具结果：function_call_output / custom_tool_call_output / …
      ↓
Router：按同一映射转换结果
      ↓
模型继续推理或回答
```

Prompt 说明工具用法；真实身份映射和校验由代码负责。
恢复调用类型发生在执行之前；执行后回传的是结果，而不是新的调用。

## 2026-09-09 补充：API 代理与 MCP 执行桥的区别

所有者补充了 CC Switch 的实现对比。本补充完善保留记录，不恢复开发。

CC Switch 的 API 代理在请求侧转换工具声明并保存工具上下文，在响应侧恢复
custom、namespace 和客户端工具发现的 Responses 语义，再交给 Codex 执行。
该流程不需要另建 `codex_tool_call` MCP 执行入口；参考其
[本地路由说明](https://github.com/farion1231/cc-switch/blob/main/docs/user-manual/en/2-providers/2.1-add.md#codex-local-routing-and-model-mapping)、
[转换源码](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/proxy/providers/transform_codex_chat.rs)
及 [v3.16.1 工具恢复说明](https://github.com/farion1231/cc-switch/blob/main/docs/release-notes/v3.16.1-zh.md)。

本项目现有 Router 已处于同样的 Codex API 请求/响应位置。普通工具继续采用
“代码转换声明 → 保存原始身份 → 校验并恢复调用 → Desktop 执行”的路径，
无需再经通用 MCP 桥接。MCP 可以提供搜索、GitHub、数据库等具体能力；这些
工具若已作为当前任务的函数声明暴露，仍走普通映射。模型需要工具定义和用法说明，
但 Prompt 不能替代协议转换、身份校验或执行权限。

这里的“有状态”指本次请求的工具映射和流式校验状态。当前实现由 Desktop 提交的
tools/input 重建对应关系，不代表 Router 需要持久化业务对话或另建任务执行循环。
“可逆”限于明确支持的工具契约；已经支持的原生类型不必强制改成 function。

托管 `web_search` 仍需独立的搜索执行能力。CC Switch 的
[Claude→Responses 搜索说明](https://github.com/farion1231/cc-switch/blob/main/docs/guides/claude-codex-routing-guide-en.md)
同样要求上游实现托管搜索。另须注意，其 Chat 转换器对未知类型存在直接忽略分支，
不能据此概括为所有路径都明确拒绝；本项目保留 `unsupported_tool_type` 的显式拒绝。
普通工具协议转换和待研究的搜索能力替换必须分别评估，不能把后者当作无损类型还原。

## 已有基础与暂停范围

已有适配器保存 `ToolSpec` 与 `RequestContext`，支持显式配置的 function/custom、
namespace、客户端工具发现及 JSON/SSE 调用还原。相关源码和既有诊断修复保留。
`tool_search` 是发现工具；它不是联网搜索。

暂停的是新增的“托管 `web_search` → 实际 Desktop 搜索工具”能力绑定。
暂停时曾编写未验证的实现和测试草稿，尚未执行该草稿测试、部署或启用。
草稿曾保存为私有补丁及源文件快照，并从当前可执行源码改动中撤出，未进入安装和
发布内容。该归档现已按所有者明确要求清理；已有适配器和错误分类修复继续保留。

当前显式适配路由仍对托管 `web_search` / `web_search_preview` 返回
`unsupported_tool_type`。这条错误分类修复不代表搜索兼容已经完成。

## 留待重新评估的两种绑定

| 方案 | 前提 | 需要保持的边界 |
|---|---|---|
| 绑定当前任务的搜索 function/MCP 函数 | 当前请求真实声明且已加载该工具；准确 name/namespace 和参数 schema 已核对 | 模型调用还原到原工具，由 Desktop 执行；不凭名称猜测 |
| 通过当前任务的 `exec` 访问内部搜索工具 | 当前请求声明并允许该 exec；端点策略明确指定内部工具及参数格式 | 固定桥接程序在 Desktop 运行时核对真实工具列表；查询仅作为 JSON 数据；一次调用，不自动重试 |

曾考虑先支持单一文本查询，并对暂时不能保留的域名限制、位置、缓存模式、
搜索上下文大小及图片结果明确拒绝。具体配置字段和协议版本仍是草案，
不能视为已支持的公开接口。

已安装搜索 MCP 或某个工具名称含有 `search`，不等于该工具可供当前任务调用。
只看见 `exec` 声明也不等于 Router 知道其内部工具列表。原始托管工具与替代
执行工具的身份、参数约束和结果语义均需要明确记录，不能宣称是无损改名。

## 将来恢复开发前的检查点

- 重新检查当前 Desktop、LM Studio、模型和工具接口，确认是否仍需这一方案。
- 明确目标工具、允许范围、参数和结果契约；保持 `none`、指定工具、并行和取消约束。
- 验证未知/歧义目标、schema 变化、未加载、权限拒绝、错误及取消均不会产生重试。
- 验证文本、Unicode、来源 URL、内容边界、call_id 和历史关联；不伪造搜索结果或引用。
- 验证 JSON/SSE 在一致成功终态前不交付可执行调用；保留原生模型和原生搜索通路。
- 分别记录隔离测试与真实 Desktop 搜索及审批证据；检查通过不自动启用服务或部署。

更完整的既有映射边界见 [工具兼容架构](responses-tool-compatibility-plan.md)。
本文保留的是可重新评估的设计，不是继续开发的排期或自动执行指令。
