# 数据、身份与接口契约 v1

### PS-03-04 回退与历史消费者

- `deployment.history {installation_id,offset=0,limit=10}` 是后台任务，分页上限 10、响应上限 30000 UTF-8 字节。返回真实 `current_generation`、`records`、`total/next_offset`、`issues`、`recovery_required`、`verification_status=not_run`、`cleanup_available=false`。记录含 `transaction_id/plan_id`、可空 `generation_id/sequence/committed_at`、实际 `state`、`kind=deployment|rollback|unknown`、`reverts_transaction`、`rollback_allowed/blockers`、`retention {protected,reasons,refs,cleanup_available}`。静态可预览条件不授予执行许可。
- `deployment.rollback.create {installation_id,transaction_id,expected_generation,idempotency_key}` 全部 UUID、拒绝额外字段；来源仅为该安装已认证提交的事务。返回普通固定计划预览，附 `intent=rollback/source_transaction_id`。`plan.inspect/approve` 共用原入口和原生确认；`deployment.rollback`、`plan.apply`、`assistant.apply_plan` 均以 `{plan_id,plan_hash,approval_id}` 进入同一执行器。AI 不能自签批准。
- 回退的 `rollback_source` precondition 封存来源事务/代次/计划哈希、MAC header signature、当前代次，以及服务器派生备份路径、强哈希和物理身份；这些字段参与整个 ActionPlan 哈希和批准绑定，客户端不能提交文件操作或恢复路径。内部恢复 Profile 不创建草稿，不修改用户方案。
- 新计划封存 `owner_policy=explicit-v1`；即使字节相同，共享 owner 增减也生成真实文件事务和新代次，持久化新归属。旧计划继续按其原有字节变更规则验证历史。
- 恢复问题使用独立 MAC 域的私有 issue journal，返回 `state=recovery_required|resolved` 与 `files[{target_root_kind,relative_path,expected,actual,preserved,copy_sha256}]`；同时保留 `conflicts/resolved/recovery_required` 兼容投影。先持久 DB 阻断、再写 header、实际保全及 preservation，最后复查才写 resolved。没有成功保全的条目明确 `preserved=false`，不能解释为副本存在；header 尚未写成也保留 DB 恢复阻断，普通启动恢复不能自动清除。
- `deployment.conflicts.recheck {installation_id}` 只验证人工已恢复的实际文件、所有相关原备份及承诺的冲突副本，再持久解除问题并调用元数据恢复；不覆盖游戏文件。返回 `resolved_count/remaining_count/recovery_required/issues/verification_status=not_run`，消费者必须重读历史和部署状态。所有历史、owner、基线、回退来源及未完成事务引用保护，未提供部分删除或清理入口。

### PS-03-03 实际执行与持久恢复入口

- `plan.apply {plan_id,plan_hash,approval_id}` 与 `assistant.apply_plan` 使用同一关闭字段 DTO、JobManager 和 `DesktopContext.deployments.apply`。非空计划必须带后端原生批准 ID；no-op 可为 null，重验后不创建事务/代次。AI 无批准发行入口、路径、journal、故障开关或 shell 参数。
- `deployment.status {installation_id,plan_id?}` 为同步持久快照，包含 transaction/current_generation、blockers、recovery_required、execution_available、startup_recovery 和固定 `verification_status=not_run`。preview/approve 的 `execution_available=false` 仍描述只读入口；实际可执行性取此快照并在 apply 内重新检查，不把快照当写权限。
- `deployment.recover {installation_id}` 为后台任务，恢复开始后使用独立有界预算。启动 composition 在开放正常消费者之前调用同一个 RecoveryService。应用取消转受控恢复；UI 等实际任务终态后复读持久状态，JobManager 的 cancelled 不能替代 rolled_back。关闭预览不能伪造已恢复。
- SQLite schema 7 增加 `deployment_current(installation_id,generation_id,manifest_hash,manifest_file_hash)`，安装唯一，两个实体 FK 和两个 SHA256 校验约束。迁移不回填历史 DTO 为 current；实际 executor 同一 SQLite 提交登记 committed Transaction、不可变 DeploymentGeneration 和 current 指针。
- `deployed=true/committed` 仅说明文件部署提交。没有 RuntimeSession/玩法/加载验证，`verification_status=not_run` 不升级。回执 `io_error` 保留占用、权限、空间分类、原生错误/errno 及实际尝试数。
- host 构造的 `deployment_fault(point,details)` 只供隔离验证，不在 bridge/tool、环境变量或产品设置暴露。真实消费者不接收 Mock 终态。

### PS-03-02 统一计划预览与原生批准

- `DesktopContext.plans` 为唯一 PlanService，生产 `plan.create {profile_id,expected_revision,idempotency_key}`、`plan.inspect {plan_id,plan_hash,offset?:0,limit?:1..20}`、`plan.approve {plan_id,plan_hash}` 均返回后台任务。`expected_revision` 是已提交 Profile DTO 修订；编辑器状态新增 `profile_revision` 显式提供此值，原 `revision/committed_revision` 仍是草稿编辑/保存计数。未保存内容拒绝，草稿修订另纳入计划绑定，撤销回到已保存内容后也必须重新预览。
- `assistant.propose_plan` 和 `assistant.inspect_plan` 使用相同闭合请求、后台任务和 PlanService。没有 assistant 批准工具；PS-03-03 新增的 `assistant.apply_plan` 只消费已有原生批准，见上节。提案不能包含路径、operations、actor、scope 或 approved。模型即使调用公共 plan.approve，也只能触发相同宿主原生人工确认，不能提交“用户已批准”作为依据。此处是实际本地工具路由，真实 provider 与 AI 编排仍属 M4，未进行联网调用。
- 预览返回轻量 `plan` 头、`files/total/next_offset/no_op/can_approve`、`space`、空 `configuration_changes` 与 `configuration_policy=unsupported`、`execution_available=false`。头保留计划身份/hash、Profile/代次/完整 baseline/指纹/有效期、空间需求、重启和批准范围；完整 operations/preconditions/verification_checks 在真实 SQLite ActionPlan 中，头提供相应计数。`backup_count` 是完整范围数量，头 `backup_scope` 是本页具体范围；所有备份目标可从 files.backup_required 逐页核对。文件行包含 change、before/after hash、前后 owner 集合、大小和备份要求。分页按实际字节自适应，next_offset 不必增加20；每页不超过30,000字节，适配32KiB真实任务限额，不能截断成完整结果。
- 拒绝保留稳定 PLAN_*／GUARD_* 代码；未托管冲突和托管漂移返回有界 conflicts 目标/hash，不能留下可批准的旧计划。目标冲突、确定兼容性阻断、未支持配置、空间不足、路径/来源/基线/指纹/修订漂移及过期均不产生安装写入。受控文件动作仍只有 install_owned/replace_owned/remove_owned；supported_config 尚无适配器，非空 Profile.config_overrides 明确拒绝。
- 后端 Approval 固定 actor=native-user、plan_hash、精确 scope 和计划到期时间；其 approval_id 是私有32字节签发密钥对域分隔 plan_hash 的 HMAC-SHA256 前128位。密钥仅在实际宿主人工确认完成后于私有 data root 创建，以原生独占创建、flush 和非链接读句柄核验；不经桥返回，不是客户端提供的凭据。普通 Store 插入自行构造的 Approval DTO 不能取得签发身份。签发密钥丢失、内容或作用域改变、过期、计划绑定漂移均拒绝。此边界不防御任意本机代码执行或管理员直接读取/改写全部私有数据。
- no-op 仍持久化真实完整 BaselineSnapshot 和 ActionPlan，但不创建 Approval、Transaction 或 DeploymentGeneration。相同幂等键重试返回同一计划/hash/期限，不悄悄延长；变化后的旧键拒绝。批准入口仅写管理器记录；实际部署使用 PS-03-03 的 `plan.apply`，恢复使用 `deployment.recover`。历史用户回滚和 game.launch 仍归后续卡片。

