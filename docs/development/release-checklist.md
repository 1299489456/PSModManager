# 便携包交付检查

先按[开发说明](README.md)准备本地依赖和离线资源，再构建完整便携包。

## 构建

```powershell
npm --prefix frontend run build
.venv\Scripts\python.exe pack_pyinstaller.py --output-root .preview\release-01
```

输出目录必须不存在。构建不会自动安装依赖或下载离线资源。打包入口直接包含中文说明、用户文档、许可正文、文件清单和 ZIP，无需旧的本机交付脚本。

## 检查实际产物

```powershell
$env:PSMM_PORTABLE_ROOT = (Resolve-Path .preview\release-01\dist\PSModManager).Path
$env:PSMM_PORTABLE_ZIP = (Resolve-Path .preview\release-01\PSModManager-v0.1.0-windows-x64.zip).Path
$env:PSMM_FINAL_DELIVERY = '1'
.venv\Scripts\python.exe -m pytest tests\integration\test_package_layout.py -q
```

- 确认构建使用当前前端与图标，包内没有凭据、用户数据库、日志、游戏和 Mod 文件。
- 核对第三方许可材料及版本；升级依赖后同步 `third_party/notices/`。
- 用独立测试目录启动最终 EXE，检查首启、窗口和受影响操作；不使用真实存档代替测试夹具。
- 分别记录包体校验、实际界面操作、真实游戏及干净机器检查。没有执行的项目写明未测。
- 未签名 EXE 可能收到系统安全提示；不要以“包检查通过”宣称已完成发布签名或所有 Mod 兼容验证。

## 自动公开发布

对外分发使用 `pack_pyinstaller.py --public-release`，搭配 `tools/prepare_public_toolchain.py` 生成的独立资源目录；公开包仅内置 Cpp2IL 和插件，Unity 首次生成依赖由 MelonLoader 下载。不要上传上述本地完整包，也不要把本地工具包、用户数据或个人 API Key 提交到 Git。

- 同步 `pyproject.toml`、`backend/app/identity.py`、`uv.lock` 的项目版本和用户版本说明。
- 推送 `main`，确认 `Windows portable` 的干净 runner 构建与包体测试成功。
- 推送相同版本的 `vMAJOR.MINOR.PATCH` 标签，工作流自动构建并创建带 ZIP、`SHA256SUMS.txt` 的 Release。
- 上传失败会保留未发布草稿供检查，不覆盖已有 Release；已公开版本应通过新版本修复。
- Nexus 应用注册、个人 Key 使用范围和未签名 EXE 限制仍适用；发布包不会改变第三方服务权限。

完整命令、运行环境和重试方式见[自动发布开发说明](README.md#8-自动构建并发布-github-release)。

本地 `docs/development/current-local-delivery.json`、`docs/development/evidence/` 和 `.preview/` 仅保存当前机器的产物及验证记录，不纳入公开源码。旧版交付报告保留在本地和既有 Git 历史中。

[已知限制](known-limitations.md) · [第三方说明](../../THIRD_PARTY_NOTICES.md) · [用户指南](../user/quick-start.md)
