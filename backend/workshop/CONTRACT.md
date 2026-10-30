# 正式版 Workshop 服务接口

`from backend.workshop import WorkshopService, WorkshopError, MESSAGES, IMAGE_HOSTS`

```python
WorkshopService(paths, *, session=None, opener=None,
    process_status=None, write_guard=None, steam_factory=None)
```

`paths` 是现有 `AppPaths`；`session` 是可注入的 HTTPX 客户端。`installation` 是宿主已选择、校验的安装记录 dict，含绝对 `canonical_root`、可选 `app_id="4348910"`；不接受页面任意目录。工坊目录仅由该安装所在 `steamapps` 推导。手动原生安装仍能启动游戏，但不启用 Steam 工坊修改能力。

`process_status(installation)` 返回 `{state: "stopped"|"running"|"unknown", writable: bool}`。`write_guard(installation)` 返回同启动器、原生模组写入共用的安装 lease；其 `check()` 在持有期间重新核对游戏进程。缺少两者时直接操作不可用。宿主退出调用 `close()`；启动游戏前，在已持有该安装 lease 时调用 `release(installation)`。

## DesktopApi 接线

- `status(installation, *, workshop_id=None, refresh=False)`：立即读取本地记录和最近 Steam 快照，后台去重探测，不同步等待 SDK；返回 `{app_id, observed_at, local_available, items, downloads, issues, session, runtime, capabilities, operations}`。指定 ID 可后台查询未订阅条目。手动 refresh=true 绕过成功/失败的 30 秒 TTL，安装保护允许时立即排队探测；已有探测仍去重，不把页面调用变成阻塞探测。恢复检查尚未通过时不因 refresh 绕过保护。
- `action(installation, *, action, ids, source="manual", submission_allowed=None)`：`action=subscribe|unsubscribe|update`；1 至 100 个数字 Workshop ID。source 只能由宿主传 manual/automatic，页面 action 不接受。submission_allowed 是宿主本批次不可复活 Event.is_set，关闭自动更新立即阻止后续逐项 begin，但已经提交项继续回调核对；重新开启使用新 Event，不恢复旧批次。返回操作 DTO，初始通常 queued，前端轮询 status.operations，批量逐项报告。
- `capabilities(installation=None)`：无安装时仅公共浏览/官方交接可用；有安装时能力来自实际会话与进程保护。
- `browse(installation=None, *, query="", page=1, sort="hot", refresh=False, page_size=24)`：page_size 支持 12/24/48；内部兼容旧 30。官方页仍取 30 项，按绝对偏移拼接/裁剪，最多跨 3 个源页，缓存源页复用，无页间重复/漏项。sort=hot/recent/new/mine；mine 合并本机记录与最近实际订阅，公开 API 补标题、封面。返回 `{items, page, page_size, total, has_more, source, capabilities}` 及下述缓存字段。
- `query(workshop_ids, installation=None, *, refresh=False)`：1 至 30 个 ID，返回 `{items, source}` 及缓存字段；同一条目的列表、批量查询、单项详情共用持久元数据。
- `detail(workshop_id, installation=None, *, refresh=False)`：立即使用已有卡片/完整详情，完整文本和作者/依赖补充后台重验证；完全无缓存时仅等待必要的公开 API，不等待可选详情 HTML 或 SDK。新增 description_scope=summary/detail、source_pending。summary 不能作为完整翻译源；8k 截断另用 description_truncated 表示。未知依赖仍为 required_items=[] / children_kind=unknown。
- `cached_detail(workshop_id, installation=None)`：宿主 AI 缓存专用，纯本地，完整公开源存在则返回 dict，否则 None；不排队、不联网、不初始化 SDK。
- `translation_detail(workshop_id, installation=None, *, check=lambda:None)`：仅在已开始的 AI job 内等待完整单项公开源，每 0.1 秒调用 check；同一请求去重。保留 8k 截断标记，无完整源时明确失败。
- `image(workshop_id, *, refresh=False)`：只接受 ID，从已核验公开元数据取 Valve 图片 URL。返回 `{workshop_id, state:ready|loading|unavailable, data_url, stale, refreshing, source_updated_at, error}`；有缓存立即返回受控 PNG/JPEG/GIF/WebP data URL，无缓存后台下载；元数据尚未到达时也返回 loading，自行后台查官方 API，不依赖页面先 query。旧请求失败后如独立批查询提供了新 URL，可立即下载，不继承缺失元数据的退避。条目 preview_cached 是 bool；preview_url 保留在线后备。宿主该单独响应需允许 2 MiB，一般消息不扩大限制。
- `open(workshop_id=None, *, intent="detail", target="steam")`：保留官方交接备用入口。`intent=detail|subscribe|unsubscribe|workshop`，`target=steam|browser`。严格生成 Valve 页面/Steam URI；返回 `state="handoff_opened", confirmation_required=true, subscription_state="unknown"`，打开页面不等于完成账号动作。
- `anonymous_download(...)`：抛 `WORKSHOP_ANONYMOUS_UNAVAILABLE`。匿名下载能力未验证，不安装 SteamCMD、不制造登录、不以订阅冒充匿名下载。
- `release(installation=None)`：释放自有 helper。指定安装时先查实际活动下载及未确认动作；有活动或无法消解的 uncertain 则抛 `WORKSHOP_OPERATION_BUSY`，启动器不得继续。无 Steam 库且无相关 provider/active/uncertain 的手动安装直接返回。无参数只关闭已有 helper，不替代指定安装的启动检查。
- `is_busy()`：无 SDK 调用，检查后台动作/uncertain、状态 probe、公共元数据/封面任务，供宿主阻断迁移。
- `close()`：停止新任务，取消排队 HTTP，排空运行中的后台元数据/封面写入后再关闭 HTTP 客户端；返回后没有旧目录缓存晚写。Steam 已接受的任务可能继续执行，关闭管理器不表示取消下载。

