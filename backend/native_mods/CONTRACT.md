# 原生模组服务 v3（用户启用批准与手动检查分离）

入口 `from backend.native_mods import NativeModService, NativeModError`。
`NativeModService(paths, write_guard=callable, process_status=callable)`：paths 使用既有 AppPaths（管理器私有文件写 paths.root/native-mods）。
installation 使用既有安装 dict：`installation_id, canonical_root`；宿主可额外提供 `workshop_root, persistent_root`。
不提供时分别推导 Steam library 的 workshop/content/4348910 和当前用户 LocalLow/Questing Goose Studio/Probably Stolen。
**process_status(installation) 返回 `{state: stopped|running|unknown, writable?:bool, ...}`；仅 stopped 且宿主 writable 不为 false 显示可写。默认 unknown，异常视为 unknown。write_guard(installation) 是宿主提供的 contextmanager，持安装互斥 lease 并检查进程，yield 对象须有 `check()`；事务全程持 lease，原子替换前再次 check。未提供 write_guard 时写入拒绝。**

所有返回值为 JSON 可序列化 dict；失败抛 `NativeModError`，继承 RuntimePolicyError，带 `code` 和 `detail`。IDs 一律真实 manifest ID，保持大小写。绝不把原型别名当 ID。

## 方法

- `snapshot(installation)` → `{installation_id, revision, ready, enabled_ids, mods, issues, runtime, paths, log, runtime_reconciliation, rules}`。
  revision 覆盖状态原字节和包文件 stat 版本用于并发检查。没有对应 gameVersion/scanner 证据不会伪造扫描通过。
  mods 是每个 native ID 一行，含 `id,name,author,version,description,game_versions,prerequisites,capabilities,source,workshop_id,folder_path,assemblies,files,size_bytes,updated_at,preview_path,sources,installed,target_enabled,approval,run,issues`。
  `sources` 保留同 ID Local/Workshop 副本（source_id、source、workshop_id、folder_path、active）。active 按游戏发现规则 Local 优先；重复本地来源明确报问题。
  `approval={state: unknown|approved|changed|updated_unverified|developer_mode, declared, detected, approved_at, updated_at, scan_status, current_scan_status, approval_source, approval_basis}`；detected 可为 null，表示没有检测证据，不等于“未检出权限”。scan_status 为 historical_record/not_scanned/unknown，current_scan_status 为 not_scanned，快照不执行扫描。approval_source 为 manager/game_or_external/unknown；新的用户保存批准 approval_basis=user_enable，scan_status=not_scanned。历史扫描记录仍单独标历史，不把批准当安全结论。
  `run={state: unknown|running|not_running|failed|partial, runtime_state, audit_line, audit_lines, events, observed_entry_count, log_path, ...}` 是最近 Player.log 启动证据，不代表实时进程；实时进程在 runtime。无日志或没记录保持 unknown；coverage 仅限 reported_entries，当前未扫描 IMod 入口总数，不能断言未记录的入口运行。