### PS-03-01 共享部署守卫接口

`DesktopContext.deployment_guards` 使用已登记 Installation、完整 RuntimeFingerprint 和同一真实 SQLite。生产桥提供只读 `deployment.guard_status {installation_id:UUID}`；不接受路径、动作、批准、PID 或任意进程参数。返回 installation_id、ready、blockers、process_status（clear/running/unknown）、execution_available=false 和快照边界说明。ready 只表示此刻未发现守卫阻断，不能作为稍后写入的许可。PS-03-03 的 plan.apply 已接入同一共享守卫；deployment.rollback/game.launch 尚未交付，仍返回 UNSUPPORTED_COMMAND。

原生后端消费者使用 `DeploymentGuards.operation(installation_id, intent)`，intent 为 apply/rollback/launch；整个上下文持有安装路径链、指纹文件句柄和真实安装身份 OS lease。准备目标使用 `prepare_target(installation_id, root_kind, relative_path)` 返回不可变 TargetSnapshot（祖先身份、目标身份/强哈希或不存在）；准备允许在游戏运行或有未完成事务时只读执行。每次文件动作必须使用 `session.step(snapshot)`，从返回 PinnedTarget 的当前句柄消费；不存在目标仅可用其 `create_new()`，以 CREATE_NEW 拒绝抢先创建，禁止重开已检查的字符串路径。不隐式创建父目录。该接口是内部 guard 集成，不是批准、备份、journal 或 executor。

进程守卫当前限定 NTFS，从固定 EXE 句柄取得实际文件使用者，再以同一进程句柄核验创建时间和映像 section。每次以管理器自身映像作原生能力正控制；相关进程查询拒绝、候选新增、接口/文件系统不支持及异常返回均为 unknown。不会按名称或缓存路径忽略受限候选，不自动提权。具体 NT 内部能力与 Windows 版本边界见04协议；其他文件系统当前不能部署或启动。

守卫拒绝使用稳定 GUARD_* 代码；状态 RPC 不暴露其他进程路径、PID 列表或异常正文。stage/ready/applying/rolling_back/recovery_required 的持久 Transaction 阻断新 mutation/启动；failed/cancelled 且 last_sequence>0 同样阻断。planned/approved 尚未进入文件阶段，不因此禁止只读计划准备。持久事务读取不分页遗漏；保留但未完成解释的 journal 也阻断。损坏/无法读取的依据不能得到 ready=true。

本卡不改 DTO/schema，不提前提供恢复状态转换。PS-03-03/04/06 后续必须复用同一 operation/check/step，并分别补充 journal 对账、批准/基线、原子替换、恢复能力及真实启动会话；不能以本次状态 RPC 或合成文件消费者证明这些未来功能已完成。

### PS-02-05 包库详情与工作流接线

- `package.detail {release_id,manifest_hash,offset?:0,limit?:1..20}` 返回后台任务，使用只读 SQLite 快照验证锁定版本 manifest 和实际库字节，分页输出文件目标/hash/大小/生命周期/归属/角色。返回 display_name、version_raw、roles、integrity、total/offset/next_offset、detail_hash；同目标同 hash 的 shared_owner_releases 与不同 hash 的 conflicting_owner_releases 分开。owner_integrity 明确其他 owner 只代表索引声明，ownership_scope=library_file_entries_only，deployment_generation=null。
- 详情目标比较沿用 Windows 大小写/Unicode NFC 和目标根别名语义，相邻目录不产生所有权。detail_hash 绑定完整详情与 owner 标签，分页发现漂移回到首页重新读取。响应不超过 48 KiB，缺失/损坏/锁变化/超限分别返回 PACKAGE_DETAIL_NOT_FOUND/CORRUPT/LOCK_CHANGED/LIMIT；不回显原始异常或任意路径。任务可取消，前端取消并释放离开视图后的迟到任务。
- 前端接通既有 package.adopt_preview/adopt_files/adopt 白名单。snapshot_id 是 UUID，manifest_hash 是 SHA256；paths 是预览中的 1～128 个精确文件路径，由后端再次核验，不接收游戏根或写入目的地。操作失败保持失败语义，UI 提示重新扫描/预览，不能当入库成功。
- 双列表拖拽调用 `profile.edit` 的 `move_selections`，绑定 expected_revision；按钮启停继续使用 set_enabled。`move_selections {release_ids,selected,before_release_id}` 一次原子调整启停与显示位置：release_ids 必须来自同一栏，批量按原列表顺序移动，before_release_id 为目标栏未移动项或 null（目标栏末尾）。同栏拖动只改变显示顺序，跨栏同时改变启停；无效/跨方案/过期修订拒绝。批量是一项编辑与一条撤销记录，不新增部署接口。选择新清单先清旧预览；取消后不能确认旧文件。导出仍由显式原生保存选择器新建用户指定 JSON，这是受控导出的写入范围，不代表其他操作可以写安装树。
- 方案中的 `selections` 数组顺序是 Mod 版本的显示顺序，草稿历史、v2 清单导入导出及已保存 Profile 均保留；不改变加载器、MelonPriority 或 Harmony 规则。现有 `Profile.display_order` 仍按 Component ID 校验，不混入 release_id。筛选不改变顺序：插入锚点来自当前可见且未移动的行，隐藏项相互次序保持。
- `profile.get` 等草稿状态响应给已解析选择增加 display_name，直接从本地 ModPackage 取得，不依赖包库当前分页；未解析时为 null。该显示字段不进入版本锁、撤销内容或便携清单，不改变既有存储 schema。

### PS-02-04 有界兼容性报告

- `compatibility.check {profile_id,expected_revision,profile_kind?:"draft"|"committed",enable_release_ids?:UUID[],disable_release_ids?:UUID[]}` 经统一后台任务返回报告，默认 draft。每个候选数组最多 200 项、不得重复或交集；不接收路径、规则或 apply 字段。检查和候选预览不修改 Profile，不自动启用、删除或排序。
- `CompatibilityService` 从只读 SQLite 快照取得真实 Profile、版本锁、组件、关系、文件及已存日志；相关发布版本的 manifest 和库字节均重验。报告绑定 profile_kind/revision、installation、fingerprint、input_hash、verified_release_ids 和 report_hash；结束另开快照检查元数据漂移，再重验相关库字节。配置修订或输入变化拒绝旧结果，界面清除旧报告并取消/丢弃迟到任务。
- 报告分 dependency/file/runtime 类别，finding 带 severity、code、端点/版本、来源、指纹范围和 reason_chain。`enable_preview` 展示完整启用闭包，`disable_preview` 展示反向影响集合；候选另算 `candidate_blocking/candidate_finding_codes/candidate_unknown_count`。`complete` 仅指闭包完整，`compatibility_allows_plan` 仅指本检查未发现阻断；始终保留 `dependency_coverage=stored_facts_only`、`runtime_compatibility=unknown`，不输出全局 plan_ready。
- 硬依赖只在 confirmed、受支持的显式来源、精确 fingerprint_scope 与完整指纹同时满足时阻断。缺失目标可用持久化 Relation 的 `capability="component:<UUID>"` 表示，避免伪造不存在的外键端点。数值发布版本支持 1～4 段和逗号组合比较；其他版本语法保留未知。AssemblyRef 仅是软编译引用，AssemblyVersion 不转换成框架 API 能力；图接口接受带独立来源/指纹的整数能力事实，当前包库未提供这类能力证明时按未知处理。
- 本地导入将实际 helper 的 AssemblyRef 与内容 API 要求保存为 Relation：前者 inferred soft_dependency，后者 confirmed embedded_metadata 声明；portable 声明没有安装指纹范围，不能自动提升为当前硬依赖。沿用原 Component/FileEntry/Relation DTO，无新数据库 schema。框架/type/value 内容 ID 命名空间分别比较，静态重复只作提示。
- 同 Windows 目标不同 hash 是文件阻断，同 hash 共享仅提示；规范化大小写、Unicode NFC 和 game_root 下 Mods/Plugins/UserLibs 别名。程序集遮蔽继承 Loader 0.7.3 阶段/排除/子目录规则，必须有明确配置与指纹；当前服务未提供 Loader 配置证明，因此该项保留未知。显示顺序不改变 Loader、MelonPriority 或 Harmony 顺序。
- 运行提示复用 PS-01-06 对管理器已存会话日志的规则，不读取游戏日志；保留 source/epoch/session/evidence，最多 warning，不证明当前部署。`runtime_evidence.current_deployment_verified=false`，缺日志保持 unknown。
- 服务上限：200 个版本/包、1000 组件、2000 关系、4000 文件、4 MiB 汇总输入、2000 条/2 MiB 日志、相关唯一库字节 512 MiB、48 KiB 报告、120 秒合作式预算。超限拒绝，不以截断图给出确定结论。错误为 COMPATIBILITY_INPUT_INVALID/LIMIT/PROFILE_NOT_FOUND/STALE/UNRESOLVED；后台任务仍遵守取消与清理契约。

