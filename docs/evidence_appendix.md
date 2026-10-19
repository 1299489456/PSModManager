# Probably Stolen / RimCrow 本地证据附录
日期：2026-09-24。只对应本次上传快照。编号 G 为游戏文件，R 为 RimCrow 源码，T 为实际执行的检查。
原始 .7z 未被文件检索服务解析；本附录来自已挂载文件的本地解包和只读分析。行号从文件第一行算起。`<GAME_ROOT>` 替代原日志中的私人安装路径。DLL 仅解析 PE/CLI 元数据和嵌入 JSON，不加载或执行。
## 输入与核验摘要
```json
{
  "analysis_date": "2026-09-24",
  "scope": "上传压缩包静态审阅；没有运行 Windows 游戏、DLL 或桌面 GUI；没有完整构建验证。",
  "archives": {
    "RimCrow.7z": {
      "size": 28875138,
      "sha256": "2234c490cb07c62043796a309ee64e891601dae5f094822fb1a8e8584f00c67d"
    },
    "Probably Stolen Demo.7z": {
      "size": 321244241,
      "sha256": "dc4c2c446b25fe2503e75327289e0feab44c6bfa9f53f620dde12d993e947e75"
    }
  },
  "rimcrow_commit": "a7e875112a175a914bb4706a82589d96d62adc70",
  "rimcrow_substantive_diff_ignoring_eol": "",
  "root_dll_count": 45,
  "meloninfo_count": 44,
  "dll_hashes_present_in_log": 43,
  "loader_log_relative_path": "MelonLoader/Logs/26-9-24_14-15-5.log",
  "loader_log_lines": 1049,
  "loader_log_sha256": "1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a",
  "tests": {
    "python_compileall": "passed syntax compilation only",
    "frontend_modIdentity_node": "passed this one assertion script",
    "ai_assistant_pytest": "collection failed: missing vdf; no business assertions executed",
    "windows_gui_game_full_build": "not executed"
  },
  "standard_config_parse_probe": [
    {
      "file": "Loader.cfg",
      "standard_tomllib": "pass",
      "top_level_keys": 5
    },
    {
      "file": "MelonPreferences.cfg",
      "standard_tomllib": "reject",
      "error": "Invalid initial character for a key part (at line 28, column 2)"
    },
    {
      "file": "PSMarketRequests.cfg",
      "standard_tomllib": "pass",
      "top_level_keys": 26
    },
    {
      "file": "PSProcurement.cfg",
      "standard_tomllib": "pass",
      "top_level_keys": 34
    },
    {
      "file": "PSWallpaper.cfg",
      "standard_tomllib": "reject",
      "error": "Invalid value (at line 4, column 15)"
    }
  ]
}
```
## G1｜游戏身份与实际加载环境
来源：`Probably Stolen_Data/app.info`；SHA-256：`21dca9b2e18762bacd225e45eaad29e8031ddfadbaa29954c00095a71516f8c3`。
### L1–L2

```text
L1: Questing Goose Studio
L2: Probably Stolen
```
## G2｜加载环境、运行库恢复、内容包与注册数量
来源：`MelonLoader/Logs/26-9-24_14-15-5.log`；SHA-256：`1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a`。
### L1–L4

```text
L1: [14:15:09.533] [MelonLoader.Bootstrap] Using HostFXR Path: C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll
L2: [14:15:09.535] [MelonLoader.Bootstrap] Using .NET runtime: 'C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll'
L3: [14:15:09.544] [ERROR] [MelonLoader.Bootstrap] Failed to initialize a .NET domain
L4: [14:15:09.544] [MelonLoader.Bootstrap] Downloading the .NET Runtime Installer from: https://aka.ms/dotnet/6.0/dotnet-runtime-win-x64.exe
```
### L105–L138

```text
L105: [14:15:11.789] [MelonLoader.Bootstrap] 100%
L106: [14:15:11.789] [MelonLoader.Bootstrap] Running the .NET Runtime Installer...
L107: [14:15:26.226] [MelonLoader.Bootstrap] Using HostFXR Path: C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll
L108: [14:15:26.226] [MelonLoader.Bootstrap] Using .NET runtime: 'C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll'
L109: 
L110: [14:15:27.052] ------------------------------
L111: [14:15:27.053] MelonLoader v0.7.3 Open-Beta
L112: [14:15:27.054] OS: Windows 11
L113: [14:15:27.055] Hash Code: BDD43DC0F3893C208C95389B863AB61C967AE14204DA0DD8051647E754C8709B
L114: [14:15:27.055] ------------------------------
L115: [14:15:27.055] Game Type: Il2cpp
L116: [14:15:27.055] Game Arch: x64
L117: [14:15:27.055] ------------------------------
L118: [14:15:27.055] Command-Line: 
L119: [14:15:27.055] ------------------------------
L120: [14:15:27.055] Core::BasePath = <GAME_ROOT>
L121: [14:15:27.056] Game::BasePath = <GAME_ROOT>
L122: [14:15:27.056] Game::DataPath = <GAME_ROOT>\Probably Stolen_Data
L123: [14:15:27.056] Game::ApplicationPath = <GAME_ROOT>\Probably Stolen.exe
L124: [14:15:27.056] Runtime Type: net6
L125: [14:15:27.609] ------------------------------
L126: [14:15:27.609] Game Name: Probably Stolen
L127: [14:15:27.610] Game Developer: Questing Goose Studio
L128: [14:15:27.611] Unity Version: 2021.3.45f2
L129: [14:15:27.612] Game Version: 0.46D
L130: [14:15:27.612] ------------------------------
L131: 
L132: [14:15:28.470] Preferences Loaded!
L133: 
L134: [14:15:28.492] Loading UserLibs...
L135: [14:15:28.494] 0 UserLibs loaded.
L136: 
L137: [14:15:28.494] Loading Plugins...
L138: [14:15:28.498] 0 Plugins loaded.
```
### L552–L555

```text
L552: [14:16:09.786] [物品工坊] 已读取物品包：第七席与绯色来客 by 私人创作（12 个配置）
L553: [14:16:09.798] [物品工坊] 已读取物品包：gofishing by ALR（45 个配置）
L554: [14:16:09.799] [物品工坊] 已读取物品包：E1z的更多食物 by E1z（22 个配置）
L555: [14:16:09.900] [物品工坊] 已读取物品包：食物奢侈品拓展包 by 三木（21 个配置）
```
### L705–L710

```text
L705: [14:16:10.375] by ModDesign
L706: [14:16:10.375] Assembly: WineAppraisalMaster.dll
L707: [14:16:10.375] ------------------------------
L708: [14:16:10.375] ------------------------------
L709: [14:16:10.376] 43 Mods loaded.
L710: 
```
### L739–L744

```text
L739: [14:16:28.991] [Brewing_Expansion] Brewing Expansion Mod loaded!
L740: [14:16:29.121] [Brewing Expansion] [BE] 已加载 117 张 PNG（外部 0 + DLL 110）
L741: [14:16:29.600] [CharacterForge Runtime] [人物工坊] 已读取人物包：第七席与绯色来客 by 私人创作（3 人）
L742: [14:16:29.601] [CharacterForge Runtime] [人物工坊] 已读取人物包：钓鱼商人 by ALR（2 人）
L743: [14:16:29.687] [人物工坊] Runtime API 1 已加载 5 个人物。
L744: [14:16:29.702] [CustomerCreditBoost] 已加载。展示柜加成(基础价值)：对应物品基础价值每 100 信用点 +5%，仅对被吸引顾客生效
```
### L782–L787

```text
L782: [14:16:30.456] [ItemForge Runtime] [物品工坊] 注册目录：ContainerItemDirectory
L783: [14:16:30.471] [ItemForge Runtime] [物品工坊] 注册目录：WineDirectory
L784: [14:16:30.484] [ItemForge Runtime] [物品工坊] 注册目录：FoodItemDirectory
L785: [14:16:30.498] [ItemForge Runtime] [物品工坊] 注册目录：MaterialDirectory
L786: [14:16:31.072] [物品工坊] Runtime API 19 已加载 100 个物品；全局补丁仅安装一次。
L787: [14:16:31.650] [More Device Upgrades] [More Update 修正] 已启用定向类型查找；诊断文件：<GAME_ROOT>\UserData\MoreUpdateDiagnostics\MoreUpdate-20260924-141631-073-29160-e56b3ede024f4104ad13f6bc8f7717bd.log
```
## G3｜当前真实诊断案例
来源：`MelonLoader/Logs/26-9-24_14-15-5.log`；SHA-256：`1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a`。
### L954–L958

```text
L954: [14:16:39.356] [XIAOWO-UnlimitedBayExpansion] Unlimited bay expansions and weekly Wednesday Jackson visits enabled.
L955: [14:16:39.426] [XIAOWO-WRDUnlock] Demo network selection and missing purchase actions patched.
L956: [14:16:39.479] [XIAOWO-WeeklyVisitors] Weekly retired chemist, retired gunsmith, and retired junker visits enabled.
L957: [14:16:39.479] [烛印会与失灯区] 烛印会与失灯区 1.0.9：支持1.0存档继续游玩；首次从旧开发版升级请新建存档。F7 打开冒险日志。
L958: [14:16:39.653] [科学养鼠] SciRat patched PlayerStore.EndNight
```
### L975–L980

```text
L975: [14:16:40.036] [第七席与绯色来客] 版本 0.3.3：机器人新来访资金保底 2000，保留启动兼容修正与诊断。
L976: [14:16:40.037] [第七席与绯色来客] 启动诊断文件：<GAME_ROOT>\UserData\CuratedVisitors.StartupTrace-20260924-141639-933-29160-ee61a20e09a6451ea7db5976249b5456.log
L977: [14:16:40.038] [第七席与绯色来客·人物资源] 人物资源已启用：赫礼、送货犬、绯罗；由人物工坊读取配置与贴图。
L978: [14:16:40.490] [钓鱼料理扩展] [钓鱼料理] 前置齐备，料理功能已启用。
L979: [14:16:40.491] [钓鱼料理] v1.0.0 已加载；等待物品工坊和去钓鱼！物品包。
L980: [14:16:41.538] [鼠布林&假酒供应商mod] 鼠布林&假酒供应商mod v2.0.0 已加载。
```
### L1013–L1019

```text
L1013: [14:16:46.277] [WaterScanTooltip] patches: 5
L1014: [14:16:46.279] [XIAOWO-PrecisionBayExpansion] INACTIVE: XIAOWO-TradePerks detected; expansion remains entirely controlled by the main mod.
L1015: [14:16:46.822] [烛印会与失灯区] [烛印会] 来访参数=正式；战利品概率独立保留，无比例兑换。
L1016: [14:16:46.824] [ERROR] [烛印会与失灯区] [第七席] 本次运行已停用本 MOD，未安装游戏补丁。检测到本MOD旧版两个DLL。请退出后移走旧版，再用新存档启动0.5.0；其他作者MOD保持原样。
L1017: [14:16:54.099] [第七席与绯色来客] 前置与内容包检查通过，游戏补丁已启用。启动诊断已完成。
L1018: [14:16:54.269] [XIAOWO-TradePerks] Community panel attached to the main menu UI layer.
L1019: [14:16:54.270] [XIAOWO-TradePerks] Original main-menu modding button enabled for MelonLoader mod management.
```
### L1025–L1031

