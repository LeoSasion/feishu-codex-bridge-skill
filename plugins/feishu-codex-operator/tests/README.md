# 回归测试维护

先根据改动选择测试。修复完成后执行相关模块；跨层改动、发布或部署前执行全量。
所有测试仍要求精确 Operator 主服务已停止且无待回调请求，并使用隔离临时状态。

## 按改动选择

| 改动 | 首选测试 |
| --- | --- |
| 工具声明、历史、JSON/SSE 还原 | `test_responses_tools`、`test_responses_events`、`test_responses_router` |
| HTTP/WS 分流、传输大小、认证隔离 | `test_model_router`、`test_responses_router`；热加载另加 `test_registry_reload` |
| 能力或验收绑定 | `test_responses_profiles`、`test_responses_verification`、`test_responses_labels`；探测/评测/指标各运行对应模块 |
| Beeper、回调、调度 | `test_beeper_relay`、`test_final_callback`、`test_performance`；本地 provider 另加 `test_beeper_provider` |
| 持久化、绑定、额度、观察器 | 对应的 `test_state`、`test_routing`、`test_rate_limits`、`test_responder_observer`，以及有调用关系的模块 |
| 配置或 App Server | `test_config`、`test_app_server`、`test_app_server_catalog`，以及受影响的调用方 |
| 安装、入口、卸载、模型发现 | 对应的 `test_install_upgrade`、`test_operator_installation`、`test_desktop_entry`、`test_operator_uninstall`、`test_model_router_config`、`test_model_router_lifecycle`、`test_lmstudio_discovery` |
| 发布清单、规则镜像、来源选择 | 发布审计；来源选择逻辑变化另运行 `test_source_route_contract` |
| 纯文档措辞 | 发布审计与链接/差异检查即可；不重跑模型或 CLI 执行用例 |

例如，从仓库根运行工具适配的相关模块：

```powershell
Push-Location .\plugins\feishu-codex-operator\tests
try {
    python -B -m unittest -v test_responses_tools test_responses_events test_responses_router
} finally {
    Pop-Location
}
```

全量仍使用标准 unittest，没有新增测试框架或隐藏的排除清单：

```powershell
python -B -m unittest discover -s .\plugins\feishu-codex-operator\tests -v
```

真实 CLI 用例通过 `CODEX_OPERATOR_TEST_CLI` 显式选用当前 Desktop 安装内的
可执行文件；不要使用 PATH 中的 Codex。目录用例还需
`CODEX_OPERATOR_TEST_CATALOG` 指向原生目录 fixture，不能用已追加 Beeper/外部模型的
实时缓存重复合并。临时 CLI home、模拟上游和只读 fixture 验证不等于真实 Desktop 验收。
不设置这些变量时，CLI 用例会明确跳过；发布前须按适用范围补跑并记录跳过原因。

## 覆盖归属与本次去重

| 删除或合并的检查 | 保留覆盖的位置 |
| --- | --- |
| `test_agents_rules.py` 的规则逐字匹配与重复镜像检查 | 发布审计核对完整镜像字节；Beeper、回调、额度、观察器测试验证实际行为 |
| 配置测试中的脚本词句、部分模块名和固定发布版本 | 安装测试核对全部 manifest 文件摘要、启动清单和实际 `status` JSON；版本一致性由发布审计负责 |
| 单独的纯算术 exec、串行 exec、嵌套 MCP 成功往返 | 合为一次真实 CLI 的 exec → MCP → 结果回传，同时断言串行限制；alpha.122 改用完整 JSON 上游验证事件投射，另一例保留 SSE 上游的图像工具能力拒绝 |
| CLI 评测中重复的格式/错误组合 | 纯函数测试覆盖 LF/CRLF、空白上限、缺失/非法文本；真实 CLI 保留一次格式策略对照和每类停止违规 |
| 重复的 HTTP 参数失败、上游 429 用例 | 工具单元测试保留具体参数语义；HTTP/WS 的发送前拒绝及 JSON 上游错误保真用例覆盖路由边界 |
| 分散的可选协议参数与证据失效样板 | `test_optional_codecs_are_explicit_and_invalidate_contract_evidence` 统一用表格覆盖，各 codec 专属行为继续独立测试 |

HTTP 与 WebSocket、JSON 与流式、内存校验与持久化恢复的边界不同；名称相似不表示重复。
成功终态前不释放工具、不确定结果不重放、权限拒绝、Unicode 保真、并发竞争、恢复与
原生通路隔离都有独立作用。不要为了降低测试数删除这些用例，也不要把多个无关行为
塞进一个方法来隐藏数量。新增回归应对应可复现的问题或尚未覆盖的行为。