### PS-02-03 配置草稿与便携清单

- SQLite schema 6 增加 `profile_drafts` 和 `profile_editor_selection`，按既有备份及事务迁移协议升级。草稿包含名称、分组、锁定选择、已保存内容及最多100步撤销/重做历史；每次有效编辑或撤销/重做递增草稿 revision。批量启停是一项编辑，重开保留草稿及当前配置选择。
- `ProfileService` 是当前配置编辑入口。`profile.list {installation_id}` 返回轻量列表及 selected_profile_id；create、clone、select 只改变本地配置。`profile.get {profile_id}` 返回完整草稿。`profile.edit {profile_id,expected_revision,action}` 的封闭 action 为 set_selection、set_enabled、move_selections、rename、set_group、undo、redo；过期修订拒绝覆盖。新 release 不替换已有 release_id/manifest_hash 锁。
- `profile.commit {profile_id,expected_revision}` 是后台任务，校验每个锁定版本及实际库字节，并原子发布现有 `contracts.Profile`/`Store.profiles` 契约；后续 planner 仍消费该 DTO 的 revision。草稿 revision 与已提交 DTO revision 分别计数。任务终态仅返回小摘要，UI 再调用 get；未解析版本可编辑、导出和重新打开，但不能提交。该操作没有部署副作用，不生成 DeploymentGeneration。
- `profile.choose {}` 只由原生选择器登记短期 source_handle。`profile.import_preview {source_handle,installation_id}` 校验 UTF-8 JSON schema、256KiB 大小、最多200个唯一选择、来源和分组引用，返回 preview_hash、逐项 resolved 状态及本地解析出的 display_name（缺失为 null，不加入便携清单）。`profile.import_commit {source_handle,preview_hash,installation_id,name}` 绑定该来源、原始字节摘要及目标安装；变更文件、过期句柄、错误 hash 或目标安装均拒绝。导入只创建草稿，不下载内容、不自动提交配置。
- `profile.export {profile_id}` 返回 `{manifest}`；`profile.save_export {profile_id}` 经原生保存选择器仅新建 JSON 文件，不覆盖已有文件，不写安装或数据根。便携清单 `schema_version=2` 只有 name、groups、selections；选择项只有 release_id、manifest_hash、selected、version_raw、source(kind/observed_at)、group。无 installation_id、source.reference、路径、凭据、DLL 或存档。未知版本文本及缺失 release 的锁定信息原样往返；明显路径、控制字符或凭据内容拒绝，不静默改成 null。未知清单 schema 和额外字段拒绝。旧仓储 v1 导出维持原兼容行为，新编辑器使用 v2。


### PS-02-02 本地安全导入

- 原生选择器 `package.choose {kind:"dll"|"directory"|"zip"}` 返回 `source_handle/display_name`，取消返回 null。JS 不接受源路径；句柄仅当前宿主有效、300秒过期、最多8个。拒绝 data root 和 RimCrow 源目录重叠。依据 2026-09-29 用户反馈，明确选择的游戏安装文件可以作为只读导入来源；这不授权写入、移动或删除安装文件，也不自动登记安装接管归属。只支持本地 Windows 普通文件/目录，不打开 UNC 或链接来源。
- `package.import_preview {source_handle}` 是后台任务。DLL、目录和 ZIP 都复制到 data root/imports 下本次 UUID 目录，使用不透明平面文件名，归档路径不作为写入路径。预览返回 preview_id、preview_hash、display_name、total、files、next_offset、can_commit、warnings；`package.import_files {preview_id,offset?,limit?:1..20}` 分页。预览最多8份、300秒过期；过期/关闭宿主仅清理自身平面暂存，遇到不安全目录保留并报错，不追踪链接清理。
- 每个预览文件包含原 path、SHA256、size、target_root_kind/relative_path、lifecycle、ownership、roles、parser_status。明确 Mods/Plugins/UserLibs 树沿用目标根；单个裸 DLL 建议 Mods；UserData 分类配置或业务数据；其他路径保留 game_root/unowned/unknown，只存库，不授予部署权。所有 DLL 必须调用现有 LPAC MetadataWorker，缺 runtime/helper、隔离失败、超时、畸形输入均拒绝本次预览，不退回主进程加载程序集。
- `package.import_commit {preview_id,preview_hash,display_name,version_raw?}` 是显式确认后台任务。校验预览和每份暂存字节，复制到既有 SHA256 包库，以一个 SQLite 事务提交 ModPackage/ModRelease/FileEntry/Component，随后经 PackageRepository.release 校验。返回 release_id、manifest_hash、file_count、enabled=false、deployed=false；不会创建 Profile、BaselineSnapshot 或 DeploymentGeneration。生产 Profile 可校验消费该版本。相同显示名、版本与文件 manifest 幂等；同版本异内容不覆盖原 release。
- commit 使用的是预览时已冻结的私有字节，不重新把来源目录的新内容混入确认结果。取消/失败不留下半包索引；复制完的未引用不可变字节可保留供重复校验使用，无自动淘汰。失败暂存清理后需重新预览；成功暂存清理失败返回 cleanup_pending，而不伪造提交失败。关闭对话框仅释放前端订阅，已存预览仍受宿主过期/关闭清理规则约束。
- 策略拒绝返回安全 `error_code/message/can_commit=false`，不回显任意异常或路径。异步任务 succeeded 仅表示拒绝结果已正常交付，消费者必须检查 error_code；只有无错误的 import_commit 返回 release_id 才表示入库成功。取消仍为 cancelled；所有复制、解压、解析和提交检查取消，SQLite 提交通过 task.commit 线性化。
- 公布限制：128个文件、256个目录/ZIP条目、路径240字符/16层；单文件及 ZIP 源64MiB、展开总量256MiB、压缩比100、120秒预览预算、ZIP中央目录1MiB。ZIP只接收 stored/deflated，拒绝 ZIP64、加密、多卷、嵌套压缩和脚本安装。中央目录分配前检查条目/字节数；流式统计输出并校验 CRC、声明大小和完整 deflate 结束，不能凭被截断的 ZipExtFile 输出通过。单页20条限制输出，不向JS返回原始程序集/worker stdout。
- Windows目录及源文件通过有读访问权且不共享删除的句柄固定，暂存与库分片目录在写入前固定到提交结束，防止目录替换竞态；源文件不共享写入，拒绝软硬链接和重解析点。元数据证据独占创建，不能覆写预置链接目标。无法取得保护句柄则拒绝，不降低保护继续。
- 实际界面位于顶部“导入”，独立于安装选择；原生文件选择→分页预览→确认入库调用同一产品 API。CASE-023 与 CASE-024 分项 synthetic 证据包含实际 WebView 合成 ZIP 工作流和真实 worker 的 DLL/目录/ZIP 集成；不等同 G1 人工视觉/系统 DPI 或实机部署验收。

