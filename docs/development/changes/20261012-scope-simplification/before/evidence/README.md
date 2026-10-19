# 开发证据

当前没有产品运行证据。本目录保存规范和文档检查结果，不能据此更新产品任务为VERIFIED。

产品run路径：`<task>/<UTC-run>/summary.json`，配套输出见 [验证指南](../08-verification-guide.md)。文档检查放 `planning-validation.json`，其 `verification_kind` 必须为 `documentation_structure`。

summary至少记录schema_version、task_id、profile、run_id、started_at/ended_at、command、exit_code、verification_status、版本/文件指纹、discovered/executed/passed/failed/skipped计数、必要case分项、blockers、limitations、artifacts。任务不存在Git时记录文件指纹。

不要把真实DLL、存档、凭据、完整私人日志写入可共享证据。使用脱敏引用；无法共享的原始材料保留本机受控位置并说明访问边界。没有文件的“证据路径”不能支撑通过。

失败run不覆盖或删除。新run记录supersedes，保留失败原因与修复差异。台账只引用摘要，不改历史报告内容。
