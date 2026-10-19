# Probably Stolen 模组管理器：本地核验与源码证据

日期：2026-09-24。基于用户上传档案的静态审查；没有启动游戏或执行模组 DLL。

R = RimCrow 压缩包解出的项目根目录。G = Probably Stolen Demo 游戏根目录。所有行号按上传文件本地文本计数；网页可能折叠空行，因此网页显示行号可不同。日志中的个人安装路径已替换为 `<GAME_ROOT>`。

PE/CLI 元数据通过字节解析取得，不使用 Assembly.Load 或运行模组。源代码节选来自用户提供文件。


## E01 样本身份与检查范围

RimCrow HEAD：`a7e875112a175a914bb4706a82589d96d62adc70`。

`backend/_version.py` 声明 `0.24.7`，build 为 `dev`；HEAD 提交标题为 release: v0.24.7。工作树普通 diff 显示大量行尾差异，`git diff --ignore-space-at-eol --numstat` 没有内容差异输出。不能将此解释为已经验证发行安装包。

统计只计算 .py/.js/.vue，不含资源、翻译 JSON、依赖与空目录：
```json
{
  "backend": {
    "files": 111,
    "lines": 68101
  },
  "tests": {
    "files": 63,
    "lines": 21757
  },
  "frontend/src": {
    "files": 180,
    "lines": 65067
  },
  "test_files": 61,
  "test_functions": 853,
  "syntax_errors": [],
  "deps_available": {
    "pytest": true,
    "pydantic": true,
    "peewee": false,
    "keyring": false,
    "litellm": false,
    "lxml": true,
    "requests": true,
    "psutil": true
  }
}
```

档案哈希：
- RimCrow.7z：`2234c490cb07c62043796a309ee64e891601dae5f094822fb1a8e8584f00c67d`
- Probably Stolen Demo.7z：`dc4c2c446b25fe2503e75327289e0feab44c6bfa9f53f620dde12d993e947e75`



## E02 游戏与运行环境

**文件：`MelonLoader/Latest.log`**；SHA256：`1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a`

```text
00001 [14:15:09.533] [MelonLoader.Bootstrap] Using HostFXR Path: C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll
00002 [14:15:09.535] [MelonLoader.Bootstrap] Using .NET runtime: 'C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll'
00003 [14:15:09.544] [ERROR] [MelonLoader.Bootstrap] Failed to initialize a .NET domain
00004 [14:15:09.544] [MelonLoader.Bootstrap] Downloading the .NET Runtime Installer from: https://aka.ms/dotnet/6.0/dotnet-runtime-win-x64.exe
```
```text
00105 [14:15:11.789] [MelonLoader.Bootstrap] 100%
00106 [14:15:11.789] [MelonLoader.Bootstrap] Running the .NET Runtime Installer...
00107 [14:15:26.226] [MelonLoader.Bootstrap] Using HostFXR Path: C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll
00108 [14:15:26.226] [MelonLoader.Bootstrap] Using .NET runtime: 'C:\Program Files\dotnet\host\fxr\10.0.11\hostfxr.dll'
00109 
00110 [14:15:27.052] ------------------------------
00111 [14:15:27.053] MelonLoader v0.7.3 Open-Beta
00112 [14:15:27.054] OS: Windows 11
00113 [14:15:27.055] Hash Code: BDD43DC0F3893C208C95389B863AB61C967AE14204DA0DD8051647E754C8709B
00114 [14:15:27.055] ------------------------------
00115 [14:15:27.055] Game Type: Il2cpp
00116 [14:15:27.055] Game Arch: x64
00117 [14:15:27.055] ------------------------------
00118 [14:15:27.055] Command-Line: 
00119 [14:15:27.055] ------------------------------
00120 [14:15:27.055] Core::BasePath = <GAME_ROOT>
00121 [14:15:27.056] Game::BasePath = <GAME_ROOT>
00122 [14:15:27.056] Game::DataPath = <GAME_ROOT>\Probably Stolen_Data
00123 [14:15:27.056] Game::ApplicationPath = <GAME_ROOT>\Probably Stolen.exe
00124 [14:15:27.056] Runtime Type: net6
00125 [14:15:27.609] ------------------------------
00126 [14:15:27.609] Game Name: Probably Stolen
00127 [14:15:27.610] Game Developer: Questing Goose Studio
00128 [14:15:27.611] Unity Version: 2021.3.45f2
00129 [14:15:27.612] Game Version: 0.46D
00130 [14:15:27.612] ------------------------------
00131 
00132 [14:15:28.470] Preferences Loaded!
00133 
00134 [14:15:28.492] Loading UserLibs...
00135 [14:15:28.494] 0 UserLibs loaded.
00136 
00137 [14:15:28.494] Loading Plugins...
00138 [14:15:28.498] 0 Plugins loaded.
00139 
00140 [14:15:29.610] Loading Il2CppAssemblyGenerator...
00141 [14:15:29.638] [Il2CppAssemblyGenerator] Contacting RemoteAPI...
00142 [14:15:31.033] [Il2CppAssemblyGenerator] Game Not Found on RemoteAPI Host (https://api.melonloader.com/api/v1/game/probably-stolen)
```
**文件：`MelonLoader/net6/MelonLoader.runtimeconfig.json`**；SHA256：`d8c6bf06df9cfb54b0828b0986b53e50507fc23a6a52071051ff238eea28ecc4`

```text
00001 {
00002   "runtimeOptions": {
00003     "tfm": "net6.0",
00004     "rollForward": "LatestMinor",
00005     "framework": {
00006       "name": "Microsoft.NETCore.App",
00007       "version": "6.0.0"
00008     },
00009     "configProperties": {
00010       "System.Globalization.Invariant": true,
00011       "System.Globalization.PredefinedCulturesOnly": true,
00012       "System.Reflection.Metadata.MetadataUpdater.IsSupported": false
00013     }
00014   }
00015 }
```
**文件：`Probably Stolen_Data/app.info`**；SHA256：`21dca9b2e18762bacd225e45eaad29e8031ddfadbaa29954c00095a71516f8c3`

```text
00001 Questing Goose Studio
00002 Probably Stolen
```


## E03 45 个 DLL、43 个直接加载程序集的交叉核验

按 MelonLoader 官方 v0.7.3 `Melons/MelonPreprocessor.cs` 的规则复现：过滤无 MelonInfo 的文件；按 AssemblyName.Name 分组，保留最高 AssemblyVersion。此处没有用 MelonInfo 显示版本替代 AssemblyVersion。

```json
{
  "dll_count": 45,
  "melons_with_info": 44,
  "expected_preprocessor_count": 43,
  "logged_assembly_count": 43,
  "expected_matches_logged": true,
  "missing_in_log": [],
  "unexpected_in_log": [],
  "content_only": [
    "PSItemPack_SanmuShiwushechipinPack1.dll"
  ],
  "shadowed_under_0_7_3": [
    "RatColonyOptimizer.dll"
  ],
  "matching_sha256_count": 43
}
```

结果：预测文件集合与 Latest.log 的 43 个程序集集合完全相同，43 个日志 SHA256 与提交样本逐一相同。属于静态规则推演与历史日志交叉印证，不是本次 Windows 启动测试。
**文件：`MelonLoader/Latest.log`**；SHA256：`1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a`

```text
00399 [14:16:06.570] ------------------------------
00400 [14:16:07.980] Melon Assembly loaded: '.\Mods\3低概率2%.dll'
00401 [14:16:07.981] SHA256 Hash: '887F6B6E6332A6BE1A859BD6762F09C2FA4A8C92243E9B9EE44920048E8C9AA7'
00402 [14:16:08.011] Melon Assembly loaded: '.\Mods\AugPresenceGuard.dll'
00403 [14:16:08.011] SHA256 Hash: '5F0C47EBA2F6AD4A8624E09B3D6AE0292E2F95258A8F1728070F4BA7B981C731'
00404 [14:16:08.013] Melon Assembly loaded: '.\Mods\AutoBatterySwap.dll'
00405 [14:16:08.013] SHA256 Hash: 'D697C3B4A779A34C6C4F8CBD8D4C497B227B71E1EB7326AFE0844A2C495F6223'
00406 [14:16:08.026] Melon Assembly loaded: '.\Mods\BrewingExpansion.dll'
00407 [14:16:08.026] SHA256 Hash: 'CF5F2BF8C091AEE06E986D1C456D65AD0D632B14FE170DA559007589D718CDCE'
00408 [14:16:08.028] Melon Assembly loaded: '.\Mods\CharacterForgeRuntime.dll'
00409 [14:16:08.028] SHA256 Hash: '6BF67F845621EABE1AEC84C59EB48EF7E8565B5C294329F831F92AB82E938594'
00410 [14:16:08.029] Melon Assembly loaded: '.\Mods\ClickToAdvance.dll'
00411 [14:16:08.029] SHA256 Hash: 'BCF261776A6B0327725374B699BD2D31AC9235635D2F9933C0E67F77799DD9D4'
```
```text
00704 [14:16:10.375] 鼠布林&假酒供应商mod v2.0.0
00705 [14:16:10.375] by ModDesign
00706 [14:16:10.375] Assembly: WineAppraisalMaster.dll
00707 [14:16:10.375] ------------------------------
00708 [14:16:10.375] ------------------------------
00709 [14:16:10.376] 43 Mods loaded.
00710 
00711 [14:16:13.030] [WARNING] [Il2CppInterop] Class::Init signatures have been exhausted, using a substitute!
00712 [14:16:13.199] [Il2CppInterop] Registered mono type MelonLoader.Support.MonoEnumeratorWrapper in il2cpp domain
00713 [14:16:13.573] Support Module Loaded: <GAME_ROOT>\MelonLoader\Dependencies\SupportModules\Il2Cpp.dll
```
```text
00552 [14:16:09.786] [物品工坊] 已读取物品包：第七席与绯色来客 by 私人创作（12 个配置）
00553 [14:16:09.798] [物品工坊] 已读取物品包：gofishing by ALR（45 个配置）
00554 [14:16:09.799] [物品工坊] 已读取物品包：E1z的更多食物 by E1z（22 个配置）
00555 [14:16:09.900] [物品工坊] 已读取物品包：食物奢侈品拓展包 by 三木（21 个配置）
00556 [14:16:09.915] [物品工坊] 已向 Item Manager 自动同步 100 个检索条目（替换/清理 100 个）。
00557 [14:16:09.915] ------------------------------
00558 [14:16:09.915] ItemForge Runtime v1.2.3
00559 [14:16:09.915] by ALR
00560 [14:16:09.915] Assembly: ItemForgeRuntime.dll
```


## E04 重点 DLL 身份证据

```json
[
  {
    "file": "ItemForgeRuntime.dll",
    "sha256": "d52270822759d26f231ecc6cacec1ab74a1f0e2b3c80448d4cf20d3e8af5964f",
    "assembly_name": "ItemForgeRuntime",
    "assembly_version": "1.2.3.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "GeneratedItemMod.Core",
          "ItemForge Runtime",
          "1.2.3",
          "ALR",
          null
        ],
        "blob_hex": "01001547656e6572617465644974656d4d6f642e436f7265114974656d466f7267652052756e74696d6505312e322e3303414c52ff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "JCuratedVisitors.dll",
    "sha256": "585d9f84649a85db5ab1214029811251563f392ebec5bf63f7a7fd3305d8cdd0",
    "assembly_name": "JCuratedVisitors",
    "assembly_version": "1.0.9.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "CuratedVisitors.Core",
          "烛印会与失灯区",
          "1.0.9",
          "私人创作",
          null
        ],
        "blob_hex": "0100144375726174656456697369746f72732e436f726515e7839be58db0e4bc9ae4b88ee5a4b1e781afe58cba05312e302e390ce7a781e4babae5889be4bd9cff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "KitchenCrashTrace.dll",
    "sha256": "72f48dc697591d4c0241ca69efb4a9da32050dab2ea4fd3daed2300b7ff2d09f",
    "assembly_name": "KitchenCrashTrace",
    "assembly_version": "0.3.2.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "KitchenCrashTrace.Core",
          "Kitchen Crash Trace",
          "0.3.2",
          "Local diagnostics",
          null
        ],
        "blob_hex": "0100164b69746368656e437261736854726163652e436f7265134b69746368656e20437261736820547261636505302e332e32114c6f63616c20646961676e6f7374696373ff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      },
      {
        "type": "MelonLoader.MelonPriorityAttribute",
        "args": [
          -2147483648
        ],
        "blob_hex": "0100000000800000"
      }
    ]
  },
  {
    "file": "PSCharacterPack_CuratedVisitors.dll",
    "sha256": "6209b8f7c2aa761cf5cd3e86629abb83f38dfa5fe73d594137a8e987823c18e3",
    "assembly_name": "PSCharacterPack_CuratedVisitors",
    "assembly_version": "0.3.3.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "CuratedVisitorsCharacters.Core",
          "第七席与绯色来客·人物资源",
          "0.3.3",
          "私人创作",
          null
        ],
        "blob_hex": "01001e4375726174656456697369746f7273436861726163746572732e436f726526e7acace4b883e5b8ade4b88ee7bbafe889b2e69da5e5aea2c2b7e4babae789a9e8b584e6ba9005302e332e330ce7a781e4babae5889be4bd9cff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "PSItemPack_CuratedVisitors.dll",
    "sha256": "75fcb11415a0c939da0c47be1b123937299f7c8d50bf33840f08e0d94d04e126",
    "assembly_name": "PSItemPack_CuratedVisitors",
    "assembly_version": "0.3.3.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "CuratedVisitors.Core",
          "第七席与绯色来客",
          "0.3.3",
          "私人创作",
          null
        ],
        "blob_hex": "0100144375726174656456697369746f72732e436f726518e7acace4b883e5b8ade4b88ee7bbafe889b2e69da5e5aea205302e332e330ce7a781e4babae5889be4bd9cff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "PSItemPack_SanmuShiwushechipinPack1.dll",
    "sha256": "5b9990f1b78ffb3d913977efc33920eb3778dfdd07cf9668263be822ed982370",
    "assembly_name": "PSItemPack_SanmuShiwushechipinPack1",
    "assembly_version": "1.0.0.0",
    "melon_attributes": []
  },
  {
    "file": "PSPDA.dll",
    "sha256": "f7eb2a5ad22b9da1b48fb116c25bcf969b7f52caf776ef95cd4251ba93c79301",
    "assembly_name": "PSPDA",
    "assembly_version": "1.0.0.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "PSPDA.PspdaMod",
          "Terminal",
          "0.5.66",
          "梦曦",
          null
        ],
        "blob_hex": "01000e50535044412e50737064614d6f64085465726d696e616c06302e352e363606e6a2a6e69ba6ff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "RatColonyOptimizer.dll",
    "sha256": "d48dd8c870c296321ae1665c020ae0ab70de756e2d31d749cb138cf75346b008",
    "assembly_name": "RatGeneticRenamer",
    "assembly_version": "1.0.0.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "RatGeneticRenamer.Core",
          "Rat Colony Optimizer",
          "1.0.0",
          "Bloom",
          null
        ],
        "blob_hex": "01001652617447656e6574696352656e616d65722e436f72651452617420436f6c6f6e79204f7074696d697a657205312e302e3005426c6f6f6dff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "RatGeneticRenamer.dll",
    "sha256": "c0a8c71050368d47cc3c02304bed7163bc27c89415dfe2b3c9848144a2b43db3",
    "assembly_name": "RatGeneticRenamer",
    "assembly_version": "2.6.3.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "RatGeneticRenamer.Core",
          "Rat Genetic Renamer",
          "1.0.0",
          "DyrClone",
          null
        ],
        "blob_hex": "01001652617447656e6574696352656e616d65722e436f7265135261742047656e657469632052656e616d657205312e302e3008447972436c6f6e65ff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "WagesPerks.dll",
    "sha256": "90c37b898dd42078a2129d1a25b4b99554bc03a0d3a744dbfcd6459eefda05c4",
    "assembly_name": "JacksonPerks",
    "assembly_version": "1.0.0.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "JacksonPerks.Core",
          "Wage's Perks",
          "1.2.2-localfix1",
          "gwxxwg12332",
          null
        ],
        "blob_hex": "0100114a61636b736f6e5065726b732e436f72650c576167652773205065726b730f312e322e322d6c6f63616c666978310b6777787877673132333332ff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "XIAOWO-WRDUnlock.dll",
    "sha256": "9a43c9838d591a05c17ca0025c4b4d943c2f1ba6d1e9aba053b3d8ac39257bdc",
    "assembly_name": "NetworkUnlockEffective",
    "assembly_version": "1.0.7.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "NetworkUnlockEffective.Core",
          "XIAOWO-WRDUnlock",
          "1.0.7",
          "遗忘的小窝",
          null
        ],
        "blob_hex": "01001b4e6574776f726b556e6c6f636b4566666563746976652e436f7265105849414f574f2d575244556e6c6f636b05312e302e370fe98197e5bf98e79a84e5b08fe7aa9dff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      }
    ]
  },
  {
    "file": "XIAOWOTradePerks.dll",
    "sha256": "0b9f190c67d4c14976d4a7f4c8bc03c1c66449ce92fcf9cee1fce70f38280547",
    "assembly_name": "XIAOWOTradePerks",
    "assembly_version": "1.8.2.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "XIAOWOTradePerks.Core",
          "XIAOWO-TradePerks",
          "1.8.2",
          "遗忘的小窝",
          null
        ],
        "blob_hex": "0100155849414f574f54726164655065726b732e436f7265115849414f574f2d54726164655065726b7305312e382e320fe98197e5bf98e79a84e5b08fe7aa9dff0000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          "Questing Goose Studio",
          "Probably Stolen"
        ],
        "blob_hex": "0100155175657374696e6720476f6f73652053747564696f0f50726f6261626c792053746f6c656e0000"
      },
      {
        "type": "MelonLoader.MelonPriorityAttribute",
        "args": [
          -2147483648
        ],
        "blob_hex": "0100000000800000"
      }
    ]
  },
  {
    "file": "moreUpdate.dll",
    "sha256": "49189ca42c3f573e334d7a8c8efbaabf69650bd5fbe53ed81419aadd9b87db9c",
    "assembly_name": "MoreDeviceUpgrades",
    "assembly_version": "1.0.0.0",
    "melon_attributes": [
      {
        "type": "MelonLoader.MelonInfoAttribute",
        "args": [
          "MoreDeviceUpgrades.MoreDeviceUpgrades",
          "More Device Upgrades",
          "0.2.0-localfix1",
          "mod-dev",
          ""
        ],
        "blob_hex": "0100254d6f726544657669636555706772616465732e4d6f72654465766963655570677261646573144d6f7265204465766963652055706772616465730f302e322e302d6c6f63616c66697831076d6f642d646576000000"
      },
      {
        "type": "MelonLoader.MelonGameAttribute",
        "args": [
          null,
          null
        ],
        "blob_hex": "0100ffff0000"
      }
    ]
  }
]
```


## E05 资源包与版本诊断字符串

下列字符串来自嵌入资源名或 #US 字符串堆；静态字符串不能单独证明某分支已经执行，需结合 E03/E06 日志。

### ItemForgeRuntime.dll
```json
{
  "resources": [
    {
      "name": "manifest.xml",
      "offset": 0,
      "external": false
    }
  ],
  "relevant_user_strings": [
    "PSItemPack_*.dll",
    "ItemForge.items.json",
    "[物品工坊] Runtime 已启动，但没有找到兼容的 PSItemPack_*.dll 物品包。"
  ]
}
```

### JCuratedVisitors.dll
```json
{
  "resources": [
    {
      "name": "JCuratedVisitors.crises.json",
      "offset": 0,
      "external": false
    },
    {
      "name": "JCuratedVisitors.marriage.json",
      "offset": 1792,
      "external": false
    },
    {
      "name": "JCuratedVisitors.consumables.json",
      "offset": 14904,
      "external": false
    },
    {
      "name": "JCuratedVisitors.valuables.json",
      "offset": 17720,
      "external": false
    },
    {
      "name": "JCuratedVisitors.special-market.json",
      "offset": 23296,
      "external": false
    },
    {
      "name": "JCuratedVisitors.commissions.json",
      "offset": 26840,
      "external": false
    },
    {
      "name": "JCuratedVisitors.skills.json",
      "offset": 35464,
      "external": false
    },
    {
      "name": "JCuratedVisitors.wine.json",
      "offset": 38368,
      "external": false
    }
  ],
  "relevant_user_strings": [
    "1.0需要新存档；检测到此前测试版数据，未迁移或覆盖。",
    "此存档由更高版本创建，请更新模组；本版本拒绝写入。",
    "1.0需要新存档；已保留此前版本数据，请返回主菜单新开档。",
    "烛印会与失灯区 1.0.9：支持1.0存档继续游玩；首次从旧开发版升级请新建存档。F7 打开冒险日志。",
    "检测到本MOD旧版两个DLL。请退出后移走旧版，再用新存档启动0.5.0；其他作者MOD保持原样。"
  ]
}
```

### PSItemPack_SanmuShiwushechipinPack1.dll
```json
{
  "resources": [
    {
      "name": "ItemForge.items.json",
      "offset": 0,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_01.png",
      "offset": 38616,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_02.png",
      "offset": 41288,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_03.png",
      "offset": 43656,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_04.png",
      "offset": 46376,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_05.png",
      "offset": 48392,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_06.png",
      "offset": 55368,
      "external": false
    },
    {
      "name": "ItemForge.icons.f_kz_07.png",
      "offset": 57728,
      "external": false
    }
  ],
  "relevant_user_strings": []
}
```

### PSPDA.dll
```json
{
  "resources": [
    {
      "name": "PSPDA.art.mxi_phone.png",
      "offset": 0,
      "external": false
    },
    {
      "name": "PSPDA.art.mxi_phone_alert.png",
      "offset": 832,
      "external": false
    },
    {
      "name": "PSPDA.art.wallpaper.png",
      "offset": 1648,
      "external": false
    },
    {
      "name": "PSPDA.art.frame.png",
      "offset": 185944,
      "external": false
    },
    {
      "name": "PSPDA.art.round150.png",
      "offset": 203832,
      "external": false
    },
    {
      "name": "PSPDA.art.round74.png",
      "offset": 205840,
      "external": false
    },
    {
      "name": "PSPDA.art.round88.png",
      "offset": 206616,
      "external": false
    },
    {
      "name": "PSPDA.art.round190.png",
      "offset": 207552,
      "external": false
    }
  ],
  "relevant_user_strings": [
    "[设置页] 后门三键在 UserData\\MelonPreferences.cfg 的 `PSPDA` 类目里（MelonLoader 可能写成 `PSPDA = { … }` 内联、也可能写成 `[PSPDA]` 段，两种都认）｜值是启动时读进内存的 ⇒ **要关掉游戏改文件、改完重启游戏**才生效（开着改会被覆盖）",
    "当前筛选下没有可显示的设置项。\n\n（检查 UserData\\MelonPreferences.cfg 里 `PSPDA` 类目的 SettingsMode / SettingsShowKeys / SettingsHideKeys；★ 改完要重启游戏）"
  ]
}
```


## E06 真实自停用、兼容性提示和历史阶段

**文件：`MelonLoader/Latest.log`**；SHA256：`1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a`

