# 从源码运行与开发 PSModManager

当前桌面入口是 `main.py` → `backend/app` → `frontend/src/app`。面向 Probably Stolen 正式版的原生 Mono 模组，主流程为“选择游戏 → 导入目录 / ZIP 或打开 Steam 创意工坊 → 启用与排序 → 保存 → 运行”。AI 只在需要时辅助日志排错。

界面和交互以已确认的[正式版温室原型](../design/formal-release-ui/prototype.html)及其[说明](../design/formal-release-ui/README.md)为准，不重新设计设置、日志与外框。模组页为“可用模组 → 启用与顺序 → 模组详情”三栏；启用不弹权限提示，权限列在详情中，游戏批准仍由游戏完成。原型演示数据不得作为正式版运行状态。

以下命令使用 **Windows x64 + PowerShell**，除单独说明外均在仓库根目录执行。开发和测试使用独立数据目录与获准的隔离副本；合成样本用于边界情况，不替代真实包的只读检查。隔离数据目录不会把真实游戏变成测试副本，请勿为调试选择真实游戏并执行写入或启动。

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

## 4. 原生游戏与写入边界

正式版不需要 MelonLoader、Cpp2IL 或 Unity 生成依赖；源码启动、本地打包和公开构建都不下载、不安装、不打包这些工具。历史准备脚本与许可材料保留，不再是启动前置。打包器拒绝旧 `--offline-resources` 参数并提示移除；旧 `PSMM_OFFLINE_RESOURCES` 环境变量不参与资源收集。

原生模组读取游戏 `Mods` 和同一 Steam 库的 `workshop/content/4348910`。用户状态来自游戏的 `SaveFile.es3`，配置来自 `ModConfigs`，运行证据来自 `Player.log`。分别记录已安装、目标启用、游戏批准和最近启动的加载结果；不能从已启用或已批准推断正在运行。排序仅保存 `modOrder`，当前游戏先加载 DLL 再应用顺序，不能承诺初始化顺序。

原生写入必须经过进程 guard、并发版本核对与恢复点；游戏运行中或无法确认是否运行时，只编辑草稿。修改 ES3 保留类型包装与未知字段，不创建批准记录。工坊订阅、取消和更新通过本机已登录 Steam 的官方 UGC 接口请求，管理器不手写 ACF 或工坊文件。发起操作时也检查游戏运行状态；已被 Steam 接受的任务由客户端继续执行，不受原生恢复点回滚。匿名下载不是订阅，未验证本游戏支持前保持不可用。服务接口见[原生契约](../../backend/native_mods/CONTRACT.md)与[工坊契约](../../backend/workshop/CONTRACT.md)。

Steam UGC 使用独立 spawn 子进程与私有管道；`main.py` 在桌面参数解析前调用 `multiprocessing.freeze_support()`。原生 DLL 仅从所选游戏的 `Probably Stolen_Data/Plugins/x86_64/steam_api64.dll` 读取，先固定文件句柄并核验 Valve 签名，不复制进程序包。冻结验收除主窗口外还须覆盖实际子进程启动、只读状态返回、退出和原生输出静默；源码测试不能代替这条路径。

## 5. 使用隔离数据启动

```powershell
$dataRoot = Join-Path (Get-Location).Path '.preview\dev-data'
.\.venv\Scripts\python.exe .\main.py --psmm-data-root $dataRoot
```

这里传入绝对路径，数据写入 `.preview/dev-data`，不使用正常用户的默认数据目录。需要保留多套调试环境时，更换目录名即可。不要将数据目录放在游戏、Mod 来源目录或已有 RimCrow 数据中。

首次启动会显示数据位置选择；继续使用该独立目录即可。AI 可不配置；需要真实网络验证时只使用自己的账户，在界面中输入 AI 密钥，不放入源码、环境示例、测试快照或日志。工坊公开浏览不要求账号；直接订阅和更新使用官方客户端会话，不读取密码或登录令牌。独立应用数据目录不会隔离 Steam 账号副作用，实际订阅写入需单独获准；当前不注册 `nxm://` 协议。

## 6. 按改动运行测试

先跑与改动相关的测试。下面给出后端基础和前端主流程的直接入口，不启动游戏：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_app_identity.py tests/unit/test_contracts.py tests/native_mods tests/workshop -q
node --test frontend/src/app/bridge/client.test.js frontend/src/features/native/nativeWorkspaceRuntime.test.mjs frontend/src/features/native/nativeModel.test.mjs
```

其他常用范围：

```powershell
# 数据位置迁移，使用测试临时目录
.\.venv\Scripts\python.exe -m pytest tests/integration/test_storage_location.py -q

