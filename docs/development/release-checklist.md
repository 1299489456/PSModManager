# 当前本地交付清单

## 当前版本：轻量 Mod 操作

当前程序和 ZIP 以 [current-local-delivery.json](current-local-delivery.json) 为准。普通保存、启停及卸载默认采用轻量批次文件操作，不增加模式开关。保留统一 Mod 工作区与日志 AI、已有包库和旧部署记录；本轮重新构建 EXE，前端代码未修改。

54 项后端定向验证、7 项错误提示检查、23 项包检查通过；实际 WebView 跑通 1863 文件，冻结 EXE 跑通 130 文件启停。旧特殊进程守卫仍保留 8 项失败。实际耗时、产物、截图和未测项目见 [本轮报告](evidence/20261015-lightweight-mod-ops/report.md)；[上一轮界面报告](evidence/20261015-workspace-redesign/report.md)保留历史数据，不能用旧 358 / 262 项结果替代本轮验证。保存前正常关闭游戏。真实游戏写入/玩法、外部 AI、人工/DPI 未验收，未公开发布。

## 历史交付存档：2026-10-14 说明与许可补充

以下数值仅描述当时的包，不是当前程序。该次只准备本地材料，版本为 **0.1.0 / Windows x64**；当时原可执行程序、前端、规则和 helper 未重编译。

## 文件

| 材料 | 当前位置/摘要 |
|---|---|
| 中文说明和许可补充后的 ZIP | `.preview/p504-20261014T090037Z/PSModManager-v0.1.0-windows-x64-local.zip` |
| ZIP 大小 / SHA256 | 69,037,721 bytes / `6b12cf27089b3248b9e4ab81adf0e063d44b6bd5c2eb9e1dffc4abdcb007403e` |
| ZIP 外部摘要文件 | `.preview/p504-20261014T090037Z/SHA256SUMS.txt` |
| 便携目录 | `.preview/p504-20261014T090037Z/portable/PSModManager`；439 文件、142,876,478 bytes |
| 包内文件清单与摘要 | `build-manifest.json`、`SHA256SUMS.txt`；SHA 文件覆盖除自身外的所有包文件 |
| 项目与第三方许可 | 根 `LICENSE`、`THIRD_PARTY_NOTICES.md`、`licenses/`；66 组件映射、76 材料文件来源/摘要，另有映射 JSON |
| 原 PS-05-02 包 | `.preview/p502-07/PSModManager-v0.1.0-windows-x64.zip`，SHA256 `c318cd4cf4e1bcf93a8cd0d06aca1ef24dc46c18e05c268e96d3227646ca9ab6`，保留原样 |

新 ZIP 增加 35 个说明/许可文件，只替换原 `README-PORTABLE.txt`、`build-manifest.json` 和 `SHA256SUMS.txt`。其余原 401 项逐字节一致。GUI EXE SHA256 为 `2a8aebb41bfdd7a0083aee63dd1029f313388227367bcb461a1f70d03066dcfb`，console host 为 `5476a1b8437fadb4b7bb41d9b6163d4ab50acaea1be127ccb620d7d44d7e6aa7`。

```powershell
Get-FileHash -Algorithm SHA256 .preview/p504-20261014T090037Z/PSModManager-v0.1.0-windows-x64-local.zip
```

完整解压 ZIP 后运行 `PSModManager.exe`，保留旁边的 `PSModManager.WorkerHost.exe` 和 `_internal`。Windows 10/11 x64、.NET Framework 4.8+、WebView2 Runtime 是目标环境前置；Python、Node、.NET SDK 不作为运行依赖。程序不自动安装运行时或加载器。EXE 未作发布签名。

## 说明和实际验证

[快速开始](../user/quick-start.md)、[数据与重装](../user/data-and-reinstall.md)、[常见问题](../user/troubleshooting.md)、[版本说明](../user/release-notes.md)对应当前按钮；[第三方说明](../../THIRD_PARTY_NOTICES.md)和[已知限制](known-limitations.md)保留实际来源及边界。

PS-05-03 的 13 项当前输入及原始 4 PASS 结果已重新核对，包含真实文件安装/启停/卸载、重开、进程缺 WebView2 提示、合成 schema 6 升级及单份备份复制启动。该 CASE-048 结果被本卡引用，未把它扩大为真实用户或跨机器数据迁移通过。最终交付包的本轮布局、摘要、文档/许可检查及实际 EXE 启动另见 [本轮报告](evidence/PS-05-04/20261014T090037Z-local-delivery/report.md)。

干净机器、独立标准账户、物理缺运行时新机器、真实用户旧数据迁移、真实游戏玩法、外部 provider 和新人工/DPI 结果仍未测。M6 在线源/下载、游戏内观测、存档隔离和自动更新延后，不作为首版本地管理前置。

原 RimCrow 目录、真实游戏和正式存档未写入；没有安装依赖、提交/推送或上传/发布。本任务结束后停止，不自动执行 M6。