```text
01009 [14:16:45.205] [XIAOWO-TradePerks] Registered perk: 阔客磁场
01010 [14:16:45.628] [Kitchen_Crash_Trace] 饮料机诊断已就绪，模式=observe；记录到 UserData/KitchenCrashTrace.log。
01011 [14:16:45.739] [AutoBatterySwap] patches: 7
01012 [14:16:46.204] [NestedStorage] patches: 13
01013 [14:16:46.277] [WaterScanTooltip] patches: 5
01014 [14:16:46.279] [XIAOWO-PrecisionBayExpansion] INACTIVE: XIAOWO-TradePerks detected; expansion remains entirely controlled by the main mod.
01015 [14:16:46.822] [烛印会与失灯区] [烛印会] 来访参数=正式；战利品概率独立保留，无比例兑换。
01016 [14:16:46.824] [ERROR] [烛印会与失灯区] [第七席] 本次运行已停用本 MOD，未安装游戏补丁。检测到本MOD旧版两个DLL。请退出后移走旧版，再用新存档启动0.5.0；其他作者MOD保持原样。
01017 [14:16:54.099] [第七席与绯色来客] 前置与内容包检查通过，游戏补丁已启用。启动诊断已完成。
01018 [14:16:54.269] [XIAOWO-TradePerks] Community panel attached to the main menu UI layer.
01019 [14:16:54.270] [XIAOWO-TradePerks] Original main-menu modding button enabled for MelonLoader mod management.
01020 [14:16:54.298] [PSWallpaper] [PSWallpaper] [壁纸] ★ 已把「壁纸」注册进 PSPDA 手机（id=g_wall，第 1 次尝试成功；主屏图标要等壳把 g_wall 加进主屏清单才显示）
01021 [14:16:54.299] [PSWallpaper] [PSWallpaper] [壁纸] 注册状态：未查询｜目录 <GAME_ROOT>\UserData\wallpapers｜候选 1 项（含内置）｜当前 ok｜亮度 55｜适配 cover
01022 [14:16:54.395] [PSWallpaper] [PSWallpaper] [壁纸] ★ 已把壁纸提供方注册进 PSPDA（`PSPDA.PhoneWallpaper.Register`，第 1 次尝试成功，providerType=System.Func`3[[System.Int32, System.Private.CoreLib, Version=6.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e],[System.Int32, System.Private.CoreLib, Version=6.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e],[System.Object, System.Private.CoreLib, Version=6.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e]][System.Int32,System.Int32,System.Object]（程序集 System.Private.CoreLib））—— spriteProvider(W,H) 会在壳每次渲染面板时被调用
01023 [14:16:54.400] [PSWarehouse] [14:16:54.399] [仓储] ★ 已把「仓储」注册进 PSPDA 手机（id=g_ware，第 1 次尝试成功，用 8 参（含按钮回调 ＋ 红点自判 ＋ **物品图标回调** ＋ ★**重画请求**））
01024 [14:16:54.400] [PSWarehouse] [14:16:54.400] [仓储] ★★ 第 8 参**重画请求**已接上 ⇒ 那句发送结果提示（`@sub=`）会**自己消失**（≈2500 ms 后我们回一次 true，壳下一帧重画）
01025 [14:16:54.418] [XIAOWO-PrecisionBayExpansion] Main-menu expansion title visible; shared promotion=True
01026 [14:16:57.179] [XIAOWO-TradePerks] MOD 兼容总线已自动去重 62 个完全相同的 Harmony 补丁，来源：CharacterForgeRuntime, DialogueSpeed, ItemForgeRuntime, NetworkUnlockEffective, PSWarehouse, WaterTower。
01027 [14:16:59.451] [XIAOWO-TradePerks] MOD 兼容性检测：共享方法=46，潜在冲突=21，确认冲突=0，停用模块=0；brokerAliases=0, itemConflicts=0, unresolvedItems=0, deduplicatedPatches=62, dedupeFailures=0, loadedMods=43。
01028 [14:16:59.452] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 DirectoryMaster.Item 可能被 MOD JacksonPerks, XIAOWOKitchenBusiness 的前置补丁跳过原方法
01029 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.CanTarget 可能被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 的前置补丁跳过原方法
01030 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.CanTarget 的返回值也会被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 修改
01031 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.GetNegociatedValue 的返回值也会被 MOD JacksonPerks, PSItemPack_CuratedVisitors, XIAOWOKitchenBusiness 修改
01032 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.MayTarget 可能被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 的前置补丁跳过原方法
01033 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.MayTarget 的返回值也会被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 修改
01034 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 GameItem.Target 可能被 MOD ItemForgeRuntime, JacksonPerks, PSItemPack_CuratedVisitors, WineAppraisalMaster 的前置补丁跳过原方法
01035 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 ItemFeature.CreateAndAddFeatureFromEvent 可能被 MOD WineAppraisalMaster 的前置补丁跳过原方法
01036 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 ItemFeature.GetActualDisplay 的返回值也会被 MOD WineAppraisalMaster 修改
01037 [14:16:59.539] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 ItemFeature.GetPublicDisplay 的返回值也会被 MOD WineAppraisalMaster 修改
01038 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 PlayerStore.BuyItem 可能被 MOD alr.fishing-cuisine-expansion 的前置补丁跳过原方法
01039 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 PlayerStore.ExecuteGameOver 可能被 MOD JacksonPerks 的前置补丁跳过原方法
01040 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 PlayerStore.SellItem 可能被 MOD PSItemPack_CuratedVisitors, alr.fishing-cuisine-expansion 的前置补丁跳过原方法
01041 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StartingPerk.GetLocalizedDescription 的返回值也会被 MOD WineAppraisalMaster 修改
01042 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StartingPerk.GetLocalizedDisplayName 的返回值也会被 MOD WineAppraisalMaster 修改
01043 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StoreClientManager.CheckRentDay 可能被 MOD JacksonPerks 的前置补丁跳过原方法
01044 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StoreClientManager.HandleInspectionClient 可能被 MOD JacksonPerks 的前置补丁跳过原方法
01045 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 __c._LandlordWholesale_b__22_0 可能被 MOD JacksonPerks 的前置补丁跳过原方法
01046 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StoreEvent.GetDisplayName 的返回值也会被 MOD WineAppraisalMaster 修改
01047 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StoreEvent.GetEventPriceEffect 的返回值也会被 MOD WineAppraisalMaster 修改
01048 [14:16:59.540] [WARNING] [XIAOWO-TradePerks] 可能存在 MOD 冲突：目标方法 StoreEvent.IsItemAffectedByThisEvent 的返回值也会被 MOD WineAppraisalMaster 修改
01049 [14:19:44.360] [WARNING] [ClickToAdvance] DialogUIManager.Instance 为 null
```
**文件：`UserData/CuratedVisitors.StartupTrace-20260924-141646-280-29160-ded15b696bf34d53bd43b2c02d80c9d6.log`**；SHA256：`2c862f0ee85f33f353666d232425e1a9fea3b5f94b25d7543fc77a0c1c6afc42`

```text
00001 00001 2026-09-24T14:16:46.3466123+08:00 SESSION START 0.5.0; fresh save; independent ledger, equipment, bosses and story; no workshop dependency
00002 00002 2026-09-24T14:16:46.3589579+08:00 ENV .NET 6.0.36; Microsoft Windows 10.0.26200; X64
00003 00003 2026-09-24T14:16:46.3625498+08:00 ASSEMBLY KitchenCrashTrace, Version=0.3.2.0, Culture=neutral, PublicKeyToken=null; MVID=e6741b42-9c58-4e22-8b0f-d8685d638134
00004 00004 2026-09-24T14:16:46.3657117+08:00 SHA256 KitchenCrashTrace 72F48DC697591D4C0241CA69EFB4A9DA32050DAB2EA4FD3DAED2300B7FF2D09F
00005 00005 2026-09-24T14:16:46.3686452+08:00 ASSEMBLY XIAOWOTradePerks, Version=1.8.2.0, Culture=neutral, PublicKeyToken=null; MVID=51e00ce0-f58c-4ebf-96a1-fa4ea68bf38c
```


## E07 配置与副文件

**文件：`UserData/Loader.cfg`**；SHA256：`0b894bc615b1122d2b2d33462046e3cabf3ad7ede16cd85ce39e93620b8e3717`

```text
00001 [loader]
00002 # Disables MelonLoader. Equivalent to the '--no-mods' launch option
00003 disable = false
00004 # Equivalent to the '--melonloader.debug' launch option
00005 debug_mode = false
00006 # Capture all Unity player logs into MelonLoader's logs even if the game disabled them. NOTE: Depending on the game or Unity version, these logs can be overly verbose. Equivalent to the '--melonloader.captureplayerlogs' launch option
00007 capture_player_logs = false
00008 # The maximum Harmony log verbosity to capture into MelonLoader's logs. Possible values in verbosity order are: "None", "Error", "Warn", "Info", "Debug", or "IL". Equivalent to the '--melonloader.harmonyloglevel' launch option
00009 harmony_log_level = "Warn"
00010 # Only use this if the game freezes when trying to quit. Equivalent to the '--quitfix' launch option
00011 force_quit = false
00012 # Disables the start screen. Equivalent to the '--melonloader.disablestartscreen' launch option
00013 disable_start_screen = false
00014 # Starts the dotnet debugger on Windows and wait it is attached or just wait until one is attached without launch on other OSes (only for Il2Cpp games). Equivalent to the '--melonloader.launchdebugger' launch option
00015 launch_debugger = false
00016 # Sets the loader theme. Currently, the only available themes are "Normal" and "Lemon". Equivalent to the '--melonloader.consolemode' launch option (0 for Normal, 4 for Lemon)
00017 theme = "Normal"
00018 # Disables the loading of Melon Subfolders. Equivalent to the '--melonloader.nosfload' launch option
00019 disable_subfolder_load = false
00020 # Disables the requirement of needing a manifest json inside a Melon Subfolder for it to be loaded. Equivalent to the '--melonloader.nosfmanifest' launch option
00021 disable_subfolder_manifest = false
00022 # Manually defines the HostFXR path to use for DotNet Initialization. Equivalent to the '--melonloader.hostfxr' launch option
00023 hostfxr_path_override = ""
```
```text
00035 [logs]
00036 # Sets the maximum amount of log files in the Logs folder (Default: 10). Equivalent to the '--melonloader.maxlogs' launch option
00037 max_logs = 10
```

识别到的 UserData 文件和 Mods 非 DLL 文件（只列路径、不包含图片或存档内容）：
```text
Mods/item_mod_config.json
Mods/item_catalog.json
UserData/PSProcurement.log
UserData/PSPDA.log
UserData/PSWallpaper.log
UserData/KitchenCrashTrace.log
UserData/PSProcurement.cfg
UserData/PSMarketRequests.cfg
UserData/CuratedVisitors.StartupTrace-20260924-141646-280-29160-ded15b696bf34d53bd43b2c02d80c9d6.log
UserData/PSWarehouse.log
UserData/Loader.cfg
UserData/PSWallpaper.cfg
UserData/PSMarketRequests.log
UserData/MelonPreferences.cfg
UserData/CuratedVisitors.StartupTrace-20260924-141639-933-29160-ee61a20e09a6451ea7db5976249b5456.log
UserData/MoreUpdateDiagnostics/MoreUpdate-20260924-141631-073-29160-e56b3ede024f4104ad13f6bc8f7717bd.log
Mods/BrewingExpansion/brewing_data_9.txt
Mods/BrewingExpansion/brewing_data.txt
Mods/BrewingExpansion/brewing_data_11.txt
Mods/MoreStorageColors/Reference/f_kz_03.png
Mods/MoreStorageColors/Reference/guide_indicator_red.png
Mods/MoreStorageColors/Reference/bm.png
Mods/MoreStorageColors/Reference/med_bottle_violet.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_mini.png
Mods/MoreStorageColors/Reference/red_serum_s.png
Mods/MoreStorageColors/Reference/checkbox_orange.png
Mods/MoreStorageColors/Reference/pouch_injector_large.png
Mods/MoreStorageColors/Reference/25ml_red_50.png
Mods/MoreStorageColors/Reference/rat_s_m_d.png
Mods/MoreStorageColors/Reference/animal_cage.png
Mods/MoreStorageColors/Reference/cropped-color-saturation.png
Mods/MoreStorageColors/Reference/red_bar.png
Mods/MoreStorageColors/Reference/small_red_10.png
Mods/MoreStorageColors/Reference/dropdown_button_2.png
Mods/MoreStorageColors/Reference/paper_buttons_l_3.png
Mods/MoreStorageColors/Reference/gf_blazing_chili_pepper.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool_spectral_analyser.png
Mods/MoreStorageColors/Reference/m16_receiver.png
Mods/MoreStorageColors/Reference/stanag_20.png
Mods/MoreStorageColors/Reference/paper_buttons_l_4.png
Mods/MoreStorageColors/Reference/fridge.png
Mods/MoreStorageColors/Reference/example_seal.png
Mods/MoreStorageColors/Reference/injector_purple_large.png
Mods/MoreStorageColors/Reference/dropdown_box.png
Mods/MoreStorageColors/Reference/hand_steel.png
Mods/MoreStorageColors/Reference/default_d.png
Mods/MoreStorageColors/Reference/small_water_bottle.png
Mods/MoreStorageColors/Reference/desk_animations2_1.png
Mods/MoreStorageColors/Reference/peanut_butter_green.png
Mods/MoreStorageColors/Reference/down_hl.png
Mods/MoreStorageColors/Reference/berry_5small.png
Mods/MoreStorageColors/Reference/border_outside_top_rounded.png
Mods/MoreStorageColors/Reference/networkIcons_8.png
Mods/MoreStorageColors/Reference/grid_black.png
Mods/MoreStorageColors/Reference/raw_xeno_meat.png
Mods/MoreStorageColors/Reference/screen_dark.png
Mods/MoreStorageColors/Reference/dropdown_panel.png
Mods/MoreStorageColors/Reference/beis_icecream.png
Mods/MoreStorageColors/Reference/push_button_frame.png
Mods/MoreStorageColors/Reference/sec_p.png
Mods/MoreStorageColors/Reference/improvised_stunprod_unloaded.png
Mods/MoreStorageColors/Reference/skinning_knife.png
Mods/MoreStorageColors/Reference/xl_dark_10.png
Mods/MoreStorageColors/Reference/tutorial_bubble_x2.png
Mods/MoreStorageColors/Reference/aug_id_guide.png
Mods/MoreStorageColors/Reference/clientele-Sheet_1.png
Mods/MoreStorageColors/Reference/100ml_hl_75.png
Mods/MoreStorageColors/Reference/letter-incorrect4.png
Mods/MoreStorageColors/Reference/heavy_pistol_ammo.png
Mods/MoreStorageColors/Reference/shotgun_3.png
Mods/MoreStorageColors/Reference/paper_buttons_screen_3.png
Mods/MoreStorageColors/Reference/sbattery0.png
Mods/MoreStorageColors/Reference/25ml_red_90.png
Mods/MoreStorageColors/Reference/gf_fishing_net.png
Mods/MoreStorageColors/Reference/water_on.png
Mods/MoreStorageColors/Reference/play_hl.png
Mods/MoreStorageColors/Reference/checkbox_disable.png
Mods/MoreStorageColors/Reference/fillerweed_seed.png
Mods/MoreStorageColors/Reference/energy_drink.png
Mods/MoreStorageColors/Reference/container_tag_2x.png
Mods/MoreStorageColors/Reference/aug_chip.png
Mods/MoreStorageColors/Reference/xiaowo_ratcolony_growth_mutagen.png
Mods/MoreStorageColors/Reference/mouse_icon_left.png
Mods/MoreStorageColors/Reference/steel_sheet_rough_large.png
Mods/MoreStorageColors/Reference/water_ration_small.png
Mods/MoreStorageColors/Reference/printer_plastic.png
Mods/MoreStorageColors/Reference/bottle_printer.png
Mods/MoreStorageColors/Reference/gf_spicy_pickled_fish.png
Mods/MoreStorageColors/Reference/screen_shadow.png
Mods/MoreStorageColors/Reference/mini_red_100.png
Mods/MoreStorageColors/Reference/postit_blue.png
Mods/MoreStorageColors/Reference/grid_green.png
Mods/MoreStorageColors/Reference/book4.png
Mods/MoreStorageColors/Reference/peanut_butter_red.png
Mods/MoreStorageColors/Reference/labeler.png
Mods/MoreStorageColors/Reference/normal_blue_100.png
Mods/MoreStorageColors/Reference/hot_sauce.png
Mods/MoreStorageColors/Reference/handmade_pistol.png
Mods/MoreStorageColors/Reference/letter-chinese.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_blender_b.png
Mods/MoreStorageColors/Reference/farBandana_3.png
Mods/MoreStorageColors/Reference/small_blue_50.png
Mods/MoreStorageColors/Reference/civilian_hull.png
Mods/MoreStorageColors/Reference/UIMask.png
Mods/MoreStorageColors/Reference/dossier_1.png
Mods/MoreStorageColors/Reference/networkIcons_15.png
Mods/MoreStorageColors/Reference/turbo_booster_adv_ready.png
Mods/MoreStorageColors/Reference/mining_system.png
Mods/MoreStorageColors/Reference/mini_red_10.png
Mods/MoreStorageColors/Reference/aug_scanner.png
Mods/MoreStorageColors/Reference/medical_pouch.png
Mods/MoreStorageColors/Reference/hydroponic_system.png
Mods/MoreStorageColors/Reference/calendar_top-Sheet_1.png
Mods/MoreStorageColors/Reference/jar_pink.png
Mods/MoreStorageColors/Reference/enemy.png
Mods/MoreStorageColors/Reference/gf_rainbow_fish.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_0.png
Mods/MoreStorageColors/Reference/farm_module_l5.png
Mods/MoreStorageColors/Reference/midPuncho_2.png
Mods/MoreStorageColors/Reference/wrench.png
Mods/MoreStorageColors/Reference/50ml_hl_90.png
Mods/MoreStorageColors/Reference/module_red_2.png
Mods/MoreStorageColors/Reference/networkIcons_22.png
Mods/MoreStorageColors/Reference/simple_backpack_modular.png
Mods/MoreStorageColors/Reference/50ml_dark_90.png
Mods/MoreStorageColors/Reference/shot_animation_wound_1.png
Mods/MoreStorageColors/Reference/100ml_red_100.png
Mods/MoreStorageColors/Reference/small_hl_90.png
Mods/MoreStorageColors/Reference/items_food_juice.png
Mods/MoreStorageColors/Reference/model_citizen.png
Mods/MoreStorageColors/Reference/handnote.png
Mods/MoreStorageColors/Reference/cv_bobblehead.png
Mods/MoreStorageColors/Reference/xl_blue_90.png
Mods/MoreStorageColors/Reference/rat_xl_f.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_5ml.png
Mods/MoreStorageColors/Reference/network_button_hover.png
Mods/MoreStorageColors/Reference/gf_drying_rack.png
Mods/MoreStorageColors/Reference/healing_bandage.png
Mods/MoreStorageColors/Reference/xl.png
Mods/MoreStorageColors/Reference/outside_top.png
Mods/MoreStorageColors/Reference/module_red_2_h.png
Mods/MoreStorageColors/Reference/dossier_0.png
Mods/MoreStorageColors/Reference/fce_dish_poached_fillet.png
Mods/MoreStorageColors/Reference/node_m.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_nightmare_dust.png
Mods/MoreStorageColors/Reference/peanut_butter.png
Mods/MoreStorageColors/Reference/farm_module_l6.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_100ml.png
Mods/MoreStorageColors/Reference/battery_ext20.png
Mods/MoreStorageColors/Reference/midTrechCoat_1.png
Mods/MoreStorageColors/Reference/fermentation_airlock.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_ship_storage_bay_chem.png
Mods/MoreStorageColors/Reference/cv_kun_doll.png
Mods/MoreStorageColors/Reference/nutrifruit_seed_packet_old.png
Mods/MoreStorageColors/Reference/pitcher_red_75.png
Mods/MoreStorageColors/Reference/background_fill.png
Mods/MoreStorageColors/Reference/nutrient_tablet_container.png
Mods/MoreStorageColors/Reference/nightly_tab_reversed.png
Mods/MoreStorageColors/Reference/berry_sign.png
Mods/MoreStorageColors/Reference/button_main_menu_h.png
Mods/MoreStorageColors/Reference/medical_system.png
Mods/MoreStorageColors/Reference/faucet.png
Mods/MoreStorageColors/Reference/delete_save_button_hl.png
Mods/MoreStorageColors/Reference/heavy_handmade_pistol_s.png
Mods/MoreStorageColors/Reference/25ml_blue_100.png
Mods/MoreStorageColors/Reference/button_rewind_normal.png
Mods/MoreStorageColors/Reference/hydroponic_off.png
Mods/MoreStorageColors/Reference/cost_area_bought.png
Mods/MoreStorageColors/Reference/heating_plate_b.png
Mods/MoreStorageColors/Reference/desequencer_r.png
Mods/MoreStorageColors/Reference/injector_purple.png
Mods/MoreStorageColors/Reference/smuggler_bay_mini.png
Mods/MoreStorageColors/Reference/normal_dark_50.png
Mods/MoreStorageColors/Reference/e1z_bagsinstantnoodles_fastfood.png
Mods/MoreStorageColors/Reference/powerblock.png
Mods/MoreStorageColors/Reference/f_kz_10.png
Mods/MoreStorageColors/Reference/pouch_general_large.png
Mods/MoreStorageColors/Reference/small_blue_90.png
Mods/MoreStorageColors/Reference/uiback1LTan.png
Mods/MoreStorageColors/Reference/wine_bottom.png
Mods/MoreStorageColors/Reference/recharger2.png
Mods/MoreStorageColors/Reference/fce_dish_oil_croaker.png
Mods/MoreStorageColors/Reference/dream_cap_s.png
Mods/MoreStorageColors/Reference/zerochew.png
Mods/MoreStorageColors/Reference/desk_animations4_8.png
Mods/MoreStorageColors/Reference/50ml_red_90.png
Mods/MoreStorageColors/Reference/pitcher_dark_50.png
Mods/MoreStorageColors/Reference/letter-incorrect5.png
Mods/MoreStorageColors/Reference/hydroreed_filter.png
Mods/MoreStorageColors/Reference/secret_admirer.png
Mods/MoreStorageColors/Reference/previous_hl.png
Mods/MoreStorageColors/Reference/hand_gold.png
Mods/MoreStorageColors/Reference/smuggler_bay_large.png
Mods/MoreStorageColors/Reference/outside_left.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_fanta_precursor.png
Mods/MoreStorageColors/Reference/100ml_hl_25.png
Mods/MoreStorageColors/Reference/start.png
Mods/MoreStorageColors/Reference/dark_red.png
Mods/MoreStorageColors/Reference/client_shadow_small.png
Mods/MoreStorageColors/Reference/25ml_dark_90.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_kotton_fiber_p.png
Mods/MoreStorageColors/Reference/items_weapons_ranged_antique_2.png
Mods/MoreStorageColors/Reference/xl_hl_10.png
Mods/MoreStorageColors/Reference/gf_glass_fish.png
Mods/MoreStorageColors/Reference/mini_hl_10.png
Mods/MoreStorageColors/Reference/farm_module5.png
Mods/MoreStorageColors/Reference/e1z_sickers_quickfood.png
Mods/MoreStorageColors/Reference/water_filter_adv.png
Mods/MoreStorageColors/Reference/mini_hl_50.png
Mods/MoreStorageColors/Reference/paper_buttons2.png
Mods/MoreStorageColors/Reference/gf_purple_fillet.png
Mods/MoreStorageColors/Reference/stun_baton.png
Mods/MoreStorageColors/Reference/letter_correct.png
Mods/MoreStorageColors/Reference/convict.png
Mods/MoreStorageColors/Reference/peanut_butter_gray.png
Mods/MoreStorageColors/Reference/notice_box.png
Mods/MoreStorageColors/Reference/bullet_nato_556.png
Mods/MoreStorageColors/Reference/kotton_plant.png
Mods/MoreStorageColors/Reference/inspection_receipt.png
Mods/MoreStorageColors/Reference/small_red_25.png
Mods/MoreStorageColors/Reference/bell_button_sheet_1.png
Mods/MoreStorageColors/Reference/slip_2.png
Mods/MoreStorageColors/Reference/info.png
Mods/MoreStorageColors/Reference/nutrifruit_seed_packet.png
Mods/MoreStorageColors/Reference/gf_dried_purifier_fish.png
Mods/MoreStorageColors/Reference/farBandana_1.png
Mods/MoreStorageColors/Reference/prime_real_estate.png
Mods/MoreStorageColors/Reference/networkIcons_4.png
Mods/MoreStorageColors/Reference/wrench_orange.png
Mods/MoreStorageColors/Reference/button_up_red.png
Mods/MoreStorageColors/Reference/hydroreed_p.png
Mods/MoreStorageColors/Reference/cv_musicbox.png
Mods/MoreStorageColors/Reference/bubble_sci_p.png
Mods/MoreStorageColors/Reference/li_eat_snackbar_unpack.png
Mods/MoreStorageColors/Reference/injector_white.png
Mods/MoreStorageColors/Reference/WAM_RatlinGeneSerumSprite.png
Mods/MoreStorageColors/Reference/strip1.png
Mods/MoreStorageColors/Reference/e1z_coconutdrink_candrink.png
Mods/MoreStorageColors/Reference/next_c.png
Mods/MoreStorageColors/Reference/short_sleeper.png
Mods/MoreStorageColors/Reference/textbox.png
Mods/MoreStorageColors/Reference/cv_weather.png
Mods/MoreStorageColors/Reference/screwdriver_s.png
Mods/MoreStorageColors/Reference/100ml_red_90.png
Mods/MoreStorageColors/Reference/gen_button_2x_n.png
Mods/MoreStorageColors/Reference/example_letter3.png
Mods/MoreStorageColors/Reference/play.png
Mods/MoreStorageColors/Reference/vertical_scroll_bar_handle_2x.png
Mods/MoreStorageColors/Reference/normal_hl_90.png
Mods/MoreStorageColors/Reference/up.png
Mods/MoreStorageColors/Reference/water_tablet.png
Mods/MoreStorageColors/Reference/large_blue_90.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_fillerweed_p.png
Mods/MoreStorageColors/Reference/small_dark_50.png
Mods/MoreStorageColors/Reference/plant_analyser.png
Mods/MoreStorageColors/Reference/mini_red_75.png
Mods/MoreStorageColors/Reference/battery60.png
Mods/MoreStorageColors/Reference/ammo_box_packed.png
Mods/MoreStorageColors/Reference/soap.png
Mods/MoreStorageColors/Reference/first_aid_box.png
Mods/MoreStorageColors/Reference/logo_incorrect3.png
Mods/MoreStorageColors/Reference/icon_contraceptive.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool_chemforge.png
Mods/MoreStorageColors/Reference/beer_case_5.png
Mods/MoreStorageColors/Reference/fireaxe_big.png
Mods/MoreStorageColors/Reference/light_pistol_ammo.png
Mods/MoreStorageColors/Reference/turbo_booster_adv.png
Mods/MoreStorageColors/Reference/injector_blood_red.png
Mods/MoreStorageColors/Reference/gf_electric_eel_fillet.png
Mods/MoreStorageColors/Reference/f_kz_15.png
Mods/MoreStorageColors/Reference/armor_conditioner_system.png
Mods/MoreStorageColors/Reference/shot_animation_body_3.png
Mods/MoreStorageColors/Reference/wire.png
Mods/MoreStorageColors/Reference/recharger_base.png
Mods/MoreStorageColors/Reference/outside_right.png
Mods/MoreStorageColors/Reference/50ml_hl_75.png
Mods/MoreStorageColors/Reference/mxi_phone.png
Mods/MoreStorageColors/Reference/icons_junker.png
Mods/MoreStorageColors/Reference/f_kz_11.png
Mods/MoreStorageColors/Reference/oxycodone_pill.png
Mods/MoreStorageColors/Reference/50ml_dark_100.png
Mods/MoreStorageColors/Reference/kitchen_cleaver.png
Mods/MoreStorageColors/Reference/glock_receiver2.png
Mods/MoreStorageColors/Reference/large_dark_75.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_misc_card_chem.png
Mods/MoreStorageColors/Reference/bookBack_white.png
Mods/MoreStorageColors/Reference/storage_bay_large_msc_3.png
Mods/MoreStorageColors/Reference/e1z_gum_treat.png
Mods/MoreStorageColors/Reference/gf_dried_large_yellow_croaker.png
Mods/MoreStorageColors/Reference/silhouette-animations-tenth-size_0.png
Mods/MoreStorageColors/Reference/deed.png
Mods/MoreStorageColors/Reference/shotgun_shadow.png
Mods/MoreStorageColors/Reference/injector_color1.png
Mods/MoreStorageColors/Reference/paper_buttons4.png
Mods/MoreStorageColors/Reference/strip_new.png
Mods/MoreStorageColors/Reference/rat_s_m.png
Mods/MoreStorageColors/Reference/100ml_blue_100.png
Mods/MoreStorageColors/Reference/f_kz_01.png
Mods/MoreStorageColors/Reference/25ml_red_100.png
Mods/MoreStorageColors/Reference/energy_hand_canon.png
Mods/MoreStorageColors/Reference/networkIcons_26.png
Mods/MoreStorageColors/Reference/round74.png
Mods/MoreStorageColors/Reference/chem_scanner2.png
Mods/MoreStorageColors/Reference/tutorial_book_full.png
Mods/MoreStorageColors/Reference/purifier_2.png
Mods/MoreStorageColors/Reference/bloomberry_p2.png
Mods/MoreStorageColors/Reference/aqua_scan_button.png
Mods/MoreStorageColors/Reference/button_zoom_2.png
Mods/MoreStorageColors/Reference/cig_black.png
Mods/MoreStorageColors/Reference/icons_gunsmith.png
Mods/MoreStorageColors/Reference/shot_animation_body_0.png
Mods/MoreStorageColors/Reference/simple_backpack_modular_medium.png
Mods/MoreStorageColors/Reference/safe.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool_chemforge_r.png
Mods/MoreStorageColors/Reference/welder_s.png
Mods/MoreStorageColors/Reference/storage_bay_large_msc_2.png
Mods/MoreStorageColors/Reference/dossier_3.png
Mods/MoreStorageColors/Reference/postit_pink.png
Mods/MoreStorageColors/Reference/med_bottle_blue.png
Mods/MoreStorageColors/Reference/halo_glow.png
Mods/MoreStorageColors/Reference/newspaper.png
Mods/MoreStorageColors/Reference/pitcher_blue_25.png
Mods/MoreStorageColors/Reference/general_button_2x.png
Mods/MoreStorageColors/Reference/pitcher_blue_100.png
Mods/MoreStorageColors/Reference/example_chip1.png
Mods/MoreStorageColors/Reference/notebook_manual_section.png
Mods/MoreStorageColors/Reference/ending.png
Mods/MoreStorageColors/Reference/magnifier.png
Mods/MoreStorageColors/Reference/blank_card.png
Mods/MoreStorageColors/Reference/combat_machete_old.png
Mods/MoreStorageColors/Reference/seal_holo.png
Mods/MoreStorageColors/Reference/desk_animations1_8.png
Mods/MoreStorageColors/Reference/cardboard_box.png
Mods/MoreStorageColors/Reference/emoji_smile.png
Mods/MoreStorageColors/Reference/pitcher_dark_90.png
Mods/MoreStorageColors/Reference/networkIcons_25.png
Mods/MoreStorageColors/Reference/salvage_system.png
Mods/MoreStorageColors/Reference/farPuncho_1.png
Mods/MoreStorageColors/Reference/additionc_gambling.png
Mods/MoreStorageColors/Reference/spectral_analyser.png
Mods/MoreStorageColors/Reference/cigarette_color_guide.png
Mods/MoreStorageColors/Reference/desk_animations2_5.png
Mods/MoreStorageColors/Reference/logo-complexe1.png
Mods/MoreStorageColors/Reference/walker1_2.png
Mods/MoreStorageColors/Reference/gf_rusty_slicing_knife.png
Mods/MoreStorageColors/Reference/shot_animation_body_7.png
Mods/MoreStorageColors/Reference/gf_volcanic_fish.png
Mods/MoreStorageColors/Reference/postit.png
Mods/MoreStorageColors/Reference/lottery.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_blender.png
Mods/MoreStorageColors/Reference/iconMineral.png
Mods/MoreStorageColors/Reference/pitcher_blue_75.png
Mods/MoreStorageColors/Reference/mouse_trap_cheese.png
Mods/MoreStorageColors/Reference/arrow.png
Mods/MoreStorageColors/Reference/normal_red_100.png
Mods/MoreStorageColors/Reference/farm_module_l2.png
Mods/MoreStorageColors/Reference/shotgun_grip_mod.png
Mods/MoreStorageColors/Reference/small_dark_75.png
Mods/MoreStorageColors/Reference/box_tampon.png
Mods/MoreStorageColors/Reference/50ml_blue_10.png
Mods/MoreStorageColors/Reference/strip3.png
Mods/MoreStorageColors/Reference/injector_black.png
Mods/MoreStorageColors/Reference/desk_animations3_3.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_fillerweed_s.png
Mods/MoreStorageColors/Reference/small.png
Mods/MoreStorageColors/Reference/xl_red_50.png
Mods/MoreStorageColors/Reference/white.png
Mods/MoreStorageColors/Reference/close_pressed_down.png
Mods/MoreStorageColors/Reference/networkIcons_7.png
Mods/MoreStorageColors/Reference/pitcher_hl_90.png
Mods/MoreStorageColors/Reference/module_green_2_s.png
Mods/MoreStorageColors/Reference/25ml_dark_75.png
Mods/MoreStorageColors/Reference/nego_button.png
Mods/MoreStorageColors/Reference/titan_armor10s.png
Mods/MoreStorageColors/Reference/chemforge_r.png
Mods/MoreStorageColors/Reference/cigarette_color_card_item.png
Mods/MoreStorageColors/Reference/e1z_sprinte_candrink.png
Mods/MoreStorageColors/Reference/command_keycard.png
Mods/MoreStorageColors/Reference/border_white.png
Mods/MoreStorageColors/Reference/long_module1.png
Mods/MoreStorageColors/Reference/module_green_3.png
Mods/MoreStorageColors/Reference/coffee_bean.png
Mods/MoreStorageColors/Reference/glock_receiver.png
Mods/MoreStorageColors/Reference/glass_button_1.png
Mods/MoreStorageColors/Reference/tiktok_logo.png
Mods/MoreStorageColors/Reference/storage_bay_large_msc_4.png
Mods/MoreStorageColors/Reference/gf_dried_rainbow_fish.png
Mods/MoreStorageColors/Reference/networkIcons_5.png
Mods/MoreStorageColors/Reference/100ml.png
Mods/MoreStorageColors/Reference/farm_module1.png
Mods/MoreStorageColors/Reference/injector_green_2.png
Mods/MoreStorageColors/Reference/bad_lease.png
Mods/MoreStorageColors/Reference/med1.png
Mods/MoreStorageColors/Reference/red_serum.png
Mods/MoreStorageColors/Reference/additionc_nicotine.png
Mods/MoreStorageColors/Reference/clientele-Sheet_2.png
Mods/MoreStorageColors/Reference/f_kz_05.png
Mods/MoreStorageColors/Reference/hydroponic_p_on.png
Mods/MoreStorageColors/Reference/blood_bag.png
Mods/MoreStorageColors/Reference/button_up_orange.png
Mods/MoreStorageColors/Reference/bottle2.png
Mods/MoreStorageColors/Reference/steel_sheet_rough.png
Mods/MoreStorageColors/Reference/blender.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_aloe_vera.png
Mods/MoreStorageColors/Reference/walker1_7.png
Mods/MoreStorageColors/Reference/letter_incorrect.png
Mods/MoreStorageColors/Reference/beer_case_2.png
Mods/MoreStorageColors/Reference/water_sign.png
Mods/MoreStorageColors/Reference/module_red_3.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool_chemforge_b.png
Mods/MoreStorageColors/Reference/guide_ribwich_logo.png
Mods/MoreStorageColors/Reference/pouch_general.png
Mods/MoreStorageColors/Reference/letter-correct.png
Mods/MoreStorageColors/Reference/gf_small_yellow_croaker.png
Mods/MoreStorageColors/Reference/rat_xl_m.png
Mods/MoreStorageColors/Reference/dream_cap_p.png
Mods/MoreStorageColors/Reference/nutrient_tablet.png
Mods/MoreStorageColors/Reference/blue_button_2x.png
Mods/MoreStorageColors/Reference/alarm_system.png
Mods/MoreStorageColors/Reference/toolbox_old.png
Mods/MoreStorageColors/Reference/bloomberry_p.png
Mods/MoreStorageColors/Reference/genetic_scanner.png
Mods/MoreStorageColors/Reference/checkbox.png
Mods/MoreStorageColors/Reference/midBackpack_0.png
Mods/MoreStorageColors/Reference/med_box_u.png
Mods/MoreStorageColors/Reference/material_sign.png
Mods/MoreStorageColors/Reference/large_red_25.png
Mods/MoreStorageColors/Reference/desk_animations4_7.png
Mods/MoreStorageColors/Reference/e1z_driedveggies_diredfood.png
Mods/MoreStorageColors/Reference/tutorial_page.png
Mods/MoreStorageColors/Reference/desk_animations1_7.png
Mods/MoreStorageColors/Reference/round190.png
Mods/MoreStorageColors/Reference/cv_applause.png
Mods/MoreStorageColors/Reference/tutorial_bubble_unpointed_x2.png
Mods/MoreStorageColors/Reference/guide_ribwich_logo_incorrect.png
Mods/MoreStorageColors/Reference/phone_button1h.png
Mods/MoreStorageColors/Reference/storage_bay_large2.png
Mods/MoreStorageColors/Reference/pitcher_red_90.png
Mods/MoreStorageColors/Reference/wanted_portait1.png
Mods/MoreStorageColors/Reference/feces_large.png
Mods/MoreStorageColors/Reference/nightmare_cap.png
Mods/MoreStorageColors/Reference/border_red.png
Mods/MoreStorageColors/Reference/rat_xxl_m.png
Mods/MoreStorageColors/Reference/black_lamp_on_2.png
Mods/MoreStorageColors/Reference/xl_red_75.png
Mods/MoreStorageColors/Reference/icons_fence.png
Mods/MoreStorageColors/Reference/button_main_menu_p.png
Mods/MoreStorageColors/Reference/wanted_portait5.png
Mods/MoreStorageColors/Reference/wine_bottle.png
Mods/MoreStorageColors/Reference/midBackpack_3.png
Mods/MoreStorageColors/Reference/button_integoration.png
Mods/MoreStorageColors/Reference/module_green_2_h.png
Mods/MoreStorageColors/Reference/header2.png
Mods/MoreStorageColors/Reference/f_kz_07.png
Mods/MoreStorageColors/Reference/chip_damaged.png
Mods/MoreStorageColors/Reference/rat_l_m.png
Mods/MoreStorageColors/Reference/normal_dark_100.png
Mods/MoreStorageColors/Reference/titan_armor9.png
Mods/MoreStorageColors/Reference/case_front.png
Mods/MoreStorageColors/Reference/f_kz_02.png
Mods/MoreStorageColors/Reference/injector_pure_white.png
Mods/MoreStorageColors/Reference/injector_background.png
Mods/MoreStorageColors/Reference/module_red.png
Mods/MoreStorageColors/Reference/padlock.png
Mods/MoreStorageColors/Reference/large_blue_25.png
Mods/MoreStorageColors/Reference/wine_label0.png
Mods/MoreStorageColors/Reference/xl_dark_50.png
Mods/MoreStorageColors/Reference/bottle0.png
Mods/MoreStorageColors/Reference/hatchet.png
Mods/MoreStorageColors/Reference/nego_buttonx2-2Hover.png
Mods/MoreStorageColors/Reference/Background.png
Mods/MoreStorageColors/Reference/aquascan.png
Mods/MoreStorageColors/Reference/50ml_blue_50.png
Mods/MoreStorageColors/Reference/green_serum.png
Mods/MoreStorageColors/Reference/red_beer.png
Mods/MoreStorageColors/Reference/normal_dark_25.png
Mods/MoreStorageColors/Reference/module_yellow_2.png
Mods/MoreStorageColors/Reference/sci_module.png
Mods/MoreStorageColors/Reference/page3.png
Mods/MoreStorageColors/Reference/small_hl_100.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_acid.png
Mods/MoreStorageColors/Reference/pitcher_hl_25.png
Mods/MoreStorageColors/Reference/injector_turquoise.png
Mods/MoreStorageColors/Reference/battery100.png
Mods/MoreStorageColors/Reference/paper_buttons3.png
Mods/MoreStorageColors/Reference/screen_light.png
Mods/MoreStorageColors/Reference/e1z_chips_treat.png
Mods/MoreStorageColors/Reference/pitcher_red_25.png
Mods/MoreStorageColors/Reference/nego_button_Hover.png
Mods/MoreStorageColors/Reference/stopwatch.png
Mods/MoreStorageColors/Reference/wirecutter.png
Mods/MoreStorageColors/Reference/berry_1normal.png
Mods/MoreStorageColors/Reference/button_rewind_highlight.png
Mods/MoreStorageColors/Reference/stanag_30.png
Mods/MoreStorageColors/Reference/chip_correct.png
Mods/MoreStorageColors/Reference/seal_golden.png
Mods/MoreStorageColors/Reference/water_filter.png
Mods/MoreStorageColors/Reference/med_card.png
Mods/MoreStorageColors/Reference/e1z_coconutmilk_drink.png
Mods/MoreStorageColors/Reference/5ml_dark.png
Mods/MoreStorageColors/Reference/heavy_handmade_pistol.png
Mods/MoreStorageColors/Reference/mini_blue_100.png
Mods/MoreStorageColors/Reference/machine_bay0.png
Mods/MoreStorageColors/Reference/food_stamp.png
Mods/MoreStorageColors/Reference/juice_pouch.png
Mods/MoreStorageColors/Reference/outside_bottom_right.png
Mods/MoreStorageColors/Reference/energy_credit_breeder.png
Mods/MoreStorageColors/Reference/button_zoom_1.png
Mods/MoreStorageColors/Reference/large_hl_10.png
Mods/MoreStorageColors/Reference/heavy_handmade_pistol_m.png
Mods/MoreStorageColors/Reference/button_bargain-Sheet_1.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_hydroreed_filter.png
Mods/MoreStorageColors/Reference/outside.png
Mods/MoreStorageColors/Reference/metal_ingot.png
Mods/MoreStorageColors/Reference/networkIcons_3.png
Mods/MoreStorageColors/Reference/xl_blue_100.png
Mods/MoreStorageColors/Reference/uv.png
Mods/MoreStorageColors/Reference/gf_large_yellow_croaker.png
Mods/MoreStorageColors/Reference/default_c.png
Mods/MoreStorageColors/Reference/normal_red_50.png
Mods/MoreStorageColors/Reference/contractBack.png
Mods/MoreStorageColors/Reference/light_purple.png
Mods/MoreStorageColors/Reference/hiring_sign.png
Mods/MoreStorageColors/Reference/button_rewind_pressed.png
Mods/MoreStorageColors/Reference/light_dropship.png
Mods/MoreStorageColors/Reference/colorful.png
Mods/MoreStorageColors/Reference/coffee_bean_p.png
Mods/MoreStorageColors/Reference/button_up_green.png
Mods/MoreStorageColors/Reference/vertical_scroll_bar.png
Mods/MoreStorageColors/Reference/50ml_red_100.png
Mods/MoreStorageColors/Reference/networkIcons_27.png
Mods/MoreStorageColors/Reference/glass_shard.png
Mods/MoreStorageColors/Reference/oxycodone_pill_bottle.png
Mods/MoreStorageColors/Reference/large_hl_90.png
Mods/MoreStorageColors/Reference/electronic1.png
Mods/MoreStorageColors/Reference/paper_buttons_close1.png
Mods/MoreStorageColors/Reference/printer_chip2.png
Mods/MoreStorageColors/Reference/intel_red.png
Mods/MoreStorageColors/Reference/scrap_metal_2.png
Mods/MoreStorageColors/Reference/yeast_gen.png
Mods/MoreStorageColors/Reference/nutrifruit_old.png
Mods/MoreStorageColors/Reference/gf_double_hook_fishing_rod.png
Mods/MoreStorageColors/Reference/iconGrowth_lit.png
Mods/MoreStorageColors/Reference/gf_electric_eel.png
Mods/MoreStorageColors/Reference/beer_case_1.png
Mods/MoreStorageColors/Reference/intimidating.png
Mods/MoreStorageColors/Reference/desk_animations4_4.png
Mods/MoreStorageColors/Reference/gf_dried_horned_swordfish.png
Mods/MoreStorageColors/Reference/emoji_star.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_3.png
Mods/MoreStorageColors/Reference/shotgun_stock_mod.png
Mods/MoreStorageColors/Reference/close.png
Mods/MoreStorageColors/Reference/injector_dam3.png
Mods/MoreStorageColors/Reference/items_medical_13.png
Mods/MoreStorageColors/Reference/light_orange.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_chem_finisher.png
Mods/MoreStorageColors/Reference/arrow_right.png
Mods/MoreStorageColors/Reference/gf_purifier_fish.png
Mods/MoreStorageColors/Reference/farm_module_l3.png
Mods/MoreStorageColors/Reference/flux_agent_advanced.png
Mods/MoreStorageColors/Reference/machine_bay3.png
Mods/MoreStorageColors/Reference/cassette2.png
Mods/MoreStorageColors/Reference/ammo_box.png
Mods/MoreStorageColors/Reference/fce_dish_lemon_fillet.png
Mods/MoreStorageColors/Reference/desk_animations2_4.png
Mods/MoreStorageColors/Reference/midTrechCoat_2.png
Mods/MoreStorageColors/Reference/calendar_top-Sheet_3.png
Mods/MoreStorageColors/Reference/end_top.png
Mods/MoreStorageColors/Reference/rat_m_f_d.png
Mods/MoreStorageColors/Reference/illiterate.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_lipobond.png
Mods/MoreStorageColors/Reference/chemical3.png
Mods/MoreStorageColors/Reference/nego_button_Disabled.png
Mods/MoreStorageColors/Reference/pitcher_hl_10.png
Mods/MoreStorageColors/Reference/moonshine.png
Mods/MoreStorageColors/Reference/water_pitcher.png
Mods/MoreStorageColors/Reference/cig_red.png
Mods/MoreStorageColors/Reference/chem_scan_button.png
Mods/MoreStorageColors/Reference/black.png
Mods/MoreStorageColors/Reference/wine_rack.png
Mods/MoreStorageColors/Reference/ul_p.png
Mods/MoreStorageColors/Reference/black_lamp_on_alt_3.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_cafeine_pill.png
Mods/MoreStorageColors/Reference/f_kz_13.png
Mods/MoreStorageColors/Reference/25ml_hl_90.png
Mods/MoreStorageColors/Reference/g_buy.png
Mods/MoreStorageColors/Reference/refresh_2.png
Mods/MoreStorageColors/Reference/recharger1.png
Mods/MoreStorageColors/Reference/midGasmask_1.png
Mods/MoreStorageColors/Reference/100ml75.png
Mods/MoreStorageColors/Reference/dark_green.png
Mods/MoreStorageColors/Reference/small_hl_10.png
Mods/MoreStorageColors/Reference/farm_module_l1.png
Mods/MoreStorageColors/Reference/nudka.png
Mods/MoreStorageColors/Reference/ribwich.png
Mods/MoreStorageColors/Reference/improvised_spear.png
Mods/MoreStorageColors/Reference/red.png
Mods/MoreStorageColors/Reference/pitcher_dark_25.png
Mods/MoreStorageColors/Reference/highlight_white.png
Mods/MoreStorageColors/Reference/close_button_h.png
Mods/MoreStorageColors/Reference/midBackpack_2.png
Mods/MoreStorageColors/Reference/long_pipe.png
Mods/MoreStorageColors/Reference/guide_indicator_orange.png
Mods/MoreStorageColors/Reference/icon_synaprest.png
Mods/MoreStorageColors/Reference/chip_cracked.png
Mods/MoreStorageColors/Reference/module_yellow_2_s.png
Mods/MoreStorageColors/Reference/raw_meat.png
Mods/MoreStorageColors/Reference/heavy_handmade_pistol_2.png
Mods/MoreStorageColors/Reference/items_weapons_ranged_antique_0.png
Mods/MoreStorageColors/Reference/battery0.png
Mods/MoreStorageColors/Reference/dark_purple.png
Mods/MoreStorageColors/Reference/e1z_chickensausage_canfood.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_kotton_plant_s.png
Mods/MoreStorageColors/Reference/cassette4.png
Mods/MoreStorageColors/Reference/cropped-letter-font.png
Mods/MoreStorageColors/Reference/refresh.png
Mods/MoreStorageColors/Reference/beer_case_7.png
Mods/MoreStorageColors/Reference/shot_animation_body_6.png
Mods/MoreStorageColors/Reference/cassette8.png
Mods/MoreStorageColors/Reference/50ml_red_50.png
Mods/MoreStorageColors/Reference/xiaowo_ratcolony_breeding_inducer.png
Mods/MoreStorageColors/Reference/simple_backpack_large.png
Mods/MoreStorageColors/Reference/large_hl_50.png
Mods/MoreStorageColors/Reference/chemforge.png
Mods/MoreStorageColors/Reference/battery40.png
Mods/MoreStorageColors/Reference/sec_box_u.png
Mods/MoreStorageColors/Reference/network_button_normal.png
Mods/MoreStorageColors/Reference/battery_ext0.png
Mods/MoreStorageColors/Reference/WAM_Icon_moddesign_adulterated_alcohol_supplier.png
Mods/MoreStorageColors/Reference/muzzleflash2.png
Mods/MoreStorageColors/Reference/scratch_back.png
Mods/MoreStorageColors/Reference/smelling_salt.png
Mods/MoreStorageColors/Reference/nutrifruit_p.png
Mods/MoreStorageColors/Reference/nav_system.png
Mods/MoreStorageColors/Reference/light_pistol_ammo_nl.png
Mods/MoreStorageColors/Reference/light_cyan.png
Mods/MoreStorageColors/Reference/arrow_left.png
Mods/MoreStorageColors/Reference/silhouette-animations-tenth-size_3.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_module_chem_module.png
Mods/MoreStorageColors/Reference/clientele-Sheet_3.png
Mods/MoreStorageColors/Reference/small_flash.png
Mods/MoreStorageColors/Reference/gen_button_2x_h.png
Mods/MoreStorageColors/Reference/midGasmask_3.png
Mods/MoreStorageColors/Reference/down.png
Mods/MoreStorageColors/Reference/bloomberry_seed.png
Mods/MoreStorageColors/Reference/eng.png
Mods/MoreStorageColors/Reference/module_green_l.png
Mods/MoreStorageColors/Reference/surgery_toolkit.png
Mods/MoreStorageColors/Reference/100ml_blue_75.png
Mods/MoreStorageColors/Reference/button_contact.png
Mods/MoreStorageColors/Reference/normal_blue_10.png
Mods/MoreStorageColors/Reference/colorChecker.png
Mods/MoreStorageColors/Reference/emoji_sad.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_coffee_bean_p.png
Mods/MoreStorageColors/Reference/desk_animations4_1.png
Mods/MoreStorageColors/Reference/perfume2.png
Mods/MoreStorageColors/Reference/rep_indicator.png
Mods/MoreStorageColors/Reference/50ml_red_75.png
Mods/MoreStorageColors/Reference/vending0.png
Mods/MoreStorageColors/Reference/pitcher_hl_75.png
Mods/MoreStorageColors/Reference/permit_gun_3.png
Mods/MoreStorageColors/Reference/chem_scanner.png
Mods/MoreStorageColors/Reference/small_blue_10.png
Mods/MoreStorageColors/Reference/label.png
Mods/MoreStorageColors/Reference/items_medical_14.png
Mods/MoreStorageColors/Reference/items_weapons_ranged_antique_3.png
Mods/MoreStorageColors/Reference/nuclear_waste.png
Mods/MoreStorageColors/Reference/vacuum_robot.png
Mods/MoreStorageColors/Reference/business_card_template.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_dream_dust.png
Mods/MoreStorageColors/Reference/pipe.png
Mods/MoreStorageColors/Reference/normal_red_10.png
Mods/MoreStorageColors/Reference/titan_armor.png
Mods/MoreStorageColors/Reference/100ml_dark_25.png
Mods/MoreStorageColors/Reference/blue_blood_bag.png
Mods/MoreStorageColors/Reference/mini_hl_90.png
Mods/MoreStorageColors/Reference/trashcan100.png
Mods/MoreStorageColors/Reference/25ml_red_10.png
Mods/MoreStorageColors/Reference/heating_plate.png
Mods/MoreStorageColors/Reference/manual1.png
Mods/MoreStorageColors/Reference/hemostatic_agent.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_hydro_plant_analyser.png
Mods/MoreStorageColors/Reference/pc_count-Sheet_3.png
Mods/MoreStorageColors/Reference/gf_fish_oil.png
Mods/MoreStorageColors/Reference/sliding_area.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_meal_cap_l.png
Mods/MoreStorageColors/Reference/module_green_2.png
Mods/MoreStorageColors/Reference/cassette3.png
Mods/MoreStorageColors/Reference/indicator_orange.png
Mods/MoreStorageColors/Reference/uibackOrange1L.png
Mods/MoreStorageColors/Reference/perfume1.png
Mods/MoreStorageColors/Reference/desk_animations3_4.png
Mods/MoreStorageColors/Reference/large_red_90.png
Mods/MoreStorageColors/Reference/fillerweed.png
Mods/MoreStorageColors/Reference/simple_backpack_medium_military.png
Mods/MoreStorageColors/Reference/sup_card.png
Mods/MoreStorageColors/Reference/nutrifruit_seed_old.png
Mods/MoreStorageColors/Reference/module_extractor_adv.png
Mods/MoreStorageColors/Reference/small_raw_xeno_meat.png
Mods/MoreStorageColors/Reference/indicator_missing.png
Mods/MoreStorageColors/Reference/next.png
Mods/MoreStorageColors/Reference/desk_animations2_0.png
Mods/MoreStorageColors/Reference/printer_chip3.png
Mods/MoreStorageColors/Reference/previous_c.png
Mods/MoreStorageColors/Reference/next_d.png
Mods/MoreStorageColors/Reference/pitcher_dark_100.png
Mods/MoreStorageColors/Reference/simple_backpack_modular_large.png
Mods/MoreStorageColors/Reference/cup_noodle.png
Mods/MoreStorageColors/Reference/100ml_hl_90.png
Mods/MoreStorageColors/Reference/module_red_t.png
Mods/MoreStorageColors/Reference/normal_dark_90.png
Mods/MoreStorageColors/Reference/black_lamp.png
Mods/MoreStorageColors/Reference/scratch_stack.png
Mods/MoreStorageColors/Reference/c4.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_misc_chemistry_guide.png
Mods/MoreStorageColors/Reference/intel_sec.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_4.png
Mods/MoreStorageColors/Reference/module_corrupt.png
Mods/MoreStorageColors/Reference/logo_correct.png
Mods/MoreStorageColors/Reference/molotov_cocktail.png
Mods/MoreStorageColors/Reference/letter-incorrect3.png
Mods/MoreStorageColors/Reference/f_kz_22.png
Mods/MoreStorageColors/Reference/titan_armor10h.png
Mods/MoreStorageColors/Reference/text_background.png
Mods/MoreStorageColors/Reference/checkbox_arrow.png
Mods/MoreStorageColors/Reference/recharger_top.png
Mods/MoreStorageColors/Reference/pc_count-Sheet_0.png
Mods/MoreStorageColors/Reference/desk_animations3_0.png
Mods/MoreStorageColors/Reference/mini75.png
Mods/MoreStorageColors/Reference/25ml_red_25.png
Mods/MoreStorageColors/Reference/small_dark_90.png
Mods/MoreStorageColors/Reference/hydroponic.png
Mods/MoreStorageColors/Reference/50ml_red_10.png
Mods/MoreStorageColors/Reference/dialogue_bubble_science.png
Mods/MoreStorageColors/Reference/farBackpack_0.png
Mods/MoreStorageColors/Reference/mini_red_90.png
Mods/MoreStorageColors/Reference/scav_medal.png
Mods/MoreStorageColors/Reference/shotgun_shadow_only.png
Mods/MoreStorageColors/Reference/module_black_4.png
Mods/MoreStorageColors/Reference/manual4.png
Mods/MoreStorageColors/Reference/codebook.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_hydroponic.png
Mods/MoreStorageColors/Reference/networkIcons_29.png
Mods/MoreStorageColors/Reference/ally.png
Mods/MoreStorageColors/Reference/paper_buttons_close3.png
Mods/MoreStorageColors/Reference/normal_hl_75.png
Mods/MoreStorageColors/Reference/nutrifruit_seed.png
Mods/MoreStorageColors/Reference/first_aid_box_2.png
Mods/MoreStorageColors/Reference/networkIcons_14.png
Mods/MoreStorageColors/Reference/previous_d.png
Mods/MoreStorageColors/Reference/battery_ext80.png
Mods/MoreStorageColors/Reference/desk_animations3_6.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_dream_cap.png
Mods/MoreStorageColors/Reference/canteen.png
Mods/MoreStorageColors/Reference/machine_bay1.png
Mods/MoreStorageColors/Reference/indicator_red.png
Mods/MoreStorageColors/Reference/mini_dark_25.png
Mods/MoreStorageColors/Reference/module_green.png
Mods/MoreStorageColors/Reference/gf_salt.png
Mods/MoreStorageColors/Reference/frag.png
Mods/MoreStorageColors/Reference/tazer.png
Mods/MoreStorageColors/Reference/pc_count-Sheet_2.png
Mods/MoreStorageColors/Reference/uiback1LGreen.png
Mods/MoreStorageColors/Reference/dealmaker.png
Mods/MoreStorageColors/Reference/shampoo.png
Mods/MoreStorageColors/Reference/pause.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_birth_control_pill.png
Mods/MoreStorageColors/Reference/desk_animations3_2.png
Mods/MoreStorageColors/Reference/pitcher_red_100.png
Mods/MoreStorageColors/Reference/bell_button_sheet_2.png
Mods/MoreStorageColors/Reference/connected.png
Mods/MoreStorageColors/Reference/printer_chip4.png
Mods/MoreStorageColors/Reference/calendar_top-Sheet_0.png
Mods/MoreStorageColors/Reference/fanny_pack.png
Mods/MoreStorageColors/Reference/water_test_strip.png
Mods/MoreStorageColors/Reference/purifier_w.png
Mods/MoreStorageColors/Reference/pouch_injector_large_double.png
Mods/MoreStorageColors/Reference/storage_bay1.png
Mods/MoreStorageColors/Reference/pitcher_dark_75.png
Mods/MoreStorageColors/Reference/eng_box_u.png
Mods/MoreStorageColors/Reference/event.png
Mods/MoreStorageColors/Reference/network_button_disabled.png
Mods/MoreStorageColors/Reference/pause_c.png
Mods/MoreStorageColors/Reference/scratch.png
Mods/MoreStorageColors/Reference/white_rounded.png
Mods/MoreStorageColors/Reference/rev_p.png
Mods/MoreStorageColors/Reference/slider_bar_filled_2x.png
Mods/MoreStorageColors/Reference/card_mining.png
Mods/MoreStorageColors/Reference/letter_incorrect8.png
Mods/MoreStorageColors/Reference/nutrifruit_s.png
Mods/MoreStorageColors/Reference/25ml_blue_25.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_meal_cap_s.png
Mods/MoreStorageColors/Reference/home.png
Mods/MoreStorageColors/Reference/scrollbar_handle.png
Mods/MoreStorageColors/Reference/side_street.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_tool_hydroponic_p_on.png
Mods/MoreStorageColors/Reference/seal-open.png
Mods/MoreStorageColors/Reference/letter_incorrect7.png
Mods/MoreStorageColors/Reference/desk_animations2_7.png
Mods/MoreStorageColors/Reference/100ml_red_10.png
Mods/MoreStorageColors/Reference/nego_buttonx2-3Pressed.png
Mods/MoreStorageColors/Reference/e1z_bucketinstantnoodles_fastfood.png
Mods/MoreStorageColors/Reference/module_red_c.png
Mods/MoreStorageColors/Reference/25ml_hl_100.png
Mods/MoreStorageColors/Reference/25ml_dark_10.png
Mods/MoreStorageColors/Reference/noun-people-6240743-FFFFFF.png
Mods/MoreStorageColors/Reference/blabbermouth.png
Mods/MoreStorageColors/Reference/25ml_hl_25.png
Mods/MoreStorageColors/Reference/networkIcons_30.png
Mods/MoreStorageColors/Reference/med2.png
Mods/MoreStorageColors/Reference/toolbox_new.png
Mods/MoreStorageColors/Reference/uiback2L.png
Mods/MoreStorageColors/Reference/iconWater.png
Mods/MoreStorageColors/Reference/requirement.png
Mods/MoreStorageColors/Reference/black_lamp_on_alt_1.png
Mods/MoreStorageColors/Reference/down_c.png
Mods/MoreStorageColors/Reference/joe_card.png
Mods/MoreStorageColors/Reference/mouse_trap.png
Mods/MoreStorageColors/Reference/100ml_dark_75.png
Mods/MoreStorageColors/Reference/cert3.png
Mods/MoreStorageColors/Reference/energy_credit_counterfeit.png
Mods/MoreStorageColors/Reference/perk_n.png
Mods/MoreStorageColors/Reference/animal_feeder.png
Mods/MoreStorageColors/Reference/ap_ammo_box_packed.png
Mods/MoreStorageColors/Reference/wanted_portait3.png
Mods/MoreStorageColors/Reference/sec_box.png
Mods/MoreStorageColors/Reference/networkIcons_0.png
Mods/MoreStorageColors/Reference/paper_buttons_screen_2.png
Mods/MoreStorageColors/Reference/injector_green.png
Mods/MoreStorageColors/Reference/beer_case_3.png
Mods/MoreStorageColors/Reference/sec_lamps_green.png
Mods/MoreStorageColors/Reference/sec_keycard.png
Mods/MoreStorageColors/Reference/f_kz_14.png
Mods/MoreStorageColors/Reference/guide_color_guide.png
Mods/MoreStorageColors/Reference/networkIcons_6.png
Mods/MoreStorageColors/Reference/networkIcons_32.png
Mods/MoreStorageColors/Reference/midBandana_1.png
Mods/MoreStorageColors/Reference/guide2.png
Mods/MoreStorageColors/Reference/nutrifruit_xl.png
Mods/MoreStorageColors/Reference/evaporator_b.png
Mods/MoreStorageColors/Reference/cropped-seal-broke.png
Mods/MoreStorageColors/Reference/50ml_hl_50.png
Mods/MoreStorageColors/Reference/midGasmask_2.png
Mods/MoreStorageColors/Reference/storage_bay_msc.png
Mods/MoreStorageColors/Reference/dark.png
Mods/MoreStorageColors/Reference/outline_weapon.png
Mods/MoreStorageColors/Reference/button_unavaible.png
Mods/MoreStorageColors/Reference/beer_case_0.png
Mods/MoreStorageColors/Reference/alarm_system_r.png
Mods/MoreStorageColors/Reference/hemostatic_bandage_small.png
Mods/MoreStorageColors/Reference/modding_layout_2x.png
Mods/MoreStorageColors/Reference/mini_blue_10.png
Mods/MoreStorageColors/Reference/walker1_4.png
Mods/MoreStorageColors/Reference/large_dark_90.png
Mods/MoreStorageColors/Reference/pouch_medical.png
Mods/MoreStorageColors/Reference/cat_bar.png
Mods/MoreStorageColors/Reference/farBackpack_2.png
Mods/MoreStorageColors/Reference/phone_button_large.png
Mods/MoreStorageColors/Reference/nightly_tab.png
Mods/MoreStorageColors/Reference/shot_animation_wound_3.png
Mods/MoreStorageColors/Reference/feces.png
Mods/MoreStorageColors/Reference/processed_meat.png
Mods/MoreStorageColors/Reference/berry_2under1.png
Mods/MoreStorageColors/Reference/med_bottle_small_pink.png
Mods/MoreStorageColors/Reference/button_disabled_green.png
Mods/MoreStorageColors/Reference/farTrechCoat_3.png
Mods/MoreStorageColors/Reference/5ml_blue.png
Mods/MoreStorageColors/Reference/service_box_u.png
Mods/MoreStorageColors/Reference/one_hand.png
Mods/MoreStorageColors/Reference/100ml_dark_10.png
Mods/MoreStorageColors/Reference/powerblock_on.png
Mods/MoreStorageColors/Reference/fountain0.png
Mods/MoreStorageColors/Reference/f_kz_20.png
Mods/MoreStorageColors/Reference/mouse_trap_m.png
Mods/MoreStorageColors/Reference/fce_dish_crystal_sushi.png
Mods/MoreStorageColors/Reference/client_counter-Sheet_2.png
Mods/MoreStorageColors/Reference/dream_cap_seed.png
Mods/MoreStorageColors/Reference/large_dark_25.png
Mods/MoreStorageColors/Reference/cassette7.png
Mods/MoreStorageColors/Reference/module_extractor.png
Mods/MoreStorageColors/Reference/peanut_butter_orange.png
Mods/MoreStorageColors/Reference/working_pipes.png
Mods/MoreStorageColors/Reference/networkIcons_1.png
Mods/MoreStorageColors/Reference/lockpicks.png
Mods/MoreStorageColors/Reference/xl_blue_25.png
Mods/MoreStorageColors/Reference/cap.png
Mods/MoreStorageColors/Reference/eng_box.png
Mods/MoreStorageColors/Reference/water_bottle.png
Mods/MoreStorageColors/Reference/items_hydroponic_plant_1.png
Mods/MoreStorageColors/Reference/network_button_pressed.png
Mods/MoreStorageColors/Reference/logo_incorrect5.png
Mods/MoreStorageColors/Reference/rat_xl_f_d.png
Mods/MoreStorageColors/Reference/shutter_button_sheet_2.png
Mods/MoreStorageColors/Reference/items_food_soda_red.png
Mods/MoreStorageColors/Reference/bread_loaf.png
Mods/MoreStorageColors/Reference/med_bottle_red.png
Mods/MoreStorageColors/Reference/med_module.png
Mods/MoreStorageColors/Reference/25ml_hl_50.png
Mods/MoreStorageColors/Reference/networkIcons_33.png
Mods/MoreStorageColors/Reference/empty_beer_bottle.png
Mods/MoreStorageColors/Reference/jar_green.png
Mods/MoreStorageColors/Reference/card_2.png
Mods/MoreStorageColors/Reference/button_transfer.png
Mods/MoreStorageColors/Reference/kotton_fiber_p.png
Mods/MoreStorageColors/Reference/chem_finisher.png
Mods/MoreStorageColors/Reference/mini_blue_25.png
Mods/MoreStorageColors/Reference/e1z_beat_drink.png
Mods/MoreStorageColors/Reference/items_medical_12.png
Mods/MoreStorageColors/Reference/letter_incorrect6.png
Mods/MoreStorageColors/Reference/medical_sign.png
Mods/MoreStorageColors/Reference/shot_animation_wound_5.png
Mods/MoreStorageColors/Reference/large_hl_75.png
Mods/MoreStorageColors/Reference/farTrechCoat_2.png
Mods/MoreStorageColors/Reference/button_confront_2x.png
Mods/MoreStorageColors/Reference/close_pressed.png
Mods/MoreStorageColors/Reference/letter-font.png
Mods/MoreStorageColors/Reference/food_sign.png
Mods/MoreStorageColors/Reference/outside_top_right.png
Mods/MoreStorageColors/Reference/pitcher_hl_100.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_nightmare_cap.png
Mods/MoreStorageColors/Reference/small_red_90.png
Mods/MoreStorageColors/Reference/desk_animations1_1.png
Mods/MoreStorageColors/Reference/magnifying_mask.png
Mods/MoreStorageColors/Reference/moisture_farm.png
Mods/MoreStorageColors/Reference/berry_4mold.png
Mods/MoreStorageColors/Reference/pitcher0.png
Mods/MoreStorageColors/Reference/gf_dried_volcanic_fish.png
Mods/MoreStorageColors/Reference/injector_pure_white_s.png
Mods/MoreStorageColors/Reference/50ml_dark_10.png
Mods/MoreStorageColors/Reference/midBandana_3.png
Mods/MoreStorageColors/Reference/minimalist.png
Mods/MoreStorageColors/Reference/nutrifruit_crate.png
Mods/MoreStorageColors/Reference/plasma_fuel_cell.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_kotton_plant_l.png
Mods/MoreStorageColors/Reference/long_module3.png
Mods/MoreStorageColors/Reference/desk_animations1_5.png
Mods/MoreStorageColors/Reference/module_ruined.png
Mods/MoreStorageColors/Reference/uiback1.png
Mods/MoreStorageColors/Reference/checkbox_checked_orange.png
Mods/MoreStorageColors/Reference/republic_logo_checker.png
Mods/MoreStorageColors/Reference/middle.png
Mods/MoreStorageColors/Reference/playtest_button_steam.png
Mods/MoreStorageColors/Reference/25ml.png
Mods/MoreStorageColors/Reference/skincare_cream.png
Mods/MoreStorageColors/Reference/50ml_hl_25.png
Mods/MoreStorageColors/Reference/small_dark_10.png
Mods/MoreStorageColors/Reference/mini_hl_100.png
Mods/MoreStorageColors/Reference/toilet_paper.png
Mods/MoreStorageColors/Reference/pitcher_dark_10.png
Mods/MoreStorageColors/Reference/desk_animations3_5.png
Mods/MoreStorageColors/Reference/shutter_button_sheet_0.png
Mods/MoreStorageColors/Reference/5ml_red.png
Mods/MoreStorageColors/Reference/button_disabled_blue.png
Mods/MoreStorageColors/Reference/injector_red.png
Mods/MoreStorageColors/Reference/playtest_button_discord.png
Mods/MoreStorageColors/Reference/sec_module.png
Mods/MoreStorageColors/Reference/yeast.png
Mods/MoreStorageColors/Reference/morsel_small.png
Mods/MoreStorageColors/Reference/armor_lining.png
Mods/MoreStorageColors/Reference/exotic_matter_cell.png
Mods/MoreStorageColors/Reference/cropped-logo-correct.png
Mods/MoreStorageColors/Reference/button_dismiss.png
Mods/MoreStorageColors/Reference/border_tan.png
Mods/MoreStorageColors/Reference/seal-correct.png
Mods/MoreStorageColors/Reference/rat_m_f.png
Mods/MoreStorageColors/Reference/mini_dark_90.png
Mods/MoreStorageColors/Reference/furnace_on.png
Mods/MoreStorageColors/Reference/tab_active.png
Mods/MoreStorageColors/Reference/wild_pat.png
Mods/MoreStorageColors/Reference/water_bottle_full_premium.png
Mods/MoreStorageColors/Reference/manual5.png
Mods/MoreStorageColors/Reference/box_cereal.png
Mods/MoreStorageColors/Reference/farPuncho_3.png
Mods/MoreStorageColors/Reference/large_blue_100.png
Mods/MoreStorageColors/Reference/water_bottle_full.png
Mods/MoreStorageColors/Reference/dossier.png
Mods/MoreStorageColors/Reference/end.png
Mods/MoreStorageColors/Reference/desk_animations3_8.png
Mods/MoreStorageColors/Reference/up_c.png
Mods/MoreStorageColors/Reference/close_button_p.png
Mods/MoreStorageColors/Reference/indicator_green.png
Mods/MoreStorageColors/Reference/25ml_dark_25.png
Mods/MoreStorageColors/Reference/farPuncho_2.png
Mods/MoreStorageColors/Reference/guide_davre_logo_incorrect.png
Mods/MoreStorageColors/Reference/card_rev.png
Mods/MoreStorageColors/Reference/button_zoom_0.png
Mods/MoreStorageColors/Reference/strip5.png
Mods/MoreStorageColors/Reference/revolver_short_tac.png
Mods/MoreStorageColors/Reference/meal_cap_s.png
Mods/MoreStorageColors/Reference/100ml_red_50.png
Mods/MoreStorageColors/Reference/button_load_hl.png
Mods/MoreStorageColors/Reference/long_module2.png
Mods/MoreStorageColors/Reference/dream_cap_l.png
Mods/MoreStorageColors/Reference/storage_bay_chem.png
Mods/MoreStorageColors/Reference/water_test_container.png
Mods/MoreStorageColors/Reference/f_kz_18.png
Mods/MoreStorageColors/Reference/rat_m_m.png
Mods/MoreStorageColors/Reference/button_disabled_orange.png
Mods/MoreStorageColors/Reference/small_red_75.png
Mods/MoreStorageColors/Reference/desk_animations2_2.png
Mods/MoreStorageColors/Reference/Tmp_Icon_1.png
Mods/MoreStorageColors/Reference/beer_case_8.png
Mods/MoreStorageColors/Reference/scroll_bar_top_x2.png
Mods/MoreStorageColors/Reference/slip.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_zyanide_pill.png
Mods/MoreStorageColors/Reference/glass_button_0.png
Mods/MoreStorageColors/Reference/gf_salted_fish.png
Mods/MoreStorageColors/Reference/networkIcons_10.png
Mods/MoreStorageColors/Reference/dot.png
Mods/MoreStorageColors/Reference/xiaowo_ui_solid.png
Mods/MoreStorageColors/Reference/cv_amber.png
Mods/MoreStorageColors/Reference/guide1.png
Mods/MoreStorageColors/Reference/revolver_2.png
Mods/MoreStorageColors/Reference/sbattery80.png
Mods/MoreStorageColors/Reference/guide_davre_logo.png
Mods/MoreStorageColors/Reference/cv_kun_hammer.png
Mods/MoreStorageColors/Reference/50ml_red_25.png
Mods/MoreStorageColors/Reference/fce_cookware_pan.png
Mods/MoreStorageColors/Reference/recorder-Sheet_2.png
Mods/MoreStorageColors/Reference/battery20.png
Mods/MoreStorageColors/Reference/module_yellow.png
Mods/MoreStorageColors/Reference/xl_red_10.png
Mods/MoreStorageColors/Reference/outside_bottom.png
Mods/MoreStorageColors/Reference/small_blue_100.png
Mods/MoreStorageColors/Reference/handmade_pistol_2.png
Mods/MoreStorageColors/Reference/cv_generosity.png
Mods/MoreStorageColors/Reference/xiaowo_kitchen_pot.png
Mods/MoreStorageColors/Reference/card_wild_2.png
Mods/MoreStorageColors/Reference/desequencer.png
Mods/MoreStorageColors/Reference/scratch_2.png
Mods/MoreStorageColors/Reference/sbattery20.png
Mods/MoreStorageColors/Reference/button_up_blue.png
Mods/MoreStorageColors/Reference/small_red_50.png
Mods/MoreStorageColors/Reference/guide0.png
Mods/MoreStorageColors/Reference/border_bottom_resizable_button.png
Mods/MoreStorageColors/Reference/stanag_30_mod.png
Mods/MoreStorageColors/Reference/book5.png
Mods/MoreStorageColors/Reference/phone_button_normal.png
Mods/MoreStorageColors/Reference/cafeine_pill.png
Mods/MoreStorageColors/Reference/button_up.png
Mods/MoreStorageColors/Reference/printer_off.png
Mods/MoreStorageColors/Reference/slider_handle_h.png
Mods/MoreStorageColors/Reference/handmade_smg.png
Mods/MoreStorageColors/Reference/wanted_portait6.png
Mods/MoreStorageColors/Reference/guide_indicator_green.png
Mods/MoreStorageColors/Reference/general_button_2x_d.png
Mods/MoreStorageColors/Reference/light_pistol_ammo_p.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_fillerweed.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_1ml.png
Mods/MoreStorageColors/Reference/g_chat.png
Mods/MoreStorageColors/Reference/dream_cap.png
Mods/MoreStorageColors/Reference/sup_module.png
Mods/MoreStorageColors/Reference/evidence_box_u.png
Mods/MoreStorageColors/Reference/dropdown_button.png
Mods/MoreStorageColors/Reference/e1z_3flavouricecream_treat.png
Mods/MoreStorageColors/Reference/items_medical_11.png
Mods/MoreStorageColors/Reference/cassette1.png
Mods/MoreStorageColors/Reference/hydroponic_p_off.png
Mods/MoreStorageColors/Reference/card_rev_2.png
Mods/MoreStorageColors/Reference/today.png
Mods/MoreStorageColors/Reference/letter-incorrect2.png
Mods/MoreStorageColors/Reference/logo-correct.png
Mods/MoreStorageColors/Reference/shot_animation_body_4.png
Mods/MoreStorageColors/Reference/gf_rainbow_fillet.png
Mods/MoreStorageColors/Reference/items_weapons_ranged_antique_4.png
Mods/MoreStorageColors/Reference/nego_buttonx2-4Disabled.png
Mods/MoreStorageColors/Reference/battery10.png
Mods/MoreStorageColors/Reference/25ml_hl_10.png
Mods/MoreStorageColors/Reference/fat_meat.png
Mods/MoreStorageColors/Reference/cmd_point.png
Mods/MoreStorageColors/Reference/cropped-color-ok.png
Mods/MoreStorageColors/Reference/sci_card.png
Mods/MoreStorageColors/Reference/25ml_hl_75.png
Mods/MoreStorageColors/Reference/mouse_trap_f.png
Mods/MoreStorageColors/Reference/checkbox_2.png
Mods/MoreStorageColors/Reference/button_back_blue_m.png
Mods/MoreStorageColors/Reference/wine_berry.png
Mods/MoreStorageColors/Reference/shutter_button_sheet_1.png
Mods/MoreStorageColors/Reference/small_hl_25.png
Mods/MoreStorageColors/Reference/module_yellow_c.png
Mods/MoreStorageColors/Reference/large_red_75.png
Mods/MoreStorageColors/Reference/water_jug_full.png
Mods/MoreStorageColors/Reference/walker1_3.png
Mods/MoreStorageColors/Reference/combat_knife.png
Mods/MoreStorageColors/Reference/purifier.png
Mods/MoreStorageColors/Reference/cert2.png
Mods/MoreStorageColors/Reference/lower_p.png
Mods/MoreStorageColors/Reference/dossier_2.png
Mods/MoreStorageColors/Reference/cmd.png
Mods/MoreStorageColors/Reference/simple_backpack_large_military.png
Mods/MoreStorageColors/Reference/titan_armor10l.png
Mods/MoreStorageColors/Reference/nutrifruit_l.png
Mods/MoreStorageColors/Reference/depression_section_x2.png
Mods/MoreStorageColors/Reference/farm_module_l8.png
Mods/MoreStorageColors/Reference/farm_module_l4.png
Mods/MoreStorageColors/Reference/black_lamp_on_alt_2.png
Mods/MoreStorageColors/Reference/25ml_dark_50.png
Mods/MoreStorageColors/Reference/sbattery100.png
Mods/MoreStorageColors/Reference/bandage_small.png
Mods/MoreStorageColors/Reference/guide4.png
Mods/MoreStorageColors/Reference/sec_lamps_red.png
Mods/MoreStorageColors/Reference/grid_tan.png
Mods/MoreStorageColors/Reference/icon_toxicity.png
Mods/MoreStorageColors/Reference/e1z_luncheon_meat_canfood.png
Mods/MoreStorageColors/Reference/100ml_red_25.png
Mods/MoreStorageColors/Reference/guide3.png
Mods/MoreStorageColors/Reference/50ml_hl_100.png
Mods/MoreStorageColors/Reference/bottle5.png
Mods/MoreStorageColors/Reference/wine_label2.png
Mods/MoreStorageColors/Reference/100ml_hl_50.png
Mods/MoreStorageColors/Reference/e1z_greenicecream_treat.png
Mods/MoreStorageColors/Reference/cv_receiver.png
Mods/MoreStorageColors/Reference/battery_ext40.png
Mods/MoreStorageColors/Reference/portable_water_purifier.png
Mods/MoreStorageColors/Reference/border_right_scrollbar_button.png
Mods/MoreStorageColors/Reference/eng_card.png
Mods/MoreStorageColors/Reference/walker1_1.png
Mods/MoreStorageColors/Reference/fce_cookware_grill.png
Mods/MoreStorageColors/Reference/normal_red_75.png
Mods/MoreStorageColors/Reference/coin.png
Mods/MoreStorageColors/Reference/items_food_pilk_alt.png
Mods/MoreStorageColors/Reference/networkIcons_20.png
Mods/MoreStorageColors/Reference/heavy_smg.png
Mods/MoreStorageColors/Reference/example_letter1.png
Mods/MoreStorageColors/Reference/noun-beaker-7821176-FFFFFF.png
Mods/MoreStorageColors/Reference/electronic3.png
Mods/MoreStorageColors/Reference/light.png
Mods/MoreStorageColors/Reference/1ml_blue.png
Mods/MoreStorageColors/Reference/water_ration.png
Mods/MoreStorageColors/Reference/paper_buttons1.png
Mods/MoreStorageColors/Reference/f_kz_04.png
Mods/MoreStorageColors/Reference/paper_buttons_l_1.png
Mods/MoreStorageColors/Reference/strip4.png
Mods/MoreStorageColors/Reference/bicarsole_pill_bottle.png
Mods/MoreStorageColors/Reference/xl_hl_25.png
Mods/MoreStorageColors/Reference/desk_animations3_7.png
Mods/MoreStorageColors/Reference/xl_red_25.png
Mods/MoreStorageColors/Reference/module_yellow_t.png
Mods/MoreStorageColors/Reference/wanted_portait9.png
Mods/MoreStorageColors/Reference/wanted_portait2.png
Mods/MoreStorageColors/Reference/beer_case_6.png
Mods/MoreStorageColors/Reference/mini.png
Mods/MoreStorageColors/Reference/desk_animations1_4.png
Mods/MoreStorageColors/Reference/silhouette-animations-tenth-size_1.png
Mods/MoreStorageColors/Reference/toothpaste.png
Mods/MoreStorageColors/Reference/fce_dish_sour_spicy_fish.png
Mods/MoreStorageColors/Reference/items_food_bag_of_flour.png
Mods/MoreStorageColors/Reference/letter_incorrect3.png
Mods/MoreStorageColors/Reference/paper_buttons_close2.png
Mods/MoreStorageColors/Reference/moisture_farm_w.png
Mods/MoreStorageColors/Reference/UIBevel.png
Mods/MoreStorageColors/Reference/silhouette-animations-tenth-size_2.png
Mods/MoreStorageColors/Reference/farm_module6.png
Mods/MoreStorageColors/Reference/black_bottle.png
Mods/MoreStorageColors/Reference/printer_on.png
Mods/MoreStorageColors/Reference/button_generic_x2.png
Mods/MoreStorageColors/Reference/service_box.png
Mods/MoreStorageColors/Reference/mini_hl_25.png
Mods/MoreStorageColors/Reference/UISprite.png
Mods/MoreStorageColors/Reference/evidence_box.png
Mods/MoreStorageColors/Reference/module_black_5.png
Mods/MoreStorageColors/Reference/iconTrash.png
Mods/MoreStorageColors/Reference/exghange_logo.png
Mods/MoreStorageColors/Reference/large_dark_100.png
Mods/MoreStorageColors/Reference/guide5.png
Mods/MoreStorageColors/Reference/fce_dish_creamy_grilled_fish.png
Mods/MoreStorageColors/Reference/mini_red_50.png
Mods/MoreStorageColors/Reference/gf_processing_station.png
Mods/MoreStorageColors/Reference/desk_animations4_0.png
Mods/MoreStorageColors/Reference/intel_yellow.png
Mods/MoreStorageColors/Reference/border_top_button_x_select.png
Mods/MoreStorageColors/Reference/pitcher_blue_10.png
Mods/MoreStorageColors/Reference/module_yellow_l.png
Mods/MoreStorageColors/Reference/cropped-letter-correct.png
Mods/MoreStorageColors/Reference/midBandana_0.png
Mods/MoreStorageColors/Reference/juice_pouch_old.png
Mods/MoreStorageColors/Reference/large_water_bottle_full.png
Mods/MoreStorageColors/Reference/heavy_pistol_ammo_nl.png
Mods/MoreStorageColors/Reference/mouse_icon_right.png
Mods/MoreStorageColors/Reference/xl_dark_25.png
Mods/MoreStorageColors/Reference/5ml.png
Mods/MoreStorageColors/Reference/storage_bay_large1.png
Mods/MoreStorageColors/Reference/f_kz_17.png
Mods/MoreStorageColors/Reference/delete_save_button.png
Mods/MoreStorageColors/Reference/grey_2x.png
Mods/MoreStorageColors/Reference/outside_bottom_left.png
Mods/MoreStorageColors/Reference/border_bottom_resizable_button_down.png
Mods/MoreStorageColors/Reference/flashbang.png
Mods/MoreStorageColors/Reference/WAM_M9RatMeatSprite.png
Mods/MoreStorageColors/Reference/lottery_old.png
Mods/MoreStorageColors/Reference/hydroreed_seed.png
Mods/MoreStorageColors/Reference/pause_hl.png
Mods/MoreStorageColors/Reference/checkbox_checked.png
Mods/MoreStorageColors/Reference/black_lamp_on_1.png
Mods/MoreStorageColors/Reference/shot_animation_body_2.png
Mods/MoreStorageColors/Reference/printed_gun.png
Mods/MoreStorageColors/Reference/hydro_upgrade_kit.png
Mods/MoreStorageColors/Reference/case_back.png
Mods/MoreStorageColors/Reference/farm_module3.png
Mods/MoreStorageColors/Reference/key_box.png
Mods/MoreStorageColors/Reference/light_red.png
Mods/MoreStorageColors/Reference/outline_backpack.png
Mods/MoreStorageColors/Reference/cost_area.png
Mods/MoreStorageColors/Reference/xl_blue_10.png
Mods/MoreStorageColors/Reference/logo-complexe2.png
Mods/MoreStorageColors/Reference/trash_pile.png
Mods/MoreStorageColors/Reference/cropped-letter-incorrect3.png
Mods/MoreStorageColors/Reference/chip_color.png
Mods/MoreStorageColors/Reference/button_disabled_purple.png
Mods/MoreStorageColors/Reference/fillerweed_p.png
Mods/MoreStorageColors/Reference/gf_carbon_fiber_fishing_rod.png
Mods/MoreStorageColors/Reference/gf_dried_electric_eel.png
Mods/MoreStorageColors/Reference/client_counter-Sheet_3.png
Mods/MoreStorageColors/Reference/networkIcons_12.png
Mods/MoreStorageColors/Reference/normal_blue_90.png
Mods/MoreStorageColors/Reference/trashcan.png
Mods/MoreStorageColors/Reference/desk_animations1_2.png
Mods/MoreStorageColors/Reference/gf_horned_swordfish.png
Mods/MoreStorageColors/Reference/desk_animations4_2.png
Mods/MoreStorageColors/Reference/cert4.png
Mods/MoreStorageColors/Reference/farBackpack_1.png
Mods/MoreStorageColors/Reference/diplomatic.png
Mods/MoreStorageColors/Reference/printer_chip_pistol.png
Mods/MoreStorageColors/Reference/book1.png
Mods/MoreStorageColors/Reference/dark_cyan.png
Mods/MoreStorageColors/Reference/noun-flyer-7880108-FFFFFF.png
Mods/MoreStorageColors/Reference/cassette9.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_evaporator.png
Mods/MoreStorageColors/Reference/ore2.png
Mods/MoreStorageColors/Reference/f_kz_12.png
Mods/MoreStorageColors/Reference/pc_count-Sheet_1.png
Mods/MoreStorageColors/Reference/chemforge_b.png
Mods/MoreStorageColors/Reference/normal_hl_25.png
Mods/MoreStorageColors/Reference/100ml_dark_50.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_meal_cap.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_amenities_chem_scanner.png
Mods/MoreStorageColors/Reference/safes.png
Mods/MoreStorageColors/Reference/next_hl.png
Mods/MoreStorageColors/Reference/drain.png
Mods/MoreStorageColors/Reference/letter_incorrect4.png
Mods/MoreStorageColors/Reference/foil.png
Mods/MoreStorageColors/Reference/meal_cap_l.png
Mods/MoreStorageColors/Reference/gf_sardine.png
Mods/MoreStorageColors/Reference/storage_bay2.png
Mods/MoreStorageColors/Reference/gf_volcanic_fish_fillet.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_material_chemical3.png
Mods/MoreStorageColors/Reference/items_weapons_ranged_antique_1.png
Mods/MoreStorageColors/Reference/Checkmark.png
Mods/MoreStorageColors/Reference/xl_red_100.png
Mods/MoreStorageColors/Reference/injector_color2.png
Mods/MoreStorageColors/Reference/xl_hl_75.png
Mods/MoreStorageColors/Reference/client_counter-Sheet_0.png
Mods/MoreStorageColors/Reference/large.png
Mods/MoreStorageColors/Reference/kotton_plant_seed.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_tool_hydroponic_off.png
Mods/MoreStorageColors/Reference/bioprinter_system.png
Mods/MoreStorageColors/Reference/handle.png
Mods/MoreStorageColors/Reference/xl_dark_100.png
Mods/MoreStorageColors/Reference/cv_seat.png
Mods/MoreStorageColors/Reference/farBandana_2.png
Mods/MoreStorageColors/Reference/nightmare_dust.png
Mods/MoreStorageColors/Reference/cv_kun_gold.png
Mods/MoreStorageColors/Reference/50ml_blue_100.png
Mods/MoreStorageColors/Reference/card_chem.png
Mods/MoreStorageColors/Reference/wanted_portait7.png
Mods/MoreStorageColors/Reference/1ml_red.png
Mods/MoreStorageColors/Reference/ser_card.png
Mods/MoreStorageColors/Reference/25ml_blue_10.png
Mods/MoreStorageColors/Reference/previous.png
Mods/MoreStorageColors/Reference/tan.png
Mods/MoreStorageColors/Reference/shot_animation_wound_4.png
Mods/MoreStorageColors/Reference/25ml_blue_75.png
Mods/MoreStorageColors/Reference/injector_dam1.png
Mods/MoreStorageColors/Reference/water_jug.png
Mods/MoreStorageColors/Reference/revolver_short.png
Mods/MoreStorageColors/Reference/shotgun_stock.png
Mods/MoreStorageColors/Reference/border_blue.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_dream_cap_l.png
Mods/MoreStorageColors/Reference/purple_serum.png
Mods/MoreStorageColors/Reference/blue.png
Mods/MoreStorageColors/Reference/trashcan25.png
Mods/MoreStorageColors/Reference/gf_sealed_can.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_module_extractor.png
Mods/MoreStorageColors/Reference/fce_dish_blazing_grilled_fish.png
Mods/MoreStorageColors/Reference/iconGrowth.png
Mods/MoreStorageColors/Reference/chemical2.png
Mods/MoreStorageColors/Reference/paper_wanted.png
Mods/MoreStorageColors/Reference/blackmail.png
Mods/MoreStorageColors/Reference/water_recycler_full.png
Mods/MoreStorageColors/Reference/dropdown_2x.png
Mods/MoreStorageColors/Reference/uiback1L.png
Mods/MoreStorageColors/Reference/icons_water_merchant.png
Mods/MoreStorageColors/Reference/sentinel.png
Mods/MoreStorageColors/Reference/light_green.png
Mods/MoreStorageColors/Reference/module_red_l.png
Mods/MoreStorageColors/Reference/gf_transparent_membrane.png
Mods/MoreStorageColors/Reference/items_food_burger.png
Mods/MoreStorageColors/Reference/trashcan0.png
Mods/MoreStorageColors/Reference/rat_xxl_m_d.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_module_extractor_adv.png
Mods/MoreStorageColors/Reference/wine_label1.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_module_solvent_recovery.png
Mods/MoreStorageColors/Reference/general_button_2x_hl.png
Mods/MoreStorageColors/Reference/large_storage_bay.png
Mods/MoreStorageColors/Reference/simple_backpack_medium.png
Mods/MoreStorageColors/Reference/sample.png
Mods/MoreStorageColors/Reference/xiaowo_kitchen_stove_single.png
Mods/MoreStorageColors/Reference/button_rewind_disabled.png
Mods/MoreStorageColors/Reference/icon_euphoria.png
Mods/MoreStorageColors/Reference/icons_street_food_vendor.png
Mods/MoreStorageColors/Reference/strip0.png
Mods/MoreStorageColors/Reference/med.png
Mods/MoreStorageColors/Reference/rat_xxl_f_d.png
Mods/MoreStorageColors/Reference/wine_fermenting.png
Mods/MoreStorageColors/Reference/xiaowo_ratcolony_gene_stabilizer.png
Mods/MoreStorageColors/Reference/printer_chip1.png
Mods/MoreStorageColors/Reference/blender_b.png
Mods/MoreStorageColors/Reference/trashcan75.png
Mods/MoreStorageColors/Reference/border_right_scrollbar.png
Mods/MoreStorageColors/Reference/lower.png
Mods/MoreStorageColors/Reference/cassette5.png
Mods/MoreStorageColors/Reference/nutrichips_old.png
Mods/MoreStorageColors/Reference/normal_dark_10.png
Mods/MoreStorageColors/Reference/xl_dark_90.png
Mods/MoreStorageColors/Reference/midPuncho_1.png
Mods/MoreStorageColors/Reference/purifier_on.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_material_chemical1.png
Mods/MoreStorageColors/Reference/ul.png
Mods/MoreStorageColors/Reference/networkIcons_2.png
Mods/MoreStorageColors/Reference/middle_top.png
Mods/MoreStorageColors/Reference/intel_blue.png
Mods/MoreStorageColors/Reference/chem_finisher_b.png
Mods/MoreStorageColors/Reference/g_req.png
Mods/MoreStorageColors/Reference/jar_orange.png
Mods/MoreStorageColors/Reference/midGasmask_0.png
Mods/MoreStorageColors/Reference/machine_bay4.png
Mods/MoreStorageColors/Reference/small_blue_25.png
Mods/MoreStorageColors/Reference/pack_condom.png
Mods/MoreStorageColors/Reference/100ml_blue_25.png
Mods/MoreStorageColors/Reference/labeler_blue.png
Mods/MoreStorageColors/Reference/desk_animations3_1.png
Mods/MoreStorageColors/Reference/module_red_2_s.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_kotton_plant.png
Mods/MoreStorageColors/Reference/slider_button.png
Mods/MoreStorageColors/Reference/icons_hydroponic_farmer.png
Mods/MoreStorageColors/Reference/close_button_n.png
Mods/MoreStorageColors/Reference/midPuncho_3.png
Mods/MoreStorageColors/Reference/sec_slip.png
Mods/MoreStorageColors/Reference/farTrechCoat_0.png
Mods/MoreStorageColors/Reference/f_kz_08.png
Mods/MoreStorageColors/Reference/50ml_blue_75.png
Mods/MoreStorageColors/Reference/magnifying_glass.png
Mods/MoreStorageColors/Reference/pitcher.png
Mods/MoreStorageColors/Reference/f_kz_19.png
Mods/MoreStorageColors/Reference/gf_super_salt.png
Mods/MoreStorageColors/Reference/icons_apprentice.png
Mods/MoreStorageColors/Reference/letter_incorrect2.png
Mods/MoreStorageColors/Reference/start_top.png
Mods/MoreStorageColors/Reference/xl_blue_75.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_1.png
Mods/MoreStorageColors/Reference/wine_label3.png
Mods/MoreStorageColors/Reference/mini_dark_10.png
Mods/MoreStorageColors/Reference/pitcher_blue_90.png
Mods/MoreStorageColors/Reference/box_cutter.png
Mods/MoreStorageColors/Reference/noun-flag-7646741-FFFFFF.png
Mods/MoreStorageColors/Reference/midBackpack_1.png
Mods/MoreStorageColors/Reference/mp9_receiver.png
Mods/MoreStorageColors/Reference/button_disabled_red.png
Mods/MoreStorageColors/Reference/pitcher_hl_50.png
Mods/MoreStorageColors/Reference/scav.png
Mods/MoreStorageColors/Reference/cassette.png
Mods/MoreStorageColors/Reference/button_up_purple.png
Mods/MoreStorageColors/Reference/sbattery10.png
Mods/MoreStorageColors/Reference/default_hl.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_hydroreed_p.png
Mods/MoreStorageColors/Reference/mini_blue_75.png
Mods/MoreStorageColors/Reference/large_red_10.png
Mods/MoreStorageColors/Reference/slip2.png
Mods/MoreStorageColors/Reference/bottle4.png
Mods/MoreStorageColors/Reference/emoji_happy.png
Mods/MoreStorageColors/Reference/button.png
Mods/MoreStorageColors/Reference/dropdown_arrow.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_base.png
Mods/MoreStorageColors/Reference/card_pharma.png
Mods/MoreStorageColors/Reference/items_medical_10.png
Mods/MoreStorageColors/Reference/stanag_20_mod.png
Mods/MoreStorageColors/Reference/ore1.png
Mods/MoreStorageColors/Reference/wine_label_red.png
Mods/MoreStorageColors/Reference/topical_bandage_small.png
Mods/MoreStorageColors/Reference/25ml_dark_100.png
Mods/MoreStorageColors/Reference/mini_hl_75.png
Mods/MoreStorageColors/Reference/nutrifruit.png
Mods/MoreStorageColors/Reference/button_main_menu_n.png
Mods/MoreStorageColors/Reference/eng_p.png
Mods/MoreStorageColors/Reference/gun_part.png
Mods/MoreStorageColors/Reference/rat_s_f.png
Mods/MoreStorageColors/Reference/e1z_daguo_treat.png
Mods/MoreStorageColors/Reference/desk_animations4_3.png
Mods/MoreStorageColors/Reference/small_dark_100.png
Mods/MoreStorageColors/Reference/recorder_text_separators.png
Mods/MoreStorageColors/Reference/storage_bay_large_msc.png
Mods/MoreStorageColors/Reference/100ml_blue_50.png
Mods/MoreStorageColors/Reference/day.png
Mods/MoreStorageColors/Reference/desk_animations2_3.png
Mods/MoreStorageColors/Reference/normal_hl_10.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_dream_cap_s.png
Mods/MoreStorageColors/Reference/strip_used.png
Mods/MoreStorageColors/Reference/injector_base.png
Mods/MoreStorageColors/Reference/soda_2l.png
Mods/MoreStorageColors/Reference/slider_background.png
Mods/MoreStorageColors/Reference/100ml_hl_10.png
Mods/MoreStorageColors/Reference/small_water_bottle_full.png
Mods/MoreStorageColors/Reference/small_raw_meat.png
Mods/MoreStorageColors/Reference/calendar_top-Sheet_2.png
Mods/MoreStorageColors/Reference/normal_dark_75.png
Mods/MoreStorageColors/Reference/example_logo1.png
Mods/MoreStorageColors/Reference/clientele-Sheet_0.png
Mods/MoreStorageColors/Reference/Tmp_Icon_0.png
Mods/MoreStorageColors/Reference/50ml_hl_10.png
Mods/MoreStorageColors/Reference/large_red_100.png
Mods/MoreStorageColors/Reference/meat_scrap.png
Mods/MoreStorageColors/Reference/normal.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_5.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_evaporator_b.png
Mods/MoreStorageColors/Reference/battery80.png
Mods/MoreStorageColors/Reference/household_sign.png
Mods/MoreStorageColors/Reference/example_chip2.png
Mods/MoreStorageColors/Reference/networkIcons_17.png
Mods/MoreStorageColors/Reference/border_top_button_x.png
Mods/MoreStorageColors/Reference/recharger3.png
Mods/MoreStorageColors/Reference/machine_bay2.png
Mods/MoreStorageColors/Reference/Back_White.png
Mods/MoreStorageColors/Reference/wine_label5.png
Mods/MoreStorageColors/Reference/blue_serum.png
Mods/MoreStorageColors/Reference/heavy_duty_hull.png
Mods/MoreStorageColors/Reference/networkIcons_23.png
Mods/MoreStorageColors/Reference/Check.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_tool_hydroponic_p_off.png
Mods/MoreStorageColors/Reference/down_d.png
Mods/MoreStorageColors/Reference/paper_buttons_close4.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_dream_cap_p.png
Mods/MoreStorageColors/Reference/projector.png
Mods/MoreStorageColors/Reference/g_phone.png
Mods/MoreStorageColors/Reference/farm_module_l7.png
Mods/MoreStorageColors/Reference/pitcher_red_10.png
Mods/MoreStorageColors/Reference/gene_edit_probe.png
Mods/MoreStorageColors/Reference/round88.png
Mods/MoreStorageColors/Reference/mini_blue_90.png
Mods/MoreStorageColors/Reference/med_box.png
Mods/MoreStorageColors/Reference/black_lamp_on_3.png
Mods/MoreStorageColors/Reference/up_hl.png
Mods/MoreStorageColors/Reference/50ml_dark_50.png
Mods/MoreStorageColors/Reference/dialogue_bubble2_pink_pointed.png
Mods/MoreStorageColors/Reference/grid_blue.png
Mods/MoreStorageColors/Reference/injector_pink_alt.png
Mods/MoreStorageColors/Reference/module_white.png
Mods/MoreStorageColors/Reference/stun_gun.png
Mods/MoreStorageColors/Reference/kotton_fabric.png
Mods/MoreStorageColors/Reference/water_off.png
Mods/MoreStorageColors/Reference/networkIcons_19.png
Mods/MoreStorageColors/Reference/sec_card_2.png
Mods/MoreStorageColors/Reference/100ml_hl_100.png
Mods/MoreStorageColors/Reference/berry_2under2.png
Mods/MoreStorageColors/Reference/exchange_directive.png
Mods/MoreStorageColors/Reference/core.png
Mods/MoreStorageColors/Reference/outside_top_left.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_fanta_pill.png
Mods/MoreStorageColors/Reference/rev_2.png
Mods/MoreStorageColors/Reference/storage_bay_gun.png
Mods/MoreStorageColors/Reference/trashcan50.png
Mods/MoreStorageColors/Reference/turbo_booster.png
Mods/MoreStorageColors/Reference/cropped-logo-simple.png
Mods/MoreStorageColors/Reference/grid_white.png
Mods/MoreStorageColors/Reference/welfare_program.png
Mods/MoreStorageColors/Reference/gf_yellow_croaker_fillet.png
Mods/MoreStorageColors/Reference/crowbar.png
Mods/MoreStorageColors/Reference/alert.png
Mods/MoreStorageColors/Reference/hand_carbon.png
Mods/MoreStorageColors/Reference/engine.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_amenities_chem_scanner2.png
Mods/MoreStorageColors/Reference/button_load.png
Mods/MoreStorageColors/Reference/cassette_player.png
Mods/MoreStorageColors/Reference/xl_red_90.png
Mods/MoreStorageColors/Reference/pill2.png
Mods/MoreStorageColors/Reference/e1z_redox_candrink.png
Mods/MoreStorageColors/Reference/desk_animations4_6.png
Mods/MoreStorageColors/Reference/stun_gun_2.png
Mods/MoreStorageColors/Reference/recharger_o.png
Mods/MoreStorageColors/Reference/grid_red.png
Mods/MoreStorageColors/Reference/normal_blue_25.png
Mods/MoreStorageColors/Reference/fce_fishing_kitchen.png
Mods/MoreStorageColors/Reference/glass_shard_shiv.png
Mods/MoreStorageColors/Reference/nl_ammo_box_packed.png
Mods/MoreStorageColors/Reference/card_brewer.png
Mods/MoreStorageColors/Reference/sbattery60.png
Mods/MoreStorageColors/Reference/evaporator.png
Mods/MoreStorageColors/Reference/phone_button_press.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_nightmare_cap_p.png
Mods/MoreStorageColors/Reference/e1z_beansfish_canfood.png
Mods/MoreStorageColors/Reference/walker1_5.png
Mods/MoreStorageColors/Reference/battery_ext10.png
Mods/MoreStorageColors/Reference/glock_receiver_2.png
Mods/MoreStorageColors/Reference/inventor_main.png
Mods/MoreStorageColors/Reference/med_bottle_orange.png
Mods/MoreStorageColors/Reference/small_hl_75.png
Mods/MoreStorageColors/Reference/addiction_narcotic.png
Mods/MoreStorageColors/Reference/injector_red_2.png
Mods/MoreStorageColors/Reference/combat_machete.png
Mods/MoreStorageColors/Reference/stanag_10_mod.png
Mods/MoreStorageColors/Reference/book1_2.png
Mods/MoreStorageColors/Reference/tutorial_book.png
Mods/MoreStorageColors/Reference/turbo_booster_adv_charging.png
Mods/MoreStorageColors/Reference/border_outside_top_rounded_crop.png
Mods/MoreStorageColors/Reference/midPuncho_0.png
Mods/MoreStorageColors/Reference/small_red_100.png
Mods/MoreStorageColors/Reference/stanag_10.png
Mods/MoreStorageColors/Reference/blank_card_wip.png
Mods/MoreStorageColors/Reference/50ml_dark_25.png
Mods/MoreStorageColors/Reference/improvised_stunprod.png
Mods/MoreStorageColors/Reference/shot_animation_wound_2.png
Mods/MoreStorageColors/Reference/jar_blue.png
Mods/MoreStorageColors/Reference/perk_p.png
Mods/MoreStorageColors/Reference/pillbottle2.png
Mods/MoreStorageColors/Reference/1ml.png
Mods/MoreStorageColors/Reference/simple_backpack.png
Mods/MoreStorageColors/Reference/storage_bay_large.png
Mods/MoreStorageColors/Reference/xl_hl_100.png
Mods/MoreStorageColors/Reference/guide6.png
Mods/MoreStorageColors/Reference/permit_gun_2.png
Mods/MoreStorageColors/Reference/ocutol_pill_bottle.png
Mods/MoreStorageColors/Reference/satchel.png
Mods/MoreStorageColors/Reference/furnace.png
Mods/MoreStorageColors/Reference/icons_organ_trader.png
Mods/MoreStorageColors/Reference/pill1.png
Mods/MoreStorageColors/Reference/sec_seal_2x.png
Mods/MoreStorageColors/Reference/case.png
Mods/MoreStorageColors/Reference/button_disabled.png
Mods/MoreStorageColors/Reference/inside.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_chem_finisher_b.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_25ml.png
Mods/MoreStorageColors/Reference/up_d.png
Mods/MoreStorageColors/Reference/box_nail.png
Mods/MoreStorageColors/Reference/phone_button1p.png
Mods/MoreStorageColors/Reference/pitcher_red_50.png
Mods/MoreStorageColors/Reference/page2.png
Mods/MoreStorageColors/Reference/nutrichips.png
Mods/MoreStorageColors/Reference/farTrechCoat_1.png
Mods/MoreStorageColors/Reference/paper_buttons_l_2.png
Mods/MoreStorageColors/Reference/nego_button_Pressed.png
Mods/MoreStorageColors/Reference/scrap_metal.png
Mods/MoreStorageColors/Reference/aloe_vera.png
Mods/MoreStorageColors/Reference/desk_animations4_5.png
Mods/MoreStorageColors/Reference/water_tablet_container.png
Mods/MoreStorageColors/Reference/walker1_0.png
Mods/MoreStorageColors/Reference/kotton_plant_s.png
Mods/MoreStorageColors/Reference/playtest_button.png
Mods/MoreStorageColors/Reference/bag.png
Mods/MoreStorageColors/Reference/cheese.png
Mods/MoreStorageColors/Reference/e1z_smallbucketinstantnoodles_fastfood.png
Mods/MoreStorageColors/Reference/e1z_pickle_canfood.png
Mods/MoreStorageColors/Reference/100ml_blue_90.png
Mods/MoreStorageColors/Reference/normal_blue_75.png
Mods/MoreStorageColors/Reference/shotgun_grip.png
Mods/MoreStorageColors/Reference/cross.png
Mods/MoreStorageColors/Reference/large_dark_50.png
Mods/MoreStorageColors/Reference/recharger_g.png
Mods/MoreStorageColors/Reference/rat_l_m_d.png
Mods/MoreStorageColors/Reference/networkIcons_18.png
Mods/MoreStorageColors/Reference/gen_button_2x_p.png
Mods/MoreStorageColors/Reference/playd.png
Mods/MoreStorageColors/Reference/farPuncho_0.png
Mods/MoreStorageColors/Reference/smoke_grenade_green.png
Mods/MoreStorageColors/Reference/phone_red_button_p.png
Mods/MoreStorageColors/Reference/default.png
Mods/MoreStorageColors/Reference/chem_module.png
Mods/MoreStorageColors/Reference/networkIcons_31.png
Mods/MoreStorageColors/Reference/hydroponic_off_2.png
Mods/MoreStorageColors/Reference/example_letter2.png
Mods/MoreStorageColors/Reference/logo_checker.png
Mods/MoreStorageColors/Reference/injector_blue.png
Mods/MoreStorageColors/Reference/e1z_spicysticks_treat.png
Mods/MoreStorageColors/Reference/faucet1.png
Mods/MoreStorageColors/Reference/light_yellow.png
Mods/MoreStorageColors/Reference/xl_hl_90.png
Mods/MoreStorageColors/Reference/card.png
Mods/MoreStorageColors/Reference/baseball_bat.png
Mods/MoreStorageColors/Reference/bell_button_sheet_0.png
Mods/MoreStorageColors/Reference/beer_case_4.png
Mods/MoreStorageColors/Reference/f_kz_21.png
Mods/MoreStorageColors/Reference/large_blue_10.png
Mods/MoreStorageColors/Reference/permit_gun_1.png
Mods/MoreStorageColors/Reference/eng_module.png
Mods/MoreStorageColors/Reference/mini_blue_50.png
Mods/MoreStorageColors/Reference/fixalin_pill_bottle.png
Mods/MoreStorageColors/Reference/gf_dried_sardine.png
Mods/MoreStorageColors/Reference/element_bg.png
Mods/MoreStorageColors/Reference/wine_label4.png
Mods/MoreStorageColors/Reference/rev.png
Mods/MoreStorageColors/Reference/InputFieldBackground.png
Mods/MoreStorageColors/Reference/pouch_hydration.png
Mods/MoreStorageColors/Reference/uiback1LTan_embossed.png
Mods/MoreStorageColors/Reference/uibacktop1.png
Mods/MoreStorageColors/Reference/flux_agent.png
Mods/MoreStorageColors/Reference/clipboard.png
Mods/MoreStorageColors/Reference/desk_animations2_6.png
Mods/MoreStorageColors/Reference/emoji_angry.png
Mods/MoreStorageColors/Reference/machine_bay_ext.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_medical_reducing_agent.png
Mods/MoreStorageColors/Reference/strip2.png
Mods/MoreStorageColors/Reference/shotgun.png
Mods/MoreStorageColors/Reference/mirage_generator.png
Mods/MoreStorageColors/Reference/large_blue_75.png
Mods/MoreStorageColors/Reference/large_dark_10.png
Mods/MoreStorageColors/Reference/vending.png
Mods/MoreStorageColors/Reference/desk_animations1_0.png
Mods/MoreStorageColors/Reference/xl_hl_50.png
Mods/MoreStorageColors/Reference/intel_tan.png
Mods/MoreStorageColors/Reference/perk_h.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_hydroponic_off.png
Mods/MoreStorageColors/Reference/walker1_6.png
Mods/MoreStorageColors/Reference/pill_bottle.png
Mods/MoreStorageColors/Reference/snitch.png
Mods/MoreStorageColors/Reference/purifier_jug.png
Mods/MoreStorageColors/Reference/text_background_in.png
Mods/MoreStorageColors/Reference/gf_heat_gland.png
Mods/MoreStorageColors/Reference/items_medical_17.png
Mods/MoreStorageColors/Reference/100ml_dark_90.png
Mods/MoreStorageColors/Reference/additionc_alcohol.png
Mods/MoreStorageColors/Reference/blue_close_button.png
Mods/MoreStorageColors/Reference/small_blue_75.png
Mods/MoreStorageColors/Reference/50ml.png
Mods/MoreStorageColors/Reference/cassette6.png
Mods/MoreStorageColors/Reference/phone_button_hover.png
Mods/MoreStorageColors/Reference/cert1.png
Mods/MoreStorageColors/Reference/paper_buttons_screen_1.png
Mods/MoreStorageColors/Reference/rat_s_f_d.png
Mods/MoreStorageColors/Reference/dust.png
Mods/MoreStorageColors/Reference/chemical1.png
Mods/MoreStorageColors/Reference/desk_animations1_3.png
Mods/MoreStorageColors/Reference/slider_handle_n.png
Mods/MoreStorageColors/Reference/1ml_dark.png
Mods/MoreStorageColors/Reference/cv_robot.png
Mods/MoreStorageColors/Reference/meal_cap.png
Mods/MoreStorageColors/Reference/logo_incorrect.png
Mods/MoreStorageColors/Reference/card_wild.png
Mods/MoreStorageColors/Reference/gene_edit_probe_box.png
Mods/MoreStorageColors/Reference/badge.png
Mods/MoreStorageColors/Reference/slider_bar_2x.png
Mods/MoreStorageColors/Reference/mini_dark_100.png
Mods/MoreStorageColors/Reference/injector_dam2.png
Mods/MoreStorageColors/Reference/lottery_teared.png
Mods/MoreStorageColors/Reference/small_dark_25.png
Mods/MoreStorageColors/Reference/illegal_yeast.png
Mods/MoreStorageColors/Reference/shot_animation_body_5.png
Mods/MoreStorageColors/Reference/shotgun_2.png
Mods/MoreStorageColors/Reference/icons_chemist.png
Mods/MoreStorageColors/Reference/pouch.png
Mods/MoreStorageColors/Reference/canister_nerve.png
Mods/MoreStorageColors/Reference/alarm_system_b.png
Mods/MoreStorageColors/Reference/Serial.png
Mods/MoreStorageColors/Reference/normal_hl_100.png
Mods/MoreStorageColors/Reference/jar_empty.png
Mods/MoreStorageColors/Reference/gf_purification_sac.png
Mods/MoreStorageColors/Reference/bm_p.png
Mods/MoreStorageColors/Reference/button_glass.png
Mods/MoreStorageColors/Reference/networkIcons_28.png
Mods/MoreStorageColors/Reference/close_normal.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_heating_plate.png
Mods/MoreStorageColors/Reference/client_counter-Sheet_1.png
Mods/MoreStorageColors/Reference/kotton_plant_l.png
Mods/MoreStorageColors/Reference/flashlight3.png
Mods/MoreStorageColors/Reference/heavy_pistol_ammo_p.png
Mods/MoreStorageColors/Reference/midTrechCoat_3.png
Mods/MoreStorageColors/Reference/xiaowo_kitchen_stove_double.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_material_chemical2.png
Mods/MoreStorageColors/Reference/finalelog.png
Mods/MoreStorageColors/Reference/50ml_blue_25.png
Mods/MoreStorageColors/Reference/gf_purifier_fish_meat.png
Mods/MoreStorageColors/Reference/fireaxe.png
Mods/MoreStorageColors/Reference/icons_rock_bottom.png
Mods/MoreStorageColors/Reference/slot.png
Mods/MoreStorageColors/Reference/xiaowo_ratcolony_veterinary_serum.png
Mods/MoreStorageColors/Reference/rat_l_f.png
Mods/MoreStorageColors/Reference/magnum_revolver.png
Mods/MoreStorageColors/Reference/gf_swordfish_rostrum.png
Mods/MoreStorageColors/Reference/grey2_2x.png
Mods/MoreStorageColors/Reference/large_red_50.png
Mods/MoreStorageColors/Reference/items_medical_16.png
Mods/MoreStorageColors/Reference/module_yellow_2_h.png
Mods/MoreStorageColors/Reference/desk_animations1_6.png
Mods/MoreStorageColors/Reference/ser_module.png
Mods/MoreStorageColors/Reference/tab.png
Mods/MoreStorageColors/Reference/li_eat_snackbar.png
Mods/MoreStorageColors/Reference/electronic2.png
Mods/MoreStorageColors/Reference/gf_conductive_tendon.png
Mods/MoreStorageColors/Reference/clipboard_2.png
Mods/MoreStorageColors/Reference/combat_knife_2.png
Mods/MoreStorageColors/Reference/revolver.png
Mods/MoreStorageColors/Reference/visual_book.png
Mods/MoreStorageColors/Reference/berry_3over.png
Mods/MoreStorageColors/Reference/letter-incorrect1.png
Mods/MoreStorageColors/Reference/book2.png
Mods/MoreStorageColors/Reference/outside_crop.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_bloomberry_p2.png
Mods/MoreStorageColors/Reference/battery_ext100.png
Mods/MoreStorageColors/Reference/rat_l_f_d.png
Mods/MoreStorageColors/Reference/mentor_contract.png
Mods/MoreStorageColors/Reference/normal_red_25.png
Mods/MoreStorageColors/Reference/farBandana_0.png
Mods/MoreStorageColors/Reference/logo_incorrect4.png
Mods/MoreStorageColors/Reference/normal_red_90.png
Mods/MoreStorageColors/Reference/networkIcons_16.png
Mods/MoreStorageColors/Reference/lunch_box.png
Mods/MoreStorageColors/Reference/sandal_weapon.png
Mods/MoreStorageColors/Reference/book3.png
Mods/MoreStorageColors/Reference/paper_buttons_screen_0.png
Mods/MoreStorageColors/Reference/networkIcons_24.png
Mods/MoreStorageColors/Reference/border_black.png
Mods/MoreStorageColors/Reference/tazer_2.png
Mods/MoreStorageColors/Reference/sawyer_crew.png
Mods/MoreStorageColors/Reference/cassette10.png
Mods/MoreStorageColors/Reference/items_red_mushroom.png
Mods/MoreStorageColors/Reference/nego_buttonx2-1Normal.png
Mods/MoreStorageColors/Reference/100ml_red_75.png
Mods/MoreStorageColors/Reference/injector_pink.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_tool2_heating_plate_b.png
Mods/MoreStorageColors/Reference/50ml_dark_75.png
Mods/MoreStorageColors/Reference/normal_blue_50.png
Mods/MoreStorageColors/Reference/midBandana_2.png
Mods/MoreStorageColors/Reference/storage_bay.png
Mods/MoreStorageColors/Reference/recharger4.png
Mods/MoreStorageColors/Reference/sec_card.png
Mods/MoreStorageColors/Reference/farm_module2.png
Mods/MoreStorageColors/Reference/water_recycler_empty.png
Mods/MoreStorageColors/Reference/emoji_skull.png
Mods/MoreStorageColors/Reference/metal_scanner.png
Mods/MoreStorageColors/Reference/e1z_bakedbeans_canfood.png
Mods/MoreStorageColors/Reference/cv_eclipse.png
Mods/MoreStorageColors/Reference/networkIcons_11.png
Mods/MoreStorageColors/Reference/cropped-seal-correct.png
Mods/MoreStorageColors/Reference/dream_dust.png
Mods/MoreStorageColors/Reference/50ml_blue_90.png
Mods/MoreStorageColors/Reference/smuggler_bay.png
Mods/MoreStorageColors/Reference/logo-simple.png
Mods/MoreStorageColors/Reference/box_ok.png
Mods/MoreStorageColors/Reference/dropdown_option.png
Mods/MoreStorageColors/Reference/cmd_module.png
Mods/MoreStorageColors/Reference/tooltip_2x.png
Mods/MoreStorageColors/Reference/box_tea_bag.png
Mods/MoreStorageColors/Reference/glass_button_2.png
Mods/MoreStorageColors/Reference/event_top.png
Mods/MoreStorageColors/Reference/peanut_butter_blue.png
Mods/MoreStorageColors/Reference/fce_cookware_pot.png
Mods/MoreStorageColors/Reference/general_button_2x_c.png
Mods/MoreStorageColors/Reference/flashlight.png
Mods/MoreStorageColors/Reference/gf_makeshift_fishing_rod.png
Mods/MoreStorageColors/Reference/green_bar.png
Mods/MoreStorageColors/Reference/body.png
Mods/MoreStorageColors/Reference/gf_dried_glass_fish.png
Mods/MoreStorageColors/Reference/wanted_portait4.png
Mods/MoreStorageColors/Reference/small_smoke.png
Mods/MoreStorageColors/Reference/box.png
Mods/MoreStorageColors/Reference/peanut_butter_purple.png
Mods/MoreStorageColors/Reference/nightmare_cap_p.png
Mods/MoreStorageColors/Reference/bottle3.png
Mods/MoreStorageColors/Reference/elements_outer.png
Mods/MoreStorageColors/Reference/guide_section.png
Mods/MoreStorageColors/Reference/nuts_metal_pile.png
Mods/MoreStorageColors/Reference/f_kz_09.png
Mods/MoreStorageColors/Reference/armored_hull.png
Mods/MoreStorageColors/Reference/mini_red_25.png
Mods/MoreStorageColors/Reference/icons_rancher.png
Mods/MoreStorageColors/Reference/top_button.png
Mods/MoreStorageColors/Reference/stick_grenade.png
Mods/MoreStorageColors/Reference/gf_oil_packed_fish.png
Mods/MoreStorageColors/Reference/border_outside_top_rounded_new.png
Mods/MoreStorageColors/Reference/play_c.png
Mods/MoreStorageColors/Reference/nuts.png
Mods/MoreStorageColors/Reference/button_sec.png
Mods/MoreStorageColors/Reference/small_hl_50.png
Mods/MoreStorageColors/Reference/wanted_portait8.png
Mods/MoreStorageColors/Reference/sec.png
Mods/MoreStorageColors/Reference/paper_towel.png
Mods/MoreStorageColors/Reference/gf_chili_pepper.png
Mods/MoreStorageColors/Reference/rat_xl_m_d.png
Mods/MoreStorageColors/Reference/sbattery40.png
Mods/MoreStorageColors/Reference/25ml_blue_90.png
Mods/MoreStorageColors/Reference/button_confront.png
Mods/MoreStorageColors/Reference/25ml_red_75.png
Mods/MoreStorageColors/Reference/node_s.png
Mods/MoreStorageColors/Reference/button_bargain-Sheet_2.png
Mods/MoreStorageColors/Reference/battery_ext60.png
Mods/MoreStorageColors/Reference/25ml_blue_50.png
Mods/MoreStorageColors/Reference/example_logo2.png
Mods/MoreStorageColors/Reference/med3.png
Mods/MoreStorageColors/Reference/xiaowo_beta_chem_chem_container_50ml.png
Mods/MoreStorageColors/Reference/text_box.png
Mods/MoreStorageColors/Reference/tape_anim-Sheet_2.png
Mods/MoreStorageColors/Reference/midTrechCoat_0.png
Mods/MoreStorageColors/Reference/dark_yellow.png
Mods/MoreStorageColors/Reference/checkbox_hover.png
Mods/MoreStorageColors/Reference/xl_blue_50.png
Mods/MoreStorageColors/Reference/mini_dark_75.png
Mods/MoreStorageColors/Reference/salve.png
Mods/MoreStorageColors/Reference/100ml_blue_10.png
Mods/MoreStorageColors/Reference/xiaowo_ratcolony_unstable_mutagen.png
Mods/MoreStorageColors/Reference/button_bargain-Sheet_0.png
Mods/MoreStorageColors/Reference/recorder-Sheet_1.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_bloomberry_p.png
Mods/MoreStorageColors/Reference/playtest_button_feedback.png
Mods/MoreStorageColors/Reference/border_green.png
Mods/MoreStorageColors/Reference/items_sign_1.png
Mods/MoreStorageColors/Reference/farBackpack_3.png
Mods/MoreStorageColors/Reference/pillbottle1.png
Mods/MoreStorageColors/Reference/pitcher_blue_50.png
Mods/MoreStorageColors/Reference/kitchen_knife.png
Mods/MoreStorageColors/Reference/border_top_button_x_inverted.png
Mods/MoreStorageColors/Reference/saving.png
Mods/MoreStorageColors/Reference/energy_credit.png
Mods/MoreStorageColors/Reference/xl_dark_75.png
Mods/MoreStorageColors/Reference/moisture_farm_r.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_nutrifruit_p.png
Mods/MoreStorageColors/Reference/large_hl_25.png
Mods/MoreStorageColors/Reference/shot_animation_wound_0.png
Mods/MoreStorageColors/Reference/module_green_t.png
Mods/MoreStorageColors/Reference/superyeast.png
Mods/MoreStorageColors/Reference/networkIcons_13.png
Mods/MoreStorageColors/Reference/items_medical_15.png
Mods/MoreStorageColors/Reference/normal_hl_50.png
Mods/MoreStorageColors/Reference/uiback1LDarkTan.png
Mods/MoreStorageColors/Reference/degenerative_osteoarthritis.png
Mods/MoreStorageColors/Reference/unknown.png
Mods/MoreStorageColors/Reference/battery_bar.png
Mods/MoreStorageColors/Reference/networkIcons_21.png
Mods/MoreStorageColors/Reference/med_p.png
Mods/MoreStorageColors/Reference/gf_dried_small_yellow_croaker.png
Mods/MoreStorageColors/Reference/rat_m_m_d.png
Mods/MoreStorageColors/Reference/recorder-Sheet_0.png
Mods/MoreStorageColors/Reference/dark_orange.png
Mods/MoreStorageColors/Reference/feces_small.png
Mods/MoreStorageColors/Reference/green.png
Mods/MoreStorageColors/Reference/expedition_box.png
Mods/MoreStorageColors/Reference/xiaowo_beta_hydro_ship_hydroponic_system.png
Mods/MoreStorageColors/Reference/bookBack.png
Mods/MoreStorageColors/Reference/large_blue_50.png
Mods/MoreStorageColors/Reference/100ml_dark_100.png
Mods/MoreStorageColors/Reference/f_kz_06.png
Mods/MoreStorageColors/Reference/letter_incorrect5.png
Mods/MoreStorageColors/Reference/rat_xxl_f.png
Mods/MoreStorageColors/Reference/overlay_box.png
Mods/MoreStorageColors/Reference/organ_transport_box.png
Mods/MoreStorageColors/Reference/mini_dark_50.png
Mods/MoreStorageColors/Reference/recharger0.png
Mods/MoreStorageColors/Reference/fillerweed_s.png
Mods/MoreStorageColors/Reference/revolver_long.png
Mods/MoreStorageColors/Reference/large_hl_100.png
Mods/MoreStorageColors/Reference/e1z_boxinstantnoodles_fastfood.png
Mods/MoreStorageColors/Reference/bottle1.png
Mods/MoreStorageColors/Reference/logo_incorrect2.png
Mods/MoreStorageColors/Reference/g_setup.png
Mods/MoreStorageColors/Reference/pause_d.png
Mods/MoreStorageColors/Reference/morsel.png
Mods/MoreStorageColors/Reference/module_green_c.png
Mods/MoreStorageColors/Reference/makeshift_hull.png
Mods/MoreStorageColors/Reference/injector_pouch.png
Mods/MoreStorageColors/Reference/indicator_frame.png
Mods/MoreStorageColors/Reference/printer_chip5.png
Mods/MoreStorageColors/Textures/household/storage_bay_large.png
Mods/MoreStorageColors/Textures/household/storage_bay.png
Mods/MoreStorageColors/Textures/substance/storage_bay_large.png
Mods/MoreStorageColors/Textures/substance/storage_bay.png
Mods/MoreStorageColors/Textures/medical/storage_bay_large.png
Mods/MoreStorageColors/Textures/medical/storage_bay.png
Mods/MoreStorageColors/Textures/food/storage_bay_large.png
Mods/MoreStorageColors/Textures/food/storage_bay.png
Mods/MoreStorageColors/Textures/material/storage_bay_large.png
Mods/MoreStorageColors/Textures/material/storage_bay.png
Mods/MoreStorageColors/Textures/weapon/storage_bay_large.png
Mods/MoreStorageColors/Textures/weapon/storage_bay.png
Mods/MoreStorageColors/Textures/water/storage_bay_large.png
Mods/MoreStorageColors/Textures/water/storage_bay.png
```


## E08 RimCrow 技术栈和游戏绑定

**文件：`pyproject.toml`**；SHA256：`62f337ed5fe9ed5350de584c5f2a99e2385d2dcb4ab0649fca2a0d5d39ae71aa`

```text
00001 [project]
00002 name = "rimcrow"
00003 version = "0.1.0"
00004 description = "Add your description here"
00005 readme = "README.md"
00006 requires-python = ">=3.11"
00007 dependencies = [
00008     "beautifulsoup4>=4.14.3",
00009     "certifi>=2026.1.4",
00010     "colorlog>=6.10.1",
00011     "icecream>=2.1.8",
00012     "json-repair>=0.58.0",
00013     "keyring>=25.7.0",
00014     "litellm>=1.81.14",
00015     "lxml>=6.0.2",
00016     "nuitka>=2.8.10",
00017     "openai>=2.15.0",
00018     "packaging>=25.0",
00019     "pathspec>=1.0.4",
00020     "peewee>=3.18.3",
00021     "pillow>=12.0.0",
00022     "pip-system-certs>=5.3",
00023     "psutil>=7.2.2",
00024     "pydantic>=2.12.5",
00025     "pyinstaller>=6.18.0",
00026     "pytest>=9.0.3",
00027     "python-dateutil>=2.9.0.post0",
00028     "python-docx>=1.2.0",
00029     "pywebview>=6.1",
00030     "pywin32>=311; sys_platform == 'win32'",
00031     "reportlab>=4.5.1",
00032     "requests>=2.32.5",
00033     "send2trash>=2.0.0",
00034     "setuptools<70.0.0",
00035     "steam-vdf>=0.1.10",
00036     "zstandard>=0.25.0",
00037 ]
00038 
```
**文件：`backend/scanner/mod_scanner.py`**；SHA256：`28398458eb8d6e5a19baa2e5b24bb46cfca9b090535fb1b850113c0a58bfc1d9`

```text
00530         payload.setdefault('metrics', {})
00531         if emit_events:
00532             EventBus.emit('scan-complete', payload)
00533 
00534     def _process_single_mod( self, mod_path, is_dlc_dir, existing_snapshots, dlc_parser: DLCParser | None, forced_update=False, size_check_override: bool | None = None, size_check_paths: set[str] | None = None, stats: dict | None = None, scan_details: dict | None = None ):
00535         """
00536         处理单个 Mod 的纯函数逻辑。
00537         返回: Mod数据字典 或 None(无效) 或 {'_skipped': True, 'package_id': ...}
00538         """
00539         mod_path = normalize_path_for_storage(mod_path)
00540         try:
00541             about_state = ModAnalyzer.resolve_mod_about_state(mod_path, cleanup_dual_files=True)
00542         except OSError as e:
00543             logger.warning(f"清理重复 About 文件失败：{mod_path}，错误：{e}")
00544             about_state = ModAnalyzer.resolve_mod_about_state(mod_path, cleanup_dual_files=False)
00545         about_file = about_state.resolved_path
00546         is_disabled = about_state.is_disabled
00547         if not about_file and not is_dlc_dir: return None # 既不是 DLC 也没有 About.xml，无效
00548         
00549         # 检查 mtime
00550         try:
```
**文件：`backend/managers/mgr_load_order.py`**；SHA256：`3452aee88f90a544fa572ebd58c0a3dc3a6cfb66ad67ea9ea29b68e7279ff4d9`

```text
00068 class LoadOrderManager:
00069     """
00070     负责管理 ModsConfig.xml (加载顺序)
00071     功能：读取当前激活列表、保存列表、自动备份
00072     1. 当天：保留所有操作备份。
00073     2. 过去：只保留当天的最后一份。
00074     3. 过期：删除超过 30 天的备份（除了最后一份）。
00075     4. 兜底：永远保留最新的一份备份。
00076     """
00077     
00078     def __init__(self, context: ProfileContext, rotate_backups: bool = False):
```
```text
00744     def _write_xml_tree_atomically(self, tree, write_path: str):
00745         parent_dir = os.path.dirname(write_path) or "."
00746         os.makedirs(parent_dir, exist_ok=True)
00747         fd, temp_path = tempfile.mkstemp(
00748             prefix=f".{Path(write_path).stem}_",
00749             suffix=f"{Path(write_path).suffix}.tmp",
00750             dir=parent_dir,
00751         )
00752         os.close(fd)
00753         try:
00754             tree.write(temp_path, pretty_print=True, xml_declaration=True, encoding="utf-8")
00755             os.replace(temp_path, write_path)
00756         except Exception:
00757             try:
00758                 if os.path.exists(temp_path):
00759                     os.remove(temp_path)
00760             except OSError:
00761                 pass
00762             raise
00763 
```


## E09 RimCrow Agent 工具定义与执行路由

**文件：`backend/ai/ai_tools.py`**；SHA256：`4628ae1c71262819c57a036600ba38868dcfd66dc826caace6cc0356edfb823f`

```text
00301 TOOL_DEFINITIONS: dict[str, ToolDefinition[Any]] = {
00302     "get_log_context": ToolDefinition(
00303         name="get_log_context",
00304         label="日志上下文",
00305         llm_description="读取某条错误摘要对应的详细日志上下文，包含原始报错内容与截断堆栈。仅当摘要不足以定位问题时再调用。",
00306         ui_description="获取指定错误日志的详细上下文（包含完整错误信息和核心堆栈）。仅当摘要中的 stack_preview 无法让你得出结论时才使用。",
00307         args_model=GetLogContextArgs,
00308         handler_name="_tool_get_log_context",
00309         result_summarizer=_summarize_log_context,
00310     ),
00311     "search_mods": ToolDefinition(
00312         name="search_mods",
00313         label="搜索模组",
00314         llm_description="按名称、别名、作者或 package_id 片段搜索可能对应的模组，用于确认准确 package_id。",
00315         ui_description="当且仅当你不确定某个 Mod 的准确 package_id 时，使用此工具模糊搜索。可输入名字、别名或部分 ID 查找可能对应的 package_id。",
00316         args_model=SearchModsArgs,
00317         handler_name="_tool_search_mods",
00318         result_summarizer=_summarize_search_mods,
00319     ),
00320     "get_active_mod_list": ToolDefinition(
00321         name="get_active_mod_list",
00322         label="当前启用列表",
00323         llm_description="获取当前环境中的启用模组列表，用于整体排序和冲突排查。",
00324         ui_description="获取当前排序文件中的已激活模组列表，用于宏观检查模组顺序，返回模组包名列表。",
00325         args_model=GetActiveModListArgs,
00326         handler_name="_tool_get_active_mod_list",
00327         result_summarizer=_summarize_active_mods,
00328     ),
00329     "get_mod_info": ToolDefinition(
00330         name="get_mod_info",
00331         label="模组元数据",
00332         llm_description=f"读取已安装模组的元数据。simple 返回基本信息：{AI_MOD_INFO_SIMPLE_FIELDS}；all 返回全部信息；specific 返回指定字段：{AI_MOD_INFO_ALLOWED_FIELDS}。",
00333         ui_description=f"获取当前环境中已安装模组的元数据。simple 返回基本必要信息：{AI_MOD_INFO_SIMPLE_FIELDS}。all 返回全部信息；specific 返回可选字段信息：{AI_MOD_INFO_ALLOWED_FIELDS}。",
00334         args_model=GetModInfoArgs,
00335         handler_name="_tool_get_mod_info",
00336         result_summarizer=_summarize_mod_info,
00337     ),
00338     "get_mod_rules": ToolDefinition(
00339         name="get_mod_rules",
00340         label="模组规则",
00341         llm_description="读取模组依赖、前置、后置和不兼容规则，用于判断显性排序和兼容关系。",
00342         ui_description="获取该模组的依赖模组、前置/后置模组和不兼容模组规则，以了解该模组与其它模组之间的显性关系。只有在怀疑缺失前置、加载顺序错误或存在不兼容模组时再调用。",
00343         args_model=GetModRulesArgs,
00344         handler_name="_tool_get_mod_rules",
00345         result_summarizer=_summarize_mod_rules,
00346     ),
00347     "get_mod_user_data": ToolDefinition(
00348         name="get_mod_user_data",
00349         label="模组用户数据",
00350         llm_description="读取管理器中的模组别名、标签、备注、颜色、分组等用户态信息，仅用于辅助理解玩家归类。",
00351         ui_description="获取该已安装模组在管理器中的用户态上下文：别名、标签、备注、颜色、自定义类型和所属分组。这些信息只用于辅助理解玩家归类，属于模组管理器专有信息，与游戏机制无关。",
00352         args_model=GetModUserDataArgs,
00353         handler_name="_tool_get_mod_user_data",
00354         result_summarizer=_summarize_mod_user_data,
00355     ),
00356     "get_group_mods": ToolDefinition(
00357         name="get_group_mods",
00358         label="分组模组",
00359         llm_description="按分组名读取分组下的模组列表，仅用于辅助理解玩家自己的分组归类。",
00360         ui_description="按分组名查询分组所含模组。分组信息只用于辅助理解玩家归类，属于模组管理器专有信息，与游戏机制无关。",
00361         args_model=GetGroupModsArgs,
00362         handler_name="_tool_get_group_mods",
00363         result_summarizer=_summarize_group_mods,
00364     ),
00365 }
00366 
00367 
00368 def get_tool_definitions() -> dict[str, dict[str, Any]]:
00369     """返回给前端展示用的工具定义。"""
00370     definitions: dict[str, dict[str, Any]] = {}
00371     for name, definition in TOOL_DEFINITIONS.items():
00372         definitions[name] = definition.to_frontend_definition()
00373     return definitions
```
```text
00383         self.payload = dict(payload or {})
00384         self.request_payload = self._extract_request_payload(self.payload)
00385         self.reader = reader
00386         self.log_source_type, self.log_filename = self._resolve_log_context()
00387         
00388         # 预加载：缓存一份当前已激活的 Mod ID 集合，供各个工具复用
00389         self._active_mod_ids_set = self._get_active_mod_id_set()
00390 
00391         # 工具注册表只保留一份“规范对象”，由它同时驱动 schema 生成和入参校验。
00392         self.registry: dict[str, ToolSpec[Any]] = {
00393             name: definition.bind(getattr(self, definition.handler_name))
00394             for name, definition in TOOL_DEFINITIONS.items()
00395         }
00396 
00397     # =====================================================================
00398     # 外部接口：供 AIManager 调用
00399     # =====================================================================
00400 
00401     def get_tool_schemas(self, enabled_names: list[str] | None = None) -> list[dict[str, Any]]:
00402         """
00403         动态生成大模型所需的 OpenAI/LiteLLM 标准 JSON Schema 结构。
00404         :param enabled_names: 前端传来的启用工具名称列表，如果为空则返回全部
00405         """
00406         schemas = []
00407         enabled_names = enabled_names or []
00408         for name, spec in self.registry.items():
00409             # 过滤未启用的工具
00410             if enabled_names and name not in enabled_names: continue
00411             schemas.append(spec.to_openai_schema())
00412         return schemas
00413 
00414     def execute_structured(self, name: str, arguments_str: str) -> ToolExecutionResult:
00415         """
00416         统一的工具执行路由。
00417 
00418         流程固定为：
00419         1. 解析模型给出的 JSON 参数
00420         2. 用 `pydantic` 做强校验和规范化
00421         3. 把“干净参数对象”交给具体工具方法
00422 
00423         这样具体工具方法可以专注业务，不必反复写 `get()/int()/try/except` 样板代码。
00424         """
00425         try:
00426             args = json.loads(arguments_str) if arguments_str else {}
00427         except json.JSONDecodeError:
00428             payload = {"error": "工具参数格式不正确，请重新组织输入后重试。"}
00429             return self._build_execution_result(name=name, payload=payload)
00430 
00431         spec = self.registry.get(name)
00432         if spec is None:
00433             payload = {"error": "系统工具不可用，请稍后重试。"}
00434             return self._build_execution_result(name=name, payload=payload)
00435 
00436         try:
00437             validated_args = spec.definition.args_model.model_validate(args)
00438         except ValidationError as e:
00439             logger.warning(
00440                 "[AI诊断] 工具参数校验失败。tool=%s",
00441                 name,
00442                 extra={"error_code": "AI.TOOL.ARGS_INVALID", "extra_context": {"tool": name}},
00443             )
00444             payload = {"error": "工具参数不符合要求，请重新组织输入后重试。"}
00445             return self._build_execution_result(name=name, payload=payload)
00446 
00447         try:
00448             logger.debug(f"[AI诊断] 开始执行工具 name={name} args={validated_args.model_dump()}")
00449             payload = spec.handler(validated_args)
00450             return self._build_execution_result(name=name, payload=payload)
00451         except Exception as e:
00452             logger.error(
00453                 "AI 工具执行异常。tool=%s",
```


## E10 RimCrow 多轮与权限过滤链

**文件：`backend/ai/assistant_runtime.py`**；SHA256：`ff3686df2af34b84181fb0fa2334e31972de601c05e6a0e895b44a6ccfccc967`

```text
00045 from backend.utils.error_contract import build_stack_detail, classify_exception
00046 from backend.utils.event_bus import EventBus
00047 from backend.utils.logger import logger
00048 
00049 ASSISTANT_MAX_LOOPS = 10
00050 TRACE_RECORD_LIMIT = 200
00051 SESSION_RECENT_MESSAGE_LIMIT = 8
00052 SESSION_EVIDENCE_LIMIT = 12
00053 TRACE_TOOL_TIME_SPAN_FALLBACK = 1000
```
```text
01207         requested_tools = list(payload.get("enabled_tools", []) or [])
01208         allowed_tools = list(assistant.tool_scope_selectable or [])
01209         tools = None
01210         tool_choice = None
01211         if allowed_tools:
01212             if not requested_tools:
01213                 requested_tools = list(allowed_tools)
01214             requested_tool_names = [name for name in requested_tools if name in allowed_tools]
01215             if requested_tool_names:
01216                 tools = tool_executor.get_tool_schemas(requested_tool_names)
01217                 tool_choice = "auto"
01218                 variables["tools_description"] = self._build_tools_description(tools)
01219             else:
01220                 variables["tools_description"] = "本轮不可调用外部工具，只能基于当前上下文作答。"
01221         return variables, tools, tool_choice
01222 
01223     def _build_tools_description(self, tools: list[dict[str, Any]] | None) -> str:
01224         if not tools: return "本轮不可调用外部工具，只能基于当前上下文作答。"
01225         tool_lines = []
01226         for tool in tools:
01227             name = str(tool.get("function", {}).get("name") or "").strip()
01228             if not name:
```
```text
01673         loop_count = 0
01674         executed_tool_signatures: set[str] = set()
01675         base_prompt_tokens = int((prompt_input_breakdown or {}).get("total_tokens", 0) or 0)
01676         cumulative_prompt_input_breakdown = create_prompt_input_breakdown()
01677 
01678         while loop_count < ASSISTANT_MAX_LOOPS:
01679             loop_count += 1
01680             try:
01681                 prompt_tokens_this_round = self._estimate_messages_tokens(messages, model_name)
01682                 token_usage["estimated_prompt_tokens"] += prompt_tokens_this_round
01683                 token_usage["estimated_total_tokens"] += prompt_tokens_this_round
01684                 round_prompt_input_breakdown = self._build_prompt_input_breakdown_for_round(
01685                     prompt_tokens_this_round=prompt_tokens_this_round,
01686                     base_prompt_tokens=base_prompt_tokens,
01687                     base_prompt_breakdown=prompt_input_breakdown,
```
```text
01786     def _execute_tool_calls(
01787         self,
01788         *,
01789         session_id: str,
01790         formatted_tool_calls: list[dict[str, Any]],
01791         tool_executor: AIToolExecutor,
01792         messages: list[dict],
01793         executed_tool_signatures: set[str],
01794         trace_record: RequestTraceRecord | None = None,
01795         session_state: SessionState,
01796     ) -> None:
01797         for tc in formatted_tool_calls:
01798             func_name = tc["function"]["name"]
01799             func_args = tc["function"]["arguments"]
01800             tool_start_at = time.perf_counter()
01801             tool_display = tool_executor.build_tool_call_display(func_name, func_args)
01802 
01803             EventBus.emit("ai-tool-call", {
01804                 "session_id": session_id,
01805                 "tool_id": tc["id"],
01806                 "name": func_name,
01807                 "display_name": tool_display.get("display_name", ""),
01808                 "arguments": func_args,
01809                 "arguments_preview": tool_display.get("arguments_preview", ""),
01810                 "arguments_pretty": tool_display.get("arguments_pretty", ""),
01811             })
01812 
01813             call_signature = f"{func_name}|{func_args}"
01814             if call_signature in executed_tool_signatures:
01815                 tool_execution = ToolExecutionResult(
01816                     name=func_name,
01817                     ok=False,
01818                     model_output=json.dumps({"error": DUPLICATE_TOOL_CALL_ERROR}, ensure_ascii=False),
01819                     data={"error": DUPLICATE_TOOL_CALL_ERROR},
01820                     summary=f"执行失败：{DUPLICATE_TOOL_CALL_ERROR}",
01821                     error=DUPLICATE_TOOL_CALL_ERROR,
01822                 )
01823                 logger.warning(f"[AI防死锁] 拦截重复调用: {call_signature}")
01824             else:
01825                 executed_tool_signatures.add(call_signature)
01826                 self._raise_if_cancelled(session_id)
01827                 try:
01828                     tool_execution = tool_executor.execute_structured(func_name, func_args)
01829                 except AssistantRequestCancelled:
01830                     raise
01831                 except Exception as tool_err:
01832                     logger.error(f"[AI会话] 工具执行异常 tool={func_name}: {tool_err}", exc_info=True)
01833                     tool_error_message = "工具执行失败，请稍后重试。"
01834                     tool_execution = ToolExecutionResult(
01835                         name=func_name,
01836                         ok=False,
01837                         model_output=json.dumps({"error": tool_error_message}, ensure_ascii=False),
01838                         data={"error": tool_error_message},
01839                         summary=f"执行失败：{tool_error_message}",
01840                         error=tool_error_message,
01841                     )
01842                 self._raise_if_cancelled(session_id)
01843 
01844             duration_ms = int((time.perf_counter() - tool_start_at) * 1000)
```
**文件：`backend/ai/def_entries.py`**；SHA256：`ef160a5e5755d8f43353391dbcb4e7e14e23f84972b1ce4aa3262b9ed863123a`

```text
00145         "assistant.log_game": _assistant_definition(
00146             assistant_id="assistant.log_game",
00147             name="游戏日志助手",
00148             description="面向 RimWorld 游戏日志的多轮强化诊断助手。",
00149             prompt_id="game_log_analysis",
00150             source_kinds=["logs"],
00151             tool_scope=[
00152                 "get_log_context",
00153                 "search_mods",
00154                 "get_active_mod_list",
00155                 "get_mod_info",
00156                 "get_mod_rules",
00157                 "get_mod_user_data",
00158                 "get_group_mods",
00159             ],
00160             action_types=["MOD_STATE", "MOD_RULE"],
00161         ),
00162         "assistant.log_app": _assistant_definition(
00163             assistant_id="assistant.log_app",
00164             name="软件日志助手",
00165             description="面向 RimCrow 自身运行日志的多轮诊断助手。",
00166             prompt_id="app_log_analysis",
00167             source_kinds=["logs"],
00168             tool_scope=[],
00169             action_types=["TEXT_TRANSFER"],
00170         ),
00171         "assistant.mod_general": _assistant_definition(
00172             assistant_id="assistant.mod_general",
00173             name="模组助手",
00174             description="面向单个或多个模组信息的通用多轮助手。",
00175             prompt_id="mod_general_assistant",
00176             source_kinds=["mods", "global"],
00177             tool_scope=[
00178                 "search_mods",
00179                 "get_mod_info",
00180                 "get_mod_rules",
00181                 "get_mod_user_data",
00182                 "get_group_mods",
00183             ],
00184             action_types=["MOD_STATE", "MOD_RULE"],
00185         ),
00186     }
00187 
```


## E11 AI 建议卡片与前端列表状态

**文件：`frontend/src/features/ai/ai-store/runtime/aiActionRuntime.js`**；SHA256：`315093b96c4744ab89a57fdf0bef576260c73f186d84be7d16c9ff3f63a7d7dc`

```text
00376    * 每个 key 对应一种后端动作类型，真正执行时由消息卡片按
00377    * `type -> executor` 路由。这样比把执行逻辑直接塞进组件更容易维护：
00378    * - 组件只负责交互
00379    * - store 只负责状态变更
00380    * - runtime 负责把两者粘起来
00381    */
00382   async MOD_STATE(payload, action) {
00383     const shouldEnable = getActionVariant(action) !== 'disable'
00384     const modIds = Array.isArray(payload.mod_ids) ? payload.mod_ids : []
00385     if (modIds.length === 0) {
00386       toast.warning(getActionMissingPayloadMessage(action))
00387       return
00388     }
00389     // 默认前端会先把这类动作拆成单模组卡片；
00390     // 这里仍保留逐项执行兜底，避免其它入口直接执行批量 payload。
00391     for (const modId of modIds) {
00392       await modStore.changeModsActive([modId], shouldEnable)
00393     }
00394     const successMessage = getActionSuccessMessage(action)
00395     if (successMessage) {
00396       toast.success(successMessage)
00397     }
00398   },
00399 
00400   async MOD_RULE(payload, action) {
00401     /**
00402      * 应用一条用户规则动作。
00403      *
00404      * 规则类动作有副作用且可能影响排序，因此必须先确认，再在成功后
00405      * 根据后端定义决定是否追加“顺手自动排序”的二次动作。
00406      */
00407     const normalizedType = String(action?.variant || payload.rule_type || '')
00408     if (!payload.mod_id || !payload.target_id || !normalizedType) {
00409       toast.warning(getActionMissingPayloadMessage(action))
00410       return
00411     }
00412 
00413     const confirmMeta = getActionConfirmMeta(action)
00414     const confirmed = await confirmStore.confirmAction(
00415       confirmMeta.title || t('ai.actions.fallback.confirm_title', '确认执行操作'),
00416       confirmMeta.message || getActionPreview(action) || getActionDescription(action),
00417       { type: 'warning', confirmText: confirmMeta.confirmText || t('common.action.confirm', '确认'), cancelText: t('common.action.cancel', '取消') },
00418     )
00419     if (!confirmed) return
00420 
00421     await ruleStore.addUserModRule(payload.mod_id, normalizedType, payload.target_id)
00422 
00423     const postSuccessMeta = getActionPostSuccessMeta(action)
00424     if (postSuccessMeta.message && await confirmStore.confirmAction(
00425       postSuccessMeta.title || t('ai.actions.fallback.applied_title', '操作已应用'),
00426       postSuccessMeta.message,
00427       { type: 'success', confirmText: postSuccessMeta.confirmText || t('common.action.ok', '确定'), cancelText: t('common.action.later_short', '稍后') },
00428     )) {
00429       await modStore.autoSortMods()
00430       return
```
**文件：`frontend/src/features/mod/stores/modStore.js`**；SHA256：`8d56991edd9283be5de762579ed07fca7b5530deb7c5d65c0ecc832df94e3685`

```text
00722   // 批量启用/停用Mod
00723   const changeModsActive = async (ids, active) => {
00724     if(typeof ids === 'string') ids = [ids]
00725     const nextIds = Array.isArray(ids) ? [...ids] : []
00726     if (nextIds.length === 0) return false
00727     return await runListHistoryTransaction({
00728       type: active ? 'change-active-enable' : 'change-active-disable',
00729       label: active
00730         ? t('history.mod.activate_count', '启用 {count} 个 Mod', { count: nextIds.length })
00731         : t('history.mod.deactivate_count', '停用 {count} 个 Mod', { count: nextIds.length }),
00732       trackedModIds: nextIds
00733     }, async () => {
00734       removeIdsOnAllList(nextIds)
00735       if(active) {
00736         await smartInsertMods(nextIds)
00737       } else {
00738         inactiveIds.value.unshift(...nextIds)
00739       }
00740       takeModListByIds(nextIds).forEach(mod => {
00741         mod.last_moved_time = Date.now()
00742         mod.last_active_time = Date.now()
00743       })
00744     })
00745   }
00746   const canSwitchCoexistenceSource = (id) => {
```


## E12 日志监视器不能原样移植

**文件：`backend/managers/mgr_game_logs.py`**；SHA256：`114462ec1823b7619f37b0bb5ff606a5f21506e966d6b5cf3fccdd195a3f8efe`

```text
00451         self._stop_event.clear()
00452         self._realtime_thread = threading.Thread(target=self._tail_log_file, daemon=True)
00453         self._realtime_thread.start()
00454         logger.info(f"游戏实时日志监视器已启动 -> {self.realtime_log_file}")
00455 
00456     def stop_realtime_monitor(self):
00457         """停止监视线程"""
00458         if self._realtime_thread and self._realtime_thread.is_alive():
00459             self._stop_event.set()
00460             # self._realtime_thread.join(timeout=2) # 等待线程优雅退出
00461             logger.info("游戏实时日志监视器已停止。")
00462             
00463     def _tail_log_file(self):
00464         """后台线程的核心工作函数：Tailing the log file"""
00465         try:
00466             with open(self.realtime_log_file, 'r', encoding='utf-8', errors='replace') as f:
00467                 current_line_no = sum(1 for _ in f)
00468                 # 直接跳到文件末尾
00469                 f.seek(0, 2)
00470                 while not self._stop_event.is_set():
00471                     line = f.readline()
00472                     if not line:
00473                         time.sleep(0.1) # 没有新内容，短暂休眠
00474                         continue
00475                     current_line_no += 1
00476                     
00477                     # 有新内容，解析并推送
00478                     try:
00479                         data = json.loads(line)
00480                         data['level'] = self._normalize_log_level(data, self.realtime_log_file, current_line_no)
00481                         data['id'] = generate_log_id(data.get('timestamp', ''), data['level'], data.get('message', ''))
00482                         data['raw_lines'] = [current_line_no]
00483                         EventBus.emit('game-log', data)
00484                     except json.JSONDecodeError:
00485                         # 忽略无法解析的行
00486                         pass
00487         except Exception as e:
00488             logger.error(f"日志监视线程异常退出: {e}", exc_info=True)
00489 
00490 
00491     def get_log_files(self):
00492         """获取日志文件列表，支持 app(软件日志) 和 game(游戏日志)"""
00493         result =[]
00494         for filename in self._GAME_LOG_FILENAMES:
00495             filepath = self.resolve_log_file_path(filename)
00496             if filepath and os.path.exists(filepath):
00497                 stat = os.stat(filepath)
00498                 result.append({
```


## E13 凭据迁移与明文回退边界

**文件：`backend/utils/secret_store.py`**；SHA256：`ce35f77596757cd54447478f5f4b5bd5d61dda36762e1afd87a8742977353e04`

```text
00186             for key, path in SECRET_FIELDS.items()
00187         }
00188 
00189     def migrate_and_hydrate(self, runtime_config: Any) -> bool:
00190         """
00191         旧配置里如果还有明文，优先写入系统凭据库；随后把可读取的凭据回灌到运行时配置。
00192         返回值表示是否成功把至少一项旧明文迁移进凭据库。
00193         """
00194         changed = False
00195         for key, path in SECRET_FIELDS.items():
00196             plaintext = str(_get_nested_value(runtime_config, path) or "")
00197             stored = self.get_secret(key)
00198             if plaintext:
00199                 try:
00200                     self.set_secret(key, plaintext)
00201                     stored = plaintext
00202                     changed = True
00203                 except SecretStoreError:
00204                     # 写入失败时保留旧配置明文；下次启动会继续尝试迁移，避免静默丢失。
00205                     self.fallback_keys.add(key)
00206                     self.fallback_errors[key] = _tr("secret.errors.fallback_plaintext_kept", "本机安全存储不可用，密钥暂时保留在配置文件中")
00207                     stored = plaintext
00208             if stored:
00209                 _set_nested_value(runtime_config, path, stored)
00210         return changed
00211 
00212     def apply_secret_inputs(self, runtime_config: Any, data: dict[str, Any]) -> bool:
00213         """应用设置中的密钥输入：未提交或普通空值保持不变，显式清除才删除。"""
00214         data.pop("_preserve_secret_keys", None)
00215         raw_clear_keys = data.pop("_clear_secret_keys", []) or []
00216         if isinstance(raw_clear_keys, str):
00217             raw_clear_keys = [raw_clear_keys]
00218         if not isinstance(raw_clear_keys, (list, tuple, set)):
00219             raw_clear_keys = []
00220         clear_keys = {
00221             self.validate_key(key)
```
注意：这里是旧配置明文迁移失败时的保留路径；不能泛化成所有新密钥都会自动明文保存。新设置输入走 set_secret，失败会报错。


## E14 验证执行记录

Python 源文件静态 AST 解析未发现语法错误；这不等于依赖加载、类型、业务逻辑或打包验证通过。

`node frontend/tests/modIdentity.test.mjs`：退出码 0；仅此独立前端脚本。

尝试 `python -m pytest -q tests/test_load_order_parsers.py tests/test_load_order_share_code.py`：退出码 2，收集阶段缺少依赖 vdf；没有取得这两个测试模块的通过结果。这是本审查环境依赖不足，不能当成项目业务缺陷。没有运行全套 853 个静态识别的 test_ 函数（参数化测试数量也可能不同），没有 Windows GUI/游戏端到端运行。

```text

==================================== ERRORS ====================================
______________ ERROR collecting tests/test_load_order_parsers.py _______________
ImportError while importing test module '<RIMCROW>/tests/test_load_order_parsers.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.13/importlib/__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_load_order_parsers.py:6: in <module>
    from backend.load_order import (
backend/load_order/__init__.py:2: in <module>
    from .import_check import build_import_check_report
backend/load_order/import_check.py:4: in <module>
    from backend.database.workshop_selection import build_install_source
backend/database/workshop_selection.py:6: in <module>
    from backend.utils.tools import normalize_package_id, normalize_workshop_id
backend/utils/tools.py:14: in <module>
    from backend.paths.core import (
backend/paths/__init__.py:10: in <module>
    from .game_locations import (
backend/paths/game_locations.py:9: in <module>
    import vdf
E   ModuleNotFoundError: No module named 'vdf'
=========================== short test summary info ============================
ERROR tests/test_load_order_parsers.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.18s

```


## E15 许可证

**文件：`LICENSE`**；SHA256：`3944bc4dac485e160dfdadbb27fad6c7b1ab6bad2bbf40cc784fbd1b8eff36e4`

```text
00001 MIT License
00002 
00003 Copyright (c) 2026 RimCrow contributors
00004 
00005 Permission is hereby granted, free of charge, to any person obtaining a copy
00006 of this software and associated documentation files (the "Software"), to deal
00007 in the Software without restriction, including without limitation the rights
00008 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
00009 copies of the Software, and to permit persons to whom the Software is
00010 furnished to do so, subject to the following conditions:
00011 
00012 The above copyright notice and this permission notice shall be included in all
00013 copies or substantial portions of the Software.
00014 
00015 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
00016 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
00017 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
00018 AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
00019 LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
00020 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
00021 SOFTWARE.
```


## 45 DLL 精简清单

| 文件 | AssemblyName | AssemblyVersion | MelonInfo 名称 | MelonInfo 版本 | SHA256 |
|---|---|---|---|---|---|

| 3低概率2%.dll | EmptyNukeBarrel_Rare | 1.0.27.0 | EmptyNukeBarrel_Rare | 1.0.27-localfix1 | 887f6b6e6332a6be1a859bd6762f09c2fa4a8c92243e9b9ee44920048e8c9aa7 |

| AugPresenceGuard.dll | AugPresenceGuard | 1.0.0.0 | AugPresenceGuard | 1.0.0 | 5f0c47eba2f6ad4a8624e09b3d6ae0292e2f95258a8f1728070f4ba7b981c731 |

| AutoBatterySwap.dll | AutoBatterySwap | 0.0.0.0 | AutoBatterySwap | 1.10.1 | d697c3b4a779a34c6c4f8cbd8d4c497b227b71e1eb7326afe0844a2c495f6223 |

| BrewingExpansion.dll | BrewingExpansion | 0.0.0.0 | Brewing Expansion | 0.4.0 | cf5f2bf8c091aee06e986d1c456d65ad0d632b14fe170da559007589d718cdce |

| CharacterForgeRuntime.dll | CharacterForgeRuntime | 1.1.1.0 | CharacterForge Runtime | 1.1.1 | 6bf67f845621eabe1aec84c59eb48ef7e8565b5c294329f831f92ab82e938594 |

| ClickToAdvance.dll | ClickToAdvance | 1.0.0.0 | ClickToAdvance | 1.0.0 | bcf261776a6b0327725374b699bd2d31ac9235635d2f9933c0e67f77799dd9d4 |

| CustomerCreditBoostm.dll | CustomerCreditBoost | 0.0.0.0 | CustomerCreditBoost | 1.2.0 | 31cc77774e4f32cb88ca2ff08944a224b1637550684a9d639181e2af58e1048e |

| DialogueSpeed-1.0.0m.dll | DialogueSpeed | 1.0.0.0 | DialogueSpeed | 1.0.0 | 94f0db8e57ed08a138d7dd14b1be9ff9f3f7ee409b26667bb5c8d8639c8afef1 |

| ExtraPerks.dll | ExtraPerks | 0.2.0.0 | ExtraPerks | 0.2.0 | a66b0a8be9f7d1a56824ee78044689293b50839d9ab4ac3f9bc4ff46afdeb31d |

| InventorySorter.dll | InventorySorter | 1.0.0.0 | Inventory Sorter | 1.0.1 | e20f6122908dc3b37c04d6ba0597192462e363ac740de3591b2d04c42d1de76c |

| ItemForgeRuntime.dll | ItemForgeRuntime | 1.2.3.0 | ItemForge Runtime | 1.2.3 | d52270822759d26f231ecc6cacec1ab74a1f0e2b3c80448d4cf20d3e8af5964f |

| JCuratedVisitors.dll | JCuratedVisitors | 1.0.9.0 | 烛印会与失灯区 | 1.0.9 | 585d9f84649a85db5ab1214029811251563f392ebec5bf63f7a7fd3305d8cdd0 |

| KitchenCrashTrace.dll | KitchenCrashTrace | 0.3.2.0 | Kitchen Crash Trace | 0.3.2 | 72f48dc697591d4c0241ca69efb4a9da32050dab2ea4fd3daed2300b7ff2d09f |

| MoreStorageColors.dll | MoreStorageColors | 1.1.0.0 | More Storage Colors | 1.1.0 | d6f49a6d31bd09f1e9798d8aed46ca7d5001380f99c015e2f8f24ec3c8946749 |

| Nested Storage.dll | Nested Storage | 0.0.0.0 | NestedStorage | 1.7.2 | be640b87b468db5f9629df37cb9216db3ceddf912338e77a76a87c6b738f7df7 |

| PSCharacterPack_CuratedVisitors.dll | PSCharacterPack_CuratedVisitors | 0.3.3.0 | 第七席与绯色来客·人物资源 | 0.3.3 | 6209b8f7c2aa761cf5cd3e86629abb83f38dfa5fe73d594137a8e987823c18e3 |

| PSCharacterPack_Fishingfriends.dll | PSCharacterPack_Fishingfriends | 1.0.0.0 | 钓鱼商人 | 1.3.10 | 903902f8a9ced57fadc9ea6a6beb1b52acc54083169eb16191affdb0d9fea043 |

| PSFishingCuisineExpansion.dll | PSFishingCuisineExpansion | 1.0.0.0 | 钓鱼料理扩展 | 1.0.0 | 6f4daf8dc153827de84cb91e9c197442e009e2eeb51f061d57b468a619078a34 |

| PSItemPack_CuratedVisitors.dll | PSItemPack_CuratedVisitors | 0.3.3.0 | 第七席与绯色来客 | 0.3.3 | 75fcb11415a0c939da0c47be1b123937299f7c8d50bf33840f08e0d94d04e126 |

| PSItemPack_Gofishing.dll | PSItemPack_Gofishing | 1.0.0.0 | gofishing | 1.3.10 | ccf46fe925f070a1adc24c85018b1c9049c710fb6cd56deabbcb9cba011ee152 |

| PSItemPack_MoreFood.dll | PSItemPack_MoreFood | 1.0.0.0 | E1z的更多食物 | 1.0.1 | 190c29ed382d6da2ea0e5fb7a803f3e101a968f1e7b997b5918731f42fcff79c |

| PSItemPack_SanmuShiwushechipinPack1.dll | PSItemPack_SanmuShiwushechipinPack1 | 1.0.0.0 | 无（资源内容包） | — | 5b9990f1b78ffb3d913977efc33920eb3778dfdd07cf9668263be822ed982370 |

| PSMarketRequests.dll | PSMarketRequests | 1.0.0.0 | PSMarketRequests | 1.4.13 | 4e0c6038921666e79ff71dcf6af3367ef9799a2a1131634e036c79bd8871ca52 |

| PSPDA.dll | PSPDA | 1.0.0.0 | Terminal | 0.5.66 | f7eb2a5ad22b9da1b48fb116c25bcf969b7f52caf776ef95cd4251ba93c79301 |

| PSProcurement.dll | PSProcurement | 1.0.0.0 | PSProcurement | 0.47.72 | df1cac8084f177afc5d541d2ae4c14bb8bd30983101a4462527e9164c4bbdf5e |

| PSWallpaper.dll | PSWallpaper | 1.0.0.0 | PSWallpaper | 1.0.10 | fc934110f8adc51e9ad1435eea4306f21532d6ce9c40cd9af5d6f3a302c5a3bd |

| PSWarehouse.dll | PSWarehouse | 1.0.0.0 | PSWarehouse | 0.6.4 | ceebb9fe0141d68c8e7a22ffc156ba0fc061d7b47f0b3119e64d06794e918d6a |

| ProbablyStolenInspector.dll | ProbablyStolenInspector | 0.0.0.0 | ProbablyStolenInspector | 3.20.3 | 76206b4664c255052ef8de0a77fa5dec302c503e6806258d4feeb0172682ef6a |

| ProbablyStolenItemManager.dll | ProbablyStolenItemManager | 0.0.0.0 | Probably Stolen Item Manager | 0.4.7 | 0ae56b227ba886db8d3e7733b660648479935c9c2dc028b40dd55332bb56a8fc |

| RatColonyOptimizer.dll | RatGeneticRenamer | 1.0.0.0 | Rat Colony Optimizer | 1.0.0 | d48dd8c870c296321ae1665c020ae0ab70de756e2d31d749cb138cf75346b008 |

| RatGeneticRenamer.dll | RatGeneticRenamer | 2.6.3.0 | Rat Genetic Renamer | 1.0.0 | c0a8c71050368d47cc3c02304bed7163bc27c89415dfe2b3c9848144a2b43db3 |

| SciRat.dll | SciRat | 1.1.0.0 | 科学养鼠 | 1.3.10 | 0de62c4d0dbd734ae2fae5aacc0d9b293c11a8fba1d1b4a33d7e517dda4dee1a |

| WagesPerks.dll | JacksonPerks | 1.0.0.0 | Wage's Perks | 1.2.2-localfix1 | 90c37b898dd42078a2129d1a25b4b99554bc03a0d3a744dbfcd6459eefda05c4 |

| WaterScanTooltip.dll | WaterScanTooltip | 1.0.0.0 | WaterScanTooltip | 1.5.2 | 550fec74acfe7d09dc770d882150d7b7d34e115f48cc22792a2aa53afa2cb064 |

| WaterTower.dll | WaterTower | 0.1.0.0 | WaterTower | 1.0.5 | bd0f1e25a079db788a7eed8c2010a051c653d2593c30441d996c3183a40f6223 |

| WineAppraisalMaster.dll | WineAppraisalMaster | 2.0.0.0 | 鼠布林&假酒供应商mod | 2.0.0 | c6dc086f88d290ed3a18006b244f82429073241ed52203547cc786d5fab6e060 |

| XIAOWO-WRDUnlock.dll | NetworkUnlockEffective | 1.0.7.0 | XIAOWO-WRDUnlock | 1.0.7 | 9a43c9838d591a05c17ca0025c4b4d943c2f1ba6d1e9aba053b3d8ac39257bdc |

| XIAOWOKitchenBusiness.dll | XIAOWOKitchenBusiness | 1.1.41.0 | XIAOWO-KitchenBusiness | 1.1.41 | e95beddb14ec931479b8457489cdc983ac0c93350f21d5731501171ab4045087 |

| XIAOWONellisStory-v1.2.1.dll | XIAOWONellisStory | 1.2.1.0 | XIAOWO-NellisStory | 1.2.1 | 82bd1bc8f546e7693ddeb46b2249c564db7303aa6a838c46d8a7b14712b14b01 |

| XIAOWOPrecisionBayExpansion.dll | XIAOWOPrecisionBayExpansion | 1.0.1.0 | XIAOWO-PrecisionBayExpansion | 1.0.1 | 44209f8473ce79a6e5d69bfa237fd1854deb2dbef26c4f4649ad1001aeaef365 |

| XIAOWORatColony.dll | XIAOWORatColony | 1.0.12.0 | XIAOWO-RatColony | 1.0.12 | e04344955b82fc08c5cc38f6ede29319db78f62100b0c4148249e9e088fd830d |

| XIAOWOTradePerks.dll | XIAOWOTradePerks | 1.8.2.0 | XIAOWO-TradePerks | 1.8.2 | 0b9f190c67d4c14976d4a7f4c8bc03c1c66449ce92fcf9cee1fce70f38280547 |

| XIAOWOUnlimitedBayExpansion.dll | XIAOWOUnlimitedBayExpansion | 1.0.2.0 | XIAOWO-UnlimitedBayExpansion | 1.0.2 | 5166ebe10102459b90a9f5fb38ed5ad12488e3870109a0a5b2fb9a424c1b2a5c |

| XIAOWOWeeklyVisitors.dll | XIAOWOWeeklyVisitors | 1.0.3.0 | XIAOWO-WeeklyVisitors | 1.0.3 | 1817ad8d317471e088cf38c63c8cf76bfaa119ff7d6628c129c37d295c314e25 |

| moreUpdate.dll | MoreDeviceUpgrades | 1.0.0.0 | More Device Upgrades | 0.2.0-localfix1 | 49189ca42c3f573e334d7a8c8efbaabf69650bd5fbe53ed81419aadd9b87db9c |