### 会话与能力

```text
session: {state: ready|unavailable, reason_code, message, observed_at,
          stale, refreshing, age_seconds, probe_owned_busy, last_error?}
runtime: {state: stopped|running|unknown, writable: boolean,
          blockers, busy_owner?: workshop_probe|workshop_action, ...宿主字段}
capabilities:
  public_browse: true
  official_handoff: true
  account: {state: verified|unverified, uses: steam_client,
            password_required: false, message}
  direct_subscription: {available, reason_code, reason, blockers}
  direct_updates: {available, reason_code, reason, blockers}
  anonymous_download: {available: false, reason}
```

ready 表示最近观察到 Steam 在线、AppID 和许可匹配，不含账号 ID；available 还要求游戏确认为退出且安装可写。客户端未运行、离线、许可缺失、签名/接口不符、超时各有安全原因。Steam `needs_update=false` 是该时刻客户端状态，不是对远端绝对最新版本的保证。

连接失败保留上次 items/observed_at，但 session unavailable/stale 且 evidence.steam_stale=true，不把历史值当实时成功。动作结束和启动前 release 也只使观察过期，不清空这些历史字段；下一次允许的 status 立即后台核对，连续失败仍保留最近观察。首次还无观察时 observed_at/age_seconds 为 null。自有后台 probe 仅因持 lease 而拒写时保留 writable=false，原因是 WORKSHOP_SESSION_CONNECTING；不能用此标签盖住同时存在的恢复或目录问题。

安装保护原因在 session、capability、action 和异步 probe 异常中采用同一映射。runtime.blockers 与 capability.blockers 保留宿主原始代码列表；不返回异常文本、native_error 或内部路径。已确认游戏运行优先，其后区分以下原因，不能把 stopped+writable=false 一律解释成游戏运行或工坊操作未确认：

| 宿主 blocker | Workshop 安全 reason_code |
| --- | --- |
| GUARD_RECOVERY_REQUIRED | WORKSHOP_RECOVERY_REQUIRED |
| GUARD_INSTALLATION_CHANGED / GUARD_IDENTITY_CHANGED | WORKSHOP_INSTALLATION_CHANGED |
| GUARD_PATH_INVALID / GUARD_PATH_UNAVAILABLE / GUARD_REPARSE_POINT / GUARD_HARDLINK / GUARD_TARGET_NOT_ALLOWED | WORKSHOP_INSTALLATION_UNAVAILABLE |
| GUARD_PROCESS_RUNNING / GUARD_PROCESS_UNKNOWN | WORKSHOP_PROCESS_RUNNING / WORKSHOP_PROCESS_UNKNOWN |
| GUARD_LEASE_BUSY（其他操作） | WORKSHOP_INSTALLATION_BUSY |
| GUARD_LEASE_LOST | WORKSHOP_OPERATION_INTERRUPTED |
| 未识别的 guard 拒绝，或 stopped 拒写但没有原因 | WORKSHOP_GUARD_UNAVAILABLE |

