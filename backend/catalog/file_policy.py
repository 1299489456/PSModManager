"""Mod-scoped supplementary files without granting game or credential targets."""
from pathlib import PurePosixPath
import re

from . import archive_policy as archive

MOD_ROOTS = {'mods', 'plugins', 'user_libs'}
CONFIG_EXTENSIONS = {'.cfg', '.toml', '.ini', '.yaml', '.yml', '.json', '.xml', '.config'}
RESOURCE_EXTENSIONS = {'.txt', '.md', '.pdf', '.dds', '.jpeg', '.webp', '.bmp', '.gif', '.ttf', '.otf',
    '.asset', '.assetbundle', '.unity3d', '.bytes', '.bin', '.dat', '.csv', '.tsv'}


def credential_file(path):
    """Recognize credential files, not story/texture names containing 'secret'."""
    parts = path.replace('\\', '/').casefold().split('/')
    leaf = parts[-1]
    if leaf == '.env' or leaf.startswith('.env.') or leaf in {'.npmrc', '.pypirc', 'id_rsa', 'id_ed25519'}:
        return True
    if any(part in {'.aws', '.ssh', '.gnupg', 'credentials', 'secrets'} for part in parts[:-1]):
        return True
    stem = PurePosixPath(leaf).stem
    return bool(re.fullmatch(r'(?:credentials?|passwords?|secrets?|tokens?|api[_-]?key|access[_-]token|refresh[_-]token)', stem)
        or re.search(r'[._-](?:credentials?|passwords?|secrets?|tokens?|api[_-]?key)$', stem))


def scoped_supplement(file):
    path = file['relative_path']
    suffix = PurePosixPath(path).suffix.casefold()
    return (file['target_root_kind'] in MOD_ROOTS and not credential_file(path)
        and file['lifecycle'] in {'unowned', 'shared_config', 'private_config'}
        and suffix in CONFIG_EXTENSIONS | RESOURCE_EXTENSIONS)


def ordinary_file(file):
    if file['target_root_kind'] not in MOD_ROOTS or credential_file(file['relative_path']):
        return False
    try:
        archive.canonical_name(file['relative_path'])
        archive.check_content_name(file['relative_path'])
    except archive.ImportRejected:
        return False
    owned = file.get('ownership') in {'exclusive', 'shared_identical'}
    return (file['lifecycle'] == 'immutable' and owned) or (
        scoped_supplement(file) and file.get('ownership') in {'exclusive', 'shared_identical', 'unknown', 'external'})
