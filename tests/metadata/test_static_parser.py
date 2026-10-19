"""Self-authored files, real net10 subprocess, no input assembly execution."""
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('psmm_metadata_build', ROOT / 'tools/PSModManager.Metadata/build.py')
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


@pytest.fixture(scope='module')
def artifacts(tmp_path_factory):
    base = tmp_path_factory.mktemp('static-parser')
    helper = BUILD.compile_source(ROOT / 'tools/PSModManager.Metadata/Program.cs', base / 'helper/PSModManager.Metadata.dll')
    marker = base / 'constructor-must-not-run.txt'
    source = base / 'Fixture.cs'
    source.write_text('''using System;
using System.IO;
using System.Reflection;
[assembly: AssemblyVersion("2.3.4.5")]
[assembly: MelonLoader.MelonInfo(typeof(Fixture), "Static fixture", "0.46D", "Synthetic author")]
[assembly: MelonLoader.MelonGame("Synthetic studio", "Synthetic game")]
public class Fixture { }
namespace MelonLoader {
 [AttributeUsage(AttributeTargets.Assembly)] public class MelonInfoAttribute : Attribute {
  public MelonInfoAttribute(Type type, string name, string version, string author) { File.WriteAllText(@"MARKER", "EXECUTED"); throw new Exception("must never run"); }
 }
 [AttributeUsage(AttributeTargets.Assembly)] public class MelonGameAttribute : Attribute {
  public MelonGameAttribute(string developer, string game) { throw new Exception("must never run"); }
 }
}'''.replace('MARKER', str(marker)), encoding='utf-8')
    resource = base / 'content.json'
    resource.write_text('{"RequiredRuntimeApi":19,"BundleId":"self-authored"}', encoding='utf-8')
    dll = BUILD.compile_source(source, base / 'Fixture.dll', library=True, resources=[(resource, 'ItemForge.items.json')])
    return base, helper, dll, marker, resource, source


def invoke(artifacts, path=None, raw=None, args=()):
    _, helper, dll, *_ = artifacts
    request = raw if raw is not None else json.dumps({'protocol_version': 1, 'input_path': str(path or dll)}).encode()
    result = subprocess.run([BUILD.toolchain()[0], str(helper), *args], input=request, capture_output=True, timeout=8)
    assert result.stderr == b''
    assert len(result.stdout) <= 256 * 1024
    return result.returncode, json.loads(result.stdout)


def test_isolation_required_refuses_unsandboxed_process_before_input_open(artifacts):
    code, output = invoke(artifacts, artifacts[0] / 'missing-must-not-open.dll', args=('--require-isolation',))
    assert code == 2
    assert output == {'protocol_version': 1, 'status': 'sandbox_unavailable', 'report': None,
                      'error': 'network_boundary_unproven'}


@pytest.mark.parametrize('args', [('--unknown',), ('--require-isolation', '--extra')])
def test_unknown_arguments_rejected(artifacts, args):
    code, output = invoke(artifacts, args=args)
    assert code == 2 and output['status'] == 'invalid_request'


def test_identity_resources_and_attributes_are_static(artifacts):
    base, _, dll, marker, resource, _ = artifacts
    before = hashlib.sha256(dll.read_bytes()).hexdigest()
    code, output = invoke(artifacts)
    assert code == 0 and output['status'] == 'managed'
    report = output['report']
    assert report['assembly_name'] == 'Fixture'
    assert report['assembly_version'] == '2.3.4.5'
    assert report['machine'] == 'I386' and report['any_cpu'] is True
    assert report['cli_flags'] & 1
    assert any(r['name'] == 'System.Runtime' for r in report['references'])
    info = next(a for a in report['custom_attributes'] if a['type'] == 'MelonLoader.MelonInfoAttribute')
    assert info['decoded'] and info['arguments'][1:] == ['Static fixture', '0.46D', 'Synthetic author']
    game = next(a for a in report['custom_attributes'] if a['type'] == 'MelonLoader.MelonGameAttribute')
    assert game['arguments'] == ['Synthetic studio', 'Synthetic game']
    item = report['resources'][0]
    assert item['sha256'] == hashlib.sha256(resource.read_bytes()).hexdigest()
    assert json.loads(item['json_text'])['RequiredRuntimeApi'] == 19
    assert not marker.exists()
    assert before == hashlib.sha256(dll.read_bytes()).hexdigest()
    assert invoke(artifacts)[1] == output


