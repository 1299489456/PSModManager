"""PSModManager desktop entry point. Importing this module performs no startup."""
import argparse
import sys
import sqlite3


def main(argv=None):
    parser = argparse.ArgumentParser(prog='PSModManager', description='PSModManager 独立桌面')
    parser.add_argument('--psmm-data-root', help='独立数据目录（绝对路径）')
    parser.add_argument('--psmm-protected-root', action='append', default=[],
                        help='禁止与数据目录重叠的游戏或包来源目录，可重复指定')
    args = parser.parse_args(argv)
    from backend.app.composition import run_desktop
    try:
        return run_desktop(args.psmm_data_root, args.psmm_protected_root)
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
    raise SystemExit(main())