```text
L1025: [14:16:54.418] [XIAOWO-PrecisionBayExpansion] Main-menu expansion title visible; shared promotion=True
L1026: [14:16:57.179] [XIAOWO-TradePerks] MOD 兼容总线已自动去重 62 个完全相同的 Harmony 补丁，来源：CharacterForgeRuntime, DialogueSpeed, ItemForgeRuntime, NetworkUnlockEffective, PSWarehouse, WaterTower。
L1027: [14:16:59.451] [XIAOWO-TradePerks] MOD 兼容性检测：共享方法=46，潜在冲突=21，确认冲突=0，停用模块=0；brokerAliases=0, itemConflicts=0, unresolvedItems=0, deduplicatedPatches=62, dedupeFailures=0, loadedMods=43。
L1028: [14:16:59.452] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 DirectoryMaster.Item 可能被 MOD JacksonPerks, XIAOWOKitchenBusiness 的前置补丁跳过原方法
L1029: [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.CanTarget 可能被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 的前置补丁跳过原方法
L1030: [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.CanTarget 的返回值也会被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 修改
L1031: [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.GetNegociatedValue 的返回值也会被 MOD JacksonPerks, PSItemPack_CuratedVisitors, XIAOWOKitchenBusiness 修改
```
### L1049–L1049

```text
L1049: [14:19:44.360] [WARNING] [ClickToAdvance] DialogUIManager.Instance 为 null
```
## G4｜同次启动的组件身份、版本文字与自停用
来源：`UserData/CuratedVisitors.StartupTrace-20260924-141646-280-29160-ded15b696bf34d53bd43b2c02d80c9d6.log`；SHA-256：`2c862f0ee85f33f353666d232425e1a9fea3b5f94b25d7543fc77a0c1c6afc42`。
### L1–L2

```text
L1: 00001 2026-09-24T14:16:46.3466123+08:00 SESSION START 0.5.0; fresh save; independent ledger, equipment, bosses and story; no workshop dependency
L2: 00002 2026-09-24T14:16:46.3589579+08:00 ENV .NET 6.0.36; Microsoft Windows 10.0.26200; X64
```
### L73–L86

```text
L73: 00073 2026-09-24T14:16:46.5090386+08:00 ASSEMBLY PSItemPack_Gofishing, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null; MVID=d54b5838-bac2-4dde-b7bd-b2497085dd4f
L74: 00074 2026-09-24T14:16:46.5094854+08:00 SHA256 PSItemPack_Gofishing CCF46FE925F070A1ADC24C85018B1C9049C710FB6CD56DEABBCB9CBA011EE152
L75: 00075 2026-09-24T14:16:46.5095915+08:00 ASSEMBLY JCuratedVisitors, Version=1.0.9.0, Culture=neutral, PublicKeyToken=null; MVID=90b1e0bd-6618-433e-a636-0a6e5c3deac5
L76: 00076 2026-09-24T14:16:46.5493989+08:00 SHA256 JCuratedVisitors 585D9F84649A85DB5AB1214029811251563F392EBEC5BF63F7A7FD3305D8CDD0
L77: 00077 2026-09-24T14:16:46.6241640+08:00 ASSEMBLY SciRat, Version=1.1.0.0, Culture=neutral, PublicKeyToken=null; MVID=00730740-6c71-4261-9c89-b81f22493f4f
L78: 00078 2026-09-24T14:16:46.6254988+08:00 SHA256 SciRat 0DE62C4D0DBD734AE2FAE5AACC0D9B293C11A8FBA1D1B4A33D7E517DDA4DEE1A
L79: 00079 2026-09-24T14:16:46.6256437+08:00 ASSEMBLY PSItemPack_CuratedVisitors, Version=0.3.3.0, Culture=neutral, PublicKeyToken=null; MVID=fe712005-e214-4bd0-aab8-f300cb5c4efb
L80: 00080 2026-09-24T14:16:46.6262657+08:00 SHA256 PSItemPack_CuratedVisitors 75FCB11415A0C939DA0C47BE1B123937299F7C8D50BF33840F08E0D94D04E126
L81: 00081 2026-09-24T14:16:46.6263753+08:00 ASSEMBLY PSCharacterPack_CuratedVisitors, Version=0.3.3.0, Culture=neutral, PublicKeyToken=null; MVID=9f1ea69e-bd1e-403f-8aea-43d9415f0609
L82: 00082 2026-09-24T14:16:46.6267079+08:00 SHA256 PSCharacterPack_CuratedVisitors 6209B8F7C2AA761CF5CD3E86629ABB83F38DFA5FE73D594137A8E987823C18E3
L83: 00083 2026-09-24T14:16:46.6268133+08:00 ASSEMBLY PSCharacterPack_Fishingfriends, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null; MVID=eda4d1ff-8d1f-4f05-bb33-5a2b8d683bc8
L84: 00084 2026-09-24T14:16:46.6270794+08:00 SHA256 PSCharacterPack_Fishingfriends 903902F8A9CED57FADC9EA6A6BEB1B52ACC54083169EB16191AFFDB0D9FEA043
L85: 00085 2026-09-24T14:16:46.6271900+08:00 ASSEMBLY PSFishingCuisineExpansion, Version=1.0.0.0, Culture=neutral, PublicKeyToken=null; MVID=e88459f9-67d0-4ef3-b798-f942bfe01d5f
L86: 00086 2026-09-24T14:16:46.6275150+08:00 SHA256 PSFishingCuisineExpansion 6F4DAF8DC153827DE84CB91E9C197442E009E2EEB51F061D57B468A619078A34
```
### L108–L108

