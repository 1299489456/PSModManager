# PSModManager 开发文档入口

版本：2.0；调整日期：2026-10-12。当前定位是简单的 Windows 本地 Mod 管理器，AI 为可选辅助。

首要流程：选择游戏 → 添加 DLL、目录或 ZIP → 点击安装；已安装列表直接启用、停用、卸载。方案、日志、配置编辑和备份按需使用。用户不需要先管理计划、批准记录、恢复问题或阶段门。

PS-03-07 已把默认模组页接到普通安装、启用、停用、卸载入口，点击一次执行，无需创建方案或批准计划。包库、planner/executor 和守卫内部复用；方案保留为可选入口，历史页已按用户要求移除。安装选择自动查找 Steam 游戏库并允许手动指定，不再要求完整游戏指纹。进度、实际验证与未测范围见 [台账](task-register.json) 和对应 evidence；本卡完成后停止。

PS-04-01 新增可选 AI 页，支持 OpenAI 兼容流式会话、独立 Windows 凭据和当前问题/手选文本发送。PS-04-02 增加可选的当前游戏/包库只读查询及单个日志片段选择，PS-04-03 接通建议卡片的安装/启用/停用按钮，复用普通 Mod 服务。PS-04-04 统一请求限额与取消清理，失败后可点击重试；断网仍可普通安装、启停、卸载。查询默认关闭，只有主动测试或发送才联网；采用已生成建议是本地动作。真实 provider 未测，不影响基础 Mod 管理。

PS-04-05 已验证所选日志、查询、建议和普通安装闭环，并修复原生类型缓存累积导致的退出超时，实际桌面完整路线自然退出通过。结果及真实 provider 未测范围见 [AI 能力报告](ai-capability-report.md)。合成 API、本地桌面和外部模型结果分别记录，不产生新的阶段放行步骤。

## 从这里开始

1. 读 [产品范围](01-product-scope.md) 和 [安装流程](04-deployment-protocol.md)。
2. 根据用户指定任务，读 [路线](09-roadmap.md)、[执行约定](10-execution-guide.md) 及对应任务卡。
3. 需要复制开发指令时用 [指令册](11-copy-paste-prompts.md)。一次只处理明确指定的任务。
4. 按改动运行针对性检查；结果写清通过、失败和未测范围即可，见 [验证方式](08-verification-guide.md)。

## 配套文档

| 文件 | 用途 |
|---|---|
| [01-product-scope.md](01-product-scope.md) | 核心功能、可选功能与不做的功能 |
| [02-architecture.md](02-architecture.md) | 现有模块复用和最小接线 |
| [03-data-contracts.md](03-data-contracts.md) | 用户动作、状态及现有接口边界 |
| [04-deployment-protocol.md](04-deployment-protocol.md) | 普通安装、启停、卸载和错误处理 |
| [05-diagnostics-ai.md](05-diagnostics-ai.md) | 可选 AI、日志和发送内容 |
| [06-ui-workflows.md](06-ui-workflows.md) | 简单操作流程与界面目标 |
| [07-validation-matrix.md](07-validation-matrix.md) | 按功能选择验证；旧 CASE 编号索引 |
| [08-verification-guide.md](08-verification-guide.md) | 适度验证与真实结果记录 |
| [09-roadmap.md](09-roadmap.md) | 核心接线与可选后续工作 |
| [10-execution-guide.md](10-execution-guide.md) | 开发执行规则 |
| [11-copy-paste-prompts.md](11-copy-paste-prompts.md) | 单任务短指令；历史门索引 |
| [12-decisions-and-risks.md](12-decisions-and-risks.md) | 本次取舍及保留的基本边界 |
| [task-register.json](task-register.json) | 任务依赖和原结果记录 |
| [evidence/README.md](evidence/README.md) | 测试结果保存约定 |

## 当前要求与历史资料

用户当前要求优先。G0～G5 已撤销强制前置作用；台账保留其原状态和历史结果，不把 BLOCKED 改成 PASS，也不再据它们阻断独立开发。人工体验检查按用户安排进行，不设审批会或统一放行手续。

旧 37 张卡保留编号供查找，不代表每项都必须完成才能用首版；旧总工时和卡内旧估算不再作为当前排期。范围变化说明和原文备份见 [本次调整](changes/20261012-scope-simplification/README.md)。

旧资料的事实与当时建议保留：[主分析](../ProbablyStolen_ModManager_Report.md)、[来源记录](../ProbablyStolen_Audit_Evidence.md)、[补充报告](../analysis_report.html)、[附录](../evidence_appendix.md)。主分析和 HTML 仅增加历史范围提示，ZIP、历史测试和人工意见不改写。历史实现中的 HMAC、journal、lease 和多代记录可以复用，不继续扩成用户必须操作的功能。

开发默认使用合成安装与隔离数据根，保留工作区修改和 RimCrow 源目录只读约束。既有适用授权持续有效；本次文档调整不触发真实游戏操作、依赖安装、联网 AI、提交或发布。
