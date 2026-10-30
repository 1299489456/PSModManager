# 正式版分析：证据索引与验证边界

日期：2026-10-28（America/Los_Angeles）。完整分析、32 项功能清单、实施与验收方案统一见 [PSModManager 正式版升级分析与功能改进方案](ProbablyStolen_FormalRelease_Upgrade_Plan_2026-10-28.md)。本文件保留逐条源码追踪，作为详细参考。

这份索引供后续实现者回到当前样本查证。反编译行号属于本次 ILSpy 输出，不是游戏开发者原始源码行号；游戏或工坊更新后须重新建立对应关系。

## 1. 输入与本地材料

| 缩写 | 绝对路径 |
|---|---|
| R | `E:\WorkSpace\ForTools\PSModManager` |
| G | `E:\Entertainment\Steam\steamapps\common\Probably Stolen` |
| W | `E:\Entertainment\Steam\steamapps\workshop\content\4348910` |
| P | `C:\Users\Administrator\AppData\LocalLow\Questing Goose Studio\Probably Stolen` |
| A | `E:\WorkSpace\ForTools\PSModManager\.preview\formal-release-audit` |
| D | `E:\WorkSpace\ForTools\PSModManager\.preview\formal-release-audit\game-decompiled` |
| M | `E:\WorkSpace\ForTools\PSModManager\.preview\formal-release-audit\workshop-decompiled` |
| B | `E:\WorkSpace\GitHub\RimCrow`（只读交互参考） |

下文 `D/ModLoader.cs:128-154` 等定位表示按上述目录展开。项目内报告不复制个人配置、存档正文或 Steam 账户标识。

### 可直接复用的分析交付

- `A/observations.json`：Build、运行后端、真实包摘要、原生期望顺序、批准概要和最近启动审计；无完整批准指纹及所有者账号值。
- `A/player-log-mod-excerpt.txt`：保留原始 `Player.log` 行号的模组相关片段，所有者和指纹显示值已省略。
- `A/inventory.json`：逐包完整清单、文件/程序集/引用/资源元数据、Steam 安装字段、静态补丁目标交集。
- `A/manager-gap-audit.md`：当前管理器真实入口与 14 项主要缺口、复用边界、源码行号。
- `A/native-security-audit.md`：扫描规则、批准状态机、指纹算法、签名列表、解析风险。
- `A/workshop-package-audit.md`：七包目录和业务 DLL 分析、发布协议与上传器格式分歧。
- `D`：游戏程序集导出的 931 个 C# 文件。
- `M`：11 个工坊业务 DLL 的反编译输出；支持库做静态元数据盘点，未执行。
- `A/firstpass/ES3Settings.decompiled.cs`：ES3 默认文件/路径设置的定向反编译。
- `A/capture_observations.py`：只读采集脚本，仅向 A 写摘要；不 import 游戏或管理器入口、不执行 DLL。

这些中间材料在已忽略的 `.preview` 中，适合本机复核，不是产品源代码或可对外再分发的 SDK。长期留存的是本报告和升级方案；后续需要移交完整材料时应显式选择范围。

## 2. 核心结论的原始定位

### E01 — 正式版身份与状态快照

- `E:\Entertainment\Steam\steamapps\appmanifest_4348910.acf`：appid `4348910`、buildid `26284217`、安装目录。
- `G/MonoBleedingEdge` 与 `G/Probably Stolen_Data/Managed/Assembly-CSharp.dll` 存在；根目录 `GameAssembly.dll` 不存在。
- `P/Player.log:1-4`：Mono 路径与 Unity `2021.3.45f2`。
- `D/PlayerStore.cs:214-221`：非 limited build，展示版本 `VERSION 100-Rev3`。
- `P/Player.log:52`：加载器审计的 `Application.version=0.46D`；原生批准记录同样记录该版本。
- `P/SaveFile.es3`：已只读解析 `modOrder` 与 `modTrust`。7 个期望启用 ID、7 条批准记录。
- `P/Player.log:21-30,31-59,63-75`：本次启动 1 个启用、7 个工坊包被发现，只有 `item_details` 实际运行；随后 6 个启用/批准并保存顺序。

