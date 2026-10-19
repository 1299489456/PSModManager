# 逐任务开发指令册

一次复制一个完整代码块。所有指令都只授权指定任务的常规本地实现；安装依赖、真实游戏写入/启动、外部AI调用及发布仍受已有具体授权范围约束。以下不会启动自动任务循环，不使用task-*系列技能。

## M0 独立基线与验证基础

### PS-00-01 固定参考基线并建立独立源码副本

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-00-01：固定参考基线并建立独立源码副本。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-00-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 无，前序门为 无。核对证据有效性，不能仅看文件存在。
目标交付：PS-00-02 使用可追溯本地副本完成独立启动。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-001 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-00-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-00-02 独立产品身份、数据根与最小桌面入口

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-00-02：独立产品身份、数据根与最小桌面入口。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-00-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-01，前序门为 无。核对证据有效性，不能仅看文件存在。
目标交付：新的桌面入口供后续工作台直接复用。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-001、CASE-002 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-00-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-00-03 核心 DTO、存储模型与迁移基座

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-00-03：核心 DTO、存储模型与迁移基座。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-00-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-02，前序门为 无。核对证据有效性，不能仅看文件存在。
目标交付：库存/Profile/计划服务共享明确 DTO 和真实 SQLite。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-003 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-00-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-00-04 桌面命令桥、进度事件与真实 WebView 验证入口

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-00-04：桌面命令桥、进度事件与真实 WebView 验证入口。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-00-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-03，前序门为 无。核对证据有效性，不能仅看文件存在。
目标交付：所有后续页面通过同一桥接消费应用服务。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-004 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-00-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-00-05 合成样本、验证 runner 与证据基座

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-00-05：合成样本、验证 runner 与证据基座。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-00-05.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-04，前序门为 无。核对证据有效性，不能仅看文件存在。
目标交付：以后每张任务卡的统一验证入口。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-005 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-00-05/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G0 阶段检查

```text
只检查 PSModManager 的 G0 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G0记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M1 只读库存与诊断

### PS-01-01 安装发现、目录校验与运行环境指纹

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-01：安装发现、目录校验与运行环境指纹。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-05，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：安装选择器为库存和启动提供真实Installation。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-006 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-02 受限元数据工作进程与 C# 解析器

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-02：受限元数据工作进程与 C# 解析器。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-00-05，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：库存扫描调用隔离helper；未知来源不安全解析明确降级。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-007、CASE-010、CASE-024 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-03 只读库存、增量缓存与扫描代次

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-03：只读库存、增量缓存与扫描代次。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-01、PS-01-02，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：库存API可返回分页扫描结果和未知项。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-008、CASE-009、CASE-014 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-04 内容框架角色、嵌入清单和依赖证据

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-04：内容框架角色、嵌入清单和依赖证据。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-03，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：详情页与兼容图消费逻辑关系和来源。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-011、CASE-012、CASE-013、CASE-014、CASE-015 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-05 多源日志 tail、会话划分与证据索引

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-05：多源日志 tail、会话划分与证据索引。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-05.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-01、PS-00-04，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：问题面板与运行验证通过证据ID读取准确日志窗口。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-016、CASE-017 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-05/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-06 确定性诊断与加载结果解释

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-06：确定性诊断与加载结果解释。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-06.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-04、PS-01-05，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：只读工作台无需AI即可解释当前环境。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-010、CASE-011、CASE-018、CASE-019、CASE-020 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-06/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-01-07 只读工作台集成与本机基线核对

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-01-07：只读工作台集成与本机基线核对。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-01-07.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-06、PS-00-04，前序门为 G0。核对证据有效性，不能仅看文件存在。
目标交付：M1可交付的只读诊断版。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-021 中本任务责任；按 synthetic、desktop-local、real-readonly 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-01-07/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G1 阶段检查

```text
只检查 PSModManager 的 G1 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G1记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M2 包库与配置编辑

### PS-02-01 不可变包库、文件归属与显式接管

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-02-01：不可变包库、文件归属与显式接管。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-02-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-01-07，前序门为 G1。核对证据有效性，不能仅看文件存在。
目标交付：导入服务和Profile使用可校验的本地发布版本。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-022 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-02-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-02-02 本地 DLL、目录与 ZIP 安全导入

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-02-02：本地 DLL、目录与 ZIP 安全导入。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-02-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-02-01、PS-01-02，前序门为 G1。核对证据有效性，不能仅看文件存在。
目标交付：导入预览页提供真实包库输入。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-023、CASE-024 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-02-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-02-03 Profile 草稿、版本锁与清单导入导出

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-02-03：Profile 草稿、版本锁与清单导入导出。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-02-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-02-01，前序门为 G1。核对证据有效性，不能仅看文件存在。
目标交付：当前配置编辑和后续planner使用同一Profile契约。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-025 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-02-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-02-04 依赖与兼容性图的可解释检查

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-02-04：依赖与兼容性图的可解释检查。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-02-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-02-03、PS-01-04、PS-01-06，前序门为 G1。核对证据有效性，不能仅看文件存在。
目标交付：检查按钮和planner可展示原因链/候选组合。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-015、CASE-026 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-02-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-02-05 包库与配置编辑工作流集成

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-02-05：包库与配置编辑工作流集成。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-02-05.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-02-02、PS-02-03、PS-02-04，前序门为 G1。核对证据有效性，不能仅看文件存在。
目标交付：M2可交付的离线库与组合编辑版。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-027 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-02-05/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G2 阶段检查