```text
L108: 00108 2026-09-24T14:16:46.8247813+08:00 STARTUP DISABLED
```
## G5｜DLL 静态元数据与嵌入清单
完整 45 项见 `mod_inventory.json`；资源清单摘要见 `embedded_manifest_summary.json`。下面列出影响设计的条目。
```json
{
  "file": "JCuratedVisitors.dll",
  "sha256": "585d9f84649a85db5ab1214029811251563f392ebec5bf63f7a7fd3305d8cdd0",
  "assembly": "JCuratedVisitors",
  "assembly_version": "1.0.9.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "CuratedVisitors.Core",
        "烛印会与失灯区",
        "1.0.9",
        "私人创作",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 394,
  "embedded_manifest_resources": [],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.8.0.2580"
    }
  ]
}
```
```json
{
  "file": "KitchenCrashTrace.dll",
  "sha256": "72f48dc697591d4c0241ca69efb4a9da32050dab2ea4fd3daed2300b7ff2d09f",
  "assembly": "KitchenCrashTrace",
  "assembly_version": "0.3.2.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "KitchenCrashTrace.Core",
        "Kitchen Crash Trace",
        "0.3.2",
        "Local diagnostics",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "MelonLoader.MelonPriorityAttribute",
      "value": -2147483648
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 0,
  "embedded_manifest_resources": [],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
```json
{
  "file": "PSCharacterPack_Fishingfriends.dll",
  "sha256": "903902f8a9ced57fadc9ea6a6beb1b52acc54083169eb16191affdb0d9fea043",
  "assembly": "PSCharacterPack_Fishingfriends",
  "assembly_version": "1.0.0.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "CharacterForge.ContentPack.ContentPackMod",
        "钓鱼商人",
        "1.3.10",
        "ALR",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 2,
  "embedded_manifest_resources": [
    {
      "file": "PSCharacterPack_Fishingfriends.dll",
      "resource": "CharacterForge.characters.json",
      "resource_sha256": "62574dbc5cd3f523e5ff3d54ac94b97aa160563951bc6812f72b5df59b405e95",
      "metadata": {
        "RequiredRuntimeApi": 1,
        "GeneratorVersion": "1.1.1",
        "BundleId": "fishingfriends",
        "BundleName": "钓鱼商人",
        "Author": "ALR",
        "BundleVersion": "1.3.10",
        "item_count": 0,
        "character_count": 2
      }
    }
  ],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
```json
{
  "file": "PSItemPack_Gofishing.dll",
  "sha256": "ccf46fe925f070a1adc24c85018b1c9049c710fb6cd56deabbcb9cba011ee152",
  "assembly": "PSItemPack_Gofishing",
  "assembly_version": "1.0.0.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "ItemForge.ContentPack.ContentPackMod",
        "gofishing",
        "1.3.10",
        "ALR",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 47,
  "embedded_manifest_resources": [
    {
      "file": "PSItemPack_Gofishing.dll",
      "resource": "ItemForge.items.json",
      "resource_sha256": "14905b235f3a7898d818052d11ef5b4cc2f21aeb76ea6d10c9d113dcbe12ebdf",
      "metadata": {
        "RequiredRuntimeApi": 19,
        "GeneratorVersion": "1.2.3",
        "BundleId": "gofishing",
        "BundleName": "gofishing",
        "Author": "ALR",
        "BundleVersion": "1.3.10",
        "item_count": 45,
        "character_count": 0
      }
    }
  ],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
```json
{
  "file": "PSItemPack_SanmuShiwushechipinPack1.dll",
  "sha256": "5b9990f1b78ffb3d913977efc33920eb3778dfdd07cf9668263be822ed982370",
  "assembly": "PSItemPack_SanmuShiwushechipinPack1",
  "assembly_version": "1.0.0.0",
  "attributes": [
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 22,
  "embedded_manifest_resources": [
    {
      "file": "PSItemPack_SanmuShiwushechipinPack1.dll",
      "resource": "ItemForge.items.json",
      "resource_sha256": "231cf28dd525bc1866c5fef9988955f7dc31b869ce51da0d352278c5153216c0",
      "metadata": {
        "RequiredRuntimeApi": 5,
        "GeneratorVersion": "1.1.1",
        "BundleId": "sanmu_shiwushechipin_pack1",
        "BundleName": "食物奢侈品拓展包",
        "Author": "三木",
        "item_count": 21,
        "character_count": 0
      }
    }
  ],
  "hash_mentioned_in_bundled_loader_log": false,
  "loader_assembly_refs": []
}
```
```json
{
  "file": "RatColonyOptimizer.dll",
  "sha256": "d48dd8c870c296321ae1665c020ae0ab70de756e2d31d749cb138cf75346b008",
  "assembly": "RatGeneticRenamer",
  "assembly_version": "1.0.0.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "RatGeneticRenamer.Core",
        "Rat Colony Optimizer",
        "1.0.0",
        "Bloom",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 0,
  "embedded_manifest_resources": [],
  "hash_mentioned_in_bundled_loader_log": false,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
```json
{
  "file": "RatGeneticRenamer.dll",
  "sha256": "c0a8c71050368d47cc3c02304bed7163bc27c89415dfe2b3c9848144a2b43db3",
  "assembly": "RatGeneticRenamer",
  "assembly_version": "2.6.3.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "RatGeneticRenamer.Core",
        "Rat Genetic Renamer",
        "1.0.0",
        "DyrClone",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 0,
  "embedded_manifest_resources": [],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
```json
{
  "file": "XIAOWOTradePerks.dll",
  "sha256": "0b9f190c67d4c14976d4a7f4c8bc03c1c66449ce92fcf9cee1fce70f38280547",
  "assembly": "XIAOWOTradePerks",
  "assembly_version": "1.8.2.0",
  "attributes": [
    {
      "type": "MelonLoader.MelonInfoAttribute",
      "values": [
        "XIAOWOTradePerks.Core",
        "XIAOWO-TradePerks",
        "1.8.2",
        "遗忘的小窝",
        null
      ]
    },
    {
      "type": "MelonLoader.MelonGameAttribute",
      "values": [
        "Questing Goose Studio",
        "Probably Stolen"
      ]
    },
    {
      "type": "MelonLoader.MelonPriorityAttribute",
      "value": -2147483648
    },
    {
      "type": "System.Runtime.Versioning.TargetFrameworkAttribute",
      "values": [
        ".NETCoreApp,Version=v6.0"
      ]
    }
  ],
  "embedded_resource_count": 26,
  "embedded_manifest_resources": [],
  "hash_mentioned_in_bundled_loader_log": true,
  "loader_assembly_refs": [
    {
      "name": "MelonLoader",
      "version": "0.7.3.0"
    }
  ]
}
```
## G6｜共享配置、中文裸键与独立配置格式
来源：`UserData/MelonPreferences.cfg`；SHA-256：`6e7317cbf542c977549596f815192c5d569845c47887f08a8b71a2ff2ba14181`。
### L1–L38

```text
L1: [XIAOWOTradePerks_ModManager]
L2: # Physical key above Tab; combinations such as Ctrl+Shift+M are supported.
L3: ToggleHotkey = "BackQuote"
L4: 
L5: [customercredit]
L6: # 
L7: creditsperboost = 100
L8: # 
L9: boostpercent = 5
L10: 
L11: [DialogueSpeed]
L12: # Enable faster dialogue / 启用对话加速
L13: Enabled = true
L14: # Typewriter speed multiplier (higher = faster) / 打字速度倍率（越大越快）
L15: SpeedMultiplier = 4.0
L16: # Delay between dialogue lines (higher = faster) / 句间等待倍率（越大越快）
L17: QueueDelayMultiplier = 1.0
L18: # Skip typewriter effect / 跳过打字机效果（瞬间显示）
L19: InstantText = false
L20: # Speed up hotkey combo, e.g. Equals or Alt+Equals / 加速组合键
L21: SpeedUpHotkey = "Equals"
L22: # Speed down hotkey combo, e.g. Minus or Alt+Minus / 减速组合键
L23: SpeedDownHotkey = "Minus"
L24: 
L25: [ExtraPerks_WideConnections]
L26: LastOutsideGrant = ""
L27: 
L28: [物品整理]
L29: # 排序按钮的总开关。
L30: 启用 = true
L31: # 已放置的存储单元（货架/笼子）保持不动，只整理散落物品。
L32: 容器保持原位 = true
L33: # 不在交易/选择物品弹窗中添加排序按钮。
L34: 跳过交易窗口 = true
L35: # 隐藏格子数少于此值的网格（忽略小型储物/垃圾网格）。
L36: 最小格子数 = 10
L37: # 显示始终打开的库存（展示柜/主存储）。有标题的容器始终显示。
L38: 显示后台存储 = true
```
## G7｜同为 cfg 但不能按标准 TOML 直接处理
来源：`UserData/PSWallpaper.cfg`；SHA-256：`bcacbcd5d16f5967a562706efebd2fe358391cb294aa20568b0270c598ed9a89`。
### L1–L14

```text
L1: # PSWallpaper —— 手机「壁纸」应用（改完重开手机页面即生效）
L2: [PSWallpaper]
L3: # 壁纸文件名（相对 UserData\wallpapers\；空 = 内置星空。可写子目录\名.png）
L4: WallpaperFile=
L5: # 适配：cover（裁切铺满，默认）/ fit（完整显示留边）/ stretch（拉伸）
L6: WallpaperFit=cover
L7: # ★ 底图亮度 0~100，5 一档；越大越亮，0 会变成全黑底图（默认 55）
L8: WallpaperBrightness=55
L9: # 扫描子目录的层数（默认 1）
L10: WallpaperScanDepth=1
L11: # 候选列表最多列几张（默认 40）
L12: WallpaperMaxCandidates=40
L13: # 把结果写入 UserData\PSWallpaper.log
L14: LogToFile=true
```
## G8｜非 DLL 文件与生命周期归属

```text
Plugins/：0 个文件
UserLibs/：0 个文件
Mods/BrewingExpansion/：3 个文件
  Mods/BrewingExpansion/brewing_data.txt (89 bytes)
  Mods/BrewingExpansion/brewing_data_9.txt (9 bytes)
  Mods/BrewingExpansion/brewing_data_11.txt (8 bytes)
Mods/MoreStorageColors/：1862 个文件
  Mods/MoreStorageColors/Reference/hand_steel.png (945 bytes)
  Mods/MoreStorageColors/Reference/cig_red.png (636 bytes)
  Mods/MoreStorageColors/Reference/guide5.png (422 bytes)
  Mods/MoreStorageColors/Reference/paper_buttons2.png (184 bytes)
  Mods/MoreStorageColors/Reference/printer_chip_pistol.png (358 bytes)
  Mods/MoreStorageColors/Reference/shutter_button_sheet_1.png (826 bytes)
  Mods/MoreStorageColors/Reference/border_black.png (111 bytes)
  Mods/MoreStorageColors/Reference/100ml_dark_10.png (236 bytes)
  Mods/MoreStorageColors/Reference/rat_m_m_d.png (463 bytes)
  Mods/MoreStorageColors/Reference/animal_cage.png (1147 bytes)
  Mods/MoreStorageColors/Reference/checkbox_checked.png (249 bytes)
  Mods/MoreStorageColors/Reference/100ml_blue_75.png (390 bytes)
Mods/item_catalog.json (134353 bytes)
Mods/item_mod_config.json (112 bytes)
UserData/：14 个文件
  UserData/KitchenCrashTrace.log (3607 bytes)
  UserData/CuratedVisitors.StartupTrace-20260924-141646-280-29160-ded15b696bf34d53bd43b2c02d80c9d6.log (16082 bytes)
  UserData/Loader.cfg (4805 bytes)
  UserData/PSMarketRequests.cfg (2359 bytes)
  UserData/CuratedVisitors.StartupTrace-20260924-141639-933-29160-ee61a20e09a6451ea7db5976249b5456.log (74148 bytes)
  UserData/PSProcurement.cfg (9693 bytes)
  UserData/PSPDA.log (5786 bytes)
  UserData/PSMarketRequests.log (869 bytes)
  UserData/MelonPreferences.cfg (4121 bytes)
  UserData/PSWallpaper.log (1749 bytes)
  UserData/PSWarehouse.log (15512 bytes)
  UserData/PSProcurement.log (4865 bytes)
```

未提供实际 Windows LocalLow 目录，不据此确定存档位置；Mods 内文本不一概视为可删除资源。
## R1｜应用版本与技术栈
来源：`backend/_version.py`；SHA-256：`e91da361f536e68843d58e91c1bac254de9381cf14525e4a0e78edc26c294b00`。
### L1–L4

```text
L1: # backend/_version.py
L2: __version__ = "0.24.7"  # 主版本.次版本.补丁
L3: __db_version__ = "5"
L4: __build__ = "dev"  # dev, alpha, beta, stable, release
```
## R2｜Python 依赖范围
来源：`pyproject.toml`；SHA-256：`62f337ed5fe9ed5350de584c5f2a99e2385d2dcb4ab0649fca2a0d5d39ae71aa`。
### L1–L41

```text
L1: [project]
L2: name = "rimcrow"
L3: version = "0.1.0"
L4: description = "Add your description here"
L5: readme = "README.md"
L6: requires-python = ">=3.11"
L7: dependencies = [
L8:     "beautifulsoup4>=4.14.3",
L9:     "certifi>=2026.1.4",
L10:     "colorlog>=6.10.1",
L11:     "icecream>=2.1.8",
L12:     "json-repair>=0.58.0",
L13:     "keyring>=25.7.0",
L14:     "litellm>=1.81.14",
L15:     "lxml>=6.0.2",
L16:     "nuitka>=2.8.10",
L17:     "openai>=2.15.0",
L18:     "packaging>=25.0",
L19:     "pathspec>=1.0.4",
L20:     "peewee>=3.18.3",
L21:     "pillow>=12.0.0",
L22:     "pip-system-certs>=5.3",
L23:     "psutil>=7.2.2",
L24:     "pydantic>=2.12.5",
L25:     "pyinstaller>=6.18.0",
L26:     "pytest>=9.0.3",
L27:     "python-dateutil>=2.9.0.post0",
L28:     "python-docx>=1.2.0",
L29:     "pywebview>=6.1",
L30:     "pywin32>=311; sys_platform == 'win32'",
L31:     "reportlab>=4.5.1",
L32:     "requests>=2.32.5",
L33:     "send2trash>=2.0.0",
L34:     "setuptools<70.0.0",
L35:     "steam-vdf>=0.1.10",
L36:     "zstandard>=0.25.0",
L37: ]
L38: 
L39: [tool.pytest.ini_options]
L40: testpaths = ["tests"]
L41: pythonpath = ["."]
```
## R3｜Vue / JavaScript 前端依赖
来源：`frontend/package.json`；SHA-256：`4a89ee118efdaeebb7272f38f8585b719e652848bf04ecbabc7fa79d24a077e2`。
### L1–L49

```text
L1: {
L2:   "name": "frontend",
L3:   "private": true,
L4:   "version": "0.0.0",
L5:   "type": "module",
L6:   "scripts": {
L7:     "dev": "vite",
L8:     "build": "vite build",
L9:     "preview": "vite preview",
L10:     "i18n:extract": "cd .. && uv run python scripts/extract_i18n_messages.py",
L11:     "i18n:check": "cd .. && uv run python scripts/extract_i18n_messages.py --check",
L12:     "prebuild": "npm run i18n:check"
L13:   },
L14:   "dependencies": {
L15:     "@codemirror/lang-json": "^6.0.2",
L16:     "@codemirror/lang-xml": "^6.1.0",
L17:     "@replit/codemirror-lang-csharp": "^6.2.0",
L18:     "@tailwindcss/vite": "^4.1.17",
L19:     "@tanstack/vue-virtual": "^3.13.26",
L20:     "@vueuse/core": "^14.1.0",
L21:     "date-fns": "^4.1.0",
L22:     "dompurify": "^3.3.0",
L23:     "driver.js": "^1.4.0",
L24:     "highlight.js": "^11.11.1",
L25:     "lucide-vue-next": "^0.562.0",
L26:     "markdown-it": "^14.1.1",
L27:     "motion-v": "^1.7.4",
L28:     "pinia": "^3.0.4",
L29:     "tw-animate-css": "^1.4.0",
L30:     "v-viewer": "^3.0.23",
L31:     "viewerjs": "^1.11.7",
L32:     "vue": "^3.5.24",
L33:     "vue-codemirror6": "^1.5.2",
L34:     "vue-i18n": "^11.4.2",
L35:     "vue-toastification": "^2.0.0-rc.5",
L36:     "vue-virtual-scroller": "^2.0.0-beta.8",
L37:     "vue3-colorpicker": "^2.3.0",
L38:     "vue3-json-viewer": "^2.4.1"
L39:   },
L40:   "devDependencies": {
L41:     "@tailwindcss/typography": "^0.5.19",
L42:     "@vitejs/plugin-vue": "^6.0.1",
L43:     "autoprefixer": "^10.4.22",
L44:     "postcss": "^8.5.6",
L45:     "sass-embedded": "^1.97.1",
L46:     "tailwindcss": "^4.1.17",
L47:     "vite": "^7.2.4"
L48:   }
L49: }
```
## R4｜RimWorld 特定的模组扫描入口
来源：`backend/scanner/mod_scanner.py`；SHA-256：`28398458eb8d6e5a19baa2e5b24bb46cfca9b090535fb1b850113c0a58bfc1d9`。
### L525–L555

```text
L525:         payload['status'] = normalized_status
L526:         payload.setdefault('type', 'scan')
L527:         payload.setdefault('id', payload.get('task_id', ''))
L528:         payload.setdefault('progress', 100 if normalized_status == 'success' else 0)
L529:         payload.setdefault('message', tr("tasks.scan.finished", "扫描完成") if normalized_status == 'success' else '')
L530:         payload.setdefault('metrics', {})
L531:         if emit_events:
L532:             EventBus.emit('scan-complete', payload)
L533: 
L534:     def _process_single_mod( self, mod_path, is_dlc_dir, existing_snapshots, dlc_parser: DLCParser | None, forced_update=False, size_check_override: bool | None = None, size_check_paths: set[str] | None = None, stats: dict | None = None, scan_details: dict | None = None ):
L535:         """
L536:         处理单个 Mod 的纯函数逻辑。
L537:         返回: Mod数据字典 或 None(无效) 或 {'_skipped': True, 'package_id': ...}
L538:         """
L539:         mod_path = normalize_path_for_storage(mod_path)
L540:         try:
L541:             about_state = ModAnalyzer.resolve_mod_about_state(mod_path, cleanup_dual_files=True)
L542:         except OSError as e:
L543:             logger.warning(f"清理重复 About 文件失败：{mod_path}，错误：{e}")
L544:             about_state = ModAnalyzer.resolve_mod_about_state(mod_path, cleanup_dual_files=False)
L545:         about_file = about_state.resolved_path
L546:         is_disabled = about_state.is_disabled
L547:         if not about_file and not is_dlc_dir: return None # 既不是 DLC 也没有 About.xml，无效
L548:         
L549:         # 检查 mtime
L550:         try:
L551:             stat = os.stat(about_file) if about_file else os.stat(mod_path)
L552:             mtime = int(stat.st_mtime * 1000)
L553:             ctime = int(stat.st_ctime * 1000)
L554:         except OSError:
L555:             mtime = 0; ctime = 0
```
## R5｜About.xml 与载入顺序语义
来源：`backend/scanner/parser_xml.py`；SHA-256：`8358d3866982fd7649dc2138e9a60b3154c78b23daf1b702e974b0753f375d03`。
### L58–L79

```text
L58:             "dependencies_mods": [],      # 依赖 Mod
L59:             "load_after_mods": [],        # 必须在这些 Mod 之后加载
L60:             "load_before_mods": [],       # 必须在这些 Mod 之前加载
L61:             "incompatible_mods": [],      # 与这些 Mod 不兼容
L62:             
L63:             # --- 附加信息 ---
L64:             "icon_path": "",     # 1.5+ 自定义图标路径
L65:             "save_breaking": None,  # 是否破坏存档 (ModSync)，True: 破坏, None：未知, False: 不破坏
L66:         }
L67: 
L68:         about_path = about_path or os.path.join(mod_path, "About", "About.xml")
L69:         manifest_path = os.path.join(mod_path, "About", "Manifest.xml")
L70:         modsync_path = os.path.join(mod_path, "About", "ModSync.xml")
L71: 
L72:         # 1. 优先解析 About.xml (官方标准)
L73:         if os.path.exists(about_path):
L74:             self._parse_about(about_path, data)
L75: 
L76:         # 2. 补充解析 Manifest.xml (旧版本号、依赖补充)
L77:         if os.path.exists(manifest_path):
L78:             self._parse_manifest(manifest_path, data)
L79: 
```
### L302–L325

```text
L302:             # <modDependencies>
L303:             #   <li>
L304:             #     <packageId>brrainz.harmony</packageId>
L305:             #     <displayName>Harmony</displayName>
L306:             #     <steamWorkshopUrl>...</steamWorkshopUrl>
L307:             #     <downloadUrl>...</downloadUrl>
L308:             #   </li>
L309:             # </modDependencies>
L310:             # 1. Load Before (含 force 和 ByVersion)
L311:             data["load_before_mods"] = self._merge_relations(root, [
L312:                 ("loadBefore", "all", False),
L313:                 ("forceLoadBefore", "all", True),
L314:                 ("loadBeforeByVersion", "BY_TAG", False)
L315:             ])
L316: 
L317:             # 2. Load After (含 force 和 ByVersion)
L318:             data["load_after_mods"] = self._merge_relations(root, [
L319:                 ("loadAfter", "all", False),
L320:                 ("forceLoadAfter", "all", True),
L321:                 ("loadAfterByVersion", "BY_TAG", False)
L322:             ])
L323: 
L324:             # 3. Incompatible (含 ByVersion)
L325:             data["incompatible_mods"] = self._merge_relations(root, [
```
## R6｜严格禁用依赖 About.xml.disabled
来源：`backend/scanner/analyzer.py`；SHA-256：`9ed4eebfb191909ef592af72c18b93e93e65c9ee4c55f7da24a449f286688dab`。
### L357–L380

```text
L357:         return info
L358:     
L359:     @staticmethod
L360:     def resolve_mod_about_state(mod_path: str, cleanup_dual_files: bool = False) -> ModAboutState:
L361:         """
L362:         统一解析 Mod 的 About 文件状态。
L363: 
L364:         规则：
L365:         - `About.xml` 存在时视为启用。
L366:         - 仅存在 `About.xml.disabled` 时视为禁用。
L367:         - 两者同时存在时，以 `About.xml` 为准；如允许修复，则自动删除残留的 `.disabled`。
L368:         """
L369:         about_xml = os.path.join(mod_path, 'About', 'About.xml')
L370:         disabled_xml = about_xml + '.disabled'
L371: 
L372:         has_about = os.path.exists(about_xml)
L373:         has_disabled = os.path.exists(disabled_xml)
L374:         cleaned_conflict = False
L375: 
L376:         if has_about and has_disabled and cleanup_dual_files:
L377:             os.remove(disabled_xml)
L378:             has_disabled = False
L379:             cleaned_conflict = True
L380: 
```
## R7｜RimWorld 环境及 savedatafolder 参数
来源：`backend/managers/mgr_profile.py`；SHA-256：`af8f16964d2a8d58c3f9d77cb546a4cfb1e4dd214a06fc44f87492d9e60ca145`。
### L38–L57

```text
L38:     run_commands: list = field(default_factory=list)
L39:     inactive_mods_order: list = field(default_factory=list)
L40:     temp_mods_order: list = field(default_factory=list)
L41:     is_steam: bool = False
L42:     is_steam_managed: bool = False
L43:     runtime_capabilities: dict = field(default_factory=dict)
L44:     
L45:     is_healthy: bool = True
L46:     health_report: dict = field(default_factory=dict) 
L47:     
L48:     def __post_init__(self):
L49:         # 环境上下文创建时固定布局，避免系统类型或路径探测变化后派生路径前后不一致。
L50:         object.__setattr__(self, '_resolved_layout', resolve_rimworld_layout(self.game_install_path))
L51: 
L52:     # 根据创建上下文固定得到的布局计算绝对路径。
L53:     @property
L54:     def local_mods_path(self):
L55:         return self._resolved_layout.local_mods_root
L56:     @property
L57:     def game_dlc_path(self):
```
### L587–L623

```text
L587:         return self.build_profile_context(profile.id)
L588:         
L589:     
L590:     def get_launch_args(self, profile_id: str = '', include_executable: bool = True):
L591:         """
L592:         获取启动参数
L593:         :param profile_id: 环境ID，默认当前环境
L594:         :param include_executable: 是否包含游戏可执行文件路径
L595:         :return: 启动参数列表
L596:         """
L597:         if not profile_id:
L598:             profile_id = self.current_profile.id
L599:         profile = GameProfile.get_or_none(GameProfile.id == profile_id)
L600:         if not profile: return []
L601:         args = []
L602:         if include_executable:
L603:             args.append(GameManager.detect_executable(profile.game_install_path) or '')
L604:         # 只要环境显式绑定了用户数据根目录，就始终把 savedatafolder 注入启动参数。
L605:         default_user_data_path = GameManager.auto_detect_paths().get('user_data_path','')
L606:         profile_root = self._normalize_user_data_path(profile.user_data_path) if profile.user_data_path else ""
L607:         is_default_root = False
L608:         if default_user_data_path and profile_root:
L609:             is_default_root = UserDataRoot.from_raw(
L610:                 default_user_data_path,
L611:                 default_roots=GameManager.get_default_user_data_paths(),
L612:             ).equivalent_to(profile_root)
L613:         if profile_root and not is_default_root:
L614:             args.append(f"-savedatafolder={os.path.abspath(profile_root)}")
L615:         # 合并自定义参数
L616:         run_commands = getattr(profile, "run_commands", None) or []
L617:         if run_commands: args.extend(run_commands)
L618: 
L619:         return args
L620: 
L621:     def _clone_user_data(self, src_config_dir, target_root):
L622:         """复制存档和配置到新隔离区"""
L623:         # src_config_dir 通常是 .../LocalLow/Ludeon Studios/RimWorld by Ludeon Studios/Config
```
## R8｜七个 AI 读取工具的实际定义
来源：`backend/ai/ai_tools.py`；SHA-256：`4628ae1c71262819c57a036600ba38868dcfd66dc826caace6cc0356edfb823f`。
### L301–L365

```text
L301: TOOL_DEFINITIONS: dict[str, ToolDefinition[Any]] = {
L302:     "get_log_context": ToolDefinition(
L303:         name="get_log_context",
L304:         label="日志上下文",
L305:         llm_description="读取某条错误摘要对应的详细日志上下文，包含原始报错内容与截断堆栈。仅当摘要不足以定位问题时再调用。",
L306:         ui_description="获取指定错误日志的详细上下文（包含完整错误信息和核心堆栈）。仅当摘要中的 stack_preview 无法让你得出结论时才使用。",
L307:         args_model=GetLogContextArgs,
L308:         handler_name="_tool_get_log_context",
L309:         result_summarizer=_summarize_log_context,
L310:     ),
L311:     "search_mods": ToolDefinition(
L312:         name="search_mods",
L313:         label="搜索模组",
L314:         llm_description="按名称、别名、作者或 package_id 片段搜索可能对应的模组，用于确认准确 package_id。",
L315:         ui_description="当且仅当你不确定某个 Mod 的准确 package_id 时，使用此工具模糊搜索。可输入名字、别名或部分 ID 查找可能对应的 package_id。",
L316:         args_model=SearchModsArgs,
L317:         handler_name="_tool_search_mods",
L318:         result_summarizer=_summarize_search_mods,
L319:     ),
L320:     "get_active_mod_list": ToolDefinition(
L321:         name="get_active_mod_list",
L322:         label="当前启用列表",
L323:         llm_description="获取当前环境中的启用模组列表，用于整体排序和冲突排查。",
L324:         ui_description="获取当前排序文件中的已激活模组列表，用于宏观检查模组顺序，返回模组包名列表。",
L325:         args_model=GetActiveModListArgs,
L326:         handler_name="_tool_get_active_mod_list",
L327:         result_summarizer=_summarize_active_mods,
L328:     ),
L329:     "get_mod_info": ToolDefinition(
L330:         name="get_mod_info",
L331:         label="模组元数据",
L332:         llm_description=f"读取已安装模组的元数据。simple 返回基本信息：{AI_MOD_INFO_SIMPLE_FIELDS}；all 返回全部信息；specific 返回指定字段：{AI_MOD_INFO_ALLOWED_FIELDS}。",
L333:         ui_description=f"获取当前环境中已安装模组的元数据。simple 返回基本必要信息：{AI_MOD_INFO_SIMPLE_FIELDS}。all 返回全部信息；specific 返回可选字段信息：{AI_MOD_INFO_ALLOWED_FIELDS}。",
L334:         args_model=GetModInfoArgs,
L335:         handler_name="_tool_get_mod_info",
L336:         result_summarizer=_summarize_mod_info,
L337:     ),
L338:     "get_mod_rules": ToolDefinition(
L339:         name="get_mod_rules",
L340:         label="模组规则",
L341:         llm_description="读取模组依赖、前置、后置和不兼容规则，用于判断显性排序和兼容关系。",
L342:         ui_description="获取该模组的依赖模组、前置/后置模组和不兼容模组规则，以了解该模组与其它模组之间的显性关系。只有在怀疑缺失前置、加载顺序错误或存在不兼容模组时再调用。",
L343:         args_model=GetModRulesArgs,
L344:         handler_name="_tool_get_mod_rules",
L345:         result_summarizer=_summarize_mod_rules,
L346:     ),
L347:     "get_mod_user_data": ToolDefinition(
L348:         name="get_mod_user_data",
L349:         label="模组用户数据",
L350:         llm_description="读取管理器中的模组别名、标签、备注、颜色、分组等用户态信息，仅用于辅助理解玩家归类。",
L351:         ui_description="获取该已安装模组在管理器中的用户态上下文：别名、标签、备注、颜色、自定义类型和所属分组。这些信息只用于辅助理解玩家归类，属于模组管理器专有信息，与游戏机制无关。",
L352:         args_model=GetModUserDataArgs,
L353:         handler_name="_tool_get_mod_user_data",
L354:         result_summarizer=_summarize_mod_user_data,
L355:     ),
L356:     "get_group_mods": ToolDefinition(
L357:         name="get_group_mods",
L358:         label="分组模组",
L359:         llm_description="按分组名读取分组下的模组列表，仅用于辅助理解玩家自己的分组归类。",
L360:         ui_description="按分组名查询分组所含模组。分组信息只用于辅助理解玩家归类，属于模组管理器专有信息，与游戏机制无关。",
L361:         args_model=GetGroupModsArgs,
L362:         handler_name="_tool_get_group_mods",
L363:         result_summarizer=_summarize_group_mods,
L364:     ),
L365: }
```
### L400–L459

```text
L400: 
L401:     def get_tool_schemas(self, enabled_names: list[str] | None = None) -> list[dict[str, Any]]:
L402:         """
L403:         动态生成大模型所需的 OpenAI/LiteLLM 标准 JSON Schema 结构。
L404:         :param enabled_names: 前端传来的启用工具名称列表，如果为空则返回全部
L405:         """
L406:         schemas = []
L407:         enabled_names = enabled_names or []
L408:         for name, spec in self.registry.items():
L409:             # 过滤未启用的工具
L410:             if enabled_names and name not in enabled_names: continue
L411:             schemas.append(spec.to_openai_schema())
L412:         return schemas
L413: 
L414:     def execute_structured(self, name: str, arguments_str: str) -> ToolExecutionResult:
L415:         """
L416:         统一的工具执行路由。
L417: 
L418:         流程固定为：
L419:         1. 解析模型给出的 JSON 参数
L420:         2. 用 `pydantic` 做强校验和规范化
L421:         3. 把“干净参数对象”交给具体工具方法
L422: 
L423:         这样具体工具方法可以专注业务，不必反复写 `get()/int()/try/except` 样板代码。
L424:         """
L425:         try:
L426:             args = json.loads(arguments_str) if arguments_str else {}
L427:         except json.JSONDecodeError:
L428:             payload = {"error": "工具参数格式不正确，请重新组织输入后重试。"}
L429:             return self._build_execution_result(name=name, payload=payload)
L430: 
L431:         spec = self.registry.get(name)
L432:         if spec is None:
L433:             payload = {"error": "系统工具不可用，请稍后重试。"}
L434:             return self._build_execution_result(name=name, payload=payload)
L435: 
L436:         try:
L437:             validated_args = spec.definition.args_model.model_validate(args)
L438:         except ValidationError as e:
L439:             logger.warning(
L440:                 "[AI诊断] 工具参数校验失败。tool=%s",
L441:                 name,
L442:                 extra={"error_code": "AI.TOOL.ARGS_INVALID", "extra_context": {"tool": name}},
L443:             )
L444:             payload = {"error": "工具参数不符合要求，请重新组织输入后重试。"}
L445:             return self._build_execution_result(name=name, payload=payload)
L446: 
L447:         try:
L448:             logger.debug(f"[AI诊断] 开始执行工具 name={name} args={validated_args.model_dump()}")
L449:             payload = spec.handler(validated_args)
L450:             return self._build_execution_result(name=name, payload=payload)
L451:         except Exception as e:
L452:             logger.error(
L453:                 "AI 工具执行异常。tool=%s",
L454:                 name,
L455:                 extra={"error_code": "AI.TOOL.EXECUTION_FAILED", "extra_context": {"tool": name}},
L456:                 exc_info=True,
L457:             )
L458:             payload = {"error": "工具执行失败，请稍后重试。"}
L459:             return self._build_execution_result(name=name, payload=payload)
```
## R9｜有界 Agent 执行、重复工具限制与轨迹
来源：`backend/ai/assistant_runtime.py`；SHA-256：`ff3686df2af34b84181fb0fa2334e31972de601c05e6a0e895b44a6ccfccc967`。
### L43–L53

```text
L43: from backend.settings import settings
L44: from backend.utils.constants import get_lang_by_code
L45: from backend.utils.error_contract import build_stack_detail, classify_exception
L46: from backend.utils.event_bus import EventBus
L47: from backend.utils.logger import logger
L48: 
L49: ASSISTANT_MAX_LOOPS = 10
L50: TRACE_RECORD_LIMIT = 200
L51: SESSION_RECENT_MESSAGE_LIMIT = 8
L52: SESSION_EVIDENCE_LIMIT = 12
L53: TRACE_TOOL_TIME_SPAN_FALLBACK = 1000
```
### L1656–L1686

```text
L1656:     def _run_session_loop(
L1657:         self,
L1658:         *,
L1659:         session_id: str,
L1660:         messages: list[dict],
L1661:         llm_kwargs: dict,
L1662:         model_name: str,
L1663:         token_usage: dict[str, Any],
L1664:         prompt_input_breakdown: dict[str, Any],
L1665:         assistant: AssistantDefinition,
L1666:         tools,
L1667:         tool_choice,
L1668:         tool_executor: AIToolExecutor,
L1669:         trace_record: RequestTraceRecord | None = None,
L1670:         session_state: SessionState,
L1671:         resolved_attachments: list[ResolvedContextAttachment],
L1672:     ) -> dict[str, Any]:
L1673:         loop_count = 0
L1674:         executed_tool_signatures: set[str] = set()
L1675:         base_prompt_tokens = int((prompt_input_breakdown or {}).get("total_tokens", 0) or 0)
L1676:         cumulative_prompt_input_breakdown = create_prompt_input_breakdown()
L1677: 
L1678:         while loop_count < ASSISTANT_MAX_LOOPS:
L1679:             loop_count += 1
L1680:             try:
L1681:                 prompt_tokens_this_round = self._estimate_messages_tokens(messages, model_name)
L1682:                 token_usage["estimated_prompt_tokens"] += prompt_tokens_this_round
L1683:                 token_usage["estimated_total_tokens"] += prompt_tokens_this_round
L1684:                 round_prompt_input_breakdown = self._build_prompt_input_breakdown_for_round(
L1685:                     prompt_tokens_this_round=prompt_tokens_this_round,
L1686:                     base_prompt_tokens=base_prompt_tokens,
```
### L1757–L1784

```text
L1757:                 logger.debug(
L1758:                     f"[AI会话] 会话完成 session_id={session_id} loop={loop_count} "
L1759:                     f"analysis_chars={len(final_response.get('analysis', ''))} "
L1760:                     f"total_tokens≈{token_usage['estimated_total_tokens']}"
L1761:                 )
L1762:                 return final_response
L1763:             except AssistantRequestCancelled:
L1764:                 raise
L1765:             except Exception as exc:
L1766:                 error_id = uuid.uuid4().hex[:10]
L1767:                 logger.error(
L1768:                     "AI 会话执行失败。session_id=%s",
L1769:                     session_id,
L1770:                     extra={"error_code": "AI.SESSION.RUN_FAILED", "extra_context": {"session_id": session_id, "error_id": error_id}},
L1771:                     exc_info=True,
L1772:                 )
L1773:                 return self._build_error_response(exc, token_usage, error_id=error_id, context={"session_id": session_id})
L1774: 
L1775:         return self._build_forced_summary(
L1776:             session_id,
L1777:             messages,
L1778:             llm_kwargs,
L1779:             model_name,
L1780:             token_usage,
L1781:             assistant=assistant,
L1782:             prompt_input_breakdown=prompt_input_breakdown,
L1783:             accumulated_prompt_input_breakdown=cumulative_prompt_input_breakdown,
L1784:         )
```
### L1786–L1836

```text
L1786:     def _execute_tool_calls(
L1787:         self,
L1788:         *,
L1789:         session_id: str,
L1790:         formatted_tool_calls: list[dict[str, Any]],
L1791:         tool_executor: AIToolExecutor,
L1792:         messages: list[dict],
L1793:         executed_tool_signatures: set[str],
L1794:         trace_record: RequestTraceRecord | None = None,
L1795:         session_state: SessionState,
L1796:     ) -> None:
L1797:         for tc in formatted_tool_calls:
L1798:             func_name = tc["function"]["name"]
L1799:             func_args = tc["function"]["arguments"]
L1800:             tool_start_at = time.perf_counter()
L1801:             tool_display = tool_executor.build_tool_call_display(func_name, func_args)
L1802: 
L1803:             EventBus.emit("ai-tool-call", {
L1804:                 "session_id": session_id,
L1805:                 "tool_id": tc["id"],
L1806:                 "name": func_name,
L1807:                 "display_name": tool_display.get("display_name", ""),
L1808:                 "arguments": func_args,
L1809:                 "arguments_preview": tool_display.get("arguments_preview", ""),
L1810:                 "arguments_pretty": tool_display.get("arguments_pretty", ""),
L1811:             })
L1812: 
L1813:             call_signature = f"{func_name}|{func_args}"
L1814:             if call_signature in executed_tool_signatures:
L1815:                 tool_execution = ToolExecutionResult(
L1816:                     name=func_name,
L1817:                     ok=False,
L1818:                     model_output=json.dumps({"error": DUPLICATE_TOOL_CALL_ERROR}, ensure_ascii=False),
L1819:                     data={"error": DUPLICATE_TOOL_CALL_ERROR},
L1820:                     summary=f"执行失败：{DUPLICATE_TOOL_CALL_ERROR}",
L1821:                     error=DUPLICATE_TOOL_CALL_ERROR,
L1822:                 )
L1823:                 logger.warning(f"[AI防死锁] 拦截重复调用: {call_signature}")
L1824:             else:
L1825:                 executed_tool_signatures.add(call_signature)
L1826:                 self._raise_if_cancelled(session_id)
L1827:                 try:
L1828:                     tool_execution = tool_executor.execute_structured(func_name, func_args)
L1829:                 except AssistantRequestCancelled:
L1830:                     raise
L1831:                 except Exception as tool_err:
L1832:                     logger.error(f"[AI会话] 工具执行异常 tool={func_name}: {tool_err}", exc_info=True)
L1833:                     tool_error_message = "工具执行失败，请稍后重试。"
L1834:                     tool_execution = ToolExecutionResult(
L1835:                         name=func_name,
L1836:                         ok=False,
```
### L1847–L1889

```text
L1847:                 func_name,
L1848:                 tool_execution.model_output,
L1849:             )
L1850:             result_display = tool_executor.build_tool_result_display(tool_execution)
L1851:             if trace_record is not None:
L1852:                 trace_record.tool_calls.append(
L1853:                     RequestToolCallTrace(
L1854:                         tool_id=tc["id"],
L1855:                         name=func_name,
L1856:                         arguments=func_args,
L1857:                         status="done" if tool_ok else "error",
L1858:                         result=tool_execution.model_output,
L1859:                         duration_ms=duration_ms,
L1860:                         summary=tool_summary,
L1861:                         display_name=tool_display.get("display_name", ""),
L1862:                         arguments_preview=tool_display.get("arguments_preview", ""),
L1863:                         arguments_pretty=tool_display.get("arguments_pretty", ""),
L1864:                         result_pretty=result_display.get("result_pretty", ""),
L1865:                     )
L1866:                 )
L1867: 
L1868:             EventBus.emit("ai-tool-result", {
L1869:                 "session_id": session_id,
L1870:                 "tool_id": tc["id"],
L1871:                 "status": "done" if tool_ok else "error",
L1872:                 "duration_ms": duration_ms,
L1873:                 "display_name": result_display.get("display_name", ""),
L1874:                 "summary": tool_summary,
L1875:                 "result": tool_execution.model_output,
L1876:                 "result_pretty": result_display.get("result_pretty", ""),
L1877:             })
L1878:             self._append_evidence_item(
L1879:                 session_state,
L1880:                 source=f"tool:{func_name}",
L1881:                 summary=tool_summary,
L1882:                 data=tool_execution.data,
L1883:             )
L1884: 
L1885:             messages.append({
L1886:                 "role": "tool",
L1887:                 "tool_call_id": tc["id"],
L1888:                 "name": func_name,
L1889:                 "content": tool_execution.model_output,
```
## R10｜内置助手的 RimWorld 提示词、工具和动作
来源：`backend/ai/def_entries.py`；SHA-256：`ef160a5e5755d8f43353391dbcb4e7e14e23f84972b1ce4aa3262b9ed863123a`。
### L105–L184

```text
L105:         "game_log_analysis": _with_prompt_meta({
L106:             "name": "游戏日志分析",
L107:             "description": "分析 RimWorld 游戏日志，帮助玩家解决 Mod 冲突和报错。",
L108:             "system": (
L109:                 "你是一个专门处理 Unity3D 和 RimWorld 游戏错误日志的诊断专家，"
L110:                 "擅长判断真正根因、区分连锁报错，并给出可靠且节制的修复建议。\n"
L111:                 "如果涉及 Mod 冲突、排序错误、缺失前置或不兼容关系，请明确指出所有涉及 Mod 的 package_id。\n\n"
L112:                 "- 日志来源: {diagnosis_context.source_type}\n"
L113:                 "- 日志文件名: {diagnosis_context.filename}\n\n"
L114:                 "会话流程：\n"
L115:                 "1. 首轮先阅读已压缩的错误摘要，识别最高价值的 1-3 个根因候选；不要为了凑证据而反复调用工具。\n"
L116:                 "2. 只有在摘要不足以确认根因时才调用 get_log_context。\n"
L117:                 "3. 调用 get_log_context 时，只能使用附件错误摘要中 errors[].target_line 的原始数值。不要把日志编号、表格序号、重复次数或你推测的行号当作 target_line。\n"
L118:                 "4. 工具返回足够证据后立即收敛结论；不要重复查询同一 target_line。\n"
L119:                 "5. 明确区分“已确认事实”“高概率推断”“仍需验证”。\n\n"
L120:                 "诊断边界：\n"
L121:                 "- 优先解释最早、最具体、最能导致后续连锁报错的异常；高频报错不一定是根因。\n"
L122:                 "- 不要把存档编辑、开发者模式操作、向作者反馈等建议写成可执行 action；这些只能写在 analysis 中。\n"
L123:                 "- 涉及启用/停用 Mod 或新增排序/冲突规则时，只有 package_id、动作方向和理由都明确，才允许输出 action；否则 actions 必须为空数组。\n"
L124:                 "- action 只表达应用可以直接执行的最小变更，不要在 action 里写夸大的标题或说明。\n\n"
L125:                 "analysis 部分尽量按以下结构组织：\n"
L126:                 "- 结论\n"
L127:                 "- 关键证据\n"
L128:                 "- 修复建议\n"
L129:                 "- 待验证项"
L130:             ),
L131:             "user_template": "{message}\n\n{diagnosis_context_block}\n",
L132:         }, category="assistant", attachment_kinds=["diagnosis_context"]),
L133:         "mod_general_assistant": _with_prompt_meta({
L134:             "name": "模组通用助手",
L135:             "description": "面向模组信息、排序、兼容、依赖与使用建议的多轮助手默认模板。",
L136:             "system": "你是 RimCrow 内置的模组助手，负责围绕 RimWorld 模组信息进行多轮协助。\n\n你的基本原则：\n1. 优先利用当前消息、已解析附件摘要和已知事实回答，不要先入为主。\n2. 如果工具已返回足够信息，先基于现有证据收敛，不要反复检索同一问题。\n3. 明确区分“已确认事实”和“基于经验的推断”。\n4. 涉及模组时，尽量同时写出名称与 packageId；如果信息不足，就直接说明不足，不要编造。\n5. 如果用户是在比较、筛选、解释或排查模组，请优先给出清楚结论，再补简要依据。\n6. 回答始终使用 {target_lang}。\n\n如果当前会话没有足够上下文，你可以先提出最关键的下一步问题，或者在允许时调用工具补齐信息。",
L137:             "user_template": "{message}\n\n{mod_selection_block}",
L138:         }, category="assistant", attachment_kinds=["mod_selection"]),
L139:     }
L140: 
L141: 
L142: def get_default_assistant_definitions() -> dict[str, dict]:
L143:     """返回系统内置助手入口定义。"""
L144:     return {
L145:         "assistant.log_game": _assistant_definition(
L146:             assistant_id="assistant.log_game",
L147:             name="游戏日志助手",
L148:             description="面向 RimWorld 游戏日志的多轮强化诊断助手。",
L149:             prompt_id="game_log_analysis",
L150:             source_kinds=["logs"],
L151:             tool_scope=[
L152:                 "get_log_context",
L153:                 "search_mods",
L154:                 "get_active_mod_list",
L155:                 "get_mod_info",
L156:                 "get_mod_rules",
L157:                 "get_mod_user_data",
L158:                 "get_group_mods",
L159:             ],
L160:             action_types=["MOD_STATE", "MOD_RULE"],
L161:         ),
L162:         "assistant.log_app": _assistant_definition(
L163:             assistant_id="assistant.log_app",
L164:             name="软件日志助手",
L165:             description="面向 RimCrow 自身运行日志的多轮诊断助手。",
L166:             prompt_id="app_log_analysis",
L167:             source_kinds=["logs"],
L168:             tool_scope=[],
L169:             action_types=["TEXT_TRANSFER"],
L170:         ),
L171:         "assistant.mod_general": _assistant_definition(
L172:             assistant_id="assistant.mod_general",
L173:             name="模组助手",
L174:             description="面向单个或多个模组信息的通用多轮助手。",
L175:             prompt_id="mod_general_assistant",
L176:             source_kinds=["mods", "global"],
L177:             tool_scope=[
L178:                 "search_mods",
L179:                 "get_mod_info",
L180:                 "get_mod_rules",
L181:                 "get_mod_user_data",
L182:                 "get_group_mods",
L183:             ],
L184:             action_types=["MOD_STATE", "MOD_RULE"],
```
## R11｜动作卡片的执行边界
来源：`frontend/src/features/ai/ai-store/runtime/aiActionRuntime.js`；SHA-256：`315093b96c4744ab89a57fdf0bef576260c73f186d84be7d16c9ff3f63a7d7dc`。
### L382–L437

```text
L382:   async MOD_STATE(payload, action) {
L383:     const shouldEnable = getActionVariant(action) !== 'disable'
L384:     const modIds = Array.isArray(payload.mod_ids) ? payload.mod_ids : []
L385:     if (modIds.length === 0) {
L386:       toast.warning(getActionMissingPayloadMessage(action))
L387:       return
L388:     }
L389:     // 默认前端会先把这类动作拆成单模组卡片；
L390:     // 这里仍保留逐项执行兜底，避免其它入口直接执行批量 payload。
L391:     for (const modId of modIds) {
L392:       await modStore.changeModsActive([modId], shouldEnable)
L393:     }
L394:     const successMessage = getActionSuccessMessage(action)
L395:     if (successMessage) {
L396:       toast.success(successMessage)
L397:     }
L398:   },
L399: 
L400:   async MOD_RULE(payload, action) {
L401:     /**
L402:      * 应用一条用户规则动作。
L403:      *
L404:      * 规则类动作有副作用且可能影响排序，因此必须先确认，再在成功后
L405:      * 根据后端定义决定是否追加“顺手自动排序”的二次动作。
L406:      */
L407:     const normalizedType = String(action?.variant || payload.rule_type || '')
L408:     if (!payload.mod_id || !payload.target_id || !normalizedType) {
L409:       toast.warning(getActionMissingPayloadMessage(action))
L410:       return
L411:     }
L412: 
L413:     const confirmMeta = getActionConfirmMeta(action)
L414:     const confirmed = await confirmStore.confirmAction(
L415:       confirmMeta.title || t('ai.actions.fallback.confirm_title', '确认执行操作'),
L416:       confirmMeta.message || getActionPreview(action) || getActionDescription(action),
L417:       { type: 'warning', confirmText: confirmMeta.confirmText || t('common.action.confirm', '确认'), cancelText: t('common.action.cancel', '取消') },
L418:     )
L419:     if (!confirmed) return
L420: 
L421:     await ruleStore.addUserModRule(payload.mod_id, normalizedType, payload.target_id)
L422: 
L423:     const postSuccessMeta = getActionPostSuccessMeta(action)
L424:     if (postSuccessMeta.message && await confirmStore.confirmAction(
L425:       postSuccessMeta.title || t('ai.actions.fallback.applied_title', '操作已应用'),
L426:       postSuccessMeta.message,
L427:       { type: 'success', confirmText: postSuccessMeta.confirmText || t('common.action.ok', '确定'), cancelText: t('common.action.later_short', '稍后') },
L428:     )) {
L429:       await modStore.autoSortMods()
L430:       return
L431:     }
L432: 
L433:     const successMessage = getActionSuccessMessage(action)
L434:     if (successMessage && !postSuccessMeta.message) {
L435:       toast.success(successMessage)
L436:     }
L437:   },
```
## R12｜启停动作修改列表状态，不等于部署已验证
来源：`frontend/src/features/mod/stores/modStore.js`；SHA-256：`8d56991edd9283be5de762579ed07fca7b5530deb7c5d65c0ecc832df94e3685`。
### L722–L745

```text
L722:   // 批量启用/停用Mod
L723:   const changeModsActive = async (ids, active) => {
L724:     if(typeof ids === 'string') ids = [ids]
L725:     const nextIds = Array.isArray(ids) ? [...ids] : []
L726:     if (nextIds.length === 0) return false
L727:     return await runListHistoryTransaction({
L728:       type: active ? 'change-active-enable' : 'change-active-disable',
L729:       label: active
L730:         ? t('history.mod.activate_count', '启用 {count} 个 Mod', { count: nextIds.length })
L731:         : t('history.mod.deactivate_count', '停用 {count} 个 Mod', { count: nextIds.length }),
L732:       trackedModIds: nextIds
L733:     }, async () => {
L734:       removeIdsOnAllList(nextIds)
L735:       if(active) {
L736:         await smartInsertMods(nextIds)
L737:       } else {
L738:         inactiveIds.value.unshift(...nextIds)
L739:       }
L740:       takeModListByIds(nextIds).forEach(mod => {
L741:         mod.last_moved_time = Date.now()
L742:         mod.last_active_time = Date.now()
L743:       })
L744:     })
L745:   }
```
## R13｜现有单文件原子写入保护
来源：`backend/utils/json_io.py`；SHA-256：`87d85b38de9cd7b6cfa7ea5816aad90cb98d8d0e819d1f7c64004eccfe85bb09`。
### L1–L49

```text
L1: import json
L2: import os
L3: import tempfile
L4: from pathlib import Path
L5: from typing import Any
L6: 
L7: 
L8: def write_json_atomic(path: Path | str, payload: Any, *, indent: int = 4, lock: Any = None) -> None:
L9:     """
L10:     以“同目录临时文件 + fsync + os.replace”写入 JSON。
L11:     同目录替换能保证跨平台原子性；fsync 用于降低断电或崩溃时写入半截文件的风险。
L12:     """
L13:     target_path = Path(path)
L14:     target_path.parent.mkdir(parents=True, exist_ok=True)
L15: 
L16:     def _write() -> None:
L17:         fd = None
L18:         temp_path: Path | None = None
L19:         try:
L20:             fd, temp_name = tempfile.mkstemp(
L21:                 prefix=f".{target_path.stem}.",
L22:                 suffix=".tmp",
L23:                 dir=str(target_path.parent),
L24:             )
L25:             temp_path = Path(temp_name)
L26:             with os.fdopen(fd, "w", encoding="utf-8") as handle:
L27:                 fd = None
L28:                 json.dump(payload, handle, indent=indent, ensure_ascii=False)
L29:                 handle.flush()
L30:                 os.fsync(handle.fileno())
L31:             os.replace(temp_path, target_path)
L32:         except Exception:
L33:             if fd is not None:
L34:                 try:
L35:                     os.close(fd)
L36:                 except OSError:
L37:                     pass
L38:             if temp_path and temp_path.exists():
L39:                 try:
L40:                     temp_path.unlink()
L41:                 except OSError:
L42:                     pass
L43:             raise
L44: 
L45:     if lock is None:
L46:         _write()
L47:         return
L48:     with lock:
L49:         _write()
```
## R14｜现有包安装的暂存与替换逻辑
来源：`backend/managers/mgr_mod_package.py`；SHA-256：`9866025b05f6ce3f890e721c2741b180bf431b5e71a5e0a0ad52aace99f21764`。
### L997–L1070

```text
L997:     def _import_mod_directories(
L998:         self,
L999:         bundle: zipfile.ZipFile,
L1000:         mod_entries: list[dict[str, Any]],
L1001:         target_root: str,
L1002:         conflict_plan: dict[str, dict[str, Any]],
L1003:         *,
L1004:         cancel_event: threading.Event | None = None,
L1005:         progress_callback: Callable[[int, int, dict[str, Any]], None] | None = None,
L1006:     ) -> dict[str, Any]:
L1007:         if not target_root:
L1008:             raise ValueError("未指定有效的模组导入目标")
L1009:         os.makedirs(target_root, exist_ok=True)
L1010:         warnings: list[str] = []
L1011:         imported_mods: list[dict[str, Any]] = []
L1012:         skipped_mods: list[dict[str, Any]] = []
L1013:         renamed_mods: list[dict[str, Any]] = []
L1014:         normalized_entries = [item for item in mod_entries if str(item.get("folder_name") or "").strip()]
L1015:         total_mods = len(normalized_entries)
L1016: 
L1017:         for index, item in enumerate(normalized_entries, start=1):
L1018:             self._check_import_cancelled(cancel_event)
L1019:             if progress_callback:
L1020:                 progress_callback(index, total_mods, item)
L1021:             folder_name = str(item.get("folder_name") or "").strip()
L1022:             plan = conflict_plan.get(folder_name.lower(), {"mode": "overwrite", "rename_to": ""})
L1023:             destination_name = folder_name
L1024:             destination_path = os.path.join(target_root, destination_name)
L1025:             if os.path.exists(destination_path):
L1026:                 mode = plan.get("mode", "overwrite")
L1027:                 if mode == "skip":
L1028:                     skipped_mods.append({"folder_name": folder_name, "path": destination_path})
L1029:                     continue
L1030:                 if mode == "rename":
L1031:                     destination_name = plan.get("rename_to") or self._build_unique_import_name(target_root, folder_name)
L1032:                     destination_path = os.path.join(target_root, destination_name)
L1033:                     renamed_mods.append({"from": folder_name, "to": destination_name})
L1034:             staging_root = self._extract_mod_directory_to_staging(
L1035:                 bundle,
L1036:                 folder_name,
L1037:                 destination_path,
L1038:                 cancel_event=cancel_event,
L1039:             )
L1040:             try:
L1041:                 replace_dir_atomically(destination_path, staging_root)
L1042:             finally:
L1043:                 shutil.rmtree(staging_root, ignore_errors=True)
L1044:             imported_mods.append({"folder_name": destination_name, "path": destination_path})
L1045:             if destination_name != folder_name:
L1046:                 warnings.append(f'模组 "{folder_name}" 已另存为 "{destination_name}"')
L1047: 
L1048:         return {
L1049:             "warnings": warnings,
L1050:             "imported_mods": imported_mods,
L1051:             "skipped_mods": skipped_mods,
L1052:             "renamed_mods": renamed_mods,
L1053:         }
L1054: 
L1055:     def _extract_mod_directory_to_staging(
L1056:         self,
L1057:         bundle: zipfile.ZipFile,
L1058:         folder_name: str,
L1059:         destination_path: str,
L1060:         *,
L1061:         cancel_event: threading.Event | None = None,
L1062:     ) -> Path:
L1063:         staging_root = create_sibling_stage_dir(destination_path, "mod-import-")
L1064:         extract_prefix_to_dir(
L1065:             bundle,
L1066:             f"mods/{folder_name}",
L1067:             staging_root,
L1068:             cancel_check=lambda: self._check_import_cancelled(cancel_event),
L1069:         )
L1070:         return staging_root
```
## R15｜凭据保存与拒绝明文降级的分支
来源：`backend/utils/secret_store.py`；SHA-256：`ce35f77596757cd54447478f5f4b5bd5d61dda36762e1afd87a8742977353e04`。
### L95–L145

```text
L95:     def get_secret(self, key: str) -> str:
L96:         normalized = self.validate_key(key)
L97:         backend = self.backend
L98:         if backend is None:
L99:             self.last_error = _tr("secret.errors.backend_unavailable", "系统凭据库不可用")
L100:             return ""
L101:         try:
L102:             value = backend.get_password(self.service_name, normalized)
L103:             if not value:
L104:                 value = self._migrate_legacy_secret(backend, normalized)
L105:             self.last_error = ""
L106:             return str(value or "")
L107:         except Exception as exc:
L108:             self.last_error = str(exc) or _tr("secret.errors.read_failed", "系统凭据库读取失败")
L109:             logger.warning("读取系统凭据失败: %s", normalized, exc_info=True)
L110:             return ""
L111: 
L112:     def _migrate_legacy_secret(self, backend: Any, key: str) -> str:
L113:         for legacy_service_name in self.legacy_service_names:
L114:             legacy_value = backend.get_password(legacy_service_name, key)
L115:             if not legacy_value:
L116:                 continue
L117:             try:
L118:                 backend.set_password(self.service_name, key, str(legacy_value))
L119:                 try:
L120:                     backend.delete_password(legacy_service_name, key)
L121:                 except Exception as exc:
L122:                     if not self._is_missing_secret_error(exc):
L123:                         logger.warning("旧凭据删除失败，将在后续启动继续清理: service=%s key=%s", legacy_service_name, key, exc_info=True)
L124:                 logger.info("已迁移旧凭据: %s -> %s key=%s", legacy_service_name, self.service_name, key)
L125:             except Exception:
L126:                 logger.warning("旧凭据迁移失败，已保留旧值等待下次重试: service=%s key=%s", legacy_service_name, key, exc_info=True)
L127:             return str(legacy_value or "")
L128:         return ""
L129: 
L130:     def set_secret(self, key: str, value: str) -> None:
L131:         normalized = self.validate_key(key)
L132:         text = str(value or "").strip()
L133:         backend = self.backend
L134:         if backend is None:
L135:             raise self._fail(_tr("secret.errors.local_store_unavailable", "本机安全存储不可用，请检查系统凭据服务后重试"))
L136:         try:
L137:             if text:
L138:                 backend.set_password(self.service_name, normalized, text)
L139:             else:
L140:                 self.delete_secret(normalized)
L141:             self.fallback_keys.discard(normalized)
L142:             self.fallback_errors.pop(normalized, None)
L143:             self.last_error = ""
L144:         except Exception as exc:
L145:             raise self._fail(_tr("secret.errors.save_failed", "无法保存密钥，请确认本机安全存储可用后重试"), exc) from exc
```
## R16｜浏览器模式桥接：公开方法枚举、调用分支和 CORS
来源：`backend/browser_runtime.py`；SHA-256：`4f349b2df55b614cad675711e7ec0f1c0240c9c21478e37d00673f546111397b`。
### L588–L625

```text
L588: 
L589: class BrowserAppServer:
L590:     def __init__(self, api, static_root: Path | None, use_dev_server: bool = False):
L591:         self.api = api
L592:         self.static_root = static_root
L593:         self.use_dev_server = use_dev_server
L594:         self._shutdown_event = threading.Event()
L595:         self._serve_thread: threading.Thread | None = None
L596:         self._session_manager = BrowserSessionManager(self.request_shutdown)
L597:         self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._create_handler())
L598:         self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
L599: 
L600:     @staticmethod
L601:     def resolve_static_root():
L602:         candidates = [Path(os.getcwd()) / "frontend" / "dist", Path(os.getcwd())]
L603:         for root in candidates:
L604:             if (root / "index.html").exists(): return root
L605:         return None
L606: 
L607:     @staticmethod
L608:     def should_use_dev_server():
L609:         return not getattr(__import__("sys"), "frozen", False) and is_port_available(DEV_SERVER_HOST, DEV_SERVER_PORT)
L610: 
L611:     def get_launch_url(self):
L612:         api_base = quote(self.base_url, safe="")
L613:         if self.use_dev_server:
L614:             separator = "&" if "?" in DEV_SERVER_URL else "?"
L615:             return f"{DEV_SERVER_URL}{separator}api_base={api_base}"
L616:         return f"{self.base_url}/?api_base={api_base}"
L617: 
L618:     def get_available_methods(self):
L619:         blocked = {"cleanup", "get_window", "set_window"}
L620:         return sorted(
L621:             name
L622:             for name in dir(self.api)
L623:             if not name.startswith("_") and name not in blocked and callable(getattr(self.api, name))
L624:         )
L625: 
```
### L690–L718

```text
L690:                     session_id = str(payload.get("client_id", "")).strip()
L691:                     ok = outer._session_manager.heartbeat(session_id)
L692:                     return self._send_json({"status": "success" if ok else "warning", "message": "" if ok else tr("browser.session.expired", "浏览器会话已失效，请刷新页面或重启软件。")})
L693:                 if parsed.path == "/api/session/close":
L694:                     payload = self._read_json_body()
L695:                     session_id = str(payload.get("client_id", "")).strip()
L696:                     outer._session_manager.close_session(session_id)
L697:                     return self._send_json({"status": "success"})
L698:                 if parsed.path.startswith("/api/call/"):
L699:                     method_name = unquote(parsed.path.removeprefix("/api/call/")).strip()
L700:                     return self._handle_api_call(method_name)
L701:                 self.send_error(404, "Not Found")
L702: 
L703:             def log_message(self, format, *args): return
L704: 
L705:             def _handle_api_call(self, method_name: str):
L706:                 if method_name not in outer.get_available_methods():
L707:                     return self._send_json({"status": "error", "message": tr("browser.api.method_missing", "请求的后端接口不存在，请刷新页面或重启软件后重试。")}, status_code=404)
L708: 
L709:                 payload = self._read_json_body()
L710:                 args = payload.get("args", [])
L711:                 kwargs = payload.get("kwargs", {})
L712:                 method = getattr(outer.api, method_name)
L713:                 try:
L714:                     result = method(*args, **kwargs)
L715:                 except Exception as exc:
L716:                     error_id = uuid.uuid4().hex[:10]
L717:                     user_message = tr("browser.api.call_failed", "后端接口执行失败。可能是运行环境、路径权限或内部状态暂时不可用。")
L718:                     envelope = coerce_error_envelope(
```
### L784–L805

```text
L784: 
L785:                 if not outer.static_root:
L786:                     return self._send_json({"status": "error", "message": tr("browser.static.frontend_missing", "未找到前端页面资源，请重新构建前端或检查安装包完整性。")}, status_code=500)
L787: 
L788:                 request_path = parsed.path or "/"
L789:                 relative_path = request_path.lstrip("/")
L790:                 candidate = (outer.static_root / relative_path).resolve()
L791:                 root_resolved = outer.static_root.resolve()
L792: 
L793:                 if request_path == "/" or not relative_path:
L794:                     candidate = root_resolved / "index.html"
L795: 
L796:                 if not str(candidate).startswith(str(root_resolved)):
L797:                     self.send_error(403, "Forbidden")
L798:                     return
L799: 
L800:                 if candidate.is_dir():
L801:                     candidate = candidate / "index.html"
L802:                 if not candidate.exists() or not candidate.is_file():
L803:                     candidate = root_resolved / "index.html"
L804: 
L805:                 content_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
```
### L837–L850

```text
L837:             def _read_json_body(self):
L838:                 content_length = int(self.headers.get("Content-Length", "0") or "0")
L839:                 if content_length <= 0: return {}
L840:                 raw_body = self.rfile.read(content_length)
L841:                 if not raw_body: return {}
L842:                 try: return json.loads(raw_body.decode("utf-8"))
L843:                 except Exception: return {}
L844: 
L845:             def _send_common_headers(self, content_type="application/json; charset=utf-8"):
L846:                 self.send_header("Content-Type", content_type)
L847:                 self.send_header("Access-Control-Allow-Origin", "*")
L848:                 self.send_header("Access-Control-Allow-Headers", "Content-Type")
L849:                 self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
L850: 
```
## R17｜旧日志规则以 RimWorld 为中心
来源：`backend/managers/mgr_game_logs.py`；SHA-256：`114462ec1823b7619f37b0bb5ff606a5f21506e966d6b5cf3fccdd195a3f8efe`。
### L1–L115

```text
L1: # backend/managers/mgr_game_logs.py
L2: import copy
L3: import json
L4: import os
L5: import re
L6: from datetime import datetime
L7: import threading
L8: import time
L9: 
L10: from backend.load_order.package_tokens import parse_package_token
L11: from backend.managers.mgr_game import GameManager
L12: from backend.managers.mgr_load_order import LoadOrderManager
L13: from backend.managers.mgr_profile import ProfileContext
L14: from backend.i18n.messages import localized_key, tr
L15: from backend.utils.event_bus import EventBus
L16: from backend.utils.logger import BaseLogReader, generate_log_id, logger
L17: 
L18: 
L19: INFERRED_TYPE_LABELS = {
L20:     'XMLSyntaxError': tr('game_log.inferred_type.xml_syntax_error', 'XML 语法错误'),
L21:     'DefConfigError': tr('game_log.inferred_type.def_config_error', '定义配置错误'),
L22:     'CrossRefError': tr('game_log.inferred_type.cross_ref_error', '引用缺失'),
L23:     'AssemblyConflict': tr('game_log.inferred_type.assembly_conflict', '程序集冲突'),
L24:     'TickException': tr('game_log.inferred_type.tick_exception', 'Tick 异常'),
L25:     'DrawException': tr('game_log.inferred_type.draw_exception', '绘制异常'),
L26:     'NullReference': tr('game_log.inferred_type.null_reference', '空引用异常'),
L27:     'OutOfMemory': tr('game_log.inferred_type.out_of_memory', '内存不足'),
L28:     'MissingTexture': tr('game_log.inferred_type.missing_texture', '贴图缺失'),
L29:     'MissingDef': tr('game_log.inferred_type.missing_def', '定义缺失'),
L30:     'XMLFieldError': tr('game_log.inferred_type.xml_field_error', 'XML 字段错误'),
L31:     'MissingReference': tr('game_log.inferred_type.missing_reference', '引用加载失败'),
L32:     'TranslationError': tr('game_log.inferred_type.translation_error', '翻译数据错误'),
L33:     'PackageIdError': tr('game_log.inferred_type.package_id_error', '包名格式错误'),
L34:     'PatchWarning': tr('game_log.inferred_type.patch_warning', '补丁警告'),
L35: }
L36: 
L37: 
L38: class LogAnalyzer:
L39:     """启发式日志诊断分析器：推断错误类型并提取关联的 Mod ID 或 文件路径"""
L40:     def __init__(self):
L41:         self.known_rules = [
L42:             {
L43:                 'id': 'missing_def',
L44:                 'type': 'MissingDef',
L45:                 'pattern': re.compile(r'\b\S+\s+\S+\s+is not defined in any loaded mods\.', re.IGNORECASE),
L46:                 'explanation': tr('game_log.diagnosis.missing_def', '有 Mod 正在引用不存在的定义，通常是缺少前置、版本不匹配或旧存档残留引用。')
L47:             },
L48:             {
L49:                 'id': 'xml_unknown_field',
L50:                 'type': 'XMLFieldError',
L51:                 'pattern': re.compile(r"XML error:\s+.+?\s+doesn't correspond to any field in type\s+.+?\.", re.IGNORECASE),
L52:                 'explanation': tr('game_log.diagnosis.xml_unknown_field', '某个 XML 字段不被当前游戏或目标类型识别，常见原因是 Mod 版本过旧、游戏版本不匹配或配置写错。')
L53:             },
L54:             {
L55:                 'id': 'config_error',
L56:                 'type': 'DefConfigError',
L57:                 'pattern': re.compile(r'Config error in', re.IGNORECASE),
L58:                 'explanation': tr('game_log.diagnosis.config_error', '某个定义配置不符合游戏要求，通常需要检查日志提到的对象和对应 Mod。')
L59:             },
L60:             {
L61:                 'id': 'cross_reference_missing',
L62:                 'type': 'CrossRefError',
L63:                 'pattern': re.compile(r'Could not resolve cross-reference|Could not resolve reference to object with loadID', re.IGNORECASE),
L64:                 'explanation': tr('game_log.diagnosis.cross_reference_missing', '游戏无法找到被引用的对象，常见原因是缺少依赖、加载顺序不对或存档里引用了已移除内容。')
L65:             },
L66:             {
L67:                 'id': 'could_not_load_reference',
L68:                 'type': 'MissingReference',
L69:                 'pattern': re.compile(r'Could not load reference to\s+.+?\s+named\s+.+', re.IGNORECASE),
L70:                 'explanation': tr('game_log.diagnosis.could_not_load_reference', '日志中的对象引用没有加载成功，优先检查相关 Mod 是否启用、依赖是否齐全。')
L71:             },
L72:             {
L73:                 'id': 'missing_texture',
L74:                 'type': 'MissingTexture',
L75:                 'pattern': re.compile(r'Failed to find any texture|Could not load UnityEngine\.Texture2D', re.IGNORECASE),
L76:                 'explanation': tr('game_log.diagnosis.missing_texture', '游戏找不到贴图资源，通常是资源路径写错、Mod 文件缺失或版本包不完整。')
L77:             },
L78:             {
L79:                 'id': 'null_reference_exception',
L80:                 'type': 'NullReference',
L81:                 'pattern': re.compile(r'NullReferenceException', re.IGNORECASE),
L82:                 'explanation': tr('game_log.diagnosis.null_reference_exception', '有代码访问了不存在的对象。单独看这类错误信息量有限，需要结合错误堆栈里的来源继续判断。')
L83:             },
L84:             {
L85:                 'id': 'type_load_exception',
L86:                 'type': 'AssemblyConflict',
L87:                 'pattern': re.compile(r'ReflectionTypeLoadException|MissingMethodException|TypeLoadException|MissingFieldException', re.IGNORECASE),
L88:                 'explanation': tr('game_log.diagnosis.type_load_exception', '程序集或方法字段不匹配，常见原因是 Mod 版本不兼容、依赖库版本冲突或游戏更新后旧 DLL 未适配。')
L89:             },
L90:             {
L91:                 'id': 'translation_data_error',
L92:                 'type': 'TranslationError',
L93:                 'pattern': re.compile(r'Translation data .*? has errors|translation data has errors', re.IGNORECASE),
L94:                 'explanation': tr('game_log.diagnosis.translation_data_error', '翻译文件存在格式或字段问题，通常不一定导致崩溃，但会影响文本显示。')
L95:             },
L96:             {
L97:                 'id': 'package_id_format',
L98:                 'type': 'PackageIdError',
L99:                 'pattern': re.compile(r'PackageId .*? is not in valid format', re.IGNORECASE),
L100:                 'explanation': tr('game_log.diagnosis.package_id_format', 'Mod 的 packageId 格式不规范，可能影响依赖识别、加载顺序和管理器匹配。')
L101:             },
L102:             {
L103:                 'id': 'patch_obsolete_method',
L104:                 'type': 'PatchWarning',
L105:                 'pattern': re.compile(r'patch(?:es)? on obsolete method|patched jump instruction', re.IGNORECASE),
L106:                 'explanation': tr('game_log.diagnosis.patch_obsolete_method', '有补丁作用在过时或不稳定的方法上，通常说明相关 Mod 需要更新。')
L107:             },
L108:         ]
L109:         # 常见错误特征匹配
L110:         self.rules = {
L111:             'XMLSyntaxError': re.compile(r'XML error|XML parsing error', re.IGNORECASE),
L112:             'DefConfigError': re.compile(r'Config error in', re.IGNORECASE),
L113:             'CrossRefError': re.compile(r'Could not resolve cross-reference|cross-reference|Could not resolve reference to object with loadID', re.IGNORECASE),
L114:             'AssemblyConflict': re.compile(r'ReflectionTypeLoadException|MissingMethodException|TypeLoadException', re.IGNORECASE),
L115:             'TickException': re.compile(r'Exception ticking', re.IGNORECASE),
```
## R18｜MIT 许可
来源：`LICENSE`；SHA-256：`3944bc4dac485e160dfdadbb27fad6c7b1ab6bad2bbf40cc784fbd1b8eff36e4`。
### L1–L21

```text
L1: MIT License
L2: 
L3: Copyright (c) 2026 RimCrow contributors
L4: 
L5: Permission is hereby granted, free of charge, to any person obtaining a copy
L6: of this software and associated documentation files (the "Software"), to deal
L7: in the Software without restriction, including without limitation the rights
L8: to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
L9: copies of the Software, and to permit persons to whom the Software is
L10: furnished to do so, subject to the following conditions:
L11: 
L12: The above copyright notice and this permission notice shall be included in all
L13: copies or substantial portions of the Software.
L14: 
L15: THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
L16: IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
L17: FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
L18: AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
L19: LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
L20: OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
L21: SOFTWARE.
```
## T1｜执行范围与原始测试输出

### ai_runtime.txt

```text

==================================== ERRORS ====================================
_____________ ERROR collecting tests/test_ai_assistant_runtime.py ______________
ImportError while importing test module '/mnt/data/audit/rimcrow/RimCrow/tests/test_ai_assistant_runtime.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_ai_assistant_runtime.py:59: in <module>
    import backend.ai.assistant_runtime as assistant_runtime_module
backend/ai/assistant_runtime.py:26: in <module>
    from backend.ai.def_actions import normalize_ai_actions
backend/ai/def_actions.py:10: in <module>
    from backend.ai.ai_contracts import (
backend/ai/ai_contracts.py:13: in <module>
    from backend.utils.tools import (
backend/utils/tools.py:14: in <module>
    from backend.paths.core import (
backend/paths/__init__.py:10: in <module>
    from .game_locations import (
backend/paths/game_locations.py:9: in <module>
    import vdf
E   ModuleNotFoundError: No module named 'vdf'
=========================== short test summary info ============================
ERROR tests/test_ai_assistant_runtime.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.16s

```
### compileall.txt

```text
backend/static_page.py:1315: SyntaxWarning: invalid escape sequence '\{'
  const formatMessage = (template, params = {{}}) => String(template || '').replace(/\{{(\w+)\}}/g, (_, key) => params[key] ?? '');
backend/static_page.py:1315: SyntaxWarning: invalid escape sequence '\}'
  const formatMessage = (template, params = {{}}) => String(template || '').replace(/\{{(\w+)\}}/g, (_, key) => params[key] ?? '');
backend/static_page.py:1394: SyntaxWarning: invalid escape sequence '\w'
  </html>"""

```
### frontend_mod_identity.txt

```text
命令无标准输出；退出码在 audit_summary.json 记录。
```
### 额外只读配置探测

```json
[
  {
    "file": "Loader.cfg",
    "standard_tomllib": "pass",
    "top_level_keys": 5
  },
  {
    "file": "MelonPreferences.cfg",
    "standard_tomllib": "reject",
    "error": "Invalid initial character for a key part (at line 28, column 2)"
  },
  {
    "file": "PSMarketRequests.cfg",
    "standard_tomllib": "pass",
    "top_level_keys": 26
  },
  {
    "file": "PSProcurement.cfg",
    "standard_tomllib": "pass",
    "top_level_keys": 34
  },
  {
    "file": "PSWallpaper.cfg",
    "standard_tomllib": "reject",
    "error": "Invalid value (at line 4, column 15)"
  }
]
```
标准解析器拒绝不等于游戏配置损坏，可能是方言或不同格式。
