"""Safety disposition uses native recovery, including disabled historic approval."""
import json
from pathlib import Path
import shutil

import pytest

from backend.native_mods import NativeModError
from backend.native_mods.state import load_state
from tests.native_mods.test_native_service import env, package, approve


def test_security_disable_revokes_disabled_target_and_enabled_dependents(env):
    svc, inst, game, _, workshop, state, _ = env
    target = package(workshop, 'target', folder_name='12345')
    addon = package(game / 'Mods', 'addon', dependencies=('target',))
    other = package(game / 'Mods', 'other')
    # Target is already disabled, but a historical approval and enabled dependent remain.
    approve(state, [(target, 'workshop', '12345'), (addon, 'local', None), (other, 'local', None)], ['addon', 'other'])
    original = state.read_bytes()
    package_bytes = {p.name: p.read_bytes() for p in target.iterdir()}
    result = svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target')
    assert result['enabled_ids'] == ['other'] and result['affected_ids'] == ['addon']
    assert [r['id'] for r in load_state(state)['trust_document']['records']] == ['other']
    assert json.loads(state.read_text())['unrelated'] == json.loads(original)['unrelated']
    assert package_bytes == {p.name: p.read_bytes() for p in target.iterdir()}
    recovery = svc._home(inst) / 'recovery' / result['recovery_id']
    assert (recovery / 'before.bin').read_bytes() == original


def test_security_disable_can_reduce_risk_with_unrelated_invalid_plan(env):
    svc, inst, game, _, _, state, _ = env
    target = package(game / 'Mods', 'target')
    broken = package(game / 'Mods', 'broken', dependencies=('missing',))
    approve(state, [(target, 'local', None), (broken, 'local', None)], ['target', 'broken'])
    result = svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target')
    assert result['enabled_ids'] == ['broken']
    assert [r['id'] for r in load_state(state)['trust_document']['records']] == ['broken']


def test_security_disable_retains_drift_and_live_process_guards(env):
    svc, inst, game, _, _, state, lease = env
    package(game / 'Mods', 'target')
    snap = svc.snapshot(inst)
    state.write_bytes(state.read_bytes() + b' ')
    before = state.read_bytes()
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.security_disable(inst, snap['revision'], 'target')
    assert state.read_bytes() == before
    lease.fail_at = lease.calls + 1
    with pytest.raises(NativeModError, match='NATIVE_PROCESS_UNKNOWN'):
        svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target')
    assert state.read_bytes() == before


def test_local_quarantine_journal_retains_security_operation_identity(env):
    svc, inst, game, _, _, _, _ = env
    target = package(game / 'Mods', 'target')
    original = {p.name: p.read_bytes() for p in target.iterdir()}
    result = svc.local_remove(inst, 'target', svc.snapshot(inst)['revision'], operation_id='c' * 32)
    record = next(r for r in svc.recovery_list(inst)['items'] if r['id'] == result['recovery_id'])
    assert record['security_operation_id'] == 'c' * 32
    assert record['mod_id'] == 'target' and record['kind'] == 'local_remove'
    assert not target.exists()
    svc.recovery_restore(inst, record['id'], result['revision'])
    assert original == {p.name: p.read_bytes() for p in target.iterdir()}


def test_security_disable_removes_missing_quarantined_id_before_restore(env):
    svc, inst, game, _, _, state, _ = env
    target = package(game / 'Mods', 'target')
    approve(state, [(target, 'local', None)], ['target'])
    original = state.read_bytes()
    svc.local_remove(inst, 'target', svc.snapshot(inst)['revision'])
    assert not target.exists()
    # Another process restores stale intent/approval while the package is absent.
    state.write_bytes(original)
    result = svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target')
    assert result['enabled_ids'] == [] and load_state(state)['trust_document']['records'] == []


def test_risk_package_restore_requires_exact_security_operation_and_verification(env):
    svc, inst, game, _, _, _, _ = env
    target = package(game / 'Mods', 'target')
    (target / 'Main.dll').write_bytes(b'corrupt quarantine fixture')
    removed = svc.local_remove(inst, 'target', svc.snapshot(inst)['revision'], operation_id='d' * 32)
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_INVALID'):
        svc.recovery_restore(inst, removed['recovery_id'], removed['revision'])
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_INVALID'):
        svc.recovery_restore(inst, removed['recovery_id'], removed['revision'], security_operation_id='e' * 32,
                             verify_package=lambda p: None)
    checked = []
    def verify(p):
        assert (Path(p['folder_path']) / 'Main.dll').read_bytes() == b'corrupt quarantine fixture'
        checked.append(p['folder_path'])
    result = svc.recovery_restore(inst, removed['recovery_id'], removed['revision'],
        security_operation_id='d' * 32, verify_package=verify)
    assert len(checked) == 2 and result['enabled_ids'] == []
    assert (target / 'Main.dll').read_bytes() == b'corrupt quarantine fixture'


def test_security_recovery_permit_covers_only_own_pending_journal(env):
    svc, inst, game, _, _, _, _ = env
    target = package(game / 'Mods', 'target')
    folder, record = svc._new_recovery(inst, 'local_remove', 'owned interrupted quarantine',
        metadata={'mod_id': 'target', 'folder_name': 'target', 'security_operation_id': 'f' * 32})
    shutil.copytree(target, folder / 'package')
    svc._finish(folder, record, 'prepared')
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_PENDING'):
        svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target')
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_PENDING'):
        svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target', operation_id='a' * 32, recovery=True)
    result = svc.security_disable(inst, svc.snapshot(inst)['revision'], 'target', operation_id='f' * 32, recovery=True)
    assert not result['runtime']['writable']  # Native package recovery remains to be consumed.
    restored = svc.recovery_restore(inst, record['id'], result['revision'], security_operation_id='f' * 32,
        verify_package=lambda p: None)
    assert restored['runtime']['writable'] and svc._pending(inst) == []
