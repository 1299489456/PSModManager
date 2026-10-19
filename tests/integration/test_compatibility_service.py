"""CASE-015/026: real private import, persisted Profile, graph and plan-store gate."""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest

from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import ENTITY_MODELS
from backend.games.probably_stolen.layout import identity
from backend.profiles.compatibility import CompatibilityService, CompatibilityError
from backend.storage.repositories import StorageConflict
from tests.integration.test_content_catalog import compile_fixture, manifest


@pytest.fixture
def app(tmp_path):
    context = DesktopContext(resolve_paths(str(tmp_path / 'private')))
    context.initialize()
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))
    root = tmp_path / 'synthetic-game'
    root.mkdir()
    (root / 'save.dat').write_bytes(b'untouched synthetic save')
    fixtures['RuntimeFingerprint'].update(coverage='complete', loader_version='0.7.3',
        exe_hash='1' * 64, game_assembly_hash='2' * 64, metadata_hash='3' * 64)
    fixtures['Installation']['canonical_root'] = str(root)
    fixtures['Installation']['file_identity'] = identity(root)
    context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fixtures['RuntimeFingerprint']))
    context.store.put(ENTITY_MODELS['Installation'].model_validate(fixtures['Installation']))
    try:
        yield context, fixtures, root
    finally:
        context.close()


def publish(context, tmp_path, name, payload=b'asset', target='Mods/shared.png'):
    root = tmp_path / name
    path = root / target
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    handle = context.imports.grant('directory', str(root))['source_handle']
    page = context.imports.preview(handle)
    return context.imports.commit(page['preview_id'], page['preview_hash'], name, '1-local')


def profile(context, fixtures, releases, *, commit=False):
    draft = context.profiles.create(fixtures['Installation']['installation_id'], '合成检查')
    for release in releases:
        draft = context.profiles.edit(draft['profile_id'], draft['revision'], dict(op='set_selection', selection=dict(
            release_id=release['release_id'], manifest_hash=release['manifest_hash'], selected=True)))
    if commit:
        context.profiles.commit(draft['profile_id'], draft['revision'])
    return draft


def component(context, release, name='Synthetic'):
    row = next(item for item in context.store.list('FileEntry', limit=100) if str(item.release_id) == release['release_id'])
    result = ENTITY_MODELS['Component'](component_id=uuid4(), release_id=release['release_id'], artifact_hash=row.source_hash,
        file_name=name + '.dll', assembly_name=name, assembly_version='1.0.0.0', melon_info=None, roles={'unknown'}, content_ids=[])
    context.store.put(result)
    return str(result.component_id)


def add_relation(context, fixtures, source, *, kind='hard_dependency', target=None, capability=None):
    result = ENTITY_MODELS['Relation'](relation_id=uuid4(), from_id=source, to_id=target,
        capability=capability, kind=kind, requirement_raw=None,
        source=dict(kind='author_manifest', reference='synthetic:manifest', observed_at='2026-09-29T00:00:00Z'),
        confidence='confirmed', fingerprint_scope=fixtures['RuntimeFingerprint']['fingerprint_id'])
    context.store.put(result)
    return result


def tree(root):
    return {str(path.relative_to(root)): (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob('*') if path.is_file()}


def plan(context, fixtures, draft):
    context.store.put(ENTITY_MODELS['BaselineSnapshot'].model_validate(fixtures['BaselineSnapshot']))
    data = deepcopy(fixtures['ActionPlan'])
    data.update(profile_id=draft['profile_id'], profile_revision=0, plan_id=str(uuid4()), idempotency_key=str(uuid4()))
    return ENTITY_MODELS['ActionPlan'].model_validate(data)


def test_case015_actual_worker_import_persists_requirements_and_soft_references(app, tmp_path):
    context, fixtures, game = app
    dll = compile_fixture(tmp_path / 'source', 'ItemContent', resource=('ItemForge.items.json', manifest('ItemForge', 19)))
    def imported():
        handle = context.imports.grant('dll', str(dll))['source_handle']
        page = context.imports.preview(handle)
        return context.imports.commit(page['preview_id'], page['preview_hash'], 'Actual content', 'unknown-local')
    release = imported()
    first = [item.model_dump(mode='json') for item in context.store.list('Relation', limit=100)]
    assert any(row['kind'] == 'capability_requirement' and row['capability'] == 'itemforge.runtime_api'
               and row['requirement_raw'] == 19 and row['confidence'] == 'confirmed' for row in first)
    assert any(row['kind'] == 'soft_dependency' and row['capability'].startswith('assembly:')
               and row['confidence'] == 'inferred' for row in first)
    assert all(row['fingerprint_scope'] is None and row['source']['reference'].startswith('artifact:') for row in first)
    assert imported()['release_id'] == release['release_id']
    assert [item.model_dump(mode='json') for item in context.store.list('Relation', limit=100)] == first
    draft = profile(context, fixtures, [release])
    before = tree(game), tree(context.paths.root / 'library')
    database_before = context.paths.database.read_bytes(), context.paths.database.stat().st_mtime_ns
    report = CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])
    assert report['binding']['profile_id'] == draft['profile_id']
    assert not report['blocking']
    assert report['findings']
    assert before == (tree(game), tree(context.paths.root / 'library'))
    assert database_before == (context.paths.database.read_bytes(), context.paths.database.stat().st_mtime_ns)
    assert not context.store.list('ActionPlan') and not context.store.list('DeploymentGeneration')


