# 模组手动检查 v2

`ModSecurityService(paths, native_mods, *, worker=None, antivirus=None)`。默认 worker 为既有 `MetadataWorker.inspect_security`；测试可注入惰性解析器，不是页面参数。安装为宿主已授权 dict/Pydantic。所有 ID 是 native manifest ID。错误为 `SecurityError(RuntimePolicyError)`，导出中文 `MESSAGES`。

## 普通操作与检查分离

启用、保存和启动不触发静态分析、游戏原生扫描、Defender 或 AI，不等待手动扫描完成。未检查、旧报告、不完整或高风险报告均不自动阻止普通操作。用户主动隔离和未完成处置仍有效；实际游戏运行、未知进程、原生配置冲突和恢复保护继续保留。

`NativeModService.apply` 只计算游戏协议需要的清单与声明 DLL 指纹，写入选择、顺序及兼容批准。批准表示用户同意启用，不表示检查通过：`detected=null`、`approval_basis='user_enable'`、`scan_status='not_scanned'`。本服务不写批准，也不存在 `prepare_enable`。参见 `../native_mods/CONTRACT.md`。

## 宿主接口与建议命令

- `status(installation)` / `security.status`：`{installation_id, state, scanning, operation, items, blocking_reasons, records, error}`。只读库存/stat提示，不自动发起分析、Defender 或网络。items 每原生 ID 一项：`{id,name,source,workshop_id,content_hash,state,verdict,blocking,blocking_reasons,review_reasons,scanned_at,report_id,disposition}`。坏报告只使对应条目 unavailable，不影响其他报告；顶层 error 明确读取失败。
- `activity(installation)` / `security.activity`：只返回内存深拷贝 `{installation_id,scanning,operation}`，不枚举库存、不读报告，供进度轮询。
- `detail(installation,id)` / `security.detail`：`{item,report,records}`；report 为完整已完成或未完成报告，未知为 null。含 `files:[{path,size,sha256,kind}]`、`stats:{total_bytes,file_count,by_kind,largest_files,embedded_resource_bytes}`、`diff:{baseline_report_id,added,removed,modified,unchanged_count,byte_delta}`、`findings:[{id,rule,severity,title,evidence:[{path,method,offset,detail}],basis}]`、`coverage:{status,files_total,files_hashed,pe_total,pe_scanned,gaps}`、`antivirus:{state,engine,...}`、`blocking_reasons`。旧报告继续可读，但 state=stale 时不能授权当前内容。
- `scan(installation,ids=None,antivirus=False)` / `security.scan`：接受手动后台检查，立即返回 status；`operation={id,state:queued|running|completed|failed,done,total,error,ids,current_id,current_name,current_file,phase}`，重叠扫描拒绝。ids 省略检查全部有效来源，指定则只查选中项；Defender 仅显式 antivirus=true 时调用。轮询 activity，结束时再读取 status/detail。
- `check_launch(installation,check=callable,enabled_ids=None)`：保存传入目标列表，启动使用已保存列表。只检查持久处置记录；没有有效隔离或未完成处置时，不枚举模组、不读取报告、不做内容哈希。返回 `{checked_ids:[],content_hashes:{}}`，不冒充完成扫描。扫描不阻止普通保存，正在进行的处置仍互斥。无法控制外部 Steam/EXE 启动。
- `dispose_preview(installation,id,mode='disable'|'quarantine')` / `security.dispose.preview`：`{preview_id,id,mode,source,content_hash,affected_ids,expected_revision,effect}`，token 由宿主生成、短期内存保存。
- `dispose(installation,preview_id)` / `security.dispose`：只消费宿主预览token，内容/原生revision漂移拒绝。复用 native.disable_preview/security_disable 停用依赖闭包并撤信任（包括已停用包的残留批准）；local quarantine 额外 local_remove 移到原生恢复点；workshop quarantine 只做持久逻辑阻断，不移动/删包/改ACF/订阅。返回 `{record,status}`。
- `restore(installation,record_id)` / `security.restore`：恢复本地移出包时复用 native.recovery_restore；解除逻辑隔离。绝不恢复批准或自动启用。返回 `{record,status}`。随后可显式启用，不强制重新检查。
- `close()`：停止接纳工作并等待已接纳扫描退出，返回后不再写管理器缓存。`is_busy()` 供迁移前检查。

