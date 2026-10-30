"""Opt-in real client probe. No subscription/download/install-info calls.

Run from the repository root:
  python tests/workshop/probe_steam_readonly.py --game-root <verified game root>
"""
import argparse
import json
import multiprocessing
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.workshop.steam_session import SteamHelper
from backend.workshop import WorkshopError


def app_presence():
    """App-specific UI state only; no registry account or credential values."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam\Apps\4348910') as key:
            return {name: winreg.QueryValueEx(key, name)[0] for name in ('Running', 'Updating')}
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-root', required=True)
    args = parser.parse_args()
    helper = SteamHelper(dict(canonical_root=str(Path(args.game_root).resolve()), app_id='4348910'))
    before = app_presence()
    try:
        result = helper.snapshot()
        during = app_presence()
        helper.close()
        time.sleep(1)
        result['presence_observation'] = dict(before=before, during=during, after_shutdown=app_presence(),
                                            boundary='Steam app registry observation, not a UI screenshot')
        # The native process's stdout/stderr is suppressed before LoadLibrary;
        # JSON contains only Workshop IDs and state, never account identifiers.
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except WorkshopError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False))
        return 1
    finally:
        helper.close()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
