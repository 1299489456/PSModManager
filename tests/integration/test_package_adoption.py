"""CASE-022 synthetic adoption through real composition, SQLite and private bytes."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import threading
import time
from uuid import uuid4

import pytest

from backend.app.composition import DesktopContext, DesktopApi
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS, Profile
from backend.games.probably_stolen.layout import identity
from backend.games.probably_stolen.fingerprint import DiscoveryCancelled
from backend.storage.inventory_repository import InventoryRepository
from backend.storage.package_repository import PackageRepository


def test_explicit_orphan_delete_reserves_writer_before_removal(installation, monkeypatch):
    import sqlite3
    from backend.storage import Store
    context, root, _ = installation
    snapshot = preview(installation)
    file = context.store.get('BaselineSnapshot', snapshot['snapshot_id']).files[0]
    repository = PackageRepository(context.store, context.packages.library)
    artifact = repository.copy(root, file, lambda: None)
    context.store.put(artifact)
    other = Store(context.paths.database)
    other.connection.execute('PRAGMA busy_timeout=0')
    original = repository.verify
    observed = []
    def verify_under_reservation(*args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            other.connection.execute('BEGIN IMMEDIATE')
        observed.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(repository, 'verify', verify_under_reservation)
    try:
        with pytest.raises(ValueError, match='confirmation'):
            repository.delete_unreferenced_artifact(artifact.sha256, confirmed=False)
        repository.delete_unreferenced_artifact(artifact.sha256, confirmed=True)
        assert observed == [True]
        assert not repository.path(artifact.sha256).exists()
        assert not context.store.list('Artifact')
    finally:
        other.close()


def test_package_list_job_remains_cancellable(installation, monkeypatch):
    context, _, _ = installation
    adopt(context, preview(installation))
    entered = threading.Event()
    def slow_verify(self, digest, size=None, *, check=lambda: None):
        entered.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            check()
            time.sleep(.01)
        pytest.fail('Cancellation did not reach artifact verification')
    monkeypatch.setattr(PackageRepository, 'verify', slow_verify)
    api = DesktopApi(context)
    result = api.dispatch(dict(schema_version=1, request_id='list', command='package.list', payload={}))
    assert result['ok'] and entered.wait(2)
    response = api.dispatch(dict(schema_version=1, request_id='cancel', command='task.cancel', payload={'task_id':result['task_id']}))
    assert response['ok']
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = context.jobs.snapshot(result['task_id'])
        if state['state'] == 'cancelled':
            break
        time.sleep(.01)
    assert state['state'] == 'cancelled'


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / 'synthetic-game'
    root.mkdir()
    contents = {'Mods/Main.dll': b'synthetic assembly', 'Mods/assets/icon.png': b'asset',
                'UserLibs/Shared.dll': b'shared', 'Mods/mystery.bin': b'unknown',
                'Mods/settings.cfg': b'config', 'Mods/session.log': b'generated'}
    for relative, data in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    context = DesktopContext(resolve_paths(str(tmp_path / 'private-data')))
    context.initialize()
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    fixtures['Installation'].update(canonical_root=str(root), file_identity=identity(root))
    item = context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    try:
        yield context, root, item.installation_id
    finally:
        context.close()


def inventory(context, installation_id, status='complete'):
    repository = InventoryRepository(context.store)
    generation = repository.begin(installation_id, {})
    repository.finish(installation_id, generation, [], status, {}, [])


def preview(fixture):
    context, _, installation_id = fixture
    inventory(context, installation_id)
    return context.packages.preview(installation_id)


def adopt(context, snapshot, paths=None, name='Example', version='1.0'):
    return context.packages.adopt(snapshot['snapshot_id'], snapshot['manifest_hash'],
                                  paths or ['Mods/Main.dll', 'Mods/assets/icon.png'], name, version)


def profile(installation_id, result):
    release = result['release']
    return Profile(profile_id=uuid4(), installation_id=installation_id, revision=0,
                   selections=[dict(release_id=release['release_id'], manifest_hash=release['manifest_hash'])],
                   display_order=[], config_overrides={})


def test_multiple_files_repeat_deduplicates_and_profile_consumes(installation):
    context, root, installation_id = installation
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    snapshot = preview(installation)
    result = adopt(context, snapshot)
    assert adopt(context, snapshot) == result
    assert len(result['files']) == 2
    assert len(context.store.list('ModPackage')) == len(context.store.list('ModRelease')) == 1
    assert len(context.store.list('Artifact')) == 2
    saved = context.store.profiles.create(profile(installation_id, result))
    assert context.store.get('Profile', saved.profile_id) == saved
    assert result['deployment_generation'] is None
    assert not context.store.list('DeploymentGeneration')
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_shared_artifact_preserves_all_owners(installation):
    context, _, installation_id = installation
    snapshot = preview(installation)
    first = adopt(context, snapshot, ['UserLibs/Shared.dll'], 'First')
    second = adopt(context, snapshot, ['UserLibs/Shared.dll'], 'Second')
    assert len(context.store.list('Artifact')) == 1
    current = context.packages.preview(installation_id)
    shared = next(f for f in current['files'] if f['relative_path'] == 'UserLibs/Shared.dll')
    assert set(shared['owners']['matching']) == {first['release']['release_id'], second['release']['release_id']}
    assert shared['ownership'] == 'managed'
    digest = first['files'][0]['source_hash']
    with pytest.raises(ValueError, match='referenced'):
        PackageRepository(context.store, context.packages.library).delete_unreferenced_artifact(digest, confirmed=True)


def test_ownership_requires_adoption_in_each_installation(installation):
    context, root, installation_id = installation
    second_root = root.parent / 'second-synthetic-game'
    shutil.copytree(root, second_root)
    second = context.store.get('Installation', installation_id).model_copy(update={
        'installation_id': uuid4(), 'canonical_root': str(second_root), 'file_identity': identity(second_root)})
    context.store.put(second)
    inventory(context, second.installation_id)
    adopt(context, preview(installation), ['Mods/Main.dll'])
    before = context.packages.preview(second.installation_id)
    entry = next(f for f in before['files'] if f['relative_path'] == 'Mods/Main.dll')
    assert entry['ownership'] == 'external'
    assert entry['owners'] == {'matching': [], 'conflicting': []}
    own = adopt(context, before, ['Mods/Main.dll'])
    after = context.packages.preview(second.installation_id)
    entry = next(f for f in after['files'] if f['relative_path'] == 'Mods/Main.dll')
    assert entry['ownership'] == 'managed'
    assert entry['owners']['matching'] == [own['release']['release_id']]


def test_same_version_different_hash_has_distinct_release(installation):
    context, root, _ = installation
    first = adopt(context, preview(installation), ['Mods/Main.dll'])
    (root / 'Mods/Main.dll').write_bytes(b'new same-version bytes')
    second = adopt(context, preview(installation), ['Mods/Main.dll'])
    assert first['release']['package_id'] == second['release']['package_id']
    assert first['release']['version_raw'] == second['release']['version_raw']
    assert first['release']['release_id'] != second['release']['release_id']
    assert len(context.store.list('ModRelease')) == 2


def test_repeat_from_new_snapshot_retains_both_origins(installation):
    context, _, _ = installation
    first_snapshot = preview(installation)
    first = adopt(context, first_snapshot)
    second_snapshot = preview(installation)
    second = adopt(context, second_snapshot)
    assert first_snapshot['snapshot_id'] != second_snapshot['snapshot_id']
    assert first['release'] == second['release']
    origins = context.store.connection.execute(
        'SELECT snapshot_id FROM package_adoptions WHERE release_id=?',
        (first['release']['release_id'],)).fetchall()
    assert {row['snapshot_id'] for row in origins} == {first_snapshot['snapshot_id'], second_snapshot['snapshot_id']}
    assert len(context.store.list('Artifact')) == 2
    assert len([p for p in context.packages.library.rglob('*') if p.is_file()]) == 2


@pytest.mark.parametrize('path,lifecycle', [('Mods/mystery.bin', 'unowned'), ('Mods/settings.cfg', 'shared_config'), ('Mods/session.log', 'generated')])
def test_unknown_mutable_generated_not_claimed(installation, path, lifecycle):
    context, _, _ = installation
    snapshot = preview(installation)
    entry = next(f for f in snapshot['files'] if f['relative_path'] == path)
    assert entry['lifecycle'] == lifecycle and entry['ownership'] == 'external'
    with pytest.raises(ValueError):
        adopt(context, snapshot, [path])
    assert not context.store.list('ModRelease')


@pytest.mark.parametrize('status', [None, 'partial', 'failed'])
def test_adoption_does_not_wait_for_optional_inventory(installation, status):
    context, _, installation_id = installation
    if status:
        inventory(context, installation_id, status)
    result = context.packages.preview(installation_id)
    assert result['coverage'] == 'complete' and result['total'] == 6
    assert context.store.get('BaselineSnapshot', result['snapshot_id']).scan_generation == 0


def test_cancel_preserves_no_release_and_retry(installation):
    context, _, _ = installation
    snapshot = preview(installation)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DiscoveryCancelled):
        context.packages.adopt(snapshot['snapshot_id'], snapshot['manifest_hash'], ['Mods/Main.dll'], 'Example', cancel=cancel)
    assert not context.store.list('ModRelease')
    cancel.clear()
    assert adopt(context, snapshot)['release']


def test_cancel_after_copy_does_not_publish(installation):
    context, _, _ = installation
    snapshot = preview(installation)
    def cancel_after_first_artifact():
        return any(p.is_file() and not p.name.startswith('.copy-') for p in context.packages.library.rglob('*'))
    with pytest.raises(DiscoveryCancelled):
        context.packages.adopt(snapshot['snapshot_id'], snapshot['manifest_hash'], ['Mods/Main.dll', 'Mods/assets/icon.png'], 'Example', cancel=cancel_after_first_artifact)
    assert not context.store.list('ModRelease')
    assert not context.store.list('ModPackage')
    assert len(adopt(context, snapshot)['files']) == 2


def test_partial_optional_inventory_does_not_invalidate_adoption(installation):
    context, _, installation_id = installation
    inventory(context, installation_id)
    inventory(context, installation_id, 'partial')
    assert context.packages.preview(installation_id)['coverage'] == 'complete'


def test_partial_baseline_cannot_be_confirmed(installation):
    context, _, _ = installation
    snapshot = preview(installation)
    partial = context.store.get('BaselineSnapshot', snapshot['snapshot_id']).model_copy(update={'snapshot_id': uuid4(), 'coverage': 'partial'})
    context.store.put(partial)
    with pytest.raises(ValueError, match='baseline mismatch'):
        context.packages.adopt(partial.snapshot_id, partial.manifest_hash, ['Mods/Main.dll'], 'Example')
    assert not context.store.list('ModRelease')


def test_copy_failure_is_unpublished_and_retryable(installation, monkeypatch):
    context, _, _ = installation
    snapshot = preview(installation)
    original = PackageRepository.copy
    count = 0
    def fail_second(self, *args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('injected copy failure')
        return original(self, *args)
    with monkeypatch.context() as patch:
        patch.setattr(PackageRepository, 'copy', fail_second)
        with pytest.raises(OSError, match='injected'):
            adopt(context, snapshot)
    assert not context.store.list('ModRelease')
    assert not context.store.list('FileEntry')
    assert not list(context.packages.library.rglob('.copy-*'))
    assert len(adopt(context, snapshot)['files']) == 2


def test_game_copy_independent_of_library(installation):
    context, root, _ = installation
    result = adopt(context, preview(installation), ['Mods/Main.dll'])
    digest = result['files'][0]['source_hash']
    artifact = PackageRepository(context.store, context.packages.library).verify(digest)
    assert not os.path.samefile(artifact, root / 'Mods/Main.dll')
    original = artifact.read_bytes()
    (root / 'Mods/Main.dll').write_bytes(b'changed game copy')
    assert artifact.read_bytes() == original
    assert context.packages.resolve_release(result['release']['release_id'], result['release']['manifest_hash'])


def test_corrupt_library_rejected_by_profile(installation):
    context, _, installation_id = installation
    result = adopt(context, preview(installation), ['Mods/Main.dll'])
    artifact = PackageRepository(context.store, context.packages.library).path(result['files'][0]['source_hash'])
    artifact.write_bytes(b'X' * artifact.stat().st_size)
    assert context.packages.list()['releases'][0]['integrity'] == 'corrupt'
    with pytest.raises(ValueError, match='hash mismatch'):
        context.store.profiles.create(profile(installation_id, result))
    assert not context.store.list('Profile')


def test_profile_rejects_incorrect_lock(installation):
    context, _, installation_id = installation
    result = adopt(context, preview(installation))
    value = profile(installation_id, result).model_dump(mode='json')
    value['selections'][0]['manifest_hash'] = '0' * 64
    with pytest.raises(ValueError, match='lock mismatch'):
        context.store.profiles.create(Profile.model_validate(value))


def test_existing_profile_rejects_corruption_on_create_export_update(installation):
    context, _, installation_id = installation
    result = adopt(context, preview(installation), ['Mods/Main.dll'])
    saved = context.store.profiles.create(profile(installation_id, result))
    artifact = PackageRepository(context.store, context.packages.library).path(result['files'][0]['source_hash'])
    artifact.write_bytes(b'X' * artifact.stat().st_size)
    with pytest.raises(ValueError, match='hash mismatch'):
        context.store.profiles.create(saved)
    with pytest.raises(ValueError, match='hash mismatch'):
        context.store.profiles.export(saved.profile_id)
    with pytest.raises(ValueError, match='hash mismatch'):
        context.store.profiles.update(saved.model_copy(update={'revision': 1}), expected_revision=0)
    assert context.store.get('Profile', saved.profile_id) == saved


def test_unselected_file_change_does_not_block_selected_adoption(installation):
    context, root, _ = installation
    snapshot = preview(installation)
    (root / 'Mods/new.dll').write_bytes(b'new')
    assert len(adopt(context, snapshot)['files']) == 2
    assert (root / 'Mods/new.dll').read_bytes() == b'new'


def test_selected_file_change_refuses_adoption(installation):
    context, root, _ = installation
    snapshot = preview(installation)
    (root / 'Mods/Main.dll').write_bytes(b'changed selected file')
    with pytest.raises(ValueError, match='drift'):
        adopt(context, snapshot)
    assert not context.store.list('ModRelease')


@pytest.mark.parametrize('paths', [['../outside.dll'], ['Mods/../outside.dll'], ['C:/outside.dll'], ['Mods/Main.dll', 'mods/main.dll']])
def test_unsafe_or_duplicate_selection_rejected(installation, paths):
    context, _, _ = installation
    with pytest.raises(ValueError):
        adopt(context, preview(installation), paths)
    assert not context.store.list('ModRelease')


def test_hardlinked_source_rejected(installation):
    context, root, installation_id = installation
    inventory(context, installation_id)
    os.link(root / 'Mods/Main.dll', root / 'Mods/linked.dll')
    with pytest.raises(ValueError):
        context.packages.preview(installation_id)


def test_library_hardlink_rejected(installation):
    context, root, _ = installation
    result = adopt(context, preview(installation), ['Mods/Main.dll'])
    repository = PackageRepository(context.store, context.packages.library)
    digest = result['files'][0]['source_hash']
    os.link(repository.path(digest), root.parent / 'linked-library')
    with pytest.raises(ValueError, match='Unsafe library'):
        context.packages.resolve_release(result['release']['release_id'], result['release']['manifest_hash'])


def test_symlink_source_rejected(installation):
    context, root, installation_id = installation
    inventory(context, installation_id)
    try:
        (root / 'Mods/linked.dll').symlink_to(root / 'UserLibs/Shared.dll')
    except OSError as exc:
        pytest.skip(f'Symlink privilege unavailable: {exc}')
    with pytest.raises(ValueError):
        context.packages.preview(installation_id)


def test_directory_junction_source_rejected(installation):
    """A directory reparse point is distinct from the file symlink case above."""
    context, root, installation_id = installation
    inventory(context, installation_id)
    junction = root / 'Mods/linked-directory'
    environment = dict(os.environ, PSMM_TEST_JUNCTION=str(junction),
                       PSMM_TEST_JUNCTION_TARGET=str(root / 'UserLibs'))
    created = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:PSMM_TEST_JUNCTION -Target $env:PSMM_TEST_JUNCTION_TARGET | Out-Null"],
        env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW)
    assert created.returncode == 0, created.stderr
    try:
        assert junction.lstat().st_file_attributes & 0x400
        with pytest.raises(ValueError, match='ADOPTION_READ_FAILED'):
            context.packages.preview(installation_id)
        assert not context.store.list('ModRelease')
    finally:
        # Remove only this link, without traversing or deleting its target.
        os.rmdir(junction)


def test_preview_pagination_keeps_snapshot_identity(installation):
    context, root, _ = installation
    for number in range(25):
        (root / f'Mods/{number}.dll').write_bytes(str(number).encode())
    snapshot = preview(installation)
    assert len(snapshot['files']) == 20 and snapshot['total'] == 31
    second = context.packages.preview_page(snapshot['snapshot_id'], snapshot['next_offset'])
    assert second['snapshot_id'] == snapshot['snapshot_id']
    assert second['manifest_hash'] == snapshot['manifest_hash']
    assert second['next_offset'] is None
    assert len({f['relative_path'] for f in snapshot['files'] + second['files']}) == 31


def api_result(api, command, payload):
    response = api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command=command, payload=payload))
    assert response['ok'], response
    if 'task_id' not in response:
        return response['data']
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = api.dispatch(dict(schema_version=1, request_id=str(uuid4()), command='task.snapshot', payload={'task_id': response['task_id']}))['data']
        if state['state'] in ('succeeded', 'failed', 'cancelled'):
            assert state['state'] == 'succeeded', state
            return state['data']
        time.sleep(.01)
    pytest.fail('Package API job did not finish')


def test_actual_api_adoption_is_independent_of_missing_running_or_failed_metadata_scan(installation):
    context, _, installation_id = installation
    api = DesktopApi(context)
    payload = {'installation_id': str(installation_id)}
    assert api_result(api, 'package.adopt_preview', payload)['coverage'] == 'complete'
    repository = InventoryRepository(context.store)
    generation = repository.begin(installation_id, {})
    running = api_result(api, 'package.adopt_preview', payload)
    assert running['coverage'] == 'complete'
    issues = [{'path': 'Mods/Main.dll', 'code': 'WORKER_INITIALIZATION_FAILED'}]
    repository.finish(installation_id, generation, [], 'partial', {}, issues)
    partial = api_result(api, 'package.adopt_preview', payload)
    assert partial['coverage'] == 'complete'


def test_many_resources_preview_reads_each_once_without_metadata_worker(installation, monkeypatch):
    from backend.catalog import packages
    from backend.scanning.worker_client import MetadataWorker
    context, root, installation_id = installation
    assets = root / 'Mods/assets'
    for index in range(1914):
        (assets / f'asset-{index:04}.png').write_bytes(b'fixture resource')
    reads = []
    original = packages.read_file
    def counted(root, relative, *args, **kwargs):
        reads.append(relative)
        return original(root, relative, *args, **kwargs)
    monkeypatch.setattr(packages, 'read_file', counted)
    monkeypatch.setattr(MetadataWorker, 'inspect', lambda *a, **k: pytest.fail('Adoption must not parse assemblies'))
    progress = []
    result = context.packages.preview(installation_id, progress=lambda completed, total, *args: progress.append((completed, total)))
    assert result['coverage'] == 'complete' and result['total'] == 1920
    assert len(reads) == len(set(reads)) == 1920
    assert progress[-1] == (1920, 1920)
    reads.clear()
    assert len(adopt(context, result)['files']) == 2
    assert reads == ['Mods/Main.dll', 'Mods/assets/icon.png']


def test_actual_api_read_error_identifies_file_and_reason(installation, monkeypatch):
    from backend.catalog import packages
    context, _, installation_id = installation
    original = packages.read_file
    def denied(root, relative, *args, **kwargs):
        if relative == 'Mods/Main.dll':
            raise PermissionError('fixture lock')
        return original(root, relative, *args, **kwargs)
    monkeypatch.setattr(packages, 'read_file', denied)
    result = api_result(DesktopApi(context), 'package.adopt_preview', {'installation_id': str(installation_id)})
    assert result == {'error_code': 'ADOPTION_READ_FAILED', 'issues': [{'path': 'Mods/Main.dll', 'code': 'FILE_UNREADABLE'}]}


def test_actual_api_and_reopen_persistence(installation):
    context, _, installation_id = installation
    inventory(context, installation_id)
    api = DesktopApi(context)
    snapshot = api_result(api, 'package.adopt_preview', {'installation_id': str(installation_id)})
    result = api_result(api, 'package.adopt', dict(snapshot_id=snapshot['snapshot_id'], manifest_hash=snapshot['manifest_hash'], paths=['Mods/Main.dll'], display_name='API package'))
    assert result['deployment_generation'] is None
    assert api_result(api, 'package.list', {})['releases'][0]['integrity'] == 'verified'
    context.close()
    context.initialize()
    assert context.packages.list()['releases'][0]['release_id'] == result['release']['release_id']
    assert context.packages.resolve_release(result['release']['release_id'], result['release']['manifest_hash'])
    assert not context.store.list('DeploymentGeneration')