```text
只检查 PSModManager 的 G2 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G2记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M3 可恢复部署与离线MVP

### PS-03-01 部署路径保护、安装锁与进程守卫

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-01：部署路径保护、安装锁与进程守卫。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-02-05，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：所有部署/回滚/启动入口共享后端守卫。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-028 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-02 ActionPlan、差异预检与批准绑定

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-02：ActionPlan、差异预检与批准绑定。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-01、PS-02-04，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：用户和AI都走同一计划预览/批准入口。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-029 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-03 持久 journal、部署执行与启动恢复

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-03：持久 journal、部署执行与启动恢复。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-02，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：可靠executor供部署UI、回滚和AI复用。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-029、CASE-030、CASE-031、CASE-032、CASE-033 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-04 回滚、外部漂移与恢复历史

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-04：回滚、外部漂移与恢复历史。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-03，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：部署历史和恢复页显示可验证的回退操作。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-022、CASE-034 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-05 配置适配器与保真字段变更

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-05：配置适配器与保真字段变更。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-05.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-03、PS-02-03，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：配置详情可预览受支持字段并纳入普通部署。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-035 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-05/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-06 游戏启动、会话关联与分层运行验证

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-06：游戏启动、会话关联与分层运行验证。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-06.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-04、PS-03-05、PS-01-05，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：底部启动与运行历史使用分层真实结果。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-036 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-06/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-03-07 部署工作台闭环与离线 MVP 实机验收

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-03-07：部署工作台闭环与离线 MVP 实机验收。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-03-07.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-04、PS-03-05、PS-03-06，前序门为 G2。核对证据有效性，不能仅看文件存在。
目标交付：M3可使用的离线模组管理MVP。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-037、CASE-038 中本任务责任；按 synthetic、desktop-local、real-game 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-03-07/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G3 阶段检查

```text
只检查 PSModManager 的 G3 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G3记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M4 AI证据与受控计划

### PS-04-01 AI 会话基础、独立凭据与发送范围

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-04-01：AI 会话基础、独立凭据与发送范围。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-04-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-03-07，前序门为 G3。核对证据有效性，不能仅看文件存在。
目标交付：AI面板具备独立可控会话基础。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-039 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-04-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-04-02 证据工具与执行时授权

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-04-02：证据工具与执行时授权。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-04-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-04-01、PS-01-06，前序门为 G3。核对证据有效性，不能仅看文件存在。
目标交付：AI能补齐证据而无自由文件权限。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-040、CASE-041 中本任务责任；按 synthetic 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-04-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-04-03 AI 提案、批准卡片与部署计划接入

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-04-03：AI 提案、批准卡片与部署计划接入。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-04-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-04-02、PS-03-02，前序门为 G3。核对证据有效性，不能仅看文件存在。
目标交付：用户从AI建议卡片进入可审阅可回滚的部署。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-042 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-04-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-04-04 AI 预算、取消、降级和验证反馈

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-04-04：AI 预算、取消、降级和验证反馈。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-04-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-04-03，前序门为 G3。核对证据有效性，不能仅看文件存在。
目标交付：AI完整会话可靠结束并反馈可验证结果。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-043 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-04-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-04-05 AI 诊断闭环验收

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-04-05：AI 诊断闭环验收。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-04-05.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-04-04，前序门为 G3。核对证据有效性，不能仅看文件存在。
目标交付：M4可用的有证据AI诊断与计划闭环。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-044 中本任务责任；按 synthetic、desktop-local、ai-live 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-04-05/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G4 阶段检查