意义：截图中的 7 个激活项与同一进程实际运行 1 个并不矛盾；修改等待重启。

### E02 — 启停、生命周期与程序集执行

- `D/ModLoader.cs:128-154`：Awake 初始化全链。
- `D/ModLoader.cs:196-224`：`modOrder` 加载到顺序与启用集合；停用同时 `ModTrust.Revoke`；保存后要求重启。
- `D/ModLoader.cs:235-263`：`ReorderMods` 过滤/补齐启用项、排序内存列表、持久化。
- `D/ModLoader.cs:720-775`：先本地后工坊发现；本地只扫直接子目录。
- `D/ModLoader.cs:872-1012`：单包发现、启用判断、信任门、逐 DLL 处理、错误记录。
- `D/ModLoader.cs:1014-1080`：`Assembly.LoadFrom`、入口类型选择、构造、`PatchAll/Init/OnEnable`、失败清理。
- `D/ModLoader.cs:1278-1295`：退出时 `OnDisable` 与对应 Harmony 撤销。
- `D/IMod.cs:1-8`：接口的三个生命周期方法。

决定性结论：设置停用只是改变后续加载，DLL 不会因此在当前进程热卸载。

### E03 — 包格式、来源与多入口

- `D/ModManifest.cs:3-22`：原生字段集合。
- `D/ModLoader.cs:64-82,608-718`：ID 与清单校验；取 XML 根但不限制其名称；Assemblies 和 Capabilities 元素必需。
- `D/ModLoader.cs:783-869`：声明路径、根/Assemblies 直接 DLL 扫描、存在性和 IMod 字节初筛。
- `D/ModLoader.cs:879-896`：重复 ID、首个目录与本地遮蔽工坊规则。
- `D/ModLoader.cs:978-997,1024-1075`：多个入口逐条添加 LoadedMod，包尾元数据补充给最后匹配记录。
- `D/ModUIManager.cs:376-398,489-495`：逐 LoadedMod 建行；激活取原生启用集合，不取 IsEnabled 运行结果。
- `W/*/manifest.xml` 与 `A/inventory.json`：7 包唯一 ID、21 个声明 DLL、8 个入口。

多入口实际 UI/状态表现尚未重启复现。管理器应保留包级与入口级两个层次，避免把部分入口成功说成整包全部成功。

### E04 — 自动排序与版本提示的限制

- `D/ModLoader.cs:147-151,738-758,1014-1080`：保存顺序在 DLL 初始化后才应用。
- `D/ModLoader.cs:1083-1098`：缺失 Prerequisites 只打印警告。
- `D/ModUIManager.cs:1099-1138`：只遍历激活项的依赖 DFS，循环访问直接返回，无明确环路错误结果。
- `D/ModHook.cs:109-169`：按 `GetInvocationList` 顺序调用并逐个捕获异常；不按 modOrder 重新排序。
- `D/ModUIManager.cs:825-852,875-894`：清单版本规范化为数字/点前缀，UI提示，不是严格 SemVer 阻断。

对当前代码可确认保存顺序不能保证已完成的初始化顺序；特定组合是否因此失败仍需最小运行复现，不由静态顺序直接推断。

### E05 — Steam 发现、下载和启动交接

- `D/ModWorkshop.cs:15-17,62-93`：正式 AppID、订阅枚举、安装状态和安装路径、未安装下载请求。
- `D/ModWorkshop.cs:104-126`：安装/下载回调、待重启。
- `D/ModWorkshop.cs:129-160`：取消订阅的异步结果，不是直接删除目录。
- `D/ModWorkshop.cs:163-330`：详情、封面和发布者信息缓存与更新。
- `D/ModUIManager.cs:1161-1227`：取消订阅与本地遮蔽副本处理。
- `D/SteamManager.cs`：`RestartAppIfNecessary(4348910)`、初始化与 `RunCallbacks`；直接 EXE 可发生 Steam 重启交接。
- `E:\Entertainment\Steam\steamapps\workshop\appworkshop_4348910.acf`：LastBuildID `26284217`，NeedsUpdate/NeedsDownload 均 0；7 个已安装条目的记录大小与磁盘逐包相符。

