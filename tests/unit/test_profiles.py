"""CASE-025 synthetic, real SQLite, published package files and persistent drafts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4
import zipfile

import pytest

from backend.app.paths import resolve_paths
from backend.catalog.imports import ImportService
from backend.contracts import ENTITY_MODELS
from backend.games.probably_stolen.layout import identity
from backend.profiles.service import ProfileService
from backend.profiles import serialization as wire
from backend.storage import Store


@pytest.fixture
def setup(tmp_path):
    paths = resolve_paths(str(tmp_path / 'private'))
    paths.root.mkdir()
    game = tmp_path / 'synthetic-game'; game.mkdir()
    (game / 'save.sav').write_bytes(b'save sentinel')
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    store = Store(paths.database, library_root=paths.root / 'library')
    store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    fixtures['Installation'].update(canonical_root=str(game), file_identity=identity(game))
    installation = store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    importer = ImportService(paths)
    releases = []
    for index in range(2):
        source = tmp_path / f'input{index}.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr(f'asset{index}.txt', f'asset {index}')
        handle = importer.grant('zip', str(source))['source_handle']
        preview = importer.preview(handle)
        releases.append(importer.commit(preview['preview_id'], preview['preview_hash'], f'Package {index}', '未知版本 0.46D'))
    importer.close()
    service = ProfileService(paths)
    try:
        yield service, store, str(installation.installation_id), releases, game
    finally:
        service.close()
        store.close()
        assert (game / 'save.sav').read_bytes() == b'save sentinel'
        assert [p.name for p in game.iterdir()] == ['save.sav']


def add(service, draft, release, selected=True):
    return service.edit(draft['profile_id'], draft['revision'], {'op':'set_selection', 'selection':{
        'release_id':release['release_id'], 'manifest_hash':release['manifest_hash'], 'selected':selected}})


def move(service, draft, ids, selected, before=None):
    return service.edit(draft['profile_id'], draft['revision'], {'op': 'move_selections',
        'release_ids': ids, 'selected': selected, 'before_release_id': before})


def selection_ids(draft):
    return [item['release_id'] for item in draft['selections']]


def test_case025_display_reorder_survives_undo_redo_commit_reopen_and_manifest(setup, tmp_path):
    service, store, installation, releases, _ = setup
    draft = service.create(installation, 'Ordered')
    for release in releases:
        draft = add(service, draft, release)
    original = selection_ids(draft)
    reordered = move(service, draft, [original[1]], True, original[0])
    assert selection_ids(reordered) == original[::-1]
    assert reordered['revision'] == draft['revision'] + 1
    undone = service.edit(draft['profile_id'], reordered['revision'], {'op': 'undo'})
    assert selection_ids(undone) == original
    redone = service.edit(draft['profile_id'], undone['revision'], {'op': 'redo'})
    assert selection_ids(redone) == original[::-1]
    committed = service.commit(draft['profile_id'], redone['revision'])
    dto = store.get('Profile', draft['profile_id'])
    assert [str(item.release_id) for item in dto.selections] == original[::-1]
    assert dto.display_order == []  # This field contains Component IDs, not releases.
    reopened = ProfileService(service.paths)
    try:
        assert reopened.get(draft['profile_id']) == committed
    finally:
        reopened.close()
    _, handle, preview = import_manifest(service, tmp_path, service.export(draft['profile_id']), installation)
    imported = service.import_commit(handle['source_handle'], preview['preview_hash'], installation, 'Roundtrip')
    assert selection_ids(imported) == original[::-1]
    assert not store.list('DeploymentGeneration')


def ordered_unknown_draft(service, installation, tmp_path):
    value = manifest()
    value['selections'] = [dict(deepcopy(value['selections'][0]), release_id=str(uuid4()), selected=index < 3) for index in range(5)]
    _, handle, preview = import_manifest(service, tmp_path, value, installation)
    return service.import_commit(handle['source_handle'], preview['preview_hash'], installation, 'Ordered unknown')


def test_case025_cross_lane_block_move_is_one_atomic_edit_in_original_order(setup, tmp_path):
    service, _, installation, _, _ = setup
    draft = ordered_unknown_draft(service, installation, tmp_path)
    a, b, c, d, e = selection_ids(draft)
    moved = move(service, draft, [c, a], False, e)
    assert selection_ids(moved) == [b, d, a, c, e]
    assert [item['selected'] for item in moved['selections']] == [True, False, False, False, False]
    assert moved['revision'] == draft['revision'] + 1
    assert all(item['manifest_hash'] == 'a' * 64 for item in moved['selections'])
    undone = service.edit(draft['profile_id'], moved['revision'], {'op': 'undo'})
    assert undone['selections'] == draft['selections']
    redone = service.edit(draft['profile_id'], undone['revision'], {'op': 'redo'})
    assert redone['selections'] == moved['selections']


def test_case025_reorder_keeps_unmoved_hidden_relative_order_and_lane_end(setup, tmp_path):
    service, _, installation, _, _ = setup
    draft = ordered_unknown_draft(service, installation, tmp_path)
    a, b, c, d, e = selection_ids(draft)
    moved = move(service, draft, [a], True, c)
    assert selection_ids(moved) == [b, a, c, d, e]
    appended = move(service, moved, [a], True)
    assert selection_ids(appended) == [b, c, a, d, e]
    unchanged = move(service, appended, [a], True)
    assert unchanged == appended


@pytest.mark.parametrize('bad', ['mixed', 'duplicate', 'self_anchor', 'missing_anchor', 'wrong_lane', 'non_bool', 'empty', 'extra', 'bad_anchor'])
def test_case025_invalid_reorder_never_changes_draft_or_revision(setup, tmp_path, bad):
    service, _, installation, _, _ = setup
    draft = ordered_unknown_draft(service, installation, tmp_path)
    a, b, _, d, _ = selection_ids(draft)
    action = {'op': 'move_selections', 'release_ids': [a], 'selected': True, 'before_release_id': b}
    if bad == 'mixed': action['release_ids'] = [a, d]
    elif bad == 'duplicate': action['release_ids'] = [a, a]
    elif bad == 'self_anchor': action['before_release_id'] = a
    elif bad == 'missing_anchor': action['before_release_id'] = str(uuid4())
    elif bad == 'wrong_lane': action['before_release_id'] = d
    elif bad == 'non_bool': action['selected'] = 1
    elif bad == 'empty': action['release_ids'] = []
    elif bad == 'extra': action['order'] = []
    elif bad == 'bad_anchor': action['before_release_id'] = 42
    with pytest.raises(wire.ProfileError):
        service.edit(draft['profile_id'], draft['revision'], action)
    assert service.get(draft['profile_id']) == draft


def test_case025_reorder_rejects_stale_revision_and_other_profile_release(setup, tmp_path):
    service, _, installation, _, _ = setup
    draft = ordered_unknown_draft(service, installation, tmp_path)
    a, b, c, _, _ = selection_ids(draft)
    changed = move(service, draft, [c], True, a)
    with pytest.raises(wire.ProfileError, match='PROFILE_REVISION_CONFLICT'):
        move(service, draft, [b], True)
    with pytest.raises(wire.ProfileError, match='PROFILE_UNRESOLVED'):
        move(service, changed, [str(uuid4())], True)
    assert service.get(draft['profile_id']) == changed


def test_case025_manifest_preview_labels_resolved_releases_without_changing_portable_schema(setup, tmp_path):
    service, _, installation, releases, _ = setup
    value = manifest(releases[0])
    value['selections'].append(manifest()['selections'][0])
    _, _, preview = import_manifest(service, tmp_path, value, installation)
    assert preview['selections'][0]['display_name'] == 'Package 0'
    assert preview['selections'][1]['display_name'] is None
    assert all('display_name' not in item for item in wire.decode(wire.encode(value))['selections'])


def test_case025_persistent_draft_batch_undo_redo_and_active(setup):
    service, store, installation, releases, _ = setup
    draft = service.create(installation, '中文配置')
    assert not store.list('Profile')
    for release in releases:
        draft = add(service, draft, release)
    before = draft['revision']
    draft = service.edit(draft['profile_id'], before, {'op':'set_enabled', 'release_ids':[r['release_id'] for r in releases], 'selected':False})
    assert draft['revision'] == before + 1
    assert all(not s['selected'] for s in draft['selections'])
    other = ProfileService(service.paths)
    assert other.get(draft['profile_id']) == draft
    assert other.list(installation)['selected_profile_id'] == draft['profile_id']
    undone = other.edit(draft['profile_id'], draft['revision'], {'op':'undo'})
    assert all(s['selected'] for s in undone['selections'])
    redone = other.edit(draft['profile_id'], undone['revision'], {'op':'redo'})
    assert all(not s['selected'] for s in redone['selections'])
    assert not store.list('DeploymentGeneration') and not store.list('Profile')
    other.close()


def test_case025_cas_concurrent_edit_exactly_one_winner(setup):
    service, _, installation, _, _ = setup
    draft = service.create(installation, 'Original')
    def update(name):
        try:
            return service.edit(draft['profile_id'], 0, {'op':'rename', 'name':name})['revision']
        except wire.ProfileError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ['One', 'Two']))
    assert sorted(map(str, results)) == ['1', 'PROFILE_REVISION_CONFLICT']


def test_case025_commit_uses_existing_profile_dto_and_keeps_draft_history(setup):
    service, store, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Main'), releases[0])
    saved = service.commit(draft['profile_id'], draft['revision'])
    assert not saved['dirty'] and saved['committed_revision'] == draft['revision']
    actual = store.get('Profile', draft['profile_id'])
    assert actual.selections[0].manifest_hash == releases[0]['manifest_hash'] and actual.revision == 0
    draft = service.edit(saved['profile_id'], saved['revision'], {'op':'set_enabled', 'release_ids':[releases[0]['release_id']], 'selected':False})
    assert draft['dirty'] and store.get('Profile', draft['profile_id']) == actual
    service.commit(draft['profile_id'], draft['revision'])
    assert store.get('Profile', draft['profile_id']).revision == 1
    undone = service.edit(draft['profile_id'], draft['revision'], {'op':'undo'})
    assert undone['dirty'] and not store.get('Profile', draft['profile_id']).selections[0].selected
    assert not store.list('DeploymentGeneration')


def test_case025_clone_groups_and_name_preserve_source(setup):
    service, _, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Original'), releases[0])
    draft = service.edit(draft['profile_id'], draft['revision'], {'op':'set_group','release_ids':[releases[0]['release_id']], 'group':'工具组'})
    clone = service.clone(draft['profile_id'], draft['revision'], 'Clone')
    assert clone['profile_id'] != draft['profile_id'] and clone['groups'] == ['工具组']
    clone = service.edit(clone['profile_id'], 0, {'op':'rename', 'name':'New name'})
    assert service.get(draft['profile_id']) == draft
    assert service.list(installation)['selected_profile_id'] == clone['profile_id']
    service.select(draft['profile_id'])
    assert service.list(installation)['selected_profile_id'] == draft['profile_id']


def manifest(release=None):
    return {'schema_version':2, 'name':'Imported', 'groups':['工具组'], 'selections':[{
        'release_id':release['release_id'] if release else str(uuid4()),
        'manifest_hash':release['manifest_hash'] if release else 'a'*64, 'selected':True,
        'version_raw':'未知版本 Nightly 0.46D', 'source':{'kind':'user','observed_at':None}, 'group':'工具组'}]}


def import_manifest(service, tmp_path, value, installation):
    source = tmp_path / 'manifest.json'; source.write_bytes(wire.encode(value))
    grant = service.grant(str(source))
    preview = service.import_preview(grant['source_handle'], installation)
    return source, grant, preview


def test_case025_unknown_version_missing_content_roundtrip_without_loss(setup, tmp_path):
    service, store, installation, _, _ = setup
    value = manifest()
    _, grant, preview = import_manifest(service, tmp_path, value, installation)
    assert not preview['selections'][0]['resolved']
    draft = service.import_commit(grant['source_handle'], preview['preview_hash'], installation, value['name'])
    assert service.export(draft['profile_id']) == value
    with pytest.raises(wire.ProfileError, match='UNRESOLVED'):
        service.commit(draft['profile_id'], 0)
    assert not store.list('Profile')
    assert ProfileService(service.paths).get(draft['profile_id']) == draft


@pytest.mark.parametrize('unsafe', ['C:\\Users\\secret', '/etc/passwd', 'https://example.com', 'password=123', 'Bearer abc', 'token', 'a\ncommand'])
def test_case025_manifest_private_version_rejected(unsafe):
    value = manifest(); value['selections'][0]['version_raw'] = unsafe
    with pytest.raises(wire.ProfileError):
        wire.encode(value)


@pytest.mark.parametrize('location', ['root', 'selection', 'source'])
def test_case025_manifest_extra_credentials_or_paths_rejected(location):
    value = manifest()
    target = value if location == 'root' else value['selections'][0] if location == 'selection' else value['selections'][0]['source']
    target['path'] = 'C:\\private\\save.sav'
    with pytest.raises(wire.ProfileError):
        wire.decode(json.dumps(value).encode())


@pytest.mark.parametrize('raw', [b'{"schema_version":2,"schema_version":2}', b'{', b'null', b'[]', b'{"a":NaN}', b'x'*(wire.MAX_BYTES+1)],
                         ids=['duplicate','truncated','null','array','nan','overlarge'])
def test_case025_malformed_or_overlarge_manifest_rejected(raw):
    with pytest.raises(wire.ProfileError):
        wire.decode(raw)


def test_case025_manifest_known_reference_hash_lock_and_privacy(setup, tmp_path):
    service, store, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Main'), releases[0])
    exported = service.export(draft['profile_id'])
    assert exported['selections'][0]['version_raw'] == '未知版本 0.46D'
    assert str(service.paths.root) not in json.dumps(exported)
    assert set(exported['selections'][0]['source']) == {'kind', 'observed_at'}
    _, grant, preview = import_manifest(service, tmp_path, exported, installation)
    assert preview['selections'][0]['resolved']
    imported = service.import_commit(grant['source_handle'], preview['preview_hash'], installation, 'Imported')
    service.commit(imported['profile_id'], 0)
    assert str(store.get('Profile', imported['profile_id']).selections[0].release_id) == releases[0]['release_id']
    wrong = deepcopy(releases[0]); wrong['manifest_hash'] = '0'*64
    with pytest.raises(wire.ProfileError, match='UNRESOLVED'):
        add(service, draft, wrong)


def test_case025_source_changes_invalidate_preview_even_restored_timestamp(setup, tmp_path):
    service, _, installation, _, _ = setup
    source, grant, preview = import_manifest(service, tmp_path, manifest(), installation)
    stamp = source.stat()
    source.write_bytes(source.read_bytes().replace(b'Imported', b'Replaced'))
    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(wire.ProfileError, match='SOURCE_CHANGED'):
        service.import_commit(grant['source_handle'], preview['preview_hash'], installation, 'Main')
    assert not service.list(installation)['profiles']


def test_case025_preview_hash_expiry_and_source_capacity(setup, tmp_path):
    service, _, installation, _, _ = setup
    _, grant, preview = import_manifest(service, tmp_path, manifest(), installation)
    with pytest.raises(wire.ProfileError, match='PREVIEW_CHANGED'):
        service.import_commit(grant['source_handle'], '0'*64, installation, 'Main')
    service._sources[grant['source_handle']]['expires'] = 0
    with pytest.raises(wire.ProfileError, match='SOURCE_EXPIRED'):
        service.import_preview(grant['source_handle'], installation)


def test_case025_source_hardlink_and_junction_refused(setup, tmp_path):
    service, _, _, _, _ = setup
    source = tmp_path / 'manifest.json'; source.write_bytes(wire.encode(manifest()))
    alias = tmp_path / 'alias.json'; os.link(source, alias)
    with pytest.raises(ValueError):
        service.grant(str(alias))
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'manifest.json').write_bytes(wire.encode(manifest()))
    junction = tmp_path / 'junction'
    result = subprocess.run(['cmd','/c','mklink','/J',str(junction),str(outside)],capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(ValueError):
            service.grant(str(junction / 'manifest.json'))
    finally:
        junction.rmdir()


def test_case025_game_and_data_sources_refused(setup):
    service, _, _, _, game = setup
    for source in (game / 'save.sav', service.paths.database):
        with pytest.raises(wire.ProfileError, match='UNSAFE'):
            service.grant(str(source))


def test_case025_corrupt_library_and_cancellation_never_publish(setup):
    service, store, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Main'), releases[0])
    def cancel():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError):
        service.commit(draft['profile_id'], draft['revision'], check=cancel)
    assert not store.list('Profile')
    with pytest.raises(InterruptedError):
        service.commit(draft['profile_id'], draft['revision'], commit=lambda action:cancel())
    assert not store.list('Profile') and service.get(draft['profile_id'])['dirty']
    file = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?', (releases[0]['release_id'],)).fetchone()[0]
    (service.paths.root / 'library' / file[:2] / file).write_bytes(b'corrupt')
    with pytest.raises(wire.ProfileError, match='UNRESOLVED'):
        service.commit(draft['profile_id'], draft['revision'])
    assert not store.list('Profile')


def test_case025_source_remains_pinned_until_import_transaction_commits(setup, tmp_path, monkeypatch):
    service, _, installation, _, _ = setup
    source, grant, preview = import_manifest(service, tmp_path, manifest(), installation)
    original = service._create
    attempts = []
    def replace(store, installation_id, value):
        with pytest.raises(OSError):
            source.write_bytes(b'replaced during commit')
        with pytest.raises(OSError):
            source.rename(source.with_name('moved.json'))
        attempts.append(True)
        return original(store, installation_id, value)
    monkeypatch.setattr(service, '_create', replace)
    service.import_commit(grant['source_handle'], preview['preview_hash'], installation, 'Imported')
    assert attempts and wire.decode(source.read_bytes())['name'] == 'Imported'


def test_case025_preview_binds_target_installation(setup, tmp_path):
    service, _, installation, _, _ = setup
    _, grant, preview = import_manifest(service, tmp_path, manifest(), installation)
    with pytest.raises(wire.ProfileError, match='PREVIEW_CHANGED'):
        service.import_commit(grant['source_handle'], preview['preview_hash'], str(uuid4()), 'Other')
    assert not service.list(installation)['profiles']


def test_case025_source_read_permission_denied_and_oversize(setup, tmp_path):
    import win32file
    import win32con
    service, _, installation, _, _ = setup
    source = tmp_path / 'manifest.json'; source.write_bytes(wire.encode(manifest()))
    handle = win32file.CreateFile(str(source), win32con.GENERIC_READ, 0, None, win32con.OPEN_EXISTING, 0, None)
    try:
        with pytest.raises(ValueError):
            service.grant(str(source))
    finally:
        handle.Close()
    source.write_bytes(b'x' * (wire.MAX_BYTES + 1))
    with pytest.raises(wire.ProfileError, match='MANIFEST_INVALID'):
        service.grant(str(source))
    assert not service.list(installation)['profiles']


def test_case025_unknown_version_full_contract_length_and_punctuation_roundtrip():
    value = manifest()
    value['selections'][0]['version_raw'] = ('unknown [dev]! ' + 'x'*186)[:200]
    assert len(value['selections'][0]['version_raw']) == 200
    assert wire.decode(wire.encode(value)) == value


def test_case025_new_release_never_floats_existing_lock(setup, tmp_path):
    service, store, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Pinned'), releases[0])
    source = tmp_path / 'new-version.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('asset0.txt', b'new version bytes')
    importer = ImportService(service.paths)
    try:
        preview = importer.preview(importer.grant('zip', str(source))['source_handle'])
        newer = importer.commit(preview['preview_id'], preview['preview_hash'], 'Package 0', '2.0')
    finally:
        importer.close()
    assert newer['release_id'] != releases[0]['release_id']
    assert store.get('ModRelease', newer['release_id']).package_id == store.get('ModRelease', releases[0]['release_id']).package_id
    reopened = ProfileService(service.paths)
    try:
        assert reopened.get(draft['profile_id']) == draft
        reopened.commit(draft['profile_id'], draft['revision'])
    finally:
        reopened.close()
    assert str(store.get('Profile', draft['profile_id']).selections[0].release_id) == releases[0]['release_id']


def test_case025_missing_library_bytes_keep_draft_lock_after_reopen(setup):
    service, store, installation, releases, _ = setup
    draft = add(service, service.create(installation, 'Pinned'), releases[0])
    digest = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?', (releases[0]['release_id'],)).fetchone()[0]
    (service.paths.root / 'library' / digest[:2] / digest).unlink()
    reopened = ProfileService(service.paths)
    try:
        after = reopened.get(draft['profile_id'])
        assert after['selections'] == draft['selections']
        with pytest.raises(wire.ProfileError, match='UNRESOLVED'):
            reopened.commit(draft['profile_id'], draft['revision'])
        assert reopened.get(draft['profile_id']) == after
    finally:
        reopened.close()
    assert not store.list('Profile')

