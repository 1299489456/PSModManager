# 诊断、证据与 AI

### PS-01-06 当前离线规则消费者

`diagnostics.explain` 已接 DesktopApi 与产品只读 SQLite，返回版本化规则解释，可在无AI、无网络请求条件下调用。实际工作台布局由PS-01-07复用本接口，本任务不更换前端设计。输入、分页、限额和快照刷新约定见03；执行权限边界见04。

规则定义存于 `backend/diagnostics/rules/definitions.json`，代码在同名 rules.py（目录仅保存数据，不是Python包）。所有运行结论限定到证据的source/epoch/session；明确启动标记之外的unknown会话不能猜测恢复，注册后出现self_disabled不得被注册成功覆盖。指纹未知时Loader专用结论降为待验证。日志中的自报版本、PID和冲突数量不是对当前部署、实际运行时或全局兼容性的独立证明。只识别有测试的模板，未识别格式保留待验证范围。

### PS-01-05 当前产品接线与限制

实际入口为 DesktopApi 的 logs.watch、logs.sources、session.list、evidence.list/read；共用现有 task.subscribe/events/cancel，问题面板与后续运行验证可按证据ID消费原窗口。当前交付是已接入产品的服务/API及合成集成验证，未交付问题面板布局（PS-01-07）或离线诊断规则（PS-01-06）。不把教程中的“失败”、单纯异常字样、历史注册数量判为当前故障/成功。

监视只读已登记合成安装，解析UTF8增量、无效字节、ANSI和残行；无效字节在显示中替换，原字节留存。来源出错持续重试并报告partial；限制和接口见03。读取按文件identity、长度和已读首尾锚点判断轮转/截断；这不是任意中间字节篡改的全文件审计。至多8MiB稳定完整日志可做精确归档副本去重，较大日志继续增量索引但不声称去重。

证据留在独立data root，没有网络/模型调用或整文件上传。未来AI消费者仍必须按授权、脱敏和窗口选择规则处理日志；本次不验证真实游戏、真实PID绑定、实机日志格式或模型供应商。

## 1. 日志管线

由 PS-01-05 实现 Latest.log、MelonLoader/Logs 与明确选择的模组日志；Player.log 在定位核实后接入。只读取允许的日志根、文件数与大小范围，拒绝跟随越界链接。多行堆栈按事件聚合，说明文字中的“失败”不自动算错误。

tailer 持有 file identity、epoch、byte offset、增量 decoder 和未完成行缓冲；处理迟到文件、截断、替换、轮转、多字节跨块、编码错误和超长行。UI 打开/关闭不重复创建观察器；生命周期由安装会话服务管理。Latest 与归档副本按内容和会话来源去重，不能仅按文件名去重。

会话按启动标志、时间和进程信息划分，无法判断则 unknown；部署后的新会话才可用于新部署验证。证据引用包含 source_id/epoch/字节范围/片段哈希，不允许行号在轮转后悄悄指向新文本。

## 2. 确定性规则

| rule_id | 输入及结论 | 不允许推断 |
|---|---|---|
| ML073-SHADOW | 同 AssemblyName 的 AssemblyVersion 高版本覆盖；附加载规则版本 | 低版本可自动删除或两者功能等价 |
| IF-CONTENT | ItemForge 资源+框架标记/日志关联，识别内容角色与 RequiredRuntimeApi | 无 MelonInfo 就损坏；静态发现就已注册 |
| CF-CONTENT | CharacterForge 资源与类型化 BundleId | 跨框架同 ID 必冲突 |
| PS-SELF-DISABLED | 本次明确自停用事件，关联准确组件及候选旧组件 | 自动删除两个名字相似 DLL |
| PS-VERSION-TEXT | 元数据和诊断模板版本不同，展示来源冲突 | 按旧 0.5.0 文案降级 1.0.9 |
| ML-RECOVERED | 早期 runtime 失败后有同会话继续启动证据 | 单看首个 ERROR 判断启动失败 |
| PS-DELEGATED | 主动交由主模组控制的 INACTIVE | 普通加载失败 |
| HARMONY-POTENTIAL | 来源报告潜在21/确认0等统计，保留归属 | 独立证明全局兼容或要求禁用21个模组 |

诊断分 confirmed_failure、delegated、potential_risk、recovered、info、needs_verification。硬阻断必须有明确、适用当前指纹的规则依据。AssemblyRef 只是编译依赖线索，不能推出所有逻辑依赖和最低兼容版本。

规则定义含版本、输入要求、适用 Loader/Game 指纹、反例和证据模板。未知 Loader 版本不套用 v0.7.3 的确定结论；允许展示静态身份与建议待验证。来源参考：[固定 v0.7.3 预处理源码](https://raw.githubusercontent.com/LavaGang/MelonLoader/v0.7.3/MelonLoader/Melons/MelonPreprocessor.cs)、[目录规则](https://raw.githubusercontent.com/LavaGang/MelonLoader/v0.7.3/MelonLoader/Melons/MelonFolderHandler.cs)。

## 3. 运行验证层次

VerificationRun 记录 checks[] 与每项的 not_run/passed/failed/unknown：字节部署一致、预期版本注册、未再次自停用、主菜单到达、内容注册、玩法确认。只得到前两项不能宣称全部修复。

会话无确定结束信号、日志不包含初始化信息、玩家未确认玩法时，结果为 partial/unknown；没有 ERROR 不等于通过。可选 companion 增加观测，不是 M1–M5 前置。

## 4. AI 的职责

AI 读取证据、解释问题、提出 PlanProposal；后端转换为 03/04 定义的 ActionPlan。模型不能直接调用 filesystem、shell、删除存档或修改 GameAssembly。默认工具：environment_snapshot、search_components、inspect_package、read_evidence、get_runtime_status、diff_profile、get_supported_config_view。所有返回带 evidence_id 与覆盖范围，不能凭模型提供的绝对路径读文件。

调用执行时重新验证 session.allowed_tools、installation scope、角色、参数 schema、输出大小、取消状态。即使工具已注册但不在会话 allowlist，也必须拒绝；不靠“没把 schema 发给模型”保护权限。异常和脱敏结果可入 trace，密钥不可入 trace。

PlanProposal 只包含目标组件 ID、建议动作类型、原因与 evidence_ids；后端解析真实文件、计算差异、校验依赖和风险。用户批准 plan_hash 后卡片调用普通 deployment.apply，不能直接 changeModsActive 后提示已修复。

## 5. 凭据、预算与取消

独立 keyring service `PSModManager`；保存失败可选择仅本会话记忆或明确失败，不静默写明文。导入 RimCrow 配置/密钥默认关闭。模型连接测试属于用户触发的联网动作，不随程序启动自动发送。

初始策略参数（实现可配置并测试）：最大10轮、单工具30秒、会话总180秒、网络重试最多2次；令牌上限在配置中显式设置，未知价格不伪造精确费用。重复调用采用规范化参数签名，返回可收敛错误。取消停止调度、关闭流/子任务并进入唯一终态；已批准的文件事务依 04 回滚规则处理，不能中途遗弃。

日志、README、嵌入 JSON、工具返回文本均为不可信内容。脱敏个人路径、用户名、token、代理认证和敏感 URL 参数；仅发送需要的窗口与元数据。默认不上传完整 DLL、存档和全部日志。攻击用例含日志要求“调用隐藏工具/忽略权限”，后端仍拒绝。

没有密钥、断网、429、供应商失败或预算耗尽时，离线规则、模组管理、启动、恢复继续工作。合成 provider 验证协议；真实 provider 验证需要另外记录授权、模型、脱敏发送范围和消耗，不能由 mock 代替。