### PS-02-01 本地包库与显式接管

- 数据库 schema 5 增加 `package_adoptions(snapshot_id,release_id,adopted_at)`，保留每次已确认接管的来源与版本关系；沿用事务迁移、备份及结构/外键检查。包字节在 data root 的 `library/<hash前2位>/<SHA256>`，仅复制、fsync、校验后发布，不与安装建立硬链接，不自动淘汰。
- `package.adopt_preview {installation_id}` 是后台任务，要求最新库存扫描完整，随后重新枚举并强哈希 Mods/Plugins/UserLibs 全部文件。`BaselineSnapshot` 的 complete 仅覆盖此显式三根范围，包括未知文件；不读取 UserData、存档或根外数据。任何错误、取消或范围限额都拒绝完整基线。快照是只读观察，不产生 DeploymentGeneration，也不批准部署。未来超出三根的配置计划必须另取相应完整事实。
- 缺少完整扫描时区分 `ADOPTION_SCAN_REQUIRED`、`ADOPTION_SCAN_RUNNING`、`ADOPTION_SCAN_INCOMPLETE`，返回有界的失败路径与错误代码。桌面“扫描并读取清单”依次调用实际库存扫描和接管预览；扫描失败保留原因，不反复要求用户重新扫描，也不省略逐文件确认。成功入库后刷新当前方案的包库列表。
- 预览终态返回 snapshot_id、manifest_hash、scope、coverage、total、前20个 files 和 next_offset；`package.adopt_files {snapshot_id,offset?,limit?:1..20}` 获取其余清单。未登记路径显示 external；每项返回生命周期、同哈希所有 release owner 和冲突版本。包关联以逐文件明确选择为准，目录相邻不产生归属。
- `package.adopt {snapshot_id,manifest_hash,paths:[1..128个精确相对路径],display_name,version_raw?,package_id?}` 表示确认此快照和清单，返回后台 task_id。只接受已扫描且识别为 immutable 的逐项文件；配置、业务数据、生成物和 unowned 不接管。复制前后重验完整快照；源安装不写入、不移动、不删除。提交与任务取消线性化，失败保留未发布的已校验字节供重试，SQLite 中不留半个 release。未引用字节只可经显式 `delete_unreferenced_artifact(...,confirmed=True)` 清理，引用中字节拒绝删除；没有自动 LRU。
- 发布 manifest 哈希由原文 version_raw 和排序的文件目标/哈希/生命周期计算；同文字版本不同内容产生不同 release。包 ID 缺省由安装身份与显示名稳定生成，也可传已有 package_id 表示同一包的新来源；同一确认重复提交幂等。共享文件以 `shared_identical` 明确登记允许相同字节共享，全部 owner 从 FileEntry 查询；这不是已部署状态或目录删除权限。DLL 建立 Component 映射，未知程序集声明保持 null/unknown，不推断执行结果。
- `package.list {offset?,limit?:1..20}` 返回后台 task_id，终态包含发布版本、库字节完整性与有界来源摘要；哈希循环检查取消，不占用桥接请求锁等待磁盘。`PackageService.resolve_release(release_id,manifest_hash)` 为后续导入服务提供校验消费者。生产 DesktopContext 中现有 Profile create/update/export 同时验证版本锁、manifest 与所有库字节，损坏/缺失拒绝消费。底层 `Store(path)` 仍可存 DTO 结构资料；它不是包发布入口。PS-02-02/03 的导入格式和草稿 UI 未在本任务提前实施。
- 合成服务/API/真实 SQLite/文件集成证据归 PS-02-01；包库页面的人工操作闭环归 PS-02-05。G1 未通过时，本卡独立实现和 synthetic 结果不放行阶段门。

### PS-01-06 离线解释接口

`diagnostics.explain {installation_id,generation?:int,session_id?:UUID,offset?:int,limit?:1..20,snapshot_id?:SHA256}` 是只读工作台的产品命令。服务以 SQLite mode=ro 和单个读事务读取登记安装、运行指纹、所选库存代次及不可变日志；不打开游戏文件、不联网、不执行AI或计划。Loader版本取该扫描的policy，不用今天的环境覆盖历史扫描。默认库存选择最近完整代次（没有则最近尝试），返回 last_attempt、inventory_status、freshness=stored_observation，不能冒充重新扫描实机。

返回 diagnostics、summary、rule_version、generation、log_through、session_scope、coverage、total、next_offset 和 snapshot_id。每个结论区分规则/证据/库存身份/推断/待复现/候选动作；日志 evidence_ids 可经 evidence.read 读取原字节，静态条目通过 inventory.detail 读取元数据。未指定session时解释历史会话集合，各会话独立判断，不把所有日志当当前启动。coverage_scope=stored_inputs_only；即使库存及已保存日志投影完整，也不证明日志采集完整，log_collection_coverage=unknown。deployment_verification 恒为 not_run。

后续页必须携带首屏 snapshot_id；建议同时固定 generation/session_id。输入或解释结果发生变化返回 DIAGNOSTIC_SNAPSHOT_CHANGED，刷新首页后重试，不静默混页。前端不能上传日志文本、规则、路径、指纹或伪造诊断事实。最多读取512个库存条目/16MiB、2000条日志/4MiB，触限明确partial；规则输出至多512条。单页最多20条/约40KiB；SQL进度及解释阶段检查2秒预算，超限提示选择具体会话。数据截断不能产生完整覆盖或部署通过结论。

此接口输出观察投影，不篡改既有 DiagnosticEvidence DTO，也不将库存 entry_id 伪装成已登记 Component ID。历史片段及规则来源保留，未来计划服务必须重新验证当前指纹、绑定会话和授权范围。

### PS-01-05 已实现日志证据接口

