"""Bounded read-only staging and strict single-request metadata protocol."""
from __future__ import annotations

import hashlib
import json
import os
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


def decode_response(process):
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
    try:
        value = json.loads(process['stdout'])
    except (ValueError, TypeError):
        raise ValueError('WORKER_PROTOCOL_INVALID') from None
    if (not isinstance(value,dict) or set(value) != {'protocol_version','status','report','error'}
            or type(value['protocol_version']) is not int or value['protocol_version'] != 1
            or value['status'] not in ('managed','native','corrupt','limit_exceeded','invalid_request','io_error','sandbox_unavailable')):
        raise ValueError('WORKER_PROTOCOL_INVALID')
    success = value['status'] in ('managed','native')
    if process['exit_code'] != (0 if success else 2): raise ValueError('WORKER_EXIT_MISMATCH')
    if success and (not isinstance(value['report'],dict) or value['error'] is not None):
        raise ValueError('WORKER_PROTOCOL_INVALID')
    if not success and (value['report'] is not None or not isinstance(value['error'],str)):
        raise ValueError('WORKER_PROTOCOL_INVALID')
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


class MetadataWorker:
    def __init__(self, data_root, *, helper=HELPER):
        self.data_root = Path(data_root).absolute()
        self.helper = Path(helper).absolute()

    def inspect(self, root, relative, *, cancelled=lambda:False):
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
            process = run_sandbox(command,
                                  json.dumps(dict(protocol_version=1,input_path=str(copied))).encode('utf-8'),
                                  stage=stage, cancelled=cancelled)
            result['process'] = process
            value = decode_response(process)
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