## 状态与边界

state：unscanned / scanning / reviewed / attention / high_risk / incomplete / stale / unavailable / blocked；verdict：no_high_risk_findings / review_required / high_risk / incomplete。reviewed 仅表示本次规则未发现高风险。扫描失败、超限、LPAC不可用为 incomplete。blocking 和 blocking_reasons 仅指用户隔离或未完成处置；检查告警放在 review_reasons。历史报告内的 blocking_reasons 不是当前启动策略。

quarantine 是 ID+来源级限制，跨内容更新仍有效；成功 disable 记录不是永久隔离。报告按安装/来源/全包内容/规则版本绑定。损坏报告不阻止普通使用，可手动重查：保留坏文件并生成新报告，diff.baseline_error 说明没有可用比较基线。损坏处置索引必须明确修复，不能静默清空，以免抹掉用户隔离决定。

持久目录：`paths.root/mod-security/<installation_id>/`，报告和处置记录存储在该安装目录；`mod-security-analysis/` 按文件内容+规则版本+协议/分析器版本缓存惰性文件解析。两者均为用户私有数据，不能打包分发。手动检查完整指纹包括全部相对路径及文件内容，mtime仅刷新提示。所有游戏/包写仍由 NativeModService 负责，扫描只读源。scan 不执行Mod，不自动调用AI或外部信誉查询；Defender自身云策略必须显式展示。

## 可选 AI 解读

使用现有日志分析功能指定的 Provider，未配置时静态检查仍可用。`security.ai.preview({installation_id,id,report_id})` 只在本地构造白名单、脱敏、有界的报告摘要，返回短期 preview_id、实际完整 messages、Provider/模型/地址、字节数及截断提示。原始 DLL、完整反编译源码及任意扩展字段不发送。

用户明确确认后，`security.ai.send({preview_id})` 复核报告和 Provider 未改变，发送与预览完全相同的消息；改变则要求重新预览，同一令牌重复确认不会重复请求。复用 AI 任务、流式输出、取消、错误及导出；该分支没有工具或处置卡片，不写批准、不启停模组、不自动隔离。名称、字符串和描述均为不可信报告材料。旧/不完整/高风险报告可解读，仍保留适用范围。

## 稳定补充字段及恢复语义