- 数据库 schema 4 增加 log_sources、log_sessions、log_evidence、log_snapshots，沿用备份、事务迁移和失败回退。源游标、decoder/残行、会话状态与本轮证据原子保存。原始片段以 BLOB 保存，ID 由 source/epoch/字节范围/SHA256 确定，替换日志不改变旧 ID 的原文。
- `logs.watch {installation_id,user_logs?:string[]}` 通过实际 DesktopApi/LogService/JobManager 启动持续监视；同安装同选项复用 task_id，返回 reused。更换选项先 task.cancel 并等待终态。复用返回原任务 request_id；消费者按返回任务重新订阅。页面重载仅释放订阅，不取消或重复创建监视；DesktopContext 退出取消并释放句柄。
- `logs.sources {installation_id}` 返回允许源的 source_id/relative_path/kind，不暴露内部缓冲。`evidence.list {installation_id,after?:int,limit?:1..20,session_id?:UUID,group_id?:UUID}` 返回 entries/total/next_cursor；`session.list` 同分页字段。不同消费者独立保管 after，不共享可变读取游标。
- `evidence.read {installation_id,evidence_id,offset?:int,limit?:1..8192}` 仅从 SQLite 返回不可变原片段的窗口、raw_base64、text、excerpt_hash、window_start/end、total/next_offset。excerpt_hash 对应完整片段，字节窗口切在 UTF8 字符中间时 text 可含替换符，raw_base64 是准确字节依据；列表 text 去 ANSI 且截至512字符。跨安装ID读取拒绝。
- 允许已登记安装的 MelonLoader/Latest.log、MelonLoader/Logs 下最多16个直接 .log 文件，以及显式选择的最多8个 UserData 相对 .log 路径。不递归发现归档，只检查前256目录项；不读 Player.log、未选UserData或任意绝对路径。安装、源路径拒绝重解析点/硬链接及 data root 重叠。
- 最多4个安装监视、每安装64个历史源；每 poll 最多256KiB/1024条，长行按16KiB分片，取消可在块间生效。证据库最多20000条/32MiB原字节，快照最多20000条，达到限额失败并保留已存证据，不静默淘汰。查询只解码分页行，不返回全库。
- Latest/归档去重只凭同安装、不同源、至少一方为归档的完整快照 SHA256+长度；快照只对稳定且已消费到完整行边界的至多8MiB文件生成。仅隐藏已证实复制的字节范围，后续追加独立可见；两源旧证据ID仍可读取。超限/残行/无法证明的副本保留独立记录，不仅凭文件名或日志文字合并。
- 会话以源、epoch、启动标记和明确PID行分段；缺乏依据时 pid/started_at/generation_id 为 null，association_confidence=unknown。日志声明PID为 inferred，未向OS核实；只含时分秒不推算日期，无部署 generation 自动绑定。多行异常共享 event_group_id（最多128行），阶段只是日志观察，不是诊断结论。
- `session.list` 可附 `historical_reference {date,sha256,basis:"known_reference_hash"}`：仅已保存日志快照 SHA256 精确匹配固定原始资料，且会话字节范围在该快照内时提供。界面将其标为历史日志，非本次启动；不回填 `started_at`、PID 或部署代次，不从目录名、文件时间或相似日志推测日期。未知或追加范围保留未知。
- 固定 Loader 0.7.3 规则接受合法 `+build.metadata`，保留 PE 版本原文作为指纹及诊断来源；其他版本、预发布后缀或非法构建后缀仍降级未知。诊断有界查询先处理 DLL，避免大量图片等资产挤掉组件；总条目或字节截断仍明确 partial，不因调整顺序宣称覆盖全部库存。
- 元数据 helper 的 `managed` 表示程序集身份成功解析，不保证可选嵌入资源全部读取。`resource_coverage` 明确 complete/partial、总数、已报告数、遗漏数、跳过载荷数及输出限额降级。原有 64 MiB 输入、64 个资源、单资源 256 KiB、累计 1 MiB 和 256 KiB 输出上限不提高；超限资源只报告名称/长度/跳过原因，不伪造 JSON 或哈希。详情提示部分读取，关系图降级 partial，缺失端点记 unknown；不得据此断言资源或依赖不存在。

本文件把原报告的多套草案统一为一套开发契约。数据库表可分期创建，但不得改变以下语义。JSON 使用 snake_case、UTF-8；schema_version 初始为 1；UUID 为不透明稳定标识，SHA256 为小写 64 位十六进制；UTC 时间用 RFC3339。未知值用 null/unknown，不捏造 0.0.0。

## 1. 核心实体

| 实体 | 必需字段/关系 | 约束 |
|---|---|---|
| Installation | installation_id, canonical_root, file_identity, channel, app_id, build_id, fingerprint_id | 路径别名不能生成重复安装；路径不作为跨机器主键 |
| RuntimeFingerprint | fingerprint_id, game_version_raw, unity_version, loader_version, exe_hash, game_assembly_hash, metadata_hash, observed_at | 强哈希与来源分别记录；昂贵哈希后台完成前标 partial |
| ModPackage | package_id, display_name, source, trust | 管理器分配 ID；来源声明和推断不同 |
| ModRelease | release_id, package_id, version_raw, manifest_hash | 不可变内容版本；同文字版本不同哈希不得覆盖 |
| Component | component_id, release_id, artifact_hash, file_name, assembly_name, assembly_version, melon_info, roles, content_ids | roles 是集合，可同时为 melon_mod 与 itemforge_content |
| Artifact | sha256, size, library_relative_path | 包库原件不可变；禁止硬链接到可写游戏目录 |
| FileEntry | file_entry_id, release_id, source_hash, target_root_kind, relative_path, lifecycle, ownership | 独立 UUID；不接受任意绝对部署路径；路径规范化后判冲突 |
| Relation | relation_id, from_id, to_id/capability, kind, requirement_raw, source, confidence, fingerprint_scope | 独立 UUID；依赖版本可能是整数 API，不等于 SemVer |
| Profile | profile_id, installation_id, revision, selections, config_overrides, display_order | selections 锁 release_id/hash；显示顺序不控制 loader |
| BaselineSnapshot | snapshot_id, installation_id, scan_generation, manifest_hash, files, coverage | 首次完整只读接管的事实快照，不是部署代次；部分扫描不能成为可写基线 |
| DeploymentGeneration | generation_id, installation_id, sequence, manifest_hash, transaction_id, committed_at | 仅成功提交才产生代次，历史不可就地改写 |
| RuntimeSession | session_id, installation_id, generation_id?, pid?, started_at?, log_file_ids, association_confidence | 旧日志和进程外启动可无法关联代次，保留 unknown |
| Observation | observation_id, session_id, component_id?, event_kind, stage, evidence_ids | 注册、初始化、内容注册和玩法结果分开 |
| DiagnosticEvidence | evidence_id, source_id, epoch, byte_start/end, excerpt_hash, source_time, rule_id?, component_ids | 轮转后 epoch 改变；保留原始来源 |
| ActionPlan | plan_id, installation_id, profile_revision, base_generation?, baseline_snapshot_id, fingerprint_id, plan_hash, operations, preconditions, verification_checks | 首次无generation时绑定完整BaselineSnapshot；模型只提交提案，规范计划由后端计算 |
| Approval | approval_id, plan_id, plan_hash, scope, actor, expires_at | 只批准固定计划，变更后失效 |
| Transaction | transaction_id, plan_id, state, journal_path, target_generation, last_sequence | 幂等键、恢复与取消见 04 |

来源 source 统一为 `{kind, reference, observed_at}`，kind 为 author_manifest / embedded_metadata / loader_rule / runtime_log / user / heuristic。confidence 为 confirmed / inferred / unknown，不能只给一个没有解释的百分数。

PS-00-03 的完整 DTO 位于 `backend/contracts/`：17 个实体和嵌套 DTO 使用 `schema_version=1`，禁止额外字段。完整样本位于 `tests/fixtures/contracts/valid.json`，拒绝样本位于 `invalid.json`。RuntimeFingerprint 增加 source 和 partial/complete coverage；complete 必须具备三个强哈希。ContentId 使用 `{framework,type,value}` 命名空间；roles 为非空集合，未知为 unknown。时间仅接受 UTC RFC3339 字符串或带 UTC 时区的 datetime，不接受数值 epoch。版本原文可为 null 或原始字符串，不转换为 SemVer。Profile selections 为 `{release_id,manifest_hash,selected}`，revision 从 0 开始。

ActionPlan 完整字段以 04 为准，包括 profile_id、baseline_manifest_hash、idempotency_key 和 expires_at；PlanOperation 的写目标只允许 mods/plugins/user_libs/supported_config。FileEntry 的 game_root 仅表示库存事实，不授予计划写权限。Relation 的 from_id/to_id 指向 Component，或使用 capability。错误信封中的 ErrorDTO 由共享 DTO 生成；顶层信封兼容现有 API，不额外插入嵌套 schema_version。

## 2. 三层状态与关系

- desired：Profile selections 中选中/未选中；修改后 Profile.revision 增加。
- deployed：absent / matching / drifted / partial / unmanaged；取自磁盘与部署清单比较。
- observed：unknown / discovered / registered / initialized / content_registered / shadowed / delegated / self_disabled / failed。一个组件在多阶段可有多条 observation，摘要不能抹去后续失败。

观察状态绑定 session，不能以旧会话“已加载”覆盖新部署的 unknown。加载器注册成功不等于初始化成功，更不等于玩法通过。

关系 kind：hard_dependency、soft_dependency、capability_requirement、mutual_exclusion、assembly_shadow、file_conflict、content_id_conflict、runtime_interaction。显示顺序独立保存。不同框架的 BundleId 需按框架/类型命名空间判断，不能仅因同字符串就判冲突。

