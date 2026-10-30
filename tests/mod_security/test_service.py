"""Only isolated fixture files. No Mod execution, Steam, Defender, or uploads."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import struct
import threading
import time

import pytest

from backend.app.paths import AppPaths
from backend.mod_security import ModSecurityService, SecurityError
from backend.mod_security.defender import classify
from backend.mod_security.evidence import content
from backend.native_mods import NativeModService, NativeModError
from backend.native_mods.state import ORDER_TYPE, encode
from backend.runtime.events import RuntimePolicyError


def pe():
    raw = bytearray(1024); raw[:2] = b'MZ'
    struct.pack_into('<I', raw, 60, 128); raw[128:132] = b'PE\0\0'
    struct.pack_into('<H', raw, 134, 1); struct.pack_into('<H', raw, 148, 224)
    struct.pack_into('<H', raw, 152, 0x10b)
    struct.pack_into('<II', raw, 152 + 96 + 14 * 8, 0x2000, 72)
    struct.pack_into('<IIII', raw, 152 + 224 + 8, 512, 0x2000, 512, 512)
    struct.pack_into('<II', raw, 520, 0x2048, 128); raw[584:588] = b'BSJB'; raw[600:605] = b'IMod\0'
    return bytes(raw)


def package(root, ident, deps=(), folder=None):
    path = root / (folder or ident); path.mkdir(parents=True)
    (path / 'Main.dll').write_bytes(pe())
    (path / 'manifest.xml').write_text('<Manifest><ID>' + ident + '</ID><Name>' + ident + '</Name>'
        '<Assemblies><Assembly>Main.dll</Assembly></Assemblies><Capabilities/><Prerequisites>' +
        ''.join('<Mod>' + dep + '</Mod>' for dep in deps) + '</Prerequisites></Manifest>', encoding='utf8')
    return path


class Worker:
    def __init__(self):
        self.calls = []; self.change = None; self.partial = False; self.entered = threading.Event(); self.release = None
        self.calls_evidence = []; self.strings = []; self.resources = []; self.limited = False

    def inspect_security(self, root, relative, *, cancelled=lambda: False):
        self.calls.append(relative); self.entered.set()
        if self.release is not None:
            while not self.release.wait(.01):
                if cancelled():
                    return dict(sha256=None, status='cancelled', metadata=None)
        raw = (root / relative).read_bytes()
        if self.change:
            self.change()
        return dict(status='managed', sha256=hashlib.sha256(raw).hexdigest(), metadata={'security': dict(
            schema_version=2, analyzer='psmm-static-security-v2',
            coverage=dict(status='partial' if self.partial else 'complete', reasons=['test'] if self.partial else [],
                          evidence_limited=self.limited, omitted_evidence_counts={'calls': 1} if self.limited else {}),
            sizes=dict(embedded_resource_payload_bytes=0), calls=self.calls_evidence, pinvokes=[], strings=self.strings,
            resources=self.resources, declarations=[])})


class AV:
    def __init__(self, state='unavailable'):
        self.calls = 0; self.state = state
    def scan(self, root, *, cancelled=lambda: False):
        self.calls += 1
        return dict(state=self.state, engine='Microsoft Defender', detections=['Fixture:Test'] if self.state == 'detected' else [])


@pytest.fixture
def env(tmp_path):
    game, data, workshop, manager = [tmp_path / p for p in ('game','data','workshop','manager')]
    for p in (game / 'Mods', data, workshop, manager, game / 'Probably Stolen_Data/Managed', game / 'MonoBleedingEdge'):
        p.mkdir(parents=True, exist_ok=True)
    (game / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll').write_bytes(b'identity')
    inst = dict(installation_id='test-install', canonical_root=str(game), persistent_root=str(data), workshop_root=str(workshop))
    runtime = dict(state='stopped', writable=True)
    @contextmanager
    def guard(_):
        class Lease:
            def check(self):
                if runtime['state'] != 'stopped':
                    raise NativeModError('NATIVE_PROCESS_UNKNOWN')
        lease = Lease(); lease.check(); yield lease
    paths = AppPaths(manager)
    native = NativeModService(paths, write_guard=guard, process_status=lambda _: runtime.copy())
    state = data / 'SaveFile.es3'
    state.write_bytes(encode({'modOrder': {'__type': ORDER_TYPE, 'value': []},
                            'modTrust': {'__type': 'string', 'value': '{"records":[]}'}}))
    worker, av = Worker(), AV()
    svc = ModSecurityService(paths, native, worker=worker, antivirus=av)
    result = dict(svc=svc, native=native, inst=inst, game=game, data=data, workshop=workshop, paths=paths,
                  state=state, worker=worker, av=av, runtime=runtime)
    yield result
    svc.close()


def enable(env, ids, trust=False):
    doc = json.loads(env['state'].read_text())
    doc['modOrder']['value'] = ids
    if trust:
        doc['modTrust']['value'] = json.dumps({'records': [{'id': i, 'source': 'Workshop'} for i in ids]})
    env['state'].write_bytes(encode(doc))


def scan(env, **kwargs):
    svc, inst = env['svc'], env['inst']
    svc.scan(inst, **kwargs)
    deadline = time.monotonic() + 5
    while svc.is_busy() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not svc.is_busy()
    result = svc.status(inst)
    assert result['operation']['state'] == 'completed', result
    return result


def test_content_scan_then_launch_and_persist_without_antivirus(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    assert env['worker'].calls == []
    result = scan(env)
    assert result['items'][0]['state'] == 'reviewed'
    assert not env['av'].calls
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    other = ModSecurityService(env['paths'], env['native'], worker=Worker(), antivirus=AV())
    assert other.check_launch(env['inst']) == dict(checked_ids=[], content_hashes={}); other.close()


def test_all_resources_bind_report_and_content_diff(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    (p / 'texture.bin').write_bytes(b'old'); (p / 'removed.txt').write_bytes(b'remove')
    scan(env)
    old = (p / 'texture.bin').stat()
    (p / 'texture.bin').write_bytes(b'new'); os.utime(p / 'texture.bin', ns=(old.st_atime_ns, old.st_mtime_ns))
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    (p / 'removed.txt').unlink(); (p / 'added.json').write_text('{}')
    scan(env)
    report = env['svc'].detail(env['inst'], 'a')['report']
    assert [f['path'] for f in report['diff']['modified']] == ['texture.bin']
    assert [f['path'] for f in report['diff']['added']] == ['added.json']
    assert [f['path'] for f in report['diff']['removed']] == ['removed.txt']
    assert env['worker'].calls == ['Main.dll']  # File cache; package content still fully read.


@pytest.mark.parametrize('suffix,data', [('hidden.bin',pe()), ('bad.dll',b'not a valid DLL')])
def test_all_executable_candidates_including_disguised_are_parsed(env, suffix, data):
    p = package(env['game'] / 'Mods', 'a'); (p / suffix).write_bytes(data)
    # Same PE bytes use the legitimate per-content cache; distinctive bytes force a call.
    (p / suffix).write_bytes(data + b'extra')
    scan(env)
    assert suffix in env['worker'].calls
    assert any(f['rule'] == 'unlisted_pe' for f in env['svc'].detail(env['inst'], 'a')['report']['findings'])


@pytest.mark.parametrize('kind', ['partial', 'limited', 'script', 'archive', 'embedded'])
def test_missing_coverage_stays_incomplete_without_becoming_a_launch_gate(env, kind):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].partial = kind == 'partial'; env['worker'].limited = kind == 'limited'
    if kind == 'script': (p / 'run.ps1').write_text('Write-Output inert')
    if kind == 'archive': (p / 'hidden.bin').write_bytes(b'PK\x03\x04inert')
    if kind == 'embedded': env['worker'].resources = [dict(name='x', header_kind='pe')]
    result = scan(env)
    assert result['items'][0]['state'] == 'incomplete'
    assert not result['items'][0]['blocking'] and result['items'][0]['blocking_reasons'] == []
    assert 'SECURITY_INCOMPLETE' in result['items'][0]['review_reasons']
    assert env['svc'].detail(env['inst'], 'a')['report']['coverage']['status'] == 'incomplete'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_isolated_worker_failure_is_incomplete(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].inspect_security = lambda *a, **k: dict(status='hash_only', sha256=hashlib.sha256(pe()).hexdigest(), error='HELPER_UNAVAILABLE', metadata=None)
    result = scan(env)
    assert result['items'][0]['state'] == 'incomplete'


def test_change_during_worker_not_attached_to_old_report(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].change = lambda: (p / 'later.txt').write_text('new')
    result = scan(env)
    assert not result['items'][0]['blocking']
    assert 'SECURITY_CONTENT_CHANGED' in result['items'][0]['review_reasons']
    assert env['svc'].detail(env['inst'], 'a')['report']['coverage']['status'] == 'incomplete'


def test_network_reflection_and_pinvoke_not_high_risk(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].calls_evidence = [dict(method='M',offset=1,target='System.Net.HttpClient::Send',category='network'),
                                   dict(method='M',offset=2,target='System.Reflection.Assembly::Load',category='reflection')]
    result = scan(env)
    assert result['items'][0]['verdict'] != 'high_risk'
    assert not any(f['severity'] == 'high' for f in env['svc'].detail(env['inst'], 'a')['report']['findings'])
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_sensitive_string_only_requires_review_not_malware(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].strings = [dict(method='M',offset=0,value='Login Data')]
    result = scan(env)
    assert result['items'][0]['state'] == 'attention'
    assert not result['items'][0]['blocking']
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


@pytest.mark.parametrize('combination', ['all','no_string','no_read','no_send','different_method','overload','local_shadow','unknown_scope','wrong_key'])
def test_high_risk_requires_concrete_same_method_combination(env, combination):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    if combination != 'no_string': env['worker'].strings = [dict(method='M',method_token='0x06000001',offset=0,value='Login Data')]
    if combination != 'no_read': env['worker'].calls_evidence.append(dict(method='M',method_token='0x06000001',offset=3,target='System.IO.File::ReadAllBytes',category='files'))
    if combination != 'no_send': env['worker'].calls_evidence.append(dict(method='Other' if combination == 'different_method' else 'M',method_token='0x06000002' if combination in ('different_method','overload') else '0x06000001',offset=8,target='System.Net.Http.HttpClient::PostAsync',category='network'))
    for call in env['worker'].calls_evidence:
        name = 'mscorlib' if call['category'] == 'files' else 'System.Net.Http'
        call['target_kind'] = 'member_reference'
        call['declaring_scope'] = dict(kind='assembly_reference', assembly=dict(name=name,culture='',
            public_key_token='b77a5c561934e089' if name == 'mscorlib' else 'b03f5f7f11d50a3a'))
        if combination == 'local_shadow': call['declaring_scope']['kind'] = 'local'
        if combination == 'unknown_scope': call.pop('declaring_scope')
        if combination == 'wrong_key': call['declaring_scope']['assembly']['public_key_token'] = '0000000000000000'
    result = scan(env)
    if combination == 'all':
        assert result['items'][0]['verdict'] == 'high_risk'
        assert result['items'][0]['state'] == 'high_risk'
        assert 'SECURITY_HIGH_RISK' in result['items'][0]['review_reasons']
        assert not any(f['id'] == 'DEFENDER' for f in env['svc'].detail(env['inst'], 'a')['report']['findings'])
    else:
        assert result['items'][0]['verdict'] != 'high_risk'
    assert not result['items'][0]['blocking'] and result['items'][0]['blocking_reasons'] == []
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


@pytest.mark.parametrize('avstate', ['unavailable','incomplete','clean','detected'])
def test_optional_defender_separate_from_static_coverage(env, avstate):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); env['av'].state = avstate
    result = scan(env, antivirus=True)
    report = env['svc'].detail(env['inst'], 'a')['report']
    assert report['coverage']['status'] == 'complete' and report['antivirus']['state'] == avstate
    if avstate == 'detected':
        assert report['verdict'] == 'high_risk' and result['items'][0]['state'] == 'high_risk'
        assert any(f['id'] == 'DEFENDER' and f['severity'] == 'high' for f in report['findings'])
        assert 'SECURITY_HIGH_RISK' in result['items'][0]['review_reasons']
    assert not result['items'][0]['blocking']
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_defender_ambiguous_exit_two_not_malware():
    assert classify(2, 'An error occurred')['state'] == 'incomplete'
    assert classify(2, 'Threat Name: Trojan:Win32/Test!a')['state'] == 'detected'
    assert classify(0, 'Scan finished')['state'] == 'incomplete'
    assert classify(0, '')['state'] == 'incomplete'
    assert classify(0, 'No threats found.')['state'] == 'clean'


def test_bad_persistent_index_blocks_launch_not_browsing(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); scan(env)
    (env['paths'].root / 'mod-security/test-install/state.json').write_text('{broken')
    assert env['svc'].status(env['inst'])['error'] == 'SECURITY_RECORD_INVALID'
    assert env['svc'].detail(env['inst'], 'a')['report'] is None
    with pytest.raises(SecurityError, match='SECURITY_RECORD_INVALID'): env['svc'].check_launch(env['inst'])


def test_missing_report_file_is_report_error_but_not_launch_gate(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); scan(env)
    report = env['svc'].detail(env['inst'], 'a')['report']
    (env['paths'].root / 'mod-security/test-install/reports' / (report['report_id'] + '.json')).unlink()
    assert env['svc'].status(env['inst'])['error'] == 'SECURITY_RECORD_INVALID'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_async_busy_and_close_drains_cancelled_worker(env):
    package(env['game'] / 'Mods', 'a'); env['worker'].release = threading.Event()
    accepted = env['svc'].scan(env['inst'])
    assert accepted['scanning'] and env['worker'].entered.wait(2)
    with pytest.raises(SecurityError, match='SECURITY_BUSY'): env['svc'].scan(env['inst'])
    env['svc'].close()
    assert not env['svc'].is_busy()
    with pytest.raises(SecurityError, match='SECURITY_CLOSED'): env['svc'].scan(env['inst'])


def test_manual_scan_in_progress_does_not_delay_or_inspect_ordinary_launch(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    env['worker'].release = threading.Event()
    env['svc'].scan(env['inst'])
    assert env['worker'].entered.wait(2)
    def forbidden(*args, **kwargs):
        raise AssertionError('ordinary launch must not inspect Mod content or reports')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(env['native'], 'snapshot', forbidden)
            patch.setattr(env['svc'].store, 'report', forbidden)
            patch.setattr('backend.mod_security.service.content', forbidden)
            patch.setattr(env['worker'], 'inspect_security', forbidden)
            start = time.monotonic()
            assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
            assert time.monotonic() - start < .3
            assert env['svc'].is_busy() and env['worker'].calls == ['Main.dll']
            assert not env['av'].calls
    finally:
        env['worker'].release.set()


def test_unscanned_launch_does_not_enumerate_packages_or_call_hash_parser(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    def forbidden(*args, **kwargs):
        raise AssertionError('manual analysis is not an ordinary launch dependency')
    monkeypatch.setattr(env['native'], 'snapshot', forbidden)
    monkeypatch.setattr(env['svc'].store, 'report', forbidden)
    monkeypatch.setattr('backend.mod_security.service.content', forbidden)
    monkeypatch.setattr(env['worker'], 'inspect_security', forbidden)
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    assert env['worker'].calls == [] and not env['av'].calls


def test_activity_reports_memory_progress_without_inventory_disk_or_analysis(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); package(env['game'] / 'Mods', 'b')
    env['worker'].release = threading.Event()
    env['svc'].scan(env['inst'])
    assert env['worker'].entered.wait(2)
    def forbidden(*args, **kwargs):
        raise AssertionError('activity must only read the in-memory operation')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(env['native'], 'snapshot', forbidden)
            patch.setattr(env['svc'].store, 'load', forbidden)
            patch.setattr(env['svc'].store, 'report', forbidden)
            patch.setattr('backend.mod_security.service.content', forbidden)
            patch.setattr(env['worker'], 'inspect_security', forbidden)
            start = time.monotonic()
            result = env['svc'].activity(env['inst'])
            assert time.monotonic() - start < .3
            assert result['installation_id'] == 'test-install' and result['scanning']
            assert result['operation']['state'] == 'running'
            assert result['operation']['done'] == 0 and result['operation']['total'] == 2
            result['operation']['done'] = 999
            result['operation']['ids'].append('not-real')
            second = env['svc'].activity(env['inst'])
            assert second['operation']['done'] == 0 and second['operation']['ids'] == ['a', 'b']
            unknown = env['svc'].activity(dict(env['inst'], installation_id='other-install'))
            assert unknown == dict(installation_id='other-install', scanning=False, operation=None)
    finally:
        env['worker'].release.set()
    deadline = time.monotonic() + 5
    while env['svc'].is_busy() and time.monotonic() < deadline:
        time.sleep(.01)
    result = env['svc'].activity(env['inst'])
    assert not result['scanning'] and result['operation']['state'] == 'completed'
    assert result['operation']['done'] == 2


def test_disabled_incomplete_does_not_block_enabled_other_package(env):
    package(env['game'] / 'Mods', 'a'); b = package(env['game'] / 'Mods', 'b')
    (b / 'script.ps1').write_text('inert'); enable(env, ['a']); scan(env)
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_different_sources_require_new_report(env):
    local = package(env['game'] / 'Mods', 'a'); package(env['workshop'], 'a', folder='123')
    enable(env, ['a']); scan(env)
    # Native local_remove is used only on the isolated fixture and leaves disabled.
    env['native'].local_remove(env['inst'], 'a', env['native'].snapshot(env['inst'])['revision'])
    assert not local.exists()
    enable(env, ['a'])
    assert env['svc'].status(env['inst'])['items'][0]['state'] == 'unscanned'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_workshop_quarantine_disable_dependencies_persist_restore_no_enable(env):
    p = package(env['workshop'], 'a', folder='123'); package(env['game'] / 'Mods', 'b', ['a'])
    enable(env, ['a','b'], trust=True); before = (p / 'Main.dll').read_bytes()
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    assert preview['affected_ids'] == ['a','b'] and preview['effect'] == 'logical_quarantine_files_remain'
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    assert p.exists() and (p / 'Main.dll').read_bytes() == before
    assert env['native'].snapshot(env['inst'])['enabled_ids'] == []
    assert json.loads(json.loads(env['state'].read_text())['modTrust']['value'])['records'] == []
    other = ModSecurityService(env['paths'], env['native'], worker=Worker(), antivirus=AV())
    try:
        enable(env, ['a']); (p / 'updated.txt').write_text('update')
        with pytest.raises(SecurityError, match='SECURITY_QUARANTINED'): other.check_launch(env['inst'])
        other.restore(env['inst'], record['id'])
        assert env['native'].snapshot(env['inst'])['enabled_ids'] == []
        assert other.status(env['inst'])['items'][0]['state'] == 'unscanned'
    finally: other.close()


def test_local_quarantine_restore_package_without_trust_or_enable(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'], trust=True)
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    result = env['svc'].dispose(env['inst'], preview['preview_id'])
    assert not p.exists() and result['record']['package_recovery_id']
    env['svc'].restore(env['inst'], result['record']['id'])
    assert p.exists() and env['native'].snapshot(env['inst'])['enabled_ids'] == []
    assert json.loads(json.loads(env['state'].read_text())['modTrust']['value'])['records'] == []


def test_disabled_stale_approval_is_revoked(env):
    package(env['workshop'], 'a', folder='123'); enable(env, ['a'], trust=True); enable(env, [])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'disable')
    env['svc'].dispose(env['inst'], preview['preview_id'])
    assert json.loads(json.loads(env['state'].read_text())['modTrust']['value'])['records'] == []


@pytest.mark.parametrize('state', ['running','unknown'])
def test_running_and_unknown_reject_disposition(env, state):
    p = package(env['game'] / 'Mods', 'a')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    env['runtime']['state'] = state
    with pytest.raises(SecurityError, match='SECURITY_PROCESS_UNSAFE'): env['svc'].dispose(env['inst'], preview['preview_id'])
    assert p.exists()


def test_preview_content_drift_rejected(env):
    p = package(env['game'] / 'Mods', 'a')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    (p / 'changed.txt').write_text('change')
    with pytest.raises(SecurityError, match='SECURITY_CONTENT_CHANGED'): env['svc'].dispose(env['inst'], preview['preview_id'])
    assert p.exists()


def test_guard_failure_after_prepared_leaves_persistent_block(env, monkeypatch):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    def fail(*args, **kwargs): raise NativeModError('NATIVE_PROCESS_UNKNOWN')
    monkeypatch.setattr(env['native'], 'security_disable', fail)
    with pytest.raises(NativeModError): env['svc'].dispose(env['inst'], preview['preview_id'])
    assert p.exists() and env['svc'].status(env['inst'])['records'][0]['status'] == 'failed'
    with pytest.raises(SecurityError, match='SECURITY_QUARANTINED'): env['svc'].check_launch(env['inst'])


def test_check_launch_never_acquires_native_write_lease(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); scan(env)
    def fail(*args): raise AssertionError('nested lease')
    monkeypatch.setattr(env['native'], 'write_guard', fail)
    env['runtime']['writable'] = False  # The caller already owns the lease.
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_crash_after_local_move_recovers_exact_native_journal_token(env, monkeypatch):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    original = env['svc']._record_update
    def crash(installation, record):
        if record.get('package_recovery_id'): raise SystemExit('simulated process exit')
        return original(installation, record)
    monkeypatch.setattr(env['svc'], '_record_update', crash)
    with pytest.raises(SystemExit): env['svc'].dispose(env['inst'], preview['preview_id'])
    assert not p.exists()
    record = env['svc'].status(env['inst'])['records'][0]
    assert record['status'] == 'prepared' and record['package_recovery_id'] is None
    env['svc'].restore(env['inst'], record['id'])
    assert p.exists() and env['native'].snapshot(env['inst'])['enabled_ids'] == []


def test_crash_after_native_restore_retries_exact_content_without_enabling(env, monkeypatch):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    original = env['svc'].store.save
    def crash(inst, state, expected):
        if any(r['status'] == 'restored' for r in state['records']): raise SystemExit('simulated process exit')
        return original(inst, state, expected)
    monkeypatch.setattr(env['svc'].store, 'save', crash)
    with pytest.raises(SystemExit): env['svc'].restore(env['inst'], record['id'])
    assert p.exists()
    monkeypatch.setattr(env['svc'].store, 'save', original)
    env['svc'].restore(env['inst'], record['id'])
    assert env['native'].snapshot(env['inst'])['enabled_ids'] == []


def test_report_write_failure_preserves_incomplete_evidence_without_launch_gate(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); scan(env)
    def fail(*args): raise OSError('fixture disk failure')
    monkeypatch.setattr(env['svc'].store, 'put_report', fail)
    env['svc'].scan(env['inst'])
    deadline = time.monotonic() + 5
    while env['svc'].is_busy() and time.monotonic() < deadline: time.sleep(.01)
    assert env['svc'].status(env['inst'])['operation']['state'] == 'failed'
    other = ModSecurityService(env['paths'], env['native'], worker=Worker(), antivirus=AV())
    try:
        item = other.status(env['inst'])['items'][0]
        assert item['state'] == 'incomplete' and 'SECURITY_INCOMPLETE' in item['review_reasons']
        assert not item['blocking']
        assert other.check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    finally: other.close()


def test_valid_json_report_corruption_is_diagnosed_but_does_not_gate_launch(env):
    package(env['game'] / 'Mods', 'a'); package(env['game'] / 'Mods', 'b'); enable(env, ['a']); scan(env)
    report = env['svc'].detail(env['inst'], 'a')['report']
    path = env['paths'].root / 'mod-security/test-install/reports' / (report['report_id'] + '.json')
    report['findings'] = []
    report['scanned_at'] = 'changed'
    path.write_bytes(encode(report))
    status = env['svc'].status(env['inst'])
    assert status['error'] == 'SECURITY_RECORD_INVALID' and status['state'] == 'error'
    assert status['blocking_reasons'] == []
    items = {item['id']: item for item in status['items']}
    assert items['a']['state'] == 'unavailable' and not items['a']['blocking']
    assert items['a']['review_reasons'] == ['SECURITY_RECORD_INVALID']
    assert items['b']['state'] == 'reviewed' and items['b']['report_id']
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})
    damaged = path.read_bytes()
    assert scan(env, ids=['a'])['error'] is None
    fresh = env['svc'].detail(env['inst'], 'a')['report']
    assert fresh['report_id'] != report['report_id']
    assert fresh['diff']['baseline_error'] == 'SECURITY_RECORD_INVALID'
    assert fresh['diff']['baseline_report_id'] is None
    assert path.read_bytes() == damaged


def test_ordinary_resource_display_truncation_does_not_make_rules_incomplete(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    original = env['worker'].inspect_security
    def resource_sample(*args, **kwargs):
        result = original(*args, **kwargs)
        sec = result['metadata']['security']
        sec['coverage'].update(evidence_limited=True, omitted_evidence_counts={'resources': 800})
        sec['resource_kind_counts'] = {'png': 844}
        return result
    env['worker'].inspect_security = resource_sample
    result = scan(env)
    assert result['items'][0]['state'] == 'reviewed'
    assert env['svc'].detail(env['inst'], 'a')['report']['coverage']['status'] == 'complete'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_resource_payload_truncation_is_explicit_incomplete(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    original = env['worker'].inspect_security
    def resource_sample(*args, **kwargs):
        result = original(*args, **kwargs)
        result['metadata']['security']['resource_kind_counts'] = {'pe': 100}
        return result
    env['worker'].inspect_security = resource_sample
    result = scan(env)
    assert result['items'][0]['state'] == 'incomplete'


def test_changed_cache_payload_is_reparsed_not_trusted(env):
    package(env['game'] / 'Mods', 'a'); scan(env)
    cache = next((env['paths'].root / 'mod-security-analysis').glob('*.json'))
    envelope = json.loads(cache.read_text())
    envelope['result']['metadata']['security']['calls'] = [{'bad': 'data'}]
    cache.write_bytes(encode(envelope))
    scan(env)
    assert len(env['worker'].calls) == 2


def test_host_guard_failure_is_not_hidden_by_record_only_launch_check(env):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a']); scan(env)
    count = 0
    def guard():
        nonlocal count
        count += 1
        raise RuntimePolicyError('GUARD_PROCESS_RUNNING')
    with pytest.raises(RuntimePolicyError, match='GUARD_PROCESS_RUNNING'):
        env['svc'].check_launch(env['inst'], check=guard)
    assert count == 1


def test_restore_removes_external_missing_id_intent_and_approval(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    enable(env, ['a'], trust=True)
    env['svc'].restore(env['inst'], record['id'])
    assert p.exists() and env['native'].snapshot(env['inst'])['enabled_ids'] == []
    assert json.loads(json.loads(env['state'].read_text())['modTrust']['value'])['records'] == []


def test_source_disappears_during_scan_is_incomplete(env, monkeypatch):
    package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    def limited(*args): raise NativeModError('NATIVE_PACKAGE_LIMIT')
    monkeypatch.setattr('backend.mod_security.service.content', limited)
    assert scan(env)['items'][0]['state'] == 'incomplete'


def test_local_rename_does_not_release_source_quarantine(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    package(env['game'] / 'Mods', 'a', folder='renamed')
    scan(env); enable(env, ['a'])
    with pytest.raises(SecurityError, match='SECURITY_QUARANTINED'): env['svc'].check_launch(env['inst'])
    with pytest.raises(SecurityError, match='SECURITY_CONTENT_CHANGED'): env['svc'].restore(env['inst'], record['id'])
    assert not p.exists()


def test_no_imod_marker_local_shadow_cannot_authorize_workshop_fallback(env):
    p = package(env['game'] / 'Mods', 'a'); package(env['workshop'], 'a', folder='123'); enable(env, ['a'])
    (p / 'Main.dll').write_bytes(pe().replace(b'IMod\0', b'AAAA\0'))
    scan(env)
    report = env['svc'].detail(env['inst'], 'a')['report']
    assert any(g['reason'] == 'source_resolution_unproven' for g in report['coverage']['gaps'])
    assert report['coverage']['status'] == 'incomplete'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


def test_imod_marker_crosses_stream_chunk_boundary(env):
    p = package(env['game'] / 'Mods', 'a'); enable(env, ['a'])
    raw = pe().replace(b'IMod\0', b'AAAA\0') + b'x' * (256 * 1024 - 2 - 1024) + b'IMod\0'
    (p / 'Main.dll').write_bytes(raw)
    scan(env)
    assert env['svc'].detail(env['inst'], 'a')['report']['coverage']['status'] == 'complete'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


@pytest.mark.parametrize('local', [False,True])
def test_workshop_source_order_is_not_guessed(env, local):
    package(env['workshop'], 'a', folder='123'); package(env['workshop'], 'a', folder='456')
    if local: package(env['game'] / 'Mods', 'a')
    enable(env, ['a']); scan(env)
    report = env['svc'].detail(env['inst'], 'a')['report']
    if local:
        assert not any(g['reason'] == 'source_ambiguous' for g in report['coverage']['gaps'])
    else:
        assert any(g['reason'] == 'source_ambiguous' for g in report['coverage']['gaps'])
        assert report['coverage']['status'] == 'incomplete'
    assert env['svc'].check_launch(env['inst']) == dict(checked_ids=[], content_hashes={})


@pytest.mark.parametrize('bad', ['broken_declared','unlisted_pe'])
def test_security_restore_returns_exact_risky_package_still_disabled(env, bad):
    p = package(env['game'] / 'Mods', 'a')
    if bad == 'broken_declared': (p / 'Main.dll').write_bytes(b'inert broken PE')
    else: (p / 'hidden.bin').write_bytes(pe())
    before = {f.name:f.read_bytes() for f in p.iterdir()}
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    env['svc'].restore(env['inst'], record['id'])
    assert {f.name:f.read_bytes() for f in p.iterdir()} == before
    assert env['native'].snapshot(env['inst'])['enabled_ids'] == []


def test_owned_pending_native_package_journal_recovers_from_security_page(env):
    p = package(env['game'] / 'Mods', 'a')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    journal = env['paths'].root / 'native-mods/test-install/recovery' / record['package_recovery_id'] / 'record.json'
    doc = json.loads(journal.read_text(encoding='utf-8')); doc['status'] = 'failed'; journal.write_bytes(encode(doc))
    assert env['native'].snapshot(env['inst'])['runtime']['writable'] is False
    env['svc'].restore(env['inst'], record['id'])
    assert p.exists() and env['native'].snapshot(env['inst'])['runtime']['writable'] is True


def test_other_pending_operation_does_not_receive_recovery_authority(env):
    package(env['game'] / 'Mods', 'a'); package(env['game'] / 'Mods', 'b')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    snap = env['native'].snapshot(env['inst'])
    other = env['native'].security_disable(env['inst'], snap['revision'], 'b', operation_id='f' * 32)
    journal = env['paths'].root / 'native-mods/test-install/recovery' / other['recovery_id'] / 'record.json'
    doc = json.loads(journal.read_text(encoding='utf-8')); doc['status'] = 'failed'; journal.write_bytes(encode(doc))
    with pytest.raises(NativeModError, match='NATIVE_RECOVERY_PENDING'): env['svc'].restore(env['inst'], record['id'])


def test_changed_backup_is_not_restored(env):
    p = package(env['game'] / 'Mods', 'a')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    backup = env['paths'].root / 'native-mods/test-install/recovery' / record['package_recovery_id'] / 'package/Main.dll'
    backup.write_bytes(b'changed inert data')
    with pytest.raises(SecurityError, match='SECURITY_CONTENT_CHANGED'): env['svc'].restore(env['inst'], record['id'])
    assert not p.exists()


def test_restore_recovers_published_but_pending_derived_import(env, monkeypatch):
    p = package(env['game'] / 'Mods', 'a')
    preview = env['svc'].dispose_preview(env['inst'], 'a', 'quarantine')
    record = env['svc'].dispose(env['inst'], preview['preview_id'])['record']
    original = env['native'].recovery_restore
    def crash(*args, **kwargs):
        result = original(*args, **kwargs)
        journal = env['paths'].root / 'native-mods/test-install/recovery' / result['recovery_id'] / 'record.json'
        doc = json.loads(journal.read_text(encoding='utf-8'))
        assert doc['security_restore_of'] == record['package_recovery_id']
        doc['status'] = 'prepared'; journal.write_bytes(encode(doc))
        raise SystemExit('fixture crash after publication')
    monkeypatch.setattr(env['native'], 'recovery_restore', crash)
    with pytest.raises(SystemExit): env['svc'].restore(env['inst'], record['id'])
    assert p.exists() and env['native'].snapshot(env['inst'])['runtime']['writable'] is False
    monkeypatch.setattr(env['native'], 'recovery_restore', original)
    env['svc'].restore(env['inst'], record['id'])
    assert env['native'].snapshot(env['inst'])['runtime']['writable'] is True
    assert env['native'].snapshot(env['inst'])['enabled_ids'] == []
