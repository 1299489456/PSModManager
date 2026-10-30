"""Bounded read-only staging and strict single-request metadata protocol."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath
import shutil
import threading
import time
from uuid import uuid4

from backend.games.probably_stolen.layout import approved_root, safe_path, signature
from .worker_sandbox import run_sandbox

MAX_INPUT = 64 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(2)
HELPER = Path(__file__).resolve().parents[2] / 'tools/PSModManager.Metadata/bin'


def relative_file(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('Invalid metadata relative path')
    path = PureWindowsPath(value)
    if path.drive or path.root or any(part in ('.','..') or part.rstrip(' .') != part
                                     or ':' in part or any(c in '<>"|?*' or ord(c)<32 for c in part) for part in path.parts):
        raise ValueError('Invalid metadata relative path')
    # Check lexical spelling before pathlib can silently normalize it.
    if any(part in ('','.','..') for part in value.replace('\\','/').split('/')):
        raise ValueError('Invalid metadata relative path')
    return '/'.join(path.parts)


def owned_directory(path):
    """Reject redirection before creating a manager-owned staging directory."""
    path = Path(path).absolute()
    if str(path).startswith('\\\\') or any(p == '..' or ':' in p or p.rstrip(' .') != p for p in path.parts[1:]):
        raise ValueError('Invalid data directory')
    for current in (path, *path.parents):
        if current.exists() and (current.is_symlink() or current.lstat().st_file_attributes & 0x400):
            raise ValueError('Redirected data directory')
    path.mkdir(parents=True, exist_ok=True)
    return path


def _decode_envelope(process, version):
    if (not process['started'] or not process['appcontainer'] or process['capabilities'] != 0
            or not process.get('lpac_policy_applied')):
        raise ValueError('SANDBOX_UNAVAILABLE')
    if process['status'] != 'exited': raise ValueError(process['status'].upper())
    if (process['cleanup_remaining'] != 0 or not process.get('profile_deleted')
            or not process.get('desktop_closed')
            or process.get('console_host', {}).get('cleanup_remaining', 0) != 0):
        raise ValueError('WORKER_CLEANUP_FAILED')
    if process['exit_code'] == 0xC0000142 and not process['stdout']:
        raise ValueError('WORKER_INITIALIZATION_FAILED')
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate worker field')
            result[key] = value
        return result
    try:
        value = json.loads(process['stdout'], object_pairs_hook=unique_fields if version in (2, 3) else None)
    except (ValueError, TypeError):
        raise ValueError('WORKER_PROTOCOL_INVALID') from None
    if (not isinstance(value,dict) or set(value) != {'protocol_version','status','report','error'}
            or type(value['protocol_version']) is not int or value['protocol_version'] != version
            or value['status'] not in ('managed','native','corrupt','limit_exceeded','invalid_request','io_error','sandbox_unavailable')):
        raise ValueError('WORKER_PROTOCOL_INVALID')
    success = value['status'] in ('managed','native')
    if process['exit_code'] != (0 if success else 2): raise ValueError('WORKER_EXIT_MISMATCH')
    if success and (not isinstance(value['report'],dict) or value['error'] is not None):
        raise ValueError('WORKER_PROTOCOL_INVALID')
    if not success and (value['report'] is not None or not isinstance(value['error'],str)):
        raise ValueError('WORKER_PROTOCOL_INVALID')
    return value


def decode_response(process):
    value = _decode_envelope(process, 1)
    success = value['status'] in ('managed', 'native')
    if success:
        report = value['report']
        expected = {'machine','cli_flags','any_cpu','assembly_name','assembly_version','references','custom_attributes','resources','resource_coverage'}
        if set(report) != expected or any(not isinstance(report[k],list) for k in ('references','custom_attributes','resources')):
            raise ValueError('WORKER_PROTOCOL_INVALID')
        coverage=report['resource_coverage']
        if (not isinstance(coverage,dict) or set(coverage)!={'status','total_count','reported_count','omitted_count','skipped_payload_count','output_limited'}
                or coverage['status'] not in ('complete','partial') or type(coverage['output_limited']) is not bool
                or any(type(coverage[k]) is not int or coverage[k]<0 for k in ('total_count','reported_count','omitted_count','skipped_payload_count'))
                or coverage['reported_count']!=len(report['resources']) or coverage['reported_count']>64
                or coverage['reported_count']+coverage['omitted_count']!=coverage['total_count']
                or coverage['skipped_payload_count']>64
                or coverage['status']=='complete' and (coverage['omitted_count'] or coverage['skipped_payload_count'] or coverage['output_limited'])):
            raise ValueError('WORKER_PROTOCOL_INVALID')
    return value


SECURITY_CATEGORIES = frozenset(('dynamic_load', 'dynamic_code', 'process_start', 'network', 'native_interop',
    'registry', 'credential_access', 'file_delete', 'file_write', 'file_read', 'filesystem', 'reflection', 'open_url', 'indirect_call'))
SECURITY_COUNTS = frozenset(('methods_total', 'methods_examined', 'methods_with_body', 'methods_scanned', 'methods_skipped',
    'instructions_scanned', 'call_sites_total', 'sensitive_calls_total', 'member_references_total', 'member_references_scanned',
    'sensitive_declarations_total', 'pinvokes_total', 'strings_scanned', 'interesting_strings_total', 'resources_total',
    'resources_scanned', 'embedded_resources', 'external_resources', 'suspicious_resources_total', 'unresolved_tokens'))
SECURITY_SIZES = frozenset(('file_bytes', 'metadata_bytes', 'method_il_bytes', 'scanned_il_bytes',
    'managed_resource_directory_bytes', 'embedded_resource_payload_bytes'))
SECURITY_LIMITS = dict(input_bytes=MAX_INPUT, output_bytes=256*1024, methods=50000, member_references=50000,
    resources=16384, method_il_bytes=1024*1024, total_il_bytes=24*1024*1024, instructions=2000000,
    string_chars=512, evidence_per_list=64, call_evidence=128)
SECURITY_LISTS = ('declarations', 'calls', 'pinvokes', 'strings', 'resources')
SECURITY_LIMITATIONS = ('static_instruction_presence_not_execution', 'no_control_flow_or_dataflow',
    'no_dependency_loading', 'no_resource_deserialization_or_unpacking', 'no_deobfuscation',
    'no_runtime_generated_code', 'strings_only_from_ldstr')


def decode_security_response(process):
    """Untrusted worker output must not manufacture complete/security evidence."""
    value = _decode_envelope(process, 2)
    if value['status'] not in ('managed', 'native'):
        return value
    def require(condition):
        if not condition:
            raise ValueError('WORKER_PROTOCOL_INVALID')
    def integer(v, maximum=2**53):
        return type(v) is int and 0 <= v <= maximum
    def string(v, maximum=512):
        return isinstance(v, str) and len(v) <= maximum
    def token(v):
        return isinstance(v, str) and re.fullmatch(r'0x[0-9A-F]{8}', v) is not None
    def scope(value):
        require(isinstance(value, dict) and set(value) == {'kind', 'token', 'assembly'} and
                value['kind'] in ('local', 'assembly_reference', 'module_reference', 'indirect', 'unresolved'))
        require(value['token'] is None or token(value['token']))
        if value['kind'] == 'assembly_reference':
            require(value['token'] is not None and value['token'].startswith('0x23'))
            assembly = value['assembly']
            require(isinstance(assembly, dict) and set(assembly) == {'name', 'version', 'culture', 'public_key_token'} and
                    string(assembly['name']) and string(assembly['version'], 64) and string(assembly['culture']))
            require(assembly['public_key_token'] is None or isinstance(assembly['public_key_token'], str) and
                    re.fullmatch('[0-9a-f]{16}', assembly['public_key_token']) is not None)
        else:
            require(value['assembly'] is None)
            prefix = {'local': ('0x02', '0x00'), 'module_reference': ('0x1A',), 'indirect': ('0x11',)}.get(value['kind'])
            if prefix:
                require(value['token'] is not None and value['token'].startswith(prefix))
    require(len(process['stdout'].encode('utf-8')) <= 256*1024)
    report = value['report']
    require(set(report) == {'machine', 'cli_flags', 'any_cpu', 'assembly_name', 'assembly_version', 'references', 'security'})
    require(string(report['machine']) and type(report['any_cpu']) is bool)
    require(report['cli_flags'] is None or integer(report['cli_flags'], 0xffffffff))
    require(report['assembly_name'] is None or string(report['assembly_name']))
    require(report['assembly_version'] is None or string(report['assembly_version'], 64))
    require(isinstance(report['references'], list) and len(report['references']) <= 256)
    for row in report['references']:
        require(isinstance(row, dict) and set(row) == {'name', 'version'} and string(row['name']) and string(row['version'], 64))
    security = report['security']
    require(isinstance(security, dict) and set(security) == {'schema_version', 'analyzer', 'coverage', 'sizes', 'counts',
        'category_counts', 'resource_kind_counts', *SECURITY_LISTS})
    require(type(security['schema_version']) is int and security['schema_version'] == 2 and security['analyzer'] == 'psmm-static-security-v2')
    coverage = security['coverage']
    require(isinstance(coverage, dict) and set(coverage) == {'status', 'reasons', 'limitations', 'evidence_limited', 'omitted_evidence_counts', 'limits'})
    require(coverage['status'] in ('complete', 'partial') and type(coverage['evidence_limited']) is bool)
    for key in ('reasons', 'limitations'):
        require(isinstance(coverage[key], list) and len(coverage[key]) <= 32 and all(string(v, 80) for v in coverage[key]))
    require(coverage['limitations'] == list(SECURITY_LIMITATIONS))
    require(coverage['status'] == ('partial' if coverage['reasons'] else 'complete'))
    require(isinstance(coverage['limits'], dict) and coverage['limits'] == SECURITY_LIMITS and
            all(type(v) is int for v in coverage['limits'].values()))
    omitted = coverage['omitted_evidence_counts']
    require(isinstance(omitted, dict) and set(omitted) == set(SECURITY_LISTS) and all(integer(v) for v in omitted.values()))
    require(coverage['evidence_limited'] == any(omitted.values()))
    for key, fields in (('sizes', SECURITY_SIZES), ('counts', SECURITY_COUNTS)):
        require(isinstance(security[key], dict) and set(security[key]) == fields and all(integer(v) for v in security[key].values()))
    counts, sizes = security['counts'], security['sizes']
    require(sizes['file_bytes'] <= MAX_INPUT and sizes['scanned_il_bytes'] <= SECURITY_LIMITS['total_il_bytes'])
    for scanned, total in (('methods_examined', 'methods_total'), ('methods_with_body', 'methods_examined'),
                           ('resources_scanned', 'resources_total'), ('member_references_scanned', 'member_references_total'),
                           ('interesting_strings_total', 'strings_scanned'), ('sensitive_calls_total', 'call_sites_total')):
        require(counts[scanned] <= counts[total])
    require(counts['methods_scanned'] + counts['methods_skipped'] <= counts['methods_with_body'])
    require(sizes['scanned_il_bytes'] <= sizes['method_il_bytes'])
    require(counts['embedded_resources'] + counts['external_resources'] <= counts['resources_total'])
    require(counts['methods_examined'] <= 50000 and counts['resources_scanned'] <= 16384 and
            counts['member_references_scanned'] <= 50000 and counts['instructions_scanned'] <= 2000000)
    if coverage['status'] == 'complete':
        require(counts['methods_examined'] == counts['methods_total'] and
                counts['methods_scanned'] == counts['methods_with_body'] and counts['methods_skipped'] == 0 and
                counts['member_references_scanned'] == counts['member_references_total'] and
                counts['resources_scanned'] == counts['resources_total'] and counts['unresolved_tokens'] == 0)
    kinds = {'pe', 'zip', '7z', 'rar', 'gzip', 'png', 'jpeg', 'gif', 'riff', 'ogg', 'other'}
    for key, allowed in (('category_counts', SECURITY_CATEGORIES), ('resource_kind_counts', kinds)):
        require(isinstance(security[key], dict) and set(security[key]).issubset(allowed) and all(integer(v) for v in security[key].values()))
    require(sum(security['category_counts'].values()) == counts['sensitive_calls_total'])
    require(sum(security['resource_kind_counts'].values()) + counts['external_resources'] == counts['resources_scanned'])
    require(sum(security['resource_kind_counts'].get(kind, 0) for kind in ('pe', 'zip', '7z', 'rar', 'gzip')) == counts['suspicious_resources_total'])
    for key in SECURITY_LISTS:
        require(isinstance(security[key], list) and len(security[key]) <= (128 if key == 'calls' else 64))
        for row in security[key]:
            require(isinstance(row, dict))
            if key == 'declarations':
                require(set(row) == {'kind', 'category', 'target', 'token', 'declaring_scope'} and row['kind'] in ('member_reference', 'pinvoke') and
                        row['category'] in SECURITY_CATEGORIES and string(row['target'], 1025) and token(row['token']))
                scope(row['declaring_scope'])
            elif key == 'calls':
                require(set(row) == {'method', 'method_token', 'offset', 'opcode', 'target', 'target_token', 'target_kind', 'declaring_scope', 'category'} and
                    string(row['method']) and token(row['method_token']) and integer(row['offset'], 1024*1024) and
                    row['opcode'] in ('call', 'callvirt', 'newobj', 'jmp', 'calli') and string(row['target']) and
                    token(row['target_token']) and row['category'] in SECURITY_CATEGORIES and
                    row['target_kind'] in ('method_definition', 'member_reference', 'method_specification', 'indirect', 'unresolved'))
                scope(row['declaring_scope'])
                expected_prefix = {'method_definition': '0x06', 'member_reference': '0x0A',
                                   'method_specification': '0x2B', 'indirect': '0x11'}.get(row['target_kind'])
                if expected_prefix:
                    require(row['target_token'].startswith(expected_prefix))
            elif key == 'pinvokes':
                require(set(row) == {'method', 'method_token', 'module', 'entry_point', 'flags'} and
                    string(row['method']) and token(row['method_token']) and string(row['module']) and
                    string(row['entry_point']) and integer(row['flags'], 65535))
            elif key == 'strings':
                require(set(row) == {'method', 'method_token', 'offset', 'kind', 'value'} and string(row['method']) and
                    token(row['method_token']) and integer(row['offset'], 1024*1024) and string(row['value']) and
                    row['kind'] in ('url', 'sensitive_path', 'command_or_persistence', 'path'))
            else:
                require(set(row) == {'name', 'embedded', 'offset', 'size', 'header_kind'} and string(row['name']) and
                    type(row['embedded']) is bool and integer(row['offset']) and
                    (row['size'] is None if not row['embedded'] else integer(row['size'], MAX_INPUT)) and
                    row['header_kind'] in kinds | {'external'})
    for key, total in (('declarations', 'sensitive_declarations_total'), ('calls', 'sensitive_calls_total'),
                       ('pinvokes', 'pinvokes_total'), ('strings', 'interesting_strings_total'), ('resources', 'resources_scanned')):
        require(len(security[key]) + omitted[key] == counts[total])
    if value['status'] == 'native':
        require(coverage['status'] == 'partial' and 'native_code_not_analyzed' in coverage['reasons'])
    return value


APPROVAL_CAPABILITIES = frozenset(('files', 'network', 'process', 'dynamic-code', 'native', 'reflection-external'))
APPROVAL_LIBRARIES = frozenset(('Assembly-CSharp', 'DseCecil', '0Harmony', 'UnityEngine.CoreModule'))
APPROVAL_IDENTITY_SCHEMA = 'current-game-modscanner-lpac-v1'


def decode_approval_response(process):
    value = _decode_envelope(process, 3)
    if value['status'] != 'managed':
        return value
    def require(condition):
        if not condition:
            raise ValueError('WORKER_PROTOCOL_INVALID')
    def text(value):
        return isinstance(value, str) and len(value) <= 4096
    require(len(process['stdout'].encode('utf-8')) <= 256 * 1024)
    report = value['report']
    require(set(report) == {'schema_version', 'scanner', 'capabilities', 'findings', 'hard_flags', 'error',
                           'refused', 'methods_scanned', 'loaded_game_libraries', 'candidate_loaded'})
    require(type(report['schema_version']) is int and report['schema_version'] == 1 and
            report['scanner'] == 'current-game-ModScanner.Scan' and report['candidate_loaded'] is False)
    caps = report['capabilities']
    require(isinstance(caps, list) and len(caps) <= 6 and all(isinstance(c, str) for c in caps))
    require(len(set(caps)) == len(caps) and set(caps) <= APPROVAL_CAPABILITIES)
    loaded = report['loaded_game_libraries']
    require(isinstance(loaded, list) and all(isinstance(v, str) for v in loaded) and
            len(loaded) == len(APPROVAL_LIBRARIES) and set(loaded) == APPROVAL_LIBRARIES)
    require(type(report['methods_scanned']) is int and 0 <= report['methods_scanned'] <= 2**31-1)
    for key, hard, limit in (('findings', False, 400), ('hard_flags', True, 100)):
        require(isinstance(report[key], list) and len(report[key]) <= limit)
        for row in report[key]:
            require(isinstance(row, dict) and set(row) == {'kind', 'location', 'target', 'is_hard_flag'})
            require(all(text(row[k]) for k in ('kind', 'location', 'target')) and row['is_hard_flag'] is hard)
            if not hard:
                require(row['kind'] in APPROVAL_CAPABILITIES and row['kind'] in caps)
    require(report['error'] is None or text(report['error']))
    require(type(report['refused']) is bool and report['refused'] == (report['error'] is not None or bool(report['hard_flags'])))
    return value


def _approval_game_files(game_root):
    root = safe_path(game_root, 'Probably Stolen_Data/Managed', directory=True)
    rows, dirs_seen = [], 0
    def failed(_):
        raise ValueError('APPROVAL_GAME_UNREADABLE')
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        dirs_seen += len(dirs)
        if dirs_seen > 2048:
            raise ValueError('APPROVAL_GAME_LIMIT')
        for name in dirs:
            safe_path(root, (Path(parent) / name).relative_to(root).as_posix(), directory=True)
        for name in files:
            if name.lower().endswith('.dll'):
                relative = relative_file((Path(parent) / name).relative_to(root).as_posix())
                target = safe_path(root, relative)
                rows.append((relative, signature(target.stat())))
                if len(rows) > 2048:
                    raise ValueError('APPROVAL_GAME_LIMIT')
    rows.sort(key=lambda r: (r[0].casefold(), r[0]))
    if len({r[0].casefold() for r in rows}) != len(rows) or not {n + '.dll' for n in APPROVAL_LIBRARIES} <= {r[0] for r in rows}:
        raise ValueError('APPROVAL_GAME_LAYOUT')
    return root, rows


def _approval_bytes(root, rows, *, destination=None, cancelled=lambda: False):
    """Hash/copy the same opened inert bytes and reject source replacement."""
    result, total, deadline = [], 0, time.monotonic() + 30
    for relative, before in rows:
        if cancelled():
            raise ValueError('CANCELLED')
        source = safe_path(root, relative)
        if before[2] > MAX_INPUT:
            raise ValueError('APPROVAL_INPUT_LIMIT')
        output = None
        try:
            if destination is not None:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                output = target.open('xb')
            digest, size = hashlib.sha256(), 0
            with source.open('rb') as stream:
                if signature(os.fstat(stream.fileno())) != before:
                    raise ValueError('INPUT_CHANGED')
                while block := stream.read(256 * 1024):
                    if cancelled():
                        raise ValueError('CANCELLED')
                    size += len(block); total += len(block)
                    if size > MAX_INPUT or total > 256 * 1024 * 1024 or time.monotonic() > deadline:
                        raise ValueError('APPROVAL_INPUT_LIMIT')
                    digest.update(block)
                    if output is not None:
                        output.write(block)
                if signature(os.fstat(stream.fileno())) != before or size != before[2]:
                    raise ValueError('INPUT_CHANGED')
            if signature(safe_path(root, relative).stat()) != before:
                raise ValueError('INPUT_CHANGED')
            result.append(dict(path=relative, size=size, sha256=digest.hexdigest()))
        finally:
            if output is not None:
                output.close()
    return result


def _approval_identity(files):
    return hashlib.sha256(json.dumps({'schema': APPROVAL_IDENTITY_SCHEMA, 'game_files': files},
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class MetadataWorker:
    def __init__(self, data_root, *, helper=HELPER):
        self.data_root = Path(data_root).absolute()
        self.helper = Path(helper).absolute()

    def inspect(self, root, relative, *, cancelled=lambda:False):
        return self._inspect(root, relative, cancelled=cancelled, security=False)

    def inspect_security(self, root, relative, *, cancelled=lambda:False):
        return self._inspect(root, relative, cancelled=cancelled, security=True)

    def approval_identity(self, game_root, *, cancelled=lambda: False):
        """Current game scanner AND all metadata dependencies, not a stat cache."""
        game_root = approved_root(game_root)
        root, rows = _approval_game_files(game_root)
        files = _approval_bytes(root, rows, cancelled=cancelled)
        if _approval_game_files(game_root)[1] != rows:
            raise ValueError('INPUT_CHANGED')
        return _approval_identity(files)

    def inspect_approval(self, game_root, package_root, assemblies, *, cancelled=lambda: False):
        """Call the installed game's scanner in LPAC; never load candidate code.

        Only status=scanned, error=None, refused=False can supply approval
        capabilities. The caller binds this evidence to its full-package hash
        and scanner_identity, and remains the sole owner of approval writes.
        """
        result = dict(status='incomplete', scanner_identity=None, files=[], capabilities=None,
                      hard_flags=None, findings=None, refused=True, methods_scanned=None, error=None, process=None)
        if not SLOTS.acquire(blocking=False):
            return dict(result, error='WORKER_BUSY')
        stage = None
        try:
            game_root, package_root = approved_root(game_root), approved_root(package_root)
            for root in (game_root, package_root):
                if root == self.data_root or root in self.data_root.parents or self.data_root in root.parents:
                    raise ValueError('APPROVAL_DATA_ROOT_CONFLICT')
            if not isinstance(assemblies, (list, tuple)) or not 1 <= len(assemblies) <= 256:
                raise ValueError('APPROVAL_INPUT_LIMIT')
            names = [relative_file(value) for value in assemblies]
            if any(not name.lower().endswith('.dll') for name in names) or len({n.casefold() for n in names}) != len(names):
                raise ValueError('APPROVAL_ASSEMBLIES_INVALID')
            request = json.dumps(dict(protocol_version=3, assemblies=names)).encode('utf-8')
            if len(request) > 16384:
                raise ValueError('APPROVAL_INPUT_LIMIT')
            if cancelled():
                raise ValueError('CANCELLED')
            stages = owned_directory(self.data_root / 'metadata/staging')
            stage = stages / uuid4().hex; stage.mkdir()
            game_managed, game_rows = _approval_game_files(game_root)
            game_files = _approval_bytes(game_managed, game_rows, destination=stage / 'game', cancelled=cancelled)
            if _approval_game_files(game_root)[1] != game_rows:
                raise ValueError('INPUT_CHANGED')
            result['scanner_identity'] = _approval_identity(game_files)
            package_rows = [(name, signature(safe_path(package_root, name).stat())) for name in names]
            result['files'] = _approval_bytes(package_root, package_rows, destination=stage / 'candidate', cancelled=cancelled)
            if (self.helper / 'PSModManager.Metadata.exe').is_file():
                for artifact in self.helper.iterdir():
                    if artifact.is_symlink() or artifact.lstat().st_file_attributes & 0x400 or not artifact.is_file():
                        raise ValueError('HELPER_UNAVAILABLE')
                    shutil.copyfile(artifact, stage / artifact.name)
                command = [str(stage / 'PSModManager.Metadata.exe'), '--require-isolation', '--approval']
            else:
                dotnet = shutil.which('dotnet')
                if not dotnet:
                    raise ValueError('RUNTIME_UNAVAILABLE')
                for name in ('PSModManager.Metadata.dll', 'PSModManager.Metadata.runtimeconfig.json'):
                    artifact = self.helper / name
                    if not artifact.is_file():
                        raise ValueError('HELPER_UNAVAILABLE')
                    shutil.copyfile(artifact, stage / name)
                command = [dotnet, str(stage / 'PSModManager.Metadata.dll'), '--require-isolation', '--approval']
            process = run_sandbox(command, request, stage=stage, timeout=15, cancelled=cancelled)
            result['process'] = process
            value = decode_approval_response(process)
            if value['status'] != 'managed':
                return dict(result, error=value['error'])
            # Rehash current source bytes after the isolated call. Staged success
            # must not certify a concurrently updated game or candidate package.
            if (self.approval_identity(game_root, cancelled=cancelled) != result['scanner_identity'] or
                    _approval_bytes(package_root, package_rows, cancelled=cancelled) != result['files']):
                raise ValueError('INPUT_CHANGED')
            return dict(result, status='scanned', **{k: value['report'][k] for k in
                ('capabilities', 'hard_flags', 'findings', 'refused', 'methods_scanned', 'error')})
        except (OSError, ValueError) as exc:
            code = str(exc) if isinstance(exc, ValueError) else 'INPUT_IO_ERROR'
            return dict(result, error=code, status='cancelled' if code == 'CANCELLED' else
                        'changed' if code == 'INPUT_CHANGED' else 'incomplete')
        finally:
            try:
                if stage is not None:
                    expected = self.data_root / 'metadata/staging'
                    if stage.parent != expected or stage.resolve().parent != expected.resolve():
                        raise RuntimeError('Unsafe approval staging cleanup target')
                    for path in [stage, *stage.rglob('*')]:
                        if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
                            raise RuntimeError('Approval staging redirection')
                    shutil.rmtree(stage)
            finally:
                SLOTS.release()

    def _inspect(self, root, relative, *, cancelled, security):
        relative = relative_file(relative)
        root = approved_root(root)
        if root == self.data_root or root in self.data_root.parents or self.data_root in root.parents:
            raise ValueError('Metadata data root conflicts with source')
        result = dict(file_name=Path(relative).name, relative_path=relative, sha256=None, size=None,
                      status='unreadable', error=None, metadata=None, process=None)
        if not SLOTS.acquire(blocking=False):
            return dict(result, error='WORKER_BUSY')
        stage = None
        try:
            source = safe_path(root, relative)
            before = source.stat(); result['size'] = before.st_size
            if before.st_size > MAX_INPUT:
                return dict(result, status='limit_exceeded', error='INPUT_LIMIT')
            if cancelled(): return dict(result,status='cancelled',error='CANCELLED')
            stages = owned_directory(self.data_root / 'metadata/staging')
            stage = stages / uuid4().hex; stage.mkdir()
            copied = stage / 'input.dll'; sha = hashlib.sha256(); total = 0; start = time.monotonic()
            with source.open('rb') as input_file, copied.open('xb') as output_file:
                if signature(os.fstat(input_file.fileno())) != signature(before): raise ValueError('INPUT_CHANGED')
                while block := input_file.read(256*1024):
                    if cancelled(): return dict(result,status='cancelled',error='CANCELLED')
                    total += len(block)
                    if total > MAX_INPUT or time.monotonic()-start > 8: raise ValueError('INPUT_LIMIT')
                    sha.update(block); output_file.write(block)
                if signature(os.fstat(input_file.fileno())) != signature(before): raise ValueError('INPUT_CHANGED')
            if signature(safe_path(root,relative).stat()) != signature(before): raise ValueError('INPUT_CHANGED')
            result.update(sha256=sha.hexdigest(), status='hash_only')
            if (self.helper/'PSModManager.Metadata.exe').is_file():
                # Only the bundled trusted helper/runtime goes into the existing
                # private LPAC staging tree. The source DLL remains inert input.
                for artifact in self.helper.iterdir():
                    if artifact.is_symlink() or artifact.lstat().st_file_attributes & 0x400 or not artifact.is_file():
                        raise ValueError('HELPER_UNAVAILABLE')
                    shutil.copyfile(artifact, stage/artifact.name)
                command = [str(stage/'PSModManager.Metadata.exe'), '--require-isolation']
            else:
                dotnet = shutil.which('dotnet')
                if not dotnet: return dict(result,error='RUNTIME_UNAVAILABLE')
                for name in ('PSModManager.Metadata.dll','PSModManager.Metadata.runtimeconfig.json'):
                    artifact = self.helper / name
                    if not artifact.is_file(): return dict(result,error='HELPER_UNAVAILABLE')
                    shutil.copyfile(artifact, stage/name)
                command = [dotnet, str(stage/'PSModManager.Metadata.dll'), '--require-isolation']
            if security:
                command.append('--security')
            process = run_sandbox(command,
                                  json.dumps(dict(protocol_version=2 if security else 1,input_path=str(copied))).encode('utf-8'),
                                  stage=stage, cancelled=cancelled)
            result['process'] = process
            value = decode_security_response(process) if security else decode_response(process)
            if value['status'] == 'sandbox_unavailable':
                return dict(result,error='NETWORK_BOUNDARY_UNPROVEN')
            result.update(status=value['status'],error=value['error'],metadata=value['report'])
            # A successful parse is evidence for the staged bytes only. Do not
            # attach it to a source that changed while the worker was running.
            if signature(safe_path(root,relative).stat()) != signature(before):
                result.update(status='changed',error='INPUT_CHANGED',metadata=None)
            return result
        except (OSError,ValueError) as exc:
            result['error'] = str(exc) if isinstance(exc,ValueError) else 'INPUT_IO_ERROR'
            if result['error'] == 'CANCELLED': result['status'] = 'cancelled'
            return result
        finally:
            try:
                if stage is not None:
                    # Delete only this freshly allocated UUID directory. Never
                    # follow junctions or accept a source path as a cleanup target.
                    expected = self.data_root / 'metadata/staging'
                    if stage.parent != expected or stage.resolve().parent != expected.resolve():
                        raise RuntimeError('Unsafe metadata cleanup target')
                    for path in [stage,*stage.rglob('*')]:
                        if path.is_symlink() or path.lstat().st_file_attributes & 0x400:
                            raise RuntimeError('Metadata staging redirection')
                    shutil.rmtree(stage)
            finally:
                SLOTS.release()
