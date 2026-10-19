# PS-00-01 固定参考源码导入

这是 2026-09-28 导入时的历史记录；下文“尚未实施”和旧入口禁用说明只描述当时状态。当前范围和实际可用入口以 [README](README.md)、[产品范围](01-product-scope.md) 和现有代码为准，不据此恢复旧 RimCrow 启动链。

本次仅建立供 PS-00-02 直接改造的本地源码基线。`main.py`、`backend/`、`frontend/` 是实际消费者路径，不是另建的 Mock 或示例工程。旧启动链仍保留，**不要运行或 import 旧 main/settings/API，也不要执行 npm build/prebuild**；独立启动、命名空间和禁用入口的验收属于 PS-00-02，尚未实施。

## 来源、导入与排除

- 只读来源：`E:\WorkSpace\GitHub\RimCrow`，固定提交 `a7e875112a175a914bb4706a82589d96d62adc70`，源码版本 `0.24.7`。
- 导入前后 HEAD、完整 porcelain 状态、子模块状态和所有已跟踪普通文件 SHA256 相等；源工作区干净。未对源执行 checkout、更新索引、安装或应用启动。未读取忽略的用户数据内容。
- SteamworksPy gitlink：`c021d1c186084d22a21817fc4fe5fad1e1c20a7e`，未初始化，未导入。
- 通过 `git cat-file blob` 读取固定对象，不复制源工作树：395 个文件逐字节导入，差异 0。白名单是卡片中的五个根文件及 backend/frontend/tests/scripts 中的源码、JSON、文本、SVG；16 个已跟踪条目排除。
- 排除 `.git/.codegraph/.venv`、依赖、构建产物、源用户数据/数据库/凭据、游戏/模组二进制、上游 doc、根打包器及品牌图片。不覆盖原 docs；导入前已对全部现有文件取哈希，导入后逐一比对。
- 未初始化本项目 Git、远程或提交。工作区版本由文件清单和 SHA256 表示。
- 可复核直接入口：`python -B scripts/reference_baseline.py`。仅使用标准库；冲突检查先于导入，已有文件不一致就失败而不覆盖；每次生成独立 UTC run。后续任务开始修改源码后，不应将重新导入脚本当作修复工具。

完整逐文件清单、Git blob、双端 SHA256、模块去向和排除对象见 [import-manifest.json](evidence/PS-00-01/20260928T032245703563Z/import-manifest.json)、[excluded.json](evidence/PS-00-01/20260928T032245703563Z/excluded.json)。

## 模块去向与消费者责任

本次所有导入内容保持上游字节，以下是后续裁剪责任，不代表已经禁用。逐文件 disposition 是保留/改写/移除活动入口的完整索引；本表解释分组策略。

| 模块 | 去向和原因 | 消费任务 |
|---|---|---|
| main、settings、paths/profile、migrations、startup、window_state、platform | 改写独立身份及惰性启动；移除旧路径探测、配置/凭据迁移、自动扫描 | PS-00-02 |
| api、browser_runtime、前端 bridge/app/stores | 重建窄 API、受控事件；移除 browser RPC；不继承整个旧 facade | PS-00-02、PS-00-04 |
| database | 新建独立 schema；仅参考连接/迁移机制，禁止复制旧 DB | PS-00-03 |
| scanner、load_order、managers | 重写安装/库存/关系/事务语义；不沿用 RimWorld 扫描与文件修改行为 | PS-01-01～PS-03-07，按 02-architecture 对应行 |
| Steam/Workshop/SteamCMD、download/github/sub_browser、texture、multiplayer、rules、update | 从活动入口和启动依赖移除；本次仅保留固定源码供核对 | PS-00-02、PS-00-04 |
| ai、translation、text_search | 按明确领域需求选择性迁移；工具、提示和动作重新授权，不能整体接入 | PS-04-01～PS-04-04及领域任务 |
| utils、i18n、theme_store、static_page | 选择性保留机制；logger/secret_store/restart/tools 等存在副作用，不能当纯函数直接复用 | PS-00-02、PS-00-04及领域任务 |
| frontend/shared、locales、features | 保留视觉参考；按产品重接状态、品牌和业务，不把旧按钮隐藏视作隔离 | PS-00-02及页面责任任务 |
| tests、scripts、锁文件、Python pin | 保留历史可追溯输入；旧测试须审计导入和资源路径后才执行 | PS-00-02起；统一 runner 为 PS-00-05 |

已确认的旧链路风险：