## 3. 包与所有权

FileEntry.lifecycle 为 immutable / shared_config / private_config / mutable_data / generated / unowned。ownership 为 exclusive / shared_identical / external / unknown。不同所有者的共享不可变文件仅在目标和哈希均相同且显式登记时允许；最后一个 owner 退出后仍需验证当前字节才能移除。配置和业务数据不因禁用或卸载自动删除。

采用现有文件分两步：只读识别和快照；用户确认后在管理器库内复制并登记。外部来源不自动取得整目录归属。裸 DLL 可登记缺失元数据的本地包，原作者、许可证、版本范围保持 unknown。

示意（管理器私有格式，不是 MelonLoader manifest）：

```json
{
  "schema_version": 1,
  "package_id": "11111111-1111-4111-8111-111111111111",
  "release_id": "22222222-2222-4222-8222-222222222222",
  "version_raw": "1.0-local",
  "components": [{"roles": ["melon_mod", "itemforge_content"], "file_name": "Example.dll"}],
  "relations": [{"kind": "capability_requirement", "capability": "itemforge.runtime_api", "minimum": 19, "source": {"kind": "embedded_metadata", "reference": "ItemForge.items.json/RequiredRuntimeApi"}}]
}
```

上例仅展示字段片段，不能作为完整 schema 测试通过样本。PS-00-03 输出完整有效/无效 fixture 和严格 DTO；涉及写操作时 extra fields 必须拒绝，不能沿用 AI 输出的宽松 extra=ignore。

## 4. 桌面 API

只暴露一个明确命令路由 `dispatch(request)` 与受控事件订阅/查询，不能反射枚举 backend 所有 public 方法。UI、CLI 验证入口和 AI 计划执行调用相同应用服务。

请求：`{schema_version, request_id, command, payload, expected_revision?}`。
成功：`{request_id, ok:true, data, task_id?}`。
失败：`{request_id, ok:false, error:{code,message,retryable,details,evidence_ids}}`。
长任务事件：`{task_id, sequence, type, state, completed, total?, stage, message_code, data}`。state 为 queued/running/cancelling/succeeded/failed/cancelled；终态唯一，UI 重连可获取快照与续传，旧 task_id 不能取消新任务。

| command 分组 | 命令名 | 首个消费者 |
|---|---|---|
| 桌面身份与本地检查 | app.info、app.check（payload 为 {}） | 最小桌面与应用信息详情 |
| 长任务 | task.snapshot、task.subscribe、task.events、task.unsubscribe、task.cancel | PS-00-04 统一客户端及进度显示 |
| 安装扫描 | installation.discover/select, inventory.scan/list, task.cancel | 安装页、扫描按钮 |
| 证据 | diagnostics.list, evidence.read, session.list | 问题与日志面板 |
| 包库 | package.import_preview/import_commit/adopt, package.list | 导入预览、模组库 |
| Profile | profile.create/clone/update/export/import_preview/import_commit, compatibility.check | 当前配置、清单导入预览 |
| 部署 | plan.create/inspect/approve, deployment.apply/rollback/recover | 差异、批准、历史 |
| 启动 | game.launch, session.verify | 应用后启动 |
| AI | assistant.start/cancel, assistant.propose_plan | AI 面板 |

`app.info` 返回 name、version、data_root、config_path、database_path、log_path、keyring_service、update_source（null 表示不配置更新源），以及 latest_check_task_id（当前宿主最近本地检查 ID，没有则 null）。路径仅在用户展开应用信息详情时展示。`app.check` 启动对管理器自有 SQLite 的只读身份/完整性/外键检查，返回初始 snapshot 及顶层 task_id；不会扫描游戏。页面重载后通过 app.info 的任务 ID 与统一客户端恢复检查状态。

### PS-00-04 命令桥与任务事件

- 唯一公开宿主方法为 `dispatch`。PS-00-04 首次开放 app.info/app.check 与五个 task 命令；后续仅按本文件新增小节显式扩展白名单，未交付业务命令仍拒绝。请求严格包含 schema_version=1、1–128 位非空可打印 ASCII request_id、command、payload，总 JSON UTF-8 不超过 16 KiB；多余信封/载荷字段、bool 代替整数、不支持的 schema 均拒绝。expected_revision 留给未来需要它的写命令，本阶段不接受。
- `task.snapshot`/`task.cancel` payload 为 `{task_id}`；`task.subscribe` 为 `{task_id,after_sequence?}`；`task.events` 为 `{subscription_id,after_sequence?}`；`task.unsubscribe` 为 `{subscription_id}`。ID 是 UUID；after_sequence 默认 0，为不大于当前任务序号的安全非负整数。
- 订阅/查询返回 `{subscription_id,snapshot,events,resync_required}`。事件额外含 request_id，关联启动请求；sequence 从 1 递增，type 为 state/progress/terminal。快照是最新事件，取消只绑定传入 task_id，终态 succeeded/failed/cancelled 不再改变。客户端按 task_id/request_id/sequence 去重，旧任务、乱序和重复事件不能覆盖当前状态或重复完成。
- 事件采用受控轮询，不创建 HTTP/RPC listener。每任务保留最近 128 条；游标早于保留窗口时 events=[]、resync_required=true，客户端使用快照续接。最多累计保留 128 个任务及 128 个订阅，达到上限返回 CAPACITY_EXCEEDED；不会偷偷丢弃活跃任务。任务记录是宿主内存态，关闭取消并清理，不宣称跨进程持久续传。
- 订阅归属一个页面 owner，闲置 300 秒过期；页面 before_load 清除旧订阅并轮换 owner，卸载也主动释放。过期、未知或另一页面的订阅 ID 统一返回 SUBSCRIPTION_EXPIRED。窗口关闭后拒绝新请求，合作式取消后台任务并限定等待 2 秒；超时明确报错，不伪称清理成功。
- 返回错误只使用稳定 code/message、空 details/evidence_ids，不回显参数、异常正文、路径或密钥。未知命令为 UNSUPPORTED_COMMAND、参数错误为 INVALID_REQUEST、未知任务为 TASK_NOT_FOUND、游标越界为 INVALID_CURSOR、内部异常为 INTERNAL_ERROR；后台工作异常用 failed/JOB_FAILED，不能变为成功。
- 自制 `test.*` 命令只能由真实桌面 harness 在 Python 构造器显式注入；生产入口没有开关/请求可启用它们。合成 reducer 测试与真实 pywebview→JS→Python→JS 测试分开出证据，前者不替代后者。

### PS-01-01 安装选择

