# PSModManager 第三方说明

本说明对应本地 Windows x64 便携交付材料。PSModManager 以 MIT 许可开源，[LICENSE](LICENSE) 写明 `Copyright (c) 2026 PSMM contributors`，并按 MIT 要求保留上游 `Copyright (c) 2026 RimCrow contributors` 和 MIT 原文；第三方组件仍适用各自许可。

便携包内的 `licenses/` 保存许可正文和原有 NOTICE；`licenses/distribution-materials.json` 记录组件版本、来源、材料路径、摘要及提取方式。本文中的许可路径相对便携包根，在源代码工作区中不代表这些依赖已复制到项目根。

## 当前前端

实际入口为 `frontend/index.html` → `src/app/main.js`。运行组件为 Vue 3.5.40（包括同版本 `@vue/shared`、`@vue/reactivity`、`@vue/runtime-core`、`@vue/runtime-dom`）和 lucide-vue-next 0.562.0。当前 CSS 使用本项目 `desktop.css`，未引入 Tailwind 或 tw-animate-css；旧 `package.json` 中列出的其他前端依赖不能视作本次全部分发组件。

| 组件 | 版本 | 来源与许可 | 便携包许可材料 |
|---|---|---|---|
| Vue 及上述运行模块 | 3.5.40 | [Vue](https://github.com/vuejs/core)，MIT | `licenses/frontend/vue/LICENSE`；同目录下 `vue-*/LICENSE` |
| lucide-vue-next | 0.562.0 | [Lucide](https://github.com/lucide-icons/lucide)，ISC；含 Feather 的 MIT 版权 | `licenses/frontend/lucide-vue-next/LICENSE` |

## Python、窗口和 helper

Python 组件版本来自原便携包构建清单，而非开发环境所有已安装依赖。已有供应商许可按原文件复制；缺失的独立许可已补入交付材料。PyInstaller 构建工具及其随程序带入的 bootloader 适用 `COPYING.txt` 内的例外说明。表格位于本文后半部分。

| 组件 | 版本/范围 | 来源与许可 | 便携包许可材料 |
|---|---|---|---|
| CPython | 3.11.14 | [CPython](https://github.com/python/cpython/tree/v3.11.14)，PSF 及附带声明 | `licenses/CPython/LICENSE.txt` |
| .NET helper 自包含运行时 | 10.0.11 | [dotnet/runtime](https://github.com/dotnet/runtime)，MIT 及附带第三方声明 | `licenses/dotnet/LICENSE.txt`、`licenses/dotnet/THIRD-PARTY-NOTICES.txt` |
| WebView2 SDK 绑定和 loader | 1.0.3856.49 | [Microsoft WebView2 官方包许可](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49/License) | `licenses/WebView2-SDK/LICENSE.txt` |
| domJSON（pywebview 内嵌） | 0.1.2 | [domJSON](https://github.com/azaslavsky/domJSON)，MIT | `licenses/domJSON/LICENSE.txt`、`licenses/domJSON/NOTICE.txt` |
| Native Promise Only（pywebview 内嵌） | 0.8.1 | [Native Promise Only](https://github.com/getify/native-promise-only)，MIT | `licenses/Native-Promise-Only/LICENSE.txt`、`licenses/Native-Promise-Only/NOTICE.txt` |
| Symbol-ES6（pywebview 内嵌） | 0.1.2 | [Symbol-ES6](https://github.com/rousan/symbol-es6)，MIT | `licenses/Symbol-ES6/LICENSE`、`licenses/Symbol-ES6/NOTICE.txt` |
| certifi CA 根证书数据 | certifi 2026.6.17 | [certifi](https://github.com/certifi/python-certifi)，源自 Mozilla 根证书数据，MPL-2.0 | `licenses/certifi/LICENSE`、`licenses/MPL-2.0/LICENSE.txt` |
| OpenSSL | 3.5.5 | [OpenSSL](https://github.com/openssl/openssl/tree/openssl-3.5.5)，Apache-2.0 | `licenses/OpenSSL/LICENSE.txt` |
| SQLite | 3.50.4 | [SQLite 版权说明](https://www.sqlite.org/copyright.html)，公有领域 | `licenses/SQLite/NOTICE.txt` |
| Expat | 2.7.3 | [Expat](https://github.com/libexpat/libexpat)，MIT | `licenses/Expat/COPYING` |
| zlib | 1.3.1 | [zlib](https://github.com/madler/zlib/tree/v1.3.1)，zlib 许可 | `licenses/zlib/LICENSE` |
| libffi、liblzma | 随 CPython；当前包未记录独立语义版本 | [libffi](https://github.com/libffi/libffi)、[XZ/liblzma](https://github.com/tukaani-project/xz) | `licenses/libffi/LICENSE`、`licenses/liblzma/COPYING`、`licenses/liblzma/COPYING.0BSD` |
| Microsoft VC/UCRT 与 API runtime DLL | 原 Python 包附带 | Microsoft 分发代码 | `licenses/CPython/LICENSE.txt` 内 Microsoft Distributable Code 条款 |

WebView2 **Runtime** 与 .NET Framework 4.8+ 是目标 Windows 的外部运行条件，未随本包分发；安装与使用适用微软对应条款。便携包包含的是 WebView2 SDK 绑定/loader 和自包含 .NET helper 运行时，二者不能替代这些外部条件。

Native Promise Only 原文件和官方 README 指明 MIT。作者许可页面访问返回 HTTP 403，本地材料采用 [SPDX 官方 MIT 标准正文](https://raw.githubusercontent.com/spdx/license-list-data/main/text/MIT.txt)，配合原文件和作者许可站元数据的 Kyle Simpson 版权归属；JSON 记录这一处理，不标为供应商原文件逐字节复制。其他 unversioned upstream 许可的来源也在 JSON 中明示。这里登记本次实际材料，不承诺完整法律审计或所有静态链接子组件的独立版本鉴定。

## 构建清单中的 Python 组件

| 组件 | 版本 | 类型 | 许可材料 |
|---|---|---|---|
| annotated-types | 0.7.0 | 构建清单所列随包组件 | `licenses/annotated-types/LICENSE` |
| anyio | 4.12.1 | 构建清单所列随包组件 | `licenses/anyio/LICENSE` |
| attrs | 25.4.0 | 构建清单所列随包组件 | `licenses/attrs/LICENSE` |
| backports.tarfile | 1.2.0 | 构建清单所列随包组件 | `licenses/backports.tarfile/LICENSE` |
| bottle | 0.13.4 | 构建清单所列随包组件 | `licenses/bottle/LICENSE` |
| certifi | 2026.6.17 | 构建清单所列随包组件 | `licenses/certifi/LICENSE`, `licenses/MPL-2.0/LICENSE.txt` |
| cffi | 2.0.0 | 构建清单所列随包组件 | `licenses/cffi/LICENSE` |
| click | 8.3.1 | 构建清单所列随包组件 | `licenses/click/LICENSE.txt` |
| clr_loader | 0.2.10 | 构建清单所列随包组件 | `licenses/clr_loader/LICENSE` |
| colorama | 0.4.6 | 构建清单所列随包组件 | `licenses/colorama/LICENSE.txt` |
| h11 | 0.16.0 | 构建清单所列随包组件 | `licenses/h11/LICENSE.txt` |
| httpcore | 1.0.9 | 构建清单所列随包组件 | `licenses/httpcore/LICENSE.md` |
| httpx | 0.28.1 | 构建清单所列随包组件 | `licenses/httpx/LICENSE.md` |
| idna | 3.11 | 构建清单所列随包组件 | `licenses/idna/LICENSE.md` |
| importlib_metadata | 8.7.1 | 构建清单所列随包组件 | `licenses/importlib_metadata/LICENSE` |
| jaraco.classes | 3.4.0 | 构建清单所列随包组件 | `licenses/jaraco.classes/LICENSE` |
| jaraco.context | 6.1.2 | 构建清单所列随包组件 | `licenses/jaraco.context/LICENSE` |
| jaraco.functools | 4.5.0 | 构建清单所列随包组件 | `licenses/jaraco.functools/LICENSE` |
| Jinja2 | 3.1.6 | 构建清单所列随包组件 | `licenses/Jinja2/LICENSE.txt` |
| keyring | 25.7.0 | 构建清单所列随包组件 | `licenses/keyring/LICENSE` |
| markdown-it-py | 4.0.0 | 构建清单所列随包组件 | `licenses/markdown-it-py/LICENSE`, `licenses/markdown-it-py/LICENSE.markdown-it` |
| MarkupSafe | 3.0.3 | 构建清单所列随包组件 | `licenses/MarkupSafe/LICENSE.txt` |
| mdurl | 0.1.2 | 构建清单所列随包组件 | `licenses/mdurl/LICENSE` |
| more-itertools | 11.1.0 | 构建清单所列随包组件 | `licenses/more-itertools/LICENSE` |
| packaging | 26.2 | 构建清单所列随包组件 | `licenses/packaging/LICENSE`, `licenses/packaging/LICENSE.APACHE`, `licenses/packaging/LICENSE.BSD` |
| pillow | 12.2.0 | 构建清单所列随包组件 | `licenses/pillow/LICENSE` |
| proxy_tools | 0.1.0 | 构建清单所列随包组件 | `licenses/proxy_tools/LICENSE.txt` |
| psutil | 7.2.2 | 本次修复随包组件，BSD-3-Clause | `licenses/psutil/LICENSE` |
| pycparser | 3.0 | 构建清单所列随包组件 | `licenses/pycparser/LICENSE` |
| pydantic | 2.12.5 | 构建清单所列随包组件 | `licenses/pydantic/LICENSE` |
| pydantic_core | 2.41.5 | 构建清单所列随包组件 | `licenses/pydantic_core/LICENSE` |
| Pygments | 2.19.2 | 构建清单所列随包组件 | `licenses/Pygments/LICENSE` |
| pyinstaller | 6.19.0 | 构建工具 | `licenses/pyinstaller/COPYING.txt` |
| pyinstaller-hooks-contrib | 2026.1 | 构建工具 | `licenses/pyinstaller-hooks-contrib/LICENSE` |
| pytest | 9.0.3 | 构建清单所列随包组件 | `licenses/pytest/LICENSE` |
| python-dotenv | 1.2.1 | 构建清单所列随包组件 | `licenses/python-dotenv/LICENSE` |
| pythonnet | 3.0.5 | 构建清单所列随包组件 | `licenses/pythonnet/LICENSE` |
| pywebview | 6.2.1 | 构建清单所列随包组件 | `licenses/pywebview/LICENSE` |
| pywin32 | 311 | 构建清单所列随包组件 | `licenses/pywin32/license.txt` |
| pywin32-ctypes | 0.2.3 | 构建清单所列随包组件 | `licenses/pywin32-ctypes/LICENSE.txt` |
| rich | 14.3.3 | 构建清单所列随包组件 | `licenses/rich/LICENSE` |
| setuptools | 69.5.1 | 构建清单所列随包组件 | `licenses/setuptools/LICENSE` |
| sniffio | 1.3.1 | 构建清单所列随包组件 | `licenses/sniffio/LICENSE`, `licenses/sniffio/LICENSE.APACHE2`, `licenses/sniffio/LICENSE.MIT` |
| typing_extensions | 4.15.0 | 构建清单所列随包组件 | `licenses/typing_extensions/LICENSE` |
| typing-inspection | 0.4.2 | 构建清单所列随包组件 | `licenses/typing-inspection/LICENSE` |
| zipp | 3.23.0 | 构建清单所列随包组件 | `licenses/zipp/LICENSE` |
| zstandard | 0.25.0 | 构建清单所列随包组件 | `licenses/zstandard/LICENSE` |
