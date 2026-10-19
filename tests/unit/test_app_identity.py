import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend.app import composition
from backend.app.identity import APP_NAME, DATABASE_APPLICATION_ID
from backend.app.paths import resolve_paths, REFERENCE_ROOT, KNOWN_GAME_ROOT

ROOT = Path(__file__).resolve().parents[2]


def test_import_is_inert(tmp_path):
    code = '''
import sys, os
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'socket.connect', 'socket.bind', 'subprocess.Popen'):
        raise AssertionError(event)
    if event == 'open':
        path, mode, flags = args
        assert not any(x in str(path).lower() for x in ('rimcrow', 'backend/settings', 'backend\\\\settings'))
        assert not (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)), path
sys.addaudithook(audit)
import main
from backend.app import identity, paths, composition
assert not any(m in sys.modules for m in ('backend.settings', 'backend.api', 'webview', 'keyring'))
print('inert imports verified')
'''
    p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT)], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert list(tmp_path.iterdir()) == []


def test_default_namespace_and_no_initialization(tmp_path):
    paths = resolve_paths(environ={'LOCALAPPDATA': str(tmp_path)})
    assert paths.root == tmp_path / APP_NAME
    assert paths.database.name == 'psmodmanager.sqlite3'
    assert not paths.root.exists()


@pytest.mark.parametrize('root', ['relative', 'C:\\', '//server/share/root', str(REFERENCE_ROOT), str(REFERENCE_ROOT / 'data'), str(REFERENCE_ROOT.parent), str(KNOWN_GAME_ROOT / 'data'), str(ROOT), str(ROOT / 'data')])
def test_protected_default_roots(root):
    with pytest.raises(ValueError):
        resolve_paths(root)


@pytest.mark.parametrize('position', ['same', 'child', 'parent'])
def test_registered_game_and_package_roots(tmp_path, position):
    protected = tmp_path / 'package'
    candidate = {'same': protected, 'child': protected / 'nested', 'parent': tmp_path}[position]
    with pytest.raises(ValueError):
        resolve_paths(str(candidate), [str(protected)])


def test_legacy_namespace_and_game_markers(tmp_path):
    with pytest.raises(ValueError):
        resolve_paths(str(tmp_path / 'RimCrow' / 'child'), environ={'LOCALAPPDATA': str(tmp_path)})
    game = tmp_path / 'synthetic-game'
    (game / 'MelonLoader').mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_paths(str(game / 'new-data'))
    assert not (game / 'new-data').exists()


def test_reject_linked_data_root(tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    link = tmp_path / 'link'
    # Directory junctions exercise the real Windows reparse guard without
    # requiring the symlink privilege unavailable on standard-user desktops.
    subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(link), str(other)], check=True, capture_output=True)
    with pytest.raises(ValueError):
        resolve_paths(str(link / 'data'))


def test_reject_linked_config(tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    old = tmp_path / 'old-config.json'
    old.write_text('keep')
    os.link(old, root / 'config.json')
    with pytest.raises(ValueError):
        resolve_paths(str(root))
    assert old.read_text() == 'keep'


@pytest.mark.parametrize('child', ['', 'data'])
def test_registered_protected_junction_cannot_be_bypassed(tmp_path, child):
    target = tmp_path / 'package-source'
    target.mkdir()
    alias = tmp_path / 'registered-alias'
    subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(target)], check=True, capture_output=True)
    with pytest.raises(ValueError):
        resolve_paths(str(target / child), [str(alias)])
    assert list(target.iterdir()) == []


def test_first_and_second_initialize_preserve_old_state(tmp_path):
    legacy = tmp_path / 'RimCrow'
    legacy.mkdir()
    sentinel = legacy / 'config.json'
    sentinel.write_text('old credentials placeholder')
    paths = resolve_paths(str(tmp_path / 'PSModManager'))
    for _ in range(2):
        context = composition.DesktopContext(paths)
        context.initialize()
        info = composition.DesktopApi(context).dispatch(dict(schema_version=1, request_id='test', command='app.info', payload={}))['data']
        assert info['name'] == info['keyring_service'] == 'PSModManager'
        assert info['update_source'] is None
        context.close()
    assert sentinel.read_text() == 'old credentials placeholder'
    assert paths.log.read_text().count('startup product=') == 2
    with sqlite3.connect(paths.database) as db:
        assert db.execute('PRAGMA application_id').fetchone()[0] == DATABASE_APPLICATION_ID
        from backend.storage.schema import CURRENT_SCHEMA
        assert db.execute('PRAGMA user_version').fetchone()[0] == CURRENT_SCHEMA
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='profiles'").fetchone()[0] == 1
    assert json.loads(paths.config.read_text())['product'] == 'PSModManager'