- 在既有 `dispatch` 中增加 `installation.choose {kind:"steam"|"manual"}`：打开宿主本机目录选择器，返回短期 `source_handle`，用户取消时为 null。前端不得提交原始目录。`installation.discover {source_handles:[UUID]}`（1–8 个）返回后台任务；终态 data 为 `{candidates,selected_installation_id}`。`installation.select {candidate_id:UUID}`、`installation.use {installation_id:UUID}` 同样返回后台任务；`installation.list {}` 只读管理器中已登记 Installation，不自动扫描安装目录。
- source_handle/candidate_id 仅宿主内存有效，300 秒过期；来源最多 16 个，候选最多 16 个且总结果有大小上限。候选包括 canonical_root、file_identity、channel、app_id、build_id、valid、issues、warnings、fingerprint、historical_observation 和来源。只有有效且 coverage=complete 的候选可选择。根路径别名按解析后文件身份去重；安装内部的重解析点/硬链接拒绝，数据根重叠拒绝。Steam 库来源必须由用户主动选择，解析本地 libraryfolders 与两个已知 appmanifest，不在启动时扫描注册表或真实游戏。
- Demo AppID `4349200`、正式版 `4348910`；目录名称不决定渠道。手选目录缺少匹配 manifest 时 channel=unknown，app_id/build_id=null。当前 PE 资源的版本原文原样保留，包括 `0.46D`；无法可靠取得的游戏/Unity/Loader版本为 null。历史 Latest.log 版本以 historical_observation 单列，不能填入当前文件版本。强指纹覆盖 EXE、GameAssembly、global-metadata，布局还检查 Loader；不完整/漂移/限额结果不得成为可选择完整安装。
- 宿主只获得安装根授权时，可将 `InstallationService.allow_manual_manifest_probe` 设为 false；手选发现、选择和重新使用均不探测根外 Steam 清单，渠道/build 保持 unknown/null。该策略由宿主设置，前端不能扩权；默认已授权 Steam 发现行为不变。
- 读取仅针对已授权来源，单文件上限 512 MiB、总读取上限 1 GiB、块间时间预算 30 秒；Loader 文件上限 32 MiB，VDF/历史日志上限 1 MiB。按块检查取消与前后文件身份；这些是有界应用读取，不宣称操作系统级 IO/网络沙箱。选择和使用已登记安装均重新核验当前文件及 Steam 清单，变化返回 `{installation:null,fingerprint:null,error_code:"INSTALLATION_CHANGED"}`，表示校验完成但拒绝选择，数据库不登记新结果。
- 成功选择返回真实 `{installation,fingerprint}`，仅写本产品 SQLite。Installation ID 按文件身份保持稳定，当前 fingerprint_id 可原子推进，旧 RuntimeFingerprint 保留；因此旧计划不能绕过指纹重验。fingerprint.source.reference 记录 JSON 格式 `psmm.installation-source.v1`、discovery_sources（kind/root）与 file_facts，用于重启后的来源重验，尤其是 Steam 安装根别名；它不是外部调用授权文件。已选 ID 是宿主会话状态，重启后需要用户明确“校验并使用”。
- 登记使用后台任务提交点：提交前可取消并保持数据库原样；进入本地原子提交后取消不再伪装成撤销，完成真实登记后报告成功。安装登记不授予扫描模组、部署或启动游戏权限。全界面 RimCrow 一致性按 06 在后续工作台任务验收，本卡只接通安装选择消费者。

桌面 `DesktopContext.initialize()` 打开 `psmodmanager.sqlite3`，调用 `backend/storage/Store` 完成身份校验和迁移，并持有 inventory/profiles/plans 仓储；关闭桌面时关闭连接。SQLite application_id 固定为 0x50534D4D。PS-00-02 的身份空库是受支持的 schema 0，不能接纳 RimCrow 数据库。

路径通过安装 ID、artifact ID、evidence ID 在后端解析；文件选择器返回的源路径先登记为短期 source_handle，不得当部署目标。profile.import_preview校验source_handle/schema/大小/引用并返回缺失release项与预览hash；import_commit绑定预览hash和目标安装，源文件变化使预览失效。只读查询分页，UI 不拿全库原始日志；错误 details 不含密钥和未脱敏个人路径。

## 5. 数据库与导入导出

SQLite 只存索引、关系、计划和 journal 指针，包字节/证据原文独立存储。外键、唯一键、schema 版本和迁移前备份明确；失败恢复到旧 DB，不半迁移后继续启动。任务完成事件在持久化成功后发出。

Profile 导出默认是版本/哈希/来源清单，不含 DLL、凭据、真实绝对路径和存档；导入校验 schema/大小/引用，缺失内容成为未解析项，不自动下载。数据库旧版本只读或迁移，未来未知版本拒绝写入。

应用版本、数据库 schema、RPC schema、包 manifest、helper 协议和 Loader 规则版本分别管理，不共用一个版本号。字段兼容变更需测试旧记录；破坏性变更必须迁移任务与回退证据。

### PS-01-02 元数据 worker 与早期库存消费者

- `inventory.inspect` 接受 `{installation_id, files}`，files 为已登记安装 Mods/Plugins/UserLibs 下 1～8 个互不重复的 DLL 相对路径。拒绝前端根路径、绝对路径、路径穿越与未知安装；经 `DesktopContext.metadata` → `MetadataInspectionService` → `MetadataWorker`，返回统一后台任务。此早期入口只检查明确文件，返回 `coverage=explicit_files_only, complete_inventory=false`，不冒充 PS-01-03 的完整库存扫描。
- 每个文件结果保存原文件名/相对路径、SHA256、大小、解析状态、错误和 `metadata_report_id`；完整静态报告在 data root 的 `metadata/reports/<UUID>.json`，桥接只返回有界摘要。AssemblyName、AssemblyVersion、MelonInfo 显示名/原始版本及资源来源分开，绝不从 AssemblyName 创造同名文件。PS-01-06 再负责诊断名称关联消费者。
- helper 协议独立为 1。一次进程只接收一份 UTF-8 JSON 后 EOF：`{protocol_version:1,input_path:<隔离只读副本>}`；stdout 唯一对象 `{protocol_version,status,report,error}`。managed/native 退出 0；corrupt/limit_exceeded/invalid_request/io_error/sandbox_unavailable 退出 2，崩溃由父进程单列。report 包括 machine、原始 cli_flags、any_cpu、assembly_name/version、references、custom_attributes、resources；只静态读取 PE/CLI，不加载依赖或执行输入 DLL/属性。
- 父进程拒绝重解析点、硬链接、data root 重叠、文件身份漂移。每文件上限 64 MiB，复制/哈希预算 8 秒、256 KiB 分块；worker 8 秒、512 MiB Job commit 上限、1 个活动进程且禁止创建子进程；应用最多同时运行 2 个 worker。stdout 256 KiB/stderr 64 KiB。helper 请求 16 KiB、属性/引用各 256、资源 64 个，每资源 256 KiB/总 1 MiB、JSON 深度 32；不解压、不运行安装脚本。CASE-024 的 ZIP/目录导入限额仍归 PS-02-02。
- Windows LPAC 使用 ALL_APPLICATION_PACKAGES opt-out、零能力、只读隔离副本 ACL、挂起创建后绑定 Job Object。仅本轮独有 AppContainer profile 和隐藏 desktop 可临时创建，退出时清理；不修改已有 desktop、runtime ACL、系统防火墙。实际令牌核对 AppContainer/零能力，负例证明 LPAC 拒绝 ALL APPLICATION PACKAGES 哨兵、越界读写、网络及子进程逃逸。参考 [Microsoft 的 AppContainer/LPAC 启动与隔离说明](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)。
- `pythonw.exe` 等无控制台宿主通过本产品固定的隐藏 console host 启动同一 LPAC worker，避免受限 .NET 进程控制台初始化失败。host 只接收内部匿名管道协议，不向 JS 提供进程/命令接口，也不解析 Mod 字节；自身受外层 Job 管理，worker 仍受原子进程数/内存/私有 desktop/LPAC 边界约束。超时、取消、管道关闭、任一层 Job 分配失败均须清理，不能通过普通进程解析来降级。
- 产品固定传 `--require-isolation`，helper 在读请求/打开输入前检查 IPv4/IPv6 TCP socket 均因权限/受限 provider（10013/10107）拒绝创建。普通 AppContainer 在本机实际未能禁网，不能以 capability 数量代替负例。LPAC/清理/网络前检无法证明时返回 hash_only 与明确原因，metadata=null；缺 helper/runtime 同样不自动安装、不退回普通进程深解析。超过输入上限则标 limit_exceeded，不能伪造完整哈希。
- 每次结束清理本轮 UUID staging 副本和所有 Job 进程，保留报告；取消不删除已完成报告，也不把部分结果发布为完整库存。字段未知保留 null；外部 enum 类型不加载依赖，无法静态解码的属性明确 decoded=false。

### PS-00-03 存储基座

