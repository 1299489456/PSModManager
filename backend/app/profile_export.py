"""Native-selected, create-only portable manifest export; never overwrite game data."""
import json
import os
from pathlib import Path

from backend.app.paths import REFERENCE_ROOT, KNOWN_GAME_ROOT, overlaps
from backend.catalog.archive_policy import locked_path, canonical_name
from backend.storage import Store


def save_manifest(paths, selected, manifest):
    from backend.profiles.service import ProfileError
    if not selected:
        return {'saved': False, 'cancelled': True}
    target = Path(selected)
    try:
        if (not target.is_absolute() or str(target).startswith(('\\\\', '//'))
                or target.suffix.lower() != '.json'
                or any(part.endswith((' ', '.')) or ':' in part for part in target.parts[1:])):
            raise ValueError('Invalid destination')
        target = Path(os.path.abspath(target))
        canonical_name('/'.join(target.parts[1:]))
        forbidden = [paths.root, REFERENCE_ROOT, KNOWN_GAME_ROOT]
        store = Store(paths.database)
        try:
            forbidden.extend(Path(row[0]) for row in store.connection.execute('SELECT canonical_root FROM installations'))
        finally:
            store.close()
        if any(overlaps(target, root) for root in forbidden):
            raise ValueError('Protected destination')
        payload = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        if len(payload) > 262144:
            raise ValueError('Manifest too large')
        with locked_path(target.parent, directory=True):
            # Exclusive creation rejects existing files, links and racing replacements.
            with target.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        return {'saved': True, 'cancelled': False}
    except (OSError, ValueError):
        raise ProfileError('PROFILE_EXPORT_REFUSED') from None
