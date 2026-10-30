"""Current installed game scanner in real LPAC; candidate DLLs remain inert.

PSMM_APPROVAL_GAME_ROOT opts into read-only installed-game evidence. All candidate,
staging, drift and marker files are isolated under pytest temporary directories.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.metadata.test_static_parser import BUILD, ROOT
from backend.scanning import worker_client as client


@pytest.fixture(scope='module')
def approval_helper(tmp_path_factory):
    root = tmp_path_factory.mktemp('approval-helper')
    return BUILD.compile_source(ROOT / 'tools/PSModManager.Metadata/Program.cs', root / 'PSModManager.Metadata.dll').parent


@pytest.fixture(scope='module')
def game_root():
    value = os.environ.get('PSMM_APPROVAL_GAME_ROOT')
    if os.name != 'nt' or not value:
        pytest.skip('read-only installed-game approval evidence requires PSMM_APPROVAL_GAME_ROOT on Windows')
    root = Path(value)
    assert (root / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll').is_file()
    return root


def candidate(tmp_path, source, *, name='Candidate', references=()):
    root = tmp_path / 'candidate'; root.mkdir(exist_ok=True)
    src = tmp_path / (name + '.cs'); src.write_text(source, encoding='utf-8')
    BUILD.compile_source(src, root / (name + '.dll'), library=True, additional_references=references)
    return root


def inspect(tmp_path, helper, game, source, *, name='Candidate', references=()):
    root = candidate(tmp_path, source, name=name, references=references)
    worker = client.MetadataWorker(tmp_path / 'data', helper=helper)
    result = worker.inspect_approval(game, root, [name + '.dll'])
    assert not list((tmp_path / 'data/metadata/staging').iterdir())
    return worker, root, result


def test_current_game_capabilities_preserve_real_scanner_without_executing_candidate(tmp_path, approval_helper, game_root):
    marker = tmp_path / 'constructor-must-not-run.txt'
    source = '''using System.IO; using System.Net.Http;
public class Candidate {
 static Candidate(){File.WriteAllText(@"MARKER", "BAD");}
 public void Observe(){File.ReadAllText("ordinary-config.json"); new HttpClient().GetStringAsync("https://inert.invalid");}
}'''.replace('MARKER', str(marker))
    worker, root, result = inspect(tmp_path, approval_helper, game_root, source)
    assert result['status'] == 'scanned', result
    assert result['error'] is None and not result['refused'] and not result['hard_flags']
    assert set(result['capabilities']) == {'files', 'network'} and result['methods_scanned'] > 0
    assert result['scanner_identity'] == worker.approval_identity(game_root)
    assert result['files'][0]['path'] == 'Candidate.dll' and len(result['files'][0]['sha256']) == 64
    process = result['process']
    assert process['appcontainer'] and process['capabilities'] == 0 and process['lpac_policy_applied']
    assert process['cleanup_remaining'] == 0 and process['profile_deleted'] and process['desktop_closed']
    report = json.loads(process['stdout'])['report']
    assert set(report['loaded_game_libraries']) == client.APPROVAL_LIBRARIES and not report['candidate_loaded']
    assert not marker.exists()


def test_original_scanner_stealer_string_is_hard_even_without_network_call(tmp_path, approval_helper, game_root):
    _, _, result = inspect(tmp_path, approval_helper, game_root,
        'public class Candidate { public string Observe(){return "loginusers.vdf";} }')
    assert result['status'] == 'scanned' and result['error'] is None and result['refused']
    assert any(row['kind'] == 'stealer-strings' for row in result['hard_flags'])


def test_game_library_name_in_candidate_directory_never_enters_clr(tmp_path, approval_helper, game_root):
    marker = tmp_path / 'must-not-run.txt'
    _, _, result = inspect(tmp_path, approval_helper, game_root,
        'public class Candidate { static Candidate(){System.IO.File.WriteAllText(@"' + str(marker) + '","BAD");} }', name='DseCecil')
    assert result['status'] == 'scanned' and result['error'] is None, result
    assert result['capabilities'] == ['files'] and not marker.exists()
    assert set(json.loads(result['process']['stdout'])['report']['loaded_game_libraries']) == client.APPROVAL_LIBRARIES


def test_unlisted_reference_keeps_original_game_hard_refusal(tmp_path, approval_helper, game_root):
    foreign = tmp_path / 'Foreign.cs'; foreign.write_text('public class Foreign { public static void Call(){} }')
    foreign_dll = BUILD.compile_source(foreign, tmp_path / 'Foreign.dll', library=True)
    _, _, result = inspect(tmp_path, approval_helper, game_root,
        'public class Candidate {public void Observe(){Foreign.Call();}}', references=[foreign_dll])
    assert result['status'] == 'scanned' and result['refused']
    assert any(row['kind'] == 'unlisted-reference' for row in result['hard_flags'])


def test_corrupt_candidate_never_becomes_empty_detected_approval(tmp_path, approval_helper, game_root):
    root = tmp_path / 'candidate'; root.mkdir(); (root / 'Bad.dll').write_bytes(b'MZ-inert-invalid')
    result = client.MetadataWorker(tmp_path / 'data', helper=approval_helper).inspect_approval(game_root, root, ['Bad.dll'])
    assert result['status'] == 'scanned' and result['refused'] and result['error']


def test_candidate_update_after_worker_discards_success(tmp_path, approval_helper, game_root, monkeypatch):
    root = candidate(tmp_path, 'public class Candidate {}')
    original = client.run_sandbox
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        with (root / 'Candidate.dll').open('ab') as stream:
            stream.write(b'changed after isolated analysis')
        return result
    monkeypatch.setattr(client, 'run_sandbox', change)
    result = client.MetadataWorker(tmp_path / 'data', helper=approval_helper).inspect_approval(game_root, root, ['Candidate.dll'])
    assert result['status'] == 'changed' and result['capabilities'] is None and result['refused']


def test_game_dependency_content_changes_identity_even_with_preserved_mtime(tmp_path, approval_helper, game_root):
    copied = tmp_path / 'game'
    managed = copied / 'Probably Stolen_Data/Managed'
    shutil.copytree(game_root / 'Probably Stolen_Data/Managed', managed)
    worker = client.MetadataWorker(tmp_path / 'data', helper=approval_helper)
    before = worker.approval_identity(copied)
    dependency = managed / 'System.dll'; stat = dependency.stat()
    with dependency.open('ab') as stream: stream.write(b'inert-content-change')
    os.utime(dependency, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert worker.approval_identity(copied) != before


def test_approval_has_no_ordinary_process_fallback(approval_helper):
    result = subprocess.run([BUILD.toolchain()[0], str(approval_helper / 'PSModManager.Metadata.dll'), '--require-isolation', '--approval'],
        input=b'{"protocol_version":3,"assemblies":["Candidate.dll"]}', capture_output=True, timeout=8)
    assert result.returncode == 2
    assert json.loads(result.stdout) == dict(protocol_version=3, status='sandbox_unavailable', report=None, error='network_boundary_unproven')


@pytest.mark.parametrize('paths', [['../outside.dll'], ['C:/outside.dll'], ['a.dll', 'A.dll'], [], ['script.ps1']])
def test_approval_paths_are_host_bounded_before_worker(tmp_path, paths, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('worker must not start')
    monkeypatch.setattr(client, 'run_sandbox', forbidden)
    result = client.MetadataWorker(tmp_path / 'data').inspect_approval(tmp_path / 'game', tmp_path / 'candidate', paths)
    assert result['status'] == 'incomplete' and result['capabilities'] is None and result['refused']


def test_cancelled_approval_does_not_start_worker(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('worker must not start')
    monkeypatch.setattr(client, 'run_sandbox', forbidden)
    result = client.MetadataWorker(tmp_path / 'data').inspect_approval(tmp_path / 'game', tmp_path / 'candidate', ['Candidate.dll'], cancelled=lambda: True)
    assert result['status'] == 'cancelled' and result['capabilities'] is None


def test_host_rejects_forged_approval_output():
    report = dict(schema_version=1, scanner='current-game-ModScanner.Scan', capabilities=[], findings=[], hard_flags=[],
                  error=None, refused=False, methods_scanned=1, loaded_game_libraries=sorted(client.APPROVAL_LIBRARIES), candidate_loaded=False)
    process = dict(started=True, appcontainer=True, capabilities=0, lpac_policy_applied=True, status='exited',
        cleanup_remaining=0, profile_deleted=True, desktop_closed=True, exit_code=0)
    for change in ({'candidate_loaded': True}, {'capabilities': ['unknown']}, {'refused': False, 'error': 'unreadable'},
                   {'loaded_game_libraries': ['Candidate']}, {'hard_flags': [dict(kind='hard', location='M', target='x', is_hard_flag=True)]}):
        process['stdout'] = json.dumps(dict(protocol_version=3, status='managed', report={**report, **change}, error=None))
        with pytest.raises(ValueError, match='WORKER_PROTOCOL_INVALID'):
            client.decode_approval_response(process)
