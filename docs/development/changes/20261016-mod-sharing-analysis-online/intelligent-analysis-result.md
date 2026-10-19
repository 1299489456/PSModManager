# 智能分析：修复结果与验证

## 已实现

- 日志页标题为“日志”，分析区为“智能分析”。保留现有页面布局、日志阅读和发送前资料预览。
- 普通中文长回复不再被 16 KB / 1024 帧的旧限制提前停止。接收内容、SSE 帧、原始传输字节、单事件与模型 `finish_reason=length` 分别判断。
- 长回复使用小增量任务事件，完整文本保存在当前会话，通过现有 `ai.chat.get` 读取；不会扩大共享 JobManager 的 32 KB 单条数据限制。查询轮次切换时保留流的偏移。
- 模型长度耗尽、网络中断、达到本次预算和用户取消均保留已经收到的文本。“继续分析”在同一会话和所选日志范围内请求续写；“重试原请求”仍单独可用，不自动重发。
- 续写只裁剪下一次发送的旧回复上下文；界面和会话导出仍可查看完整已保留回复。
- 新系统提示针对 Probably Stolen / 深空当铺、Demo / Playtest / 正式版、Unity IL2CPP、MelonLoader 0.7.3、Mods / Plugins / UserLibs、程序集依赖和日志证据适配。明确区分文件启用、日志证实加载和玩法验证。
- 保留已登记版本的 `suggest_mod_action`。新增 `get_mod_workspace` 和 `suggest_workspace_action`，可查询并建议启停外部发现、尚未纳管的 Mod。
- 工作区建议绑定具体 Mod ID 和完整 `snapshot_id`；模型只生成预览。用户点击后重新核对快照，复用 `createModWorkspaceRuntime` / `mod.workspace.save`，其他 Mod 保持原状，外部 Mod 停用前保存恢复副本。执行结果显示在卡片中，并通知主 Mod 页面刷新。
- 包库名称搜索跨页查找，不再只筛选第一页面后误报没有目标。

## 根因复现

改动前用项目已有 localhost SSE 服务实测：

| 输入 | 原结果 |
| --- | --- |
| 5500 个中文字符，单片输出 | `AI_OUTPUT_LIMIT`，已显示字符数为 0 |
| 1100 个 ASCII 单字符 SSE 帧 | `AI_OUTPUT_LIMIT`，在 1024 个字符停止 |

原实现同时存在 `RequestBudget.output_bytes=16000`、`frames=1024`、`_display` 固定 16000 字节及模型 `length` 被映射为同一种显示错误。仅放大其中一项还会撞共享任务 JSON 的 32 KB 限制，因此采用完整会话加有界增量，而没有修改任务系统。

当前默认：180 秒、256 KiB 接收内容、65536 帧、16 MiB 原始 SSE 数据；工具查询仍有每次最多 4 个服务请求 / 3 批 / 12 次调用的独立预算。单条工具参数和输出、日志选择及脱敏边界保持约束。

## 验证证据

- `.venv/Scripts/python.exe -m pytest tests/integration/test_ai_intelligent_analysis.py tests/integration/test_ai_tools.py tests/integration/test_ai_plan_handoff.py tests/integration/test_ai_resilience.py tests/integration/test_log_diagnostics_workspace.py tests/unit/test_ai_privacy.py tests/unit/test_ai_tool_policy.py -q`：176 项通过。日志：`docs/development/evidence/20261016-mod-sharing-analysis-online/ai-regressions/pytest-existing-and-new.log`。
- 补充“查询轮次中的最终回复延续前一段文本”回归后，`test_ai_intelligent_analysis.py` 最终定向运行 12 项通过。日志：`docs/development/evidence/20261016-mod-sharing-analysis-online/ai-regressions/pytest-final-focused.log`。
- `node --test frontend/src/features/ai/localAiRuntime.test.js frontend/src/features/ai/aiActionRuntime.test.js frontend/src/features/ai/logDiagnosticsRuntime.test.js`：87 项通过。日志：`docs/development/evidence/20261016-mod-sharing-analysis-online/ai-regressions/node-runtime.log`。
- 主代理统一生产构建后，通过真实主入口 / WebView 运行 `tests/desktop/harness/intelligent_analysis.py`。最终证据：`docs/development/evidence/20261016-mod-sharing-analysis-online/ai-webview-02/report.json` 和 `process.json`。
  - 完整显示 37455 字节、15058 字符的合成长回复。
  - 模型长度中断后手动继续；用户取消后保留回复且可继续。
  - 外部 DLL 查询 → 建议预览时无写入 → 用户点击停用 → 结果卡片和主工作区刷新 → 用户点击重新启用 → 原始文件字节恢复。
  - 10 次 localhost 请求，无认证凭据；应用退出码 0，工作线程和订阅为 0，进程边界关闭后残留进程为 0。
  - 已查看生成截图，建议卡、目标版本/影响、继续按钮和结果可见。

## 接线和修改范围

- 共享接线仅为主代理在 composition 中传入 `ReadOnlyTools(..., workspace=self.mod_workspace)`；复用原有 `ai.suggestion.resolve`，没有新增桥接命令或模型可调用的写工具。
- 后端：`backend/ai/budgets.py`、`gateway.py`、`desktop_runtime.py`、`game_prompt.py`、`tools.py`、`tool_policy.py`、`plan_proposals.py`、`log_diagnostics.py`。
- 前端：`frontend/src/features/ai/localAiRuntime.js`、`aiActionRuntime.js`、`LogDiagnosticsWorkspace.vue`、`LocalAiPanel.vue`、`PlanCard.vue` 及对应定向测试。
- 验证：新增 `tests/integration/test_ai_intelligent_analysis.py`、`tests/desktop/harness/intelligent_analysis.py`；调整已有 AI 工具/预算测试及 localhost provider 的 length / partial-stall 模式。

## 仍需区分的实际使用条件

- 验证使用合成游戏目录和 localhost 模拟模型；真实模型的理解质量、真实游戏加载和玩法修复尚未测。未使用真实 API key、未启动或写入用户真实游戏。
- 当前工具不能改写第三方 DLL 的 `MelonPriorityAttribute` / 依赖图，不提供实际加载排序写入；UI 列表整理不是游戏排序。
- 建议卡在 10 分钟或工作区快照变化后失效；此时需要刷新/重新分析，不能沿用旧预览覆盖已变化的 Mod 状态。
- `ai-webview-01` 完成了主要交互，但最终记录阶段读取已关闭 context 的 jobs 属性失败；`ai-webview-02` 修复测试 harness 的引用并完整通过，交付使用后者。
