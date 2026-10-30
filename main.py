"""PSModManager desktop entry point. Importing this module performs no startup."""
import argparse
import sys
import sqlite3


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Protocol arguments can contain a short-lived download credential.
        self.exit(2, 'PSModManager 启动参数无效，请从管理器或官网重新打开。\n')


def main(argv=None):
    parser = _ArgumentParser(prog='PSModManager', description='PSModManager 独立桌面', allow_abbrev=False)
    parser.add_argument('--psmm-data-root', help='独立数据目录（绝对路径）')
    parser.add_argument('--psmm-protected-root', action='append', default=[],
                        help='禁止与数据目录重叠的游戏或包来源目录，可重复指定')
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    from backend.app.composition import run_desktop
    from backend.app.storage_location import startup_paths, locator_path, relaunch
    try:
        paths = startup_paths(args.psmm_data_root, args.psmm_protected_root)
        options = {}
        if args.psmm_data_root is None:
            options['storage_locator'] = locator_path()
        result = run_desktop(str(paths.root), args.psmm_protected_root, **options)
        if isinstance(result, dict) and result.get('restart_root'):
            relaunch(result['restart_root'], args.psmm_protected_root)
            return 0
        return result
    except (OSError, ValueError, RuntimeError, ImportError, sqlite3.Error) as exc:
        message = f'PSModManager 启动受阻：{exc}'
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        else:
            # A frozen GUI has no console; missing WebView2/other prerequisites
            # must still be visible without silently installing anything.
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'PSModManager', 0x10)
        return 2


if __name__ == '__main__':
    # PyInstaller's spawn helper must dispatch before desktop argument parsing.
    # Steam UGC runs in its own process and never loads its DLL into WebView2.
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
