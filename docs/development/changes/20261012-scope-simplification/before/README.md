# PSModManager 开发文档入口

版本：1.0；建立日期：2026-09-28。本文档包直接依据本仓库两组分析资料及本机只读核对编写，没有使用 task-doc-generator、task-runner、task-completion-handoff 系列技能。

当前交付是开发计划、契约、任务卡和文档校验工具。应用代码尚未导入，所有产品任务未实施、所有产品验证为 NOT_RUN。文档检查通过不表示产品可运行。

## 从这里开始

1. 阅读 [范围与需求](01-product-scope.md)、[架构与迁移边界](02-architecture.md)。
2. 阅读 [开发路线](09-roadmap.md)，按其顺序执行；第一项是 [PS-00-01](tasks/PS-00-01.md)。
3. 每项任务结合 [执行约定](10-execution-guide.md)、任务卡、[实时台账](task-register.json) 开展；一次只执行用户指定项。
4. 需要直接复制指令时使用 [逐任务指令册](11-copy-paste-prompts.md)。每段均已填写任务 ID，不会自动开始下一项。
5. 当前可执行的文档检查：在项目根目录运行 `pwsh -NoProfile -File docs/development/tools/Validate-DevelopmentDocs.ps1`。

## 配套文档

| 文件 | 用途 |
|---|---|
| [01-product-scope.md](01-product-scope.md) | 已知基线、首版范围、需求 ID、资源边界 |
| [02-architecture.md](02-architecture.md) | 技术栈、模块职责、RimCrow 取舍、目标文件结构 |
| [03-data-contracts.md](03-data-contracts.md) | 身份、状态、数据存储、RPC/事件和版本契约 |
| [04-deployment-protocol.md](04-deployment-protocol.md) | 文件所有权、计划、备份、提交、恢复、回滚 |
| [05-diagnostics-ai.md](05-diagnostics-ai.md) | 日志、证据、确定性规则、AI 工具和执行边界 |
| [06-ui-workflows.md](06-ui-workflows.md) | 页面、用户动作、状态和故障反馈 |
| [07-validation-matrix.md](07-validation-matrix.md) | 验收场景与任务追溯 |
| [08-verification-guide.md](08-verification-guide.md) | 合成/桌面/实机验证方式、证据与判定 |
| [09-roadmap.md](09-roadmap.md) | 37 项任务、依赖、工时区间、阶段门 |
| [10-execution-guide.md](10-execution-guide.md) | 开发执行、状态更新、中断恢复与交接 |
| [11-copy-paste-prompts.md](11-copy-paste-prompts.md) | 逐项开发和阶段检查指令 |
| [12-decisions-and-risks.md](12-decisions-and-risks.md) | 方案决策、待验证问题和解除条件 |
| [task-register.json](task-register.json) | 任务、依赖、需求覆盖和唯一进度记录 |
| [evidence/README.md](evidence/README.md) | 证据目录约定 |

## 资料优先级与维护

用户当前明确指示 > 当前开发契约与任务卡 > 原分析报告。真实实现若与契约不符，先记录差异，不能静默改验收标准。原报告保留作为来源，不覆盖原文。

两套报告统一为 `ActionPlan`，不再混用 OperationPlan；部署事务 `committed` 与运行验证 `passed` 分开。详情以本目录 03、04 文档为准。任务动态状态只记在台账，不在多处复制“已完成”清单。

开发目录及未来源码路径均相对 `E:\WorkSpace\ForTools\PSModManager`。任务卡中的“目标文件”是拟新增或迁移文件，当前不存在不等于文档丢失。RimCrow 源目录只读；真实游戏路径只作为受控验证目标。

## 原始资料

- [主分析报告](../ProbablyStolen_ModManager_Report.md)：领域结论、45→43 规则及建议路线。
- [源码与日志证据](../ProbablyStolen_Audit_Evidence.md)：E01–E15。
- [另一套离线报告](../analysis_report.html)、[证据附录](../evidence_appendix.md)：补充文件生命周期、配置解析和浏览器桥接风险。
- `../ProbablyStolen_ModManager_Analysis.zip` 内 `ProbablyStolen_Mod_Inventory.json`。
- `../ProbablyStolen_RimCrow_Analysis.zip` 内 `mod_inventory.json`、嵌入资源摘要、解析探测、历史检查输出。

这里只定义开发与验收，不授权提交、推送、发布、安装依赖、启动/修改真实游戏或使用外部 AI。后续执行时，已获得的具体授权持续有效，无需重复询问。