# 静态元数据解析：需要 .NET 10 SDK，样本由测试自行生成
.\.venv\Scripts\python.exe -m pytest tests/metadata/test_static_parser.py -q

# 前端全部 Node 测试；PowerShell 显式枚举，避免依赖 shell 的通配符展开
$frontendTests = Get-ChildItem .\frontend\src -Recurse -File |
    Where-Object { $_.Name -match '\.test\.(js|mjs)$' } |
    Select-Object -ExpandProperty FullName
node --test $frontendTests
```

测试目录按用途划分：`tests/unit` 为模块检查，`tests/integration` 为服务与文件操作集成检查，`tests/metadata` 为解析和隔离边界检查，`tests/desktop` 为桌面相关检查。其中不少桌面测试检查的是桥接与状态逻辑；真实窗口体验、DPI、干净机器和真实游戏需要单独验证。新增测试优先使用临时目录与合成样本，不能把实际游戏或默认用户数据当测试夹具。

每次记录本次命令、通过 / 失败 / 跳过与未测范围，不继承历史构建或截图结论。[已知限制](known-limitations.md)保留历史和当前缺口。尤其要分别验证保存后回读、实际游戏启动后的 `Player.log` 对账、真实鼠标拖动与快捷键、WebView2 和系统缩放；接口测试不能替代这些检查。

## 7. 构建 Windows x64 便携包

**每次向用户交付修复或功能更新，都必须递增产品版本号并写更新记录，包括仅在本地交付的便携包。** 同一候选版本的失败重试不用再递增。修复和小幅体验改动通常递增补丁号（如 `0.1.0 → 0.1.1`）；较大的新增功能递增次版本号；已公开的版本不覆盖、不移动旧标签。

交付前同步以下来源：

- `backend/app/identity.py` 的 `APP_VERSION`：应用信息、EXE 打包清单与 ZIP 名称的产品版本来源。
- `pyproject.toml` 的 `project.version`。
- `uv.lock` 中 `name = "psmodmanager"` 对应的版本。仅更新项目版本时，不升级任何依赖；如运行 `uv lock`，必须检查差异。
- `docs/user/release-notes.md`：在最上方添加 `# 版本号 · YYYY-MM-DD`，列出用户可见的修复和变化，保留旧版记录。设置里的“更新记录”直接读取这份文档，Markdown 会在构建时一并嵌入，离线可读；GitHub Release 也从中提取对应版本的更新内容。

`frontend/package.json` 的 `0.0.0` 是私有前端构建包的标识，不是产品版本；遗留 `backend/_version.py` 也不用于当前桌面入口，均不随产品版本更新。

先检查四处是否一致，再重新构建前端，避免把旧更新记录带入新包：

```powershell
.\.venv\Scripts\python.exe tools/check_release_version.py
if ($LASTEXITCODE) { throw '版本号或更新记录不一致，请先修正。' }
npm --prefix frontend run build
```

先完成前端 build。打包器会核对 Python、Python / npm 依赖、helper runtime 与 `third_party/notices/build-baseline.json` 中的许可基线是否一致，同时校验本地 npm 安装与 `package-lock.json`。当前基线为 Python `3.11.14`、helper runtime `10.0.11`，其余确切依赖版本见该 JSON。原生 Mono 构建不需要任何加载器离线资源。

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
- `PSModManager-v<版本号>-windows-x64.zip`：对应版本的完整 ZIP。
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

按第 7 节完成版本递增、更新记录和本地验证。获得提交与发布授权后，再提交并推送 `main`。CI 的 `check_release_version.py` 会同时核对标签、三个版本来源和最新更新记录，缺少当前版本记录或记录为空都会停止构建。

确认该提交的 Actions 构建成功，再推送相同版本的标签，例如：

```powershell
# 示例仅适用于已更新为 0.2.3 的提交；标签与源码版本必须一致。
git tag -a v0.2.3 -m "Release PSModManager 0.2.3"
git push origin v0.2.3
```