- 数据库 `user_version=1` 建立 17 个实体表及 profile_selections；版本 2 增加 profile_history，回填旧 Profile 并由触发器保存后续修订。DTO/RPC schema 仍为 1。实体完整 JSON 保存在 payload，索引列提供稳定主键、外键与唯一约束；组件角色、状态及原始版本不会折叠为一个 enabled 标记。
- 库存仓储按 DTO 保存/分页读取；Profile 更新要求 expected_revision 且严格递增一次，选择项与历史在同一事务提交。重复相同实体写入幂等，不同内容不能覆盖同一稳定身份。计划仓储只登记事实，校验完整 baseline、安装及 Profile 修订；只有 committed 事务可登记自己的 generation。本任务不实现计划计算、批准 UI 或部署执行。
- 迁移仅处理本产品已支持的 schema。首先检查身份、版本、完整性、外键和结构；高于已知版本的主文件或 WAL 数据拒绝写入。持有 SQLite writer reservation 后，使用独立只读连接创建备份，核对备份完整性、版本和 SQL 逻辑摘要，再执行事务迁移。备份元数据分别记录原文件哈希、备份字节哈希和逻辑摘要（SQLite backup 不保证逐字节等同原文件）。备份失败不升级；迁移失败回滚，并保留备份；进程中断后由 SQLite journal 恢复旧状态再重试。
- `restore_backup()` 是内部显式回退入口，调用方必须关闭所有 DB 连接，提供预期当前库和备份 SHA256；校验备份结构与完整性，保留被替换库，再替换。活跃 sidecar、损坏备份、未来版本及哈希变化拒绝回退。不删除旧数据或手改版本修复迁移。
- `Store.profiles.export()` 只构造 schema_version、profile_id、revision、selections 白名单。每项仅有 release_id、manifest_hash、selected、受限 version_raw 与 source(kind/observed_at)。不复制 config_overrides、installation、凭据、source.reference、路径或包字节；无法明确识别为数值版本及常见 alpha/beta/rc/dev/pre/local/build/preview/snapshot 后缀的版本原文导出为 null，内部原文不变。这是有意受限的清单，不是配置备份，也不执行导入或下载。

### PS-01-03 只读观察、缓存和代次

- `inventory.scan {installation_id, force?:bool}` 经同一桌面命令桥、JobManager 和 InventoryService，返回 task_id 与同步预留的 scan_generation。只读取已登记安装下 Mods/Plugins/UserLibs；UserData/Loader.cfg 和 Loader DLL 仅作策略事实读取。拒绝 data root 重叠、重解析点和硬链接。不启动游戏或加载输入程序集。
- `inventory.list {installation_id,generation?:int,limit?:1..50,offset?:int}` 返回有界分页、generation、total、next_offset、latest_complete/latest_attempt/latest_requested、last_attempt 与覆盖摘要。后续页必须带首屏 generation 以固定观察快照。默认显示最近完整库存，同时显式显示最近失败/部分扫描；无完整库存时可查询未知项。详细原始元数据留在 SQLite，桥接不返回全库报告。
- 数据库 schema 3 新增 scan_generations、scan_entries、inventory_current、inventory_tags、inventory_parser_cache，沿用已有备份及事务迁移。17 个 DTO 不改；观察行不伪造 ModRelease/Component。文件名、路径、文件身份、size/mtime/SHA256、程序集名称/版本、预测和用户标签分开保存。
- 每次请求同步递增代次；新请求形成发布屏障，旧请求只能记 superseded。只有最新请求的 complete 扫描可推进完整指针并标记 missing。partial/failed/cancelled 不删除历史观察与标签；取消记录 partial，并以任务 cancelled 结束。标签独立按安装和不区分大小写的相对路径保存，重命名产生新路径身份，旧标签仍保留。
- 缓存键为内容 SHA256 与 helper/运行配置/worker 实现指纹；命中前仍强哈希，force 跳过缓存读取。扫描结束重新枚举并强哈希，发现同长度/时间戳复原修改、替换或策略漂移则降为 partial。缓存只减少解析工作，不授予部署信任。
- 物理发现覆盖三个根的嵌套文件，包括 Loader 排除目录；0.7.3 的 manifest 存在性、大小写敏感排除名、阶段和同名程序集版本候选是独立预测。未知版本/配置、元数据不足或不完整覆盖不能给出肯定加载结论。当前配置预测不包含外部命令行覆盖，也不证明初始化成功。
- 限额：5000 文件、500 目录、16 层、120 秒、强哈希累计 1 GiB、单文件 64 MiB；触限留不完整记录，不标缺失。worker 沿用 PS-01-02 限制。最多保存 200 条扫描错误；分页响应约 48 KiB，完整覆盖和元数据受单 JSON 512 KiB 限额。

### PS-01-04 内容声明与观察关系图

- `inventory.detail {installation_id,entry_id,generation?}` 从已存库存观察读取单个条目，返回角色集合、原文版本、清单、内容ID摘要、整数API要求、diagnostics和来源；不再打开游戏文件。`inventory.contents` 同载荷增加 offset/limit（1～20），分页返回内容ID。`inventory.graph {installation_id,generation?,offset?,limit?}` 返回关系分页、节点数、coverage、dependency_coverage=unknown、plan_validation=not_run。分页沿用首个 generation；节点以库存 entry_id 关联 inventory.list/detail，不伪造已登记 Component/ModRelease。
- 解释版本为 psmm-content-relations-v1。来源包含 artifact SHA256/资源/字段或引用、观测UTC；关系附 installation_id、scan generation 和端点SHA256。confirmed 表示声明/重复ID已观察到，不表示声明可信、API满足、运行成功或计划批准。关系ID确定性生成。只读查询不改变历史观察、标签、游戏目录或包所有权。
- 精确资源名 ItemForge.items.json / CharacterForge.characters.json 支持 BundleId、RequiredRuntimeApi（非负整数）、BundleVersion、GeneratorVersion。API5/12/19与AssemblyVersion、显示版本分开。ContentId按 framework/type/value 命名空间区分 bundle/item/character。现有参考资料只提供数量摘要；当前显式支持 Items/Characters[{Id:string}]，未声称真实框架所有生成器都采用该单项结构。未知/重复/超额单项结构记partial，保留有效头部和API声明，单项ID不猜测；坏JSON或坏头部记invalid。
- 合法内容包无需 MelonInfo，Melon入口和内容角色可并存。静态 registration_status 永远 unknown；21个静态物品不是21项本次注册。仅资源前缀或文件名不赋予内容角色/整目录所有权；无引用不能得出无依赖。
- AssemblyRef输出 inferred soft_dependency，requirement_semantics=compile_reference_only，原版本不作为最低兼容版本。框架要求输出 capability_requirement 与嵌入声明来源；按运行时AssemblyName找到的只是候选，compatibility=unknown，不能把运行时1.2.3推成API19。完整静态范围未找到候选记missing（仅名称检索事实），不完整范围或未解析DLL存在时记unknown。
- 同框架、同类型、同ID形成带双方来源的静态重复关系，不证明运行时失败；跨框架同ID不冲突。遮蔽边继承已核实的 Loader规则来源，不删除原件。作者、用户、日志和启发式声明由后端有限字段接口分别表示；heuristic不能升级confirmed。生产RPC不接受前端上传规则、路径或伪造证据。实际日志关联属于PS-01-05/06，计划阻断属于PS-02-04。
- 资源JSON单份128KiB、合计256KiB、深度24、最多256内容ID、32条解析诊断；严格拒绝重复JSON键/非有限数。详情显示前16个ID与总数，完整列表另行分页。图按100行预读，每行受既有512KiB存储限额约束；累计解释最多512观察行/16MiB，最多4096边，触限明确partial，不完整图不得断言缺运行时；关系响应约40KiB。原始资源仍在PS-01-03库存元数据中，不通过详情回传全文。
