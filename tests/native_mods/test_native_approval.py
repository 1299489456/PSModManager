"""User approval is lightweight and separate from optional safety inspection."""
import builtins
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess

import pytest

from backend.native_mods import NativeModError
from backend.native_mods.packages import inspect_package, native_fingerprint_details
from backend.native_mods.state import encode, load_state
from tests.native_mods.test_native_service import env, package, approve


def records(state):
    return load_state(state)['trust_document']['records']


def test_apply_authorizes_all_targets_without_scan_or_order_change(env, monkeypatch):
    svc, inst, game, _, workshop, state, _ = env
    a = package(game / 'Mods', 'a', caps=('network', 'files'))
    b = package(workshop, 'b', folder_name='123456')
    first = svc.save(inst, svc.snapshot(inst)['revision'], ['a', 'b'])
    assert records(state) == []
    original = builtins.__import__
    def no_scanner(name, *args, **kwargs):
        assert not name.startswith(('backend.mod_security', 'backend.scanning'))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_scanner)
    result = svc.apply(inst, first['revision'], ['a', 'b'])
    assert result['enabled_ids'] == ['a', 'b'] and result['saved']
    rows = records(state)
    assert [(r['id'], r['source'], r['workshopId']) for r in rows] == [('a', 'Local', '0'), ('b', 'Workshop', '123456')]
    assert rows[0]['declared'] == ['files', 'network']
    for row, folder, source, wid in zip(rows, (a, b), ('local', 'workshop'), (None, '123456')):
        assert row['detected'] is None and row['gameVersion'] == ''
        assert all(row[k] == v for k, v in native_fingerprint_details(inspect_package(folder, source, wid)).items())
        assert row['developerMode'] is False
        assert row['approvedAt'].endswith('0Z') and row['updatedAt'] == row['approvedAt']
        assert row['psmmApproval'] == {'source': 'manager', 'basis': 'user_enable', 'scanStatus': 'not_scanned'}
    for mod in result['mods']:
        assert mod['approval']['state'] == 'approved'
        assert mod['approval']['approval_source'] == 'manager'
        assert mod['approval']['approval_basis'] == 'user_enable'
        assert mod['approval']['scan_status'] == 'not_scanned' and mod['approval']['detected'] is None


def test_apply_reauthorizes_updated_content_preserves_unknowns_deduplicates(env):
    svc, inst, game, _, _, state, _ = env
    a = package(game / 'Mods', 'a'); other = package(game / 'Mods', 'other')
    approve(state, [(a, 'local', None), (other, 'local', None)], ['a'])
    doc = json.loads(state.read_text()); store = json.loads(doc['modTrust']['value'])
    store['records'][0]['developerMode'] = True
    store['records'].append(dict(store['records'][0], unknownRecord='not-first'))
    doc['modTrust']['value'] = json.dumps(store); state.write_bytes(encode(doc))
    before = load_state(state)
    (a / 'Main.dll').write_bytes((a / 'Main.dll').read_bytes() + b'changed')
    result = svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    after = load_state(state)
    assert after['document']['unrelated'] == before['document']['unrelated']
    assert after['document']['modOrder']['unknownOrderField'] == 7
    assert after['document']['modTrust']['unknownWrapper'] == 'retain'
    assert after['trust_document']['unknownTrustField'] == {'a': 1}
    target = next(r for r in records(state) if r['id'] == 'a')
    assert target['unknownRecord'] == {'retain': True} and target['developerMode'] is False
    assert target['detected'] is None
    assert len([r for r in records(state) if r['id'] == 'a']) == 1
    assert next(r for r in records(state) if r['id'] == 'other') == store['records'][1]
    assert next(m for m in result['mods'] if m['id'] == 'a')['approval']['state'] == 'approved'


