"""CASE-006: product dispatch -> jobs -> discovery -> real SQLite selection."""
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.storage import Store
from tests.unit.test_installation_layout import make_installation, manifest


def request(command, payload=None):
    return {'schema_version': 1, 'request_id': str(uuid4()), 'command': command, 'payload': payload or {}}


def wait(api, snapshot):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = api.dispatch(request('task.snapshot', {'task_id': snapshot['task_id']}))
        assert result['ok'], result
        if result['data']['state'] in ('succeeded', 'failed', 'cancelled'):
            return result['data']
        time.sleep(.01)
    raise AssertionError('Installation job timed out')


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def active(tmp_path):
    game = tmp_path / '合成安装'
    make_installation(game)
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    original = hashes(game)
    yield context, api, game
    context.close()
    assert hashes(game) == original


def discover(api):
    choose = api.dispatch(request('installation.choose', {'kind': 'manual'}))
    assert choose['ok'] and choose['data']['source_handle']
    response = api.dispatch(request('installation.discover', {'source_handles': [choose['data']['source_handle']]}))
    assert response['ok'], response
    result = wait(api, response['data'])
    assert result['state'] == 'succeeded', result
    assert len(result['data']['candidates']) == 1
    return result['data']['candidates'][0]


def test_real_consumer_select_persists_stable_identity_and_reuses(active):
    context, api, game = active
    candidate = discover(api)
    assert candidate['valid'] and candidate['fingerprint']['coverage'] == 'partial'
    response = api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))
    result = wait(api, response['data'])
    assert result['state'] == 'succeeded'
    installation = result['data']['installation']
    assert installation['canonical_root'] == str(game.resolve())
    assert installation['channel'] == 'unknown' and installation['app_id'] is None
    db = Store(context.paths.database)
    try:
        stored = db.get('Installation', installation['installation_id'])
        fingerprint = db.get('RuntimeFingerprint', stored.fingerprint_id)
        assert fingerprint.coverage == 'partial'
        assert fingerprint.exe_hash is None
    finally:
        db.close()
    reused = api.dispatch(request('installation.use', {'installation_id': installation['installation_id']}))
    result2 = wait(api, reused['data'])
    assert result2['state'] == 'succeeded' and result2['data']['installation']['installation_id'] == installation['installation_id']
    assert result2['data']['installation']['fingerprint_id'] == installation['fingerprint_id']
    listed = api.dispatch(request('installation.list'))
    assert len(listed['data']['installations']) == 1
    assert listed['data']['selected_installation_id'] == installation['installation_id']


@pytest.mark.parametrize(('command', 'payload'), [
    ('installation.discover', {'source_handles': [str(uuid4())]}),
    ('installation.select', {'candidate_id': str(uuid4())}),
    ('installation.use', {'installation_id': str(uuid4())}),
    ('installation.choose', {'kind': 'manual', 'path': r'C:\secret'}),
    ('installation.discover', {'path': r'C:\secret'}),
    ('installation.discover', {'source_handles': []}),
    ('installation.select', {'candidate_id': 'invalid'}),
])
def test_unapproved_paths_and_handles_rejected(active, command, payload):
    _, api, _ = active
    assert api.dispatch(request(command, payload))['ok'] is False


def test_cancelled_picker_never_discovers(active):
    context, api, _ = active
    context.choose_installation_folder = lambda kind: None
    assert api.dispatch(request('installation.choose', {'kind': 'manual'}))['data'] == {'source_handle': None}
    assert context.jobs._jobs == {}


def test_expired_handle_rejected(active):
    context, api, _ = active
    context.installations._lifetime = -1
    handle = api.dispatch(request('installation.choose', {'kind': 'manual'}))['data']['source_handle']
    response = api.dispatch(request('installation.discover', {'source_handles': [handle]}))
    assert response['error']['code'] == 'SOURCE_EXPIRED'


def test_game_assembly_update_does_not_block_directory_selection(tmp_path):
    game = tmp_path / 'game'
    make_installation(game)
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    try:
        candidate = discover(api)
        with (game / 'GameAssembly.dll').open('ab') as file:
            file.write(b'synthetic external update')
        changed = hashes(game)
        response = api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))
        result = wait(api, response['data'])
        assert result['data']['installation']['canonical_root'] == str(game.resolve())
        assert len(api.dispatch(request('installation.list'))['data']['installations']) == 1
        assert hashes(game) == changed
    finally:
        context.close()


def test_incomplete_candidate_cannot_be_selected(tmp_path):
    game = tmp_path / 'game'
    make_installation(game)
    (game / 'Probably Stolen.exe').unlink()
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(game)
    api = DesktopApi(context)
    context.api = api
    before = hashes(game)
    try:
        candidate = discover(api)
        assert not candidate['valid'] and candidate['issues']
        response = api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))
        assert response['error']['code'] == 'INSTALLATION_INCOMPLETE'
        assert api.dispatch(request('installation.list'))['data']['installations'] == []
        assert hashes(game) == before
    finally:
        context.close()


