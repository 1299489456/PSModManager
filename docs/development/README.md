# 从源码运行与开发 PSModManager

当前桌面入口是 `main.py` → `backend/app` → `frontend/src/app`。主流程为“选择游戏 → 导入 Mod → 启停 → 保存 → 运行”，AI 只在需要时辅助日志排错。

以下命令使用 **Windows x64 + PowerShell**，除单独说明外均在仓库根目录执行。开发和测试使用独立数据目录与合成样本；隔离数据目录不会把真实游戏变成测试副本，请勿为调试选择真实游戏并执行安装、启停或启动。

## 1. 准备工具并克隆

先安装以下工具，安装后重新打开 PowerShell：

| 工具 | 本项目用途与版本 |
|---|---|
| [Git for Windows](https://git-scm.com/downloads/win) | 克隆和管理源码 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 按 `uv.lock` 准备 Python 环境；下文用它安装许可基线指定的 Python 3.11 补丁版 |
| [Node.js](https://nodejs.org/en/download)（含 npm） | 建议 Node 22.12+ 或更高的受支持 LTS；前端检查也接受 20.19+ |
| [.NET 10 SDK，Windows x64](https://dotnet.microsoft.com/en-us/download/dotnet/10.0) | 构建 `net10.0` 元数据 helper；当前打包基线使用 SDK `10.0.400`，仅 Runtime 不够，无需安装 Visual Studio |
| [.NET Framework 4.8+](https://dotnet.microsoft.com/en-us/download/dotnet-framework) | pywebview Windows 桌面宿主 |
| [WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/) | 嵌入式桌面界面 |

已安装的工具无需重复安装。[`.python-version`](../../.python-version) 固定为 Python `3.11.14`，与[许可基线](../../third_party/notices/build-baseline.json)一致；[`pyproject.toml`](../../pyproject.toml) 的开发运行要求仍为 `>=3.11`。下面按许可基线准备确切版本，以同时满足便携打包。前端版本检查见 [`check-environment.mjs`](../../frontend/src/app/check-environment.mjs)。

```powershell
git clone https://github.com/1299489456/PSModManager.git
Set-Location PSModManager

git --version
uv --version
node --version
npm --version
dotnet --list-sdks
```

## 2. 同步依赖并构建前端

下面是**显式联网安装步骤**，会创建 `.venv` 与 `frontend/node_modules`：

```powershell
$buildBaseline = Get-Content .\third_party\notices\build-baseline.json -Raw | ConvertFrom-Json
uv python install $buildBaseline.python_version
uv sync --locked --python $buildBaseline.python_version

Push-Location frontend
npm ci
npm run build
Pop-Location
```

`uv sync --locked` 使用现有锁文件，发现依赖声明与锁文件不一致时失败，不自动改锁；行为见 [uv 官方说明](https://docs.astral.sh/uv/concepts/projects/sync/)。npm 依赖以 [`package-lock.json`](../../frontend/package-lock.json) 为准，不要为了首次运行随意升级版本或删除锁文件。

前端产物为 `frontend/dist`。`npm run build` 的 `prebuild` 只检查 Node 和本地依赖，不安装依赖。源码桌面读取构建后的本地 HTML / JS / CSS；修改界面后需重新 build 并重开桌面。`npm run dev` 仅用于浏览器侧界面开发，普通浏览器没有 pywebview 原生桥，不能代替完整桌面流程。

后续命令直接使用 `.venv\Scripts\python.exe`，无需激活环境，也不会因 `uv run` 隐式触发同步。

## 3. 构建开发用元数据 helper

```powershell
.\.venv\Scripts\python.exe .\tools\PSModManager.Metadata\build.py
```

脚本使用已安装的 .NET 10 SDK 和 reference pack，不执行 NuGet restore 或联网下载。默认产物是 `tools/PSModManager.Metadata/bin/PSModManager.Metadata.dll` 及相邻的 `runtimeconfig.json`；源码模式需要系统上的 .NET 10 运行时，SDK 安装已包含它。helper 只解析程序集元数据，不执行被检查的 Mod DLL。协议及自包含构建细节见 [helper 文档](../../tools/PSModManager.Metadata/README.md)。

## 4. 准备本地离线修复资源

Git 仓库不包含生成依赖压缩包 `backend/loaders/melonloader/resources/toolchain.zip`。以下步骤准备原有本地完整包；资源用于 MelonLoader 0.7.3 的 Cpp2IL / Unity 依赖修复，**不是 MelonLoader 安装包**。对外分发应使用第 8 节的公开包流程，仅内置 Cpp2IL 和插件，不再分发 Unity 库。

从已提交的 [`manifest.json`](../../backend/loaders/melonloader/resources/manifest.json) 中读取官方来源，下载到本地准备目录，再用仓库脚本组装：

```powershell
$source = Join-Path (Get-Location).Path '.preview\toolchain-source'
New-Item -ItemType Directory -Path $source -Force | Out-Null
$manifest = Get-Content .\backend\loaders\melonloader\resources\manifest.json -Raw | ConvertFrom-Json
$names = @{
    'Cpp2IL' = 'Cpp2IL.exe'
    'Cpp2IL.Plugin.StrippedCodeRegSupport' = 'Cpp2IL.Plugin.StrippedCodeRegSupport.dll'
    'UnityDependencies' = 'Managed.zip'
}
foreach ($asset in $manifest.sources) {
    Invoke-WebRequest -Uri $asset.url -OutFile (Join-Path $source $names[$asset.component])
}
Copy-Item .\backend\loaders\melonloader\resources\licenses\* -Destination $source
.\.venv\Scripts\python.exe .\tools\prepare_melonloader_offline.py --source $source
```

下载的三个资源是 Cpp2IL `2022.1.0-pre-release.21` Windows EXE、同版 `StrippedCodeRegSupport` 插件，以及 Unity `2021.3.45` 的 `Managed.zip`。下载完成后，组装脚本本身离线运行，不启动这些二进制，也不读取游戏。它生成 `toolchain.zip` 和对应清单；不要将下载材料或生成的 ZIP 提交到源码仓库。修改版本或准备对外分发时，先核对 [第三方材料说明](../../THIRD_PARTY_NOTICES.md) 中的许可和再分发条件。

## 5. 使用隔离数据启动

```powershell
$dataRoot = Join-Path (Get-Location).Path '.preview\dev-data'
.\.venv\Scripts\python.exe .\main.py --psmm-data-root $dataRoot
```

这里传入绝对路径，数据写入 `.preview/dev-data`，不使用正常用户的默认数据目录。需要保留多套调试环境时，更换目录名即可。不要将数据目录放在游戏、Mod 来源目录或已有 RimCrow 数据中。

首次启动会显示数据位置选择；继续使用该独立目录即可。源码模式不提供打包版的 `nxm://` 注册入口。AI 和 Nexus 都可不配置；需要真实网络验证时只使用自己的账户，在界面中输入密钥，不放入源码、环境示例、测试快照或日志。

## 6. 按改动运行测试

先跑与改动相关的测试。下面给出后端基础和前端主流程的直接入口，不启动游戏：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app_identity.py tests/unit/test_contracts.py -q
node --test frontend/src/app/bridge/client.test.js frontend/src/features/catalog/modWorkspaceRuntime.test.mjs
```

其他常用范围：

```powershell
# 数据位置迁移与方案删除，使用测试临时目录
.\.venv\Scripts\python.exe -m pytest tests/integration/test_storage_location.py tests/integration/test_profile_delete.py -q

# 静态元数据解析：需要 .NET 10 SDK，样本由测试自行生成
.\.venv\Scripts\python.exe -m pytest tests/metadata/test_static_parser.py -q

# 前端全部 Node 测试；PowerShell 显式枚举，避免依赖 shell 的通配符展开
$frontendTests = Get-ChildItem .\frontend\src -Recurse -File |
    Where-Object { $_.Name -match '\.test\.(js|mjs)$' } |
    Select-Object -ExpandProperty FullName
node --test $frontendTests
```

测试目录按用途划分：`tests/unit` 为模块检查，`tests/integration` 为服务与文件操作集成检查，`tests/metadata` 为解析和隔离边界检查，`tests/desktop` 为桌面相关检查。其中不少桌面测试检查的是桥接与状态逻辑；真实窗口体验、DPI、干净机器和真实游戏需要单独验证。新增测试优先使用临时目录与合成样本，不能把实际游戏或默认用户数据当测试夹具。

当前已有前端生产构建成功和最终便携包布局检查 24 项通过的本地记录；**不代表全套测试全绿**。运行中检测的历史回归仍有 8 项失败，WebView 退出残留也有未解决的历史失败，详见[已知限制](known-limitations.md)。记录本次命令、通过 / 失败 / 跳过与未测范围，不继承历史结论。

## 7. 构建 Windows x64 便携包

先完成前端 build 和离线资源准备。打包器会核对 Python、Python / npm 依赖、helper runtime 与 `third_party/notices/build-baseline.json` 中的许可基线是否一致，同时校验本地 npm 安装与 `package-lock.json`。当前基线为 Python `3.11.14`、helper runtime `10.0.11`，其余确切依赖版本见该 JSON。

helper 选择本机最高版本的 .NET 10 SDK；当前使用 SDK `10.0.400`，其 runtime 和 reference pack 版本均为 `10.0.11`。需要匹配的 runtime、reference pack 与 win-x64 host pack；安装更高 SDK 后应重新核对许可基线，而不是假设仍产出同版运行时。打包还需要对应的 `Microsoft.NETCore.App.Runtime.win-x64` 本地 NuGet 缓存；准备好匹配的 SDK 后，可**显式联网**缓存一次：

```powershell
dotnet restore .\tools\PSModManager.Metadata\PSModManager.Metadata.csproj -r win-x64
```

`-r win-x64` 指定运行时目标，参见 [dotnet restore 官方说明](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-restore)。打包脚本使用本地缓存副本创建离线 feed；缺材料会报错，不替你安装 SDK、同步依赖或下载运行时。

升级 Python、SDK 或依赖后，需核对新版本的许可证，更新 `third_party/notices/` 中的许可文本、`distribution-materials.json` 和 `build-baseline.json`，必要时重新准备 runtime 缓存。不能只改基线版本来跳过许可核对。

随后使用仓库内一个**尚不存在**的输出目录：

```powershell
$output = Join-Path (Get-Location).Path ('.preview\portable-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\.venv\Scripts\python.exe .\pack_pyinstaller.py --output-root $output
```

入口为 [`pack_pyinstaller.py`](../../pack_pyinstaller.py)，布局由 [`PSModManager.spec`](../../PSModManager.spec) 定义。打包会重新构建自包含 helper，再生成主程序和 WorkerHost；前端使用已存在的 `frontend/dist`，不会替你重建。

成功后的主要输出：

- `dist/PSModManager/`：完整便携目录，包含两个 EXE、共享 `_internal`、用户文档、许可材料与构建清单。
- `PSModManager-v0.1.0-windows-x64.zip`：对应的完整 ZIP。
- `result.json`：产物路径、体积等结果；`helper.log`、`pyinstaller.log` 用于排查构建失败。

检查刚生成的实际目录和 ZIP，而不是只检查测试夹具：

```powershell
$result = Get-Content (Join-Path $output 'result.json') -Raw | ConvertFrom-Json
$env:PSMM_PORTABLE_ROOT = $result.bundle
$env:PSMM_PORTABLE_ZIP = $result.archive
$env:PSMM_FINAL_DELIVERY = '1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_package_layout.py -q
Remove-Item Env:PSMM_PORTABLE_ROOT, Env:PSMM_PORTABLE_ZIP, Env:PSMM_FINAL_DELIVERY
```

未设置产物路径时，相关包体检查会跳过；跳过不算验证了便携包。目录 / ZIP 检查通过后仍需实际桌面启动与目标环境检查，见[干净机器检查表](clean-machine-checklist.md)。构建本地产物不会创建 GitHub Release，也不代表已完成发布签名或第三方服务的公开发行登记。

## 8. 自动构建并发布 GitHub Release

[`Windows portable` 工作流](../../.github/workflows/release.yml)在 `main` 推送时构建、测试并上传保留 14 天的 Actions artifact；**只有推送 `v主版本.次版本.补丁版本` 标签才自动创建 Release**。普通源码提交不会发布新版本。

每次更新版本时，同步以下三处，然后提交并推送 `main`：

- `pyproject.toml` 的 `project.version`。
- `backend/app/identity.py` 的 `APP_VERSION`（打包器直接使用它）。
- `uv.lock` 中 `psmodmanager` 的版本：修改声明后执行 `uv lock`，检查差异没有意外升级依赖。

确认该提交的 Actions 构建成功，再推送相同版本的标签，例如：

```powershell
# 示例仅适用于已更新为 0.1.1 的提交；标签与源码版本必须一致。
git tag -a v0.1.1 -m "Release PSModManager 0.1.1"
git push origin v0.1.1
```

CI 固定 Windows 2022 x64、uv `0.10.0`、Python `3.11.14`、Node `24.19.0`、.NET SDK `10.0.400`；通过 `uv sync --locked`、`npm ci` 和 runtime `10.0.11` 准备干净构建环境。第三方 Actions 固定到提交。升级构建版本时，同时更新工作流和许可基线；不要绕过打包器的版本检查。

公开包构建使用现有单入口，但传入独立的 Cpp2IL-only 资源，不修改本地完整资源目录：

```powershell
# 显式联网下载两项官方工具，校验来源清单中的大小和 SHA-256。
.venv\Scripts\python.exe tools/prepare_public_toolchain.py --download --output-dir .preview\public-toolchain-01
.venv\Scripts\python.exe pack_pyinstaller.py --public-release --offline-resources .preview\public-toolchain-01 --output-root .preview\public-release-01
```

已有下载文件时用 `--source <文件目录>` 代替 `--download`，目录需包含 `Cpp2IL.exe` 和 `Cpp2IL.Plugin.StrippedCodeRegSupport.dll`。公开包的包体验证在第 7 节命令之外加 `$env:PSMM_PUBLIC_RELEASE = '1'`；检查后移除此环境变量。公开包只保留两项离线工具，Unity 库由 MelonLoader 首次生成时下载，国内网络可能仍影响 Unity 下载。

工作流先通过定向测试、构建和实际 ZIP 检查，再以单独发布任务的 `contents: write` 权限创建草稿，上传 ZIP 与 ZIP 外部的 `SHA256SUMS.txt`，两者成功才公开。无需填写个人 PAT 或其他 Secret；构建任务只有读权限。仓库需允许 GitHub Actions 和相应工作流令牌写入 Release。

构建失败时在 Actions 查看失败步骤和日志，修正后先推送 `main` 验证。已经存在且源码未变的标签，可在 Actions 的 **Run workflow** 输入标签，或重跑失败任务。若上传中断留下草稿，先人工检查并删除那个未发布草稿，再重试；脚本不会自动覆盖或删除任何已有 Release/附件。已公开版本需要修复时发布新版本，不移动旧标签。

Release 资产和 Actions artifact 都只包含便携 ZIP、校验清单，不包含真实配置、截图用数据、凭据或 Mod。公开包没有程序内自动升级器；这里实现的是开发者推送版本标签后自动编译、发布下载包。

## 代码导航

| 要改什么 | 从哪里看起 |
|---|---|
| 桌面启动、数据位置、桥接、窗口生命周期 | `main.py`、`backend/app/` |
| 主界面、设置、前后端通信 | `frontend/src/app/`、`frontend/src/app/bridge/` |
| Mod 列表、导入与版本替换 | `frontend/src/features/catalog/`、`frontend/src/features/imports/`、`backend/catalog/` |
| 保存启停、文件部署与恢复 | `backend/deployments/mod_workspace.py`、`ordinary.py`、`mods.py` |
| 方案、分享与删除 | `backend/profiles/`、`frontend/src/features/profiles/` |
| 游戏识别、启动、MelonLoader 环境与安装 | `backend/games/probably_stolen/`、`backend/loaders/melonloader/` |
| DLL 静态元数据与隔离 worker | `backend/scanning/`、`tools/PSModManager.Metadata/` |
| 日志与可选 AI | `backend/diagnostics/`、`backend/ai/desktop_runtime.py`、`frontend/src/features/ai/` |
| Nexus 获取与 `nxm://` 接收 | `backend/online/`、`frontend/src/features/online/` |
| SQLite 数据、异步任务 | `backend/storage/`、`backend/runtime/` |

仓库保留了部分 RimCrow 历史模块；当前产品不从 `backend/api`、`backend/managers` 或旧 `settings` 入口启动。修改前沿当前桌面调用链确认消费者，避免把历史功能接回新入口。

## 贡献与设计资料

- 小改动采用“相关上下文 → 连贯修改 → 定向验证 → 一次交付”，保留用户修改，不做顺手的大范围重构。
- 修改文件写入、数据迁移、导入、凭据、网络或进程隔离时，覆盖对应失败与恢复路径。不要通过关闭安全检查让测试通过。
- 提交前检查差异：不提交 `.venv`、`node_modules`、`.preview`、构建产物、个人配置 / 数据库、密钥、真实 Mod / 游戏或存档。
- 新增或调整依赖应同时维护依赖声明与锁文件；没有需要时不升级依赖。分享包和程序包中的第三方材料分别核对许可。

需要设计背景时，按主题查阅[产品范围](01-product-scope.md)、[架构](02-architecture.md)、[数据契约](03-data-contracts.md)、[部署行为](04-deployment-protocol.md)、[日志与 AI](05-diagnostics-ai.md)、[验证方式](08-verification-guide.md)。[路线](09-roadmap.md)、[任务台账](task-register.json)与任务卡保留历史决策和原验证状态，不是新人启动的前置清单；G0～G5 不再作为普通开发的强制审批门。
