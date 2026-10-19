# 部署、恢复与回滚协议

### PS-03-04 已接入的选择性回退

回退首先认证原生批准、完整 MAC journal、commit_intent、不可变 generation、基线和连续 owner/接管链，再从所选事务的 before/after 归属反转该事务自己的变化。当前代次和整扫描范围重新绑定到普通 ActionPlan；后续事务新增的其他 owner 保留。同一目标已被后续部署换成其他内容时返回 `ROLLBACK_SUPERSEDED`，先回退后续部署，不能把后续真实修改当外部漂移覆盖。

恢复原内容必须读取原事务保留的 backup，校验大小、SHA256、同卷、单链接和物理身份；预览之后身份或字节变化使批准失效。执行器仍把 before 备份和新 staging 写在同卷扫描树外，逐操作 intent/done、当前清单、SQLite 和 journal 使用 PS-03-03 的真实恢复链。新增文件只在当前仍匹配认证 after 且最后 owner 退出时移除；不写配置、UserData 或未知文件。

外部编辑、恢复目标的未知占位、原备份缺失/损坏拒绝回退，先在当前真实事务持久 `recovery_required`，并在私有 `deployments/history/` 中记录 MAC 恢复问题。可安全读取的普通文件通过共享守卫复制到 conflict backup，原文件保持原样；缺失、目录、链接、占用或未知进程使保全不可用时明确显示未保全并继续阻断。拒绝预览的 SQLite 写锁释放后才记录问题，避免把保全与拒绝计划一同回滚。

手动处理后复查会强哈希当前归属、恢复目标、原备份和冲突副本，全部匹配才持久 resolved 并重读实际部署状态；不进行自动覆盖，不删除保全副本。首次 header 写失败时，仍保留已提交事务的 DB 恢复阻断；普通启动/恢复不能据旧 committed journal 清掉它，显式复查需认证全部历史、备份及受影响目标当前预期（含 absent）才能解除。复查和执行统一 executor→history 锁顺序，避免保全与恢复互等。历史、当前代次、回退来源、owner、基线和未完成事务的来源保持保护；无自动 GC、无部分手动删除入口，手动清理只能在独立完整数据副本和明确恢复引用评估后另行安排，当前不能删除这些来源。

### PS-03-03 执行器与启动恢复实现

UI 与本地 AI 共用 `DeploymentExecutor.apply`；重验完整已批准 baseline、目标物理身份、Profile/草稿/兼容结果、库字节、原生批准及容量。持有共享安装 lease，暂存完成后在首个目标变更前再次完整预检。普通包仍限于已适配的三个根和不可变文件，配置、可执行文件、Loader 与 UserData 不开放。后续 PS-03-04 的历史回滚 UX 和 PS-03-06 的启动消费者尚未实现；本卡提供复用 executor/recovery 和真实共享 launch guard 阻断。

同卷 `data_root/deployments/transactions/<transaction UUID>/` 中保存平面 `.bin` backup、stage、displaced、conflicts、returned。所有父目录原生逐级固定，重解析点和普通路径硬链接拒绝；私有区域不进入任一 Loader/内容根。新字节 CREATE_NEW、flush、源与副本强哈希后登记 physical identity；备份全部验证后才进入 applying。planner 原预算之外另加 4 MiB 保守管理开销，写入前再次核对；journal 限 2 MiB/2048 记录，复制限 512 MiB/120 秒。共享卷的包/目标/备份需求仍合并预算。

Windows/NTFS 保留旧目标句柄时，整体替换采用两次同卷 NO_REPLACE 句柄移动：旧目标移到私有 displaced，再将已认证 stage 发布到固定目标父目录；中间可能暂时不存在目标名。每一步有持久 intent/evacuated/published/done 记录。单次 rename 是名称空间操作，整批文件、两步替换、current 清单与 SQLite 不构成一个原子提交。迟到文件/链接/目录不得被覆盖；不采用检查字符串后无条件 ReplaceFile。目录句柄 share READ|WRITE 且拒 DELETE 是原生 rename 所需；属性类原地重解析仍需内核相对路径拒绝和前后检查，不能宣称共享模式禁止所有属性变化。

