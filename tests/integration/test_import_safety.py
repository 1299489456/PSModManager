"""CASE-023/024: real local sources, private SQLite and actual metadata worker."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import struct
import zipfile
import zlib

import pytest

from backend.app.composition import DesktopContext
from backend.app.api import DesktopApi
from backend.app.paths import resolve_paths
from backend.catalog import archive_policy as policy
from backend.catalog.imports import ImportService
from backend.contracts import ENTITY_MODELS
from backend.games.probably_stolen.layout import identity
from backend.storage.package_repository import PackageRepository
from backend.scanning.worker_client import MetadataWorker
from tests.integration.test_metadata_worker import dll
from tests.integration.test_package_adoption import profile


@pytest.fixture
def app(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private-data')))
    context.initialize()
    service = ImportService(context.paths)
    try:
        yield context, service
    finally:
        service.close()
        context.close()


def archive(tmp_path, entries, compression=zipfile.ZIP_STORED):
    path = tmp_path / 'source.zip'
    with zipfile.ZipFile(path, 'w', compression=compression) as output:
        for name, content in entries:
            output.writestr(name, content)
    return path


def preview(service, path, kind='zip', **kwargs):
    grant = service.grant(kind, str(path))
    assert set(grant) == {'source_handle', 'display_name'}
    return service.preview(grant['source_handle'], **kwargs)


def empty_failure(context, service):
    assert not service._previews
    root = context.paths.root / 'imports'
    assert not root.exists() or not list(root.iterdir())
    for entity in ('ModPackage', 'ModRelease', 'FileEntry', 'Profile', 'DeploymentGeneration'):
        assert not context.store.list(entity)


@pytest.mark.parametrize('kind', ['dll', 'directory', 'zip'])
def test_case024_installed_mod_source_imports_readonly_copy_without_adoption(app, tmp_path, dll, kind):
    context, service = app
    game = tmp_path / 'synthetic-game'
    mods = game / 'Mods'; mods.mkdir(parents=True)
    installed = mods / '已安装 100%.dll'
    shutil.copyfile(dll, installed)
    (game / 'UserData').mkdir()
    (game / 'UserData/save.bin').write_bytes(b'untouched synthetic save')
    if kind == 'zip':
        source = archive(game, [('Mods/' + installed.name, installed.read_bytes())])
    else:
        source = mods if kind == 'directory' else installed
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    fixtures['Installation'].update(canonical_root=str(game), file_identity=identity(game))
    context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    before = {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in game.rglob('*') if p.is_file()}
    context.choose_import_source = lambda chosen_kind: str(source) if chosen_kind == kind else None
    api = DesktopApi(context)
    selected = api.dispatch(dict(schema_version=1, request_id='installed-source', command='package.choose', payload={'kind': kind}))
    assert selected['ok'] and selected['data']['source_handle'], selected
    result = context.imports.preview(selected['data']['source_handle'])
    assert result['can_commit'] and result['files'][0]['parser_status'] == 'managed'
    committed = context.imports.commit(result['preview_id'], result['preview_hash'], '已安装模组副本')
    assert committed['enabled'] is False and committed['deployed'] is False
    repository = PackageRepository(context.store, context.paths.root / 'library')
    release = repository.release(committed['release_id'], committed['manifest_hash'])
    assert release['release']['manifest_hash'] == committed['manifest_hash']
    assert context.store.connection.execute('SELECT count(*) FROM package_adoptions').fetchone()[0] == 0
    for entity in ('BaselineSnapshot', 'DeploymentGeneration', 'Profile'):
        assert not context.store.list(entity)
    for artifact in context.store.list('Artifact'):
        copied = repository.path(artifact.sha256)
        assert copied.stat().st_nlink == 1 and not os.path.samefile(copied, installed)
        assert copied.read_bytes() == installed.read_bytes()
    after = {p.relative_to(game).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in game.rglob('*') if p.is_file()}
    assert before == after
    assert not (dll.parent / 'executed.txt').exists()
    (tmp_path / 'source-preservation.json').write_text(json.dumps({'kind': kind, 'before': before, 'after': after, 'adoptions': 0, 'deployments': 0}, indent=2), encoding='utf-8')


@pytest.mark.parametrize('location', ['library', 'reference'])
@pytest.mark.parametrize('kind', ['zip', 'directory'])
def test_case023_private_library_and_reference_sources_remain_protected(app, tmp_path, monkeypatch, location, kind):
    context, service = app
    root = context.paths.root / 'source' if location == 'library' else tmp_path / 'reference'
    root.mkdir()
    if location == 'reference':
        monkeypatch.setattr('backend.catalog.imports.REFERENCE_ROOT', root)
    source = archive(root, [('asset.txt', b'resource')]) if kind == 'zip' else root
    result = DesktopApi._import_result(lambda: service.grant(kind, str(source)))
    assert result['error_code'] == 'IMPORT_SOURCE_PROTECTED_' + location.upper()
    assert result['can_commit'] is False
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)
    assert '请选择' in result['message'] or '请在包库中选择' in result['message']
    empty_failure(context, service)


@pytest.mark.parametrize('name', ['../escape.txt', 'a/../../escape.txt', '/absolute.txt',
    'C:/outside.txt', 'C:outside.txt', '\\\\server\\share\\outside.txt', 'a.txt:ads',
    'NUL.txt', 'con', 'COM1.bin', 'LPT².dat', 'trailing /x.txt', 'trailing./x.txt',
    'a//b.txt', 'a/./b.txt', '..\\escape.txt'])
def test_case023_zip_windows_path_attacks(app, tmp_path, name):
    context, service = app
    sentinel = tmp_path / 'escape.txt'; sentinel.write_bytes(b'keep')
    source = archive(tmp_path, [(name, b'attack')])
    with pytest.raises(ValueError):
        preview(service, source)
    assert sentinel.read_bytes() == b'keep'
    empty_failure(context, service)


@pytest.mark.parametrize('names', [('a.txt', 'A.TXT'), ('caf\u00e9.txt', 'cafe\u0301.txt'),
    ('a', 'a/b.txt'), ('a/b.txt', 'a\\b.txt')])
def test_case023_zip_alias_conflicts(app, tmp_path, names):
    context, service = app
    with pytest.raises(ValueError, match='IMPORT_NAME_CONFLICT'):
        preview(service, archive(tmp_path, [(name, b'x') for name in names]))
    empty_failure(context, service)


def test_case023_zip_symbolic_link_attribute(app, tmp_path):
    context, service = app
    link = zipfile.ZipInfo('link.txt'); link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ValueError, match='IMPORT_LINK_REFUSED'):
        preview(service, archive(tmp_path, [(link, b'../../outside')]))
    empty_failure(context, service)


def test_case023_raw_zip_nul_name_refused(app, tmp_path):
    context, service = app
    source = archive(tmp_path, [('aXb.txt', b'asset')])
    source.write_bytes(source.read_bytes().replace(b'aXb.txt', b'a\x00b.txt'))
    with pytest.raises(ValueError, match='PATH_INVALID'):
        preview(service, source)
    empty_failure(context, service)


def test_case023_source_hardlink_refused(app, tmp_path):
    context, service = app
    source = tmp_path / 'original.dll'; source.write_bytes(b'original')
    alias = tmp_path / 'alias.dll'; os.link(source, alias)
    with pytest.raises(ValueError):
        preview(service, alias, 'dll')
    assert source.read_bytes() == b'original'
    empty_failure(context, service)


def test_case023_directory_hardlink_refused(app, tmp_path):
    context, service = app
    outside = tmp_path / 'outside.txt'; outside.write_bytes(b'keep')
    source = tmp_path / 'source'; source.mkdir()
    os.link(outside, source / 'alias.txt')
    with pytest.raises(ValueError, match='LINK_REFUSED'):
        preview(service, source, 'directory')
    assert outside.read_bytes() == b'keep'
    empty_failure(context, service)


def test_case023_directory_junction_refused(app, tmp_path):
    context, service = app
    outside = tmp_path / 'outside'; outside.mkdir()
    sentinel = outside / 'keep.txt'; sentinel.write_bytes(b'keep')
    source = tmp_path / 'source'; source.mkdir()
    junction = source / 'link'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        with pytest.raises(ValueError):
            preview(service, source, 'directory')
        assert sentinel.read_bytes() == b'keep'
        empty_failure(context, service)
    finally:
        junction.rmdir()


def test_case023_staging_replacement_race_is_pinned(app, tmp_path, monkeypatch):
    context, service = app
    outside = tmp_path / 'outside'; outside.mkdir()
    sentinel = outside / 'keep.txt'; sentinel.write_bytes(b'keep')
    attempts = []
    original = service._copy
    def attack(reader, target, expected_size, budget):
        stage = target.parent
        with pytest.raises(OSError) as denied:
            stage.rename(stage.with_name(stage.name + '-moved'))
        assert denied.value.winerror in (5, 32, 33)
        attempts.append(denied.value.winerror)
        return original(reader, target, expected_size, budget)
    monkeypatch.setattr(service, '_copy', attack)
    result = preview(service, archive(tmp_path, [('asset.txt', b'safe')]))
    assert attempts and result['total'] == 1
    assert sentinel.read_bytes() == b'keep'
    assert list(outside.iterdir()) == [sentinel]


@pytest.mark.parametrize('limit,value,entries,compression,error', [
    ('MAX_RATIO', 2, [('bomb.txt', b'0' * 4096)], zipfile.ZIP_DEFLATED, 'RATIO'),
    ('MAX_ENTRIES', 1, [('a.txt', b'a'), ('b.txt', b'b')], zipfile.ZIP_STORED, 'ENTRY'),
    ('MAX_FILE_BYTES', 3, [('a.txt', b'1234')], zipfile.ZIP_STORED, 'FILE'),
    ('MAX_TOTAL_BYTES', 5, [('a.txt', b'123'), ('b.txt', b'456')], zipfile.ZIP_STORED, 'TOTAL'),
    ('MAX_ARCHIVE_BYTES', 20, [('a.txt', b'a')], zipfile.ZIP_STORED, 'ARCHIVE'),
])
def test_case024_archive_limits(app, tmp_path, monkeypatch, limit, value, entries, compression, error):
    context, service = app
    monkeypatch.setattr(policy, limit, value)
    with pytest.raises(ValueError, match='IMPORT_' + error + '_LIMIT'):
        preview(service, archive(tmp_path, entries, compression))
    empty_failure(context, service)


@pytest.mark.parametrize('name,content,error', [('nested.zip', b'x', 'NESTED_ARCHIVE'),
    ('innocent.dat', b'PK\x03\x04payload', 'NESTED_ARCHIVE'),
    ('run.PS1', b'echo bad', 'SCRIPT_REFUSED'), ('run.cmd', b'bad', 'SCRIPT_REFUSED'),
    ('run.js', b'bad', 'SCRIPT_REFUSED')])
def test_case024_nested_archives_and_scripts(app, tmp_path, name, content, error):
    context, service = app
    with pytest.raises(ValueError, match=error):
        preview(service, archive(tmp_path, [(name, content)]))
    empty_failure(context, service)


def test_case024_unsupported_formats(app, tmp_path):
    context, service = app
    source = tmp_path / 'source.rar'; source.write_bytes(b'Rar!')
    with pytest.raises(ValueError, match='FORMAT_UNSUPPORTED'):
        service.grant('rar', str(source))
    source = archive(tmp_path, [('asset.txt', b'text')], zipfile.ZIP_BZIP2)
    with pytest.raises(ValueError, match='FORMAT_UNSUPPORTED'):
        preview(service, source)
    empty_failure(context, service)


@pytest.mark.parametrize('mode', ['truncated', 'crc'])
def test_case024_damaged_archive_cleanup(app, tmp_path, mode):
    context, service = app
    source = archive(tmp_path, [('asset.txt', b'unique payload')])
    data = source.read_bytes()
    source.write_bytes(data[:-10] if mode == 'truncated' else data.replace(b'unique payload', b'broken payload'))
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        preview(service, source)
    empty_failure(context, service)


@pytest.mark.parametrize('mode', ['disk', 'cancel'])
def test_case024_mid_copy_failure_cleanup(app, tmp_path, monkeypatch, mode):
    context, service = app
    original = service._copy
    copied = []
    def failure(reader, target, size, budget):
        result = original(reader, target, size, budget)
        copied.append(target)
        if mode == 'disk':
            raise OSError(errno.ENOSPC, 'synthetic disk full')
        raise InterruptedError('synthetic cancellation')
    monkeypatch.setattr(service, '_copy', failure)
    with pytest.raises(OSError):
        preview(service, archive(tmp_path, [('asset.txt', b'asset')]))
    assert copied
    empty_failure(context, service)


def test_case024_real_worker_rejects_bad_dll(app, tmp_path):
    context, service = app
    source = tmp_path / 'broken.dll'; source.write_bytes(b'not a PE')
    with pytest.raises(ValueError, match='METADATA_REJECTED'):
        preview(service, source, 'dll')
    empty_failure(context, service)


def test_case024_cancellation_reaches_stream_budget(app, tmp_path, monkeypatch):
    context, service = app
    monkeypatch.setattr(policy, 'CHUNK', 4)
    calls = []
    def cancel():
        calls.append(True)
        if len(calls) == 5:
            raise InterruptedError('cancel during bounded stream')
    with pytest.raises(InterruptedError):
        preview(service, archive(tmp_path, [('asset.txt', b'asset' * 20)]), check=cancel)
    assert len(calls) == 5
    empty_failure(context, service)


def test_case024_commit_disk_failure_leaves_no_indexed_half_package(app, tmp_path, monkeypatch):
    context, service = app
    result = preview(service, archive(tmp_path, [('a.txt', b'first'), ('b.txt', b'second')]))
    original = PackageRepository.copy
    calls = []
    def full(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, 'synthetic full library disk')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PackageRepository, 'copy', full)
    with pytest.raises(OSError, match='full library disk'):
        service.commit(result['preview_id'], result['preview_hash'], 'name')
    assert len(calls) == 2
    assert not context.store.list('ModRelease')
    assert not context.store.list('ModPackage')
    assert not context.store.list('FileEntry')
    monkeypatch.setattr(PackageRepository, 'copy', original)
    with pytest.raises(ValueError, match='PREVIEW_EXPIRED'):
        service.page(result['preview_id'])
    result = preview(service, tmp_path / 'source.zip')
    assert service.commit(result['preview_id'], result['preview_hash'], 'name')['file_count'] == 2


def test_case023_worker_report_hardlink_cannot_overwrite_sentinel(app, tmp_path, dll, monkeypatch):
    context, service = app
    sentinel = tmp_path / 'sentinel.txt'; sentinel.write_bytes(b'keep')
    source = tmp_path / 'good.dll'; shutil.copyfile(dll, source)
    original = MetadataWorker.inspect
    called = []
    def inject(self, root, relative, **kwargs):
        result = original(self, root, relative, **kwargs)
        assert result['status'] == 'managed', result
        os.link(sentinel, root / (relative + '.metadata.json'))
        called.append(True)
        return result
    monkeypatch.setattr(MetadataWorker, 'inspect', inject)
    with pytest.raises(FileExistsError):
        preview(service, source, 'dll')
    assert called and sentinel.read_bytes() == b'keep'
    empty_failure(context, service)


def test_case023_library_shard_replacement_is_pinned(app, tmp_path, monkeypatch):
    context, service = app
    sentinel = tmp_path / 'sentinel.txt'; sentinel.write_bytes(b'keep')
    result = preview(service, archive(tmp_path, [('asset.txt', b'asset')]))
    original = PackageRepository.copy
    attempted = []
    def attack(self, root, file, check):
        shard = self.path(file.sha256).parent
        with pytest.raises(OSError) as denied:
            shard.rename(shard.with_name(shard.name + '-moved'))
        assert denied.value.winerror in (5, 32, 33)
        attempted.append(True)
        return original(self, root, file, check)
    monkeypatch.setattr(PackageRepository, 'copy', attack)
    assert service.commit(result['preview_id'], result['preview_hash'], 'name')['file_count'] == 1
    assert attempted and sentinel.read_bytes() == b'keep'


@pytest.mark.parametrize('kind', ['dll', 'directory', 'zip'])
def test_import_three_sources_real_worker_library_and_profile(app, tmp_path, dll, kind):
    context, service = app
    name = '中文 空格 % 模组.dll'
    if kind == 'dll':
        source = tmp_path / name; shutil.copyfile(dll, source)
    elif kind == 'directory':
        source = tmp_path / 'source'; (source / 'Mods').mkdir(parents=True)
        shutil.copyfile(dll, source / 'Mods' / name)
    else:
        source = archive(tmp_path, [('Mods/' + name, dll.read_bytes())])
    before = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()} if source.is_dir() else {source: source.read_bytes()}
    result = preview(service, source, kind)
    assert result['files'][0]['path'].endswith(name)
    assert result['files'][0]['parser_status'] == 'managed'
    stage = service._previews[result['preview_id']]['stage']
    metadata = json.loads(next(stage.glob('*.metadata.json')).read_text())
    (tmp_path / 'worker-evidence.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    assert metadata['process']['appcontainer'] and metadata['process']['capabilities'] == 0
    assert metadata['process']['cleanup_remaining'] == 0
    committed = service.commit(result['preview_id'], result['preview_hash'], '示例包', '1.0')
    assert not committed['enabled'] and not committed['deployed']
    assert service.commit(result['preview_id'], result['preview_hash'], '示例包', '1.0') == committed
    again = preview(service, source, kind)
    assert service.commit(again['preview_id'], again['preview_hash'], '示例包', '1.0') == committed
    assert len(context.store.list('ModRelease')) == 1
    assert not context.store.list('Profile') and not context.store.list('DeploymentGeneration')
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    game = tmp_path / 'synthetic-game'; game.mkdir()
    fixtures['Installation'].update(canonical_root=str(game), file_identity=identity(game))
    install = context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    saved = context.store.profiles.create(profile(install.installation_id, {'release': committed}))
    assert context.store.get('Profile', saved.profile_id) == saved
    assert all(p.read_bytes() == contents for p, contents in before.items())
    assert not (dll.parent / 'executed.txt').exists()
    assert not list(game.iterdir())


@pytest.mark.parametrize('compression', [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_case024_forged_small_size_and_matching_prefix_crc(app, tmp_path, compression):
    context, service = app
    source = archive(tmp_path, [('asset.txt', b'abcdef')], compression)
    raw = bytearray(source.read_bytes())
    central = raw.index(b'PK\x01\x02')
    # Both size and CRC are forged consistently; ZipExtFile alone can truncate
    # the output to this prefix and accept it. The full-stream policy must not.
    crc = zlib.crc32(b'a')
    struct.pack_into('<L', raw, 14, crc)
    struct.pack_into('<L', raw, 22, 1)
    struct.pack_into('<L', raw, central + 16, crc)
    struct.pack_into('<L', raw, central + 24, 1)
    source.write_bytes(raw)
    with pytest.raises(ValueError, match='IMPORT_SIZE_MISMATCH'):
        preview(service, source)
    empty_failure(context, service)


def test_preview_pagination_unknown_ownership_and_no_paths(app, tmp_path):
    context, service = app
    result = preview(service, archive(tmp_path, [(f'中文 {i}%.txt', b'resource') for i in range(25)]))
    assert result['total'] == 25 and len(result['files']) == 20 and result['next_offset'] == 20
    last = service.page(result['preview_id'], 20)
    assert len(last['files']) == 5 and last['next_offset'] is None
    assert all(row['ownership'] == 'unknown' for row in result['files'])
    assert str(tmp_path) not in json.dumps(result)
    assert not context.store.list('ModRelease')


def test_preview_hash_and_staged_bytes_tampering_rejected(app, tmp_path):
    context, service = app
    result = preview(service, archive(tmp_path, [('asset.txt', b'asset')]))
    with pytest.raises(ValueError, match='PREVIEW_CHANGED'):
        service.commit(result['preview_id'], '0' * 64, 'name')
    stage = service._previews[result['preview_id']]['stage']
    (stage / '0.blob').write_bytes(b'changed')
    with pytest.raises(ValueError):
        service.commit(result['preview_id'], result['preview_hash'], 'name')
    assert not context.store.list('ModRelease')


def test_source_changed_and_expired_handles(app, tmp_path):
    context, service = app
    source = archive(tmp_path, [('a.txt', b'a')])
    handle = service.grant('zip', str(source))['source_handle']
    source.write_bytes(source.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        service.preview(handle)
    service._sources[handle]['expires'] = 0
    with pytest.raises(ValueError, match='SOURCE_EXPIRED'):
        service.preview(handle)
    empty_failure(context, service)


def test_preview_expiration_cleans_only_owned_staging(app, tmp_path):
    context, service = app
    result = preview(service, archive(tmp_path, [('asset.txt', b'asset')]))
    sentinel = context.paths.root / 'keep.txt'; sentinel.write_bytes(b'keep')
    service._previews[result['preview_id']]['expires'] = 0
    with pytest.raises(ValueError, match='PREVIEW_EXPIRED'):
        service.page(result['preview_id'])
    assert sentinel.read_bytes() == b'keep'
    empty_failure(context, service)


@pytest.mark.parametrize('offset,limit', [(-1, 20), (0, 0), (0, 21), (True, 1)])
def test_preview_page_bounds(app, tmp_path, offset, limit):
    _, service = app
    result = preview(service, archive(tmp_path, [('asset.txt', b'asset')]))
    with pytest.raises(ValueError, match='REQUEST_INVALID'):
        service.page(result['preview_id'], offset, limit)
