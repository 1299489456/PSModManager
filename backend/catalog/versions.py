"""Version observations from embedded metadata, never from executable code."""
import json
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5


def compare_versions(incoming, previous):
    if incoming is None or previous is None:
        return 'unknown'
    if incoming.casefold() == previous.casefold():
        return 'same'
    def numeric(value):
        match = re.fullmatch(r'[vV]?(\d+(?:\.\d+)*)', value.strip())
        return tuple(map(int, match[1].split('.'))) if match else None
    left, right = numeric(incoming), numeric(previous)
    if left is None or right is None:
        return 'unknown'
    width = max(len(left), len(right))
    left, right = left + (0,) * (width-len(left)), right + (0,) * (width-len(right))
    return 'upgrade' if left > right else 'downgrade' if left < right else 'same'


def _key(analysis):
    melon = analysis.get('melon_info') or {}
    if melon.get('name') and analysis.get('assembly_name'):
        return 'melon:' + '|'.join(str(value or '').casefold() for value in
                                  (analysis['assembly_name'], melon['name'], melon.get('author')))
    if analysis.get('assembly_name'):
        return 'assembly:' + analysis['assembly_name'].casefold()
    return None


def observe(files, display_name):
    dlls = [row.get('analysis') or {} for row in files if row['path'].lower().endswith('.dll')]
    primaries = [row for row in dlls if row.get('melon_info')] or dlls
    keys = sorted(filter(None, (_key(row) for row in primaries)))
    identity = '|'.join(keys) if keys else None
    # Metadata is the authority; a filename is only an explicitly labelled hint.
    versions = sorted({(row.get('melon_info') or {}).get('version_raw') or row.get('assembly_version')
                       for row in primaries} - {None, ''})
    hint = re.search(r'(?i)(?:[-_ ]?v?)(\d+(?:\.\d+){1,4})$', display_name)
    version = versions[0] if len(versions) == 1 else hint[1] if not versions and hint else None
    names = sorted({row['melon_info']['name'] for row in primaries if (row.get('melon_info') or {}).get('name')})
    return dict(identity=identity, identity_keys=keys, identity_source='embedded_metadata' if identity else 'unconfirmed',
                display_name=names[0] if len(names) == 1 else display_name,
                version_raw=version, version_source='embedded_metadata' if versions else 'filename_hint' if hint else 'unknown')


def candidates(store, observation, files):
    keys = set(observation.get('identity_keys', []))
    paths = {(row['target_root_kind'], row['relative_path'].casefold()) for row in files
             if row['relative_path'].lower().endswith('.dll')}
    result = []
    for row in store.connection.execute('SELECT payload FROM mod_releases ORDER BY rowid DESC LIMIT 200'):
        release = json.loads(row['payload'])
        components = [json.loads(row[0]) for row in store.connection.execute(
            'SELECT payload FROM components WHERE release_id=?', (release['release_id'],))]
        component_keys = {_key(row) for row in components}
        embedded = bool(keys and keys.intersection(component_keys))
        targets = {(row[0], row[1].casefold()) for row in store.connection.execute(
            'SELECT target_root_kind,relative_path FROM file_entries WHERE release_id=?', (release['release_id'],))}
        # Target overlap is shown for explicit confirmation, not silently merged.
        if not embedded and not paths.intersection(targets):
            continue
        package = store.get('ModPackage', release['package_id'])
        result.append(dict(release_id=release['release_id'], package_id=release['package_id'],
            manifest_hash=release['manifest_hash'], version_raw=release['version_raw'], display_name=package.display_name,
            match='embedded_metadata' if embedded else 'target_overlap',
            comparison=compare_versions(observation['version_raw'], release['version_raw'])))
    return result


def package_identity(observation, display_name, matches, requested=None):
    if requested is not None:
        if requested not in {row['package_id'] for row in matches}:
            raise ValueError('IMPORT_VERSION_SELECTION_CHANGED')
        return requested
    verified = {row['package_id'] for row in matches if row['match'] == 'embedded_metadata'}
    if len(verified) == 1:
        return verified.pop()
    identity = observation['identity']
    if identity:
        return uuid5(NAMESPACE_URL, 'psmm-mod-identity:' + identity)
    return uuid5(NAMESPACE_URL, 'psmm-local-import:' + display_name)


def library_attachment(file):
    """Root readme/changelog files stay in the library, never in the game."""
    return (file['target_root_kind'] == 'game_root' and file['lifecycle'] == 'unowned'
            and '/' not in file['relative_path'] and Path(file['relative_path']).suffix.lower() in {'.md', '.txt'})
