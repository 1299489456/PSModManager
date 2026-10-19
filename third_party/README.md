# 第三方许可材料

`notices/` 保存当前锁定构建所需的许可正文、上游 NOTICE 及材料清单，不包含第三方可执行文件或 Unity 库。

- `notices/distribution-materials.json`：组件、版本、来源、许可路径和材料完整性记录。
- `notices/build-baseline.json`：这些材料对应的 Python、helper 和依赖版本。
- 前端许可覆盖 Vue、Lucide、Markdown-it、DOMPurify 的依赖闭包，其中也包含构建时使用的组件；不表示它们全部进入运行时 JavaScript。

打包入口会复制这些材料并核对版本。升级依赖时，应同时核对并更新对应许可正文和清单，不能只修改基线版本来跳过检查。

GitHub Release 的 IL2CPP 离线恢复包仅包含 MIT 许可的 Cpp2IL 和配套插件，由 `tools/prepare_public_toolchain.py` 依据固定来源及校验值准备。Unity 库适用 Unity 的条款，公开包不分发这些库，首次生成仍由 MelonLoader 联网获取。原有本地完整资源准备入口保持不变。详见 [第三方说明](../THIRD_PARTY_NOTICES.md) 和 [开发说明](../docs/development/README.md)。