def test_foreign_config_is_not_migrated(tmp_path):
    paths = resolve_paths(str(tmp_path / 'app'))
    paths.root.mkdir()
    paths.config.write_text('{"product":"RimCrow","secret":"synthetic"}')
    before = paths.config.read_bytes()
    with pytest.raises(ValueError):
        composition.DesktopContext(paths).initialize()
    assert paths.config.read_bytes() == before
    assert not paths.database.exists()


def test_foreign_database_is_not_overwritten(tmp_path):
    paths = resolve_paths(str(tmp_path / 'app'))
    paths.root.mkdir()
    with sqlite3.connect(paths.database) as db:
        db.execute('CREATE TABLE sentinel(value TEXT)')
    before = paths.database.read_bytes()
    with pytest.raises(ValueError):
        composition.DesktopContext(paths).initialize()
    assert paths.database.read_bytes() == before


def test_missing_webview_blocks_before_writes(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.setattr(composition.importlib.util, 'find_spec', lambda _: None)
    root = tmp_path / 'data'
    assert main.main(['--psmm-data-root', str(root)]) == 2
    assert 'pywebview' in capsys.readouterr().err
    assert not root.exists()


def test_missing_runtime_blocks_before_writes(tmp_path, monkeypatch, capsys):
    import main
    def absent(*_): raise OSError('synthetic missing runtime')
    monkeypatch.setattr(composition.ctypes, 'WinDLL', absent)
    root = tmp_path / 'data'
    assert main.main(['--psmm-data-root', str(root)]) == 2
    assert 'WebView2 Runtime' in capsys.readouterr().err
    assert not root.exists()


def test_actual_entry_graph_and_build_do_not_use_old_services():
    import ast
    for p in [ROOT / 'main.py', *list((ROOT / 'backend/app').glob('*.py'))]:
        for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(('backend.settings', 'backend.api', 'backend.managers', 'backend.scanner', 'backend.migrations'))
    scripts = json.loads((ROOT / 'frontend/package.json').read_text())['scripts']
    assert not any('uv run' in v or 'npm install' in v or 'npm ci' in v for v in scripts.values())
    assert [n for n in dir(composition.DesktopApi) if not n.startswith('_')] == ['dispatch']


def test_frontend_is_local_and_self_contained():
    html = composition.desktop_html()
    assert 'connect-src &#39;none&#39;' in html or "connect-src 'none'" in html
    assert '<title>PSModManager</title>' in html
    # Upstream copyright attribution is required in the bundled license view.
    # Product identity is asserted by the exact title, not by banning that name.
    assert 'runtimeBridge' not in html


def test_native_navigation_guard_pins_document_and_cancels_remote(monkeypatch):
    from types import SimpleNamespace
    class Event:
        def __init__(self): self.handlers = []
        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self
    native = SimpleNamespace(NavigationStarting=Event(), CoreWebView2InitializationCompleted=Event())
    window = SimpleNamespace(native=SimpleNamespace(browser=SimpleNamespace(webview=native)))
    logger = SimpleNamespace(info=lambda *a: None)
    external = []
    monkeypatch.setattr(composition.webbrowser, 'open', external.append)
    composition.install_navigation_guard(window, logger)
    navigate = native.NavigationStarting.handlers[0]
    for uri, denied in [('data:text/html,trusted', False), ('data:text/html,trusted', False),
                        ('data:text/html,untrusted', True), ('file:///C:/other.html', True),
                        ('https://example.invalid/', True), ('about:blank', False)]:
        event = SimpleNamespace(Uri=uri, Cancel=False)
        navigate(None, event)
        assert event.Cancel == denied
    assert external == ['https://example.invalid/']


@pytest.mark.parametrize('message,code', [(None, 'INVALID_REQUEST'), ({}, 'INVALID_REQUEST'),
    (dict(schema_version=True, request_id='x', command='app.info', payload={}), 'INVALID_REQUEST'),
    (dict(schema_version=1, request_id='x', command='app.info', payload={'path': 'arbitrary'}), 'INVALID_REQUEST'),
    (dict(schema_version=1, request_id='x', command='game.launch', payload={}), 'INVALID_REQUEST')])
def test_dispatch_rejects_unknown_and_malformed_requests(tmp_path, message, code):
    api = composition.DesktopApi(composition.DesktopContext(resolve_paths(str(tmp_path / 'data'))))
    response = api.dispatch(message)
    assert response['ok'] is False and response['error']['code'] == code
    assert not (tmp_path / 'data').exists()
