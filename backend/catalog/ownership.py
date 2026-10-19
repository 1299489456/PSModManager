"""File-level ownership; directory proximity is never evidence of ownership."""
from pathlib import PurePosixPath

from backend.contracts.models import relative_path

ROOT_KINDS = {'mods': 'mods', 'plugins': 'plugins', 'userlibs': 'user_libs'}


def classify(path):
    relative_path(path)
    parts = PurePosixPath(path).parts
    suffix = PurePosixPath(path).suffix.lower()
    if parts[0].lower() == 'userdata':
        return 'shared_config' if suffix in {'.cfg', '.toml', '.ini'} else 'mutable_data'
    if suffix in {'.log', '.tmp', '.cache'}:
        return 'generated'
    if suffix in {'.cfg', '.toml', '.ini'}:
        return 'shared_config'
    if parts[0].lower() in ROOT_KINDS and suffix in {'.dll', '.png', '.jpg', '.ogg', '.wav', '.bundle', '.json'}:
        return 'immutable'
    return 'unowned'


def target(path):
    relative_path(path)
    root, separator, child = path.partition('/')
    if not separator or root.lower() not in ROOT_KINDS:
        raise ValueError('Unsupported package target')
    return ROOT_KINDS[root.lower()], child


def owners(store, installation_id, path, digest):
    kind, child = target(path)
    rows = store.connection.execute(
        '''SELECT DISTINCT f.release_id,f.source_hash,f.relative_path FROM file_entries f
           JOIN package_adoptions a ON a.release_id=f.release_id
           JOIN baseline_snapshots b ON b.snapshot_id=a.snapshot_id
           WHERE f.target_root_kind=? AND b.installation_id=?''', (kind, str(installation_id)))
    records = [dict(row) for row in rows if row['relative_path'].casefold() == child.casefold()]
    return {'matching': sorted(r['release_id'] for r in records if r['source_hash'] == digest),
            'conflicting': sorted(r['release_id'] for r in records if r['source_hash'] != digest)}
