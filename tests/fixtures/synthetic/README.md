# 自制合成样本

这些 C# 源码、JSON、日志、配置均为 PSModManager 自制，不含真实游戏、MelonLoader 或第三方模组代码/二进制。`MelonAttributes.cs` 仅表达属性签名；`ItemForge.cs` 仅为同名角色的合成元数据载体。

运行 `scripts/build-fixtures.ps1 -OutputRoot <项目内全新绝对目录>`，使用现成 SDK 的 Roslyn 与 reference pack 直接编译，不 restore、不安装、不执行 DLL。输出含三个 Mods DLL（两个有 MelonInfo，一个普通 helper）、独立属性替身 DLL，以及嵌入 items/recipes JSON 的 ItemForge DLL；此最小集不冒充历史 45/43 样本。没有可启动游戏 EXE，安装树只有 `SYNTHETIC-INSTALL.json` 标志。

`manifest.json` 记录所有源及载荷 SHA256、编译器和参考程序集 SHA256、角色和数量；`build-report.json` 保留每条实际命令、退出码及完整输出。同一工具链在不同目录产生相同载荷字节。DLL 仅可作为后续静态 worker 的输入，绝不能加载或执行。