WORKSHOP_OPERATION_BUSY 保留给真实活动/uncertain 工坊操作；改进提示不解除其重复提交、启动或迁移保护。恢复和 lease busy 并存时优先提示恢复；安装变化导致进程未能检查时优先提示安装变化。

### 本机与公开条目

本机字段：`workshop_id, native_mod_id, title, mod_version, installed, content_state, subscription_state, download_state, update_available, evidence`；会话字段：`subscribed, steam_installed, needs_update, downloading, download_pending, disabled_locally, bytes_downloaded, bytes_total, progress`。

- Workshop ID 是来源条目 ID；nativeModID 仅从 manifest 读取，不能互换。
- installed 表示包清单及声明文件存在；steam_installed 是 Steam 标志。原生可加载性由 NativeModService 检查。
- `subscription_state=subscribed|not_subscribed|unknown`。只有当前会话能确认账号订阅；ACF/目录不能确认。离线/游戏运行时不复用旧值伪装实时确认。
- download_state 区分 downloading、download_pending、update_available、installed、not_installed、unknown。progress 有可靠字节数且总量非零时为 0 至 1，否则 null。
- ACF 应用级 NeedsDownload/NeedsUpdate 仅放 steam_record，不推导逐项队列；manifest 与 latest_manifest 不同可显示 update_available，不能声称正在下载。
- 活动写入及已提交未确认的订阅/更新包不读取 manifest，避免把部分写入判损坏。实际下载/排队项进入 downloads。

公开字段：`workshop_id, app_id="4348910", native_mod_id, title, description, description_truncated, author, preview_url, page_url, file_size, time_updated, subscriptions, rating, tags, required_items, children_kind, local, metadata_source, available`。author 是公开显示名，不返回个人 SteamID；preview_url 仅允许 Valve CDN HTTPS。详情最多 8000 字符，卡片/离线摘要 240，保留截断标记，全文打开官方页面。前端按文本渲染，不执行 HTML/BBCode。

### 操作 DTO 与完成判据

```text
operation: {operation_id, action, source: manual|automatic, state, created_at, updated_at, items}
item: {workshop_id, state, error, bytes_downloaded, bytes_total, progress,
       callback_confirmed, subscription_confirmed, download_completed,
       steam_result?, completion_source?}
error: {code, message, retryable} | null
```

操作 state：queued、running、completed、partial、failed、uncertain。逐项 state：queued、subscribing、unsubscribing、download_pending、downloading、completed、already_current、failed、uncertain。

- SubscribeItem/UnsubscribeItem 的 API handle 仅表示提交；匹配 handle、回调类型、Workshop ID 且成功，再核对目标订阅状态，才设置 subscription_confirmed。订阅完成不等于下载完成，download_completed 仍为 false。
- `DownloadItem(..., false)` 返回 true 仅表示接受。匹配 AppID/条目的成功回调，且 installed、无 needs_update/downloading/pending，才设置 download_completed。不用 high priority，避免暂停其他 Steam 下载。
- 已满足目标为 already_current；已有下载只观察，不重复排队。取消订阅不声称本机目录立即删除。
- 相同活动批次返回同一 operation_id；其他并发批次拒绝。批量中途失败保留已确认项，已提交未确认项为 uncertain，未提交项为 failed。
- 超时/会话丢失不重放。后续新会话观察到目标状态可设置 `completion_source="steam_state_readback"`，但不补造 callback_confirmed。未消解 uncertain 阻断重复操作、游戏启动和迁移。
- 终态只在共享 lease 释放后公布，前端可立即刷新原生库存。记录属于当前管理器会话，最多保留 64 条已结束历史；未消解记录不按此上限清除。

## 执行边界与生命周期

生产 steam_factory 默认 SteamHelper，以 multiprocessing spawn 隔离。主程序入口必须先 `multiprocessing.freeze_support()`；冻结包复用自身入口，无附加 helper EXE 或依赖。

只加载已选游戏 `Probably Stolen_Data/Plugins/x86_64/steam_api64.dll`，拒绝重解析路径，在固定文件/目录句柄期间验证 Windows Authenticode 和签名组织 Valve Corp.，再加载 DLL。校验使用本机缓存证书信任，不在线拉取证书。DLL 从用户安装取得，不随管理器分发。接口为 UGC v021、User v023、Utils v010、Apps v008，不兼容明确报错。

SteamAppId/SteamGameId 只设置于 helper；Init 后核对在线、许可和 AppID 4348910。不读取密码、Cookie、令牌或 SteamID；不调用 RestartAppIfNecessary，不启动游戏或执行 Mod DLL。GetItemInstallInfo 会把内容标记为使用过，因此不调用。DLL 加载前关闭原生 stdout/stderr，防止 Steam 自身诊断输出账号标识。