- `detail(installation, id)` → `{revision, mod}`，mod 补充完整 files、反向依赖等。
- `validate(installation, enabled_ids, rules=None)` → `{enabled_ids, sorted_ids, issues, blocking, approval_required_ids}`；None 使用已保存规则，显式 [] 才忽略规则。
- `enable_preview(installation, enabled_ids, id)` → 同 validate，自动加入已安装依赖（缺失不伪造安装）。
- `disable_preview(installation, enabled_ids, id)` → `{affected_ids, enabled_ids}`（返回同时停用反向依赖后的草稿，前端自行确认）。
- `save(installation, expected_revision, enabled_ids)` → 新 snapshot + `{recovery_id, saved: true}`。校验依赖/环，保留手动合法顺序；只改 modOrder，停用项删对应 modTrust.records；不创建批准。预检有阻断就拒绝；外部漂移/运行中/未知拒写。
- `apply(installation, expected_revision, enabled_ids)` → snapshot + `{recovery_id,saved:true}`。用户显式“保存”使用此方法，宿主 `native.save` 映射到 apply；上面的 save 仅保留内部顺序/恢复语义。所有目标启用包都写入当前游戏内容身份的用户批准，包括顺序未改但批准缺失或失效。目标为空只停用/撤批准。
  - **不接受 checker，不调用安全服务、LPAC/IL 解析器、原生 ModScanner、防病毒或 AI，不要求安全报告。** 异常/安全检查是独立手动功能，报告风险不参与普通保存；用户明确选择的隔离状态由宿主单独执行。
  - 事务持写 lease，只读取游戏指纹必需的 manifest 和清单声明 DLL 字节；在原子替换前重新计算这些字节，检测漂移，保留即时 lease.check、状态 revision 检查、备份和写后读回。普通资源仅作库存 stat，不读其内容或生成全包安全指纹。只读 DLL 流中的进程检查按最多 0.5 秒节流，真实写入边界从不节流。
  - 按游戏规范写 Local/Workshop、十进制 workshopId、manifestHash/files/combinedHash、规范顺序 declared、UTC 时间戳；未检测的 detected 写 **null**，gameVersion 无证据写空字符串，developerMode=false。不把用户批准说成扫描通过，也不启用开发者模式绕过未来更新处理。
  - 保留未知 ES3 wrapper/store/record 字段；同 ID 重复记录合并为一条并保留游戏所见第一条的扩展字段。扩展 `psmmApproval={source:'manager',basis:'user_enable',scanStatus:'not_scanned'}` 诚实说明批准依据；游戏忽略未知字段。
  - 内部 revision 校验不重复查询运行状态、解析 Player.log 或计算批准展示指纹；写 lease 是事务守卫。返回用户的 snapshot 才读取一次日志，并复用此次刚核实的原生指纹，避免第三次读取 DLL。
- `security_disable(installation, expected_revision, id)` → snapshot + `{recovery_id, saved:true, affected_ids}`。仅供安全服务：停用目标及已启用反向依赖，撤销这些项的批准，包含目标已停用时残留的批准。复用同一 lease、恢复点和原子写；不因其他模组的无效依赖而阻止减少启用项。
- 安全服务可用 `security_disable(..., operation_id=<32hex>, recovery=True)` 在本操作的待恢复记录下撤意图/批准；其他操作的待恢复记录仍拒绝。合法但暂时缺失的目标 ID 同样可清除，避免还原包时复活外部写回的启用状态。
- `config_read(installation, id)` → `{id,revision,exists,path,entries:[{key,value,type:'unknown'}]}`。
- `config_save(installation, id, expected_revision, entries)` → config_read + recovery_id。entries 可给完整列表或改动列表；按 key 更新字符串，保留原文件/entry 未知字段和未提交的键，不猜类型、不删除遗漏键。
- `import_preview(installation, source_path)` → `{preview_id, revision, package, issues, blocking}`。source_path 只能由宿主原生文件选择器授权；目录或 ZIP。服务私有 token，commit 不接收任意目标路径。
- `import_commit(installation, preview_id, expected_revision)` → snapshot + recovery_id。安装到 Mods/<native ID>，完整目录保留；已存在同 ID 本地包拒绝覆盖。ZIP 绝不 extractall，拒绝路径逃逸/链接/大小写冲突/保留设备名/过量展开。
- `recovery_list(installation)` → `{items:[{id,kind,created_at,label,status,restorable}]}`；`recovery_restore(installation,id,expected_revision)` → snapshot + recovery_id。每次修改创建可恢复副本；状态恢复只还原目标顺序、保留当前其他设置并撤销本次停用项批准，不恢复历史批准。
- `profile_list(installation)` → `{items:[{id,name,enabled_ids,created_at,updated_at,...}]}`。
- `profile_create(installation,name,enabled_ids,rules=None,sources=None)` / `profile_update(installation,id,name,enabled_ids,rules=None,sources=None)` / `profile_delete(installation,id)`：仅管理器私有文件，可在游戏运行时编辑；有恢复点。旧调用不变：create 省略参数取当前规则和来源；update 省略 rules 保留原方案规则，省略 sources 保留仍被选中 ID 的原来源，新 ID 才从库存补充。显式 [] 可清空。
- 导入可通过 create 的显式 rules、sources 保存接收端尚未安装的 ID。rules 验证后仅保留 before/after；sources 验证后仅保留 id/source/workshop_id/version，拒绝重复/非选中 ID 和非法工坊 ID，不携带路径、配置或批准。source 为 local/workshop/null，workshop_id 为正 uint64 的十进制字符串或 null，version 为字符串或 null。导入不安装包、不覆盖全局规则、不写游戏。
- `profile_preview(installation,id)` → validate + `{profile, missing_ids, source_changes}`，只产生草稿、不写游戏。source_changes 保持 ID 数组格式，涵盖来源、workshop_id 或已保存 version 与当前库存不同的项。
- `migration_preview(installation, legacy_profile)` → `{enabled_ids, skipped, issues, preview_only:true}`；只映射明确真实 native ID，旧 DLL/名称不猜测，不调用旧执行器。
  支持旧 get/export 的 selections、share 的 profile.selections、manifest 包装及草稿 data.current.selections；也兼容显式 enabled_ids/mods。release_id 始终是旧包引用，不视为原生 ID。每个未迁移项返回 `{index,native_id,display_name,release_id,reason,reason_text}`，selected=false 或 enabled=false 明确标记 disabled_in_legacy_profile；未识别/歧义结构报格式错，不伪装空方案。
