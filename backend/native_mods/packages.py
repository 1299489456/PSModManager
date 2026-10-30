"""Read native manifests and inert package bytes; never load a Mod assembly."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import xml.etree.ElementTree as ET
import zipfile

from .errors import NativeModError, issue
from .paths import relative, safe, valid_id
from .state import read_bytes, revision

CAPABILITIES = ('files', 'network', 'process', 'dynamic-code', 'native', 'reflection-external')
MAX_FILES = 4000
MAX_FILE = 64 * 1024 * 1024
MAX_PACKAGE = 256 * 1024 * 1024


def tree_files(root):
    root = safe(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise NativeModError('NATIVE_PACKAGE_DIRECTORY_REQUIRED')
    rows, total, names, directory_count = [], 0, set(), 0
    def failed_walk(_):
        raise NativeModError('NATIVE_PACKAGE_UNREADABLE')
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=failed_walk):
        directory_count += len(dirs)
        if directory_count > MAX_FILES:
            raise NativeModError('NATIVE_PACKAGE_LIMIT')
        for name in dirs:
            safe(Path(parent) / name)
            relative(str((Path(parent) / name).relative_to(root)))
        for name in files:
            path = safe(Path(parent) / name)
            rel = relative(path.relative_to(root).as_posix())
            if rel.casefold() in names:
                raise NativeModError('NATIVE_CASE_COLLISION', {'path': rel})
            names.add(rel.casefold())
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise NativeModError('NATIVE_UNSAFE_PATH')
            total += info.st_size
            if len(rows) >= MAX_FILES or info.st_size > MAX_FILE or total > MAX_PACKAGE:
                raise NativeModError('NATIVE_PACKAGE_LIMIT')
            rows.append({'path': rel, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                         'ctime_ns': info.st_ctime_ns, 'identity': f'{info.st_dev}:{info.st_ino}'})
    return sorted(rows, key=lambda r: (r['path'].casefold(), r['path']))


def tree_revision(rows):
    return revision(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def read_manifest(path):
    raw = read_bytes(path, 1024 * 1024)
    if raw is None:
        raise NativeModError('NATIVE_MANIFEST_MISSING')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise NativeModError('NATIVE_MANIFEST_UNSAFE_XML')
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        raise NativeModError('NATIVE_MANIFEST_INVALID') from None
    text = lambda name: (root.findtext(name) or '').strip()
    ident = text('ID')
    if not valid_id(ident):
        raise NativeModError('NATIVE_INVALID_ID')
    if root.find('Assemblies') is None or root.find('Capabilities') is None:
        raise NativeModError('NATIVE_MANIFEST_REQUIRED_ELEMENTS', {'id': ident})
    assemblies, seen = [], set()
    for node in root.findall('Assemblies/Assembly'):
        value = (node.text or '').strip().replace('\\', '/')
        if not value:
            continue
        value = relative(value)
        if '..' in value or not value.lower().endswith('.dll'):
            raise NativeModError('NATIVE_INVALID_ASSEMBLY_PATH', {'id': ident})
        if value.casefold() not in seen:
            assemblies.append(value); seen.add(value.casefold())
    if not assemblies:
        raise NativeModError('NATIVE_MANIFEST_NO_ASSEMBLIES', {'id': ident})
    capabilities = list(dict.fromkeys((n.text or '').strip().lower() for n in root.findall('Capabilities/Capability') if (n.text or '').strip()))
    if set(capabilities) - set(CAPABILITIES):
        raise NativeModError('NATIVE_UNKNOWN_CAPABILITY', {'id': ident})
    prerequisites = list(dict.fromkeys((n.text or '').strip() for n in root.findall('Prerequisites/Mod') if (n.text or '').strip()))
    if any(not valid_id(x) for x in prerequisites):
        raise NativeModError('NATIVE_INVALID_DEPENDENCY', {'id': ident})
    return dict(id=ident, name=text('Name') or ident, author=text('Author'), version=text('ModVersion'),
                description=text('Description'), game_versions=[(n.text or '').strip() for n in root.findall('GameVersions/Version') if (n.text or '').strip()],
                prerequisites=prerequisites, capabilities=capabilities, assemblies=assemblies,
                manifest_revision=revision(raw))


def managed_header(path):
    """Bounded PE/CLI structural check only; no claims about IMod or IL safety."""
    try:
        raw = read_bytes(path, MAX_FILE)
        if not raw or raw[:2] != b'MZ' or len(raw) < 64:
            return 'invalid'
        pe = struct.unpack_from('<I', raw, 60)[0]
        if raw[pe:pe + 4] != b'PE\0\0':
            return 'invalid'
        sections = struct.unpack_from('<H', raw, pe + 6)[0]
        optional_size = struct.unpack_from('<H', raw, pe + 20)[0]
        optional = pe + 24
        magic = struct.unpack_from('<H', raw, optional)[0]
        directories = optional + (96 if magic == 0x10B else 112 if magic == 0x20B else 0)
        if directories == optional or optional_size < directories - optional + 15 * 8:
            return 'invalid'
        cli_rva, cli_size = struct.unpack_from('<II', raw, directories + 14 * 8)
        if not cli_rva or not cli_size:
            return 'native'
        def mapped(rva):
            for index in range(sections):
                pos = optional + optional_size + 40 * index
                virtual_size, virtual, size, pointer = struct.unpack_from('<IIII', raw, pos + 8)
                if virtual <= rva < virtual + max(size, virtual_size):
                    offset = pointer + rva - virtual
                    if offset < len(raw):
                        return offset
            raise ValueError()
        cli = mapped(cli_rva)
        metadata_rva, metadata_size = struct.unpack_from('<II', raw, cli + 8)
        metadata = mapped(metadata_rva)
        if cli_size < 72 or metadata_size < 16 or metadata + metadata_size > len(raw) or raw[metadata:metadata + 4] != b'BSJB':
            return 'invalid'
        return 'managed'
    except (ValueError, struct.error, IndexError, OSError, NativeModError):
        return 'invalid'


def inspect_package(folder, source, workshop_id=None, *, check_binaries=False):
    folder = safe(folder)
    manifest = read_manifest(folder / 'manifest.xml')
    rows = tree_files(folder)
    files = {r['path'].casefold(): r for r in rows}
    listed = {a.casefold() for a in manifest['assemblies']}
    issues = []
    for assembly in manifest['assemblies']:
        if assembly.casefold() not in files:
            issues.append(issue('NATIVE_ASSEMBLY_MISSING', manifest['id'], path=assembly))
        elif check_binaries:
            status = managed_header(folder / assembly)
            if status != 'managed':
                issues.append(issue('NATIVE_ASSEMBLY_INVALID' if status == 'invalid' else 'NATIVE_ASSEMBLY_NOT_MANAGED', manifest['id'], path=assembly))
    for row in rows:
        parts = row['path'].split('/')
        if row['path'].lower().endswith('.dll') and row['path'].casefold() not in listed:
            native_scope = len(parts) == 1 or (len(parts) == 2 and parts[0].lower() == 'assemblies')
            issues.append(issue('NATIVE_UNLISTED_DLL', manifest['id'], path=row['path'], severity='error' if native_scope else 'warning'))
    latest = max((r['mtime_ns'] for r in rows), default=0)
    preview = next((r['path'] for r in rows if r['path'].lower() == 'preview.png'), None)
    return {**manifest, 'source': source, 'workshop_id': str(workshop_id) if workshop_id else None,
            'source_id': f'{source}:{workshop_id or folder.name}', 'folder_path': str(folder),
            'files': rows, 'size_bytes': sum(r['size'] for r in rows),
            'updated_at': datetime.fromtimestamp(latest / 1e9, timezone.utc).isoformat() if latest else None,
            'preview_path': str(folder / preview) if preview else None, 'installed': True,
            'package_revision': tree_revision(rows), 'issues': issues,
            'entry_count': None, 'metadata_status': 'managed_header_checked' if check_binaries else 'not_scanned'}


def native_fingerprint(package):
    """Exact native approval fingerprint; called only for trust comparison."""
    folder = Path(package['folder_path'])
    manifest = read_bytes(folder / 'manifest.xml', 1024 * 1024)
    if manifest is None:
        raise NativeModError('NATIVE_STATE_CHANGED')
    text = hashlib.sha256(manifest).hexdigest()
    for assembly in package['assemblies']:
        raw = read_bytes(folder / assembly, MAX_FILE)
        text += '\n' + assembly + '|' + ('missing' if raw is None else hashlib.sha256(raw).hexdigest())
    if tree_revision(tree_files(folder)) != package['package_revision']:
        raise NativeModError('NATIVE_STATE_CHANGED')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def zip_entries(path):
    path = safe(path)
    if path.stat().st_size > MAX_FILE:
        raise NativeModError('NATIVE_PACKAGE_LIMIT')
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES * 2:
                raise NativeModError('NATIVE_PACKAGE_LIMIT')
            result, names, total = [], set(), 0
            for info in infos:
                name = relative(info.filename.rstrip('/'))
                mode = (info.external_attr >> 16) & 0xffff
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or info.flag_bits & 1:
                    raise NativeModError('NATIVE_UNSAFE_ARCHIVE')
                if name.casefold() in names:
                    raise NativeModError('NATIVE_CASE_COLLISION', {'path': name})
                names.add(name.casefold())
                if info.is_dir():
                    continue
                total += info.file_size
                if info.file_size > MAX_FILE or total > MAX_PACKAGE or len(result) >= MAX_FILES:
                    raise NativeModError('NATIVE_PACKAGE_LIMIT')
                result.append((name, info))
            # Accept a root package or one common wrapping folder; never flatten.
            manifests = [name for name, _ in result if name.lower() == 'manifest.xml' or name.lower().endswith('/manifest.xml')]
            if len(manifests) != 1:
                raise NativeModError('NATIVE_ARCHIVE_PACKAGE_COUNT')
            prefix = manifests[0][:-len('manifest.xml')]
            if any(not name.startswith(prefix) for name, _ in result):
                raise NativeModError('NATIVE_ARCHIVE_MIXED_ROOTS')
            return [(name[len(prefix):], info) for name, info in result]
    except zipfile.BadZipFile:
        raise NativeModError('NATIVE_ARCHIVE_INVALID') from None


def extract_zip(source, target):
    entries = zip_entries(source)
    with zipfile.ZipFile(source) as archive:
        total = 0
        for name, info in entries:
            destination = safe(target / relative(name))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, destination.open('xb') as dst:
                count = 0
                while block := src.read(256 * 1024):
                    count += len(block); total += len(block)
                    if count > info.file_size or count > MAX_FILE or total > MAX_PACKAGE:
                        raise NativeModError('NATIVE_PACKAGE_LIMIT')
                    dst.write(block)
                if count != info.file_size:
                    raise NativeModError('NATIVE_ARCHIVE_INVALID')
