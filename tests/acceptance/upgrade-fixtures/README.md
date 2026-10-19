# PS-05-03 的单份旧数据样本

复用 `tests/fixtures/contracts/valid.json` 的 `RuntimeFingerprint`，使用现有 `open_database(..., target_version=6)` 创建真实 schema 6 SQLite 文件并写入原 payload。配置使用已有产品身份、`graphite-ash` 主题和一个未知字段标记。运行时样本写在本卡 evidence 的隔离目录，不提交或携带用户数据库。

实际便携 EXE 首次启动执行 6→7 升级，核对旧 payload、配置和备份摘要；再把这份备份和配置明确复制到另一隔离数据根，由同一便携 EXE 启动并核对。没有新增数据库恢复按钮、隐式降级或多代升级矩阵。

该样本只证明选定合成旧数据和离线复制恢复。真实用户旧版数据、旧版本可执行程序及跨版本包库迁移未测试。
