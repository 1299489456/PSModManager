# 用户动作、状态与接口约定

版本：2.0。PS-03-07 已接通普通 Mod 动作；PS-03-05/06 的可选配置和启动入口独立保留，数据库 schema 7 不变。

## 1. 用户需要看到的数据

| 数据 | 必要内容 |
|---|---|
| 游戏安装 | 选择的目录、是否可用、简短错误 |
| Mod 包 | 名称、版本、来源类型、包含文件 |
| Mod 状态 | 未安装/已安装、启用/停用、安装失败或未知 |
| 操作结果 | 做了什么、是否成功、失败原因、可重试入口 |
| 设置 | 游戏目录、私有数据位置、主题；AI 设置可选 |
| 方案、日志、备份 | 可选功能，不是安装必填材料 |

需要的身份和文件清单继续复用当前数据，不让用户填写 UUID、hash、代次、批准范围或恢复引用。

## 2. 普通动作目标

目标入口是安装、启用、停用、卸载。请求只表达已选择安装、Mod 与动作；文件路径由后端计算。明确的用户点击授权执行本次普通动作，不再要求先到独立批准页和原生批准弹窗。

`mod.list` 接收 `installation_id`、`offset`（默认 0）、`limit`（1–20，默认 20），返回真实后台任务；终态包含 `items/total/offset/next_offset`。每项含 `release_id/manifest_hash/display_name/version_raw/file_count/state/message`；state 为 not_installed/enabled/disabled/drifted/unknown。状态由提交记录和当前实际文件摘要计算，包库损坏与外改不会显示启用正常。

`mod.action` 接收 `installation_id/release_id/manifest_hash/action/idempotency_key`，action 只接受 install/enable/disable/uninstall。响应是可监听、可取消的后台任务；成功终态含 `completed: true`、实际 `state`、`action/release_id/no_op/idempotent`。业务失败终态含 `error_code/message`，文件错误可附 `io_error`；消费者检查这些字段并重新读取状态，不能仅以 Job 的 succeeded 显示完成。请求禁止路径、shell、Profile、批准标记等额外字段。

同一 key 只能表达同一动作。已经完成的文件动作重复提交返回现有结果和当前状态，不重新启用后来停用的 Mod。手工新一次点击使用新 key；PS-04-03 的同一建议卡采用固定 key，防止重复写入。内部签发 actor 为 direct-action 的固定操作记录，不调用原生确认；旧计划 actor native-user 的校验继续保留。AI 建议按钮已复用此普通入口。

## 3. 当前已实现接口

以下仍是实际实现，不因目标变更自动失效：

| 现有入口 | 当前行为 |
|---|---|
| `mod.list/action` | 默认 Mod 列表的实际状态与一次点击安装、启停、卸载 |
| `ai.settings.get/save`、`ai.credentials.clear` | 可选 OpenAI 兼容设置；密钥不回显、不写 config.json |
| `ai.test`、`ai.chat.send/get/clear/export` | 用户主动测试/流式会话及可选只读查询；没有写入工具或持久历史 |
| `ai.suggestion.resolve` | 用户点建议按钮时只读解析为普通 mod.action 请求；无文件操作或批准 |
| `installation.choose/discover/select/list` | 游戏选择与安装信息 |
| `package.choose/import_preview/import_commit` | DLL、目录、ZIP 导入 |
| `profile.*` | 草稿/保存与清单，保存本身不安装 |
| `plan.create/inspect/approve` | 旧计划预览与原生批准 |
| `plan.apply`、`assistant.apply_plan` | 共用现有 executor；非空计划目前仍需要有效批准 |
| `deployment.status/recover` | 当前事务状态与旧恢复入口 |
| `deployment.history/rollback.create/rollback` | 旧历史与回退入口 |
| `deployment.conflicts.recheck` | 旧恢复问题复查 |
| `app.theme.get/set` | 私有设置中的主题读写 |
| `config.read/save` | PS-03-05 选定的壁纸 Mod 字段读取与普通保存，不经过计划批准 |
| `game.launch/status` | PS-03-06 固定安装的直接启动与进程状态，普通按钮调用，无运行历史前置 |

