"""CASE-027: real immutable library details and bounded indexed ownership facts."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.catalog.packages import PackageDetailError
from backend.contracts import ENTITY_MODELS
from tests.integration.test_metadata_worker import dll


@pytest.fixture
def app(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private')))
    context.initialize()
    try:
        yield context
    finally:
        context.close()


def publish(context, tmp_path, name, files=None):
    root = tmp_path / name
    for relative, content in (files or {'Mods/shared.png': b'synthetic asset'}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    source = context.imports.grant('directory', str(root))['source_handle']
    preview = context.imports.preview(source)
    return context.imports.commit(preview['preview_id'], preview['preview_hash'], name, '原文-1.0-preview')


def details(context, release, **kwargs):
    return context.packages.detail(release['release_id'], release['manifest_hash'], **kwargs)


def tree(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()}


def test_case027_real_file_ownership_shared_conflicting_and_directory_neighbor(app, tmp_path):
    selected = publish(app, tmp_path, 'Selected')
    shared = publish(app, tmp_path, 'Same', {'mods/SHARED.PNG': b'synthetic asset'})
    different = publish(app, tmp_path, 'Different', {'Mods/shared.png': b'different bytes'})
    neighbor = publish(app, tmp_path, 'Neighbor', {'Mods/neighbor.png': b'synthetic asset'})
    output = details(app, selected)
    file = output['files'][0]
    assert file['target_root_kind'] == 'mods' and file['relative_path'] == 'shared.png'
    assert file['source_hash'] == hashlib.sha256(b'synthetic asset').hexdigest()
    assert file['lifecycle'] == 'immutable' and file['size'] == len(b'synthetic asset')
    assert [row['release_id'] for row in file['shared_owner_releases']] == [shared['release_id']]
    assert [row['release_id'] for row in file['conflicting_owner_releases']] == [different['release_id']]
    assert neighbor['release_id'] not in json.dumps(output)
    assert output['integrity'] == 'verified' and output['deployment_generation'] is None
    assert output['ownership_scope'] == 'library_file_entries_only'
    assert output['owner_integrity'] == 'indexed_claims_not_verified'
    assert '同目录不表示归属' in ''.join(output['warnings'])
    assert output['version_raw'] == '原文-1.0-preview'


def test_case027_details_real_worker_roles_and_no_game_or_database_writes(app, tmp_path, dll):
    source = app.imports.grant('dll', str(dll))['source_handle']
    page = app.imports.preview(source)
    release = app.imports.commit(page['preview_id'], page['preview_hash'], 'Real parsed mod', '0.46D')
    protected = tmp_path / 'synthetic-game'
    protected.mkdir()
    (protected / 'save.dat').write_bytes(b'unchanged synthetic save')
    before_game, before_library = tree(protected), tree(app.paths.root / 'library')
    before_database = app.paths.database.read_bytes(), app.paths.database.stat().st_mtime_ns
    result = details(app, release)
    assert result['roles'] == ['melon_mod'] and result['files'][0]['roles'] == ['melon_mod']
    assert result['files'][0]['relative_path'] == dll.name
    assert result['version_raw'] == '0.46D'
    assert before_game == tree(protected)
    assert before_library == tree(app.paths.root / 'library')
    assert before_database == (app.paths.database.read_bytes(), app.paths.database.stat().st_mtime_ns)
    assert str(app.paths.root) not in json.dumps(result)
    assert not app.store.list('DeploymentGeneration')


def test_case027_pagination_has_stable_identity_and_detects_changed_owner_index(app, tmp_path):
    release = publish(app, tmp_path, 'Many', {f'Mods/image-{index:02}.png': str(index).encode() for index in range(25)})
    first = details(app, release)
    second = details(app, release, offset=20)
    assert first['total'] == second['total'] == 25
    assert len(first['files']) == 20 and len(second['files']) == 5
    assert first['next_offset'] == 20 and second['next_offset'] is None
    assert first['detail_hash'] == second['detail_hash']
    assert len({row['file_entry_id'] for row in first['files'] + second['files']}) == 25
    assert len(json.dumps(first, ensure_ascii=False).encode()) < 48 * 1024
    assert details(app, release, offset=25)['files'] == []
    publish(app, tmp_path, 'New owner', {'Mods/image-00.png': b'0'})
    changed = details(app, release)
    assert changed['detail_hash'] != first['detail_hash']
    assert changed['manifest_hash'] == first['manifest_hash']


@pytest.mark.parametrize('offset,limit', [(-1, 20), (False, 20), (0, 0), (0, 21), (0, True), ('0', 20)])
def test_case027_rejects_invalid_pagination(app, tmp_path, offset, limit):
    release = publish(app, tmp_path, 'Valid')
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_INVALID_REQUEST'):
        details(app, release, offset=offset, limit=limit)


def test_case027_wrong_lock_and_missing_release_are_explicit(app, tmp_path):
    release = publish(app, tmp_path, 'Valid')
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_LOCK_CHANGED'):
        app.packages.detail(release['release_id'], '0' * 64)
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_NOT_FOUND'):
        app.packages.detail(str(uuid4()), '0' * 64)
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_INVALID_REQUEST'):
        app.packages.detail('not-a-uuid', release['manifest_hash'])


@pytest.mark.parametrize('mutation', ['corrupt', 'missing'])
def test_case027_damaged_selected_library_cannot_return_verified_details(app, tmp_path, mutation):
    release = publish(app, tmp_path, 'Valid')
    artifact = app.store.list('Artifact')[0]
    path = app.paths.root / 'library' / artifact.library_relative_path
    if mutation == 'missing':
        path.unlink()
    else:
        path.write_bytes(b'corrupted')
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_CORRUPT'):
        details(app, release)


def test_case027_other_owner_integrity_is_not_claimed_and_cancellation_propagates(app, tmp_path):
    selected = publish(app, tmp_path, 'Selected')
    other = publish(app, tmp_path, 'Other', {'Mods/shared.png': b'other'})
    artifact = next(row for row in app.store.list('Artifact') if row.sha256 == hashlib.sha256(b'other').hexdigest())
    (app.paths.root / 'library' / artifact.library_relative_path).write_bytes(b'corrupt')
    result = details(app, selected)
    assert result['files'][0]['conflicting_owner_releases'][0]['release_id'] == other['release_id']
    assert result['owner_integrity'] == 'indexed_claims_not_verified'
    def cancel():
        raise InterruptedError('synthetic cancellation')
    with pytest.raises(InterruptedError):
        details(app, selected, check=cancel)


def test_case027_output_limit_is_explicit_and_smaller_page_preserves_all_owners(app, tmp_path):
    selected = publish(app, tmp_path, 'Selected', {f'Mods/image-{index:02}.png': b'same' for index in range(20)})
    release = app.store.get('ModRelease', selected['release_id']).model_dump(mode='json')
    package = app.store.get('ModPackage', release['package_id']).model_dump(mode='json')
    files = [entry.model_dump(mode='json') for entry in app.store.list('FileEntry')]
    for index in range(12):
        package_id, release_id = str(uuid4()), str(uuid4())
        app.store.put(ENTITY_MODELS['ModPackage'].model_validate(package | dict(package_id=package_id, display_name='Owner ' + str(index) + 'x' * 100)))
        app.store.put(ENTITY_MODELS['ModRelease'].model_validate(release | dict(package_id=package_id, release_id=release_id)))
        for entry in files:
            app.store.put(ENTITY_MODELS['FileEntry'].model_validate(entry | dict(release_id=release_id, file_entry_id=str(uuid4()))))
    with pytest.raises(PackageDetailError, match='PACKAGE_DETAIL_LIMIT'):
        details(app, selected)
    page = details(app, selected, limit=1)
    assert page['total'] == 20 and page['next_offset'] == 1
    assert len(page['files'][0]['shared_owner_releases']) == 12
    assert len(json.dumps(page, ensure_ascii=False).encode()) < 48 * 1024
