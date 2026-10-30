"""Small desktop preferences in the existing, manager-owned config.json."""
import json
import os
import threading
from uuid import uuid4

from .identity import APP_NAME, KEYRING_SERVICE, UPDATE_SOURCE
from backend.catalog.archive_policy import locked_path

THEME_IDS = ('obsidian-cyan', 'graphite-ash', 'win11-mica')
IDENTITY = {'product': APP_NAME, 'version': 1, 'keyring_service': KEYRING_SERVICE, 'update_source': UPDATE_SOURCE}


class Preferences:
    def __init__(self, paths):
        self.paths = paths
        self._lock = threading.RLock()

    def read_config(self):
        self.paths.check_owned_paths()
        if not self.paths.config.exists():
            return dict(IDENTITY)
        with locked_path(self.paths.config), self.paths.config.open('r', encoding='utf-8') as stream:
            raw = stream.read(65537)
        if len(raw.encode('utf-8')) > 65536:
            raise ValueError('配置文件超过大小限制；保留原文件')
        config = json.loads(raw)
        if (not isinstance(config, dict) or any(key not in config or config[key] != value for key, value in IDENTITY.items())
                or type(config.get('version')) is not int
                or config.get('theme_id', THEME_IDS[0]) not in THEME_IDS):
            raise ValueError('配置身份或版本不兼容；保留原文件，不自动迁移')
        return config

    def theme(self):
        return {'theme_id': self.read_config().get('theme_id', THEME_IDS[0])}

    def set_theme(self, theme_id):
        if theme_id not in THEME_IDS:
            raise ValueError('Invalid theme')
        self.update('theme_id', theme_id)
        return {'theme_id': theme_id}

    def update(self, field, value):
        if field not in {'theme_id', 'ai', 'ai_providers', 'workshop', 'storage_setup_complete', 'storage_previous_root'}:
            raise ValueError('Unsupported preference')
        # The parent is pinned against rename/junction substitution. The new
        # file is created exclusively and only replaces our fixed config name.
        with self._lock, locked_path(self.paths.root, directory=True):
            config = self.read_config()
            config[field] = value
            encoded = json.dumps(config, ensure_ascii=False, indent=2).encode('utf-8')
            if len(encoded) > 65536:
                raise ValueError('配置文件超过大小限制；保留原文件')
            temporary = self.paths.root / ('.config-' + uuid4().hex + '.tmp')
            try:
                with temporary.open('xb') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.paths.check_owned_paths()
                os.replace(temporary, self.paths.config)
            finally:
                if temporary.exists():
                    temporary.unlink()