上述旧批准、HMAC、代次、MAC journal、lease 和恢复状态是当前实现细节，保留在代码及 Git 基线中的[调整前契约](https://github.com/1299489456/PSModManager/blob/c4ee861fd5c999966aba367a2a51c9bc74b4ff73/docs/development/changes/20261012-scope-simplification/before/03-data-contracts.md)中。它们不再定义首版用户必须经历的流程。文档调整不允许调用者伪造批准或绕过当前后端；后续普通入口要统一修改消费者和服务接线。

游戏启动已通过 PS-03-06 的合成进程/实际桌面入口验证；真实游戏和 AI provider 仍以具体运行及授权记录为准。

## 4. 状态表达

- 保存方案与文件安装分开；界面明确当前实际状态。
- 复制/删除失败显示失败，不用数据库先标已安装。
- 安装成功指文件操作完成，不承诺 Mod 在游戏内兼容、加载或玩法正常。
- 日志/运行观测按需展示，不成为安装成功的前置。
- 取消仅报告实际终态；无法确认时显示“未完成，可重试”，不要求用户处理恢复审批台账。
- 最近备份存在时可恢复；缺失或不可读时直接说明，可选择重新安装。

## 5. 兼容与数据维护

当前 SQLite schema 7、已有实体、JSON `schema_version` 和持久记录保持不变。文档层面不直接删字段、签发记录或迁移数据。历史记录只用于排障和兼容，不作为用户日常授权平台。

新普通动作的公开字段和错误必须同步代码与消费者测试；未知信息保持未知，不把旧 PASS 复制到新流程。配置适配、Profile 导出和复杂历史按可选卡维护。

普通后台任务最多保留 128 项；达到容量时回收线程已退出且没有活跃订阅的最旧终态记录，继续接受新动作。运行中的任务与活跃订阅不回收，容量仍不足时返回原容量错误。已回收的临时 task_id 返回 TASK_NOT_FOUND，持久安装状态不受影响；订阅在取消订阅或到期前固定对应任务。

导入成功后释放所用的临时来源句柄。最多保留 8 个导入预览；满容量时只回收最旧已完成预览，未完成预览保持有效。保留期间重复提交沿用原结果；回收后旧 preview_id 返回 IMPORT_PREVIEW_EXPIRED，重新选择来源即可。请求字段、来源期限及安装数据格式保持不变。

## 6. 已选定配置入口（PS-03-05）

`config.read` 接收 `installation_id` 和固定 `adapter_id: "pswallpaper"`；后端定位 `UserData/PSWallpaper.cfg`，返回名称、相对位置、文件摘要 `revision`、`editable` 和已有支持字段。无法可靠解析的格式/编码返回只读说明，不重写或展示未知字段值。

`config.save` 额外接收 `expected_revision`、非空 `changes` 和可选 `backup`（默认 true）。只接受 WallpaperFit（cover/fit/stretch）、WallpaperBrightness（整数 0–100）、LogToFile（严格布尔）；缺失字段不补写。成功返回新读取数据及 `saved/changed/backup_created`。未变化不写文件、不生成备份。请求不能指定路径、批准标记、原始文件文本或其他配置名。

`CONFIG_CHANGED` 表示外部修改，重新读取后才能保存；`CONFIG_MISSING/CONFIG_UNSUPPORTED/CONFIG_VALUE_INVALID/CONFIG_IO` 分别说明缺文件、不支持、值错误和占用/权限/空间等文件错误。保存复用现有安装 operation/进程检查和真实文件句柄，不创建 ActionPlan、审批或混合部署事务。游戏是否已应用设置不由文件保存结果推断。

## 7. 可选启动入口（PS-03-06）

`game.launch`、`game.status` 只接收 `installation_id`，不接受 EXE 路径、cwd、args、shell、env 或跳过检查标记。启动从已登记安装取 `Probably Stolen.exe` 和安装根，复用当前 `DeploymentGuards.operation(..., 'launch')`；未完成的实际文件操作、安装变化、进程不明或已有进程等仍通过后端直接反馈，未新增审批步骤。

响应包含 `installation_id`、`state`（not_started/running/exited/failed/unknown）、`pid`、`exit_code`、`started_at` 和普通 `message`。后三者在无当前信息时为 null；同一管理器重复启动返回 `already_running: true`，不再创建进程。外部进程通过现有真实映像检查识别；管理器不结束它。状态用持有的进程句柄避免只按 PID/name 推测，真实退出后显示退出码。

创建或检查失败使用普通错误响应；`GAME_LAUNCH_FAILED/GAME_IMAGE_CHANGED/GAME_ALREADY_RUNNING/GAME_PROCESS_UNKNOWN` 对应无法启动、映像变化、已运行和无法确认进程/程序权限。失败不能显示游戏运行正常。管理器关闭仅关闭观察句柄，不结束已启动游戏；重开重新观察当前进程，不把旧日志或旧状态当成新运行证明。没有创建新的持久会话/运行验证记录。

## 8. 可选纯文本 AI（PS-04-01）

`ai.settings.get`、`ai.credentials.clear` 接收空 payload。`ai.settings.save` 只接受 provider（固定 openai_compatible）、base_url、model、可选 api_key 和 remember（默认 true）。URL 只接受 HTTPS 或本机 HTTP，拒绝账号密码、query、fragment；裸根补 /v1，不自动探测模型。配置仅保存前三字段；空/省略 key 保留当前目标密钥，清除通过独立普通按钮。响应包含 configured、key_status（missing/saved/session/unavailable）、message，保存成功另有 saved:true。保存失败明确提示；凭据保存失败可仅本次会话使用，不默写明文。密钥按数据根和端点隔离，修改地址不会自动把旧 key 发给新服务。

`ai.test` 仅用户点击时发一条固定短问题，返回真实任务且不增加聊天记录。`ai.chat.send` 接收 question（1–2000字符）、可选 selected_text（最多4000）、selected_label（最多120）和 session_id；PS-04-02 增加可选 installation_id、selected_evidence_id（UUID）。JSON输入最多32KiB，脱敏后的用户文本最多12KiB。默认只发送固定说明、当前问句/自选文本与有界历史；主动选择查询时才开放下述只读工具。不读取任意路径、DLL、存档或全部日志，不接受自定义 tools、附件或执行字段。

任务进度 data 为 session_id 和当前累计脱敏 text；只读查询使用 ai_tool 阶段和固定 tool_name，不回显任意参数。成功终态 completed:true/text/provider/model，业务失败 error_code/message 及已脱敏部分文本。Job succeeded 不代表业务成功，消费者检查 error_code。超时/认证/限流/坏流/断流等使用固定普通消息，不透传 provider 错误体、headers 或异常详情。请求可取消，不自动重试或跟随重定向；未选择查询时模型工具调用报未支持。

`ai.chat.get/clear/export` 只接受 session_id。get 返回当前内存消息和 idle/generating/failed/cancelled 状态；clear 取消并移除该会话；export 仅返回脱敏纯文本供用户选取，不自动保存文件。最多20个本次会话，每个保留最近最多8条/48KiB；重开不继承历史，更换服务/模型需要新会话。PS-04-04 的 RequestBudget 在发送时创建：整次60秒、累计16000字节文本/1024个流事件，含所有工具往返；最多4次 HTTP、3批/12次工具，每次消息最多60000字节。不自动网络重试，不统计费用。聊天与连接测试互斥，普通 Mod 动作不受 AI 忙碌状态限制。

排队/流中/查询阶段取消复用 task.cancel 和 JobManager 清理回调，关闭 HTTP 流且不继续后续调用，未完成的建议不公开。HTTP 404 使用固定 AI_MODEL_UNAVAILABLE 消息，不透传错误体；其他既有错误码保留。前端重试是普通 ai.chat.send/ai.test 的再次用户调用，不新增批准/预算接口；只在原输入/设置/范围仍适用时提供，取消后无重试按钮。

## 9. 可选只读 AI 查询（PS-04-02）

installation_id 必须等于当前用户选择且已登记的安装，每次工具执行重新校验。selected_evidence_id 必须属于该安装；缺安装、无效日志或跨安装对象在发送前报 AI_TOOL_SCOPE，不联网。省略安装时不提供工具。会话绑定两项选择，变更后报 AI_SESSION_SCOPE_CHANGED；前端主动取消/清会话并保留草稿。AI页日志列表通过 ai.logs.list（installation_id、after≥0、limit 1–20，后两项可省略）复用本地 LogService.evidence；只返回脱敏的 evidence_id/seq/text 和 total/next_cursor，既不上传日志，也不改变普通日志页 evidence.list/read。

| 工具 | 参数及来源 |
|---|---|
| get_installation_summary | 空对象；已登记版本、channel/build摘要，不发送绝对路径/二进制或宣称运行正常 |
| search_mods | query≤100字符、offset 0–2000、limit 1–20；包库页内名称/版本匹配及 next_offset |
| get_package | release_id UUID、manifest_hash 64位小写哈希及上述分页；复用校验过的包详情，只投影文件名/大小/角色 |
| read_selected_log | evidence_id UUID且等于用户所选；复用 evidence.read，前4096字节，有 truncated 标记；不提供继续读取或改选 |
| get_installed_mods | 上述分页；复用 mod.list 的实际文件状态；enabled 不等于玩法通过 |
| get_mod_config | adapter_id 固定 pswallpaper（可省略）；复用 config.read，只读三个已支持字段 |

工具字段封闭，不接受额外字段、重复 JSON 键、类型强制转换、任意路径或命令。未知工具/无效参数/对象缺失用 AI_TOOL_UNKNOWN/ARGUMENTS/SCOPE/UNAVAILABLE 普通错误，不透传异常；只读输出先脱敏，再封装 JSON，最多12KB/项。每项10秒、整次发送60秒，最多3批工具，每批4次，共12次；调用参数最多4096字节。循环消息最多60KB，超限结束；等待现有锁/哈希时复用取消检查，不启动脱离任务的后台操作。

只支持 Chat Completions SSE function tool_calls（按 index 合并 name/arguments，finish_reason=tool_calls后调用），不支持旧 function_call、自由脚本或其他执行格式。工具往返只在当前发送内存中，公开聊天消息/导出只有用户/assistant文本，不保存原始工具交换；get 额外返回的规范化建议见第10节。模型不能通过日志、备注或自报“已批准”进入 mod.action、config.save、plan.apply、game.launch 或任何隐藏入口。

## 10. AI 建议与普通动作（PS-04-03）

在上述选定范围内增加 suggest_mod_action，只接收 release_id（UUID）、manifest_hash、action（install/enable/disable）和 reason（1–300字符），字段封闭且复用重复 JSON/类型检查。通过真实包校验后生成建议而非执行；不接任意路径、命令、安装位置、批准字段或卸载动作。沿用原工具轮次/时限，最多4张并按版本/动作去重；名称/版本投影各最多200字符。

成功 ai.chat.send 的终态和 ai.chat.get 增加 suggestions（默认空数组），每项只包含 suggestion_id、installation_id、release_id、manifest_hash、display_name、version_raw、action、reason。建议临时留在内存中，不保存聊天历史；只有成功最终回复公开。生成失败/取消、新发送、清会话都会使旧按钮失效。公开字段创建时即脱敏，清除凭据后不重新暴露过去的密钥；显示使用纯文本。

ai.suggestion.resolve 只接受 session_id、suggestion_id。后端核对会话空闲、同一连接设置、当前安装、10分钟有效期和登记清单；只读元数据，不写文件、不联网。返回 {request:{installation_id,release_id,manifest_hash,action,idempotency_key},offset}，offset 用于已有 mod.list 定位，真实包字节/目标/进程仍由普通 mod.action 检查。相同卡片返回相同请求与 key；当前字段/对象不存在、过期或改选返回普通 AI_SUGGESTION_STALE/AI_TOOL_SCOPE/AI_SESSION_* 错误。

前端 aiActionRuntime 复用 modManagerRuntime 的动作、分页/状态核对、错误和取消；卡片点击是唯一写入触发，不调用 plan/profile/旧 assistant 批准接口。完成后必须刷新并确认对应状态，才显示安装/启用/停用完成。已完成卡片禁用；重放原 key 沿用普通服务幂等，不重启用后来停用的 Mod。文件外改、未知占位、损坏包按普通错误保留文件；没有新游戏日志不声称玩法修复。
