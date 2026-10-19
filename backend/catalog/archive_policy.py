"""Bounded local import policy. Archive names never become extraction paths."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import struct
import time
import unicodedata
import zipfile
import zlib

from backend.app.paths import reject_reparse
from backend.contracts.models import relative_path

# A single resource Mod may contain thousands of assets. Content bytes, archive
# expansion, names and depth remain bounded independently of entry count.
MAX_FILES = 4000
MAX_ENTRIES = 8000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_RATIO = 100
MAX_SECONDS = 120
MAX_DEPTH = 16
CHUNK = 256 * 1024
SCRIPTS = {'.exe', '.com', '.msi', '.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.vbe', '.js', '.jse', '.wsf', '.wsh', '.sh', '.py', '.lnk', '.url', '.scr', '.reg'}
ARCHIVES = {'.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz', '.cab'}


class ImportRejected(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def canonical_name(value):
    if not isinstance(value, str) or '\x00' in value:
        raise ImportRejected('IMPORT_PATH_INVALID')
    value = value.replace('\\', '/')
    try:
        relative_path(value)
    except ValueError:
        raise ImportRejected('IMPORT_PATH_INVALID') from None
    if len(value) > 240 or len(value.split('/')) > MAX_DEPTH or any(
        re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?', p) for p in value.split('/')):
        raise ImportRejected('IMPORT_PATH_INVALID')
    return unicodedata.normalize('NFC', value)


def check_content_name(value):
    suffix = Path(value).suffix.lower()
    if suffix in SCRIPTS:
        raise ImportRejected('IMPORT_SCRIPT_REFUSED')
    if suffix in ARCHIVES:
        raise ImportRejected('IMPORT_NESTED_ARCHIVE')


class Budget:
    def __init__(self, check=lambda: None):
        self.check_cancelled = check
        self.deadline = time.monotonic() + MAX_SECONDS
        self.total = 0

    def check(self):
        self.check_cancelled()
        if time.monotonic() > self.deadline:
            raise ImportRejected('IMPORT_TIME_LIMIT')

    def consume(self, amount):
        self.check()
        self.total += amount
        if self.total > MAX_TOTAL_BYTES:
            raise ImportRejected('IMPORT_TOTAL_LIMIT')


@contextmanager
def locked_path(path, *, directory=False):
    """Pin existing parents against rename and source files against modification.

    Windows share-delete denial closes the directory replacement window while
    extracting/copying. No process or machine privileges are changed.
    """
    if os.name != 'nt':
        raise ImportRejected('IMPORT_PLATFORM_UNSUPPORTED')
    import win32file
    import win32con
    import pywintypes
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise ImportRejected('IMPORT_PATH_INVALID')
    handles = []
    try:
        for parent in reversed((path.parent, *path.parent.parents)):
            handle = win32file.CreateFile(str(parent), win32con.GENERIC_READ,
                win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None,
                win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
            handles.append(handle)
            if win32file.GetFileInformationByHandle(handle)[0] & 0x400:
                raise ImportRejected('IMPORT_LINK_REFUSED')
        handle = win32file.CreateFile(str(path), win32con.GENERIC_READ,
            win32con.FILE_SHARE_READ | (win32con.FILE_SHARE_WRITE if directory else 0), None,
            win32con.OPEN_EXISTING, win32con.FILE_FLAG_BACKUP_SEMANTICS | 0x00200000, None)
        handles.append(handle)
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & 0x400 or bool(info[0] & 0x10) != directory or (not directory and info[7] != 1):
            raise ImportRejected('IMPORT_LINK_REFUSED')
        reject_reparse(path)
        yield path
    except pywintypes.error:
        raise ImportRejected('IMPORT_PATH_UNAVAILABLE') from None
    finally:
        for handle in reversed(handles):
            handle.Close()


def zip_entries(stream, *, max_archive_bytes=None, max_total_bytes=None,
                max_file_bytes=None, max_files=None):
    """Bound central-directory work before ZipFile allocates its entry list."""
    max_archive_bytes = MAX_ARCHIVE_BYTES if max_archive_bytes is None else max_archive_bytes
    max_total_bytes = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    max_file_bytes = MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
    max_files = MAX_FILES if max_files is None else max_files
    stream.seek(0, 2)
    size = stream.tell()
    if size > max_archive_bytes:
        raise ImportRejected('IMPORT_ARCHIVE_LIMIT')
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    index = tail.rfind(b'PK\x05\x06')
    if index < 0 or len(tail) - index < 22:
        raise ImportRejected('IMPORT_ARCHIVE_INVALID')
    _, disk, central_disk, disk_count, count, central_size, offset, comment = struct.unpack('<4s4H2LH', tail[index:index+22])
    if disk or central_disk or disk_count != count or count > MAX_ENTRIES or central_size > 1024 * 1024:
        raise ImportRejected('IMPORT_ENTRY_LIMIT')
    if count == 65535 or offset == 0xffffffff or offset + central_size != size - len(tail) + index or index + 22 + comment != len(tail):
        raise ImportRejected('IMPORT_ARCHIVE_INVALID')
    stream.seek(0)
    archive = zipfile.ZipFile(stream)
    try:
        entries = archive.infolist()
        if len(entries) != count:
            raise ImportRejected('IMPORT_ENTRY_LIMIT')
        names, files, total = {}, [], 0
        for entry in entries:
            name = canonical_name(entry.orig_filename.rstrip('/') if entry.is_dir() else entry.orig_filename)
            key = name.casefold()
            if key in names:
                raise ImportRejected('IMPORT_NAME_CONFLICT')
            names[key] = entry.is_dir()
            mode = entry.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (kind not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG)
                    or entry.external_attr & 0x400 or entry.flag_bits & 1):
                raise ImportRejected('IMPORT_LINK_REFUSED')
            extra = entry.extra
            while extra:
                if len(extra) < 4:
                    raise ImportRejected('IMPORT_ARCHIVE_INVALID')
                tag, length = struct.unpack('<HH', extra[:4])
                if len(extra) < 4 + length or tag in (0x0001, 0x000d):
                    raise ImportRejected('IMPORT_ARCHIVE_INVALID')
                extra = extra[4 + length:]
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise ImportRejected('IMPORT_FORMAT_UNSUPPORTED')
            if entry.is_dir():
                if entry.file_size:
                    raise ImportRejected('IMPORT_ARCHIVE_INVALID')
                continue
            check_content_name(name)
            if entry.file_size > max_file_bytes:
                raise ImportRejected('IMPORT_FILE_LIMIT')
            if entry.file_size > max(entry.compress_size, 1) * MAX_RATIO:
                raise ImportRejected('IMPORT_RATIO_LIMIT')
            total += entry.file_size
            if len(files) >= max_files or total > max_total_bytes:
                raise ImportRejected('IMPORT_TOTAL_LIMIT')
            files.append((entry, name))
        for name in names:
            parts = name.split('/')
            if any('/'.join(parts[:i]) in names and not names['/'.join(parts[:i])] for i in range(1, len(parts))):
                raise ImportRejected('IMPORT_NAME_CONFLICT')
        return archive, files
    except BaseException:
        archive.close()
        raise


def validate_member(archive, entry, budget):
    """Verify the complete compressed stream, including dishonest size/CRC pairs.

    ZipExtFile truncates to the declared file_size. Independently count bounded
    decompressor output so an attacker cannot hide excess output behind it.
    """
    stream = archive.fp
    stream.seek(entry.header_offset)
    header = stream.read(30)
    if len(header) != 30 or header[:4] != b'PK\x03\x04':
        raise ImportRejected('IMPORT_ARCHIVE_INVALID')
    name_length, extra_length = struct.unpack('<HH', header[26:30])
    stream.seek(entry.header_offset + 30 + name_length + extra_length)
    remaining, count, checksum = entry.compress_size, 0, 0
    decoder = zlib.decompressobj(-15) if entry.compress_type == zipfile.ZIP_DEFLATED else None
    while remaining:
        budget.check()
        block = stream.read(min(64 * 1024, remaining))
        if not block:
            raise ImportRejected('IMPORT_ARCHIVE_INVALID')
        remaining -= len(block)
        pending = block
        while pending:
            budget.check()
            try:
                output = decoder.decompress(pending, min(CHUNK, entry.file_size - count + 1)) if decoder else pending
            except zlib.error:
                raise ImportRejected('IMPORT_ARCHIVE_INVALID') from None
            count += len(output)
            if count > entry.file_size:
                raise ImportRejected('IMPORT_SIZE_MISMATCH')
            checksum = zlib.crc32(output, checksum)
            if decoder and decoder.unused_data:
                raise ImportRejected('IMPORT_ARCHIVE_INVALID')
            pending = decoder.unconsumed_tail if decoder else b''
    if count != entry.file_size or checksum != entry.CRC or (decoder and not decoder.eof):
        raise ImportRejected('IMPORT_SIZE_MISMATCH')
