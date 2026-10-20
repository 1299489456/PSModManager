"""Small current-file observations for update-aware diagnostics, without game writes."""
from datetime import datetime, timezone
from pathlib import Path

from .layout import ASSEMBLY, DATA, EXE, METADATA, safe_path


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def game_files_modified_at(root):
    observed = []
    for relative in (EXE, ASSEMBLY, METADATA, 'UnityPlayer.dll', DATA + '/globalgamemanagers'):
        try:
            observed.append(safe_path(root, relative).stat().st_mtime)
        except (OSError, ValueError):
            continue
    return max(observed) if observed else None


def current_build_id(root, app_id):
    """Only probe the selected game's own Steam manifest, never other libraries."""
    from .discovery import APP_IDS, _manifest
    from .fingerprint import Budget
    root = Path(root)
    if app_id not in APP_IDS or root.parent.name.casefold() != 'common' or root.parent.parent.name.casefold() != 'steamapps':
        return None
    try:
        candidate, build = _manifest(root.parent.parent.parent, app_id, Budget())
        return build if candidate.resolve() == root.resolve() else None
    except (OSError, ValueError, UnicodeError):
        return None