当前 AppID 的 GetSubscribedItems（包含 locally disabled）提供成员资格；update/unsubscribe 不针对其他游戏订阅。新 subscribe 必须有新鲜、成功、AppID 4348910 的公开详情，不以缓存/目录猜测。每项 SDK 修改前分别 lease.check，游戏中途启动即停止后续提交；已被 Steam 接受的任务无法撤销。

被动会话观察按安装去重、缓存 30 秒；暖缓存轮询不重新连接。browse/query/detail/image/cached_detail 不发起 SDK 探测。只有 status/capabilities 请求后台观察；probe 完成即摘除并关闭 helper，然后释放 lease。SDK IPC 不持服务/UI 锁。游戏运行/未知/其他写入占用时不建 helper；状态探测持同一安装 lease，防止 launch release 后的重连竞态。动作仍持 lease 至回调处理结束，先摘除 provider、关闭，再公布终态。

helper 启动等待 20 秒，单次管道请求 10 秒，订阅回调 45 秒，下载 20 分钟。超时/丢失立即废弃连接，避免迟到响应错配下一请求，不重放 begin。私有管道只传有界 JSON。退出调用 SteamAPI_Shutdown；必要时只终止自有 helper，不终止 Steam/游戏。

2026-10-28 23:50 的只读探测证实：真实游戏进程不存在时，SteamAPI_Init 令 app-specific Running 从 0 变 1，Shutdown 一秒后仍为 1，稍后回到 0。未拍 Steam UI，不能精确量化其界面延迟。故不为卡片/详情初始化 SDK，不永久维持会话；不改注册表、不杀 Steam/游戏。

## 持久缓存、离线与自动更新来源

公开缓存位于 `paths.root/cache/workshop`，不保存账号会话、订阅 ID 列表、凭据或安装路径。metadata.json 最多 128 条记录/6 MiB，图片每个 1 MiB、最多 64 个，只接收已核验 Valve HTTPS 且无重定向的位图；SVG/HTML 拒绝。复用 locked_path 固定父目录/读文件，拒绝重解析和硬链接，独占临时文件加原子替换。损坏缓存视为 miss，不删除用户文件；不可写时元数据仍可内存使用。

browse/query/detail 的缓存字段为 `{cache_hit, stale, refreshing, source_updated_at, network_error}`。元数据 5 分钟新鲜期，过期/显式刷新先返回已有内容、后台重验证（SWR）；封面为 24 小时或 URL 变化重取。同请求合并，失败自动探测有 30 秒退避，显式 refresh 可重试。公开工作线程 3 个、封面独立 1 个；缓存/会话锁不跨网络请求。官方源首次未访问且离线仍不可凭空显示内容；访问过的元数据、封面冷启动离线可用。

本模块不会因读取状态、显示卡片或检测 needs_update 自动调用 DownloadItem；只有 action(update) 执行 SDK 修改。宿主可提供默认关闭、用户明确开启的“管理器自动更新模组”，以 source=automatic 调同一受保护动作，状态/回调不另走捷径。其开关只控制管理器提交；**Steam 客户端仍可能更新订阅内容**。

官方 SuspendDownloads 影响全部工坊下载，只持续到显式恢复或应用结束，不是持久/逐项禁止自动更新；本服务不调用它，不提供虚假永久锁定。用户需要固定版本时应使用另行导出的本地包及原生源管理方案，不能把关闭管理器策略说成关闭 Steam 自动更新。

## 网络与错误

WorkshopError 的 code/message/retryable、as_dict 仅返回安全字段；MESSAGES 是宿主错误白名单，IMAGE_HOSTS 是精确 Valve CDN 白名单，无原始响应、账户或敏感 URL。

SDK/IPC adapter 未知异常归为 WORKSHOP_SESSION_LOST，不冒充安装 lease/进程保护中断，不能以一个回调异常推断游戏已启动。已提交项仍为 uncertain，保留已确认的其他项，不自动重放。

公开 HTTP 使用既有 httpx、标准库 HTMLParser；ACF 复用 discovery.parse_vdf 的大小/深度/token/重复键限制。响应上限 4 MiB，连接/读取超时 8/25 秒，不跟随重定向。缓存 5 分钟；网络失败有缓存则显式 stale/network_error，无缓存时报错或返回本机条目。401/403 不换通道绕过，429/5xx/连接超时可显式重试。真实 30 项列表约 31–38 KiB，宿主不能采用仅 32 KiB 响应限制。

