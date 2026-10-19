# Windows x64 onedir bundle. Only the independent desktop graph is analyzed.
import os
import importlib.util
from pathlib import Path

root = Path(SPECPATH)
spec = importlib.util.spec_from_file_location('psmm_packaging', root/'pack_pyinstaller.py')
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)
bundle_data = packaging.bundle_data

excluded = ['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi', 'pytest', 'tests',
            'settings', 'backend.settings', 'backend.managers', 'backend.api',
            'backend.ai.ai_service', 'backend.ai.ai_gateway', 'backend.ai.ai_definitions',
            'litellm', 'openai', 'numpy', 'pandas', 'scipy', 'matplotlib',
            'webview.platforms.gtk', 'webview.platforms.qt', 'webview.platforms.cocoa',
            'webview.platforms.cef', 'webview.platforms.android']
gui = Analysis([str(root/'main.py')], pathex=[str(root)],
    datas=bundle_data(root, Path(os.environ['PSMM_HELPER_OUTPUT'])), binaries=[],
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium',
                   'win32timezone'], excludes=excluded, noarchive=False)
host = Analysis([str(root/'tools/PSModManager.Metadata/console_host_main.py')], pathex=[str(root)],
    datas=[], binaries=[], hiddenimports=['win32timezone'], excludes=excluded,
    noarchive=False)
gui_pyz = PYZ(gui.pure)
host_pyz = PYZ(host.pure)
options = [('X utf8', None, 'OPTION')]
desktop = EXE(gui_pyz, gui.scripts, options, exclude_binaries=True,
    name='PSModManager', console=False, debug=False, strip=False, upx=False)
worker = EXE(host_pyz, host.scripts, options, exclude_binaries=True,
    name='PSModManager.WorkerHost', console=True, debug=False, strip=False, upx=False)
COLLECT(desktop, worker, gui.binaries, gui.datas, host.binaries, host.datas,
    name='PSModManager', strip=False, upx=False)
