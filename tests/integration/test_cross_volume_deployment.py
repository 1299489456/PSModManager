"""Real separate-volume publication and recovery; all files are synthetic."""
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from backend.deployments.journal import Journal
from backend.deployments.paths import pinned_chain
from tests.integration.harness.transaction_recovery import TransactionFixture, durable_json, tree
from tests.integration.test_mod_workspace import save, view, write
from tests.integration.test_rollback_drift import rollback


@pytest.fixture
def app(tmp_path):
    # Keep retained transaction evidence on the repository volume, independently
    # from the normal pytest/LocalAppData volume. Never use a real installation.
    evidence = Path(__file__).resolve().parents[2] / 'docs/development/evidence/20261015-cross-volume/fixtures'
    evidence.mkdir(parents=True, exist_ok=True)
    if tmp_path.drive.casefold() == evidence.drive.casefold():
        pytest.skip('Two actual local volumes are required')
    game = evidence / uuid4().hex / 'synthetic-game'
    fixture = TransactionFixture(tmp_path, game_root=game)
    with pinned_chain(fixture.data) as data, pinned_chain(game) as target:
        assert data[-1][1].split(':')[0] != target[-1][1].split(':')[0]
    durable_json(fixture.root/'volumes.json', dict(data_root=str(fixture.data), game_root=str(game)))
    yield fixture
    fixture.assert_sentinels()
    fixture.close()


def frames(app):
    with app.context.deployments._store() as store:
        return app.context.history.frames(store, app.installation)


def kill_at(app, payload, point):
    app.close()
    code = '''
import json,os,sys
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
context=DesktopContext(resolve_paths(sys.argv[1]))
context.initialize()
def fault(point, details):
    if point == sys.argv[3]:
        os._exit(73)
context.deployments.fault=fault
context.deployments.apply(json.loads(sys.argv[2]))
os._exit(74)
'''
    child = subprocess.run([sys.executable, '-c', code, str(app.data), json.dumps(payload), point],
                           capture_output=True, timeout=120)
    assert child.returncode == 73, (point, child.returncode, child.stderr.decode(errors='replace'))
    durable_json(app.root/'process-exit.json', dict(point=point, exit_code=child.returncode))


def test_cross_volume_workspace_disable_enable_uninstall_and_history_undo(app):
    contents = {'Mods/CrossVolume.dll': b'synthetic mod', 'Mods/CrossVolume/assets/asset.png': b'asset'}
    for name, content in contents.items():
        write(app, name, content)
    identity = view(app)['items'][0]['id']
    assert save(app, [])['completed']
    first = frames(app)[0]
    assert first['header']['file_storage']['kind'] == 'installation_sibling_v1'
    assert all(not (app.game/name).exists() for name in contents)
    app.reopen()
    assert save(app, [identity])['completed']
    for name, content in contents.items():
        assert (app.game/name).read_bytes() == content
    item = next(item for item in view(app)['items'] if item['id'] == identity)
    result = app.call('mod.action', dict(installation_id=app.installation, release_id=item['release_id'],
        manifest_hash=item['manifest_hash'], action='uninstall', idempotency_key=str(uuid4())))
    assert result['completed'], result
    assert all(not (app.game/name).exists() for name in contents)
    source = frames(app)[-1]['transaction'].transaction_id
    app.reopen()
    rollback(app, str(source))
    for name, content in contents.items():
        assert (app.game/name).read_bytes() == content
    assert not app.status()['recovery_required']
    assert (app.data/'library').is_dir() and (app.data/'deployments/journals').is_dir()
    assert not list((app.data/'deployments/transactions').glob('*/stage'))
    app.evidence('cross-volume-lifecycle')


@pytest.mark.parametrize('point', ['journal.header', 'backup.verified', 'stage.verified',
    'operation.evacuated', 'operation.published', 'commit.intent', 'manifest.published', 'db.committed'])
def test_cross_volume_real_process_crash_recovers_whole_generation(app, point):
    _, _, payload, before, after = app.mixed()
    kill_at(app, payload, point)
    app.reopen()
    state = app.status()
    assert not state['recovery_required'], state
    assert tree(app.game) in (before, after)
    expected = after if point in {'commit.intent', 'manifest.published', 'db.committed'} else before
    assert tree(app.game) == expected
    app.evidence('cross-volume-recovered')
    app.reopen()
    assert tree(app.game) == expected and not app.status()['recovery_required']


def test_cross_volume_replaced_private_directory_is_not_used_for_recovery(app):
    _, _, payload, before, _ = app.mixed()
    kill_at(app, payload, 'transaction.ready')
    journals = list((app.data/'deployments/journals').glob('*.jsonl'))
    assert len(journals) == 1
    journal = Journal(app.data, journals[0].stem)
    header = journal.load()[0]['data']
    path = Path(header['file_storage']['ancestors'][-1][0])
    assert path.resolve().is_relative_to(app.game.parent.resolve())
    retained = path.with_name(path.name+'-retained')
    assert retained.resolve().is_relative_to(app.game.parent.resolve())
    path.rename(retained)
    path.mkdir()
    app.reopen()
    assert app.status()['recovery_required']
    assert tree(app.game) == before
    assert not list(path.iterdir())
    app.evidence('cross-volume-replaced-area-blocked')


def test_cross_volume_external_edit_survives_recovery_conflict(app):
    _, _, payload, _, _ = app.mixed()
    kill_at(app, payload, 'operation.published')
    target = app.game/'Mods/replace.png'
    target.write_bytes(b'external-edit-must-survive')
    app.reopen()
    assert app.status()['recovery_required']
    assert target.read_bytes() == b'external-edit-must-survive'
    copies = list(app.game.parent.glob('.psmm-*/transactions/*/conflicts/*.bin'))
    assert any(path.read_bytes() == b'external-edit-must-survive' for path in copies)
    app.evidence('cross-volume-external-edit-preserved')


@pytest.mark.parametrize('limited', ['data', 'game'])
def test_cross_volume_checks_free_space_on_each_actual_volume(app, monkeypatch, limited):
    import shutil
    _, _, payload, before, _ = app.mixed()
    original = shutil.disk_usage
    drive = (app.data if limited == 'data' else app.game).drive.casefold()
    def disk_usage(path):
        value = original(path)
        return type(value)(value.total, value.used, 1) if Path(path).drive.casefold() == drive else value
    monkeypatch.setattr(shutil, 'disk_usage', disk_usage)
    result = app.context.deployments.apply(payload)
    assert result['error_code'] == 'PLAN_SPACE_INSUFFICIENT', result
    assert tree(app.game) == before
    assert not app.status()['recovery_required']
    durable_json(app.root/'space-check.json', dict(limited_volume=drive, result=result))
