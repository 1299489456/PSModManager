"""Strong file facts and explicitly separate historical log observations."""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.contracts.models import RuntimeFingerprint
from .layout import EXE, ASSEMBLY, METADATA, safe_path, signature

MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_SECONDS = 30
CHUNK_BYTES = 256 * 1024


class DiscoveryCancelled(Exception):
    pass


class Budget:
    def __init__(self, cancel=None):
        self.cancel = cancel
        self.deadline = time.monotonic() + MAX_SECONDS
        self.used = 0

    def check(self):
        if self.cancel is not None and (self.cancel() if callable(self.cancel) else self.cancel.is_set()):
            raise DiscoveryCancelled('installation discovery cancelled')
        if time.monotonic() > self.deadline:
            raise ValueError('read_time_limit')


def read_file(root, relative, budget, *, limit=MAX_FILE_BYTES, capture=False, kind=None):
    budget.check()
    target = safe_path(root, relative)
    before = target.stat()
    if before.st_size > limit or budget.used + before.st_size > MAX_TOTAL_BYTES:
        raise ValueError('read_size_limit')
    digest = hashlib.sha256()
    data = bytearray()
    with target.open('rb') as stream:
        if signature(os.fstat(stream.fileno())) != signature(before):
            raise ValueError('file_drift')
        count = 0
        while True:
            budget.check()
            block = stream.read(min(CHUNK_BYTES, limit - count + 1))
            if not block:
                break
            if count == 0 and kind == 'pe':
                offset = int.from_bytes(block[60:64], 'little')
                if block[:2] != b'MZ' or offset < 64 or offset + 4 > len(block) or block[offset:offset + 4] != b'PE\0\0':
                    raise ValueError('invalid_pe_header')
            if count == 0 and kind == 'metadata' and (len(block) < 8 or block[:4] != b'\xaf\x1b\xb1\xfa'):
                raise ValueError('invalid_metadata_header')
            count += len(block)
            budget.used += len(block)
            if count > limit or budget.used > MAX_TOTAL_BYTES:
                raise ValueError('read_size_limit')
            digest.update(block)
            if capture:
                data.extend(block)
        if signature(os.fstat(stream.fileno())) != signature(before):
            raise ValueError('file_drift')
        if kind and not count:
            raise ValueError('empty_binary')
    if signature(safe_path(root, relative).stat()) != signature(before):
        raise ValueError('file_drift')
    return digest.hexdigest(), bytes(data), signature(before)


def file_version(path):
    """Read Windows version strings as data, without loading/executing the PE."""
    if os.name != 'nt':
        return None
    try:
        import win32api
        translations = win32api.GetFileVersionInfo(str(path), r'\VarFileInfo\Translation')
        for language, codepage in translations[:16]:
            for field in ('ProductVersion', 'FileVersion'):
                value = win32api.GetFileVersionInfo(str(path), f'\\StringFileInfo\\{language:04x}{codepage:04x}\\{field}')
                if isinstance(value, str) and value.strip():
                    return value[:256]
    except Exception:
        pass
    return None


def game_version_from_pe(value, unity_version=None):
    """Unity's player EXE usually carries an engine version, not a game version."""
    if not value:
        return None
    if re.match(r'^(?:20\d{2}|6000)\.\d+\.\d+(?:[abfp]\d+|\.\d+)(?:\s|$)', value):
        return None
    if unity_version and value.split()[0] == unity_version.split()[0]:
        return None
    return value


def current_versions(root):
    """Read current PE metadata only; never promote a historical log to current state."""
    def version(relative):
        try:
            target = safe_path(root, relative)
            before = signature(target.stat())
            value = file_version(target)
            return value if signature(safe_path(root, relative).stat()) == before else None
        except (OSError, ValueError):
            return None
    unity = version('UnityPlayer.dll')
    loader = version('MelonLoader/net6/MelonLoader.dll') or version('MelonLoader/MelonLoader.dll')
    return dict(game_version=game_version_from_pe(version(EXE), unity),
                unity_version=unity, loader_version=loader)


def collect(root: Path, paths: dict, budget: Budget):
    hashes = {}
    stamps = {}
    issues = []
    for relative, field in ((EXE, 'exe_hash'), (ASSEMBLY, 'game_assembly_hash'), (METADATA, 'metadata_hash')):
        hashes[field] = None
        if relative in paths:
            try:
                digest, _, stamp = read_file(root, relative, budget, kind='metadata' if relative == METADATA else 'pe')
                hashes[field] = digest
                stamps[relative] = stamp
            except (OSError, ValueError) as exc:
                issues.append(f'{relative}: {exc}')
    versions = current_versions(root)
    version = game_version_from_pe(file_version(paths[EXE]), versions['unity_version']) if EXE in stamps else None
    unity_version = versions['unity_version']
    loader_version = None
    if 'loader' in paths:
        relative = paths['loader'].relative_to(root).as_posix()
        try:
            _, _, stamps[relative] = read_file(root, relative, budget, limit=32 * 1024 * 1024, kind='pe')
            loader_version = file_version(paths['loader'])
        except (OSError, ValueError) as exc:
            issues.append(f'loader: {exc}')
    for relative, stamp in stamps.items():
        try:
            if signature(safe_path(root, relative).stat()) != stamp:
                raise ValueError('file_drift')
        except (OSError, ValueError) as exc:
            issues.append(f'{relative}: {exc}')
            hashes = {key: None for key in hashes}
            version = loader_version = unity_version = None
    now = datetime.now(timezone.utc)
    dto = RuntimeFingerprint(
        fingerprint_id=uuid4(), game_version_raw=version, unity_version=unity_version,
        loader_version=loader_version, **hashes, observed_at=now,
        source={'kind': 'embedded_metadata', 'reference': 'SHA256 current files; Unity: UnityPlayer PE; game: non-engine EXE PE version or unknown; historical log excluded', 'observed_at': now},
        coverage='complete' if all(hashes.values()) and not issues else 'partial')
    return dto.model_dump(mode='json'), issues


def history(root, budget):
    try:
        digest, data, _ = read_file(root, 'MelonLoader/Latest.log', budget, limit=1024 * 1024, capture=True)
    except FileNotFoundError:
        return None
    text = data.decode('utf-8-sig', errors='replace')
    result = {'source': 'MelonLoader/Latest.log', 'sha256': digest, 'current': False}
    for field, pattern in (
        ('game_version_raw', r'Game Version:\s*([^\r\n]+)'),
        ('unity_version', r'Unity(?: Version)?:\s*([^\r\n]+)'),
        ('loader_version', r'MelonLoader\s+v?([^\r\n]+)')):
        found = re.search(pattern, text)
        result[field] = found.group(1).strip()[:256] if found else None
    return result