journal 位于 `deployments/journals/<UUID>.jsonl`，按原生批准私有 key 的独立 HMAC 域、严格序号和前项摘要认证。header 绑定安装/data root physical identity、批准计划/操作/前后清单、目标身份与旧 current。首项临时文件 flush 后 NO_REPLACE 发布；每次 append 关闭句柄后再守卫。只有已验证链末尾的未完成半行可以先保全原始尾字节再 truncate/flush；完整坏行、错序、缺失来源或不明孤儿阻断。DB 事务丢失时仅由有效 journal 重建元数据，不能因 DB 无记录清空私有文件。

管理器在安装父目录 `.psmm-<安装 physical identity 的 SHA256>/active.json` 保存有界恢复来源标记，位于游戏及扫描树外。标记只含 installation/data identity 与 transaction UUID，不提供任意来源路径。另一 data root 看到 active 标记必须 `GUARD_RECOVERY_SOURCE_UNKNOWN` 阻断写入和 launch，不跟随其他根、争抢或代为删除。原根仅在认证该事务完成对账后删除其实际标记句柄；无法建立安全标记先拒绝游戏写入。

current 清单位于私有 `deployments/installations/<physical identity SHA256>/current.json`。目标验证后准备并 flush 清单；commit_intent 固定批准窗口内的提交时间和新清单。发布清单后同一 SQLite 事务登记 generation/current 指针/committed；journal 再记录 committed。恢复匹配新目标/清单时补齐新代次，否则仅以已认证备份恢复原代次。先对账终态元数据、恢复未完成新事务，再核对 current，不能把新事务的中间文件状态误判为旧 committed 事务损坏。未知文件不丢失；冲突原物留原位置并复制保全，进入 recovery_required。已认证 primary 对象若在竞态中变重解析/多链接，仅元数据能力将此固定对象隔离到私有区，不跟随目标、不改字节、不删除外部 alias；不明 alias 保留并阻断。

应用取消使用独立恢复预算；恢复失败明确 recovery_required。占用的原生相对 open 最多三次（20/40ms 间隔），权限/磁盘满不盲目重试部分复制或发布。committed 后故障不能变成文件未部署的假失败；清理单独记录 pending，backup/displaced 原件继续保留。本卡无备份垃圾回收。文件 flush 与进程强杀验证不等于真实电源中断、目录 namespace 或设备缓存的断电耐久性。

### PS-03-02 当前计划/批准实现与后续执行责任

PlanService 从已提交 Profile 和完整安装指纹计算计划，不消费 AI 自由路径或缓存 mtime。只枚举 Mods/Plugins/UserLibs，祖先和目标经共享原生路径守卫固定身份，全部文件（包括未知文件）重新强哈希；结束再次完整枚举和强哈希，拒绝新增、删除、重绑定或内容变化。首次计划新建 coverage=complete 的三根 BaselineSnapshot，scan_generation=0 表示独立直接观察，未冒充库存代次或已部署 generation。绑定其文件清单/hash及目标身份集合。预检不创建安装父目录；不存在父目录的写目标拒绝。

所有权来源分开核验：首次仅该安装明确 package_adoptions 回执可提供现存 owner；回执的完整 BaselineSnapshot/hash、精确文件/hash、发布 manifest 和真实库字节全部重验，库条目或相邻目录本身不授予现存目标所有权。已提交代次要求连续 sequence、正确 base_generation/committed Transaction、有效原生 Approval 来源，以及其 owned_after 与操作/前后 owner 转移一致；不接受 DTO 自称 committed 或无变化伪代次。提交后新明确接管回执按 adopted_at 纳入，旧历史回执不能重新认领已经移除又由外部恢复的文件。目标键统一 Windows 根别名、Unicode NFC 和 casefold；同目标不同 hash 阻断，同 hash 共享 owner 保留至最后一个被当前方案移除。