- `recovery_create(installation,id)` → `{recovery_id,items,coverage,excluded}`：快照指定 ID 的 ModConfigs 文件与 ModData 目录，排除非标准 UserData、存档和批准。
- `local_remove(installation,id,expected_revision)`：本地包备份到恢复点后移出 Mods，停用并撤销该 ID 批准；若存在仍启用的反向依赖，要求先停用而非暗中连带移除。
- 安全隔离可传宿主内部 `local_remove(..., operation_id=<32hex>)`；原生恢复点保存并通过 recovery_list 返回 `security_operation_id`。安全服务凭精确操作 ID 对接中断恢复，不按时间或名称猜测恢复点。
- 安全服务专用 `recovery_restore(..., security_operation_id=<32hex>, verify_package=callable)` 只接受匹配的 local_remove 恢复点；回调验证备份及发布前暂存包的全内容身份，允许原样恢复有缺陷的风险包。派生还原记录使用 `security_restore_of` 追踪，重试可消费已发布但未记账的中断，绝不覆盖不同内容。页面 API 不开放这些内部许可参数。
- `source_switch(installation,id,expected_revision,source='workshop')`：要求现有同 ID 工坊副本，移走本地遮蔽副本、保留目标启用顺序、撤销该 ID 批准；下一次显式保存批准此来源的当前游戏指纹。两者返回 snapshot + recovery_id。
- profile_create/update 返回 `{profile,items,recovery_id}`；delete 返回 `{items,recovery_id}`。profile 项为 `{id,name,enabled_ids,sources:[{id,source,workshop_id,version}],rules,created_at,updated_at}`。
- snapshot 同时提供 `runtime_reconciliation`，即 diagnostics.native_log.read_runtime_summary 原值。mods.run 同时保留 runtime_state 与 state 同义，不能把日志运行状态当实时进程。
- `rules_read(installation)` → `{rules,revision}`；`rules_save(installation,expected_revision,rules)` → 同前 + recovery_id。规则为 `{before:<ID>,after:<ID>}` 列表，存于管理器目录，不改游戏，可在游戏运行时编辑；快照、验证、保存和方案均使用该列表。

## 详情字段