ACF 只代表本机记录时刻；本轮没有在线证明当前账号全部订阅仍有效或工坊没有更新。

### E06 — 安全状态机与可恢复的原生语义

完整分支见 `A/native-security-audit.md`。核心定位：

| 机制 | 当前代码 |
|---|---|
| 第一次启用检查 | `D/ModLoader.cs:383-454` |
| UI 提示与批准动作分层 | `D/ModUIManager.cs:728-764`；`D/ModLoader.cs:457-469` |
| 后续 Match/Changed/Updated/Paused/Blocked | `D/ModLoader.cs:270-357` |
| 批准数据与比较 | `D/ModTrust.cs:15-57,66-102,128-152`；`D/ModTrustRecord.cs` |
| 指纹覆盖 manifest + 显式 DLL | `D/ModFingerprint.cs:15-59` |
| 静态扫描入口与 IL 遍历 | `D/ModScanner.cs:88-109,224-258,307-380,970-1000` |
| 能力调用规则 | `D/ModScanner.cs:31-78,454-629,668-744` |
| 硬拒绝/资源规则 | `D/ModScanner.cs:90-258,260-287,407-438,539-547,879-905,968-997` |
| 签名验证与缓存 | `D/ModTrustList.cs:81-87,150-245,339-406,438-454` |
| 封禁优先级/暂停 | `D/ModTrustList.cs:277-337` |
| 后台重判没有热卸载 | `D/ModLoader.cs:551-571` |
| 程序集 resolver | `D/ModLoader.cs:486-535` |

六种能力：files、network、process、dynamic-code、native、reflection-external。静态扫描规则不构成系统隔离，也不保证能分析动态生成的所有行为。

必须在产品中保留的细节：

- Match 指纹直接加载，不因游戏版本变化自动重新扫描。
- 更新比较 detected 能力子集，非仅比较声明；能力减少后刷新历史 detected。
- 同来源更新无需一律重新批准；来源或 Workshop ID 变化不能沿用旧批准。
- 停用同时撤销信任；恢复包、恢复方案与恢复批准必须分开。
- 名单失败/过期不等于安全；Verified 也不表示发布者账号已认证。
- 新封禁不回滚当前进程已做的操作；当前会话需要退出。
- 全包路径/重解析点防护、资源完整性、跨包同程序集名检查需要管理器补足。

Companion 只读配置导出在当前 API 上具备静态可行性，但本轮未实现，未获得游戏方稳定性承诺；不把直接调用批准函数视作用户同意。

### E07 — 原生配置

- `D/ModConfig.cs:9-33`：`entries[{key,value}]` 与 `persistentDataPath/ModConfigs/<id>.json`。
- `D/ModConfig.cs:41-72,84-155`：按 ID 缓存；运行时 Define 注册类型、范围、选项、标签和重启标识。
- `D/ModConfig.cs:158-221`：读写 JSON；保存保留 rawValues 中未定义的键。
- `D/ConfigEntry.cs`：bool/int/float/string/enum 序列化、InvariantCulture、范围限制与变更回调。
- `D/ModConfigWindow.cs:215-237`：应用值后保存，触发 `OnModConfigApplied`，显示重启提示。
- `P/ModConfigs/item_details.json`：本次唯一观察到的原生值文件。

值文件中没有完整 Schema；不能推断离线修改已被正在运行的 Mod 热应用。

### E08 — 资源、内容注册与 Hook