@pytest.mark.parametrize('platform,machine,any_cpu', [('x86', 'I386', False), ('x64', 'Amd64', False), ('anycpu', 'I386', True)])
def test_cli_machine_flags(artifacts, platform, machine, any_cpu):
    base, _, _, _, _, source = artifacts
    path = BUILD.compile_source(source, base / f'{platform}.dll', library=True, platform=platform)
    code, response = invoke(artifacts, path)
    assert code == 0
    assert response['report']['machine'] == machine
    assert response['report']['any_cpu'] == any_cpu


def test_native_is_not_corrupt(artifacts):
    base, _, dll, *_ = artifacts
    data = bytearray(dll.read_bytes())
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    # PE32 data directory entry 14 is the CLR header; remove it in an inert copy.
    struct.pack_into('<II', data, pe + 24 + 96 + 14 * 8, 0, 0)
    path = base / 'native-header-only.dll'
    path.write_bytes(data)
    code, output = invoke(artifacts, path)
    assert code == 0 and output['status'] == 'native'
    assert output['report']['assembly_name'] is None


def test_cli_header_without_metadata_is_corrupt(artifacts):
    base, _, dll, *_ = artifacts
    data = bytearray(dll.read_bytes())
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    section_count = struct.unpack_from('<H', data, pe + 6)[0]
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    cli_rva = struct.unpack_from('<I', data, pe + 24 + 96 + 14 * 8)[0]
    section_table = pe + 24 + optional_size
    for i in range(section_count):
        offset = section_table + 40 * i
        size, rva, raw_size, raw_offset = struct.unpack_from('<IIII', data, offset + 8)
        if rva <= cli_rva < rva + max(size, raw_size):
            cli_offset = raw_offset + cli_rva - rva
            break
    else:
        raise AssertionError('fixture CLI section missing')
    struct.pack_into('<II', data, cli_offset + 8, 0, 0)
    path = base / 'MissingMetadata.dll'
    path.write_bytes(data)
    code, output = invoke(artifacts, path)
    assert code == 2 and output['status'] == 'corrupt'


@pytest.mark.parametrize('data', [b'', b'not a PE', b'MZ' + b'\0' * 80])
def test_corrupt_pe_is_explicit(artifacts, data):
    path = artifacts[0] / 'corrupt.dll'
    path.write_bytes(data)
    code, output = invoke(artifacts, path)
    assert code == 2 and output['status'] == 'corrupt'


@pytest.mark.parametrize('raw', [b'{}', b'{', b'[]', b'{"protocol_version":2,"input_path":"x"}',
    b'{"protocol_version":1,"input_path":"relative.dll"}',
    b'{"protocol_version":1,"input_path":"x","extra":true}',
    b'{"protocol_version":1,"protocol_version":1,"input_path":"x"}', b'{}\n{}'])
def test_request_rejected(artifacts, raw):
    code, output = invoke(artifacts, raw=raw)
    assert code == 2 and output['status'] == 'invalid_request'


def test_request_byte_limit(artifacts):
    code, output = invoke(artifacts, raw=b' ' * (16 * 1024 + 1))
    assert code == 2 and output['status'] == 'limit_exceeded'


def test_file_size_limit(artifacts):
    path = artifacts[0] / 'oversized.dll'
    with path.open('wb') as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    code, output = invoke(artifacts, path)
    assert code == 2 and output['error'] == 'input_bytes'


def test_resource_limit(artifacts):
    base, _, _, _, _, source = artifacts
    resource = base / 'oversize.bin'
    resource.write_bytes(b'x' * (256 * 1024 + 1))
    dll = BUILD.compile_source(source, base / 'Oversize.dll', library=True, resources=[(resource, 'oversize.bin')])
    code, output = invoke(artifacts, dll)
    assert code == 0 and output['status'] == 'managed'
    report = output['report']
    assert report['assembly_name'] == 'Oversize'
    assert report['resource_coverage']['status'] == 'partial'
    assert report['resource_coverage']['skipped_payload_count'] == 1
    assert report['resources'][0]['reason'] == 'resource_bytes'
    assert report['resources'][0]['sha256'] is None
    assert report['resources'][0]['json_text'] is None
    assert any(a['type'] == 'MelonLoader.MelonInfoAttribute' for a in report['custom_attributes'])
    assert not artifacts[3].exists()


def test_invalid_json_resource_retains_hash(artifacts):
    base, _, _, _, _, source = artifacts
    resource = base / 'bad.json'
    resource.write_bytes(b'\xffinvalid-json')
    dll = BUILD.compile_source(source, base / 'InvalidJson.dll', library=True, resources=[(resource, 'bad.json')])
    code, output = invoke(artifacts, dll)
    assert code == 0
    assert output['report']['resources'][0]['json_text'] is None
    assert output['report']['resources'][0]['sha256'] == hashlib.sha256(resource.read_bytes()).hexdigest()


