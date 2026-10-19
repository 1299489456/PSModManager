# PS-05-03 当前便携产物检查

本轮使用 PS-05-02 的 Windows x64 ZIP，从归档解压新的程序目录。原始发行目录和 ZIP 保留，产品代码未改；具体命令、文件变化和失败见 [本轮报告](evidence/PS-05-03/20261014T052241Z-packaged-install/report.md)。

| 检查范围 | 当前结果 |
|---|---|
| 当前 Windows、同用户非提升权限 | PASS；kernel 10.0.26200、AMD64；实际产品 token 为 Medium、Elevation=0、Administrators deny-only，无 enabled admin privileges |
| 实际便携 UI 与 helper | PASS；自造 DLL 经原生选择器预览入库，普通按钮完成安装/停用/启用/卸载/再次安装，关闭重开后保留安装状态 |
| 文件与数据分开 | PASS；默认隔离 LocalAppData，工作目录及 404 项程序文件未变；游戏核心和合成存档标记保持；删除测试程序副本后游戏、配置、数据库和包库均保留 |
| WebView2 对当前进程不可用 | PASS；仅此进程指定空运行时目录，真实 loader 返回不可用，冻结 GUI 显示明确提示，确认后 exit 2，无自动安装 |
| schema 6 合成旧数据、设置与一份备份复制恢复 | PASS；真实冻结入口升级到 7，旧 payload 和未知配置字段保留；备份摘要正确，复制到另一隔离数据根后 GUI 能启动 |
| 干净 VM / 另一机器 | NOT_RUN；未提供目标环境，未发现 WindowsSandbox/Get-VM/VBoxManage/vmrun 入口；未创建 VM 或安装运行时 |
| 独立标准账户、实际缺 .NET Framework 的机器 | NOT_RUN；同用户非提升检查不能替代这些环境 |
| 用户实际旧数据、游戏、存档、外部 AI、人工 DPI | NOT_RUN |

实际 WebView2 为 155.0.4283.45，.NET Framework Release 为 533509。程序运行环境隐藏 Python/dotnet/venv 命令，但宿主仍有开发工具，不能称为干净机器。

WebView2 负例采用 [Microsoft 文档说明的运行时选择覆盖](https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl)：覆盖只传给测试进程，未卸载或修改系统运行时。它证明当前进程不可用时的真实错误入口，未代替物理缺运行时的新机器检查。

有选定目标机器时，可用相同 ZIP 补充记录其实际 Windows/.NET/WebView2 状态与普通动作；缺运行时先记录提示，不自动安装。此说明不产生新审批或放行步骤。PS-05-04 尚未开始。