- `D/ModLoader.cs:1100-1276`：资源路径、格式、读取顺序、异步音频和 `OnModAssetsLoaded`。
- `D/ModHook.cs:10-96`：44 个公开事件；`:109-208` 为逐处理器调用和错误归属。
- `D/ModItemDirectory.cs`：物品目录初始化触发 Mod Hook。
- `D/Directory.cs:79-112`：Add/Set 的实际移除/注册行为；日志文字不能替代调用检查。
- `D/DirectoryMaster.cs:82-107,246-272`：物品查找与缺失时 dummy 回退。
- `D/ModLootFile.cs:10-82,84-157`：JSON表、组与补丁转交。
- `D/LootRegistry.cs:400-468,471-541`：操作入 journal、顺序重放、重复注册与同条目修改。
- `D/PaperBookRegistry.cs:60-85,112-144,172-241`：模组命名空间与书籍加载。

### E09 — 跨包补丁与组合测试选择

`A/inventory.json` 的 `static_harmony_summary` 和交集清单记录来源。统计的是反编译属性行，不是实际安装补丁数：

| 包 | HarmonyPatch 属性行 | IMod 入口 |
|---|---:|---:|
| Item Details | 0，另见 10 个手工 Patch 源码调用点 | 1 |
| 电子扩展 + 翻译 | 381，其中 30 行无显式类型目标 | 2 |
| 联机 | 24，其中 7 行无显式类型目标 | 1 |
| 特性扩展 | 62，其中 1 行无显式类型目标 | 1 |
| 小鼠养殖 | 2 | 1 |
| 上传器 | 5 | 1 |
| 店铺助手 | 0，另见 7 个手工 Patch 源码调用点 | 1 |

显式目标交集为 29 个类型/方法名称组合。没有完整解析重载、动态 TargetMethod(s) 和手工循环；未证明这些组合产生行为故障。它们用于选择电子×特性、电子×联机、信息显示×玩法修改的优先验收路径。

### E10 — 存档与数据位置

- `A/firstpass/ES3Settings.decompiled.cs:25,121-139`：默认 `SaveFile.es3` 与 persistentDataPath 路径；实际本机格式另由 observations 的只读解析确认。
- `D/ModLoader.cs:196-224` 与 `D/ModTrust.cs:39-57`：模组键写入 ES3 默认文件。
- `D/ModHelper.cs:907-977`、`D/PlayerStore.cs:48,703-709`：以 `modId:key` 访问当前游玩实例的 modData。
- `D/ModHelper.cs:1025-1129`：Mod 文件/ModData 目录与边界处理。
- `D/SaveFiles.cs:12-43,71-125,173-221`：槽位、历史文件、索引、轮换/清理/复制。
- `D/PlayerStore.cs:960-1066`：读档与 Mod Hook 阶段。
- `D/SaveManager.cs:153-240`：按物品 ID 恢复、容器节点不匹配的警告路径。
- `D/DirectoryMaster.cs:223-272`：未找到工厂时创建 dummy；不是恢复完整 Mod 类型/行为。

具体包的额外位置：

- `M/3828902092/Pawnshop.ModRuntime/Pawnshop.ModRuntime.Mono/OfficialPaths.cs:8-12`：游戏根 `UserData/CyberElectronics`。
- `M/3828902092/CyberElectronics/CyberElectronics/PhoneTerminalUI.cs:866`：以 DLL 位置找壁纸；与包内旧说明不同。
- `M/3828909239/ProbablyMultiplayer-2.0.2/ProbablyMultiplayer/InstallationProfile.cs:14,44-77`：联机身份文件与旧位置迁移。
- `M/3829106910/ShopAssist/ShopAssist/Settings.cs:59-80`：ModData 设置与备份；账本/保留/待办还有随存档内容。
- `M/3829024993/RatKeeper/RatKeeper/Keeper.cs:152-173`：按笼数据存储；包内说明明确既成游戏操作不会由停用撤销。

本轮不解析游玩存档正文，不给出某个真实槽位可以安全卸载的结论。

### E11 — 工坊包与社区上传器

逐包报告 `A/workshop-package-audit.md` 列清单原字段、字节数、文件和 DLL 证据。