def test_registration_survives_service_restart_without_scanning(active):
    context, api, _ = active
    candidate = discover(api)
    result = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
    identity = result['data']['installation']['installation_id']
    from backend.app.installations import InstallationService
    reopened = InstallationService(context.paths)
    assert reopened.list()['installations'][0]['installation_id'] == identity
    assert reopened.list()['selected_installation_id'] is None  # requires explicit revalidation after restart


def test_steam_build_change_does_not_block_choice_or_write_game(tmp_path):
    import os
    import sys
    library = tmp_path / 'steam'
    game = make_installation(library / 'steamapps/common/测试安装')
    manifest(library, '4349200', game.name, '100')
    (library / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" {}', encoding='utf-8')
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    context.choose_installation_folder = lambda kind: str(library)
    api = DesktopApi(context)
    context.api = api
    enabled = [False]
    attempts = []
    protected = str(library).replace('\\', '/').casefold() + '/'
    def audit(event, args):
        if not enabled[0]: return
        if event == 'open' and isinstance(args[0], (str, bytes, Path)):
            value = str(args[0]).replace('\\', '/').casefold()
            mode, flags = args[1], args[2]
            writes = (isinstance(mode, str) and any(character in mode for character in 'wax+')) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if value.startswith(protected) and writes:
                attempts.append(event)
                raise PermissionError('Protected synthetic installation write rejected')
        if event in ('os.remove', 'os.rename', 'os.mkdir', 'os.rmdir') and any(str(arg).replace('\\', '/').casefold().startswith(protected) for arg in args if isinstance(arg, str)):
            attempts.append(event)
            raise PermissionError('Protected synthetic installation mutation rejected')
    sys.addaudithook(audit)
    try:
        before = hashes(library)
        enabled[0] = True
        handle = api.dispatch(request('installation.choose', {'kind': 'steam'}))['data']['source_handle']
        outcome = wait(api, api.dispatch(request('installation.discover', {'source_handles': [handle]}))['data'])
        candidate = outcome['data']['candidates'][0]
        assert candidate['valid'] and candidate['channel'] == 'demo' and candidate['app_id'] == '4349200'
        assert hashes(library) == before and attempts == []
        enabled[0] = False
        manifest(library, '4349200', game.name, '101')  # external updater, never product code
        changed = hashes(library)
        enabled[0] = True
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
        assert selected['data']['installation']['canonical_root'] == str(game.resolve())
        assert len(api.dispatch(request('installation.list'))['data']['installations']) == 1
        assert hashes(library) == changed and attempts == []
        (tmp_path / 'write-audit.json').write_text(json.dumps({'write_attempts': attempts, 'before': before, 'after_external_update': changed, 'after_selection': hashes(library)}), encoding='utf-8')
    finally:
        enabled[0] = False
        context.close()


def test_steam_junction_source_survives_restart(tmp_path):
    import os
    import subprocess
    game = make_installation(tmp_path / 'actual-game')
    library = tmp_path / 'steam'
    alias = library / 'steamapps/common/alias'
    alias.parent.mkdir(parents=True)
    result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(game)], capture_output=True)
    assert result.returncode == 0
    manifest(library, '4349200', 'alias', '100')
    (library / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" {}', encoding='utf-8')
    paths = resolve_paths(str(tmp_path / 'data'))
    context = DesktopContext(paths)
    context.initialize()
    context.choose_installation_folder = lambda kind: str(library)
    api = DesktopApi(context)
    context.api = api
    try:
        handle = api.dispatch(request('installation.choose', {'kind': 'steam'}))['data']['source_handle']
        candidate = wait(api, api.dispatch(request('installation.discover', {'source_handles': [handle]}))['data'])['data']['candidates'][0]
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
        identity = selected['data']['installation']['installation_id']
        context.close()
        context = DesktopContext(paths)
        context.initialize()
        api = DesktopApi(context)
        context.api = api
        reused = wait(api, api.dispatch(request('installation.use', {'installation_id': identity}))['data'])
        assert reused['state'] == 'succeeded', reused
        assert reused['data']['installation']['installation_id'] == identity
        assert reused['data']['installation']['channel'] == 'demo'
        assert reused['data']['installation']['canonical_root'] == str(game)
        assert len(api.dispatch(request('installation.list'))['data']['installations']) == 1
    finally:
        context.close()
        os.rmdir(alias)


def test_selection_does_not_read_game_binaries_or_logs(active, monkeypatch):
    context, api, game = active
    import backend.games.probably_stolen.discovery as discovery_module
    def forbidden(*args, **kwargs):
        raise AssertionError('Normal directory selection must not collect hashes or historical logs')
    monkeypatch.setattr(discovery_module, 'collect', forbidden)
    monkeypatch.setattr(discovery_module, 'history', forbidden)
    real_open = Path.open
    opened = []
    def allowed_open(path, *args, **kwargs):
        if game in path.parents:
            opened.append(path.relative_to(game).as_posix())
            assert path.name == 'Probably Stolen.exe', path
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', allowed_open)
        candidate = discover(api)
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
        assert selected['data']['installation']
        reused = wait(api, api.dispatch(request('installation.use', {'installation_id': selected['data']['installation']['installation_id']}))['data'])
        assert reused['data']['installation']
    assert opened == ['Probably Stolen.exe'] * 3


def test_automatic_steam_discovery_follows_libraries_and_selects_unique_game(tmp_path, monkeypatch):
    import backend.games.probably_stolen.discovery as discovery_module
    steam = tmp_path / 'Steam'
    library = tmp_path / 'Second Steam Library'
    game = make_installation(library / 'steamapps/common/Probably Stolen Demo')
    manifest(library, '4349200', game.name)
    (steam / 'steamapps').mkdir(parents=True)
    (steam / 'steamapps/libraryfolders.vdf').write_text('"libraryfolders" { "1" { "path" "' + str(library).replace('\\', '\\\\') + '" } }', encoding='utf-8')
    monkeypatch.setattr(discovery_module, 'automatic_steam_roots', lambda: [steam])
    original = hashes(steam), hashes(library)
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    api = DesktopApi(context)
    context.api = api
    try:
        response = api.dispatch(request('installation.auto_discover'))
        assert response['ok'] and response['task_id']
        result = wait(api, response['data'])
        assert result['state'] == 'succeeded', result
        assert len(result['data']['candidates']) == 1
        selected = result['data']['installation']
        assert selected['canonical_root'] == str(game.resolve())
        assert selected['app_id'] == '4349200'
        assert api.dispatch(request('installation.list'))['data']['selected_installation_id'] == selected['installation_id']
        assert (hashes(steam), hashes(library)) == original
    finally:
        context.close()


def test_automatic_discovery_leaves_multiple_games_for_user_choice(tmp_path, monkeypatch):
    import backend.games.probably_stolen.discovery as discovery_module
    steam = tmp_path / 'Steam'
    make_installation(steam / 'steamapps/common/Demo')
    make_installation(steam / 'steamapps/common/Retail')
    manifest(steam, '4349200', 'Demo')
    manifest(steam, '4348910', 'Retail')
    monkeypatch.setattr(discovery_module, 'automatic_steam_roots', lambda: [steam])
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    api = DesktopApi(context)
    context.api = api
    try:
        result = wait(api, api.dispatch(request('installation.auto_discover'))['data'])
        assert result['state'] == 'succeeded', result
        assert len(result['data']['candidates']) == 2
        assert result['data'].get('installation') is None
        assert api.dispatch(request('installation.list'))['data']['selected_installation_id'] is None
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': result['data']['candidates'][1]['candidate_id']}))['data'])
        assert selected['data']['installation']['channel'] == 'retail'
    finally:
        context.close()


def test_automatic_discovery_reuses_unique_saved_directory(active, monkeypatch):
    import backend.games.probably_stolen.discovery as discovery_module
    context, api, _ = active
    candidate = discover(api)
    selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
    original_id = selected['data']['installation']['installation_id']
    monkeypatch.setattr(discovery_module, 'automatic_steam_roots', lambda: [])
    context.installations._selected = None
    result = wait(api, api.dispatch(request('installation.auto_discover'))['data'])
    assert result['data']['installation']['installation_id'] == original_id


def test_automatic_root_detection_uses_registry_and_common_directory(tmp_path, monkeypatch):
    import winreg
    from backend.games.probably_stolen.discovery import automatic_steam_roots
    registry_root = tmp_path / 'Registered Steam'
    common_root = tmp_path / 'Programs/Steam'
    for root in (registry_root, common_root):
        (root / 'steamapps').mkdir(parents=True)
    class RegistryKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(winreg, 'OpenKey', lambda *args: RegistryKey())
    monkeypatch.setattr(winreg, 'QueryValueEx', lambda *args: (str(registry_root), winreg.REG_SZ))
    roots = automatic_steam_roots(environ={'ProgramFiles(x86)': str(tmp_path / 'Programs')})
    assert roots == [registry_root.resolve(), common_root.resolve()]


@pytest.mark.parametrize('kind', ['manual', 'steam'])
def test_manual_and_steam_picker_accept_same_minimal_game_directory(tmp_path, kind):
    game = tmp_path / 'Game'
    game.mkdir()
    (game / 'Probably Stolen.exe').write_bytes(b'self-authored inert selection fixture')
    context = DesktopContext(resolve_paths(str(tmp_path / 'data')))
    context.initialize()
    context.choose_installation_folder = lambda selected_kind: str(game)
    api = DesktopApi(context)
    context.api = api
    try:
        chosen = api.dispatch(request('installation.choose', {'kind': kind}))
        found = wait(api, api.dispatch(request('installation.discover', {'source_handles': [chosen['data']['source_handle']]}))['data'])
        candidate = found['data']['candidates'][0]
        assert candidate['valid'], candidate
        selected = wait(api, api.dispatch(request('installation.select', {'candidate_id': candidate['candidate_id']}))['data'])
        assert selected['data']['installation']['canonical_root'] == str(game.resolve())
        assert sorted(path.name for path in game.iterdir()) == ['Probably Stolen.exe']
    finally:
        context.close()