浏览使用官方页面 SSR，详情用无需 key 的 ISteamRemoteStorage/GetPublishedFileDetails/v1。页面结构变化报 WORKSHOP_PAGE_CHANGED，不伪装空列表。OpenID/OAuth 不等于 UGC 修改授权，不制造登录流程。

## 测试注入与证据

```python
from backend.workshop.steam_session import EngineProvider, SessionEngine
service = WorkshopService(paths, process_status=guard.status,
    write_guard=guard.lease,
    steam_factory=lambda installation: EngineProvider(SessionEngine(fake_sdk)))
```

fake_sdk 实现 ensure_online、subscription_ids、item(id)、begin(action,id)、callbacks、close，字段同 SteamNative。仅替换 SDK，实际 DesktopApi/service/lease/状态机/UI 仍运行；生产页面没有 mock 开关。

- 上一轮 `.venv/Scripts/python.exe -m pytest tests/workshop -q`：91 项通过，覆盖持久冷启动/离线封面、SWR/并发去重、摘要与完整翻译源、12/24/48 连续分页、慢 SDK 不阻塞详情、断线保留观察、自有 probe 与暖 TTL、关闭排空缓存写入、硬链接缓存保护，以及自动开关关闭时保留首项回执并停止余项；另覆盖图片先于元数据到达、独立批量元数据解除旧失败退避，以及回调异常、动作完成后断线、launch release 后保留历史观察。
- 本轮 `test_guard_mapping.py`、`test_cache_background.py`、`test_steam_session.py` 定向回归：65 项通过；覆盖恢复/目录/锁/进程原因、probe 前后 guard 变化、异常安全字段、保护优先级、恢复解除与离线失败后的手动刷新立即重试，以及真实 uncertain 继续阻断。仅 fake SDK，不初始化真实 Steam。
- 实机只读：2026-10-28 当地晚间，Valve 签名验证及 helper 初始化成功，实际订阅 7 项。Item Details、CyberElectronics、XIAOWOTradePerks、ShopAssist flags=13（Subscribed|Installed|NeedsUpdate）；uploader、RatKeeper、Multiplayer 为 5（Subscribed|Installed）。当时无逐项 downloading/pending，进度未知。
- 新一轮只读 snapshot（2026-10-29 06:50:09 UTC）前核对真实游戏进程为空，7 项均 flags=5、needs_update=false。Steam workshop_log.txt 记录本地 23:33:37 开始 Workshop 更新、23:34:11 No Error 完成；RatKeeper 又在 23:38:28 检测新版并更新，23:38:50 完成。四项待更新消失有实际 Steam 更新证据，不是管理器虚构完成；该日志不单独证明最初触发者。
- `tests/workshop/probe_steam_readonly.py --game-root <已验证根目录>` 是显式只读探测，不被 pytest 收集，仅 snapshot/Shutdown；本轮未调用订阅/取消/下载/SuspendDownloads，未改 Steam 设置或启动/停止用户游戏。
- 使用真实公开 12 项列表、Item Details 完整文字与约 0.8 MiB data URL 封面填充隔离缓存后，重新构造服务并强制网络失败，离线列表+详情+封面共 0.015 秒返回；缓存目录随隔离测试清理，不写正式用户数据。
- CyberElectronics 官方封面实读为 403124 字节 JPEG，未超过 1 MiB 限制；前端补上晚到 fallback 的监听，后台补全先到 image 请求的元数据依赖后，WebView `workshop-usability-webview-20261029-02` 实际采用 data:image。12/24/48 拼页逻辑有连续分页单测；本轮真实 48 项请求遇网络失败，不能据单测记为实网 48 项已通过。
- 已读取真实公开热门/第二页/搜索/新条目/更新时间排序、详情和封面。真实账号订阅、取消、下载未执行；此类证据为 fake SDK 驱动实际状态机/宿主/UI，不能记为实账号动作通过。

官方依据：[Steam 初始化](https://partner.steamgames.com/doc/api/steam_api)、[ISteamUGC 状态与回调](https://partner.steamgames.com/doc/api/ISteamUGC)、[公开条目详情](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage)、[工坊页面](https://steamcommunity.com/workshop/browse/?appid=4348910)、[WinVerifyTrust](https://learn.microsoft.com/en-us/windows/win32/api/wintrust/nf-wintrust-winverifytrust)。