后续 executor 写 generation.manifest_hash 时须使用计划 preconditions 中 result_manifest：对按规范目标键排序的 owned_after 清单做既有 content_hash；每项包含 target_root_kind、relative_path、sha256、size、排序唯一 owner UUID 列表。committed_at 必须落在对应签名计划创建至到期窗口内，且不早于前代；不能通过改早提交时间重新纳入旧接管回执。每条历史计划的完整 baseline 都重算 manifest，并检查安装、coverage 和规范路径唯一性。adoption_receipts 封存批准时所有接管回执的 snapshot_id/release_id/adopted_at，包括当时文件已经缺失的回执；删除或改写旧回执拒绝，新明确接管可追加。该历史来源用于下一次计划，不证明对应磁盘动作已经正确完成；PS-03-03 必须提供真正执行、journal、当前清单和 DB 对账。现有 generation 数据缺少此可核验来源时保守拒绝，不升级旧 DTO 测试数据为已部署事实。

计划 hash 覆盖完整 DTO（除自身 plan_hash），包括稳定 UUID、操作、before/after、owner、完整 baseline、指纹、Profile 与草稿修订、依赖报告、目标身份、空间策略、备份、验证项、批准范围和10分钟有效期。同幂等键不新建计划或延长期限。预览的实时 available 不参与稳定 hash，但每次预览/批准重做容量检查。空间按物理卷合并包库额外需求（已存在字节为0）、目标 staging、增长量、旧文件备份和1MiB管理开销；本版只支持私有 data root 与目标同卷的安全暂存布局，异卷非空计划明确拒绝，不假设跨卷 rename 原子。最多128文件、4096目录项、512MiB文件字节、128KiB计划，后台每阶段120秒合作式预算；人工读原生确认的时间由计划有效期约束。

真实 UI“预览部署”使用与受控 AI 工具相同产品服务。批准前后都强校验计划，人工确认后仍必须取得 DeploymentGuards.operation(...,apply)，重验指纹、进程、恢复状态和安装 lease；批准入口只保存签发绑定的 Approval，不打开目标写句柄。PS-03-03 executor 在持有同一 operation/check/step 的写上下文中重新校验批准/完整 baseline/容量，并从当前固定句柄消费每个文件动作。PlanService.validate_approval 仅是只读前置核验，不能作为稍后写入许可。PS-03-02 的 CASE-029 只覆盖规范计划、持久批准、完整基线、拒绝及幂等；重复 apply 的单次效果、执行时批准变化拒绝、journal/恢复由 PS-03-03 的实际消费者验证，不能用计划服务或合成历史代次替代。

### PS-03-01 当前守卫实现与责任边界

生产 composition 已挂载共享 `DeploymentGuards`。Windows 本地绝对路径在规范化前拒绝 ADS、设备/UNC 命名空间、点段、短文件名别名、保留名、尾随点/空格和歧义字符；普通目标仅 Mods/Plugins/UserLibs 的相对文件。supported_config 尚无 adapter 时拒绝。目录以 GENERIC_READ、share READ|WRITE 固定，但不共享 DELETE；PS-03-03 的实际句柄 rename 需要此目录共享模式，仍由固定父链、身份/重解析复检及内核相对 rename 保护。普通文件不共享 WRITE/DELETE；executor 的删除权限必须在首次受控打开时取得。所有子级用 NtCreateFile 的 RootDirectory 真实父句柄、单段名称、OBJ_DONT_REPARSE 和 FILE_OPEN_REPARSE_POINT 打开。属性类访问仍可能原地设置 reparse，不能只靠 sharing 声称阻止；相对句柄打开及前后属性复检负责拒绝这种转换。已有目标句柄强哈希和身份与准备快照一致后才交给消费者；缺失目标用 FILE_CREATE（等价 CREATE_NEW 的拒绝覆盖语义），拒绝迟到的文件/链接。不会先释放句柄再按字符串打开写入。

