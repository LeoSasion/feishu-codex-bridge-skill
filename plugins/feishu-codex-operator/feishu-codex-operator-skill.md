# Feishu Codex Operator 使用说明

这是保留的中文导航页，不另维护一份运行策略。

- [安装与日常操作](README.md)：Windows 命令、飞书 `/init` 与只读诊断。
- [开发 skill](skills/feishu-codex-operator/SKILL.md)：源码权威、按任务加载的参考资料。
- [架构](references/architecture.md)：投递、回调、额度缓存与等待策略。
- [命名](references/terminology.md)：Operator、Beeper、Responder、wake-up signal 与 wake lease。
- [升级](upgrade-operator.md)：新命名切换与数据保留。

Beeper 只中继，Responder 执行业务，Operator 负责接收、路由和回传。
