# 启动环境准备（2026-09-28）

这是当次环境准备记录，以下依赖版本和安装授权不自动延续为当前状态或新的安装授权。当前开发按 [执行约定](10-execution-guide.md) 复用已有环境，不自动安装依赖。

用户在 PS-00-01 完成后明确要求“处理启动环境”；本次据此下载并安装现有锁文件声明的依赖，不新增或升级依赖声明，不开始 PS-00-02 产品改造。原 reference-import.md 和首次证据中的“缺依赖”是当时盘点结果，本次环境证据补充其后续状态。

## 已准备

- 项目内 CPython 3.11.14：`.dev-tools/python/cpython-3.11.14-windows-x86_64-none/python.exe`；使用 `--no-bin --no-registry`，未改系统默认 Python、PATH 或注册表。
- 独立虚拟环境 `.venv/`：按 uv.lock 安装 103 个包。使用 `--frozen --no-install-project`；根工程仍是待改造的上游源码，不安装为 RimCrow 应用包。
- `frontend/node_modules/`：按 package-lock.json 安装 174 个包。`npm ci --ignore-scripts --no-audit --no-fund` 跳过生命周期脚本；esbuild、Sass、Tailwind oxide、Rollup 原生依赖经实际调用检查可用，无需补跑 postinstall。
- 沿用 Node 24.19.0、npm 11.17.0，以及已登记的 WebView2 Runtime 153.0.4234.48。未重新安装系统 WebView2。
- 使用 uv/npm 常规下载缓存；未触及源 RimCrow、真实游戏、存档或凭据。构建 Python 依赖 wheel 的正常安装步骤在日志中保留。

## 验证结果

以下进程退出码均为 0，完整命令和输出在本次证据中：

| 检查 | 结果 |
|---|---|
| `uv pip check --python .venv/Scripts/python.exe` | 103 个包兼容 |
| `uv sync --check --frozen --offline --no-install-project --no-python-downloads --python .venv/Scripts/python.exe` | 103 个包与锁一致，无需变更 |
| `.venv/Scripts/python.exe -I -B <run>/python-smoke.py` | 13 项通过：Python 3.11、11 个第三方模块加载、未导入 backend |
| `node <run>/frontend-smoke.cjs` | 8 项通过：Vue、SFC、Vite、esbuild、Sass、Tailwind/oxide、Rollup |

锁文件及依赖声明保持原字节。uv 安装日志中的旧 setuptools 文件跳过提示、跨盘硬链接失败后转普通复制提示不影响安装结果，原始日志未删除。

## 使用与边界

后续 Python 命令明确使用 `.venv/Scripts/python.exe`，无需全局激活或自动同步。项目运行时依赖 `.dev-tools/python` 中的解释器，不能只复制 `.venv` 到其他位置。

此环境可供 PS-00-02 开发使用。**尚不可安全启动旧 main.py**：旧 settings/API 的副作用、缺失的 validate_environment、独立数据根及前端品牌引用需由 PS-00-02 改造；npm prebuild 中的隐式 uv 同步也尚待该任务消除。本次未执行旧 main、npm build/prebuild、旧测试、游戏或真实桌面窗口；CASE-002 与 G0 仍 NOT_RUN。

证据：[本次摘要](evidence/PS-00-01/20260928T033001Z-environment/summary.json)。环境准备不是第二次源码导入，也不覆盖或改写首次基线证据。