- `assemblies` 为清单声明相对路径字符串数组；`files` 为 `{path,size,mtime_ns,ctime_ns,identity}` 数组。
- `metadata_status` 为 not_scanned 或 managed_header_checked；后者仅表示导入检查了 PE/CLI 头，**不是 IL 权限扫描或 IMod 入口检查**。`entry_count:null`、`patch_intersections:null`、`patch_coverage:not_scanned` 保持缺证状态。
- `data_locations:[{kind:native_config|native_data,path,exists}]` 只列标准 ModConfigs/<ID>.json 与 ModData/<ID>。
- `preview_path` 是受检包内 preview.png 路径，宿主转换安全 data URI；不返回 HTML 或自动执行内容。

## 关键边界

- 写前和落盘前检查 lease，revision 不匹配为 `NATIVE_STATE_CHANGED`；所有游戏写操作先创建恢复点。
- 已知 demo app_id 4349200、缺少 Managed/Assembly-CSharp.dll 或 MonoBleedingEdge 的安装返回 NATIVE_RUNTIME_UNSUPPORTED。snapshot 不读取默认正式版配置/日志；config_read 和 import_preview 同样拒绝混用。手动登记但具有 Mono 文件证据的安装可用。
- 未完成的游戏写恢复点会阻断继续写入并给出 NATIVE_RECOVERY_PENDING；只能显式恢复后继续。恢复游戏数据也先备份当前状态。恢复本地包不恢复历史启用和批准，不写回 Steam 目录。
- preparing 表示备份准备未完成、尚未修改游戏，restorable=false，不阻断后续操作。prepared/failed 表示可能已开始游戏变更，必须显式处理；失败事务若当前字节等于备份，可清除待恢复状态而不重复写入。
- 未识别的 ES3 编码/typedJSON 结构只读报错，不能转换猜写。未知字段保留；typedJSON 的 modTrust.value 是嵌套 JSON 字符串。
- 工坊由 Steam 管理；服务只读工坊，不写 ACF、不订阅、不取消订阅、不执行 DLL。
- Player.log 全文不返回，返回原生加载相关的有限证据行。完整日志/AI 由宿主日志模块提供。
- 依赖排序是保存顺序，不保证当前游戏的 Init/Harmony/Hook 初始化顺序。
- apply 不改游戏 DLL；官方信任列表的 block/pause 在游戏启动时仍独立执行，之后的程序集兼容性/入口/Init 失败不能由“批准”保证成功。游戏遇到 Match 不会再次执行 ModScanner，不能声称它会自动补做本次省略的安全检查。停用、安全隔离及所有恢复路径不调用 apply，不恢复批准。

## 0.3.1 当前原生批准协议证据

当前本机 Steam build **26284217**，`Assembly-CSharp.dll` 4,583,176 bytes、mtime UTC 2026-10-29 00:38:15；2026-10-30 用既有 ILSpy 9.1.0.7988 重新只读反编译，来源保留在 `.preview/native-approval-protocol-20261030/`。未启动游戏或执行 Mod。

