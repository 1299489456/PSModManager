"""CASE-029 planner slice: real desktop API, SQLite, imports and isolated disk.

Historical generations below now come from the shared production executor.
Only the native confirmation receipt and low-space fault are substituted; planning,
compatibility, fingerprint checks, file hashes and guards execute production code.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import faulthandler
import hashlib
import json
import os
import time
from uuid import uuid4, uuid5

import pytest

from backend.app.api import DesktopApi
from backend.app.composition import DesktopContext
from backend.app.paths import resolve_paths
from backend.contracts import Approval, Component, DeploymentGeneration, Relation, Transaction
from backend.contracts.plans import plan_digest
from backend.deployments.policy import PlanRejected, check_value, owned_after
from backend.games.probably_stolen.layout import ASSEMBLY
from test_installation_layout import make_installation


ROOTS = {'mods': 'Mods', 'plugins': 'Plugins', 'user_libs': 'UserLibs'}


def tree_hashes(root):
    return {p.relative_to(root).as_posix() + ('/' if p.is_dir() else ''):
            '<directory>' if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() or p.is_dir()}


def stable_preview(preview):
    """Free space is a fresh observation; stable required budgets stay bound."""
    return dict(preview, space={volume: {key: value for key, value in facts.items() if key != 'available'}
                               for volume, facts in preview['space'].items()})


class Harness:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.job_receipts = []
        self.game = make_installation(tmp_path / 'synthetic-game')
        for name in ROOTS.values():
            (self.game / name).mkdir()
        (self.game / 'UserData').mkdir()
        (self.game / 'UserData/save.sav').write_bytes(b'isolated-save-sentinel')
        self.context = DesktopContext(resolve_paths(str(tmp_path / 'private')))
        self.context.initialize()
        self.context.api = DesktopApi(self.context)
        self.context.choose_installation_folder = lambda _: str(self.game)
        grant = self.rpc('installation.choose', {'kind': 'manual'})
        found = self.rpc('installation.discover', {'source_handles': [grant['source_handle']]})
        assert len(found['candidates']) == 1 and found['candidates'][0]['valid']
        selected = self.rpc('installation.select', {'candidate_id': found['candidates'][0]['candidate_id']})
        self.installation = selected['installation']['installation_id']
        self.count = 0

    def request(self, command, payload):
        return self.context.api.dispatch({'schema_version': 1, 'request_id': str(uuid4()),
                                          'command': command, 'payload': payload})

    def rpc(self, command, payload):
        response = self.request(command, payload)
        assert response['ok'], response
        data = response['data']
        if isinstance(data, dict) and 'task_id' in data:
            started = time.monotonic()
            budget = 120 if command in {'plan.apply', 'assistant.apply_plan'} else 30
            terminal = {'succeeded', 'failed', 'cancelled'}
            transitions, timed_out = [], False
            task_id = data['task_id']
            try:
                while data['state'] not in terminal:
                    marker = (data['state'], data['stage'], data['message_code'])
                    if not transitions or marker != transitions[-1]['marker']:
                        transitions.append({'elapsed': time.monotonic() - started, 'marker': marker})
                    if time.monotonic() >= started + budget:
                        timed_out = True
                        with (self.root / 'rpc-timeout-stacks.log').open('a', encoding='utf-8') as output:
                            faulthandler.dump_traceback(file=output, all_threads=True)
                        # Drain the exact job before fixture teardown closes SQLite.
                        # A timeout remains a failure even when cancellation succeeds.
                        self.context.jobs.cancel(task_id)
                        stop_deadline = time.monotonic() + 120
                        while data['state'] not in terminal and time.monotonic() < stop_deadline:
                            time.sleep(.01)
                            data = self.context.jobs.snapshot(task_id)
                        pytest.fail(f'{command} exceeded {budget}s; final actual snapshot: {data!r}')
                    time.sleep(.005)
                    data = self.context.jobs.snapshot(task_id)
                job = self.context.jobs._jobs[task_id]
                job.thread.join(2)
                assert not job.thread.is_alive(), (command, data)
                assert data['state'] == 'succeeded', (command, data)
                return data['data']
            finally:
                # Existing semantic/disk/generation assertions remain authoritative.
                self.job_receipts.append({'command': command, 'task_id': task_id, 'budget': budget,
                    'elapsed': time.monotonic() - started, 'timed_out': timed_out,
                    'snapshot': data, 'transitions': transitions})
                (self.root / 'rpc-jobs.json').write_text(json.dumps(self.job_receipts, indent=2), encoding='utf-8')
        return data

    def publish(self, files, name=None, version='1.0'):
        self.count += 1
        source = self.root / f'input-{self.count}'
        source.mkdir()
        for relative, content in files.items():
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.context.choose_import_source = lambda _: str(source)
        granted = self.rpc('package.choose', {'kind': 'directory'})
        preview = self.rpc('package.import_preview', {'source_handle': granted['source_handle']})
        assert preview['can_commit'], preview
        release = self.rpc('package.import_commit', {
            'preview_id': preview['preview_id'], 'preview_hash': preview['preview_hash'],
            'display_name': name or f'Package {self.count}', 'version_raw': version})
        assert release.get('release_id') and release['enabled'] is False and release['deployed'] is False, release
        return release

    def profile(self, releases=()):
        state = self.rpc('profile.create', {'installation_id': self.installation, 'name': 'Plan fixture'})
        for release in releases:
            state = self.select(state, release)
        return self.commit(state)

    def select(self, state, release, enabled=True):
        return self.rpc('profile.edit', {'profile_id': state['profile_id'], 'expected_revision': state['revision'],
            'action': {'op': 'set_selection', 'selection': {
                'release_id': release['release_id'], 'manifest_hash': release['manifest_hash'], 'selected': enabled}}})

    def commit(self, state):
        saved = self.rpc('profile.commit', {'profile_id': state['profile_id'], 'expected_revision': state['revision']})
        assert 'error_code' not in saved, saved
        state = self.rpc('profile.get', {'profile_id': state['profile_id']})
        assert not state['dirty'] and state['profile_revision'] is not None
        return state

    @staticmethod
    def proposal(state, key=None):
        return {'profile_id': state['profile_id'], 'expected_revision': state['profile_revision'],
                'idempotency_key': str(key or uuid4())}

    def create(self, state, key=None):
        return self.rpc('plan.create', self.proposal(state, key))

    @staticmethod
    def identity(preview):
        return {key: preview['plan'][key] for key in ('plan_id', 'plan_hash')}

    def approve(self, preview):
        return self.rpc('plan.approve', self.identity(preview))

    def all_files(self, preview):
        rows = list(preview['files'])
        offset = preview['next_offset']
        while offset is not None:
            page = self.rpc('plan.inspect', dict(self.identity(preview), offset=offset, limit=20))
            rows.extend(page['files'])
            offset = page['next_offset']
        assert len(rows) == preview['total']
        return rows

    def historical_generation(self, preview):
        """Create real file/journal/current/DB history through the product API."""
        original = self.context.confirm_plan
        self.context.confirm_plan = lambda _: True
        try:
            approved = self.approve(preview)
            assert approved['approved'], approved
        finally:
            self.context.confirm_plan = original
        store = self.context.store
        plan = store.get('ActionPlan', preview['plan']['plan_id'])
        result = self.rpc('plan.apply',dict(self.identity(preview),approval_id=approved['approval']['approval_id']))
        assert not result.get('error_code'),result
        generation = store.get('DeploymentGeneration',result['transaction']['target_generation'])
        assert not store.list('RuntimeSession') and not store.list('Observation')
        return generation

    def adopt(self, relative, name='Explicit adoption'):
        scanned = self.rpc('inventory.scan', {'installation_id': self.installation, 'force': True})
        assert 'error_code' not in scanned, scanned
        preview = self.rpc('package.adopt_preview', {'installation_id': self.installation})
        assert preview['coverage'] == 'complete', preview
        result = self.rpc('package.adopt', {'snapshot_id': preview['snapshot_id'],
            'manifest_hash': preview['manifest_hash'], 'paths': [relative],
            'display_name': name, 'version_raw': 'adopted'})
        assert 'error_code' not in result, result
        return {'release_id': result['release']['release_id'], 'manifest_hash': result['release']['manifest_hash']}

    def assert_empty_execution(self):
        assert not self.context.store.list('Transaction')
        assert not self.context.store.list('DeploymentGeneration')
        assert not self.context.store.list('RuntimeSession')


@pytest.fixture
def app(tmp_path):
    harness = Harness(tmp_path)
    try:
        yield harness
    finally:
        harness.context.close()
        assert (harness.game / 'UserData/save.sav').read_bytes() == b'isolated-save-sentinel'


def test_case029_real_user_and_ai_share_persisted_preview_and_native_approval(app):
    release = app.publish({'Mods/asset.png': b'original png fixture', 'Plugins/settings.json': b'{"v":1}'})
    state = app.profile([release])
    before = tree_hashes(app.game)
    proposal = app.proposal(state)
    user = app.rpc('plan.create', proposal)
    assert 'error_code' not in user, user
    ai = app.context.plan_proposals.preview(proposal)
    assert stable_preview(user) == stable_preview(ai) and app.context.plan_proposals.service is app.context.plans
    assistant_rpc = app.rpc('assistant.propose_plan', proposal)
    assert stable_preview(assistant_rpc) == stable_preview(user)
    assistant_page = app.rpc('assistant.inspect_plan', app.identity(user))
    assert stable_preview(assistant_page) == stable_preview(user)
    assert not hasattr(app.context.plan_proposals, 'approve')
    forbidden = app.request('assistant.approve_plan', app.identity(user))
    assert not forbidden['ok'] and forbidden['error']['code'] == 'UNSUPPORTED_COMMAND'
    forged_actor = app.request('assistant.propose_plan', dict(proposal, actor='native-user'))
    assert not forged_actor['ok'] and forged_actor['error']['code'] == 'INVALID_REQUEST'
    assert user['execution_available'] is False and not user['no_op']
    assert user['configuration_changes'] == [] and user['configuration_policy'] == 'unsupported'
    assert user['plan']['restart_required'] and user['plan']['backup_scope'] == []
    assert user['plan']['approval_scope'] == ['install_owned']
    assert all(row['change'] == 'add' and row['before'] == 'absent' for row in user['files'])
    assert app.approve(user) == {'approval': None, 'approved': False, 'no_op': False, 'execution_available': False}
    receipts = []
    app.context.confirm_plan = lambda preview: receipts.append(preview) or True
    accepted = app.approve(user)
    assert accepted['approved'] and accepted['approval']['actor'] == 'native-user'
    assert accepted['approval']['plan_hash'] == user['plan']['plan_hash']
    assert accepted['approval']['scope'] == user['plan']['approval_scope']
    assert accepted['approval']['expires_at'] == user['plan']['expires_at']
    assert len(receipts) == 1 and stable_preview(receipts[0]) == stable_preview(user)
    plan, approval = app.context.plans.validate_approval(app.identity(user), accepted['approval']['approval_id'])
    assert str(approval.plan_id) == user['plan']['plan_id'] and plan_digest(plan) == plan.plan_hash
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_first_apply_baseline_includes_unknown_files_and_no_generation(app):
    (app.game / 'Mods/unknown.txt').write_bytes(b'keep unknown')
    (app.game / 'Plugins/existing.json').write_bytes(b'{"keep":true}')
    state = app.profile([app.publish({'UserLibs/new.png': b'new'})])
    before = tree_hashes(app.game)
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    assert preview['plan']['base_generation'] is None
    snapshot = app.context.store.get('BaselineSnapshot', preview['plan']['baseline_snapshot_id'])
    assert snapshot.coverage == 'complete' and snapshot.scan_generation == 0
    assert {file.relative_path for file in snapshot.files} == {'Mods/unknown.txt', 'Plugins/existing.json'}
    rows = {row['relative_path']: row for row in preview['files']}
    assert rows['unknown.txt']['change'] == rows['existing.json']['change'] == 'keep'
    assert rows['unknown.txt']['before'] == rows['unknown.txt']['after']
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_diff_add_replace_remove_keep_and_backup_impact_from_history(app):
    old = app.publish({'Mods/replace.json': b'{"v":1}', 'Mods/remove.png': b'remove', 'Mods/keep.png': b'keep'})
    state = app.profile([old])
    first = app.create(state)
    assert 'error_code' not in first, first
    generation = app.historical_generation(first)
    (app.game / 'Mods/unmanaged.txt').write_bytes(b'unmanaged keep')
    newer = app.publish({'Mods/replace.json': b'{"v":2}', 'Mods/keep.png': b'keep', 'Plugins/add.png': b'add'}, version='2.0')
    state = app.commit(app.select(app.select(state, old, False), newer))
    before = tree_hashes(app.game)
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    assert preview['plan']['base_generation'] == str(generation.generation_id)
    rows = {row['relative_path']: row for row in preview['files']}
    assert {name: row['change'] for name, row in rows.items()} == {
        'replace.json': 'replace', 'remove.png': 'remove', 'keep.png': 'replace',
        'unmanaged.txt': 'keep', 'add.png': 'add'}
    assert preview['plan']['backup_scope'] == ['Mods/keep.png', 'Mods/remove.png', 'Mods/replace.json']
    assert preview['plan']['approval_scope'] == ['install_owned', 'remove_owned', 'replace_owned']
    assert rows['remove.png']['after'] == 'absent' and rows['replace.json']['backup_required']
    space = next(iter(preview['space'].values()))
    assert rows['keep.png']['before'] == rows['keep.png']['after'] and rows['keep.png']['before_owner_ids'] != rows['keep.png']['owner_ids']
    assert space['backup'] == len(b'keep') + len(b'remove') + len(b'{"v":1}')
    assert space['staging'] == len(b'keep') + len(b'add') + len(b'{"v":2}')
    assert space['required'] == sum(space[k] for k in ('library', 'staging', 'backup', 'target_growth', 'overhead'))
    assert space['available'] >= space['required']
    checks = app.context.store.get('ActionPlan', preview['plan']['plan_id']).verification_checks
    assert any(check.kind == 'runtime' and check.expected == 'not_run' for check in checks)
    assert tree_hashes(app.game) == before and len(app.context.store.list('DeploymentGeneration')) == 1


def test_case029_shared_hash_keeps_file_until_last_owner_removed(app):
    a = app.publish({'Mods/shared.png': b'shared'}, name='Owner A')
    b = app.publish({'Mods/shared.png': b'shared'}, name='Owner B')
    state = app.profile([a, b])
    first = app.create(state)
    assert 'error_code' not in first, first
    assert first['total'] == 1 and first['files'][0]['owner_ids'] == sorted([a['release_id'], b['release_id']])
    app.historical_generation(first)
    state = app.commit(app.select(state, a, False))
    before = tree_hashes(app.game)
    one = app.create(state)
    assert 'error_code' not in one, one
    assert not one['no_op'] and one['files'][0]['change'] == 'replace'
    assert one['files'][0]['owner_ids'] == [b['release_id']]
    assert one['plan']['approval_scope'] == ['replace_owned'] and one['plan']['restart_required'] is True
    state = app.commit(app.select(state, b, False))
    last = app.create(state)
    assert 'error_code' not in last, last
    assert last['files'][0]['change'] == 'remove' and last['files'][0]['after'] == 'absent'
    assert last['plan']['backup_scope'] == ['Mods/shared.png']
    assert tree_hashes(app.game) == before and len(app.context.store.list('DeploymentGeneration')) == 1


@pytest.mark.parametrize('identical', [True, False])
def test_case029_unmanaged_collision_requires_explicit_adoption_even_same_hash(app, identical):
    release = app.publish({'Mods/existing.png': b'package'})
    (app.game / 'Mods/existing.png').write_bytes(b'package' if identical else b'foreign')
    state = app.profile([release])
    before = tree_hashes(app.game)
    result = app.create(state)
    assert result['error_code'] == 'PLAN_UNMANAGED_CONFLICT'
    assert not app.context.store.list('ActionPlan') and not app.context.store.list('BaselineSnapshot')
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_explicit_real_adoption_authorizes_first_replace_with_baseline(app):
    (app.game / 'Mods/existing.png').write_bytes(b'old')
    before = tree_hashes(app.game)
    adopted = app.adopt('Mods/existing.png')
    replacement = app.publish({'Mods/existing.png': b'new'}, name='Replacement')
    state = app.profile([replacement])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    row = preview['files'][0]
    assert row['change'] == 'replace' and row['before_owner_ids'] == [adopted['release_id']]
    assert row['owner_ids'] == [replacement['release_id']] and preview['plan']['base_generation'] is None
    assert preview['plan']['backup_scope'] == ['Mods/existing.png']
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_noop_idempotency_creates_no_approval_transaction_or_generation(app):
    (app.game / 'Mods/unknown.txt').write_bytes(b'keep')
    state = app.profile()
    before = tree_hashes(app.game)
    key = uuid4()
    first, second = app.create(state, key), app.create(state, key)
    assert 'error_code' not in first, first
    assert stable_preview(first) == stable_preview(second) and first['no_op'] and not first['can_approve']
    assert not first['plan']['restart_required'] and first['plan']['approval_scope'] == []
    app.context.confirm_plan = lambda _: pytest.fail('no-op cannot ask for approval')
    assert app.approve(first) == {'approval': None, 'approved': False, 'no_op': True, 'execution_available': False}
    assert len(app.context.store.list('ActionPlan')) == 1 and not app.context.store.list('Approval')
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_same_key_and_approval_retry_do_not_duplicate_or_extend_expiry(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    proposal = app.proposal(state)
    first = app.rpc('plan.create', proposal)
    assert 'error_code' not in first, first
    app.context.confirm_plan = lambda _: True
    approved = app.approve(first)
    again = app.approve(first)
    assert approved == again and stable_preview(app.rpc('plan.create', proposal)) == stable_preview(first)
    assert len(app.context.store.list('Approval')) == len(app.context.store.list('ActionPlan')) == 1
    assert approved['approval']['expires_at'] == first['plan']['expires_at']
    app.assert_empty_execution()


def test_case029_concurrent_ai_same_key_converges_to_one_persisted_plan(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    proposal = app.proposal(state)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: app.context.plan_proposals.preview(proposal), range(2)))
    assert stable_preview(results[0]) == stable_preview(results[1]) and len(app.context.store.list('ActionPlan')) == 1
    app.assert_empty_execution()


def test_case029_same_key_different_profile_or_revision_is_rejected(app):
    release = app.publish({'Mods/new.png': b'new'})
    state = app.profile([release])
    key = uuid4()
    first = app.create(state, key)
    assert 'error_code' not in first, first
    other = app.profile([release])
    assert app.create(other, key)['error_code'] == 'PLAN_IDEMPOTENCY_CONFLICT'
    state = app.commit(app.select(state, release, False))
    assert app.create(state, key)['error_code'] == 'PLAN_IDEMPOTENCY_CONFLICT'
    assert len(app.context.store.list('ActionPlan')) == 1


@pytest.mark.parametrize('drift', ['target_bytes_same_size_mtime', 'new_unknown', 'fingerprint', 'dirty_draft', 'saved_revision'])
def test_case029_facts_drift_rejects_old_inspection_and_approval_without_writes(app, drift):
    (app.game / 'Mods/unknown.png').write_bytes(b'original')
    release = app.publish({'Mods/new.png': b'new'})
    state = app.profile([release])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    if drift == 'target_bytes_same_size_mtime':
        path = app.game / 'Mods/unknown.png'; stamp = path.stat()
        path.write_bytes(b'replaced'); os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert path.stat().st_size == stamp.st_size and path.stat().st_mtime_ns == stamp.st_mtime_ns
    elif drift == 'new_unknown':
        (app.game / 'Plugins/appeared.json').write_bytes(b'{}')
    elif drift == 'fingerprint':
        path = app.game / ASSEMBLY; stamp = path.stat()
        data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    elif drift == 'dirty_draft':
        app.select(state, release, False)
    else:
        app.commit(app.select(state, release, False))
    before = tree_hashes(app.game)
    confirmations = []
    app.context.confirm_plan = lambda p: confirmations.append(p) or True
    inspected = app.rpc('plan.inspect', app.identity(preview))
    approved = app.approve(preview)
    assert 'error_code' in inspected and approved['error_code'] == inspected['error_code']
    assert not confirmations and not app.context.store.list('Approval')
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_drift_after_native_confirmation_is_rechecked_before_receipt(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    def confirm(_):
        (app.game / 'Mods/late.json').write_bytes(b'late fixture mutation')
        return True
    app.context.confirm_plan = confirm
    result = app.approve(preview)
    assert 'error_code' in result and not app.context.store.list('Approval')
    assert not (app.game / 'Mods/new.png').exists()
    app.assert_empty_execution()


def test_case029_capacity_sums_same_volume_and_low_space_fault_blocks_without_publishing(app, monkeypatch):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    before = tree_hashes(app.game)
    from backend.deployments import planner
    actual = planner.shutil.disk_usage(app.game)
    monkeypatch.setattr(planner.shutil, 'disk_usage', lambda _: type(actual)(actual.total, actual.used, 0))
    result = app.create(state)
    assert result['error_code'] == 'PLAN_SPACE_INSUFFICIENT'
    assert not app.context.store.list('ActionPlan') and not app.context.store.list('BaselineSnapshot')
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('field,value', [('actor','ai'), ('scope',['install_owned']),
    ('approved',True), ('approval_id','00000000-0000-0000-0000-000000000001'), ('relative_path','../save.sav'),
    ('operations',[]), ('shell','cmd.exe')])
def test_case029_create_closed_proposal_refuses_ai_paths_and_approval_claims(app, field, value):
    state = app.profile()
    before = tree_hashes(app.game)
    response = app.request('plan.create', dict(app.proposal(state), **{field:value}))
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST'
    assert not app.context.store.list('ActionPlan') and tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('command,payload', [('plan.approve',{'actor':'native-user'}),
    ('plan.approve',{'scope':['install_owned']}), ('plan.approve',{'approved':True}),
    ('plan.inspect',{'offset':-1}), ('plan.inspect',{'limit':21}),
    ('plan.inspect',{'offset':True}), ('plan.inspect',{'limit':0})])
def test_case029_inspection_approval_closed_payload_and_pagination_reject_bad_input(app, command, payload):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    response = app.request(command, dict(app.identity(preview), **payload))
    assert not response['ok'] and response['error']['code'] == 'INVALID_REQUEST'
    assert not app.context.store.list('Approval')


def test_case029_wrong_plan_hash_and_missing_approval_identity_are_rejected(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    bad = dict(app.identity(preview), plan_hash='0'*64)
    assert app.rpc('plan.inspect', bad)['error_code'] == 'PLAN_HASH_MISMATCH'
    assert app.rpc('plan.approve', bad)['error_code'] == 'PLAN_HASH_MISMATCH'
    with pytest.raises(PlanRejected, match='PLAN_APPROVAL_INVALID'):
        app.context.plans.validate_approval(app.identity(preview), str(uuid4()))
    assert not app.context.store.list('Approval')


@pytest.mark.parametrize('mutation', ['actor', 'scope', 'hash', 'expiry', 'identity'])
def test_case029_tampered_backend_approval_rejected_even_when_database_contains_it(app, mutation):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    app.context.confirm_plan = lambda _: True
    approval = app.approve(preview)['approval']
    forged = dict(approval)
    if mutation == 'actor': forged['actor'] = 'ai'
    elif mutation == 'scope': forged['scope'] = ['remove_owned']
    elif mutation == 'hash': forged['plan_hash'] = '0'*64
    elif mutation == 'expiry': forged['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    else: forged['approval_id'] = str(uuid4())
    app.context.store.connection.execute('UPDATE approvals SET payload=? WHERE approval_id=?',
        (json.dumps(forged), approval['approval_id']))
    before = tree_hashes(app.game)
    with pytest.raises(PlanRejected, match='PLAN_APPROVAL_INVALID'):
        app.context.plans.validate_approval(app.identity(preview), approval['approval_id'])
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('mutation', ['owner', 'operation_kind', 'operation_hash', 'backup', 'scope', 'expires'])
def test_case029_persisted_plan_mutations_invalidate_canonical_hash(app, mutation):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id']).model_dump(mode='json')
    if mutation == 'owner': plan['operations'][0]['owner_ids'] = [str(uuid4())]
    elif mutation == 'operation_kind':
        plan['operations'][0].update(kind='replace_owned', expected_before='1'*64)
    elif mutation == 'operation_hash': plan['operations'][0]['desired_after'] = '1'*64
    elif mutation == 'backup': plan['backup_scope'] = ['Mods/other.png']
    elif mutation == 'scope': plan['approval_scope'] = ['remove_owned']
    else: plan['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    app.context.store.connection.execute('UPDATE action_plans SET payload=? WHERE plan_id=?',
        (json.dumps(plan), plan['plan_id']))
    before = tree_hashes(app.game)
    assert app.rpc('plan.inspect', app.identity(preview))['error_code'] == 'PLAN_CORRUPT'
    assert app.approve(preview)['error_code'] == 'PLAN_CORRUPT'
    assert tree_hashes(app.game) == before and not app.context.store.list('Approval')
    app.assert_empty_execution()


def test_case029_expired_plan_does_not_extend_on_idempotent_retry_or_approve(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    proposal = app.proposal(state)
    preview = app.rpc('plan.create', proposal)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id'])
    plan.expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
    plan.plan_hash = plan_digest(plan)
    app.context.store.connection.execute('UPDATE action_plans SET payload=?,plan_hash=? WHERE plan_id=?',
        (plan.model_dump_json(), plan.plan_hash, str(plan.plan_id)))
    identity = {'plan_id': str(plan.plan_id), 'plan_hash': plan.plan_hash}
    assert app.rpc('plan.inspect', identity)['error_code'] == 'PLAN_EXPIRED'
    assert app.rpc('plan.approve', identity)['error_code'] == 'PLAN_EXPIRED'
    assert app.rpc('plan.create', proposal)['error_code'] == 'PLAN_EXPIRED'
    assert len(app.context.store.list('ActionPlan')) == 1 and not app.context.store.list('Approval')


@pytest.mark.parametrize('file_count', [25, 128])
def test_case029_paged_full_file_impact_matches_persisted_checks_without_execution(app, file_count):
    state = app.profile([app.publish({f'Mods/asset{i:03}.png': f'asset {i}'.encode() for i in range(file_count)})])
    before = tree_hashes(app.game)
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    rows = app.all_files(preview)
    assert preview['total'] == file_count and 0 < len(preview['files']) <= 20
    assert preview['next_offset'] == len(preview['files'])
    assert len(json.dumps(preview, ensure_ascii=False).encode('utf-8')) <= 32768
    assert {row['relative_path'] for row in rows} == {f'asset{i:03}.png' for i in range(file_count)}
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id'])
    assert len(plan.operations) == file_count and len(plan.verification_checks) == file_count+1
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('relative', ['Mods/config.cfg', 'UserData/state.json', 'loose.png'])
def test_case029_unadapted_config_mutable_and_unowned_targets_refused(app, relative):
    state = app.profile([app.publish({relative: b'fixture'})])
    before = tree_hashes(app.game)
    assert app.create(state)['error_code'] == 'PLAN_TARGET_NOT_ALLOWED'
    assert not app.context.store.list('ActionPlan') and tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_confirmed_missing_hard_dependency_blocks_actual_profile_planner(app):
    release = app.publish({'Mods/content.png': b'content'})
    store = app.context.store
    source_hash = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?', (release['release_id'],)).fetchone()[0]
    component = store.put(Component(component_id=uuid4(), release_id=release['release_id'], artifact_hash=source_hash,
        file_name='content.png', assembly_name=None, assembly_version=None, melon_info=None,
        roles={'content'}, content_ids=[]))
    store.put(Relation(relation_id=uuid4(), from_id=component.component_id, capability='component:'+str(uuid4()),
        kind='hard_dependency', requirement_raw=None,
        source={'kind':'user', 'reference':'synthetic-explicit-dependency', 'observed_at': datetime.now(timezone.utc)},
        confidence='confirmed', fingerprint_scope=store.get('Installation', app.installation).fingerprint_id))
    state = app.profile([release])
    before = tree_hashes(app.game)
    assert app.create(state)['error_code'] == 'PLAN_COMPATIBILITY_BLOCKED'
    assert not store.list('ActionPlan') and not store.list('BaselineSnapshot')
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_conflicting_packages_block_actual_consumer_before_plan_registration(app):
    a = app.publish({'Mods/conflict.png': b'A'})
    b = app.publish({'Mods/conflict.png': b'B'})
    state = app.profile([a, b])
    before = tree_hashes(app.game)
    result = app.create(state)
    assert result['error_code'] in {'PLAN_COMPATIBILITY_BLOCKED', 'PLAN_TARGET_CONFLICT'}
    assert not app.context.store.list('ActionPlan') and tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_predictable_forged_approval_record_cannot_replace_native_receipt(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id'])
    forged = app.context.store.put(Approval(approval_id=uuid5(plan.plan_id, 'native-user-approval'),
        plan_id=plan.plan_id, plan_hash=plan.plan_hash, scope=plan.approval_scope,
        actor='native-user', expires_at=plan.expires_at))
    before = tree_hashes(app.game)
    with pytest.raises(PlanRejected, match='PLAN_APPROVAL_INVALID'):
        app.context.plans.validate_approval(app.identity(preview), str(forged.approval_id))
    assert not (app.context.paths.root / '.plan-approval-key').exists()
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_native_approval_persists_across_real_context_reopen(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    app.context.confirm_plan = lambda _: True
    approved = app.approve(preview)
    assert approved['approved'], approved
    assert (app.context.paths.root / '.plan-approval-key').stat().st_size == 32
    before = tree_hashes(app.game)
    app.context.close()
    app.context = DesktopContext(resolve_paths(str(app.root / 'private')))
    app.context.initialize()
    app.context.api = DesktopApi(app.context)
    _, approval = app.context.plans.validate_approval(app.identity(preview), approved['approval']['approval_id'])
    assert str(approval.approval_id) == approved['approval']['approval_id']
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('mutation', ['target', 'revision'])
def test_case029_old_valid_native_approval_rejected_after_bound_facts_change(app, mutation):
    (app.game / 'Mods/unknown.png').write_bytes(b'unknown')
    release = app.publish({'Mods/new.png': b'new'})
    state = app.profile([release])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    app.context.confirm_plan = lambda _: True
    approval = app.approve(preview)['approval']
    if mutation == 'target':
        path = app.game / 'Mods/unknown.png'; stamp = path.stat()
        path.write_bytes(b'changed'); os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    else:
        app.commit(app.select(state, release, False))
    before = tree_hashes(app.game)
    with pytest.raises(PlanRejected):
        app.context.plans.validate_approval(app.identity(preview), approval['approval_id'])
    assert len(app.context.store.list('Approval')) == 1 and tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('mutation', ['scope', 'backup', 'operation'])
def test_case029_rehashed_altered_plan_cannot_be_approved_as_original_facts(app, mutation):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id'])
    if mutation == 'scope': plan.approval_scope = ['remove_owned']
    elif mutation == 'backup': plan.backup_scope = ['Mods/other.png']
    else: plan.operations[0].desired_after = '1'*64
    plan.plan_hash = plan_digest(plan)
    app.context.store.connection.execute('UPDATE action_plans SET payload=?,plan_hash=? WHERE plan_id=?',
        (plan.model_dump_json(), plan.plan_hash, str(plan.plan_id)))
    identity = {'plan_id': str(plan.plan_id), 'plan_hash': plan.plan_hash}
    before = tree_hashes(app.game)
    assert app.rpc('plan.inspect', identity)['error_code'] == 'PLAN_STALE'
    assert app.rpc('plan.approve', identity)['error_code'] == 'PLAN_STALE'
    assert not app.context.store.list('Approval') and tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('relative', ['../UserData/save.sav', 'C:/outside.png', 'safe.png:stream', 'CON.png'])
def test_case029_stored_operation_path_drift_rejects_without_opening_write_target(app, relative):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id']).model_dump(mode='json')
    plan['operations'][0]['relative_path'] = relative
    app.context.store.connection.execute('UPDATE action_plans SET payload=? WHERE plan_id=?',
        (json.dumps(plan), plan['plan_id']))
    before = tree_hashes(app.game)
    assert 'error_code' in app.rpc('plan.inspect', app.identity(preview))
    assert 'error_code' in app.approve(preview)
    assert not app.context.store.list('Approval') and tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_nonempty_profile_config_overrides_cannot_disappear_from_preview(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    profile = app.context.store.get('Profile', state['profile_id'])
    profile.config_overrides = {'loader-setting': {'enabled': True}}
    # Preserve the repository's monotonic profile-history contract while
    # arranging a future configuration consumer's nonempty DTO field.
    profile.revision += 1
    app.context.store.connection.execute('UPDATE profiles SET payload=?,revision=? WHERE profile_id=?',
        (profile.model_dump_json(), profile.revision, state['profile_id']))
    state = dict(state, profile_revision=profile.revision)
    before = tree_hashes(app.game)
    assert app.create(state)['error_code'] == 'PLAN_CONFIG_UNSUPPORTED'
    assert not app.context.store.list('ActionPlan') and tree_hashes(app.game) == before
    app.assert_empty_execution()


@pytest.mark.parametrize('mutation', ['unselected_library_blob', 'adoption_partial', 'adoption_missing_file'])
def test_case029_adoption_ownership_receipt_and_unselected_release_revalidated(app, mutation):
    (app.game / 'Mods/existing.png').write_bytes(b'old')
    adopted = app.adopt('Mods/existing.png')
    replacement = app.publish({'Mods/existing.png': b'new'}, name='Replacement')
    state = app.profile([replacement])
    store = app.context.store
    if mutation == 'unselected_library_blob':
        source_hash = store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?',
            (adopted['release_id'],)).fetchone()[0]
        artifact = store.get('Artifact', source_hash)
        (app.context.paths.root / 'library' / artifact.library_relative_path).write_bytes(b'bad')
    else:
        snapshot_id = store.connection.execute('SELECT snapshot_id FROM package_adoptions WHERE release_id=?',
            (adopted['release_id'],)).fetchone()[0]
        snapshot = store.get('BaselineSnapshot', snapshot_id)
        if mutation == 'adoption_partial': snapshot.coverage = 'partial'
        else: snapshot.files = []
        store.connection.execute('UPDATE baseline_snapshots SET payload=? WHERE snapshot_id=?',
            (snapshot.model_dump_json(), snapshot_id))
    before = tree_hashes(app.game)
    assert app.create(state)['error_code'] == 'PLAN_OWNERSHIP_INVALID'
    assert not store.list('ActionPlan') and tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_library_same_size_mtime_drift_invalidates_old_preview(app):
    release = app.publish({'Mods/new.png': b'new'})
    state = app.profile([release])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    source_hash = app.context.store.connection.execute('SELECT source_hash FROM file_entries WHERE release_id=?',
        (release['release_id'],)).fetchone()[0]
    artifact = app.context.store.get('Artifact', source_hash)
    path = app.context.paths.root / 'library' / artifact.library_relative_path
    stamp = path.stat(); path.write_bytes(b'bad'); os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    before = tree_hashes(app.game)
    assert 'error_code' in app.rpc('plan.inspect', app.identity(preview))
    assert 'error_code' in app.approve(preview)
    assert not app.context.store.list('Approval') and tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_draft_revision_is_not_accepted_as_committed_profile_revision(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    assert state['revision'] != state['profile_revision']
    bad = dict(app.proposal(state), expected_revision=state['revision'])
    assert app.rpc('plan.create', bad)['error_code'] == 'PLAN_PROFILE_STALE'
    assert not app.context.store.list('ActionPlan')
    app.assert_empty_execution()


def test_case029_recovery_guard_prevents_native_approval_receipt(app):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    app.context.store.put(Transaction(transaction_id=uuid4(), plan_id=preview['plan']['plan_id'],
        state='recovery_required', journal_path='journals/pending-fixture.jsonl',
        target_generation=uuid4(), last_sequence=1))
    before = tree_hashes(app.game)
    app.context.confirm_plan = lambda _: True
    assert app.approve(preview)['error_code'] == 'GUARD_RECOVERY_REQUIRED'
    assert not app.context.store.list('Approval') and not app.context.store.list('DeploymentGeneration')
    assert not (app.context.paths.root / '.plan-approval-key').exists()
    assert tree_hashes(app.game) == before


@pytest.mark.parametrize('fixture_kind', ['unapproved', 'noop'])
def test_case029_registered_generation_without_native_provenance_is_not_trusted(app, fixture_kind):
    releases = [app.publish({'Mods/new.png': b'new'})] if fixture_kind == 'unapproved' else []
    state = app.profile(releases)
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    plan = app.context.store.get('ActionPlan', preview['plan']['plan_id'])
    generation_id = uuid4()
    transaction = app.context.store.put(Transaction(transaction_id=uuid4(), plan_id=plan.plan_id,
        state='committed', journal_path='journals/untrusted-fixture.jsonl',
        target_generation=generation_id, last_sequence=1))
    app.context.store.put(DeploymentGeneration(generation_id=generation_id,
        installation_id=plan.installation_id, sequence=1, manifest_hash=check_value(plan,'result_manifest'),
        transaction_id=transaction.transaction_id, committed_at=datetime.now(timezone.utc)))
    before = tree_hashes(app.game)
    # PS-03-03 rejects the missing authenticated journal before trusting any
    # claimed committed generation; the planner cannot bypass this guard.
    assert app.create(state)['error_code'] == 'GUARD_RECOVERY_REQUIRED'
    assert len(app.context.store.list('ActionPlan')) == 1 and not app.context.store.list('Approval')
    assert tree_hashes(app.game) == before


def test_case029_new_explicit_adoption_after_generation_can_enter_next_plan(app):
    original = app.publish({'Mods/original.png': b'original'})
    state = app.profile([original])
    first = app.create(state)
    assert 'error_code' not in first, first
    app.historical_generation(first)
    (app.game / 'Mods/adopted.png').write_bytes(b'adopt-old')
    adopted = app.adopt('Mods/adopted.png')
    replacement = app.publish({'Mods/adopted.png': b'adopt-new'}, name='Replacement after generation')
    state = app.commit(app.select(state, replacement))
    before = tree_hashes(app.game)
    next_plan = app.create(state)
    assert 'error_code' not in next_plan, next_plan
    rows = {row['relative_path']: row for row in next_plan['files']}
    assert rows['original.png']['change'] == 'keep'
    assert rows['adopted.png']['change'] == 'replace'
    assert rows['adopted.png']['before_owner_ids'] == [adopted['release_id']]
    assert tree_hashes(app.game) == before
    app.historical_generation(next_plan)
    following = app.create(state)
    assert 'error_code' not in following and following['no_op'], following
    assert len(app.context.store.list('DeploymentGeneration')) == 2


def test_case029_old_adoption_receipt_cannot_reclaim_file_restored_after_removal_generation(app):
    path = app.game / 'Mods/returned.png'
    path.write_bytes(b'historical-owned')
    app.adopt('Mods/returned.png')
    state = app.profile()
    first = app.create(state)
    assert 'error_code' not in first and first['files'][0]['change'] == 'remove', first
    app.historical_generation(first)
    assert not path.exists()
    path.write_bytes(b'historical-owned')
    desired = app.publish({'Mods/returned.png': b'historical-owned'}, name='Restored foreign file')
    state = app.commit(app.select(state, desired))
    before = tree_hashes(app.game)
    assert app.create(state)['error_code'] == 'PLAN_UNMANAGED_CONFLICT'
    assert len(app.context.store.list('ActionPlan')) == len(app.context.store.list('DeploymentGeneration')) == 1
    assert tree_hashes(app.game) == before


@pytest.mark.parametrize('mutation', ['before-plan', 'after-expiry', 'before-predecessor'])
def test_case029_generation_commit_time_rejects_false_adoption_chronology(app, mutation):
    """A forged commit time cannot revive a historical owner of restored bytes."""
    path = app.game / 'Mods/restored.png'
    path.write_bytes(b'old-owned-then-removed')
    adopted = app.adopt('Mods/restored.png')
    state = app.profile()
    first = app.create(state)
    assert 'error_code' not in first and first['files'][0]['change'] == 'remove', first
    generation = app.historical_generation(first)
    assert not path.exists()
    path.write_bytes(b'old-owned-then-removed')  # Foreign restoration, no new receipt.
    store = app.context.store
    first_plan = store.get('ActionPlan', first['plan']['plan_id'])
    if mutation == 'before-plan':
        adopted_at = datetime.fromisoformat(store.connection.execute(
            'SELECT adopted_at FROM package_adoptions WHERE release_id=?',
            (adopted['release_id'],)).fetchone()[0])
        generation.committed_at = min(first_plan.expires_at-timedelta(minutes=10), adopted_at)-timedelta(seconds=1)
    elif mutation == 'after-expiry':
        generation.committed_at = first_plan.expires_at+timedelta(seconds=1)
    else:
        another = app.publish({'Mods/second.png': b'owned-by-second-generation'})
        state = app.commit(app.select(state, another))
        second = app.create(state)
        assert 'error_code' not in second, second
        successor = app.historical_generation(second)
        second_plan = store.get('ActionPlan', second['plan']['plan_id'])
        # Each commit remains inside its own plan's approval interval. Moving
        # the predecessor later specifically violates chain chronology.
        generation.committed_at = first_plan.expires_at-timedelta(minutes=1)
        assert first_plan.expires_at-timedelta(minutes=10) <= generation.committed_at <= first_plan.expires_at
        assert second_plan.expires_at-timedelta(minutes=10) <= successor.committed_at <= second_plan.expires_at
        assert successor.committed_at < generation.committed_at
    store.connection.execute('UPDATE deployment_generations SET payload=? WHERE generation_id=?',
        (generation.model_dump_json(), str(generation.generation_id)))
    before = tree_hashes(app.game)
    plans_before = [item.model_dump(mode='json') for item in store.list('ActionPlan')]
    approvals_before = [item.model_dump(mode='json') for item in store.list('Approval')]
    result = app.create(state)
    assert result['error_code'] == 'PLAN_GENERATION_INVALID'
    assert [item.model_dump(mode='json') for item in store.list('ActionPlan')] == plans_before
    assert [item.model_dump(mode='json') for item in store.list('Approval')] == approvals_before
    assert path.read_bytes() == b'old-owned-then-removed' and tree_hashes(app.game) == before


@pytest.mark.parametrize('mutation', ['missing', 'changed'])
def test_case029_issuer_key_missing_or_changed_invalidates_old_native_approval(app, mutation):
    state = app.profile([app.publish({'Mods/new.png': b'new'})])
    preview = app.create(state)
    assert 'error_code' not in preview, preview
    app.context.confirm_plan = lambda _: True
    approved = app.approve(preview)
    assert approved['approved'], approved
    key = app.context.paths.root / '.plan-approval-key'
    if mutation == 'missing':
        key.unlink()
    else:
        original = key.read_bytes()
        assert len(original) == 32
        key.write_bytes(bytes(value ^ 0xff for value in original))
        assert key.stat().st_size == 32
    before = tree_hashes(app.game)
    with pytest.raises(PlanRejected, match='PLAN_APPROVAL_INVALID'):
        app.context.plans.validate_approval(app.identity(preview), approved['approval']['approval_id'])
    assert not key.exists() if mutation == 'missing' else key.read_bytes() != original
    assert len(app.context.store.list('ActionPlan')) == len(app.context.store.list('Approval')) == 1
    assert tree_hashes(app.game) == before
    app.assert_empty_execution()


def test_case029_adoption_receipt_timestamp_seal_prevents_restored_file_reclaim(app):
    """A receipt sealed while its file is missing cannot later become a new adoption."""
    old = app.game / 'Mods/missing-at-first-plan.png'
    old.write_bytes(b'formerly-adopted')
    adopted = app.adopt('Mods/missing-at-first-plan.png')
    old.unlink()  # Fixture loss before the first signed plan; no current owner.
    state = app.profile([app.publish({'Mods/first-generation.png': b'owned'})])
    first = app.create(state)
    assert 'error_code' not in first and first['plan']['base_generation'] is None, first
    plan = app.context.store.get('ActionPlan', first['plan']['plan_id'])
    receipts = json.loads(check_value(plan, 'adoption_receipts'))
    assert any(receipt['release_id'] == adopted['release_id'] for receipt in receipts)
    assert not any(row['relative_path'] == old.name for row in first['files'])
    generation = app.historical_generation(first)
    old.write_bytes(b'formerly-adopted')  # Foreign restoration, not another adoption.
    forged_time = (generation.committed_at+timedelta(seconds=1)).isoformat()
    app.context.store.connection.execute('UPDATE package_adoptions SET adopted_at=? WHERE release_id=?',
        (forged_time, adopted['release_id']))
    before = tree_hashes(app.game)
    plans_before = [item.model_dump(mode='json') for item in app.context.store.list('ActionPlan')]
    approvals_before = [item.model_dump(mode='json') for item in app.context.store.list('Approval')]
    assert app.create(state)['error_code'] == 'PLAN_OWNERSHIP_INVALID'
    assert [item.model_dump(mode='json') for item in app.context.store.list('ActionPlan')] == plans_before
    assert [item.model_dump(mode='json') for item in app.context.store.list('Approval')] == approvals_before
    assert old.read_bytes() == b'formerly-adopted' and tree_hashes(app.game) == before


@pytest.mark.parametrize('historical_sequence', [1, 2])
@pytest.mark.parametrize('mutation', ['file-content', 'installation', 'partial', 'duplicate-path'])
def test_case029_historical_baseline_seal_rejects_payload_tamper(app, historical_sequence, mutation):
    """Signed historical plans must reverify their complete baseline payloads."""
    (app.game / 'Mods/unknown.json').write_bytes(b'{"foreign":true}')
    original = app.publish({'Mods/owned.png': b'generation-one'})
    state = app.profile([original])
    first = app.create(state)
    assert 'error_code' not in first, first
    app.historical_generation(first)
    newer = app.publish({'Mods/owned.png': b'generation-two'}, name='Second generation')
    state = app.commit(app.select(app.select(state, original, False), newer))
    second = app.create(state)
    assert 'error_code' not in second, second
    app.historical_generation(second)
    chosen = first if historical_sequence == 1 else second
    baseline = app.context.store.get('BaselineSnapshot', chosen['plan']['baseline_snapshot_id'])
    assert baseline.files and baseline.coverage == 'complete'
    payload = baseline.model_dump(mode='json')
    declared_hash = payload['manifest_hash']
    if mutation == 'file-content':
        payload['files'][0]['sha256'] = '0'*64
    elif mutation == 'installation':
        payload['installation_id'] = str(uuid4())
    elif mutation == 'partial':
        payload['coverage'] = 'partial'
    else:
        payload['files'].append(dict(payload['files'][0]))
    assert payload['manifest_hash'] == declared_hash == chosen['plan']['baseline_manifest_hash']
    app.context.store.connection.execute('UPDATE baseline_snapshots SET payload=? WHERE snapshot_id=?',
        (json.dumps(payload), str(baseline.snapshot_id)))
    before = tree_hashes(app.game)
    plans_before = [item.model_dump(mode='json') for item in app.context.store.list('ActionPlan')]
    approvals_before = [item.model_dump(mode='json') for item in app.context.store.list('Approval')]
    assert app.create(state)['error_code'] == 'PLAN_GENERATION_INVALID'
    assert [item.model_dump(mode='json') for item in app.context.store.list('ActionPlan')] == plans_before
    assert [item.model_dump(mode='json') for item in app.context.store.list('Approval')] == approvals_before
    assert len(app.context.store.list('DeploymentGeneration')) == 2 and tree_hashes(app.game) == before
