"""Real private libraries and synthetic games; no game DLL is executed."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
import zipfile

import pytest

from backend.catalog.updates import ModUpdateService
from backend.catalog.versions import compare_versions
from backend.profiles.sharing import ProfileSharingService
from backend.profiles.serialization import ProfileError
from backend.profiles.sharing import safe_file
from backend.storage.package_repository import PackageRepository
from backend.deployments.policy import PlanRejected
from tests.integration.harness.transaction_recovery import TransactionFixture


@pytest.fixture
def pair(tmp_path):
    left, right = tmp_path/'left', tmp_path/'right'
    left.mkdir(); right.mkdir()
    a, b = TransactionFixture(left), TransactionFixture(right)
    try:
        yield a, b
    finally:
        a.close(); b.close()


def sharing(app):
    return ProfileSharingService(app.context.paths, app.context.profiles, app.context.mod_workspace)


def write(app, name, data):
    path = app.game/name
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)


def publish(app, files, name, version='1.0'):
    source = app.root/str(uuid4()); source.mkdir()
    for relative, data in files.items():
        target = source/relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    imports = app.context.imports
    handle = imports.grant('directory', str(source))['source_handle']
    preview = imports.preview(handle)
    return imports.commit(preview['preview_id'], preview['preview_hash'], name, version)


def enable(app, release):
    workspace = app.context.mod_workspace
    view = workspace.list(app.installation)
    result = workspace.save(dict(installation_id=app.installation, snapshot_id=view['snapshot_id'],
        enabled_ids=[*view['enabled_ids'], release['release_id']], idempotency_key=str(uuid4())))
    assert result.get('completed'), result


def apply(app, service, profile):
    view = app.context.mod_workspace.list(app.installation)
    result = service.apply(profile['profile_id'], profile['revision'], view['snapshot_id'], str(uuid4()))
    assert result.get('completed'), result


def test_capture_external_overwrite_save_as_and_roundtrip_bundle(pair, tmp_path):
    a, b = pair
    imported = publish(a, {'Mods/One/asset.png': b'asset', 'README.txt': b'Mod readme'}, 'One', '1.2.0')
    enable(a, imported)
    write(a, 'Mods/Existing.dll', b'capture-only-not-executed')
    write(a, 'Mods/Existing/assets/kept.png', b'existing resources')
    service = sharing(a)
    original = service.capture(a.installation, '方案一')
    assert not original['dirty'] and len(original['selections']) == 2
    assert not (a.game/'README.txt').exists()
    clone = a.context.profiles.clone(original['profile_id'], original['revision'], '另存方案')
    clone = a.context.profiles.commit(clone['profile_id'], clone['revision'])
    assert clone['profile_id'] != original['profile_id']
    replaced = service.capture(a.installation, '替换后的方案', original['profile_id'], original['revision'])
    assert replaced['profile_id'] == original['profile_id'] and replaced['revision'] > original['revision']
    assert a.context.profiles.get(clone['profile_id'])['name'] == '另存方案'
    bundle = tmp_path/'shared.zip'
    exported = service.export_bundle(replaced['profile_id'], str(bundle))
    assert exported['saved'] and exported['file_count'] == 4
    receiver = sharing(b)
    grant = receiver.grant(str(bundle))
    preview = receiver.preview(grant['source_handle'], b.installation)
    assert preview['includes_mods'] and preview['missing_count'] == 0
    restored = receiver.import_bundle(grant['source_handle'], preview['preview_hash'], b.installation, '朋友的方案')
    assert not restored['dirty'] and all(item['resolved'] for item in restored['selections'])
    apply(b, receiver, restored)
    assert (b.game/'Mods/One/asset.png').read_bytes() == b'asset'
    assert (b.game/'Mods/Existing.dll').read_bytes() == b'capture-only-not-executed'
    assert (b.game/'Mods/Existing/assets/kept.png').read_bytes() == b'existing resources'
    assert not (b.game/'README.txt').exists()
    assert (b.game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
    assert (b.game/'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'


def test_manifest_only_stays_editable_when_versions_missing(pair, tmp_path):
    a, b = pair
    write(a, 'Mods/Existing.dll', b'only local')
    profile = sharing(a).capture(a.installation, '仅清单')
    path = tmp_path/'only.json'
    path.write_text(json.dumps(a.context.profiles.export(profile['profile_id'])), encoding='utf8')
    service = sharing(b); source = service.grant(str(path))
    preview = service.preview(source['source_handle'], b.installation)
    assert not preview['includes_mods'] and preview['missing_count'] == 1
    result = service.import_bundle(source['source_handle'], preview['preview_hash'], b.installation, '缺文件')
    assert result['dirty'] and not result['selections'][0]['resolved']
    with pytest.raises(ProfileError):
        apply(b, service, result)


def _bundle(pair, tmp_path):
    app, _ = pair
    release = publish(app, {'Mods/One/a.png': b'one'}, 'One', '1.0')
    enable(app, release)
    service = sharing(app)
    profile = service.capture(app.installation, '分享方案')
    source = tmp_path/'source.zip'; service.export_bundle(profile['profile_id'], str(source))
    with zipfile.ZipFile(source) as archive:
        return [(row.filename, archive.read(row)) for row in archive.infolist()]


@pytest.mark.parametrize('attack', ['traversal', 'tampered', 'script', 'credentials', 'symlink', 'extra'])
def test_bundle_rejects_unsafe_or_tampered_content(pair, tmp_path, attack):
    entries = _bundle(pair, tmp_path)
    manifest = json.loads(entries[0][1])
    if attack == 'traversal':
        entries.append(('../outside.txt', b'bad'))
    elif attack == 'tampered':
        entries[-1] = (entries[-1][0], b'edited bytes')
    elif attack in {'script', 'credentials'}:
        manifest['releases'][0]['files'][0]['relative_path'] = 'evil.exe' if attack == 'script' else 'credentials.json'
        entries[0] = ('modpack.json', json.dumps(manifest).encode())
    elif attack == 'extra':
        entries.append(('extra.txt', b'unlisted'))
    path = tmp_path/(attack+'.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            if attack == 'symlink' and name.startswith('blobs/'):
                info.create_system = 3; info.external_attr = 0o120777 << 16
            archive.writestr(info, data)
    service = sharing(pair[1]); source = service.grant(str(path))
    with pytest.raises(ValueError):
        service.preview(source['source_handle'], pair[1].installation)
    assert not pair[1].context.store.list('ModRelease')


def test_overwrite_output_and_changed_source_are_refused(pair, tmp_path):
    app, _ = pair
    write(app, 'Mods/Current.dll', b'current')
    service = sharing(app); profile = service.capture(app.installation, '方案')
    path = tmp_path/'pack.zip'; service.export_bundle(profile['profile_id'], str(path))
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        service.export_bundle(profile['profile_id'], str(path))
    assert path.read_bytes() == original
    source = service.grant(str(path)); preview = service.preview(source['source_handle'], app.installation)
    path.write_bytes(original+b'changed')
    with pytest.raises(ProfileError, match='PROFILE_SOURCE_CHANGED'):
        service.import_bundle(source['source_handle'], preview['preview_hash'], app.installation, 'changed')


def test_full_pack_over_128_files_uses_ordinary_batch(pair, tmp_path):
    app, receiver = pair
    release = app.publish({f'Mods/Big/{index}.png': bytes([index % 256]) for index in range(140)}, 'Big')
    enable(app, release)
    service = sharing(app); profile = service.capture(app.installation, '资源方案')
    path = tmp_path/'large.zip'; service.export_bundle(profile['profile_id'], str(path))
    other = sharing(receiver); source = other.grant(str(path)); preview = other.preview(source['source_handle'], receiver.installation)
    result = other.import_bundle(source['source_handle'], preview['preview_hash'], receiver.installation, '导入资源')
    apply(receiver, other, result)
    assert len(list((receiver.game/'Mods/Big').glob('*.png'))) == 140


def test_upgrade_downgrade_removes_obsolete_files_keeps_pinned_profile(pair):
    app, _ = pair
    old = publish(app, {'Mods/Example/main.png': b'old', 'Mods/Example/obsolete.png': b'remove', 'README.txt': b'old notes'}, 'Example', '1.0')
    new = publish(app, {'Mods/Example/main.png': b'new', 'Mods/Example/new.png': b'add', 'README.txt': b'new notes'}, 'Example', '2.0')
    enable(app, old)
    pinned = sharing(app).capture(app.installation, '锁定旧版')
    updates = ModUpdateService(app.context.mod_workspace)
    preview = updates.preview(app.installation, new['release_id'], new['manifest_hash'])
    assert len(preview['replacements']) == 1 and preview['replacements'][0]['comparison'] == 'upgrade'
    result = updates.apply(app.installation, new['release_id'], new['manifest_hash'], preview['snapshot_id'],
        [item['id'] for item in preview['replacements']], str(uuid4()))
    assert result['completed'] and (app.game/'Mods/Example/main.png').read_bytes() == b'new'
    assert not (app.game/'Mods/Example/obsolete.png').exists()
    assert app.context.profiles.get(pinned['profile_id'])['selections'][0]['release_id'] == old['release_id']
    apply(app, sharing(app), pinned)
    assert (app.game/'Mods/Example/main.png').read_bytes() == b'old'
    assert not (app.game/'Mods/Example/new.png').exists()


def test_upgrade_failure_recovers_all_files(pair):
    app, _ = pair
    old = publish(app, {'Mods/Example/main.png': b'old', 'Mods/Example/obsolete.png': b'keep'}, 'Example', '1.0')
    new = publish(app, {'Mods/Example/main.png': b'new'}, 'Example', '2.0')
    enable(app, old)
    updates = ModUpdateService(app.context.mod_workspace)
    preview = updates.preview(app.installation, new['release_id'], new['manifest_hash'])
    def fail(point, *args):
        if point == 'ordinary.file_applied':
            raise OSError('synthetic version replacement failure')
    app.context.deployments._fault = fail
    result = updates.apply(app.installation, new['release_id'], new['manifest_hash'], preview['snapshot_id'],
        [item['id'] for item in preview['replacements']], str(uuid4()))
    assert result.get('error_code'), result
    assert (app.game/'Mods/Example/main.png').read_bytes() == b'old'
    assert (app.game/'Mods/Example/obsolete.png').read_bytes() == b'keep'


@pytest.mark.parametrize('left,right,expected', [('v1.1.70','1.1.60','upgrade'), ('3.1','3.2','downgrade'),
    ('1.0','1.0.0','same'), ('作者版','未知版本','unknown'), (None,'1.0','unknown')])
def test_version_comparison(left, right, expected):
    assert compare_versions(left, right) == expected


def test_registered_profile_share_and_update_commands_through_desktop_api(pair, tmp_path):
    app, receiver = pair
    release = publish(app, {'Mods/Example/asset.png': b'asset'}, 'Example', '1.0')
    enable(app, release)
    profile = app.call('profile.capture', dict(installation_id=app.installation, name='API 方案'))
    path = tmp_path/'api-pack.zip'
    app.context.choose_profile_share_destination = lambda kind: str(path)
    result = app.call('profile.share_export', dict(profile_id=profile['profile_id'], kind='bundle'))
    assert result['saved'], result
    receiver.context.choose_profile_share_source = lambda: str(path)
    source = receiver.call('profile.share_choose')
    preview = receiver.call('profile.share_preview', dict(source_handle=source['source_handle'], installation_id=receiver.installation))
    imported = receiver.call('profile.share_import', dict(source_handle=source['source_handle'], preview_hash=preview['preview_hash'],
        installation_id=receiver.installation, name='API 导入'))
    view = receiver.call('mod.workspace', dict(installation_id=receiver.installation))
    applied = receiver.call('profile.apply_saved', dict(profile_id=imported['profile_id'], expected_revision=imported['revision'],
        snapshot_id=view['snapshot_id'], idempotency_key=str(uuid4())))
    assert applied['completed'], applied
    new = publish(receiver, {'Mods/Example/asset.png': b'newer'}, 'Example', '2.0')
    update = receiver.call('package.update_preview', dict(installation_id=receiver.installation,
        release_id=new['release_id'], manifest_hash=new['manifest_hash']))
    assert update['replacements'], update
    result = receiver.call('package.update_apply', dict(installation_id=receiver.installation,
        release_id=new['release_id'], manifest_hash=new['manifest_hash'], snapshot_id=update['snapshot_id'],
        replaced_ids=[row['id'] for row in update['replacements']], idempotency_key=str(uuid4())))
    assert result['completed'] and (receiver.game/'Mods/Example/asset.png').read_bytes() == b'newer'


def test_download_grant_is_limited_to_the_private_nexus_receipt_shape(pair):
    app, _ = pair
    importer = app.context.imports
    root = app.context.paths.root/'downloads'/'nexus'/uuid4().hex
    root.mkdir(parents=True)
    path = root/'download.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('Mods/Example/asset.png', b'owned download')
    with pytest.raises(ValueError, match='IMPORT_SOURCE_PROTECTED_LIBRARY'):
        importer.grant('zip', str(path))
    source = importer.grant_download('zip', str(path))
    assert importer.preview(source['source_handle'])['can_commit']
    with pytest.raises(ValueError, match='IMPORT_SOURCE_PROTECTED_LIBRARY'):
        importer.grant_download('zip', str(app.context.paths.database))
    source = importer.grant_download('zip', str(path))
    assert importer.revoke_download(source['source_handle']) is True
    assert path.exists()
    with pytest.raises(ValueError, match='IMPORT_SOURCE_EXPIRED'):
        importer.preview(source['source_handle'])
    ordinary = app.root/'ordinary.zip'
    ordinary.write_bytes(path.read_bytes())
    regular = importer.grant('zip', str(ordinary))
    assert importer.revoke_download(regular['source_handle']) is False
    assert importer.preview(regular['source_handle'])['can_commit']


def test_capture_publishes_metadata_without_rehashing_existing_library(pair, monkeypatch):
    app, _ = pair
    release = publish(app, {'Mods/Example/main.png': b'owned bytes'}, 'Example', '1.0')
    enable(app, release)
    monkeypatch.setattr(PackageRepository, 'verify', lambda *args, **kwargs: pytest.fail('Capturing a saved list must not reread library bytes'))
    result = sharing(app).capture(app.installation, '快速捕获')
    assert not result['dirty'] and result['selections'][0]['manifest_hash'] == release['manifest_hash']


def test_regular_profile_commit_checks_each_file_once_and_still_detects_corruption(pair, monkeypatch):
    app, _ = pair
    release = publish(app, {'Mods/Example/main.png': b'owned bytes'}, 'Example', '1.0')
    profile = app.context.profiles.create(app.installation, '正常提交')
    profile = app.context.profiles.edit(profile['profile_id'], profile['revision'], {'op': 'set_selection', 'selection': {
        'release_id': release['release_id'], 'manifest_hash': release['manifest_hash'], 'selected': True}})
    verify, calls = PackageRepository.verify, []
    def observed(repository, digest, *args, **kwargs):
        calls.append(digest); return verify(repository, digest, *args, **kwargs)
    monkeypatch.setattr(PackageRepository, 'verify', observed)
    committed = app.context.profiles.commit(profile['profile_id'], profile['revision'])
    assert len(calls) == 1
    path = app.context.paths.root/'library'/calls[0][:2]/calls[0]
    path.write_bytes(b'changed raw')  # same length; normal commit must hash
    with pytest.raises(ProfileError, match='PROFILE_UNRESOLVED'):
        app.context.profiles.commit(committed['profile_id'], committed['revision'])


@pytest.mark.parametrize('change', ['missing', 'resized'])
def test_capture_rejects_missing_or_resized_library_without_partial_profile(pair, change):
    app, _ = pair
    release = publish(app, {'Mods/Example/main.png': b'owned bytes'}, 'Example', '1.0')
    enable(app, release)
    store = app.context.store
    digest = next(row['source_hash'] for row in PackageRepository(store, app.context.paths.root/'library').release(release['release_id'])['files'])
    path = app.context.paths.root/'library'/digest[:2]/digest
    if change == 'missing': path.unlink()
    else: path.write_bytes(b'resized bytes')
    before = app.context.profiles.list(app.installation)['profiles']
    with pytest.raises(ProfileError, match='PROFILE_UNRESOLVED'):
        sharing(app).capture(app.installation, '不能发布')
    assert app.context.profiles.list(app.installation)['profiles'] == before


def test_fast_capture_does_not_let_modified_library_bytes_escape_export(pair, tmp_path):
    app, _ = pair
    release = publish(app, {'Mods/Example/main.png': b'good'}, 'Example', '1.0')
    enable(app, release)
    service = sharing(app); profile = service.capture(app.installation, '固定版本')
    file = PackageRepository(app.context.store, app.context.paths.root/'library').release(release['release_id'])['files'][0]
    digest = file['source_hash']; (app.context.paths.root/'library'/digest[:2]/digest).write_bytes(b'evil')
    target = tmp_path/'must-not-export.zip'
    with pytest.raises(ProfileError, match='PROFILE_SOURCE_CHANGED'):
        service.export_bundle(profile['profile_id'], str(target))
    assert not target.exists()


def test_share_includes_secret_named_texture_and_mod_documents_configs_resources(pair, tmp_path):
    app, receiver = pair
    payloads = {'Mods/Example/mod.json': b'{}', 'Mods/Example/Reference/secret_admirer.png': b'texture',
        'Mods/Example/settings.toml': b'[display]\nscale=2', 'Mods/Example.cfg': b'[display]\nscale=2',
        'Mods/Example/docs/guide.md': b'Mod documentation', 'Mods/Example/assets/texture.dds': b'asset bytes',
        'README.txt': b'library attachment'}
    release = publish(app, payloads, '合法附带文件', '1.0')
    enable(app, release)
    service = sharing(app); profile = service.capture(app.installation, '完整合法方案')
    bundle = tmp_path/'complete.zip'; result = service.export_bundle(profile['profile_id'], str(bundle))
    assert result['file_count'] == len(payloads)
    other = sharing(receiver); source = other.grant(str(bundle)); preview = other.preview(source['source_handle'], receiver.installation)
    imported = other.import_bundle(source['source_handle'], preview['preview_hash'], receiver.installation, '导入合法附带文件')
    apply(receiver, other, imported)
    for name, value in payloads.items():
        if name == 'README.txt': assert not (receiver.game/name).exists()
        else: assert (receiver.game/name).read_bytes() == value


def test_mod_config_support_does_not_overwrite_unknown_existing_config(pair):
    app, _ = pair
    release = publish(app, {'Mods/Example/settings.toml': b'package defaults'}, 'Example', '1.0')
    write(app, 'Mods/Example/settings.toml', b'unknown personal configuration')
    with pytest.raises(PlanRejected, match='PLAN_UNMANAGED_CONFLICT'):
        enable(app, release)
    assert (app.game/'Mods/Example/settings.toml').read_bytes() == b'unknown personal configuration'


@pytest.mark.parametrize('path,kind,lifecycle,reason', [
    ('Example/credentials.json', 'mods', 'immutable', 'credential_file'),
    ('Example/.env', 'mods', 'immutable', 'credential_file'),
    ('Example/settings.secrets.toml', 'mods', 'shared_config', 'credential_file'),
    ('Example/install.ps1', 'mods', 'immutable', 'script'),
    ('Example/nested.zip', 'mods', 'immutable', 'nested_archive'),
    ('ProbablyStolen.exe', 'game_root', 'unowned', 'script'),
    ('game.assets', 'game_root', 'immutable', 'game_or_unsupported_target'),
    ('save.bin', 'user_data', 'mutable_data', 'game_or_unsupported_target'),
])
def test_share_rejection_reports_the_exact_file_category(path, kind, lifecycle, reason):
    with pytest.raises(ProfileError) as caught:
        safe_file(dict(relative_path=path, target_root_kind=kind, lifecycle=lifecycle))
    assert caught.value.code == 'PROFILE_BUNDLE_UNSAFE'
    assert caught.value.issues == [dict(relative_path=path, reason=reason)]
