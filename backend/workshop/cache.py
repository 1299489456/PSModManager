"""Manager-owned, bounded public metadata and Valve image cache (no account state)."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from backend.app.paths import reject_reparse
from backend.catalog.archive_policy import locked_path
from .local import APP_ID, item_id
from .remote import safe_preview, page_url

MAX_IMAGE = 1024 * 1024
MAX_METADATA = 6 * 1024 * 1024


def stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


class PublicCache:
    def __init__(self, root):
        self.root = Path(root) / 'cache' / 'workshop'
        self.lock = threading.RLock()
        self.entries = {}
        self._load()

    def _read(self, path, limit):
        with locked_path(path), path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('cache_limit')
        return data

    def _load(self):
        try:
            value = json.loads(self._read(self.root / 'metadata.json', MAX_METADATA))
            if value.get('version') != 1 or not isinstance(value.get('entries'), list):
                return
            for entry in value['entries'][-128:]:
                key, updated, data = tuple(entry['key']), entry['updated'], entry['value']
                if (not key or key[0] not in ('browse', 'item', 'extras', 'image') or
                        not isinstance(updated, (int, float)) or not 0 <= updated <= time.time() + 60):
                    continue
                self._validate(key, data)
                self.entries[key] = (updated, data)
        except Exception:
            # A corrupt/unavailable cache is a miss; do not rewrite/delete it.
            self.entries = {}

    @staticmethod
    def _validate(key, data):
        if key[0] == 'image':
            item_id(key[1])
            if not safe_preview(data['url']) or data['mime'] not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp'):
                raise ValueError('cache_image')
        elif key[0] == 'extras':
            item_id(key[1])
            for identity in data.get('required_items', []):
                item_id(identity)
        else:
            rows = data['items'] if key[0] == 'browse' else [data]
            for row in rows:
                item_id(row['workshop_id'])
                if row.get('app_id') != APP_ID or row.get('local') is not None or row.get('native_mod_id') is not None:
                    raise ValueError('cache_scope')
                if row.get('page_url') != page_url(row['workshop_id']):
                    raise ValueError('cache_page')
                if row.get('preview_url') is not None and not safe_preview(row['preview_url']):
                    raise ValueError('cache_image')

    def get(self, key):
        with self.lock:
            return deepcopy(self.entries.get(key))

    def _write(self, name, data):
        reject_reparse(self.root)
        missing, existing = [], self.root
        while not existing.exists():
            missing.append(existing)
            existing = existing.parent
        for directory in reversed(missing):
            with locked_path(directory.parent, directory=True):
                reject_reparse(directory)
                directory.mkdir(exist_ok=True)
        with locked_path(self.root, directory=True):
            target = self.root / name
            reject_reparse(target)
            if target.exists() and target.stat().st_nlink != 1:
                raise ValueError('cache_hardlink')
            temporary = self.root / ('.cache-' + uuid4().hex + '.tmp')
            try:
                with temporary.open('xb') as stream:
                    stream.write(data)
                    stream.flush()
                reject_reparse(target)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def put(self, key, data, *, updated=None):
        self._validate(key, data)
        with self.lock:
            self.entries[key] = (updated or time.time(), deepcopy(data))
            while len(self.entries) > 128:
                del self.entries[min(self.entries, key=lambda k: self.entries[k][0])]
            payload = json.dumps(dict(version=1, entries=[dict(key=list(k), updated=t, value=v)
                for k, (t, v) in self.entries.items()]), ensure_ascii=False).encode('utf-8')
            if len(payload) <= MAX_METADATA:
                try:
                    self._write('metadata.json', payload)
                except Exception:
                    pass  # Cache persistence never blocks public browsing.

    def image(self, identity):
        entry = self.get(('image', item_id(identity)))
        if entry is None:
            return None
        try:
            data = self._read(self.root / (identity + '.image'), MAX_IMAGE)
            if image_type(data) != entry[1]['mime']:
                return None
            return entry[0], entry[1], data
        except Exception:
            return None

    def put_image(self, identity, url, data):
        identity = item_id(identity)
        mime = image_type(data)
        if not safe_preview(url) or not mime or len(data) > MAX_IMAGE:
            raise ValueError('cache_image')
        with self.lock:
            self._write(identity + '.image', data)
            self.put(('image', identity), dict(url=url, mime=mime))
            # Fixed numeric filenames only; never enumerate/delete arbitrary files.
            images = []
            for path in self.root.glob('*.image'):
                if path.stem.isascii() and path.stem.isdecimal():
                    reject_reparse(path)
                    images.append(path)
            images.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for path in images[64:]:
                if path.stem.isascii() and path.stem.isdecimal():
                    reject_reparse(path)
                    with locked_path(self.root, directory=True):
                        path.unlink()