- item 另有 `source_id`、`package_revision`、`target_enabled`；来源切换不能复用原来源报告。
- `stats.by_kind={pe:{bytes,count},media:{bytes,count},...}`。`largest_files` 与 files 同结构。另有 `method_il_bytes`、`metadata_bytes`、`resource_count`、`duplicate_files:[{sha256,paths}]`；IL/元数据/资源量来自惰性 PEReader，不把整个 DLL 当作代码。
- `diff.added/removed` 与 files 同结构；`modified=[{path,before_size,after_size,before_sha256,after_sha256,kind}]`。`assemblies=[{path,status,error,analyzer,coverage,sizes,counts,category_counts,resource_kind_counts}]` 保留有界静态分析汇总，`coverage.limitations` 明确分析范围。
- `antivirus={state:not_scanned|unavailable|clean|detected|incomplete,engine,cloud_policy:'system_managed_unknown',remediation:false,checked_at,reason,detections:[]}`；调用后可附 `return_code`。仅手动调用固定系统位置、Microsoft Authenticode 通过并持有禁止写/删除句柄的 MpCmdRun；参数固定 custom scan + DisableRemediation，不提权、不更新定义、不设置排除。不识别的输出（包括 exit 0 空输出）是 incomplete；exit 2 不能单独判有毒。本产品不上传样本，系统 Defender 云策略未读取，可能按系统策略联网；`clean` 仅表示该次引擎未报告检测，不是静态报告的替代物。
- `records=[{id,mod_id,name,source,workshop_id,content_hash,mode,status:prepared|applied|failed|restored,created_at,restored_at,restorable,recovery_ids,package_recovery_id,effect,error,affected_ids}]`。`effect` 为 disable_only / local_package_move / logical_quarantine_files_remain。本地移出使用 native `operation_id` 与安全记录精确关联；崩溃后不按时间猜恢复点，不恢复 state/trust 备份。
- scan 服务 ids 上限 1024，公共 API 上限 1000；无单独取消命令，activity 轮询。关闭面板不停止后台检查，close 通知解析器退出且等待工作结束。扫描意图先持久化；中断、报告写失败留下 pending，重开后显示不完整而非沿用旧结论，普通操作不受影响。重新成功提交该来源报告才清除 pending。phase 为 queued / reading / analyzing / antivirus / verifying / saving_report / completed，完成时清空 current 字段。
- 高风险静态规则要求同一 DLL 的同一个 `method_token` 中同时存在敏感凭据路径字符串、文件读取 IL 调用、网络发送 IL 调用；读取/发送的 `declaring_scope` 还须是框架名称+公钥 token 匹配的外部 AssemblyRef。方法名不唯一，重载不会混合；本地伪造同名类型、未知来源、错误/缺失 token 不参与高风险组合。框架身份仍是元数据声明，不是已验签/已加载实现。结果称需复核的高风险组合，不声称存在实际数据流或已经执行。单独网络/反射/PInvoke/大小仅能力提示。
- 解析器 calls/strings 风险证据被截断导致规则覆盖 incomplete；普通图片资源展示截断不导致 incomplete，资源总量仍完整。原生代码、脚本、嵌套归档、嵌入 PE/归档尚不反汇编/解包，缺口显式保留。
- 报告与缓存带内容完整性摘要以发现损坏；不是抵抗同一用户权限下主动改写整个管理器数据目录的认证机制。
- 安全规则版本为 `local-security-v2`，静态 worker wire protocol 2 / security schema 2 / analyzer `psmm-static-security-v2`。旧 schema/cache 不作新报告依据。
- 历史原生检查独立使用 worker protocol 3。旧 `report.native_scan={status,scanner_identity,files,capabilities,hard_flags,findings,refused,methods_scanned,error}` 及 `undeclared` 仍可展示，native_blocking_reasons 仅为历史证据，不再参与普通保存或启动。游戏原生扫描器不在新的保存或手动静态检查链路中，工具保留供协议核对。
- 当前有效来源还必须满足游戏前置：声明 DLL 存在，至少一个声明 DLL 的原始内容含 `IMod\0`，通过完整读取流验证并覆盖跨块边界。此标记不是实现正确性的保证。若无标记，游戏可能跳过该 local 而加载工坊包，管理器不猜 fallback，报告 `source_resolution_unproven` 并要求先明确来源。当前为 workshop 且同 ID 多 workshop 包（或多个 local）时 `source_ambiguous`，不猜遍历顺序；有效单 local 优先不受被遮蔽工坊重复影响。
- 恢复过程先 `security_disable(operation_id=record.id,recovery=True)`，只许可精确属于本操作的 pending state/local_remove/派生 import；其他未完成操作仍拒绝。`recovery_restore(security_operation_id=record.id,verify_package=...)` 的源备份和发布前 stage 都须全包内容 hash 与安全记录一致，因此坏 DLL/未声明 PE 可原样恢复为禁用，而非绕过校验。正常恢复的 state 备份绝不用于重新启用；崩溃后已发布 target 也须内容一致并归档自身 pending 标记。
