# 原生模组服务 v1

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
  `approval={state: unknown|approved|changed|updated_unverified|developer_mode, declared, detected, approved_at, updated_at, scan_status}`；scan_status 为历史或 unknown，绝不等同本次安全扫描。
  `run={state: unknown|running|not_running|failed|partial, runtime_state, audit_line, audit_lines, events, observed_entry_count, log_path, ...}` 是最近 Player.log 启动证据，不代表实时进程；实时进程在 runtime。无日志或没记录保持 unknown；coverage 仅限 reported_entries，当前未扫描 IMod 入口总数，不能断言未记录的入口运行。
- `detail(installation, id)` → `{revision, mod}`，mod 补充完整 files、反向依赖等。
- `validate(installation, enabled_ids, rules=None)` → `{enabled_ids, sorted_ids, issues, blocking, approval_required_ids}`；None 使用已保存规则，显式 [] 才忽略规则。
- `enable_preview(installation, enabled_ids, id)` → 同 validate，自动加入已安装依赖（缺失不伪造安装）。
- `disable_preview(installation, enabled_ids, id)` → `{affected_ids, enabled_ids}`（返回同时停用反向依赖后的草稿，前端自行确认）。
- `save(installation, expected_revision, enabled_ids)` → 新 snapshot + `{recovery_id, saved: true}`。校验依赖/环，保留手动合法顺序；只改 modOrder，停用项删对应 modTrust.records；不创建批准。预检有阻断就拒绝；外部漂移/运行中/未知拒写。
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
- `source_switch(installation,id,expected_revision,source='workshop')`：要求现有同 ID 工坊副本，移走本地遮蔽副本、保留目标启用顺序、撤销该 ID 批准，等待游戏重审。两者返回 snapshot + recovery_id。
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

## 验证与真实样本

最终候选完整验证 `.venv/Scripts/python.exe -m pytest tests/native_mods -q`：65 项通过。包含隔离复制实际工坊包、SaveFile.es3、可用原生配置/日志后读写，以及故障注入、漂移、链接、恶意 ZIP、批准撤销、恢复、未知字段保留、方案可移植规则/来源、版本变化提示和旧方案迁移。真实源文件不存在时该用例明确 skip，不以合成数据替代真实来源结论。

`legacy_profile_shapes.json` 按旧服务实际输出结构构造边界夹具，身份为合成值，不冒充用户真实方案。

`tests/native_mods/fixtures/real_inventory_summary.json` 是一次真实只读 DTO 捕获（路径和版本令牌脱敏），供调用方对照字段，不被产品加载，也不是固定预期库存。实际游戏启动、原生 UI 审批和重启读回尚需宿主实机验收；测试未执行任何 Mod DLL。
