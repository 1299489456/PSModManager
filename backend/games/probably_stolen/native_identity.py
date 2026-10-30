"""Small read-only native runtime identity, with current and log facts separated."""
from datetime import datetime, timezone
from pathlib import Path
import re
import struct

from .fingerprint import current_versions
from .layout import safe_path
from .update_state import current_build_id


def _user_strings(data):
    """Read only the ECMA-335 #US heap; never load or execute the assembly."""
    def u16(offset):
        return struct.unpack_from('<H', data, offset)[0]
    def u32(offset):
        return struct.unpack_from('<I', data, offset)[0]
    if data[:2] != b'MZ':
        return []
    pe = u32(60)
    if data[pe:pe+4] != b'PE\0\0':
        return []
    optional = pe + 24
    table = optional + u16(pe + 20)
    count = u16(pe + 6)
    if count > 96:
        return []
    sections = [struct.unpack_from('<IIII', data, table + 40*i + 8) for i in range(count)]
    def raw(rva):
        for virtual_size, virtual, size, offset in sections:
            if virtual <= rva < virtual + max(virtual_size, size):
                return offset + rva - virtual
        raise ValueError('RVA is not in a section')
    directories = optional + (112 if u16(optional) == 0x20b else 96)
    cli = raw(u32(directories + 14*8))
    metadata = raw(u32(cli + 8))
    if data[metadata:metadata+4] != b'BSJB':
        return []
    cursor = metadata + 16 + u32(metadata + 12)
    cursor = (cursor + 3) & ~3
    streams = u16(cursor + 2)
    cursor += 4
    if streams > 16:
        return []
    for _ in range(streams):
        offset, size = u32(cursor), u32(cursor + 4)
        end = data.index(b'\0', cursor + 8, cursor + 40)
        name = data[cursor+8:end]
        cursor = (end + 4) & ~3
        if name != b'#US':
            continue
        pos, stop = metadata + offset + 1, metadata + offset + size
        if stop > len(data):
            return []
        values = []
        while pos < stop:
            first = data[pos]
            pos += 1
            if first < 0x80:
                length = first
            elif first < 0xc0:
                length = ((first & 0x3f) << 8) | data[pos]
                pos += 1
            elif first < 0xe0:
                length = ((first & 0x1f) << 24) | int.from_bytes(data[pos:pos+3], 'big')
                pos += 3
            else:
                return []
            if pos + length > stop:
                return []
            if length and length < 512:
                value = data[pos:pos+length-1].decode('utf-16-le', errors='replace')
                if re.fullmatch(r'VERSION\s+[\w.-]{1,80}', value):
                    values.append(value)
            pos += length
        return values
    return []


def observe(installation, persistent_root):
    row = installation.model_dump(mode='json') if hasattr(installation, 'model_dump') else installation
    root = Path(row['canonical_root'])
    managed = root / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll'
    result = dict(runtime='mono' if managed.is_file() and (root/'MonoBleedingEdge').is_dir()
                  else 'il2cpp' if (root/'GameAssembly.dll').is_file() else 'unknown',
                  steam_build_id=current_build_id(root, row.get('app_id')),
                  unity_version=current_versions(root)['unity_version'],
                  display_version=None, application_version=None,
                  last_run_application_version=None, last_run_observed_at=None,
                  display_version_source=None, application_version_source='unknown',
                  legacy_remnants=[name for name in ('MelonLoader', 'Plugins', 'UserLibs', 'version.dll', 'dobby.dll')
                                   if (root/name).exists()])
    try:
        path = safe_path(root, 'Probably Stolen_Data/Managed/Assembly-CSharp.dll')
        if path.stat().st_size <= 32*1024*1024:
            candidates = set(_user_strings(path.read_bytes()))
            if len(candidates) == 1:
                result['display_version'] = candidates.pop()
                result['display_version_source'] = 'Assembly-CSharp.dll/#US'
    except (OSError, ValueError, IndexError, struct.error):
        pass
    try:
        path = safe_path(Path(persistent_root), 'Player.log')
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 512*1024))
            text = stream.read(512*1024).decode('utf-8', errors='replace')
        matches = re.findall(r'\[ModLoader\] audit: game ([^,\r\n]{1,100}),', text)
        if matches:
            result['last_run_application_version'] = matches[-1]
            result['last_run_observed_at'] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except (OSError, ValueError):
        pass
    return result