- 7 包、67 文件、36,353,733 字节；21 DLL 均含 CLR 元数据，声明文件均存在。
- 6 个 `<Manifest>`，上传器 1 个 `<Mod>`；全部 Prerequisites 空/缺省。
- 4 个本地 preview，3 包无本地 preview，不代表没有远端工坊封面。
- 引用简名都可在同包/游戏中找到；Unsafe 和 netstandard 的版本差异只记录为风险线索，未定为运行失败。

上传器根 `U = M/3829050213/ProbablyStolenWorkshopUploader`：

- `U/ProbablyStolenWorkshopUploader.Core/ProjectStore.cs:56-80`：强制根 `Mod`，与 6 个包不兼容。
- `U/ProbablyStolenWorkshopUploader.Core/ProjectStore.cs:14-52,119-160,164-266`：XML/路径防护、项目绑定、原子保存/备份。
- `U/ProbablyStolenWorkshopUploader.Core/PackageBuilder.cs:13-95,118-190`：过滤、暂存、完整声明文件与封面。
- 上传器 `SteamBackend.cs` / `UploadSession.cs` 的完整名称、行号见子报告第 6 节：依赖游戏 Steam 会话、核对拥有者与目标 AppID、创建后持久化 ID、提交更新、处理未知结果。

该工具是社区 IMod 插件，包内许可证为 MIT，未发现证明它为游戏方官方上传器的材料。未调用上传、登录、订阅或联机 API。

### E12 — 当前 PSModManager 的实际消费缺口

以下相对 R，完整追踪见 `A/manager-gap-audit.md`：

| 判断 | 代码位置 |
|---|---|
| 当前版本 v0.1.1 | `backend/app/identity.py:2-6`；`pyproject.toml:1-4` |
| 真实桌面装配 | `main.py:26-38`；`backend/app/composition.py:134-195`；`frontend/src/app/App.vue:5-18,177-190` |
| 当前命令路由 | `backend/app/api.py:336-445`；扩展在线路由 `backend/online/api.py` |
| 轻量选择已支持正式版 | `backend/games/probably_stolen/discovery.py:14`；`backend/app/installations.py:123-124,169-171`；`backend/games/probably_stolen/layout.py:79-93` |
| 列表与文件存在启用 | `backend/deployments/mod_workspace.py:101-106,139-175,178-254` |
| 停用移除文件 | `backend/deployments/ordinary.py:173-191,334-338` |
| 旧包映射 | `backend/catalog/imports.py:191-201` |
| Loader 提示与启动判断 | `backend/loaders/melonloader/service.py:146,205-235` |
| 日志缺口 | `backend/diagnostics/service.py:80-97`；`backend/ai/log_diagnostics.py:60-78` |
| 固定 AI 假设 | `backend/ai/game_prompt.py:7-12` |
| 配置专项 | `backend/deployments/config.py:45-53` |
| 静态解析底座可复用 | `tools/PSModManager.Metadata/Program.cs:44-73` |
| 旧工坊非当前接入 | `frontend/src/app/stores/app/steamWorkshopActions.js:22,34,88,245,282`；`backend/managers/mgr_steam.py:43-59,1407-1657` |
| 历史已知缺口 | `docs/development/known-limitations.md`；当前本地交付记录 `docs/development/current-local-delivery.json` |

特别保留：纯净无旧 Loader 的启动会跳过恢复，不能说“所有正式版启动都会被阻止”；危险是旧残留触发不适配恢复与默认 UI 误导。历史交付证据没有因本次只读分析自动刷新。

用户随后决定从新版产品中移除 MelonLoader 和 Nexus。上述代码是退役范围的依据，不表示要继续维护其旧运行能力；替代设计见主方案 §7.5。

### E13 — Steam 账号授权与匿名下载不是同一种能力

后续方案修订核对了以下 Valve 官方说明：