def test_automatic_checker_is_no_longer_an_apply_input(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a'); before = state.read_bytes()
    with pytest.raises(TypeError, match='approval_check'):
        svc.apply(inst, svc.snapshot(inst)['revision'], ['a'], approval_check=lambda: pytest.fail('must not scan'))
    assert state.read_bytes() == before


def test_apply_without_targets_only_revokes(env):
    svc, inst, game, _, _, state, _ = env
    a = package(game / 'Mods', 'a'); approve(state, [(a, 'local', None)], ['a'])
    svc.apply(inst, svc.snapshot(inst)['revision'], [])
    assert records(state) == [] and load_state(state)['enabled_ids'] == []


def test_resources_are_not_read_or_required_for_basic_approval(env, monkeypatch):
    svc, inst, game, _, _, state, _ = env
    a = package(game / 'Mods', 'a'); asset = a / 'asset.bin'
    asset.write_bytes(b'owned-resource' * (4 * 1024 * 1024))
    original = Path.open
    def no_asset_read(path, *args, **kwargs):
        assert path != asset, 'basic save must not read resource contents'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_asset_read)
    svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    first = records(state)[0]['combinedHash']
    # External resource updates do not invalidate the game's approval protocol.
    with original(asset, 'wb') as stream:
        stream.write(b'replaced resource')
    svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert records(state)[0]['combinedHash'] == first


def test_dll_bytes_not_stat_hints_are_rechecked_before_write(env, monkeypatch):
    svc, inst, game, _, _, state, _ = env
    folder = package(game / 'Mods', 'a'); before = state.read_bytes()
    finish = svc._finish; snapshot = svc.snapshot(inst)
    def mutate(folder_, record, status='committed', **values):
        finish(folder_, record, status, **values)
        if status == 'prepared':
            dll = folder / 'Main.dll'; raw = bytearray(dll.read_bytes()); raw[700] ^= 1; dll.write_bytes(raw)
    monkeypatch.setattr(svc, '_finish', mutate)
    # Simulate metadata/revision hints not revealing the modification.
    monkeypatch.setattr(svc, '_expect', lambda *args: snapshot)
    from backend.native_mods import packages
    original = packages.tree_revision
    monkeypatch.setattr(packages, 'tree_revision', lambda rows: snapshot['mods'][0]['package_revision'])
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.apply(inst, snapshot['revision'], ['a'])
    assert state.read_bytes() == before
    monkeypatch.setattr(packages, 'tree_revision', original)


@pytest.mark.parametrize('changed', ['manifest.xml', 'Main.dll'])
def test_content_drift_after_backup_never_grants(env, monkeypatch, changed):
    svc, inst, game, _, _, state, _ = env
    folder = package(game / 'Mods', 'a'); before = state.read_bytes(); finish = svc._finish
    def mutate(recovery, record, status='committed', **values):
        finish(recovery, record, status, **values)
        if status == 'prepared':
            path = folder / changed; path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(svc, '_finish', mutate)
    with pytest.raises(NativeModError, match='NATIVE_STATE_CHANGED'):
        svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert state.read_bytes() == before
    assert svc.recovery_list(inst)['items'][0]['status'] == 'failed'


@pytest.mark.parametrize('runtime', ['running', 'unknown'])
def test_apply_preserves_process_guard_error_contract(env, runtime):
    from backend.deployments.paths import GuardRejected
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a'); before = state.read_bytes()
    @contextmanager
    def guard(_):
        raise GuardRejected('GUARD_PROCESS_' + runtime.upper())
        yield
    svc.write_guard = guard
    with pytest.raises(GuardRejected, match='GUARD_PROCESS_' + runtime.upper()):
        svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert state.read_bytes() == before and svc.recovery_list(inst)['items'] == []


def test_apply_parses_log_only_for_returned_snapshot(env, monkeypatch):
    svc, inst, game, _, _, _, _ = env
    package(game / 'Mods', 'a'); snapshot = svc.snapshot(inst)
    from backend.diagnostics import native_log
    original = native_log.read_runtime_summary; calls = []
    def read(*args, **kwargs):
        calls.append(1); return original(*args, **kwargs)
    monkeypatch.setattr(native_log, 'read_runtime_summary', read)
    svc.apply(inst, snapshot['revision'], ['a'])
    assert len(calls) == 1