def test_case026_real_file_conflict_blocks_report_and_production_plan_store(app, tmp_path):
    context, fixtures, game = app
    releases = [publish(context, tmp_path, 'One', b'one'), publish(context, tmp_path, 'Two', b'two')]
    draft = profile(context, fixtures, releases, commit=True)
    before = tree(game)
    report = CompatibilityService(context.paths).check_committed(draft['profile_id'], 0)
    assert report['blocking'] and not report['compatibility_allows_plan']
    with pytest.raises(StorageConflict, match='Compatibility'):
        context.store.plans.add(plan(context, fixtures, draft))
    with pytest.raises(StorageConflict, match='Compatibility'):
        context.store.put(plan(context, fixtures, draft))
    assert not context.store.list('ActionPlan')
    assert before == tree(game)


@pytest.mark.parametrize('kind,blocking', [('hard_dependency', True), ('soft_dependency', False)])
def test_case015_case026_plan_consumer_distinguishes_confirmed_hard_from_soft(app, tmp_path, kind, blocking):
    context, fixtures, _ = app
    release = publish(context, tmp_path, 'Source')
    component_id = component(context, release)
    add_relation(context, fixtures, component_id, kind=kind, capability='component:' + str(uuid4()))
    draft = profile(context, fixtures, [release], commit=True)
    report = CompatibilityService(context.paths).check_committed(draft['profile_id'], 0)
    assert report['blocking'] is blocking
    candidate = plan(context, fixtures, draft)
    if blocking:
        with pytest.raises(StorageConflict, match='Compatibility'):
            context.store.plans.add(candidate)
        with pytest.raises(StorageConflict, match='Compatibility'):
            context.store.put(candidate)
    else:
        assert context.store.plans.add(candidate).plan_id == candidate.plan_id
        assert context.store.put(candidate).plan_id == candidate.plan_id


def test_case026_candidate_preview_is_readonly_and_revision_bound(app, tmp_path):
    context, fixtures, game = app
    left = publish(context, tmp_path, 'Left', target='Mods/left.png')
    right = publish(context, tmp_path, 'Right', target='Mods/right.png')
    left_component, right_component = component(context, left, 'Left'), component(context, right, 'Right')
    add_relation(context, fixtures, left_component, target=right_component)
    draft = profile(context, fixtures, [])
    before = context.profiles.get(draft['profile_id']), tree(game)
    report = CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'], enable_release_ids=[left['release_id']])
    assert report['enable_preview']
    assert before == (context.profiles.get(draft['profile_id']), tree(game))
    context.profiles.edit(draft['profile_id'], draft['revision'], dict(op='rename', name='新修订'))
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_STALE'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])


def test_case026_missing_corrupt_library_never_prepares_plan(app, tmp_path):
    context, fixtures, _ = app
    release = publish(context, tmp_path, 'Broken')
    draft = profile(context, fixtures, [release])
    artifact = context.store.list('Artifact')[0]
    (context.paths.root / 'library' / artifact.library_relative_path).write_bytes(b'corrupt')
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_UNRESOLVED'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])


def test_case026_unrelated_corrupt_release_is_not_a_used_candidate(app, tmp_path):
    context, fixtures, _ = app
    good = publish(context, tmp_path, 'Good', target='Mods/good.png')
    bad = publish(context, tmp_path, 'Bad', b'bad', target='Mods/bad.png')
    draft = profile(context, fixtures, [good])
    entry = next(item for item in context.store.list('FileEntry', limit=100) if str(item.release_id) == bad['release_id'])
    artifact = context.store.get('Artifact', entry.source_hash)
    (context.paths.root / 'library' / artifact.library_relative_path).write_bytes(b'corrupt')
    service = CompatibilityService(context.paths)
    report = service.check(draft['profile_id'], draft['revision'])
    assert not report['blocking'] and bad['release_id'] not in report['binding']['verified_release_ids']
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_UNRESOLVED'):
        service.check(draft['profile_id'], draft['revision'], enable_release_ids=[bad['release_id']])


def test_case026_changed_profile_during_check_rejects_stale_report(app, tmp_path, monkeypatch):
    context, fixtures, _ = app
    draft = profile(context, fixtures, [publish(context, tmp_path, 'Race')])
    service = CompatibilityService(context.paths)
    original = service._open
    opened = []
    @contextmanager
    def open_then_edit():
        opened.append(True)
        with original() as store:
            yield store
        if len(opened) == 1:
            context.profiles.edit(draft['profile_id'], draft['revision'], dict(op='rename', name='并发变化'))
    monkeypatch.setattr(service, '_open', open_then_edit)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_STALE'):
        service.check(draft['profile_id'], draft['revision'])


