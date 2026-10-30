"""Isolated native state tests. No test launches a game or loads Mod code."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import shutil
import struct
import zipfile

import pytest

from backend.app.paths import AppPaths
from backend.native_mods import NativeModService, NativeModError
from backend.native_mods.dependencies import analyze, enable_closure, disable_closure
from backend.native_mods.packages import inspect_package, managed_header, native_fingerprint
from backend.native_mods.paths import valid_id
from backend.native_mods.state import ORDER_TYPE, encode, load_state, load_config, revision


def cli_fixture():
    """Structural PE/CLI fixture only, not an executable IMod acceptance test."""
    raw = bytearray(1024)
    raw[:2] = b'MZ'; struct.pack_into('<I', raw, 60, 128)
    raw[128:132] = b'PE\0\0'; struct.pack_into('<H', raw, 134, 1)
    struct.pack_into('<H', raw, 148, 224); struct.pack_into('<H', raw, 152, 0x10b)
    struct.pack_into('<II', raw, 152 + 96 + 14 * 8, 0x2000, 72)
    struct.pack_into('<IIII', raw, 152 + 224 + 8, 512, 0x2000, 512, 512)
    struct.pack_into('<II', raw, 512 + 8, 0x2048, 128)
    raw[584:588] = b'BSJB'
    return bytes(raw)


def package(root, ident, dependencies=(), caps=(), *, folder_name=None):
    folder = root / (folder_name or ident)
    folder.mkdir(parents=True)
    assembly = 'Main.dll'
    (folder / assembly).write_bytes(cli_fixture())
    (folder / 'manifest.xml').write_text(
        '<Manifest><ID>' + ident + '</ID><Name>' + ident + '</Name><ModVersion>1.0</ModVersion>'
        '<Assemblies><Assembly>Main.dll</Assembly></Assemblies><Capabilities>' +
        ''.join('<Capability>' + c + '</Capability>' for c in caps) + '</Capabilities><Prerequisites>' +
        ''.join('<Mod>' + d + '</Mod>' for d in dependencies) + '</Prerequisites></Manifest>', encoding='utf-8')
    return folder


class Lease:
    def __init__(self):
        self.calls = 0
        self.fail_at = None

    def check(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise NativeModError('NATIVE_PROCESS_UNKNOWN')


@pytest.fixture
def env(tmp_path):
    game, data, workshop, manager = [tmp_path / p for p in ('game', 'userdata', 'workshop', 'manager')]
    for path in (game / 'Mods', data, workshop, manager):
        path.mkdir(parents=True)
    (game / 'Probably Stolen_Data/Managed').mkdir(parents=True)
    (game / 'MonoBleedingEdge').mkdir()
    (game / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll').write_bytes(b'identity fixture')
    installation = {'installation_id': 'test-install', 'canonical_root': str(game),
                    'persistent_root': str(data), 'workshop_root': str(workshop)}
    lease = Lease()
    @contextmanager
    def guard(_):
        yield lease
    service = NativeModService(AppPaths(manager), write_guard=guard,
                               process_status=lambda _: {'state': 'stopped'})
    document = {'modOrder': {'__type': ORDER_TYPE, 'value': [], 'unknownOrderField': 7},
                'modTrust': {'__type': 'string', 'value': json.dumps({'records': [], 'unknownTrustField': {'a': 1}}), 'unknownWrapper': 'retain'},
                'unrelated': {'__type': 'string', 'value': 'do not change', 'extra': [1, 2]}}
    state = data / 'SaveFile.es3'
    state.write_bytes(encode(document))
    return service, installation, game, data, workshop, state, lease


def approve(state_path, packages, ids):
    doc = json.loads(state_path.read_text())
    doc['modOrder']['value'] = list(ids)
    records = []
    for folder, source, workshop_id in packages:
        p = inspect_package(folder, source, workshop_id)
        records.append({'id': p['id'], 'source': source.title(), 'workshopId': workshop_id or '0',
                        'combinedHash': native_fingerprint(p), 'detected': p['capabilities'],
                        'declared': p['capabilities'], 'approvedAt': '2026-10-28T00:00:00Z',
                        'developerMode': False, 'unknownRecord': {'retain': True}})
    trust = json.loads(doc['modTrust']['value']); trust['records'] = records
    doc['modTrust']['value'] = json.dumps(trust)
    state_path.write_bytes(encode(doc))


def test_state_save_preserves_unknowns_revokes_only_disabled_never_approves(env):
    svc, inst, game, _, _, state, lease = env
    a = package(game / 'Mods', 'a'); b = package(game / 'Mods', 'b')
    package(game / 'Mods', 'new')
    approve(state, [(a, 'local', None), (b, 'local', None)], ['a', 'b'])
    before = json.loads(state.read_text())
    snap = svc.snapshot(inst)
    result = svc.save(inst, snap['revision'], ['b', 'new'])
    after = json.loads(state.read_text())
    assert result['saved'] and after['modOrder']['value'] == ['b', 'new']
    assert after['unrelated'] == before['unrelated']
    assert after['modOrder']['unknownOrderField'] == 7
    trust = json.loads(after['modTrust']['value'])
    assert [r['id'] for r in trust['records']] == ['b']
    assert trust['unknownTrustField'] == {'a': 1}
    assert trust['records'][0]['unknownRecord'] == {'retain': True}
    assert after['modTrust']['unknownWrapper'] == 'retain'
    assert lease.calls >= 2
    assert a.exists() and b.exists()
    assert svc.recovery_list(inst)['items'][0]['status'] == 'committed'
    restored = svc.recovery_restore(inst, result['recovery_id'], result['revision'])
    assert restored['enabled_ids'] == ['a', 'b']
    assert [r['id'] for r in load_state(state)['trust_document']['records']] == ['b']


def test_save_conflict_and_guard_fail_do_not_modify(env):
    svc, inst, game, _, _, state, lease = env
    package(game / 'Mods', 'a')
    snap = svc.snapshot(inst)
    state.write_bytes(state.read_bytes() + b' ')
    changed = state.read_bytes()
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.save(inst, snap['revision'], ['a'])
    assert state.read_bytes() == changed
    snap = svc.snapshot(inst); lease.fail_at = lease.calls + 2
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.save(inst, snap['revision'], ['a'])
    assert state.read_bytes() == changed
    assert svc.recovery_list(inst)['items'][0]['status'] == 'failed'


def test_no_guard_means_readonly(env):
    svc, inst, *_ = env
    denied = NativeModService(svc.paths)
    snap = denied.snapshot(inst)
    assert snap['runtime']['state'] == 'unknown'
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        denied.save(inst, snap['revision'], [])


@pytest.mark.parametrize('invalid', [b'ES3encrypted', b'{"modOrder":1}', b'{"x":1,"x":2}',
    b'{"modOrder":{"__type":"list","value":[]}}',
    encode({'modTrust': {'__type': 'string', 'value': '{"records":42}'}})])
def test_unknown_state_format_fails_closed(env, invalid):
    svc, inst, _, _, _, state, _ = env
    state.write_bytes(invalid)
    snap = svc.snapshot(inst)
    assert any(i['code'] == 'NATIVE_STATE_FORMAT' for i in snap['issues'])
    with pytest.raises(NativeModError):
        svc.save(inst, snap['revision'], [])
    assert state.read_bytes() == invalid


def test_states_sources_and_fingerprint_semantics(env):
    svc, inst, game, _, workshop, state, _ = env
    local = package(game / 'Mods', 'a', caps=['files'])
    remote = package(workshop, 'a', folder_name='123')
    approve(state, [(local, 'local', None)], ['a'])
    snap = svc.snapshot(inst); mod = snap['mods'][0]
    assert mod['installed'] and mod['target_enabled'] and mod['approval']['state'] == 'approved'
    assert mod['run']['state'] == 'unknown'
    assert [s['active'] for s in mod['sources']] == [True, False]
    assert mod['approval']['current_scan_status'] == 'not_scanned'
    (local / 'resource.txt').write_text('not covered by native fingerprint')
    assert svc.snapshot(inst)['mods'][0]['approval']['state'] == 'approved'
    (local / 'Main.dll').write_bytes(cli_fixture() + b'updated')
    assert svc.snapshot(inst)['mods'][0]['approval']['state'] == 'updated_unverified'


def test_config_unknown_fields_and_original_strings_preserved(env):
    svc, inst, game, data, *_ = env
    package(game / 'Mods', 'a')
    path = data / 'ModConfigs/a.json'; path.parent.mkdir()
    original = {'future': {'keep': 1}, 'entries': [{'key': 'x', 'value': '001', 'future': [2]}, {'key': 'z', 'value': 'true'}]}
    path.write_bytes(encode(original))
    read = svc.config_read(inst, 'a')
    assert read['entries'][0]['type'] == 'unknown'
    written = svc.config_save(inst, 'a', read['revision'], [{'key': 'x', 'value': '0002'}])
    result = json.loads(path.read_text())
    assert result == {'future': {'keep': 1}, 'entries': [{'key': 'x', 'value': '0002', 'future': [2]}, {'key': 'z', 'value': 'true'}]}
    svc.recovery_restore(inst, written['recovery_id'], svc.snapshot(inst)['revision'])
    assert json.loads(path.read_text()) == original
    with pytest.raises(NativeModError, match='NATIVE_CONFIG_FORMAT'):
        svc.config_save(inst, 'a', svc.config_read(inst, 'a')['revision'], [{'key': 'x', 'value': 2}])


def test_dependency_closure_stability_cycle_and_reverse():
    def mod(i, deps=()):
        return {'id': i, 'installed': True, 'prerequisites': list(deps), 'issues': []}
    mods = [mod('a', ['b']), mod('b'), mod('c'), mod('d', ['a'])]
    result = analyze(mods, ['c', 'a', 'b', 'd'])
    assert result['sorted_ids'] == ['c', 'b', 'a', 'd'] and not result['blocking']
    assert enable_closure(mods, ['c'], 'd')['sorted_ids'] == ['c', 'b', 'a', 'd']
    assert disable_closure(mods, ['c', 'b', 'a', 'd'], 'b')['affected_ids'] == ['b', 'a', 'd']
    assert analyze(mods, ['a'])['blocking']
    cycle = analyze([mod('a', ['b']), mod('b', ['a'])], ['a', 'b'])
    assert cycle['blocking'] and cycle['sorted_ids'] == ['a', 'b']
    assert next(i['cycle'] for i in cycle['issues'] if i['code'] == 'NATIVE_DEPENDENCY_CYCLE') == ['a', 'b', 'a']
    assert analyze(mods, ['a', 'b'], [{'before': 'a', 'after': 'b'}])['blocking']


def test_missing_dependency_blocks_without_writing(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a', dependencies=['missing'])
    before = state.read_bytes(); snap = svc.snapshot(inst)
    with pytest.raises(NativeModError, match='NATIVE_PLAN_BLOCKED'):
        svc.save(inst, snap['revision'], ['a'])
    assert before == state.read_bytes()


def test_import_and_recovery_retains_full_package(env, tmp_path):
    svc, inst, game, _, _, state, _ = env
    source = package(tmp_path / 'source', 'new')
    (source / 'Textures').mkdir(); (source / 'Textures/picture.dat').write_bytes(b'resource')
    preview = svc.import_preview(inst, str(source))
    assert not preview['blocking']
    result = svc.import_commit(inst, preview['preview_id'], preview['revision'])
    installed = game / 'Mods/new'
    assert (installed / 'Textures/picture.dat').read_bytes() == b'resource'
    assert not result['mods'][0]['target_enabled']
    assert result['mods'][0]['approval']['state'] == 'unknown'
    restored = svc.recovery_restore(inst, result['recovery_id'], result['revision'])
    assert not installed.exists() and source.exists()
    again = svc.recovery_restore(inst, restored['recovery_id'], restored['revision'])
    assert installed.exists() and again['mods'][0]['approval']['state'] == 'unknown'


def test_zip_wrapping_preserves_layout(env, tmp_path):
    svc, inst, game, *_ = env
    source = package(tmp_path / 'source', 'zipmod')
    archive = tmp_path / 'mod.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for file in source.rglob('*'):
            z.write(file, 'wrapper/' + file.relative_to(source).as_posix())
    preview = svc.import_preview(inst, str(archive))
    svc.import_commit(inst, preview['preview_id'], preview['revision'])
    assert (game / 'Mods/zipmod/manifest.xml').is_file()
    assert not (game / 'Mods/zipmod/wrapper').exists()


@pytest.mark.parametrize('name', ['../escape.dll', 'C:/escape.dll', 'dir/../../x', 'CON.dll', 'foo:bar.dll', 'trailing./a'])
def test_zip_unsafe_paths(env, tmp_path, name):
    svc, inst, *_ = env
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(name, b'x')
    with pytest.raises(NativeModError, match='NATIVE_UNSAFE_PATH'):
        svc.import_preview(inst, str(archive))


def test_zip_symlink_case_collision_and_multiple_packages(env, tmp_path):
    svc, inst, *_ = env
    archive = tmp_path / 'bad.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        entry = zipfile.ZipInfo('x'); entry.external_attr = 0o120777 << 16
        z.writestr(entry, 'outside')
    with pytest.raises(NativeModError, match='NATIVE_UNSAFE_ARCHIVE'):
        svc.import_preview(inst, str(archive))
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('a', '1'); z.writestr('A', '2')
    with pytest.raises(NativeModError, match='NATIVE_CASE_COLLISION'):
        svc.import_preview(inst, str(archive))
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('a/manifest.xml', 'x'); z.writestr('b/manifest.xml', 'x')
    with pytest.raises(NativeModError, match='NATIVE_ARCHIVE_PACKAGE_COUNT'):
        svc.import_preview(inst, str(archive))


def test_import_blocks_unlisted_and_malformed_dll(env, tmp_path):
    svc, inst, *_ = env
    source = package(tmp_path / 'source', 'bad')
    (source / 'Extra.dll').write_bytes(b'x')
    (source / 'Main.dll').write_bytes(b'not managed')
    preview = svc.import_preview(inst, str(source))
    assert preview['blocking']
    assert {'NATIVE_ASSEMBLY_INVALID', 'NATIVE_UNLISTED_DLL'} <= {i['code'] for i in preview['issues']}
    with pytest.raises(NativeModError, match='NATIVE_PLAN_BLOCKED'):
        svc.import_commit(inst, preview['preview_id'], preview['revision'])


def test_source_switch_revokes_trust_without_writing_workshop(env):
    svc, inst, game, _, workshop, state, _ = env
    local = package(game / 'Mods', 'a'); remote = package(workshop, 'a', folder_name='123')
    remote_bytes = {p.relative_to(remote).as_posix(): p.read_bytes() for p in remote.rglob('*') if p.is_file()}
    approve(state, [(local, 'local', None)], ['a'])
    snap = svc.snapshot(inst)
    result = svc.source_switch(inst, 'a', snap['revision'])
    assert result['enabled_ids'] == ['a'] and result['mods'][0]['source'] == 'workshop'
    assert load_state(state)['trust_document']['records'] == []
    assert remote_bytes == {p.relative_to(remote).as_posix(): p.read_bytes() for p in remote.rglob('*') if p.is_file()}
    restored = svc.recovery_restore(inst, result['recovery_id'], result['revision'])
    assert restored['mods'][0]['source'] == 'local' and restored['mods'][0]['approval']['state'] == 'unknown'


def test_local_remove_requires_reverse_dependents_stopped(env):
    svc, inst, game, _, _, state, _ = env
    a = package(game / 'Mods', 'a'); b = package(game / 'Mods', 'b', dependencies=['a'])
    approve(state, [(a, 'local', None), (b, 'local', None)], ['a', 'b'])
    with pytest.raises(NativeModError, match='NATIVE_DEPENDENTS_ENABLED'):
        svc.local_remove(inst, 'a', svc.snapshot(inst)['revision'])
    assert a.exists()


def test_data_snapshot_restore_does_not_touch_custom_data(env):
    svc, inst, game, data, *_ = env
    package(game / 'Mods', 'a')
    cfg = data / 'ModConfigs/a.json'; cfg.parent.mkdir(); cfg.write_bytes(encode({'entries': [{'key': 'x', 'value': 'old'}]}))
    moddata = data / 'ModData/a'; moddata.mkdir(parents=True); (moddata / 'file').write_bytes(b'old')
    custom = game / 'UserData'; custom.mkdir(); (custom / 'untouched').write_bytes(b'private')
    result = svc.recovery_create(inst, 'a')
    cfg.write_bytes(encode({'entries': [{'key': 'x', 'value': 'new'}]})); (moddata / 'file').write_bytes(b'new')
    (moddata / 'extra').write_bytes(b'added')
    svc.recovery_restore(inst, result['recovery_id'], svc.snapshot(inst)['revision'])
    assert load_config(cfg)[1]['entries'][0]['value'] == 'old'
    assert (moddata / 'file').read_bytes() == b'old' and not (moddata / 'extra').exists()
    assert (custom / 'untouched').read_bytes() == b'private'
    assert 'custom_UserData' in result['excluded']


def test_profiles_are_drafts_and_legacy_mapping_is_explicit(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a')
    before = state.read_bytes()
    svc.write_guard = None  # Manager-only profiles remain usable while running.
    created = svc.profile_create(inst, 'My selection', ['a'])
    profile_id = created['profile']['id']
    assert svc.profile_preview(inst, profile_id)['enabled_ids'] == ['a']
    svc.profile_update(inst, profile_id, 'Renamed', ['a'])
    migration = svc.migration_preview(inst, {'mods': [{'native_id': 'a'}, {'name': 'a'}, 'Unknown.dll']})
    assert migration['enabled_ids'] == ['a'] and len(migration['skipped']) == 2
    deleted = svc.profile_delete(inst, profile_id)
    assert deleted['items'] == []
    svc.recovery_restore(inst, deleted['recovery_id'], 'not-used-for-manager-only')
    assert svc.profile_list(inst)['items'][0]['name'] == 'Renamed'
    assert state.read_bytes() == before


def test_portable_profile_preserves_missing_sources_and_explicit_rules(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'installed')
    before = state.read_bytes()
    global_rules = svc.rules_read(inst)
    rules = [{'before': 'missing', 'after': 'installed', 'private_extra': 'discard'}]
    sources = [{'id': 'missing', 'source': 'workshop', 'workshop_id': '3828868413', 'version': '2.0',
                'folder_path': 'sender-only-path', 'approval': {'state': 'approved'}},
               {'id': 'installed', 'source': 'local', 'workshop_id': None, 'version': 'old'}]
    profile = svc.profile_create(inst, 'Imported', ['missing', 'installed'], rules, sources)['profile']
    assert profile['rules'] == [{'before': 'missing', 'after': 'installed'}]
    assert profile['sources'][0] == {'id': 'missing', 'source': 'workshop', 'workshop_id': '3828868413', 'version': '2.0'}
    rules[0]['before'] = 'changed'; sources[0]['version'] = 'changed'
    preview = svc.profile_preview(inst, profile['id'])
    assert preview['missing_ids'] == ['missing'] and preview['blocking']
    assert preview['profile']['sources'][0]['version'] == '2.0'
    assert preview['profile']['rules'][0]['before'] == 'missing'
    assert svc.rules_read(inst) == global_rules and state.read_bytes() == before


def test_profile_update_retains_saved_rules_and_portable_sources_by_default(env):
    svc, inst, game, *_ = env
    package(game / 'Mods', 'new')
    original_rules = [{'before': 'missing', 'after': 'new'}]
    imported = [{'id': 'missing', 'source': 'workshop', 'workshop_id': '123', 'version': '1'}]
    profile = svc.profile_create(inst, 'Original', ['missing'], original_rules, imported)['profile']
    svc.rules_save(inst, svc.rules_read(inst)['revision'], [{'before': 'new', 'after': 'missing'}])
    updated = svc.profile_update(inst, profile['id'], 'Renamed', ['missing', 'new'])['profile']
    assert updated['rules'] == original_rules and updated['sources'][0] == imported[0]
    assert updated['sources'][1] == {'id': 'new', 'source': 'local', 'workshop_id': None, 'version': '1.0'}
    overridden = svc.profile_update(inst, profile['id'], 'Explicit reset', ['missing'], [], [])['profile']
    assert overridden['rules'] == [] and overridden['sources'] == []


def test_profile_preview_reports_version_change_with_unchanged_source(env):
    svc, inst, game, *_ = env
    package(game / 'Mods', 'a')
    profile = svc.profile_create(inst, 'Pinned version', ['a'], [], [
        {'id': 'a', 'source': 'local', 'workshop_id': None, 'version': '0.9'}])['profile']
    preview = svc.profile_preview(inst, profile['id'])
    assert preview['source_changes'] == ['a'] and preview['missing_ids'] == []
    svc.profile_update(inst, profile['id'], 'Current version', ['a'], sources=[
        {'id': 'a', 'source': 'local', 'workshop_id': None, 'version': '1.0'}])
    assert svc.profile_preview(inst, profile['id'])['source_changes'] == []


@pytest.mark.parametrize('source', [
    {'id': '../bad', 'source': 'local'},
    {'id': 'not-selected', 'source': 'local'},
    {'id': 'missing', 'source': 'download-url'},
    {'id': 'missing', 'source': 'workshop', 'workshop_id': '../123'},
    {'id': 'missing', 'source': 'workshop', 'workshop_id': '18446744073709551616'},
    {'id': 'missing', 'source': 'local', 'workshop_id': '123'},
    {'id': 'missing', 'source': 'local', 'version': {'path': 'x'}},
])
def test_portable_profile_rejects_invalid_source_identity(env, source):
    svc, inst, *_ = env
    with pytest.raises(NativeModError, match='NATIVE_PROFILE_FORMAT'):
        svc.profile_create(inst, 'Invalid import', ['missing'], [], [source])
    assert svc.profile_list(inst)['items'] == []


def test_portable_profile_rejects_invalid_rules_without_writing(env):
    svc, inst, *_ = env
    with pytest.raises(NativeModError, match='NATIVE_INVALID_RULE'):
        svc.profile_create(inst, 'Invalid import', ['missing'], [{'before': '../bad', 'after': 'missing'}], [])
    assert svc.profile_list(inst)['items'] == []


@pytest.fixture
def legacy_profile_shapes():
    return json.loads((Path(__file__).parent / 'fixtures/legacy_profile_shapes.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('shape', ['get', 'export'])
def test_migration_reads_real_legacy_selection_shape_without_guessing_release_ids(env, legacy_profile_shapes, shape):
    svc, inst, game, _, _, state, _ = env
    value = legacy_profile_shapes[shape]
    release = value['selections'][0]['release_id']
    package(game / 'Mods', release)  # Even a matching native ID must not reinterpret a release UUID.
    before = state.read_bytes()
    preview = svc.migration_preview(inst, value)
    assert preview['enabled_ids'] == [] and len(preview['skipped']) == 2
    first, disabled = preview['skipped']
    assert first['release_id'] == release and first['reason'] == 'unverified_native_identity'
    assert first['display_name'] == ('物品详情旧版' if shape == 'get' else release)
    assert disabled['reason'] == 'disabled_in_legacy_profile' and disabled['reason_text']
    assert disabled['display_name'] == value['selections'][1]['release_id']
    assert state.read_bytes() == before and preview['preview_only']


@pytest.mark.parametrize('wrapper', ['profile', 'manifest', 'draft'])
def test_migration_accepts_share_and_draft_wrappers(env, legacy_profile_shapes, wrapper):
    svc, inst, *_ = env
    value = legacy_profile_shapes['export']
    release = value['selections'][0]['release_id']
    wrapped = {'data': {'current': value, 'undo': [], 'committed': value}} if wrapper == 'draft' else {wrapper: value}
    wrapped['releases'] = [{'release_id': release, 'display_name': '分享包中的名称'}]
    preview = svc.migration_preview(inst, wrapped)
    assert len(preview['skipped']) == 2 and preview['skipped'][0]['display_name'] == '分享包中的名称'


def test_migration_explicit_native_mapping_respects_disabled_and_reports_every_item(env, legacy_profile_shapes):
    svc, inst, game, *_ = env
    package(game / 'Mods', 'a'); package(game / 'Mods', 'b')
    value = legacy_profile_shapes['get']
    value['selections'][0]['native_id'] = 'a'
    value['selections'][1]['native_id'] = 'b'
    value['selections'] += [{'native_id': 'missing', 'display_name': '尚未安装'},
                            {'native_id': 'a', 'selected': 'false', 'display_name': '无效标记'},
                            {'native_id': 'a', 'display_name': '重复条目'},
                            {'native_id': 'b', 'enabled': False, 'display_name': '其他停用格式'}, 7]
    preview = svc.migration_preview(inst, value)
    assert preview['enabled_ids'] == ['a'] and len(preview['skipped']) == 6
    assert [r['reason'] for r in preview['skipped']] == ['disabled_in_legacy_profile', 'native_mod_not_installed',
        'invalid_enabled_state', 'duplicate_native_id', 'disabled_in_legacy_profile', 'invalid_legacy_entry']
    assert all(r['display_name'] and r['reason_text'] for r in preview['skipped'])


@pytest.mark.parametrize('value', [{}, {'selections': 'not-a-list'}, {'selections': [], 'mods': []},
                                  {'profile': {'name': 'Unknown non-empty format'}}])
def test_migration_unknown_or_ambiguous_shape_is_not_reported_as_empty(env, value):
    svc, inst, *_ = env
    with pytest.raises(NativeModError, match='NATIVE_PROFILE_FORMAT'):
        svc.migration_preview(inst, value)


def test_native_id_is_ascii_as_decompiled():
    assert valid_id('CyberElectronics') and valid_id('probablystolen.workshop.uploader')
    assert valid_id('a' * 64) and not valid_id('a' * 65)
    assert not valid_id('.hidden') and not valid_id('模组') and not valid_id('mod/other')
    assert valid_id('CON')  # Native identity and Windows-safe filenames are separate rules.


def test_legacy_or_demo_cannot_read_or_write_formal_user_state(env):
    svc, inst, game, _, _, state, _ = env
    before = state.read_bytes()
    game.joinpath('MonoBleedingEdge').rmdir()
    snap = svc.snapshot(inst)
    assert not snap['ready'] and snap['enabled_ids'] == [] and snap['paths']['state_file'] is None
    with pytest.raises(NativeModError, match='NATIVE_RUNTIME_UNSUPPORTED'):
        svc.save(inst, snap['revision'], [])
    with pytest.raises(NativeModError, match='NATIVE_RUNTIME_UNSUPPORTED'):
        svc.config_read(inst, 'a')
    with pytest.raises(NativeModError, match='NATIVE_RUNTIME_UNSUPPORTED'):
        svc.import_preview(inst, str(game))
    (game / 'MonoBleedingEdge').mkdir()
    inst['app_id'] = '4349200'
    assert not svc.snapshot(inst)['ready']
    assert state.read_bytes() == before


def test_rules_persist_validate_profiles_and_recover(env):
    svc, inst, game, *_ = env
    package(game / 'Mods', 'a', dependencies=['b']); package(game / 'Mods', 'b')
    original = svc.rules_read(inst)
    result = svc.rules_save(inst, original['revision'], [{'before': 'a', 'after': 'b'}])
    assert svc.snapshot(inst)['rules'] == result['rules']
    assert svc.validate(inst, ['a', 'b'])['blocking']
    profile = svc.profile_create(inst, 'with rules', ['a', 'b'])['profile']
    assert profile['rules'] == result['rules']
    with pytest.raises(NativeModError, match='NATIVE_PLAN_BLOCKED'):
        svc.save(inst, svc.snapshot(inst)['revision'], ['a', 'b'])
    svc.recovery_restore(inst, result['recovery_id'], svc.snapshot(inst)['revision'])
    assert svc.rules_read(inst)['rules'] == []


def test_host_readonly_reason_not_overridden(env):
    svc, inst, *_ = env
    svc.process_status = lambda _: {'state': 'stopped', 'writable': False, 'reason': 'pending_recovery'}
    assert svc.snapshot(inst)['runtime']['writable'] is False


def test_failed_write_blocks_until_explicit_recovery(env):
    svc, inst, game, _, _, state, lease = env
    package(game / 'Mods', 'a')
    lease.fail_at = lease.calls + 2
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    pending = svc.recovery_list(inst)['items'][0]
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_PENDING'):
        svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    svc.recovery_restore(inst, pending['id'], svc.snapshot(inst)['revision'])
    svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    assert load_state(state)['enabled_ids'] == ['a']


def test_failed_import_before_publication_can_be_resolved(env, tmp_path):
    svc, inst, game, _, _, _, lease = env
    source = package(tmp_path / 'source', 'new')
    preview = svc.import_preview(inst, str(source))
    lease.fail_at = lease.calls + 2
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.import_commit(inst, preview['preview_id'], preview['revision'])
    pending = svc.recovery_list(inst)['items'][0]
    assert not (game / 'Mods/new').exists()
    svc.recovery_restore(inst, pending['id'], svc.snapshot(inst)['revision'])
    assert not svc._pending(inst)


def test_missing_recovery_bytes_are_not_treated_as_absent_original(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a')
    result = svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    folder, _ = svc._record(inst, result['recovery_id'])
    (folder / 'before.bin').unlink()
    current = state.read_bytes()
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_INVALID'):
        svc.recovery_restore(inst, result['recovery_id'], result['revision'])
    assert state.read_bytes() == current


def test_recovery_import_undo_revokes_even_disabled_approval(env, tmp_path):
    svc, inst, game, _, _, state, _ = env
    source = package(tmp_path / 'source', 'new')
    preview = svc.import_preview(inst, str(source))
    result = svc.import_commit(inst, preview['preview_id'], preview['revision'])
    approve(state, [(game / 'Mods/new', 'local', None)], [])
    svc.recovery_restore(inst, result['recovery_id'], svc.snapshot(inst)['revision'])
    assert not (game / 'Mods/new').exists()
    assert load_state(state)['trust_document']['records'] == []


def test_hardlinked_package_is_rejected(env, tmp_path):
    import os
    svc, inst, *_ = env
    source = package(tmp_path / 'source', 'new')
    original = source / 'Main.dll'
    os.link(original, source / 'linked.dll')
    with pytest.raises(NativeModError, match='NATIVE_UNSAFE_PATH'):
        svc.import_preview(inst, str(source))


def test_walk_error_cannot_be_misread_as_empty_package(tmp_path, monkeypatch):
    from backend.native_mods import packages
    source = package(tmp_path, 'a')
    def failed_walk(*_, onerror, **__):
        onerror(PermissionError())
        return iter(())
    monkeypatch.setattr(packages.os, 'walk', failed_walk)
    with pytest.raises(NativeModError, match='NATIVE_PACKAGE_UNREADABLE'):
        packages.tree_files(source)


def test_failed_backup_has_no_game_mutation_or_pending_write(env, monkeypatch):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a')
    before = state.read_bytes()
    def fail_backup(*_):
        raise OSError('test-only backup failure')
    monkeypatch.setattr(svc, '_backup', fail_backup)
    with pytest.raises(OSError):
        svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    assert state.read_bytes() == before and svc._pending(inst) == []
    item = svc.recovery_list(inst)['items'][0]
    assert item['status'] == 'preparing' and item['restorable'] is False
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_INCOMPLETE'):
        svc.recovery_restore(inst, item['id'], svc.snapshot(inst)['revision'])


@pytest.mark.parametrize('field,value', [('developerMode', 'false'), ('detected', {'files': True}), ('workshopId', 123)])
def test_corrupt_typed_trust_record_does_not_claim_approval(env, field, value):
    svc, inst, game, _, _, state, _ = env
    local = package(game / 'Mods', 'a')
    approve(state, [(local, 'local', None)], ['a'])
    doc = json.loads(state.read_text())
    trust = json.loads(doc['modTrust']['value'])
    trust['records'][0][field] = value
    doc['modTrust']['value'] = json.dumps(trust)
    state.write_bytes(encode(doc))
    snap = svc.snapshot(inst)
    assert not snap['ready'] and snap['mods'][0]['approval']['state'] == 'unknown'
    before = state.read_bytes()
    with pytest.raises(NativeModError, match='NATIVE_PLAN_BLOCKED'):
        svc.save(inst, snap['revision'], [])
    assert state.read_bytes() == before


def test_package_drift_during_state_backup_prevents_save(env, monkeypatch):
    svc, inst, game, _, _, state, _ = env
    mod = package(game / 'Mods', 'a')
    before = state.read_bytes()
    original_backup = svc._backup
    def drift(folder, raw, name='before.bin'):
        original_backup(folder, raw, name)
        (mod / 'Main.dll').write_bytes(cli_fixture() + b'changed')
    monkeypatch.setattr(svc, '_backup', drift)
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    assert state.read_bytes() == before


def test_unchanged_failed_save_can_clear_recovery_when_previous_dependency_missing(env):
    svc, inst, game, _, _, state, lease = env
    local = package(game / 'Mods', 'a')
    approve(state, [(local, 'local', None)], ['a'])
    before = state.read_bytes()
    lease.fail_at = lease.calls + 2
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.save(inst, svc.snapshot(inst)['revision'], [])
    # Simulate an external installer moving this source after the failed write.
    local.rename(game / 'external-package')
    failed = svc.recovery_list(inst)['items'][0]
    svc.recovery_restore(inst, failed['id'], svc.snapshot(inst)['revision'])
    assert state.read_bytes() == before and not svc._pending(inst)


def test_data_recovery_early_drift_does_not_delete_new_external_data(env, monkeypatch):
    svc, inst, game, data, _, _, lease = env
    package(game / 'Mods', 'a')
    recovery = svc.recovery_create(inst, 'a')
    original_check = lease.check
    count = 0
    def changed_after_backup():
        nonlocal count
        original_check(); count += 1
        if count == 3:  # Entry, backup completion, then pre-publication.
            external = data / 'ModData/a/external'
            external.parent.mkdir(parents=True)
            external.write_bytes(b'external write')
    monkeypatch.setattr(lease, 'check', changed_after_backup)
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.recovery_restore(inst, recovery['recovery_id'], svc.snapshot(inst)['revision'])
    assert (data / 'ModData/a/external').read_bytes() == b'external write'


def test_real_native_files_copied_then_edited_only_in_isolation(env):
    """Primary real-source case; unavailable real files mean explicit skip, not synthetic pass."""
    source = Path('E:/Entertainment/Steam/steamapps/workshop/content/4348910/3828868413')
    real_data = Path.home() / 'AppData/LocalLow/Questing Goose Studio/Probably Stolen'
    if not (source / 'manifest.xml').is_file() or not (real_data / 'SaveFile.es3').is_file():
        pytest.skip('Real native package / ES3 source is not available on this host')
    svc, inst, game, data, workshop, state, _ = env
    destination = workshop / source.name
    shutil.copytree(source, destination)
    shutil.copyfile(real_data / 'SaveFile.es3', state)
    for name in ('ModConfigs/item_details.json', 'Player.log'):
        origin = real_data / name
        if origin.is_file():
            target = data / name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(origin, target)
    original_real_state = (real_data / 'SaveFile.es3').read_bytes()
    doc = json.loads(state.read_text(encoding='utf-8-sig'))
    parsed = inspect_package(destination, 'workshop', source.name, check_binaries=True)
    assert parsed['id'] == 'item_details' and not parsed['issues']
    assert all(managed_header(destination / a) == 'managed' for a in parsed['assemblies'])
    snap = svc.snapshot(inst)
    assert len(snap['mods']) == 1 and snap['mods'][0]['id'] == 'item_details'
    saved = svc.save(inst, snap['revision'], ['item_details'])
    after = json.loads(state.read_text(encoding='utf-8-sig'))
    for key in doc.keys() - {'modOrder', 'modTrust'}:
        assert after[key] == doc[key]
    assert saved['enabled_ids'] == ['item_details']
    assert (real_data / 'SaveFile.es3').read_bytes() == original_real_state