安装 lease 是名称由 volume/file identity 派生的 Global Windows mutex，不依赖安装路径字符串、Installation UUID 或 data root。重复进入同线程也拒绝，多进程/不同 data root 对同一安装仍互斥；owner 绑定 PID、进程创建时间及线程。内核在 owner 退出后释放，abandoned 不代表事务已恢复；取得锁后仍须检查持久状态。不创建、删除或强抢不明锁文件。命名对象访问受限时拒绝。

operation 强哈希重验 EXE、GameAssembly、metadata 对应当前完整指纹，并持有这些文件的只读句柄。进程检查从同一已核验 EXE 句柄调用 NtQueryInformationFile / FileProcessIdsUsingFileInformation(47)，取得使用该实际文件的进程集合，再使用同一原生进程句柄读取创建时间，以 NtQueryInformationProcess / ProcessImageFileMapping(44) 比较 FILE_EXECUTE 句柄与进程映像 section。名称、旧映像路径及该路径当前文件身份不能证明已映射身份；“异名硬链接启动后删除并重建路径”由真实文件使用者查询和映像比较共同覆盖。不对集合之外的其他保护进程做名称推断；集合内的访问拒绝或未知仍拒写。

class 47 被 Microsoft 标为 reserved for system use，class 44 同样不是稳定 Win32 公共契约。当前用目标真实句柄的 GetVolumeInformationByHandleW 限定 NTFS；每次检查将管理器自身真实映像作正控制，要求 class 44 匹配且 class 47 返回本进程。原生查询只接受完整成功、对齐长度和有界 PID 集合；不支持、异常长度、超限、正控制失败、相关进程无法查询或出现新候选均返回 unknown。其他文件系统直接拒写；其他 Windows 版本尚未验收。检查仍为快照，不保证最后一次检查之后无法外部启动。