def test_missing_file_is_io_error(artifacts):
    code, output = invoke(artifacts, artifacts[0] / 'missing.dll')
    assert code == 2 and output['status'] == 'io_error'


def test_output_limit_omits_optional_resources_preserving_identity(artifacts):
    base, _, _, _, _, source = artifacts
    resource = base / 'large.json'
    # Valid under the per-resource limit; JSON escaping in the envelope exceeds stdout cap.
    resource.write_text(json.dumps({'data': '\\' * 100000}), encoding='utf-8')
    dll = BUILD.compile_source(source, base / 'LargeOutput.dll', library=True, resources=[(resource, 'large.json')])
    code, output = invoke(artifacts, dll)
    assert code == 0 and output['status'] == 'managed'
    assert output['report']['assembly_name'] == 'LargeOutput'
    assert output['report']['resources'] == []
    assert output['report']['resource_coverage']['output_limited'] is True
    assert output['report']['resource_coverage']['status'] == 'partial'


def test_truncated_embedded_resource_is_corrupt(artifacts):
    base, _, dll, _, resource, _ = artifacts
    data = bytearray(dll.read_bytes())
    location = data.index(resource.read_bytes())
    struct.pack_into('<i', data, location - 4, 0x7fffffff)
    path = base / 'BrokenResource.dll'
    path.write_bytes(data)
    code, output = invoke(artifacts, path)
    assert code == 2 and output['status'] == 'corrupt'


def test_invalid_custom_attribute_is_marked_without_execution(artifacts):
    base, _, dll, marker, *_ = artifacts
    data = bytearray(dll.read_bytes())
    location = data.index(b'Synthetic studio')
    # String payload preceded by SerString length and two-byte custom-attribute prolog.
    data[location - 3:location - 1] = b'\0\0'
    path = base / 'BrokenAttribute.dll'
    path.write_bytes(data)
    code, output = invoke(artifacts, path)
    assert code == 0
    attr = next(a for a in output['report']['custom_attributes'] if a['type'] == 'MelonLoader.MelonGameAttribute')
    assert attr['decoded'] is False and attr['error'] == 'invalid_attribute_blob'
    assert not marker.exists()


def test_resource_count_limit(artifacts):
    base, _, _, _, resource, source = artifacts
    dll = BUILD.compile_source(source, base / 'ManyResources.dll', library=True,
                               resources=[(resource, f'item-{i}.json') for i in range(65)])
    code, output = invoke(artifacts, dll)
    assert code == 0 and output['status'] == 'managed'
    assert output['report']['assembly_name'] == 'ManyResources'
    assert len(output['report']['resources']) == 64
    assert output['report']['resource_coverage'] == dict(status='partial', total_count=65,
        reported_count=64, omitted_count=1, skipped_payload_count=0, output_limited=False)


def test_resource_aggregate_cap_preserves_identity(artifacts):
    base, _, _, _, _, source = artifacts
    resource = base / 'quarter-megabyte.bin'
    resource.write_bytes(b'x' * (256 * 1024))
    dll = BUILD.compile_source(source, base / 'Aggregate.dll', library=True,
                              resources=[(resource, f'payload-{i}.bin') for i in range(5)])
    code, output = invoke(artifacts, dll)
    assert code == 0 and output['status'] == 'managed'
    report = output['report']
    assert report['assembly_name'] == 'Aggregate'
    assert sum(r['size'] for r in report['resources'] if r.get('sha256')) == 1024 * 1024
    assert report['resources'][-1]['reason'] == 'resource_total_bytes'
    assert report['resource_coverage']['skipped_payload_count'] == 1


def test_skipped_large_resource_does_not_consume_read_budget(artifacts):
    base, _, _, _, valid, source = artifacts
    resource = base / 'large-optional.bin'
    resource.write_bytes(b'x' * (1024 * 1024 + 1))
    dll = BUILD.compile_source(source, base / 'Optional.dll', library=True,
        resources=[(resource, 'large.bin'), (valid, 'ItemForge.items.json')])
    code, output = invoke(artifacts, dll)
    assert code == 0 and output['status'] == 'managed'
    assert output['report']['resources'][0]['status'] == 'skipped_limit'
    assert output['report']['resources'][1]['sha256'] == hashlib.sha256(valid.read_bytes()).hexdigest()
    assert json.loads(output['report']['resources'][1]['json_text'])['RequiredRuntimeApi'] == 19
