"""Owned temporary roots only: no registry writes, network, or real games."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import threading
import time

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.app import storage_location as storage


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value


@pytest.fixture
def context(tmp_path):
    value = DesktopContext(resolve_paths(str(tmp_path / 'source')))
    value.ai_credentials_backend = MemoryCredentials()
    value.initialize()
    value.nexus.credentials._backend = value.ai_credentials_backend
    try:
        yield value
    finally:
        value.storage.wait()
        value.close()


def request(api, command, payload=None):
    return api.dispatch(dict(schema_version=1, request_id='storage-test', command=command, payload=payload or {}))


def test_first_start_accept_and_upgrade_do_not_force_existing_users(context):
    assert context.storage.status()['first_run'] is True
    assert context.storage.accept()['first_run'] is False
    assert context.preferences.read_config()['storage_setup_complete'] is True
    old = json.loads(context.paths.config.read_text('utf-8'))
    del old['storage_setup_complete']
    context.paths.config.write_text(json.dumps(old), 'utf-8')
    upgraded = DesktopContext(context.paths)
    upgraded.initialize()
    try:
        assert upgraded.storage.status()['first_run'] is False
    finally:
        upgraded.close()


def test_explicit_root_locator_redirect_and_missing_disk(context, tmp_path):
    local = tmp_path / 'local'
    local.mkdir()
    env = {'LOCALAPPDATA': str(local)}
    # An explicit isolated root must never be replaced by a per-user preference.
    storage._write(local / storage.LOCATOR, context.paths.root)
    assert storage.startup_paths(str(tmp_path / 'explicit'), environ=env).root == tmp_path / 'explicit'
    assert storage.startup_paths(environ=env).root == context.paths.root
    storage._write(context.paths.root / storage.REDIRECT, tmp_path / 'offline-drive')
    with pytest.raises(ValueError, match='不可用'):
        storage.startup_paths(str(context.paths.root), environ=env)
    assert not (tmp_path / 'offline-drive').exists()


def test_redirect_loop_and_nonempty_target_are_preserved(context, tmp_path):
    storage._write(context.paths.root / storage.REDIRECT, context.paths.root)
    with pytest.raises(ValueError, match='循环'):
        storage.startup_paths(str(context.paths.root))
    (context.paths.root / storage.REDIRECT).unlink()
    target = tmp_path / 'personal'
    target.mkdir()
    (target / 'keep.txt').write_text('unrelated')
    api = DesktopApi(context)
    result = request(api, 'app.storage.start', {'target': str(target)})
    assert result['error']['code'] == 'STORAGE_TARGET_NOT_EMPTY'
    assert (target / 'keep.txt').read_text() == 'unrelated'
    assert not context.storage.blocked
    for bad in [str(context.paths.root), str(context.paths.root / 'nested'), 'relative', str(tmp_path)]:
        assert not request(api, 'app.storage.start', {'target': bad})['ok']


def test_complete_migration_gates_writes_keeps_original_and_reopens(context, tmp_path):
    source = context.paths.root
    for name, data in {'library/a.bin': b'mod bytes', 'logs/extra.log': b'log',
                       'downloads/offline.zip': b'cached data', 'sharing/retained.json': b'{}'}.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # A locked Chromium cache is disposable, not an excuse to lose real data.
    (source / 'webview2/cache.tmp').write_bytes(b'not copied')
    target = tmp_path / 'other-disk-data'
    locator = tmp_path / 'user' / storage.LOCATOR
    context.storage.locator = locator
    api = DesktopApi(context)
    context.api = api
    started = request(api, 'app.storage.start', {'target': str(target)})
    assert started['ok'], started
    assert request(api, 'app.theme.set', {'theme_id': 'graphite-ash'})['error']['code'] == 'STORAGE_BUSY'
    context.storage.wait()
    state = request(api, 'app.storage.status')['data']
    assert state['phase'] == 'ready', state
    assert state['total'] == state['completed']
    assert storage.startup_paths(str(source)).root == target
    assert storage._read(locator) == str(target)
    assert (source / 'library/a.bin').read_bytes() == (target / 'library/a.bin').read_bytes() == b'mod bytes'
    assert (target / 'downloads/offline.zip').read_bytes() == b'cached data'
    assert not (target / 'webview2').exists()
    selected = []
    context.request_restart = selected.append
    assert request(api, 'app.storage.restart')['data'] == {'restarting': True}
    assert selected == [str(target)]
    context.close()
    reopened = DesktopContext(resolve_paths(str(target)))
    reopened.initialize()
    try:
        assert not reopened.storage.status()['first_run']
        assert reopened.storage.status()['previous_root'] == str(source)
        assert all(Path(row['path']).is_relative_to(target) for row in reopened.storage.status()['locations'])
        reopened.preferences.set_theme('graphite-ash')
        assert json.loads((source / 'config.json').read_text('utf-8')).get('theme_id') is None
        assert reopened.store.connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not reopened.store.connection.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        reopened.close()


def test_copy_failure_never_switches_root_or_overwrites_target(context, tmp_path, monkeypatch):
    source, target = context.paths.root, tmp_path / 'copy-failed'
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    context.storage.start(str(target))
    context.storage.wait()
    assert context.storage.status()['phase'] == 'failed'
    assert storage.startup_paths(str(source)).root == source
    assert not target.exists()
    assert not list(tmp_path.glob('.psmm-migration-*'))
    assert context.paths.database.exists()
    with pytest.raises(storage.StorageError):
        context.storage.accept()
    root = []
    context.request_restart = root.append
    context.storage.restart()
    assert root == [str(source)]


def test_copy_rejects_link_and_rebind_failure_leaves_source(context, tmp_path):
    source, target = context.paths.root, tmp_path / 'invalid-history'
    context.close(keep_store=True)
    original = (source / 'config.json').read_bytes()
    def reject(*_):
        raise ValueError('injected history validation failure')
    with pytest.raises(ValueError):
        storage.copy_data(source, target, rebind=reject)
    assert not target.exists()
    assert not list(tmp_path.glob('.psmm-migration-*'))
    assert (source / 'config.json').read_bytes() == original
    linked = source / 'hardlink'
    linked.hardlink_to(source / 'config.json')
    try:
        with pytest.raises(ValueError, match='链接'):
            storage.copy_data(source, target)
    finally:
        linked.unlink()


def test_live_wal_is_backed_up_without_copying_wal_sidecars(context, tmp_path):
    # The source connection stays alive just as the real startup Store does.
    context.store.connection.execute('PRAGMA journal_mode=WAL')
    context.preferences.set_theme('win11-mica')
    context.store.connection.execute('CREATE TABLE migration_probe (value TEXT)')
    context.store.connection.execute("INSERT INTO migration_probe VALUES ('durable')")
    context.close(keep_store=True)
    target = tmp_path / 'wal-target'
    storage.copy_data(context.paths.root, target)
    with closing(sqlite3.connect(target / 'psmodmanager.sqlite3')) as db:
        assert db.execute('SELECT value FROM migration_probe').fetchone()[0] == 'durable'
    assert not (target / 'psmodmanager.sqlite3-wal').exists()


def test_restart_has_no_inherited_handles_or_shell(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(storage.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    storage.relaunch(tmp_path / 'new-root', [tmp_path / 'protected'])
    args, options = calls[0]
    assert '--psmm-data-root' in args and str(tmp_path / 'new-root') in args
    assert options['close_fds'] is True and not options.get('shell')


def test_windows_short_paths_expand_before_deployment_use(tmp_path):
    import win32api
    long = tmp_path / 'Owned Long Storage Name'
    long.mkdir()
    short = win32api.GetShortPathName(str(long))
    assert resolve_paths(short).root == long.resolve()


def test_minimal_old_root_keeps_forwarding_even_if_locator_was_not_updated(context, tmp_path):
    local = tmp_path / 'local'
    local.mkdir()
    origin = tmp_path / 'minimal-old-root'
    storage._write(origin / storage.REDIRECT, context.paths.root)
    storage._write(local / storage.LOCATOR, origin)
    assert storage.startup_paths(environ={'LOCALAPPDATA': str(local)}).root == context.paths.root


def test_only_remembered_credentials_rebind_without_plaintext(context, tmp_path):
    from backend.ai.credentials import CredentialStore
    from backend.app.paths import AppPaths
    source = context.paths.root
    backend = context.ai.credentials._backend
    ai = {'provider': 'openai_compatible', 'base_url': 'https://example.invalid/v1', 'model': 'synthetic'}
    context.preferences.update('ai', ai)
    context.ai.credentials.set(ai, 'synthetic-saved', True)
    context.nexus.credentials.set(context.nexus.config, 'synthetic-session', False)
    target = tmp_path / 'credential-target'
    assert storage.copy_saved_credentials(context, target)
    copied = CredentialStore(AppPaths(target), backend)
    assert copied.get(ai) == ('synthetic-saved', 'saved')
    assert copied.get(context.nexus.config) == (None, 'missing')
    assert context.ai.credentials.get(ai)[0] == 'synthetic-saved'
    assert b'synthetic-saved' not in (source / 'config.json').read_bytes()
    assert not target.exists()


def test_publish_collision_never_merges_or_deletes_unrelated_files(context, tmp_path):
    context.close(keep_store=True)
    target = tmp_path / 'collision'
    def collision(*_):
        target.mkdir()
        (target / 'unrelated.txt').write_bytes(b'keep')
    with pytest.raises(OSError):
        storage.copy_data(context.paths.root, target, rebind=collision)
    assert (target / 'unrelated.txt').read_bytes() == b'keep'
    assert not (context.paths.root / storage.REDIRECT).exists()
    assert not list(tmp_path.glob('.psmm-migration-*'))


def test_failed_switch_preserves_complete_copy_without_selecting_it(context, tmp_path, monkeypatch):
    def denied(*_):
        raise PermissionError('synthetic denied')
    monkeypatch.setattr(storage, '_write', denied)
    target = tmp_path / 'unselected-copy'
    context.storage.start(str(target))
    context.storage.wait()
    result = context.storage.status()
    assert result['phase'] == 'failed'
    assert '已复制数据也已保留' in result['message']
    assert target.joinpath('psmodmanager.sqlite3').exists()
    assert storage.startup_paths(str(context.paths.root)).root == context.paths.root


def test_services_must_stop_before_any_copy(context, tmp_path, monkeypatch):
    target = tmp_path / 'must-not-copy'
    def still_busy(**_):
        raise RuntimeError('synthetic live worker')
    original = context.close
    monkeypatch.setattr(context, 'close', still_busy)
    context.storage.start(str(target))
    context.storage.wait()
    assert context.storage.status()['phase'] == 'failed'
    assert '后台操作未能安全停止' in context.storage.status()['message']
    assert not target.exists()
    monkeypatch.setattr(context, 'close', original)


def test_unfinished_worker_survives_failed_quiesce_and_cannot_restart_over_it(context, tmp_path):
    entered, finish = threading.Event(), threading.Event()
    def work(_):
        entered.set()
        finish.wait(10)
        return {'done': True}
    jobs = context.jobs
    jobs.start('owned-slow-operation', work)
    assert entered.wait(2)
    try:
        context.storage.start(str(tmp_path / 'busy-target'))
        context.storage.wait()
        assert context.storage.status()['phase'] == 'failed'
        assert context.jobs is jobs and not jobs.is_idle()
        with pytest.raises(storage.StorageError) as error:
            context.storage.restart()
        assert error.value.code == 'STORAGE_OPERATIONS_RUNNING'
    finally:
        finish.set()
        deadline = time.monotonic() + 3
        while not jobs.is_idle() and time.monotonic() < deadline:
            time.sleep(.02)
    roots = []
    context.request_restart = roots.append
    context.storage.restart()
    assert roots == [str(context.paths.root)]
