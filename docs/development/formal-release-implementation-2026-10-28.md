# 0.2.0：按已确认原型实现正式版原生模组管理

本文保留 0.2.0 首次实现记录；后续 Steam 直接订阅、更新、AI 翻译与封面修订见 [0.2.1 使用体验修订](workshop-usability-2026-10-28.md)。

## 使用入口和界面基线

界面以用户确认的 [第二版温室原型](../design/formal-release-ui/prototype.html) 为基线。正式程序使用 Vue + pywebview/WebView2，原型本身仍是独立演示文件，没有改成产品入口。

源码启动：在仓库根目录使用 `.venv/Scripts/python.exe main.py`。前端产物位于 `frontend/dist`，使用现有 `npm run build` 构建。版本号、项目元数据和应用内更新记录均为 **0.2.0**。本轮没有提交、发布或更新用户的正式游戏配置。

本地便携版（保留整个目录，不单独移动 EXE）：

- 程序：[PSModManager.exe](../../.preview/native-portable-20261028-03/dist/PSModManager/PSModManager.exe)
- 压缩包：[PSModManager-v0.2.0-windows-x64.zip](../../.preview/native-portable-20261028-03/PSModManager-v0.2.0-windows-x64.zip)
- 当前本地交付指针：`docs/development/current-local-delivery.json`，与线上发布状态分开记录。

## 已接通的功能

| 区域 | 实际行为 |
|---|---|
| 外框与设置 | 保留温室配色、标题栏、藤蔓导航、游戏安装牌子、太阳启动按钮、背景叶片和设置页结构；导航改为创意工坊，去掉 MelonLoader 状态胶囊 |
| 模组三栏 | 可用模组、启用与顺序、详情；支持跨栏拖动、多选、双击、键盘移动、撤销重做、筛选与底部待保存篮子；窄窗口用详情抽屉 |
| 启用 | 只改草稿，不弹权限提示；必要依赖一起加入，停用基础模组时列出反向依赖；保存时再进行原生预检 |
| 排序 | 稳定拓扑排序、循环检测、自定义前后规则；规则可保存并随方案恢复。文案明确是保存顺序，不能保证 DLL 初始化顺序 |
| 详情 | 真实包内封面、名称/作者/版本/来源、四格信息、生长路线、概览/权限/依赖/配置/文件/运行记录六页签、放大详情；权限与本机批准分别展示 |
| 写入 | 修改 `SaveFile.es3` 的原生顺序字段，保留其他设置；停用不删除包，不取消订阅；不生成或恢复历史批准 |
| 配置 | 读取 `ModConfigs/<ID>.json`，按字符串编辑未知类型；保留未提交键及未知字段；保存前备份，恢复后重新读取界面 |
| 本地导入 | 原生目录或 ZIP 的预览、完整包安装；拒绝裸 DLL、危险路径、链接、大小写冲突和过量解压；不执行 DLL |
| 本地来源 | 同 ID 来源盘点、本地遮蔽工坊提示、改用工坊副本、移除本地包；先建恢复点，不写 Steam 工坊目录 |
| 方案 | 创建、编辑、复制、重命名、删除、导入导出、载入草稿；保留 ID、顺序、来源、版本和规则，提示本机来源或版本变化；旧方案逐项显示迁移预览及跳过原因，不调用旧执行器 |
| 恢复 | 状态、配置、本地包及标准模组数据恢复点；未完成备份不可恢复，未完成游戏写入阻断后续写入；恢复前也备份当前状态 |
| Steam | 公开条目浏览、分页、搜索、排序、封面和详情、本机包盘点；订阅/取消订阅打开官方界面，返回后刷新核对，不伪造操作成功 |
| 日志与 AI | 原生 Player.log、最近启动对账、原笔记本日志与右侧智能分析；AI 工具只读，建议卡回到对应模组，不自动执行旧部署动作 |
| 游戏保护 | Mono/正式版识别、安装互斥锁、运行或未知状态拒写、外部文件漂移检测；运行中仍可编辑草稿；保存结果与运行结果分开确认 |
| 旧功能退役 | 桌面不再暴露 MelonLoader 安装/修复、Nexus 账号/下载、nxm 接入、旧 mod.action/plan.apply 等写入口；历史数据保留，不自动清理用户旧文件 |

应用里的安装识别分别显示菜单版本、Steam Build、Unity 版本和最近启动记录中的兼容版本；不能把旧日志兼容版本当成当前安装已核实的版本。

## 模块与数据边界

- `frontend/src/features/native/`：原型组件、草稿状态机、规则/依赖算法、原生桥接字段校验。
- `backend/native_mods/`：清单、原生状态、包、方案、规则及恢复事务。[服务合同](../../backend/native_mods/CONTRACT.md)。
- `backend/app/native_api.py`：已登记安装的权威解析、受控文件选择授权、方案分享、封面与目录打开；页面不传任意读写路径。
- `backend/workshop/`、`backend/app/workshop_api.py`：Steam 公开数据与官方操作交接。[服务合同](../../backend/workshop/CONTRACT.md)。
- `backend/diagnostics/native_log.py`、`backend/ai/`：结构化原生加载证据和可选 AI 查询。
- `backend/games/probably_stolen/native_identity.py`：只读安装识别，不执行程序集。
- 管理器私有方案、规则和恢复点位于数据根的 `native-mods/`；数据位置迁移完整复制它们。游戏目录、工坊目录和原生 LocalLow 数据目录不能作为管理器数据根或方案导出目标。