Alpha.124 在现有 probe 往返用例中加入 JSON 字符串/参数对象与 LF/CRLF 的格式组合，
全部保留精确比较；只新增一个字节差异统计的边界测试方法。差异统计只记录固定计数
和首个不同字节位置，不保存源码。格式变体通过不能覆盖另一个变体的失败。

## 本地暂存与历史记录

标准发现入口只扫描本目录。私有目录中的必要恢复原件、失败记录和验收 fixture
不加入当前测试集合。已核实无用的历史源码、runtime 和测试副本可以清理；已删除
版本不再作为恢复来源。原生目录 fixture 是显式 CLI 测试数据；读取它不会运行旧测试。

暂停的 Desktop 搜索方向仍仅保留设计记录。历史失败不因测试合并而改判，必要恢复
原件和验收证据也不作为重复测试删除。精简结果和后续验证见
[发布审计](../references/release-audit.md)。

Alpha.125 adds two behavioral methods for encoded-history argument bounds and
HTTP/WS rejection before dispatch. Existing catalog and invalid-output checks
cover the standard-function patch declaration and fixed shape diagnostics. One
standard-function scenario extends the real CLI evaluation table, bringing the
CLI scenario count from 13 to 14 without duplicating the existing exec cases.
The experimental line-array codec was removed after live failures and contributes
no test methods or runtime capability to the canonical suite.
Alpha.126 adds three behavioral methods: reject completed/failed cancellation
races, run an actual disposable CLI cancellation before JSON response headers,
and exercise standard-function source/result probes including a CRLF mismatch.
The CLI matrix has 15 scenarios. Existing shortcut installation/restoration checks
also verify distinct extension-entry metadata and preservation of official links.
No live provider cases run during unittest discovery.

Alpha.127 adds one terminal-output modality boundary method. The existing
JSON/SSE/WebSocket rejection matrix now includes plain JSON and an invalid
assistant-content tail after a valid tool call. The existing probe whitespace
matrix covers already-supported legacy string/text answers too. No new fixture
executes generated source, and private representation experiments remain outside
test discovery and canonical acceptance profiles.

Alpha.128 adds `test_terminal_eval` and a profile-identity boundary method.
Pure checks cover exact native argument/history bytes, typed command admission,
paired native results, distinct policy failures, isolated child PATH and unchanged
LF/CRLF/mixed files. The two actual CLI scenarios use mock upstream responses,
not live providers, and retain read-only/never-approve settings. Set the exact
`CODEX_OPERATOR_TEST_POWERSHELL` and/or `CODEX_OPERATOR_TEST_BASH` executable in
addition to `CODEX_OPERATOR_TEST_CLI`; never infer Git Bash from PATH on Windows.

`CODEX_OPERATOR_TEST_TERMINAL_OUTCOME` defaults to `passed`. For a separately
recorded refusal-boundary run it may explicitly be `policy_rejected`; the tests
then require failed execution receipts, one upstream dispatch, exact native
policy rejection and unchanged files. A passing refusal test is not terminal
execution acceptance. Do not choose this expectation automatically after failure
or use it to reclassify old acceptance results. Other environments must declare
and verify their actual outcome separately. This adds two actual CLI scenarios
to the existing fifteen, without rerunning live provider probes.

Alpha.129 adds three focused methods for native-workspace lifecycle, the single
literal file read and explicit child-only Windows backend selection. Existing
result/profile tests now reject forged zero-exit text, nonzero process status,
token-setup failures and contradictory successful receipts. Current `Output:`
and legacy `Final output:` envelopes retain exact body comparisons.

Set `CODEX_OPERATOR_TEST_WINDOWS_SANDBOX=unelevated` explicitly for a new Windows
terminal run using the restricted-token backend. The read-only sandbox and
never-approve policy remain fixed. The harness makes no backend retry or automatic
fallback. PowerShell byte output avoids dependence on the constrained shell's
console encoding, while preserving original BOM, Unicode and EOL bytes. It does
not establish Unicode console-text output or Windows PowerShell 5 compatibility.

The alpha.129 development run passed the PowerShell byte-reading case. Git Bash
still failed inside the native sandbox while creating its MSYS signal pipe
(Win32 error 5, native exit -1073741502). That failed result is retained; its
execution is not repeated or counted as passed in the final full regression.
The full run selects PowerShell and explicitly leaves the Bash opt-in unset.
This recorded skip is not proof of Bash compatibility or a cleared failure.

Alpha.130 extends the existing disposable shortcut installation/restoration
case with later edits to the executable, entry script, build record,
configuration and a managed shortcut pointing back to native Codex, plus a
missing shortcut or build record. Rejected
setup must preserve the bundle, shortcuts and ownership journal and create no
build output. The same case still exercises unchanged upgrades and reactivation
after a completed uninstall; it never accesses real Desktop/Start menu folders.
