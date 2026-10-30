"""Inert full-package content identity; timestamps are never launch authority."""
import hashlib
import json
import os
from pathlib import Path
import time

from backend.native_mods.packages import tree_files, tree_revision, MAX_FILE, MAX_PACKAGE
from backend.native_mods.paths import safe
from .errors import SecurityError


def kind(path, header):
    suffix = Path(path).suffix.lower()
    if header.startswith(b'MZ') or suffix in {'.dll', '.exe', '.scr', '.com', '.sys'}:
        return 'pe'
    if header.startswith((b'PK\x03\x04', b'7z\xbc\xaf\x27\x1c', b'Rar!', b'\x1f\x8b')):
        return 'archive'
    if header.startswith((b'\x89PNG', b'\xff\xd8\xff', b'GIF87a', b'GIF89a', b'OggS', b'ID3', b'fLaC')) or header[:4] == b'RIFF':
        return 'media'
    if header.startswith(b'#!') or suffix in {'.ps1', '.bat', '.cmd', '.vbs', '.js', '.jse', '.wsf', '.hta', '.py', '.sh'}:
        return 'script'
    if suffix in {'.json', '.xml', '.csv', '.txt', '.md', '.yaml', '.yml', '.ini', '.cfg'}:
        return 'data'
    return 'other'


def content(package, check=lambda: None):
    root = safe(Path(package['folder_path']))
    before = tree_files(root)
    if not before:
        raise SecurityError('SECURITY_CONTENT_CHANGED')
    files, total, marked = [], 0, []
    declared = {name.casefold() for name in package.get('assemblies', [])}
    deadline = time.monotonic() + 120
    for row in before:
        check()
        path = safe(root / row['path'])
        digest, prefix, size, tail, marker = hashlib.sha256(), b'', 0, b'', False
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            if f'{opened.st_dev}:{opened.st_ino}' != row['identity']:
                raise SecurityError('SECURITY_CONTENT_CHANGED')
            while block := stream.read(256 * 1024):
                check()
                if time.monotonic() > deadline:
                    raise SecurityError('SECURITY_INCOMPLETE', {'reason': 'content_time_limit'})
                size += len(block); total += len(block)
                if size > MAX_FILE or total > MAX_PACKAGE:
                    raise SecurityError('SECURITY_INCOMPLETE', {'reason': 'content_size_limit'})
                digest.update(block)
                marker = marker or b'IMod\0' in tail + block
                tail = block[-4:]
                if len(prefix) < 512:
                    prefix += block[:512 - len(prefix)]
            after = os.fstat(stream.fileno())
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != row['size']:
                raise SecurityError('SECURITY_CONTENT_CHANGED')
        files.append(dict(path=row['path'], size=size, sha256=digest.hexdigest(), kind=kind(row['path'], prefix)))
        if marker and row['path'].casefold() in declared:
            marked.append(row['path'])
    if tree_revision(tree_files(root)) != tree_revision(before):
        raise SecurityError('SECURITY_CONTENT_CHANGED')
    # Content/relative names bind reports, regardless of preserved timestamps.
    identity = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return dict(content_hash=identity, files=files, stat_hint=tree_revision(before), imod_marked_assemblies=marked)


def difference(previous, files):
    old = {r['path']: r for r in (previous or {}).get('files', [])}
    new = {r['path']: r for r in files}
    return dict(baseline_report_id=(previous or {}).get('report_id'),
        added=[r for p, r in new.items() if p not in old], removed=[r for p, r in old.items() if p not in new],
        modified=[dict(path=p, before_size=old[p]['size'], after_size=r['size'], before_sha256=old[p]['sha256'],
                       after_sha256=r['sha256'], kind=r['kind']) for p, r in new.items() if p in old and old[p]['sha256'] != r['sha256']],
        unchanged_count=sum(p in old and old[p]['sha256'] == r['sha256'] for p, r in new.items()),
        byte_delta=sum(r['size'] for r in files) - sum(r['size'] for r in old.values()))


def statistics(files, embedded=0):
    groups = {}
    for row in files:
        group = groups.setdefault(row['kind'], dict(bytes=0, count=0))
        group['bytes'] += row['size']; group['count'] += 1
    return dict(total_bytes=sum(r['size'] for r in files), file_count=len(files), by_kind=groups,
                largest_files=sorted(files, key=lambda r: r['size'], reverse=True)[:10], embedded_resource_bytes=embedded)
