# 参考调研：本游戏适配、Nexus 与受限 AI 协助

2026-10-16；用于本轮方案分享、智能分析、在线 Mod 与更新实现。以下分别标明竞品自述、当前源码事实与官方资料，不将参考软件的验证记录当成本项目验收。

## 实施结论

1. Nexus 游戏域使用 `probablystolen`；数字游戏 ID 为 `9791`（官方游戏页所引用的游戏图像路径）。v1 请求路径使用域名，不用数字 ID 代替。
2. 当前可交付个人 Key 的测试接入。面向普通用户公开发行前，须按 Nexus 官方 AUP 提交测试构建并注册应用；正式 SSO 依赖注册后取得的 app slug。
3. 免费账号需要官网“Download with Manager”产生的 NXM 授权；Premium 才可不带该次网站下载授权获取链接。不要做共享 Key、匿名抓取或绕过免费账号授权。
4. AI 应采用“查询已采集环境 → 解释证据 → 提议最小 Mod 变更 → 玩家点击后执行”，复用现有普通批次与恢复路径。查询工具不直接安装、停用或写配置。
5. 不把 UI 拖动列表的先后冒充游戏实际加载顺序。MelonLoader 0.7.3 的注册排序来自 DLL 元数据和程序集关系，未发现读取本管理器方案顺序的官方接口。

## 一、本游戏深度适配应落到哪些事实

竞品文档根目录：`C:\Users\Administrator\Documents\xwechat_files\wxid_zf9klupmpt9u22_85a9\msg\file\2026-10\深空当铺工具-v1.12.0`。仅阅读 Markdown，没有运行 EXE、读取账号文件或反编译；以下是该软件的文档行为，不代表本轮重验了其实现。

| 能力 | 竞品文档依据 | 对本项目的实施价值 |
| --- | --- | --- |
| 游戏版本与加载器 | `使用说明.md:211-228,269`、`更新日志.md:49` | 正确区分 Demo / Playtest / 正式版、游戏 EXE/Steam Build 线索、MelonLoader 0.7.x、Unity IL2CPP；“作者提到兼容”与“本机实际加载/玩法测试”分开。 |
| 真正的 Mod 身份 | `使用说明.md:138-146` | 静态检查 `MelonInfoAttribute` 指定的、属于本 DLL 的非抽象 `MelonMod/MelonPlugin` 入口；不能因文件名、引用 MelonLoader 或邻近 manifest 就认定为 Mod。依赖库、加载器、未知文件与已识别 Mod 分开显示。 |
| 日志与 DLL 关联 | `使用说明.md:150-156,190-192` | 名称改动后仍可按程序集、自己声明的类型/方法定位候选；同名程序集/重复类型要保留多候选。实际注册需匹配完整路径；普通模块加载不等于 Mod 已注册。 |
| 依赖与异常 | `使用说明.md:154,178-182,250-258` | 识别重复程序集、缺失依赖引用、文件读取失败、非法偏好/TOML NUL、日志调用栈；直接引用者不是崩溃责任方，共享 Harmony 补丁目标不是已证实冲突。 |
| 包级启停 | `使用说明.md:180-209` | 已登记安装按整个包移动，包含资源及 UserLibs；未知手动 DLL 单文件操作应清楚列影响。安装脚本、游戏本体、原生补丁、配置/存档不自动执行/覆盖。 |
| 本地更新与方案 | `使用说明.md:72,78-82` | 方案记录版本和文件指纹；缺失/版本变化需要用户决定。更新来源用实际安装记录/人工绑定，不按 DLL 名字猜 Nexus 条目；新版存在不等于兼容，补丁/可选文件不能笼统当升级。 |
| 可复现排查 | `使用说明.md:64-66` | 保存原组合，以包和直接引用分组；先确认同一操作确实可复现，再做停用/复现/恢复对照。AI 不得以正常退出或最后一条日志自动认定根因已修复。 |
| 可选增强采集 | `使用说明.md:156,226-246` | 运行注册、场景、心跳、GC、只读 Harmony 映射与回调耗时需要游戏内采集插件；现有日志分析不能凭空宣称这些数据。托管回调耗时不是完整 CPU/GPU，也不能直接归责。 |

竞品 `验证记录.md:9,25,51` 自述真实 Nexus 账号下载/续传仍未验证；目录查询、下载权限、采集自检与真实游戏验证分别记录。本项目应保持同样分层，不能因竞品文档称支持就将本项目状态改为通过。

### 加载顺序的官方机制

以下机制固定核对 **MelonLoader 0.7.3**，与竞品记载的测试版本一致；不能直接套用 RimWorld 的 `ModsConfig.xml`、`packageId`、`loadBefore/loadAfter` 规则。