所有已取得的候选进程句柄保持到最后一次文件使用者查询结束，再统一关闭；Windows 进程对象仍有句柄引用时 PID 不会被回收，避免两个不同进程在前后列表中占用同一 PID 却被误判为相同候选。真实映像的非主程序数据映射也由 class 44 区分。测试可通过真实 API 时序钩子在第一次文件查询后启动自建进程，第二次查询发现新增候选即拒写；这不伪造 native 返回值，也不声称强制发生了 OS PID 回收。[PID 生命周期依据](https://devblogs.microsoft.com/oldnewthing/20110107-00/?p=11803)

历史全进程枚举曾因访问权限导致 172 PASS / 42 FAIL，失败证据保留。2026-10-10 用户明确授权管理员 synthetic 子进程验证，实际提权及启用 SeDebugPrivilege 后仍有保护进程拒绝完整查询；当前机制通过文件身份限定相关集合，保留相关 AccessDenied 拒写，不自动提升产品权限，也不修改机器安全策略。管理员路由的真实 DACL 拒绝负例暂时禁用测试自身 SeDebugPrivilege，结束恢复；不能把提权绕过 DACL 的成功查询冒充拒绝负例。

每个 step 进入及退出再检 lease/进程/恢复状态和链接属性；退出检查发现消费者期间外部启动时设置 session.recovery_required 并抛错，后续步骤停止，PS-03-03 executor 转入持久恢复；恢复遇到进程或冲突阻断时保持 recovery_required。OS mutex 无法阻止 Steam 等外部程序在最后检查之后启动，不能宣称已消除外部竞态或仅因抛错便已回滚。

当前恢复入口读取同一 data root 内 Transaction 及保守的 `deployments/journals` 遗留检测；任何尚无法解释的 journal 均拒绝继续，不删除。PS-03-03 需接入完整 durable journal 及跨资源对账，并处理 data root 切换后的恢复来源识别；本卡没有实际部署，因此不宣称已验证跨 data root 的历史恢复。当前合成文件消费者用于证明 guard 真实拒写，不记录 DeploymentGeneration，不替代 PS-03-03/04/06 的 executor、回滚和启动消费者验收。

Windows 原语依据：[CreateFile](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[文件身份](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle)、[mutex owner/abandoned 语义](https://learn.microsoft.com/en-us/windows/win32/sync/mutex-objects)。测试中的强制结束仅针对自建隔离进程。

相对句柄依据：[NtCreateFile](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile)、[OBJECT_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/ntdef/ns-ntdef-_object_attributes)。映像比较参考 [NtDoc 维护者的原始接口说明](https://github.com/m417z/ntdoc/blob/main/descriptions/processinfoclass.md)；它是内部接口研究资料，不能冒称 Microsoft 对 class 44 的稳定保证。[Microsoft 对 NtQueryInformationProcess 的版本限制](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntqueryinformationprocess)同样适用。

文件使用者依据：[Microsoft FILE_INFORMATION_CLASS 的 class 47 及保留说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/ne-wdm-_file_information_class)、[NtQueryInformationFile](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/nf-ntifs-ntqueryinformationfile)、[句柄所属文件系统](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationbyhandlew)。[System Informer 维护者的句柄查询实现](https://github.com/winsiderss/systeminformer/blob/master/phlib/nativefile.c)提供可实践原型，不能代替本产品实际负例或冒称公开稳定性保证。

### PS-02-05 离线组合编辑边界

接管确认只复制明确文件到管理器不可变库，配置跨栏拖动只更新现有 Profile 草稿；两者都不产生 DeploymentGeneration。库详情的所有权是 FileEntry 索引声明，不能用其共享/冲突提示替代部署执行时的 baseline 和实际磁盘检查。清单导出仅通过原生保存对话框新建明确选择的 JSON，仍拒绝安装、正式数据和受保护根；其余导入/接管/编辑只写管理器自有数据。M2 窗口不得显示“部署成功”，应用和启动继续未开放。

### PS-02-04 计划登记前的依赖与文件检查

当前配置的检查按钮和启停候选预览调用 `CompatibilityService`，只读 Profile 草稿、包库及已保存证据，不写游戏或自动修改配置。候选完整性与候选冲突分别展示，用户需通过既有配置编辑操作明确选择。

生产 `Store` 配置 library_root 后，`PlanRepository.add(ActionPlan)` 与直接 `Store.put(ActionPlan)`（含相同计划的幂等重登）均在既有事务内，使用同一 `evaluate_store` 对已提交 Profile 修订执行检查。现有 baseline、installation、generation 和版本锁校验继续生效；确定的缺失硬依赖、互斥或不同 hash 写目标冲突拒绝登记，输入无法校验也拒绝。软引用、跨指纹/未知能力及历史运行风险不一律阻断。无 library_root 的底层 DTO 存储测试入口不是产品计划入口。

报告中的 `compatibility_allows_plan` 只回答本检查是否阻断，不授予批准、部署、启动权限，不证明运行兼容。未来 planner 复用 committed 检查取得原因链和候选版本，再独立完成计划计算；本任务仅接通现有计划仓储消费者，没有实现未来 plan.create/approve/apply。真正部署仍需按以下协议在执行时重验磁盘、指纹、baseline 和批准；一次草稿检查不能替代这些步骤。

### PS-02-03 配置保存与部署分离

配置草稿、已提交 Profile 和 DeploymentGeneration 是三种状态。编辑、批量启停、撤销/重做、克隆、清单导入及当前配置切换，只修改管理器 SQLite。保存配置校验不可变包库并发布与未来 planner 相同的 Profile DTO；界面显示“待应用”，不表示游戏文件已经改变。撤销不会回滚物理文件，克隆不会创建独立存档或复制 UserData。

一个安装仍只有一套物理部署。未来计划按已提交 Profile 的 installation_id/revision/selections 取值，并重新检查 baseline、generation 和包库；草稿 revision 不能冒充 planner 的 Profile revision。当前任务不实现部署、启动、存档隔离或 PS-02-05 跨列表拖拽。


### PS-02-02 导入不改变安装状态

导入来源由本机选择器授予短期句柄，读取范围为所选本地 DLL、目录或 ZIP；不把来源选择扩大为游戏写入或根外读取。暂存使用管理器自有 UUID 目录和平面文件名，路径规范化只产生清单目标。库字节和索引提交前校验；失败只清理本次暂存，不能清理来源或游戏目录。配置、业务数据和未知归属可存为待核对包内容，不因此获得部署删除权限。

2026-09-29 用户明确允许将游戏 Mods 中的 DLL 作为只读来源导入。保护重点是写入目的地及原文件不变，不能仅因来源位于已登记/已知安装而拒绝。普通导入仍经过路径锁、链接/重解析点拒绝、受限解析和私有副本校验；不会因导入副本而登记原安装的文件接管归属。真实来源验证只覆盖会话明确授权的文件范围。

import_commit 不产生 BaselineSnapshot、generation 或 Profile 选择，也不自动启用。后续实际部署仍需 PS-03 系列计划、批准、完整安装事实与 journal。相同内容的导入记录不等于已接管现有安装；PS-02-01 的安装级 owner 必须来自该安装的显式接管回执。

### PS-02-01 接管边界

接管服务只写管理器库与 SQLite；安装前后完整三根快照必须匹配。接管登记与 package_adoptions 来源回执在一个数据库事务提交，失败或取消不登记半个 release；已完成的不可变复制可保留并经下次校验复用。此处 complete 是 Mods/Plugins/UserLibs 全覆盖，不宣称读取整个游戏或 UserData；未知文件留在快照中但不自动授予包所有权。

共享文件的 release owner 集合可追溯，版本冲突保留各自记录；实际部署需再选择 Profile 和执行计划，接管本身不解决版本冲突、不删除任何目标。没有 generation 时仍保留 null，不能凭入库成功伪造首次部署。CASE-022 的实际停用/回滚保护继续由 PS-03-04 验收。

### PS-01-06 确定性诊断不授予执行权限

离线诊断区分 confirmed_failure、delegated、potential_risk、recovered、info 与 needs_verification。confirmed_failure描述有明确日志支持的会话故障，不等于已证明当前部署受阻；日志PID/文件名和历史初始化记录无法建立可信部署绑定时，不产生硬执行阻断。没有适用当前指纹的明确依据不能扩大阻断范围。

静态同名高版本遮蔽不意味着功能等价或可删除；旧模板0.5.0不能覆盖元数据1.0.9并触发降级；候选旧组件只可作为待核查对象，不能把名称相似文件认定为唯一原因。潜在21/确认0是来源报告，不变成21个停用动作。规则仅返回解释、需复现事项和有限候选动作，后续计划仍遵循批准与事务协议；本任务不改组合、安装或系统运行时。

### PS-01-05 日志与部署关联边界

历史 Latest、归档或新发现文件不会自动关联当前部署。日志启动标记/PID只产生 unknown/inferred 会话，generation_id 保持 null；不能以“监视开始时间”作为游戏启动时间。后续运行验证应通过 evidence_id 读取不可变 SQLite 字节窗口，并独立证明启动PID、时间及部署 generation 的关系。日志轮转、替换和截断形成不同 epoch，旧证据不重定向到新文件。此任务不启动游戏、不修改安装日志、不实施部署批准或诊断阻断。

## 1. 责任与不变量

PS-01-02 的元数据检查只产生文件身份、静态声明和有界解析证据；managed、有效属性或 LPAC 解析成功均不授予安装脚本执行、部署或启动权限。hash_only/未知解析不得变成兼容性确认或可写 BaselineSnapshot。早期 inventory.inspect 不是完整扫描；CASE-024 的包解压和导入边界由 PS-02-02 继续实现。

本协议由 PS-03-01 至 PS-03-07 实现，M2 只生成草稿。UI/AI 不能绕过应用服务直接写文件。数据根、包库、staging、旧DLL备份和冲突副本必须在所有已知Loader/内容框架扫描树外；优先放在游戏根之外的同卷管理器目录，不能选择Mods/.staging或仅依赖Disabled目录名。无法确定扫描边界或无法建立安全同卷暂存位置则拒绝部署。普通包只允许批准的 Mods、Plugins、UserLibs 子路径和已适配配置字段，加载器本体、GameAssembly、存档及可执行脚本不属于普通部署能力。

每个真实安装实例最多一个 mutation lease。安装路径解析到真实文件身份；拒绝目标祖先/子目录的 reparse point、越界、ADS、绝对 ZIP 条目、保留名、尾随点/空格等歧义。只做 startswith 不合格。打开/替换前重新核验父目录和文件身份以缩小 TOCTOU 窗口，能力不足时拒绝写入。

写操作必须满足：目标安装唯一、游戏进程已退出、基线仍匹配、备份已验证、计划已批准。备份失败绝不继续覆盖。游戏由 Steam 直接启动仍可能造成竞态，检测到即停止进入恢复；不能声称锁可以约束所有外部程序。

PS-01-01 的安装选择仅登记当前只读文件事实：Installation 保持稳定 ID，重新校验后可更新当前 fingerprint_id，旧指纹记录保留。coverage=complete 不等于兼容性、BaselineSnapshot、部署批准或启动许可；partial 不得用于部署。未来计划仍须绑定并重验当前指纹，不能使用已被新指纹替代的计划。安装选择任务的本地数据库提交点也不等同本协议的游戏文件事务。

## 2. ActionPlan

后端从 Profile、安装指纹、所有权与磁盘快照计算不可变计划：

```text
plan_id / plan_hash / installation_id / profile_id + profile_revision
base_generation / baseline_snapshot_id / fingerprint_id / idempotency_key
operations[]: operation_id, kind, target_root_kind, relative_path,
              expected_before(hash | absent), desired_after(hash | absent), owner_ids
preconditions / evidence_ids / disk_estimate_by_volume
backup_scope / verification_checks / restart_required / approval_scope / expires_at
```

operation.kind 为 install_owned / replace_owned / remove_owned / edit_supported_config。不存在任意 shell、自由文件移动、递归删除命令。回滚由历史清单生成新的恢复计划，仍适用原文件前置条件与批准。

首次接管尚无generation时，base_generation为null，必须绑定完整只读BaselineSnapshot及其manifest_hash；登记接管只改变管理器所有权记录，不伪造已提交部署。首次apply前重验该快照；首次回滚恢复事务前文件状态，未知文件保持原状。已有generation时按该代次和本次磁盘快照校验，两种基线都不得跳过强哈希。

同 ID/幂等键和相同 plan_hash 的重试返回原事务状态，不重复执行；同键不同 hash 拒绝。用户修改配置、扫描发现漂移、文件哈希/指纹变化、批准到期均使旧计划失效。批准记录写入 manager DB，不接受模型自称“用户已批准”。

## 3. 状态机

```text
planned -> approved -> staging -> ready -> applying -> committed
                  \-> failed/cancelled       \-> rolling_back -> rolled_back
                                               \--------------> recovery_required
```

恢复失败也进入 recovery_required。committed 表示磁盘和清单提交完成；独立 VerificationRun.status 为 not_run/running/passed/partial/failed。不要增加一个含糊的 transaction.verified 终态掩盖“部署成功、玩法未知”。

approved 前取消无磁盘副作用；staging/ready 取消保留可审计记录、仅回收自身暂存；applying 后取消转入受控回滚，不能立刻宣称 cancelled。committed 后取消验证不撤销已部署内容，回滚必须创建新计划。

## 4. 执行序列与崩溃窗口

1. 取得安装 lease，检测未完成 journal；有未解决事务禁止新写入和管理器启动游戏。
2. 重新验证计划/批准/磁盘代次、路径身份、进程、容量和权限。空间按包库、目标卷、备份卷分别预算。
3. 对即将改动的旧文件复制备份并校验哈希；记录“不存在”的目标。备份及 journal 未持久化前不开始游戏目录变更。
4. 将新内容写到目标同卷且扫描树外的受控 staging，flush 并校验，不依赖跨卷 rename 原子性。目标路径与staging之间的最终单文件替换仍须重新核验文件/目录身份；不得在扫描树内遗留可被Loader或二级框架发现的临时DLL。
5. journal 逐操作写 intent（含 before/after/hash/路径身份）并持久化，再执行单文件替换/移除，再写 done。目标在每次动作前重新验证。
6. 所有目标校验后，写 committed manifest 到同卷临时文件；journal 记录 commit_intent 和 manifest_hash；原子替换当前清单。
7. SQLite 事务登记不可变 generation 和 current 指针，journal 标 committed；发完成事件。当前清单、DB、journal 存在短暂跨资源窗口，启动恢复必须处理。
8. 暂存/备份按保留策略清理；清理失败只记 cleanup_pending，不把成功提交伪造成失败或删除唯一恢复来源。

日志中 intent 无 done 时，对比真实字节与 before/after：匹配 before 则未应用，匹配 after 则补记录；两者都不是即外部漂移，保全并 recovery_required。不能仅按操作序号盲目重放。

commit_intent 已有时：清单/目标全部匹配目标代次，补齐 DB 和 committed；目标未完成且 before/备份可验证则恢复旧代次；出现第三方写入、清单损坏或备份缺失则停止，展示具体恢复差异。禁止用“DB 没有该事务”直接清空 staging。

进程锁带安装文件身份和当前 owner 的 PID/启动时间，不能只按 PID 判断陈旧。使用 OS 独占句柄，进程退出自动释放；不得自动删除不明锁来争抢部署。

## 5. 回滚和数据保护

回滚仅还原本事务拥有的改动；新增文件仅在当前仍为本事务 after 哈希时移除。若用户手改文件，复制保全至 conflict backup，拒绝覆盖并提供差异。共享资源最后 owner、未知文件和可变数据的保护规则与正向部署相同。

配置按实际格式注册 adapter。MelonPreferences.cfg 存在标准 TOML 不接受的中文裸表名；PSWallpaper.cfg 的语法不同。首版只开放验证过的键；未知语法提供只读展示及原文件备份，不“修复”整个配置。保留注释、未知字段、编码、BOM、行尾和进度标记。键级变更仍以整文件 before hash 防止回写竞态。

配置值/备份可能包含敏感信息，不写入普通日志/共享诊断包。Profile 克隆不自动备份或隔离存档。禁用/卸载不删除 UserData，也不自动移除配置或历史数据。

## 6. 必须证明的故障行为

每个持久化边界均注入终止，重启恢复；包括备份中断、staging 写满、intent 后/replace 后、manifest 后/DB 前、DB 后/journal 前。另测文件占用、权限不足、目标目录替换为联接、外部修改、双管理器、游戏启动竞态、回滚失败、重复 apply。

进程强杀实验只针对自建测试进程。应用级故障注入不等于真实断电耐久性验证，实际电源中断/文件系统保证另行记录；不要用单测通过宣称绝对原子性。

### PS-01-03 库存观察与部署边界

扫描代次是只读观察版本，不是部署 transaction generation，也不是已批准 Baseline。complete 仅表示本次受限库存范围内完成事实核验；静态 Loader candidate 不代表游戏实际加载或初始化成功。部署前必须重新强哈希、核对运行环境与实际文件身份，不得直接信任解析缓存、mtime/size 或历史 complete。partial/failed/superseded 不触发 missing 清理，更不删除游戏文件、历史索引或用户标签。本卡不执行部署、回滚或写真实安装。

### PS-01-04 依赖声明与计划判断边界

库存详情和观察图只输出带来源的角色、声明、候选和静态重复关系。AssemblyRef编译版本不是最低运行版本；RequiredRuntimeApi整数不是SemVer；missing仅是有边界静态搜索的结果。尚不根据观察图执行硬阻断或部署，本阶段明确 plan_validation=not_run。PS-02-04必须结合可信声明、当前指纹、能力证据和完整范围实现计划阻断，不能把推断/软依赖升级成确定故障。内容资源或BundleId不授予目录所有权，不改变文件生命周期。