def test_restore_and_order_only_save_never_resurrect_new_approval(env):
    svc, inst, game, _, _, state, _ = env
    package(game / 'Mods', 'a')
    enabled = svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    disabled = svc.security_disable(inst, enabled['revision'], 'a')
    svc.recovery_restore(inst, disabled['recovery_id'], disabled['revision'])
    assert load_state(state)['enabled_ids'] == ['a'] and records(state) == []
    svc.save(inst, svc.snapshot(inst)['revision'], ['a'])
    assert records(state) == []


def test_approval_source_uses_native_case_sensitive_enum(env):
    svc, inst, game, _, _, state, _ = env
    a = package(game / 'Mods', 'a'); approve(state, [(a, 'local', None)], ['a'])
    doc = json.loads(state.read_text()); store = json.loads(doc['modTrust']['value'])
    store['records'][0]['source'] = 'local'; doc['modTrust']['value'] = json.dumps(store)
    state.write_bytes(encode(doc))
    assert svc.snapshot(inst)['mods'][0]['approval']['state'] == 'changed'


def test_workshop_id_uses_native_uint64_decimal_format(env):
    svc, inst, _, _, workshop, state, _ = env
    package(workshop, 'a', folder_name='00123')
    result = svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert records(state)[0]['workshopId'] == '123' and result['mods'][0]['approval']['state'] == 'approved'


@pytest.mark.parametrize('folder_name', ['not-an-id', '0', '18446744073709551616'])
def test_unrepresentable_workshop_id_cannot_get_native_approval(env, folder_name):
    svc, inst, _, _, workshop, state, _ = env
    package(workshop, 'a', folder_name=folder_name); before = state.read_bytes()
    with pytest.raises(NativeModError, match='NATIVE_APPROVAL_SOURCE_UNPROVEN'):
        svc.apply(inst, svc.snapshot(inst)['revision'], ['a'])
    assert state.read_bytes() == before


def test_emitted_record_matches_independent_dotnet_protocol_oracle(env, tmp_path):
    from tests.metadata.test_static_parser import BUILD
    svc, inst, game, _, _, state, _ = env
    folder = package(game / 'Mods', 'protocol', caps=('network', 'files'))
    original = (folder / 'Main.dll').read_bytes(); (folder / 'Main.dll').unlink()
    (folder / 'Assemblies').mkdir()
    (folder / 'Assemblies/模型.DLL').write_bytes(original)
    (folder / 'Other.dll').write_bytes(original + b'other')
    (folder / 'manifest.xml').write_text('''<?xml version="1.0" encoding="utf-8"?>
<Manifest><ID>protocol</ID><Assemblies>
 <Assembly> Other.dll </Assembly><Assembly> Assemblies\\模型.DLL </Assembly>
 <Assembly>other.DLL</Assembly></Assemblies>
 <Assemblies><Assembly>IgnoredMissing.dll</Assembly></Assemblies><Capabilities>
 <Capability><Part>NET</Part>WORK</Capability><Capability>files</Capability></Capabilities>
 <Capabilities><Capability>IgnoredUnknownCapability</Capability></Capabilities></Manifest>''', encoding='utf-8-sig')
    svc.apply(inst, svc.snapshot(inst)['revision'], ['protocol'])
    oracle = BUILD.compile_source(Path(__file__).with_name('fixtures') / 'ApprovalProtocolOracle.cs', tmp_path / 'oracle/ApprovalProtocolOracle.dll')
    run = subprocess.run([BUILD.toolchain()[0], str(oracle), str(folder), str(state), 'Local', '0'],
                         capture_output=True, text=True, encoding='utf-8', timeout=15, check=True)
    evidence = json.loads(run.stdout); record = records(state)[0]
    assert evidence['verdict'] == 'Match' and evidence['dates'] and evidence['detected_is_null']
    assert evidence['files'] == record['files']
    assert [value.split('|')[0] for value in evidence['files']] == ['Other.dll', 'Assemblies/模型.DLL']
    assert evidence['manifestHash'] == record['manifestHash'] and evidence['combinedHash'] == record['combinedHash']
