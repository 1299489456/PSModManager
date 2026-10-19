"""Verified content-addressed bytes and immutable published release manifests."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from uuid import uuid4

from backend.app.paths import reject_reparse
from backend.contracts import Artifact
from backend.games.probably_stolen.layout import safe_path, signature


def content_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


class PackageRepository:
    def __init__(self, store, root):
        self.store = store
        self.root = Path(root)
        self._checked_directories = set()
        self._source_directories = set()
        self._created_directories = set()

    def ordinary_path(self, digest):
        """One batch's immutable library path; check shared parents only once."""
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid artifact hash')
        parent = self.root/digest[:2]
        if parent not in self._checked_directories:
            reject_reparse(parent)
            self._checked_directories.add(parent)
        path = parent/digest
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if getattr(info, 'st_file_attributes', 0) & 0x400 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe library artifact')
        return path

    def capture_ordinary(self, source, check):
        """Copy and identify an existing Mod once, without per-file fsync."""
        if source.parent not in self._source_directories:
            reject_reparse(source.parent)
            self._source_directories.add(source.parent)
        before = source.lstat()
        if (getattr(before, 'st_file_attributes', 0) & 0x400 or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1 or before.st_size > 512*1024*1024):
            raise ValueError('Unsafe source file')
        if self.root not in self._created_directories:
            reject_reparse(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
            self._created_directories.add(self.root)
        temporary = self.root/('.copy-'+str(uuid4()))
        checksum = hashlib.sha256()
        try:
            with source.open('rb') as reader, temporary.open('xb') as writer:
                for chunk in iter(lambda: reader.read(256*1024), b''):
                    check(); checksum.update(chunk); writer.write(chunk)
                if signature(os.fstat(reader.fileno())) != signature(before):
                    raise ValueError('Source changed')
            digest = checksum.hexdigest()
            destination = self.ordinary_path(digest)
            if destination.parent not in self._created_directories:
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._created_directories.add(destination.parent)
            if destination.exists():
                # Existing content-addressed files may have been edited outside
                # the manager; never replace them with an unverified duplicate.
                with destination.open('rb') as reader:
                    if hashlib.file_digest(reader, 'sha256').hexdigest() != digest:
                        raise ValueError('Artifact hash mismatch')
            else:
                temporary.rename(destination)
            return Artifact(sha256=digest, size=before.st_size, library_relative_path=destination.relative_to(self.root).as_posix())
        finally:
            temporary.unlink(missing_ok=True)

    def path(self, digest):
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('Invalid artifact hash')
        path = self.root / digest[:2] / digest
        reject_reparse(path)
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise ValueError('Unsafe library artifact')
        return path

    def verify(self, digest, size=None, *, check=lambda: None):
        check()
        path = self.path(digest)
        before = path.stat()
        if before.st_size > 512 * 1024 * 1024 or (size is not None and before.st_size != size):
            raise ValueError('Artifact size mismatch or limit')
        deadline = time.monotonic() + 30
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            if signature(os.fstat(stream.fileno())) != signature(before):
                raise ValueError('Artifact changed')
            for chunk in iter(lambda: stream.read(256 * 1024), b''):
                check()
                if time.monotonic() > deadline:
                    raise ValueError('Artifact verification timeout')
                checksum.update(chunk)
            if signature(os.fstat(stream.fileno())) != signature(before):
                raise ValueError('Artifact changed')
        if checksum.hexdigest() != digest or (size is not None and before.st_size != size):
            raise ValueError('Artifact hash mismatch')
        if signature(self.path(digest).stat()) != signature(before):
            raise ValueError('Artifact changed')
        return path

    def copy(self, source_root, file, check):
        """Copy, fsync, verify, then publish. Never hardlink to an installation."""
        digest, size = file.sha256, file.size
        destination = self.path(digest)
        if destination.exists():
            self.verify(digest, size)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.parent / ('.copy-' + str(uuid4()))
            try:
                source = safe_path(source_root, file.relative_path)
                before = source.stat()
                checksum = hashlib.sha256()
                count = 0
                with source.open('rb') as reader, temporary.open('xb') as writer:
                    if signature(os.fstat(reader.fileno())) != signature(before):
                        raise ValueError('Source changed')
                    while True:
                        check()
                        chunk = reader.read(256 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > size:
                            raise ValueError('Source grew')
                        checksum.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                    if signature(os.fstat(reader.fileno())) != signature(before):
                        raise ValueError('Source changed')
                if count != size or checksum.hexdigest() != digest or signature(safe_path(source_root, file.relative_path).stat()) != signature(before):
                    raise ValueError('Source hash changed')
                check()
                self.path(digest)
                # Windows rename refuses an existing destination; competing
                # identical publication is accepted only after verification.
                try:
                    temporary.rename(destination)
                except FileExistsError:
                    self.verify(digest, size)
                self.verify(digest, size)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return Artifact(sha256=digest, size=size, library_relative_path=destination.relative_to(self.root).as_posix())

    def release(self, release_id, manifest_hash=None, *, check=lambda: None, verify_bytes=True):
        release = self.store.get('ModRelease', release_id)
        if manifest_hash is not None and release.manifest_hash != manifest_hash:
            raise ValueError('Release lock mismatch')
        rows = self.store.connection.execute('SELECT payload FROM file_entries WHERE release_id=?', (str(release_id),))
        files = [json.loads(row['payload']) for row in rows]
        if not files:
            raise ValueError('Release has no published files')
        manifest = []
        total = 0
        for file in files:
            artifact = self.store.get('Artifact', file['source_hash'])
            total += artifact.size
            if total > 1024 * 1024 * 1024 or len(files) > 5000:
                raise ValueError('Release verification limit')
            if verify_bytes:
                self.verify(artifact.sha256, artifact.size, check=check)
            manifest.append({key: file[key] for key in ('source_hash', 'target_root_kind', 'relative_path', 'lifecycle')})
        manifest.sort(key=lambda f: (f['target_root_kind'], f['relative_path']))
        if content_hash({'version_raw': release.version_raw, 'files': manifest}) != release.manifest_hash:
            raise ValueError('Release manifest mismatch')
        return {'release': release.model_dump(mode='json'), 'files': files}

    def delete_unreferenced_artifact(self, digest, *, confirmed):
        if confirmed is not True:
            raise ValueError('Explicit confirmation required')
        self.store.connection.execute('BEGIN IMMEDIATE')
        try:
            for table, column in (('file_entries', 'source_hash'), ('components', 'artifact_hash')):
                if self.store.connection.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1', (digest,)).fetchone():
                    raise ValueError('Artifact is still referenced')
            path = self.verify(digest)
            path.unlink()
            self.store.connection.execute('DELETE FROM artifacts WHERE sha256=?', (digest,))
            self.store.connection.commit()
        except BaseException:
            self.store.connection.rollback()
            raise