- [ISteamUGC — GetNumSubscribedItems](https://partner.steamgames.com/doc/api/ISteamUGC#GetNumSubscribedItems)：查询当前用户的订阅；游戏服务器调用返回 0。
- [ISteamUGC — SubscribeItem](https://partner.steamgames.com/doc/api/ISteamUGC#SubscribeItem)：订阅后触发尽快下载/安装，以异步结果确认操作。
- [ISteamUGC — DownloadItem](https://partner.steamgames.com/doc/api/ISteamUGC#DownloadItem)：存在未订阅下载路径，文档以匿名游戏服务器为例；下载开始不等于内容可读取，需等完成回调。
- [ISteamUGC — GetQueryUGCChildren](https://partner.steamgames.com/doc/api/ISteamUGC#GetQueryUGCChildren)：children 可以是合集成员或依赖，不能统一解释为加载顺序。
- [Steam OAuth](https://partner.steamgames.com/doc/webapi_overview/oauth)：需联系 Valve 获得 Client ID；Workshop 委托权限按 AppID 配置。
- [Steam 用户认证与 OpenID](https://partner.steamgames.com/doc/features/auth)：OpenID 用于验证身份、取得 SteamID；身份结果本身不是工坊代操作授权。

据此确定两条产品路径：默认账号订阅交给已登录 Steam 的官方界面；取得并验证相应 API 授权后才提供应用内直接代操作。匿名通道若可用，作为本地下载/更新跟踪，不标为账号订阅。

**本 AppID 的缺口：** 本轮未执行 `4348910` 的匿名下载，也未申请 OAuth Client ID，未订阅/取消订阅任何条目。匿名支持留给工坊接入实现者以独立缓存中的真实公开条目确认，未确认前不承诺可用；不影响官方页面交接和原生本地管理先行。

### E14 — RimCrow 交互可借鉴，游戏专属状态不能搬用

本次只读观察 B 的现有源码，而不是运行或修改 RimCrow：

| 来源 | 观察与设计用途 |
|---|---|
| `B/frontend/src/app/App.vue:37-75,106-120` | 详情、停用库、启用顺序及辅助栏；自动排序与保存入口 |
| `B/frontend/src/features/mod/list/useModListDrag.js:69-148` | 多选/分组拖动、完整原始列表重组、跨列表历史事务；不能直接用可见列表覆盖完整顺序 |
| `B/frontend/src/features/mod/list/ModListQuickActions.vue` | 依赖补齐/必要项与建议项的交互入口 |
| `R/frontend/src/features/catalog/ModWorkspace.vue:19-40,155-207,265-281` | 当前实际消费者仍为卡片/列表展示排序与待保存篮子，没有原生加载序列合同 |
| `R/frontend/src/app/App.vue:6-11,171-189` | 实际挂载 ModWorkspace、NexusPanel、MelonLoaderPanel，不能只改未接入的旧 WorkshopBrowser |
| `R/backend/app/composition.py:152-153,172-173,329-381` | Loader/Nexus 服务装配及 Nexus 收件生命周期 |
| `R/backend/online/api.py:124-147` | Nexus 账号、下载、协议处理和收件命令的退役范围 |

采用“可用模组 + 启用与顺序 + 详情”的三栏及统一有序草稿；原生依赖字段、工坊依赖身份和 ES3 状态重新接入。RimCrow 的环世界包规则、配置文件与账号/工坊写入逻辑不作为本游戏的事实来源。

本轮只确定布局和交互合同，尚未制作或验收新 UI；跨栏拖动、筛选不丢项、稳定排序和反向依赖停用都列入 P1，而非推迟为装饰性增强。

## 3. 复核方法

分析工具隔离在 A 下，没有给产品添加依赖或修改构建配置。主要命令：

```powershell
$r = 'E:\WorkSpace\ForTools\PSModManager'
$a = "$r\.preview\formal-release-audit"
$managed = 'E:\Entertainment\Steam\steamapps\common\Probably Stolen\Probably Stolen_Data\Managed'

& "$a\tools\ilspycmd.exe" --disable-updatecheck -p `
  -o "$a\game-decompiled" -r $managed "$managed\Assembly-CSharp.dll"

& "$r\.venv\Scripts\python.exe" "$a\capture_observations.py"
```

游戏程序集导出退出码 0；11 个业务 DLL 的逐项反编译成功。只读采集输出 7 包、67 文件、21 声明 DLL，missing=0、原生扫描目录下 unlisted=0，desired_enabled=7、last_launch_running=1。这些结果证明结构与现有记录，不证明六个尚待重启模组的运行兼容。

后续文档检查仅验证路径、引用、表格关键数据和 diff；没有执行应用测试套件，因为没有实施产品代码改动。现有自动化测试结果不继承为正式版适配通过。

用户后续反馈的复核只读检查了当前入口、RimCrow 的列表/拖动实现与 Valve 授权说明，随后更新这两份文档。未改产品任务台账的完成状态，未移除任何用户已安装的 Loader 或 Mod。

## 4. 外部资料及如何使用

- [游戏 Modding Guide](https://github.com/dragonpan2/probably-stolen-modding/blob/main/ModdingGuide.md)：页面说明经开发者审阅，但也要求以游戏行为为准。本轮只用于交叉核对原生 API、资源和工坊文档，主结论由本地反编译支持。
- [示例清单](https://github.com/dragonpan2/probably-stolen-modding/blob/main/Mods/LearnerMod/manifest.xml)：网页快照仍有缺少新必需元素的旧样例，不能直接作为本机正式版的完整模板。
- [Valve ISteamUGC](https://partner.steamgames.com/doc/api/ISteamUGC)：用于区分订阅、安装、下载和异步结果。
- [Valve Workshop 实施指南](https://partner.steamgames.com/doc/features/workshop/implementation)：用于选择玩家下载/作者发布接入边界，而非据此声称外部工具已获得此游戏发布权限。
- [Valve OAuth](https://partner.steamgames.com/doc/webapi_overview/oauth) 与 [用户认证](https://partner.steamgames.com/doc/features/auth)：用于区分已登录用户身份、代理操作权限与匿名下载；具体判断见 E13。
- [ILSpy](https://github.com/icsharpcode/ILSpy) 与 [ILSpyCmd 9.1.0.7988](https://www.nuget.org/packages/ilspycmd/9.1.0.7988)：本次只读反编译工具来源。

未采用第三方攻略中仍描述旧 IL2CPP/无工坊状态的内容来覆盖本机证据。

## 5. 仍需在实现阶段取得的证据

1. 真正重启后七包/八入口的加载结果、电子包双入口记录、资源和错误归属。
2. Init/Harmony/Hook 顺序的最小实际复现，尤其依赖必须先初始化的情况。
3. 电子×特性、电子×联机、信息展示×玩法修改的真实行为结果。
4. ES3 字段写入与真实游戏读回；模拟失败仅补充，不能替代原生批准/重启流程。
5. 运行中检测历史失败修复、并发写入/Steam 更新、备份恢复的实际路径。
6. 存档副本上的内容包停用/再启用/回退，保持原槽位未受影响。
7. 联机真实双会话、服务故障与保存边界；本轮没有连接作者服务器。
8. 作者发布的 Steam 会话/权限、条目绑定和未知结果恢复；只有用户明确要求发布时才产生外部操作。
9. 双列表多选/拖动/键盘/撤销、筛选保持完整顺序、稳定排序与规则冲突；补充真实有依赖的 Mod，不能以当前七包的空 Prerequisites 代替。
10. 本游戏匿名下载的实际可用性、独立缓存与更新；账号订阅交接和应用内委托授权分别验收。
11. 移除旧集成后，当前 UI、命令、任务、打包不再消费 MelonLoader/Nexus；迁移只处理本程序拥有的协议关联/凭据与可核验原生包。

这些是相关功能的验收条件，不阻止已经有证据的 P0 只读盘点与错误引导隔离先交付。
