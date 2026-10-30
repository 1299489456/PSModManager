"""Owned UI fixture: real package copies/public metadata, simulated Steam SDK.

Never imports or initializes steam_api64.dll. Account mutations and AI responses
are simulated; DesktopApi, WorkshopService, task events and native data are real.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FixtureSteamSDK:
    def __init__(self, ids):
        self.rows = {identity: dict(workshop_id=identity, subscribed=True, steam_installed=True,
            needs_update=identity in ('3828868413', '3828902092', '3828949943', '3829106910'),
            downloading=False, download_pending=False, disabled_locally=False,
            bytes_downloaded=None, bytes_total=None, progress=None) for identity in ids}
        self.pending = []
        self.serial = 0
        self.calls = []
        self.lock = threading.RLock()

    def ensure_online(self):
        pass

    def subscription_ids(self):
        with self.lock:
            return [key for key, row in self.rows.items() if row['subscribed']]

    def item(self, identity):
        with self.lock:
            row = deepcopy(self.rows.get(identity, dict(workshop_id=identity, subscribed=False,
                steam_installed=False, needs_update=False, downloading=False, download_pending=False,
                disabled_locally=False, bytes_downloaded=None, bytes_total=None, progress=None)))
            for action, key, _, started in self.pending:
                if action == 'update' and key == identity:
                    row.update(bytes_total=1000000, bytes_downloaded=int(min(1, (time.monotonic()-started)/3)*1000000))
                    row['progress'] = row['bytes_downloaded'] / row['bytes_total']
            return row

    def begin(self, action, identity):
        with self.lock:
            # Public cards may name an item that has never been installed here.
            # Steam accepts that subscription; the fixture must not KeyError
            # when delivering its later callback.
            if identity not in self.rows:
                self.rows[identity] = self.item(identity)
            self.serial += 1
            self.calls.append(dict(action=action, workshop_id=identity))
            self.pending.append((action, identity, self.serial, time.monotonic()))
            if action == 'update':
                self.rows[identity].update(downloading=True)
                return True
            return self.serial

    def callbacks(self):
        events = []
        with self.lock:
            for action, identity, call, started in self.pending[:]:
                if time.monotonic()-started < (3 if action == 'update' else .8):
                    continue
                if action == 'update':
                    self.rows[identity].update(needs_update=False, downloading=False, steam_installed=True,
                                               bytes_downloaded=1000000, bytes_total=1000000, progress=1.0)
                    events.append(dict(kind='download', workshop_id=identity, result=1))
                else:
                    self.rows[identity]['subscribed'] = action == 'subscribe'
                    events.append(dict(kind='call', call=call, callback=1313 if action == 'subscribe' else 1315,
                                       workshop_id=identity, result=1, io_failure=False))
                self.pending.remove((action, identity, call, started))
        return events

    def close(self):
        pass


def prepare_workshop(out):
    from tests.desktop.harness.formal_release_backend import prepare
    from backend.workshop.steam_session import EngineProvider, SessionEngine
    from tests.integration.harness.ai_provider import SyntheticAIProvider
    from tests.unit.test_ai_privacy import configured

    context, fixture = prepare(out)
    ids = [path.name for path in Path(fixture['workshop']).iterdir() if path.is_dir() and path.name.isdecimal()]
    sdk = FixtureSteamSDK(ids)
    context.workshop.steam_factory = lambda _: EngineProvider(SessionEngine(sdk))
    provider = SyntheticAIProvider(chunks=['界面验收译文：', '这里保留模组功能说明。'])
    provider.__enter__()
    configured(context, provider, key='owned-ui-fixture-not-a-real-key', remember=False)
    original_close = context.close
    before = (Path(fixture['user']) / 'SaveFile.es3').read_bytes()
    def close(**kwargs):
        try:
            return original_close(**kwargs)
        finally:
            provider.__exit__(None, None, None)
            report = dict(scope='simulated Steam account/AI; real UI, services and isolated package copies',
                steam_calls=sdk.calls, ai_requests=len(provider.requests),
                operations=[value for _, value in context.workshop._operations.values()],
                game_state_unchanged=before == (Path(fixture['user']) / 'SaveFile.es3').read_bytes())
            (out / 'fixture-actions.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    context.close = close
    context._ui_steam_fixture = sdk
    context._ui_ai_fixture = provider
    return context, fixture


if __name__ == '__main__':
    import argparse
    from tests.desktop.harness.formal_release_backend import serve
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-root', type=Path, required=True)
    args = parser.parse_args()
    context, fixture = prepare_workshop(args.evidence_root.resolve())
    serve(context, args.evidence_root.resolve(), fixture)
