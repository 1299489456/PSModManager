"""Bounded, atomic, content-addressed translations in the manager's private root."""
import hashlib
import json
import os
import threading
from uuid import uuid4

from backend.app.paths import reject_reparse
from backend.catalog.archive_policy import locked_path
from .gateway import AIError

MAX_BYTES = 16 * 1024 * 1024


def cache_key(workshop_id, source, config, prompt):
    # This digest is the cache identity, not an extra integrity verification.
    identity = dict(workshop_id=workshop_id, source=source, language='zh-CN',
                    provider=config['provider'], provider_id=config.get('_provider_id', 'default'),
                    base_url=config['base_url'], model=config['model'], prompt=prompt)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class TranslationCache:
    def __init__(self, paths):
        self.paths = paths
        self.path = paths.root / 'ai-translations.json'
        self._lock = threading.RLock()

    def _read(self):
        self.paths.check_owned_paths()
        reject_reparse(self.path)
        if not self.path.exists():
            return {}
        with locked_path(self.path), self.path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('cache limit')
        document = json.loads(raw)
        if (not isinstance(document, dict) or set(document) != {'version', 'entries'}
                or document['version'] != 1 or not isinstance(document['entries'], dict)):
            raise ValueError('cache format')
        for key, row in document['entries'].items():
            if (not isinstance(key, str) or len(key) != 64 or not isinstance(row, dict)
                    or set(row) != {'pieces', 'translated', 'model'} or not isinstance(row['pieces'], list)
                    or not 1 <= len(row['pieces']) <= 24 or type(row['translated']) is not int
                    or not isinstance(row['model'], str)):
                raise ValueError('cache entry')
            for piece in row['pieces']:
                if (not isinstance(piece, dict) or set(piece) != {'part', 'index', 'count', 'sep', 'text'}
                        or piece['part'] not in {'title', 'description'} or type(piece['index']) is not int
                        or type(piece['count']) is not int or not 0 <= piece['index'] < piece['count'] <= 24
                        or not isinstance(piece['sep'], str) or not isinstance(piece['text'], str)
                        or len(json.dumps(piece, ensure_ascii=False).encode('utf-8')) > 30000):
                    raise ValueError('cache piece')
        return document['entries']

    def get(self, key):
        with self._lock:
            try:
                return self._read().get(key)
            except (OSError, ValueError):
                raise AIError('AI_TRANSLATION_CACHE_IO') from None

    def put(self, key, row):
        with self._lock:
            try:
                with locked_path(self.paths.root, directory=True):
                    entries = self._read()
                    entries.pop(key, None)
                    entries[key] = row
                    raw = json.dumps(dict(version=1, entries=entries), ensure_ascii=False).encode('utf-8')
                    # Never silently evict completed work and charge for it
                    # again later. Preserve all earlier entries if full.
                    if len(raw) > MAX_BYTES:
                        raise ValueError('cache limit')
                    temporary = self.paths.root / ('.ai-translations-' + uuid4().hex + '.tmp')
                    try:
                        with temporary.open('xb') as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        reject_reparse(self.path)
                        os.replace(temporary, self.path)
                    finally:
                        if temporary.exists():
                            temporary.unlink()
            except (OSError, ValueError):
                raise AIError('AI_TRANSLATION_CACHE_IO') from None


def translation(row):
    result = dict(title='', description='')
    for piece in row['pieces']:
        result[piece['part']] += piece['sep'] + piece['text']
    return result