- `ModLoader.cs:608–714` 的 ParseManifestCore：XML 值 Trim，Assembly 反斜杠转 `/`；按完整路径 OrdinalIgnoreCase 去重并保留首项拼写/清单顺序。管理器路径边界另外拒绝路径跳转/链接/保留名。
- `ModFingerprint.cs:15–43`：manifest 原字节 SHA-256；逐个声明 DLL 的原字节 SHA-256；以 `manifestHash + "\n" + path + "|" + hash ...` 的 UTF-8 字节 SHA-256 作为 combinedHash。全部小写 hex；DLL 路径不排序、不转小写。游戏批准不覆盖普通资源；独立手动安全检查才计算全包 content_hash。
- `ModTrust.cs:30–50,66–89,128–147`：ES3 的 modTrust 为 string，内容是 `{records:[...]}` JSON；Approve 写所列字段，CompareFiles 按 source 精确大小写、UInt64 的规范十进制 workshopId、combinedHash 判定 Match。detected/declared/gameVersion/日期不参与 Match。
- `ModLoader.cs:270–355`：官方 block/pause 优先；未列出的 DLL 仍拒绝；Match 直接允许后续加载而不重扫。更新路径才扫描、拒绝硬标志、检测权限必须包含于声明及旧记录检测集。新的用户批准 detected=null 表示没有检测证据，更新后没有已检测能力的继承授权；显式保存可批准新的当前指纹。
- `ModLoader.cs:383–469`：**游戏内菜单**首次启用会检查列出 DLL 存在、可读程序集身份、不能冒充游戏程序集、ModScanner.Error/HardFlags、未声明能力；成功后 ApproveAndEnable 写记录。管理器最新用户启用路径不调用这套扫描，不声称与菜单的扫描过程等价。
- `ModCapability.cs:64–80` 与 `ModTrust.cs:149–152`：Order 与 Undeclared 对 null 使用空集合处理；ModLoader 的详情/审计及 ModSecurityDebug 通过 FormatIds 显示 detected，没有直接读取 null.Count 的调用。null 不影响 Match 身份判断。

`tests/native_mods/fixtures/ApprovalProtocolOracle.cs` 是独立 .NET 惰性字节协议 oracle：从测试清单解析路径并重算指纹、读取管理器写出的 typedJSON 和记录，使用上述原生比较条件验证 Match。它不加载候选 DLL；原生运行/IMod 初始化仍需真实游戏验收。

## 验证与真实样本

最新手动检查分离候选 `.venv/Scripts/python.exe -m pytest tests/native_mods -q`：94 项通过。原生批准相关 22 项验证无安全服务/解析器依赖、不读取普通资源、detected=null 的诚实状态、独立 .NET Match 协议 oracle、DLL 字节漂移、写入守卫、内部校验不重复解析日志及恢复不复活批准。原有库存、隔离副本、ZIP、配置、方案、迁移、撤销和恢复回归保留；真实源文件不存在时明确 skip，不以合成数据替代真实来源结论。

性能实测使用 **9 个当前真实工坊包的隔离副本**，共 **60,542,537 bytes**，其中声明 DLL **39,430,656 bytes**；包括 CyberElectronics 1.4.121（34,812,190 bytes）和 brewing_expansion 3.6.4-formal.22（18,377,567 bytes）。使用实际 DesktopContext/DeploymentGuards，仅对隔离状态保存：首次保存 **3.499 秒**，随后相同选择保存 **0.970 秒**，9 项均为用户批准、detected=null。后一次保存内库存遍历 3 次约 0.394 秒、18 次 DLL 指纹计算约 0.301 秒；Player.log 仅返回快照读取 1 次（该夹具日志缺失，不能代表大日志解析速度）。证据 `.preview/native-manual-approval-20261030/real-package-save.json`；未执行真实 Mod、未修改真实游戏/Steam 数据。

补充相同真实包副本加 **7,339,875 bytes 合成日志** 的成本测量：快照 0.616 秒，其中日志解析 0.281 秒；首次保存 3.808 秒，内部校验不解析日志，返回快照的唯一一次日志解析为 0.261 秒。该日志只用于测量，不冒充真实运行记录；证据 `.preview/native-manual-approval-20261030/log-cost.json`。

`legacy_profile_shapes.json` 按旧服务实际输出结构构造边界夹具，身份为合成值，不冒充用户真实方案。

`tests/native_mods/fixtures/real_inventory_summary.json` 是一次真实只读 DTO 捕获（路径和版本令牌脱敏），供调用方对照字段，不被产品加载，也不是固定预期库存。本轮惰性 oracle 验证 detected=null 的记录仍满足游戏 Match 字节协议；真实游戏启动、IMod 初始化和游戏重启读回尚未执行。独立手动安全检查由其服务负责，不参与普通批准验收。
