# PSModManager

Windows 本地 Probably Stolen Mod 管理器：用卡片开关选择 Mod，保存后启动游戏。支持方案与整合包分享、新旧版本替换、Nexus 在线获取；“日志”中的智能分析可读取环境并提出需要确认的启停建议。未配置在线服务或断网不影响本地管理。

## 便携使用

1. 完整解压 Windows x64 便携包，运行 `PSModManager.exe`。保留同目录的 `PSModManager.WorkerHost.exe` 和 `_internal`。
2. 启动时自动查找 Steam 游戏库；找到一个安装时直接使用，找到多个时选择要管理的安装。也可点击“选择游戏安装”手动选择含 `Probably Stolen.exe` 的游戏根目录，或选择 Steam 目录重新查找。目录选择不要求生成游戏指纹。
3. 游戏中已有的 Mod 自动以卡片出现在“模组”页。点击“导入”可添加 DLL、目录或 ZIP；点击卡片在原处展开它的子目录和资源文件。
4. 用卡片上的开关启用或停用，更改会收进右侧的“待保存”篮子。点击“保存”应用文件变更，或“保存并启动”一次完成。未保存的选择可撤销，不需要创建方案或批准计划。
5. 在“方案”页把已保存列表存为新方案、覆盖或另存，导出清单或包含 Mod 的整合包；拖入新版、旧版或整合包后，核对预览再确认安装。
6. 在“Nexus”页配置自己的 Nexus Key，浏览最近/热门内容并下载导入。游戏或管理器出问题时，打开“日志”，选择资料并“分析日志”；智能分析的启停建议由你点击后执行。

运行需要 Windows 10/11 x64、.NET Framework 4.8+、WebView2 Runtime；Python 和元数据 helper 的 .NET 运行时随包携带，不需要安装 Python、Node 或 .NET SDK。系统运行库由用户安装；MelonLoader 可通过界面主动下载安装。文件安装成功不等于游戏玩法兼容。

数据默认位于 `%LOCALAPPDATA%\PSModManager`，可在“应用信息”中查看。删除程序目录不会自动删除数据或游戏 Mod。要移除某个 Mod，请使用其“卸载”按钮。

[快速开始](docs/user/quick-start.md) · [方案、更新与在线 Mod](docs/user/sharing-updates-online.md) · [数据、备份与重装](docs/user/data-and-reinstall.md) · [常见问题](docs/user/troubleshooting.md) · [版本说明](docs/user/release-notes.md)

## 开发与本地交付记录

[开发约定](docs/development/README.md)、[本地交付清单](docs/development/release-checklist.md)和[已知限制](docs/development/known-limitations.md)分别记录实现、实际产物和未测范围。当前包在合成安装及同用户非提升 Windows 权限下验证；干净机器和真实用户旧数据结果不由这些检查代替。

本项目以 [MIT 许可](LICENSE) 开源；按 MIT 要求，LICENSE 同时保留上游 RimCrow 的原版权声明。第三方材料见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本地交付不代表已经上传或发布。