```text
只检查 PSModManager 的 G4 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G4记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M5 质量、打包与本地交付

### PS-05-01 性能、可用性与回归收口

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-05-01：性能、可用性与回归收口。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-05-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-04-05，前序门为 G4。核对证据有效性，不能仅看文件存在。
目标交付：发布前的响应性和可用性证据。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-045 中本任务责任；按 synthetic、desktop-local 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-05-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-05-02 Windows x64 便携包与构建清单

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-05-02：Windows x64 便携包与构建清单。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-05-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-01，前序门为 G4。核对证据有效性，不能仅看文件存在。
目标交付：PS-05-03验收的真实便携发行产物。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-046 中本任务责任；按 synthetic、release 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-05-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-05-03 干净 Windows 环境与安装态验收

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-05-03：干净 Windows 环境与安装态验收。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-05-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-02，前序门为 G4。核对证据有效性，不能仅看文件存在。
目标交付：可在目标用户环境运行的发行包证据。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-047、CASE-048 中本任务责任；按 release 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-05-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-05-04 使用文档、分发清单与首版总验收

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-05-04：使用文档、分发清单与首版总验收。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-05-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-03，前序门为 G4。核对证据有效性，不能仅看文件存在。
目标交付：M5本地可交付发行候选包与完整操作说明。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-048 中本任务责任；按 release 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-05-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### G5 阶段检查

```text
只检查 PSModManager 的 G5 阶段门，不开始下一阶段。
读取 docs/development/task-register.json、07-validation-matrix.md、08-verification-guide.md 和本阶段任务证据。核对任务依赖、当前代码/产物指纹、必要CASE、真实消费者和环境缺口；文档检查与产品验收分开。
对已具备授权和环境的缺失检查可补齐，不能越界访问真实资源或自动安装依赖。按实际结果将G5记PASS/FAIL/BLOCKED/NOT_RUN并关联证据；缺验证不得写通过。报告下一阶段是否就绪，不执行下一任务。不使用task-*系列技能。
```

## M6 延后增强

### PS-06-01 可选只读游戏内观测模块

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-06-01：可选只读游戏内观测模块。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-06-01.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-04，前序门为 G5。核对证据有效性，不能仅看文件存在。本次明确指定本延后任务，可将其从DEFERRED转入执行，其余M6不自动开始。
目标交付：增强运行证据，非首版依赖。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-049 中本任务责任；按 synthetic、real-game 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-06-01/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-06-02 可信在线源与更新提案

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-06-02：可信在线源与更新提案。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-06-02.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-04，前序门为 G5。核对证据有效性，不能仅看文件存在。本次明确指定本延后任务，可将其从DEFERRED转入执行，其余M6不自动开始。
目标交付：在线获取经过普通导入/部署闭环。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-050 中本任务责任；按 synthetic、release 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-06-02/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-06-03 存档发现与经实测的隔离实验

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-06-03：存档发现与经实测的隔离实验。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-06-03.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-04，前序门为 G5。核对证据有效性，不能仅看文件存在。本次明确指定本延后任务，可将其从DEFERRED转入执行，其余M6不自动开始。
目标交付：有证据后才能开放独立存档实验。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-051 中本任务责任；按 synthetic、real-game 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-06-03/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

### PS-06-04 独立应用更新渠道与失败恢复

```text
在 E:\WorkSpace\ForTools\PSModManager 中只执行 PS-06-04：独立应用更新渠道与失败恢复。
不使用 task-doc-generator、task-runner、task-completion-handoff 及该系列技能。
先读适用 AGENTS.md、docs/development/README.md、10-execution-guide.md、task-register.json 和 docs/development/tasks/PS-06-04.md；按卡片读取配套契约。
核对工作区现有修改；前置任务为 PS-05-04，前序门为 G5。核对证据有效性，不能仅看文件存在。本次明确指定本延后任务，可将其从DEFERRED转入执行，其余M6不自动开始。
目标交付：首版之后的受控自动更新。限定任务卡文件范围，接通实际消费者，不把Mock、类库、构建成功当完整交付。
落实卡片验收及 CASE-052 中本任务责任；按 synthetic、release 分别记录结果。产品runner不存在时使用卡片早期直接入口；缺依赖不自动安装，缺实机/联网授权时完成独立工作并明确缺口。
真实 RimCrow 源目录保持只读；默认使用合成安装和隔离data root。复用已有仍适用授权，不重复询问；未获授权不写/启动真实游戏、不碰正式存档、不提交/推送/发布。
证据写 docs/development/evidence/PS-06-04/<UTC-run>/，更新台账状态及引用，保留失败记录。报告实际改动、命令/退出码/计数、未验证范围和下一项就绪情况。本任务完成即停止，不自动继续下一项。
```

## 恢复中断任务

```text
在 PSModManager 恢复上次明确指定但未完成的任务。先读取 docs/development/task-register.json 与对应任务卡/证据，核对当前修改、失败运行及依赖。保留他人变更和旧证据；只继续该任务。若无法从台账唯一确定任务，报告候选并请求一个任务ID，同时可继续只读检查。不要重置工作区、重放已完成部署或自动开始下一项；不使用task-*系列技能。
```
