"""Native locations. Caller supplies an already authorized installation."""
import os
import re
from pathlib import Path, PureWindowsPath

from backend.app.paths import reject_reparse
from .errors import NativeModError

ID_RE = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}\Z')
DEVICE_RE = re.compile(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z')


def installation_dict(installation):
    return installation.model_dump(mode='json') if hasattr(installation, 'model_dump') else dict(installation)


def valid_id(value):
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise NativeModError('NATIVE_UNSAFE_PATH')
    raw = value.replace('\\', '/')
    win = PureWindowsPath(raw)
    if win.drive or win.root or any(not p or p in ('.', '..') or p.endswith((' ', '.'))
            or DEVICE_RE.fullmatch(p) or any(ord(c) < 32 or c in ':<>"|?*' for c in p)
            for p in raw.split('/')):
        raise NativeModError('NATIVE_UNSAFE_PATH')
    return raw


def safe(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '//')):
        raise NativeModError('NATIVE_UNSAFE_PATH')
    try:
        reject_reparse(path)
        if path.is_file() and path.stat().st_nlink != 1:
            raise NativeModError('NATIVE_UNSAFE_PATH')
    except ValueError:
        raise NativeModError('NATIVE_UNSAFE_PATH') from None
    return path


def locations(installation):
    row = installation_dict(installation)
    raw = row.get('canonical_root') or row.get('root_path')
    if not raw:
        raise NativeModError('NATIVE_INSTALLATION_REQUIRED')
    root = safe(Path(raw))
    persistent = row.get('persistent_root') or row.get('user_data_root')
    if not persistent:
        home = os.environ.get('USERPROFILE')
        if not home:
            raise NativeModError('NATIVE_USER_DATA_UNKNOWN')
        persistent = Path(home) / 'AppData/LocalLow/Questing Goose Studio/Probably Stolen'
    workshop = row.get('workshop_root')
    if not workshop:
        workshop = root.parent.parent / 'workshop/content/4348910' if root.parent.name.lower() == 'common' else None
    data = safe(Path(persistent))
    return dict(game_root=root, mods_root=safe(root / 'Mods'),
                workshop_root=safe(Path(workshop)) if workshop else None,
                persistent_root=data, state_file=safe(data / 'SaveFile.es3'),
                config_root=safe(data / 'ModConfigs'), log_path=safe(data / 'Player.log'))
