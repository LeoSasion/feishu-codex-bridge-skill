# Feishu Codex Bridge

[中文](#中文) | [English](#english)

## 中文

本仓库已从单一 Codex Skill 升级为完整的 Codex 插件，但保留原仓库名，方便旧用户继续识别和查找。历史 Skill 版本仍可通过既有 tags 获取；`main` 现在发布 Marketplace 布局下的插件。

### 安装

使用当前 Codex CLI 直接添加本仓库 Marketplace，然后安装插件：

```powershell
codex plugin marketplace add LeoSasion/feishu-codex-bridge-skill --ref main
codex plugin add feishu-codex-bridge@feishu-codex-bridge
```

安装后请开启一个新的 Codex 任务，并使用 `$feishu-codex-bridge` 完成飞书配置、Bridge 安装和只读诊断。

### 仓库布局

- `.agents/plugins/marketplace.json`：Codex Marketplace 入口。
- `plugins/feishu-codex-bridge`：完整、自包含的插件。
- `.codex/feishu-codex-bridge-runtime`：安装后在项目内生成的运行代码、配置与持久状态；它不属于发布源码，也不同于 `~/.codex/plugins/cache/...` 插件缓存。
- [插件说明](plugins/feishu-codex-bridge/README.md)
- [Skill 入口](plugins/feishu-codex-bridge/skills/feishu-codex-bridge/SKILL.md)

从旧版 `.codex/feishu-bridge` 升级时，Bridge 必须先停止；升级流程会整体迁移该目录以保留配置和状态。若新旧目录同时存在，安装器会拒绝自动选择或合并。

### 安全边界

Bridge 使用一个隔离 Beeper 协调真实 Codex Desktop Responder，并由 Responder 通过一次性 Final Callback 提交最终答案。当前本地队列路径不是产品级 exactly-once；极少数异常可能造成重复或漏执行，请勿用于转账、删除数据等不可逆操作。

## English

This repository has moved from a standalone Codex Skill to a complete Codex plugin while keeping the established repository name. Existing tags preserve historical Skill releases; `main` now publishes the plugin through a repository Marketplace.

### Install

```powershell
codex plugin marketplace add LeoSasion/feishu-codex-bridge-skill --ref main
codex plugin add feishu-codex-bridge@feishu-codex-bridge
```

Start a new Codex task after installation and invoke `$feishu-codex-bridge` to configure Feishu, install the Bridge runtime, and run read-only diagnostics.

The canonical source lives under `plugins/feishu-codex-bridge`. Installation creates the project-local runtime at `.codex/feishu-codex-bridge-runtime`; this is distinct from the Codex plugin cache under `~/.codex/plugins/cache/...`. A stopped legacy `.codex/feishu-bridge` runtime is migrated as a whole, while ambiguous dual directories fail closed.

The local queue path is not product-level exactly-once. Rare failures may duplicate or miss work, so do not use it for irreversible actions.
