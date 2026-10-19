# PSModManager

> [!WARNING]
> 本项目全程使用 Codex 和 Claude Code 开发；由于作者工作繁忙，维护和问题回复可能不够及时。
> 使用前请备份重要的 Mod 配置和存档，遇到问题可提交脱敏后的复现步骤与日志。

**面向 Probably Stolen 的 Windows 本地 Mod 管理器。** 选择游戏，导入 DLL、目录或 ZIP，用开关选择 Mod，保存后运行游戏。方案分享、版本替换、Nexus 在线获取和日志智能分析按需使用；不配置在线服务也能完成本地管理。

[快速开始](docs/user/quick-start.md) · [方案、更新与在线 Mod](docs/user/sharing-updates-online.md) · [数据与重装](docs/user/data-and-reinstall.md) · [常见问题](docs/user/troubleshooting.md) · [从源码开发与打包](docs/development/README.md)

## 便携使用

仓库提供源码；需要自行构建便携包时，按[开发指南](docs/development/README.md)操作。以下步骤适用于完整的 Windows x64 便携包，不是 GitHub 的源码 ZIP。

### 运行要求

- Windows 10 / 11 x64。
- .NET Framework 4.8 或更高版本、Microsoft Edge WebView2 Runtime。
- 已安装 Probably Stolen；要在游戏中加载 Mod，还需兼容的 MelonLoader 和游戏运行库。

便携包携带 Python 和元数据解析 helper 的 .NET 运行时，**不需要安装 Python、Node.js 或 .NET SDK**。游戏侧的 .NET / VC++ 运行库与管理器自身的要求不同，可在 MelonLoader 环境详情中检查并通过官方链接安装。

### 选择、保存、运行

1. **完整解压**便携 ZIP，运行 `PSModManager.exe`。保留同目录的 `PSModManager.WorkerHost.exe` 与 `_internal`，不要只复制一个 EXE。
2. **选择数据位置。** 首次启动可用默认的 `%LOCALAPPDATA%\PSModManager`，也可选择其他盘上的独立空目录。以后在“设置”→“数据位置”中迁移，完成后重启；游戏文件和存档不会搬动。
3. **选择游戏。** 管理器自动查找 Steam 安装，也可点击“选择游戏安装”，手动选择含 `Probably Stolen.exe` 的游戏根目录。顶栏的 MelonLoader 状态可查看环境、主动下载安装官方 0.7.3 x64 加载器或选择本地官方 ZIP。
4. **导入 Mod。** 拖入 DLL、目录或 ZIP，核对预览后点击“添加到包库”。游戏目录中已有的 Mod 也会出现在“模组”页，无需重复导入。
5. **启用或停用，再保存。** 卡片开关会加入右侧“待保存”列表；点击“保存”才应用变更，也可撤销未保存的选择。操作前先正常关闭游戏。
6. **运行游戏。** 点击运行按钮；有待保存变更时可用“保存并启动”，保存失败不会继续启动。

普通导入支持 DLL、目录和 ZIP，不支持 RAR / 7z、加密包或安装脚本。单次导入最多 4000 个文件，单文件及 ZIP 不超过 64 MiB，展开后共 256 MiB；安装目标限于 `Mods`、`Plugins`、`UserLibs`。更多操作与快捷键见[快速开始](docs/user/quick-start.md)。

## 方案、分享与版本更新

- **保存一套列表：** 先保存主列表，再在“方案”中选择“新方案”→“当前模组列表”；已有方案可覆盖或另存。
- **分享给朋友：** 导出 JSON 清单，或包含指定版本 Mod 文件的整合包 ZIP。对方导入、核对缺失项与冲突后再应用。分享前确认 Mod 作者允许再分发。
- **删除方案：** 在“更多（⋯）”中选“删除方案…”。只删除方案，不卸载游戏里的 Mod，也不删除包库。
- **升级或降级：** 拖入新版或旧版，核对版本与替换范围后确认。旧版本保留在包库；程序不会后台自动更新 Mod。

详细步骤见[方案、更新与在线 Mod](docs/user/sharing-updates-online.md)。

## Nexus 与日志智能分析都是可选项

**Nexus：** 使用自己的个人 API Key 浏览内容。Premium 账户可直接下载；普通账户使用官网 **Manual download**，下载后回到管理器选择 ZIP / DLL 导入。仅当官网提供 **Mod manager download** 按钮时，才能使用可选的 `nxm://` 接收功能。个人 Key 接入及公开分发条件见[Nexus 使用说明](docs/user/sharing-updates-online.md#从-nexus-获取-mod)。

**日志：** 不配置 AI 也能阅读和筛选日志。需要智能分析时，在“日志”页配置 OpenAI 兼容服务或本地服务，核对发送内容后分析、追问；启停建议须由你点击才会执行。外部模型可能产生费用，日志中也可能含个人信息，发送前请检查。

**不要上传密钥。** AI / Nexus 密钥可仅本次使用，或保存在 Windows 凭据中；不要提交到 Git、Issue、截图或分享包。也不要上传个人数据库、游戏文件、存档或未脱敏日志。

## 数据、限制与问题反馈

程序和数据目录分开。删除便携程序目录不会自动删除数据或游戏里的 Mod；卸载 Mod 请使用卡片中的“卸载”。迁移、备份和重装见[数据与重装](docs/user/data-and-reinstall.md)。

当前版本仍有[已知限制](docs/development/known-limitations.md)，包括部分运行中检测场景、历史 WebView 退出残留及尚未完成的干净机器、真实游戏和 DPI 验证。文件安装成功不等于游戏内兼容；本地测试结果不能代替这些检查。

提交问题时请附上管理器版本、Windows 版本、具体操作、预期与实际结果，以及脱敏日志。不要提供真实账号密钥或整份游戏目录。

## 开发与许可

[开发指南](docs/development/README.md)包含从 clone 开始的依赖准备、隔离数据启动、测试、打包和代码导航。用户侧变化见[版本说明](docs/user/release-notes.md)。

本项目以 [MIT 许可](LICENSE) 开源，保留上游 RimCrow 的原版权声明。第三方组件及随包材料见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；各 Mod 和游戏资源仍适用各自的许可。
