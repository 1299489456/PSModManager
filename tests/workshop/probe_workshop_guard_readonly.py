"""Opt-in real ledger -> production guard -> real Steam snapshot verification.

Never initializes DesktopContext, modifies the ledger, or submits Steam actions.
The same run() can be loaded by the frozen-package verifier without importing
backend modules from the checkout.
"""
import argparse
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

if __name__ == '__main__' and not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def run(data_root, installation_id, evidence_root):
    from backend.app.paths import resolve_paths, overlaps
    from backend.app.native_api import process_status, write_guard
    from backend.deployments.process_guard import DeploymentGuards
    from backend.deployments.planner import PlanService
    from backend.deployments.executor import DeploymentExecutor
    from backend.storage import Store
    from backend.workshop.service import WorkshopService
    from backend.workshop.steam_session import SteamHelper

    paths = resolve_paths(data_root)
    evidence = Path(evidence_root).resolve()
    if overlaps(paths.root, evidence):
        raise ValueError('Evidence must not overlap the real manager data')
    scratch = resolve_paths(evidence / 'scratch')
    evidence.mkdir(parents=True, exist_ok=False)
    scratch.root.mkdir()
    calls = []
    database_opens = []

    def readonly(database, **_):
        if Path(database).resolve() != paths.database.resolve():
            raise ValueError('Unexpected database')
        connection = sqlite3.connect(paths.database.as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        database_opens.append('mode=ro')
        return connection

    class SnapshotOnly:
        def __init__(self, installation):
            self.helper = SteamHelper(installation)

        def snapshot(self, ids=()):
            calls.append('snapshot')
            return self.helper.snapshot(ids)

        def begin(self, *_args, **_kwargs):
            calls.append('FORBIDDEN_MUTATION')
            raise AssertionError('Read-only probe forbids Steam actions')

        def close(self):
            self.helper.close()

    started = time.monotonic()
    result = dict(status='FAIL', scope='Real existing ledger and real Steam; snapshot only',
                  frozen=bool(getattr(sys, 'frozen', False)), installation_id=installation_id)
    service = None
    try:
        with patch('backend.storage.repositories.open_database', readonly):
            store = Store(paths.database)
            try:
                installation = store.get('Installation', installation_id).model_dump(mode='json')
                result['indexed_transactions'] = store.connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
                result['selected_transactions'] = store.connection.execute('''SELECT COUNT(*) FROM transactions t
                    JOIN action_plans p ON p.plan_id=t.plan_id WHERE p.installation_id=?''',
                    (installation_id,)).fetchone()[0]
            finally:
                store.close()
            guards = DeploymentGuards(paths)
            # Bind the very same authenticated recovery probe as DesktopContext.
            executor = DeploymentExecutor(paths, guards, PlanService(paths, guards))
            context = SimpleNamespace(deployment_guards=guards)
            result['guard_before'] = guards.status(installation_id)
            if not result['guard_before']['ready']:
                raise AssertionError('Real installation guard is not ready')
            service = WorkshopService(scratch,
                process_status=lambda value: process_status(context, value),
                write_guard=lambda value: write_guard(context, value),
                steam_factory=SnapshotOnly)
            service.status(installation, refresh=True)
            for thread in tuple(service._threads):
                thread.join(40)
            final = service.status(installation, _probe=False)
            result.update(session=final['session'], runtime=final['runtime'],
                capabilities=final['capabilities'], item_count=len(final['items']),
                subscribed_count=sum(row.get('subscribed') is True for row in final['items']),
                operation_count=len(final['operations']))
            service.close()
            service = None
            result['guard_after'] = guards.status(installation_id)
            if (final['session']['state'] != 'ready' or final['session'].get('refreshing')
                    or not final['runtime']['writable'] or not result['guard_after']['ready']
                    or not final['capabilities']['direct_subscription']['available']
                    or not final['capabilities']['direct_updates']['available']
                    or final['operations'] or calls != ['snapshot']):
                raise AssertionError('Production guard and Steam state did not become ready')
            result['status'] = 'PASS'
    except Exception as exc:
        # Do not serialize raw exception strings, native paths, or account data.
        result['error_type'] = type(exc).__name__
        result['error_code'] = getattr(exc, 'code', None)
        if isinstance(exc, ModuleNotFoundError):
            result['missing_module'] = exc.name
    finally:
        if service is not None:
            service.close()
        result.update(provider_calls=calls, database_open_modes=sorted(set(database_opens)),
                      elapsed_seconds=round(time.monotonic() - started, 3))
        (evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--installation-id', required=True)
    parser.add_argument('--evidence-root', required=True)
    args = parser.parse_args()
    result = run(args.data_root, args.installation_id, args.evidence_root)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