写入返回时重新检查已释放的进程互斥锁，避免把自己的锁当成永久只读；每个返回的库存快照都保留版本识别结果。

## 真实数据与演示数据

本轮只读盘点为 **7 个原生包、2 个保存启用项**，最近 Player.log 记录 `item_details` 和 `loyce_shop_assist` 已加载。它与原型旧快照的“7 启用、1 运行”不同，正式界面读取当前文件而不固定演示数字。

写入和桌面验收使用 `.preview/` 中的真实包、ES3、配置和日志副本。该环境的游戏 EXE 是项目自编写、限时退出的进程探针；没有执行游戏或模组 DLL。依赖环、危险 ZIP、故障恢复等反例采用明确的测试夹具，不能据此宣称真实七个包存在这些问题。

## 验证记录

| 检查 | 结果与范围 |
|---|---|
| 原生服务最终候选 | `python -m pytest tests/native_mods -q`：65 通过，包含真实文件副本及导入、漂移、失败恢复、方案来源/版本/规则、旧格式迁移的补充反例 |
| 工坊后端 | `tests/workshop`：49 通过；另实读 Steam 官方热门、分页、搜索及条目详情；未提交订阅或取消订阅 |
| 核心集成批次 | 原生服务、工坊、桌面 API、数据迁移、启动、AI 超时恢复、原生日志及产品身份组合：236 通过。其后修复由下列最终定向检查覆盖，不把早期批次当成后续修改的证据 |
| 宿主边界最终定向 | `tests/integration/test_native_desktop_api.py tests/unit/test_native_file_dialogs.py tests/unit/test_app_identity.py`：49 通过；包括写锁释放后的可写状态、版本信息保留、规则与来源往返、导出/数据根保护 |
| 数据迁移 | `tests/integration/test_storage_location.py`：18 通过；实际复制后重开、恢复原生方案、AI 凭据迁移与失败保留 |
| 前端 | 172 项定向测试通过；production build 成功。保留既有 Vite 单包大于 500 KB 提示，无构建失败 |
| 实际 Edge | 1600×900、1366×860、1100×760；真实鼠标跨栏拖动、Ctrl 多选、Alt+Home、Ctrl+Z/Y/S，配置保存/恢复、方案规则往返和真实工坊列表/详情已操作验证 |
| 实际 WebView2 | 同三档窗口、96 DPI；六页签及放大详情、保存读回/运行分离、安装识别、方案/日志/设置通过，0 个页面错误；独立限时 EXE 探针验证运行中可改草稿、保存禁用且 ES3 不变。结果见 `.preview/formal-release-webview-20261028-c/result.json` 与 `-d/result.json` |
| 冻结启动依赖 | 真实便携构建发现旧 inventory/graph 仍导入被排除的 Loader 模块，已将纯静态历史规则迁到 `backend/scanning/legacy_loader_rules.py`；旧路径仅留源码测试兼容导出，便携包仍排除 Loader。规则与 graph 定向 97 通过，模拟冻结排除启动 1 通过 |
| 最终便携包 | `.preview/native-portable-20261028-03`：实际包体与 ZIP 检查 27 通过；冻结 EXE 在隔离数据/用户目录启动，确认 0.2.0、WebView2 窗口与日志，正常关闭 exit 0，无遗留子进程。没有进行发布或干净机器全交互测试 |
| 版本与语法 | `tools/check_release_version.py` 返回 0.2.0；新增宿主代码编译检查通过 |

可复用桌面验收入口：

```powershell
.venv/Scripts/python.exe tests/desktop/harness/formal_release_backend.py --evidence-root .preview/<新的验收目录>
.venv/Scripts/python.exe tests/desktop/harness/formal_release_webview.py --evidence-root .preview/<另一个新的验收目录>
```

前者只在测试时将真实 DesktopApi 接到 loopback HTTP，正式桌面不带 HTTP/RPC 服务。后者使用实际 WebView2 和本地桥接，写入仅指向隔离副本。两个入口要求全新的 `.preview` 子目录，避免覆盖前次证据。

## 仍需分清的能力边界

1. **游戏批准与真实重启效果**：批准只能由游戏完成。本轮未对正式游戏执行启用写入或运行 Mod；首次正式使用时，应在游戏完成批准、按需要重启，再回到管理器刷新运行记录。探针启动和副本读回不等于真实 Mod 运行验收。
2. **Steam 获取通道**：当前提供已登录 Steam 客户端的官方交接；没有应用内直接代订阅会话，也未验证本游戏匿名下载。离线包可查看，缺少下载状态时显示未知，不推算虚假百分比。
3. **静态分析覆盖**：当前独立检查包结构与受控 PE/CLI 头，展示游戏历史检测记录；没有完整 IL 权限扫描、全部 IMod 入口枚举或补丁交集分析。这些字段显示未扫描/待核对，不表示安全或兼容性通过。
4. **备份范围**：标准 `ModConfigs`、`ModData` 及本次操作对象；不把所有自定义 UserData 和游戏存档纳入未声明的“完整备份”。
5. **桌面覆盖**：实际浏览器手势与 WebView2 分开验证。多档系统 DPI、操作系统文件选择器手势、资源管理器拖入窗口以及外部 AI 供应商响应尚未作为实机通过项。
