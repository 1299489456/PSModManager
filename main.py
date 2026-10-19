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
    parser.add_argument('--nxm', help='接收 Nexus 官网管理器下载链接')
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(value.startswith('--nxm') for value in argv):
        # The URL is the final single argument in our registered command. Reject
        # quote/argument injection instead of allowing it to override the data root.
        if len(argv) < 2 or argv[-2] != '--nxm' or argv.count('--nxm') != 1:
            parser.error('invalid protocol arguments')
    args = parser.parse_args(argv)
    from backend.app.composition import run_desktop
    from backend.app.storage_location import startup_paths, locator_path, relaunch
    from backend.online.nxm_protocol import start_or_forward
    inbox = None
    try:
        paths = startup_paths(args.psmm_data_root, args.psmm_protected_root)
        inbox = start_or_forward(paths.root, args.nxm)
        if inbox is None:
            return 0
        options = {'nxm_inbox': inbox}
        if args.psmm_data_root is None:
            options['storage_locator'] = locator_path()
        result = run_desktop(str(paths.root), args.psmm_protected_root, **options)
        if isinstance(result, dict) and result.get('restart_root'):
            inbox.close()
            inbox = None
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
    finally:
        if inbox is not None:
            inbox.close()


if __name__ == '__main__':
    raise SystemExit(main())