- 官方 [`MelonBase.cs` v0.7.3:58-72](https://github.com/LavaGang/MelonLoader/blob/v0.7.3/MelonLoader/Melons/MelonBase.cs#L58-L72)：`RegisterSorted` 调用 `SortMelons`；先调用 `DependencyGraph<T>.TopologicalSort`，随后按 `Priority` 升序排序。低数值优先。
- 官方 [`MelonAssembly.cs` v0.7.3:257-315](https://github.com/LavaGang/MelonLoader/blob/v0.7.3/MelonLoader/Melons/MelonAssembly.cs#L257-L315)：从 `MelonInfoAttribute` 取得入口和作者声明版本，并读取 `MelonPriorityAttribute`（缺失默认 0）、`MelonOptionalDependenciesAttribute`、`MelonIDAttribute`、游戏/加载器版本约束。作者版本字符串可能不符合 SemVer。
- 官方 [`MelonPriorityAttribute.cs` v0.7.3:4-12](https://github.com/LavaGang/MelonLoader/blob/v0.7.3/MelonLoader/Attributes/MelonPriorityAttribute.cs#L4-L12)：优先级是程序集属性，不是本机用户列表文件。

**决策：** 本轮可为 AI 提供“当前方案顺序”和“可观察的加载优先级/实际日志顺序”两个不同事实；能启停、切换明确版本、提出依赖建议，但不得承诺通过前端拖动解决运行时排序。修改第三方 DLL 或注入自定义加载器不属于本轮方案。

## 二、Nexus 当前官方约束与产品入口

### 官方已核对

- [本游戏官方 Nexus 页面](https://www.nexusmods.com/games/probablystolen)确认游戏域；该页链接的[游戏图片](https://images.nexusmods.com/images/games/v2/9791/tile.jpg)给出数字 ID `9791`。本轮没有用玩家 Key 验证 `games/probablystolen.json`；数字 ID 来自页面资源，API 正式返回仍以连接时的响应为准。
- [API Acceptable Use Policy](https://help.nexusmods.com/article/114-api-acceptable-use-policy)：个人 Key 用于测试/个人应用；公开应用测试结束、面向普通终端用户前需要联系 support 注册并提供测试构建、名称、说明和 Logo；SSO 可在取得 app slug 后启用。请求需真实 `Application-Name`、`Application-Version`，不得空白或冒充其他应用；用户 Key 不得存放到自有服务端，不得脱离用户主动操作调用。
- [官方 SDK `getDownloadURLs`](https://github.com/Nexus-Mods/node-nexus-api/blob/master/docs/classes/_nexus_.nexus.md#getdownloadurls)：非 Premium 必须传官网生成、来自 Download with Manager 的 `key`；参数同时支持 `expires`。API Key 与一次下载的 NXM key 是两种凭据。
- [官方 SDK 字段](https://github.com/Nexus-Mods/node-nexus-api/blob/master/src/types.ts)：账号可返回 `is_premium`；文件类型 1 主文件、2 补丁、3 可选、4 旧版、6 删除、7 归档。文件 `version` 不保证等于 Mod 总版本；`file_updates` 只有作者声明新旧文件关系时才存在。`requirements` 是可选字段，缺失不能推断无依赖。临时下载 URL 不适合长期保存。

### 竞品记录与建议实施

- `Nexus-接入说明.md:3,13,25`：竞品自身仍为个人 Key 测试接入、尚未完成公开应用注册；不是本项目可以复用的正式认证或资格。
- `Nexus-接入说明.md:9,19`、`使用说明.md:96-100`：竞品使用 v2 GraphQL `mods`，过滤 `gameDomainName=probablystolen`，按实际返回数量继续 `offset` 直到 `totalCount`；v1 最近/热门仅为可选榜单。本轮未独立验证该 GraphQL 查询的完整字段。只做 v1 榜单时应叫“最近发布/最近更新/热门”，不能标“全部 Mod”或“全站搜索”。
- `Nexus-接入说明.md:21-23`：竞品 NXM 校验域名、Mod/文件编号、账号编号和过期时间，注册协议处理器需玩家主动点击、不抢其他软件现有处理器。下载完成仍需安装预览；任务不保存 NXM 密钥。
- 推荐入口：**在线浏览 → 看作者说明与文件变体 → 免费账号到官网授权 / Premium 获取下载 → 下载完成进入已有安装预览 → 确认安装**。安装后的来源记录保留 game domain、Mod ID、file ID、作者版本、文件类型，后续更新不要靠标题猜测。
- Mod 整合包须区分“方案清单”和“包含文件”：后者不能默认拥有再分发许可。示例[作者页面](https://www.nexusmods.com/probablystolen/mods/454?tab=description)明确禁止转传文件至其他网站。分享页应提醒用户仅打包获授权的 Mod；有 Nexus 来源时保留可分享的来源编号，不导出账号或带授权参数 URL。

## 三、RimCrow 确实实现的 Agent 机制

只读源码：`E:\WorkSpace\GitHub\RimCrow`，当前提交 `a7e875112a175a914bb4706a82589d96d62adc70`；本轮静态阅读，没有运行 RimCrow 或其游戏操作。

### 查询、工具循环与提示词

- `E:\WorkSpace\GitHub\RimCrow\backend\ai\ai_tools.py:301-364`：7 个工具为 `get_log_context`、`search_mods`、`get_active_mod_list`、`get_mod_info`、`get_mod_rules`、`get_mod_user_data`、`get_group_mods`。它们是读取证据，不是任意执行 shell/写文件。
- 同文件 `:26-34,73-154`：Pydantic 拒绝额外字段；日志行号必须大于 0、堆栈最多 80 行；搜索最多 50；元数据字段白名单、scope 枚举；分组最多 100。`:414-449` 将 JSON 解析、工具名注册表及参数校验放在真正 handler 前。
- `E:\WorkSpace\GitHub\RimCrow\backend\ai\assistant_runtime.py:49,1660-1700,1786-1842,2170-2213`：最多 10 个工具轮；重复调用签名被拦截；取消前后检查；每次工具调用/结果有事件；达到上限禁用工具并让模型用现有证据总结。
- `E:\WorkSpace\GitHub\RimCrow\backend\ai\def_entries.py:84-184`：游戏日志、管理器日志和通用 Mod 助手各自有模板与工具/动作权限。首轮用压缩摘要，必要时才读指定 `target_line`，避免反复检索；区分事实、推断与待验；只有目标、方向和理由明确才产出动作。管理器日志助手禁止输出游戏 Mod 启停/排序动作。

### 可执行动作并不是任意 Agent 写权限

- `E:\WorkSpace\GitHub\RimCrow\backend\ai\def_actions.py:25-185`：`MOD_STATE(enable/disable)` 与 `MOD_RULE(loadAfter/loadBefore/incompatibleWith)` 使用固定 payload；不是自然语言命令。`:266-364` 做结构校验、允许类型过滤、去重，丢弃涉及互相冲突启停的动作。
- `E:\WorkSpace\GitHub\RimCrow\frontend\src\features\ai\AiAssistantPanel.vue:208,937-947`：动作卡通过 `@execute` 进入固定 executor；不是响应一返回就执行。
- `E:\WorkSpace\GitHub\RimCrow\frontend\src\features\ai\ai-store\runtime\aiActionRuntime.js:382-437`：启停调用 `modStore.changeModsActive`；规则先确认，再 `ruleStore.addUserModRule`；规则成功后另询问是否 `autoSortMods`。启停 executor 自身没有第二次确认框，依赖卡片点击入口。
- 同文件 `:439-481`：还实现文本复制与有限设置修改；设置受 `allowed_setting_keys` 白名单限制。这些 executor 存在不等于默认每个助手都获授权；实际权限以 `def_entries.py` 的 `action_types` 为准。

### 本项目应采用的具体适配

1. 系统提示明确 Probably Stolen、Unity IL2CPP、MelonLoader 和受管理的 Mods/Plugins/UserLibs；不要保留 RimWorld 的 XML Def、Workshop 包名、HugsLib 等前提。
2. 环境查询返回游戏版本线索、加载器版本、选择的方案、已安装包/版本、已保存启停状态及真实加载证据的来源/时间；缺失字段直接表示未知。
3. 日志摘要保留原始行号、首次异常、重复计数与关联程序集；证据工具可以定点扩展原文，不要让模型只看过短摘要后空泛猜测。
4. 修改动作绑定本项目真实 package/version ID 与当前快照，在执行前展示目标、变化和依赖影响；玩家点击后仍由原有普通批次处理器检查游戏运行状态、外部变更与恢复。
5. 可先完成“建议停用/启用 → 预览 → 确认 → 刷新环境 → 用新日志继续分析”。不能用“展示一个假动作按钮”代替实际消费链路，也不能将本地规则诊断标为 AI。

## 本轮调研限制

- 官方网页和公开源码核对为 2026-10-16；没有使用账号、真实下载授权或 AI 服务。本轮不证明实际账号登录、Premium/免费真实下载、真实模型分析质量或游戏运行效果。
- 竞品只读文档说明了可借鉴的产品行为；其非开源实现、插件、图像与资产未复制。
- RimCrow 为指定提交的静态证据；本报告不将其机制或历史测试等同于本项目已经实现。