- `main.py:48` 导入 settings，且顶层改变 DPI/工作目录；`backend/settings.py:908` 实例化 SettingsManager，初始化会建目录、加载配置、迁移凭据（约 399～407、570 行）。
- `backend/api.py:590` 初始化 DB，约 651 行建立旧 profile context、701 行涉及 SteamCMD 路径同步。旧扫描约 214 行包含缺失模组 DB 清理；不是只读扫描。
- `backend/utils/logger.py` 导入 settings 并创建日志 handler；secret_store 默认服务仍是 RimCrow，包含旧 service 迁移。
- 原 main 依赖根 `validate_environment.py`；该文件不在本卡白名单，未导入。原 pyproject 的 README、前端 branding 的根 icon.svg、捐赠图片、旧打包测试所需根打包器也被明确排除。PS-00-02 应重写入口/品牌而不是运行旧程序补齐这些依赖；打包入口归 PS-05-02。
- `frontend/package.json` 的 prebuild → i18n:check → `uv run` 会隐式同步；未执行，PS-00-02 必须消除。

## 工具、依赖与旧测试盘点

本次工具：Python 3.13.12、Node v24.19.0、npm 11.17.0、uv 0.10.0、Git 2.54.0.windows.1、PowerShell 7.6.1；.NET SDK 有 8.0.421、10.0.108、10.0.400。WebView2 注册表版本为 153.0.4234.48，尚未用实际窗口验证。

`.python-version` 固定 3.11；已登记 Python 为 3.12/3.13/3.14，未发现 3.11。项目 `.venv` 和 `frontend/node_modules` 不存在。全局 Python 缺 pywebview、peewee、steam-vdf、keyring 等；全局 openai 2.14.0 低于声明下限 2.15.0，不能用全局环境冒充锁文件环境。每项声明、锁定版本和全局发现值见 [environment.json](evidence/PS-00-01/20260928T032245703563Z/environment.json)。未安装、恢复或下载依赖。

前端锁定 Vite 7.3.6，锁中 engines 为 `^20.19.0 || >=22.12.0`，当前 Node 满足。另核对 [Vite 7 官方说明](https://vite.dev/blog/announcing-vite7) 的 Node 20.19+/22.12+ 要求，不能按旧 README 的 Node 18+ 判断。

74 个历史测试/辅助文件列入 [legacy-tests.json](evidence/PS-00-01/20260928T032245703563Z/legacy-tests.json)；Python AST 静态发现 853 个 `test_` 函数，**不是 pytest 收集数**。测试实际执行 0，全部 NOT_RUN，进程退出码为 null：缺独立环境，且旧测试可能导入有副作用的 settings。旧 JS loader 还会 mock 部分模块，不能证明真实桌面桥接。未使用历史报告中的测试成功作为当前证据。

静态解析共 286 项通过：181 Python AST/内存 compile、10 JSON、2 TOML（含 uv.lock）、93 JS/MJS `node --check`；没有执行编译后的代码或生成 pyc。Python 3.13 对 `backend/static_page.py` 给出 3 类无效转义 SyntaxWarning（AST/compile 各报告一次），保留上游内容并记录，不将其改成零警告。Vue SFC 编译、原测试、构建、独立桌面、真实游戏均未运行。

## 许可证与第三方

LICENSE 保留完整 MIT 及 `Copyright (c) 2026 RimCrow contributors`。依赖的锁定来源、272 个 npm 包的许可证声明/完整性、115 个 Python 锁条目的来源、三个 SVG 资源和排除子模块分别登记在 [third-party-sources.json](evidence/PS-00-01/20260928T032245703563Z/third-party-sources.json)。

锁文件的包来源和 license 字段是来源盘点，不是全部第三方分发审核。Python 包许可证、品牌 SVG、上游提及的 RimSort 等关联项目素材的独立许可仍由 PS-05-04 核实；本次不安装这些包、不将游戏内容或第三方模组当 fixture、不作分发授权结论。

## 验证与交接边界

CASE-001/PS-00-01 的 synthetic 责任是固定提交、清单/差异、声明、模块去向和保护现有内容；本次 4 个子项通过。完整 [summary.json](evidence/PS-00-01/20260928T032245703563Z/summary.json) 关联命令、真实退出码及哈希；文档校验另列，不计产品测试。

PS-00-02 的来源依赖已提供；环境启动前提尚未就绪，须按授权准备独立 Python 3.11 环境和前端依赖，再重写安全启动链。CASE-001 的 PS-00-02 分项及 CASE-002 均 NOT_RUN；G0 仍 NOT_RUN。本次不自动进入下一任务。