CI 固定 Windows 2022 x64、uv `0.10.0`、Python `3.11.14`、Node `24.19.0`、.NET SDK `10.0.400`；通过 `uv sync --locked`、`npm ci` 和 runtime `10.0.11` 准备干净构建环境。第三方 Actions 固定到提交。升级构建版本时，同时更新工作流和许可基线；不要绕过打包器的版本检查。

公开包与本地包使用相同原生资源清单；`--public-release` 仅选择公开交付标识：

```powershell
.venv\Scripts\python.exe pack_pyinstaller.py --public-release --output-root .preview\public-release-01
```

构建清单的 `distribution_profile` 分别为 `public-native-mono` 与 `local-native-mono`。公开包的包体验证在第 7 节命令之外加 `$env:PSMM_PUBLIC_RELEASE = '1'`；检查后移除此环境变量。发布校验只接受公开原生包，拒绝旧加载器包和本地标识包。许可文本、元数据 helper、自包含运行时与完整性校验继续保留。

工作流先通过定向测试、构建和实际 ZIP 检查，再以单独发布任务的 `contents: write` 权限创建草稿，上传 ZIP 与 ZIP 外部的 `SHA256SUMS.txt`，两者成功才公开。无需填写个人 PAT 或其他 Secret；构建任务只有读权限。仓库需允许 GitHub Actions 和相应工作流令牌写入 Release。

构建失败时在 Actions 查看失败步骤和日志，修正后先推送 `main` 验证。已经存在且源码未变的标签，可在 Actions 的 **Run workflow** 输入标签，或重跑失败任务。若上传中断留下草稿，先人工检查并删除那个未发布草稿，再重试；脚本不会自动覆盖或删除任何已有 Release/附件。已公开版本需要修复时发布新版本，不移动旧标签。

Release 资产和 Actions artifact 都只包含便携 ZIP、校验清单，不包含真实配置、截图用数据、凭据或 Mod。公开包没有程序内自动升级器；这里实现的是开发者推送版本标签后自动编译、发布下载包。

## 代码导航

| 要改什么 | 从哪里看起 |
|---|---|
| 桌面启动、数据位置、桥接、窗口生命周期 | `main.py`、`backend/app/` |
| 主界面、设置、前后端通信 | `frontend/src/app/`、`frontend/src/app/bridge/` |
| 原生包扫描、依赖排序、启用状态、配置与恢复点 | `backend/native_mods/`，接口见同目录 `CONTRACT.md` |
| 原生方案与旧方案迁移预览 | `backend/native_mods/`、`backend/profiles/` |
| 游戏识别与启动 | `backend/games/probably_stolen/` |
| DLL 静态元数据与隔离 worker | `backend/scanning/`、`tools/PSModManager.Metadata/` |
| 日志与可选 AI | `backend/diagnostics/`、`backend/ai/desktop_runtime.py`、`frontend/src/features/ai/` |
| Steam 创意工坊浏览、订阅、下载状态与更新 | `backend/workshop/`，接口见同目录 `CONTRACT.md` |
| SQLite 数据、异步任务 | `backend/storage/`、`backend/runtime/` |

仓库保留了部分 RimCrow、MelonLoader、Nexus 历史模块；当前产品不从 `backend/api`、`backend/managers` 或旧 `settings` 入口启动。旧方案只预览迁移，不调用旧文件启停执行器。修改前沿当前桌面调用链确认消费者，避免把历史功能接回新入口。

## 贡献与设计资料

- 小改动采用“相关上下文 → 连贯修改 → 定向验证 → 一次交付”，保留用户修改，不做顺手的大范围重构。
- 修改文件写入、数据迁移、导入、凭据、网络或进程隔离时，覆盖对应失败与恢复路径。不要通过关闭安全检查让测试通过。
- 提交前检查差异：不提交 `.venv`、`node_modules`、`.preview`、构建产物、个人配置 / 数据库、密钥、真实 Mod / 游戏或存档。
- 新增或调整依赖应同时维护依赖声明与锁文件；没有需要时不升级依赖。分享包和程序包中的第三方材料分别核对许可。

需要设计背景时，按主题查阅[产品范围](01-product-scope.md)、[架构](02-architecture.md)、[数据契约](03-data-contracts.md)、[部署行为](04-deployment-protocol.md)、[日志与 AI](05-diagnostics-ai.md)、[验证方式](08-verification-guide.md)。[路线](09-roadmap.md)、[任务台账](task-register.json)与任务卡保留历史决策和原验证状态，不是新人启动的前置清单；G0～G5 不再作为普通开发的强制审批门。
