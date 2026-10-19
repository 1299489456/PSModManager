# 2026-10-12 产品范围收缩

按用户当前要求，将产品定位恢复为简单的本地 Mod 管理器，AI 为可选辅助。本次只修改开发文档、任务规划及台账元数据，不修改产品代码或真实资源。

调整重点：安装、启用、停用、卸载优先；取消 G0～G5 的强制前置作用及多级批准流程；Profile、配置编辑、日志诊断、回退历史按需使用；不再把所有旧任务和故障专项当作首版必经路线。已有实现可复用，不能用新文档宣称代码已经简化。

`before/` 保存本次修改前的原文。旧任务状态、实际 PASS/FAIL/BLOCKED、历史人工意见及 evidence 内容不回填、不删除。台账中的历史阶段门保留原状态，但不再参与新任务放行；旧工时不作为当前排期。

当前简化目标和未实现差距见 [产品范围](../../01-product-scope.md)、[普通安装流程](../../04-deployment-protocol.md) 与 [路线](../../09-roadmap.md)。后续核心接线归 PS-03-07，本次不自动实施。

已同步 01–12 系列、37 张卡、指令册与台账。PS-03-07 改题为“Mod 安装、启停和卸载接线”，只依赖现有 executor 和包库 UI；PS-04-03 改为“AI 建议与普通 Mod 操作接入”，依赖同一普通管理入口。便携交付不再等待 AI。原题和旧依赖保存在台账及 `before/`，主分析和 HTML 增加当前范围提示，原文件在 `before/source-reports/`。

文档检查只扫描现行文档、卡片和调整说明，不再递归扫描历史证据、故障夹具或原文备份。结果见 [结构检查](documentation-check.json)；这是文档检查，不是产品测试。历史任务状态、验证结果、证据引用和阻断原因逐项比对无变化。本次未修改产品代码、测试或真实游戏，现有批准/恢复流程的实际简化仍由后续开发完成。

验证命令：`pwsh -NoProfile -File docs/development/tools/Validate-DevelopmentDocs.ps1 -ReportPath docs/development/changes/20261012-scope-simplification/documentation-check.json`，exit 0，3109 项结构/引用检查通过，覆盖 55 个现行 Markdown、37 张任务卡、34 项需求和 52 个 CASE 索引。三个内存负例（循环依赖、断链、无证据标为 VERIFIED）均按预期拒绝，exit 1；它们不是产品失败。`history-check.json` 对比 37 个任务及 6 个历史门共 197 个结果字段，无差异。产品测试未运行。