def test_case026_changed_library_during_graph_rejected_at_end(app, tmp_path, monkeypatch):
    from backend.profiles import graph
    context, fixtures, _ = app
    draft = profile(context, fixtures, [publish(context, tmp_path, 'Race')])
    artifact = context.store.list('Artifact')[0]
    original = graph.evaluate_graph
    def evaluate_then_corrupt(**kwargs):
        result = original(**kwargs)
        (context.paths.root / 'library' / artifact.library_relative_path).write_bytes(b'corrupt')
        return result
    monkeypatch.setattr(graph, 'evaluate_graph', evaluate_then_corrupt)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_UNRESOLVED'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])


def test_case026_fingerprint_advanced_between_snapshots_invalidates_report(app, tmp_path, monkeypatch):
    context, fixtures, _ = app
    draft = profile(context, fixtures, [publish(context, tmp_path, 'Fingerprint')])
    service = CompatibilityService(context.paths)
    original = service._open
    opened = []
    @contextmanager
    def open_then_advance():
        opened.append(True)
        with original() as store:
            yield store
        if len(opened) == 1:
            fingerprint_id = str(uuid4())
            fingerprint = fixtures['RuntimeFingerprint'] | {'fingerprint_id': fingerprint_id, 'exe_hash': '9' * 64}
            context.store.put(ENTITY_MODELS['RuntimeFingerprint'].model_validate(fingerprint))
            installation = fixtures['Installation'] | {'fingerprint_id': fingerprint_id}
            context.store.connection.execute('UPDATE installations SET fingerprint_id=?,payload=? WHERE installation_id=?',
                (fingerprint_id, json.dumps(installation), installation['installation_id']))
            context.store.connection.commit()
    monkeypatch.setattr(service, '_open', open_then_advance)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_STALE'):
        service.check(draft['profile_id'], draft['revision'])


def test_case026_report_limit_refuses_success_without_truncation(app, tmp_path, monkeypatch):
    import backend.profiles.compatibility as module
    context, fixtures, _ = app
    draft = profile(context, fixtures, [publish(context, tmp_path, 'Output')])
    monkeypatch.setattr(module, 'MAX_REPORT_BYTES', 64)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_LIMIT'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])


def test_case026_limit_and_cancel_are_explicit_not_partial_success(app, tmp_path, monkeypatch):
    import backend.profiles.compatibility as module
    context, fixtures, _ = app
    draft = profile(context, fixtures, [publish(context, tmp_path, 'Limit')])
    monkeypatch.setattr(module, 'MAX_RELEASES', 0)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_LIMIT'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])
    monkeypatch.setattr(module, 'MAX_RELEASES', 200)
    def cancel():
        raise InterruptedError('synthetic cancellation')
    with pytest.raises(InterruptedError):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'], check=cancel)
    assert not context.store.list('ActionPlan')


def test_case026_real_log_service_observations_remain_session_scoped_nonblocking(app, tmp_path, monkeypatch):
    context, fixtures, game = app
    release = publish(context, tmp_path, 'Runtime')
    component_id = component(context, release, 'SyntheticRuntime')
    draft = profile(context, fixtures, [release])
    no_logs = CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])
    assert no_logs['runtime_evidence']['coverage'] == 'unknown'
    latest = game / 'MelonLoader/Latest.log'
    latest.parent.mkdir()
    latest.write_text('MelonLoader v0.7.3\n[SyntheticRuntime] MOD 兼容性检测：共享方法=46，潜在冲突=21，确认冲突=0，停用模块=0；loadedMods=43。\n', encoding='utf-8')
    installation_id = fixtures['Installation']['installation_id']
    task = context.logs.watch(context.jobs, str(uuid4()), installation_id)
    deadline = time.monotonic() + 10
    try:
        while context.logs.evidence(installation_id)['total'] < 2:
            assert time.monotonic() < deadline, context.jobs.snapshot(task['task_id'])
            time.sleep(.02)
    finally:
        context.jobs.cancel(task['task_id'])
    while context.jobs.snapshot(task['task_id'])['state'] not in ('cancelled', 'failed', 'succeeded'):
        assert time.monotonic() < deadline
        time.sleep(.02)
    before = tree(game)
    report = CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])
    runtime = next(item for item in report['findings'] if item['code'] == 'HARMONY-POTENTIAL')
    assert runtime['severity'] == 'warning' and not report['blocking']
    assert runtime['component_ids'] == [component_id]
    assert runtime['scope']['applicability'] == 'recorded_session_only'
    assert all(runtime['scope'][key] for key in ('source_id', 'epoch', 'session_id'))
    assert runtime['detail']['facts']['reported_counts']['potential_conflicts'] == 21
    assert runtime['detail']['facts']['independently_verified'] is False
    assert runtime['reason_chain']['evidence_ids']
    assert report['runtime_evidence']['record_count'] == 2
    assert report['runtime_evidence']['current_deployment_verified'] is False
    assert before == tree(game)
    monkeypatch.setattr('backend.profiles.compatibility.MAX_LOGS', 1)
    with pytest.raises(CompatibilityError, match='COMPATIBILITY_LIMIT'):
        CompatibilityService(context.paths).check(draft['profile_id'], draft['revision'])
