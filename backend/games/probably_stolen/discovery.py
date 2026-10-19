"""Read-only Steam and manual installation discovery; never launch the game."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .fingerprint import Budget, DiscoveryCancelled, collect, history, read_file
from .layout import EXE, inspect, inspect_selection, identity, approved_root

APP_IDS = {'4349200': 'demo', '4348910': 'retail'}
MAX_CANDIDATES = 16


def parse_vdf(text):
    """Parse the quoted KeyValues subset emitted by Steam, rejecting ambiguity."""
    if len(text) > 1024 * 1024:
        raise ValueError('vdf_size_limit')
    tokens = []
    offset = 0
    pattern = re.compile(r'\s+|//[^\r\n]*|"((?:[^"\\]|\\.)*)"|([{}])')
    while offset < len(text):
        match = pattern.match(text, offset)
        if not match:
            raise ValueError('malformed_vdf')
        if match.group(1) is not None:
            # Only Steam string escaping; preserve other literal backslashes.
            tokens.append(re.sub(r'\\([\\"])', r'\1', match.group(1)))
        elif match.group(2):
            tokens.append(match.group(2))
        offset = match.end()
        if len(tokens) > 8192:
            raise ValueError('vdf_token_limit')
    position = 0

    def object_(depth=0, nested=False):
        nonlocal position
        if depth > 12:
            raise ValueError('vdf_depth_limit')
        result = {}
        while position < len(tokens):
            key = tokens[position]
            position += 1
            if key == '}':
                if not nested:
                    raise ValueError('unexpected_vdf_close')
                return result
            if key == '{' or position >= len(tokens) or key.casefold() in result:
                raise ValueError('ambiguous_vdf_key')
            value = tokens[position]
            position += 1
            if value == '}':
                raise ValueError('missing_vdf_value')
            result[key.casefold()] = object_(depth + 1, True) if value == '{' else value
        if nested:
            raise ValueError('unclosed_vdf_object')
        return result
    return object_()


def _vdf(root, relative, budget):
    _, data, _ = read_file(root, relative, budget, limit=1024 * 1024, capture=True)
    return parse_vdf(data.decode('utf-8-sig'))


def _manifest(library, app_id, budget):
    doc = _vdf(library, f'steamapps/appmanifest_{app_id}.acf', budget)
    app = doc.get('appstate')
    if not isinstance(app, dict) or app.get('appid') != app_id:
        raise ValueError('manifest_app_id_mismatch')
    name = app.get('installdir')
    if not isinstance(name, str) or not name or name in ('.', '..') or any(c in name for c in '\\/:<>"|?*') or name.rstrip(' .') != name:
        raise ValueError('manifest_install_dir_invalid')
    approved_root(library / 'steamapps/common' / name)
    build = app.get('buildid')
    if build is not None and (not isinstance(build, str) or not build.isdecimal() or len(build) > 32):
        raise ValueError('manifest_build_id_invalid')
    return library / 'steamapps/common' / name, build


def discover(steam_roots, manual_roots, *, data_root, cancel=None, progress=None, allow_manual_manifest_probe=True, lightweight=False):
    if type(allow_manual_manifest_probe) is not bool:
        raise ValueError('invalid_manual_manifest_policy')
    if len(steam_roots) + len(manual_roots) > MAX_CANDIDATES:
        raise ValueError('candidate_limit: maximum 16 selected roots')
    if lightweight:
        return _discover_lightweight(steam_roots, manual_roots, data_root=data_root, cancel=cancel,
                                     progress=progress, allow_manual_manifest_probe=allow_manual_manifest_probe)
    budget = Budget(cancel)
    data_root = approved_root(data_root)
    pending = []
    libraries = []

    def queue(path, app_id=None, build=None, issues=None, source=None):
        pending.append((Path(path), app_id, build, list(issues or []), source))
        if len(pending) > MAX_CANDIDATES * 2:
            raise ValueError('candidate_limit: too many Steam candidates')

    for value in steam_roots:
        budget.check()
        library = approved_root(value)
        if library == data_root or library in data_root.parents or data_root in library.parents:
            queue(library, issues=['data_root_conflict'], source={'kind': 'steam', 'root': str(library)})
            continue
        libraries.append(library)
        try:
            doc = _vdf(library, 'steamapps/libraryfolders.vdf', budget).get('libraryfolders')
            if not isinstance(doc, dict):
                raise ValueError('libraryfolders_object_missing')
            for key, entry in doc.items():
                if not key.isdecimal():
                    continue
                raw = entry.get('path') if isinstance(entry, dict) else entry
                if not isinstance(raw, str) or not Path(raw).is_absolute():
                    raise ValueError('library_path_invalid')
                target = approved_root(raw)
                if target == data_root or target in data_root.parents or data_root in target.parents:
                    raise ValueError('data_root_conflict')
                if target not in libraries:
                    libraries.append(target)
                if len(libraries) > MAX_CANDIDATES:
                    raise ValueError('library_limit')
        except (OSError, ValueError, UnicodeError) as exc:
            queue(library, issues=[f'libraryfolders: {exc}'], source={'kind': 'steam', 'root': str(library)})
    for library in dict.fromkeys(libraries):
        found = False
        for app_id in APP_IDS:
            budget.check()
            try:
                path, build = _manifest(library, app_id, budget)
                queue(path, app_id, build, source={'kind': 'steam', 'root': str(library)})
                found = True
            except FileNotFoundError:
                continue
            except (OSError, ValueError, UnicodeError) as exc:
                queue(library, app_id, issues=[f'appmanifest_{app_id}: {exc}'], source={'kind': 'steam', 'root': str(library)})
                found = True
        if not found:
            queue(library, issues=['supported_appmanifest_missing'], source={'kind': 'steam', 'root': str(library)})
    for value in manual_roots:
        root = approved_root(value)
        if root == data_root or root in data_root.parents or data_root in root.parents:
            queue(root, issues=['data_root_conflict'], source={'kind': 'manual', 'root': str(root)})
            continue
        matches = []
        # Only an exact manifest install path supplies channel; never directory naming.
        if allow_manual_manifest_probe and root.parent.name.casefold() == 'common' and root.parent.parent.name.casefold() == 'steamapps':
            library = root.parent.parent.parent
            for app_id in APP_IDS:
                try:
                    path, build = _manifest(library, app_id, budget)
                    if path.resolve() == root:
                        matches.append((app_id, build))
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, UnicodeError) as exc:
                    queue(root, issues=[f'appmanifest_{app_id}: {exc}'], source={'kind': 'manual', 'root': str(root)})
        if len(matches) > 1:
            queue(root, issues=['manifest_channel_conflict'], source={'kind': 'manual', 'root': str(root)})
        else:
            app_id, build = matches[0] if matches else (None, None)
            queue(root, app_id, build, source={'kind': 'manual', 'root': str(root)})
    results = []
    seen = {}
    for path, app_id, build, issues, source in pending:
        budget.check()
        root = path.resolve()
        try:
            file_id = identity(root)
        except OSError:
            file_id = 'missing:' + str(root).casefold()
        if file_id in seen:
            previous = seen[file_id]
            if app_id and previous['app_id'] and app_id != previous['app_id']:
                previous['issues'].append('manifest_channel_conflict')
                previous['valid'] = False
                previous['fingerprint']['coverage'] = 'partial'
            previous['issues'].extend(issues)
            if issues:
                previous['valid'] = False
                previous['fingerprint']['coverage'] = 'partial'
            continue
        if len(results) >= MAX_CANDIDATES:
            raise ValueError('candidate_limit: maximum 16 unique candidates')
        paths, layout_issues = inspect(root, data_root)
        issues.extend(layout_issues)
        fingerprint, hash_issues = collect(root, paths, budget)
        issues.extend(hash_issues)
        try:
            if identity(root) != file_id:
                issues.append('root_identity_drift')
        except OSError:
            issues.append('root_identity_drift')
        if issues:
            fingerprint['coverage'] = 'partial'
        observation = None
        warnings = []
        if 'data_root_conflict' not in issues:
            try:
                observation = history(root, budget)
            except (OSError, ValueError) as exc:
                warnings.append(f'historical_log: {exc}'[:256])
        candidate = {'canonical_root': str(root), 'file_identity': file_id,
                     'channel': APP_IDS.get(app_id, 'unknown'), 'app_id': app_id,
                     'build_id': build, 'valid': not issues and fingerprint['coverage'] == 'complete',
                     'issues': list(dict.fromkeys(str(issue)[:256] for issue in issues))[:16], 'fingerprint': fingerprint,
                     'historical_observation': observation, 'warnings': warnings, 'exe_relative_path': 'Probably Stolen.exe', 'source': source}
        seen[file_id] = candidate
        results.append(candidate)
        if progress:
            progress({'stage': 'fingerprint', 'completed': len(results), 'total': min(len(pending), MAX_CANDIDATES)})
    if len(json.dumps(results, ensure_ascii=False).encode('utf-8')) > 28000:
        raise ValueError('result_size_limit')
    return results


def automatic_steam_roots(*, environ=None):
    """Locate Steam itself from Windows settings and conventional directories."""
    env = os.environ if environ is None else environ
    values = []
    if os.name == 'nt':
        import winreg
        for hive, name, value in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                                  (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath'),
                                  (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, name) as key:
                    path, _ = winreg.QueryValueEx(key, value)
                    if isinstance(path, str):
                        values.append(path)
            except OSError:
                pass
    values.extend(str(Path(env[key]) / 'Steam') for key in ('ProgramFiles(x86)', 'ProgramFiles') if env.get(key))
    roots = []
    for value in values:
        try:
            root = approved_root(value)
            if root not in roots and (root / 'steamapps').is_dir():
                roots.append(root)
        except (OSError, ValueError):
            continue
    return roots[:MAX_CANDIDATES]


def _selection_fingerprint():
    # Existing stores and consumers reference this DTO. Keep unknown facts for
    # compatibility instead of hashing the whole installation during selection.
    now = datetime.now(timezone.utc)
    from backend.contracts.models import RuntimeFingerprint
    return RuntimeFingerprint(fingerprint_id=uuid4(), game_version_raw=None, unity_version=None,
        loader_version=None, exe_hash=None, game_assembly_hash=None, metadata_hash=None,
        observed_at=now, source={'kind': 'user', 'reference': 'Installation directory selection; binary fingerprints not collected',
                                 'observed_at': now}, coverage='partial').model_dump(mode='json')


def _discover_lightweight(steam_roots, manual_roots, *, data_root, cancel=None, progress=None, allow_manual_manifest_probe=True):
    budget = Budget(cancel)
    data_root = approved_root(data_root)
    pending, libraries = [], []
    for value in steam_roots:
        budget.check()
        root = approved_root(value)
        # A game directory picked through the Steam button works just like a
        # manually picked game directory.
        if (root / EXE).is_file():
            manual_roots = [*manual_roots, root]
            continue
        if root.name.casefold() == 'common' and root.parent.name.casefold() == 'steamapps':
            root = root.parent.parent
        elif root.name.casefold() == 'steamapps':
            root = root.parent
        if root not in libraries:
            libraries.append(root)
        try:
            doc = _vdf(root, 'steamapps/libraryfolders.vdf', budget).get('libraryfolders', {})
            if not isinstance(doc, dict):
                continue
            for key, entry in doc.items():
                if not key.isdecimal():
                    continue
                raw = entry.get('path') if isinstance(entry, dict) else entry
                if isinstance(raw, str) and Path(raw).is_absolute():
                    library = approved_root(raw)
                    if library not in libraries and len(libraries) < MAX_CANDIDATES:
                        libraries.append(library)
        except (OSError, ValueError, UnicodeError):
            # Some valid Steam libraries have no libraryfolders.vdf of their own.
            pass
    for library in libraries:
        budget.check()
        found = False
        for app_id in APP_IDS:
            try:
                root, build = _manifest(library, app_id, budget)
                pending.append((root, app_id, build, {'kind': 'steam', 'root': str(library)}))
                found = True
            except (OSError, ValueError, UnicodeError):
                continue
        if not found:
            for name in ('Probably Stolen Demo', 'Probably Stolen'):
                root = library / 'steamapps/common' / name
                if (root / EXE).is_file():
                    pending.append((root, None, None, {'kind': 'steam', 'root': str(library)}))
    for value in manual_roots:
        root = approved_root(value)
        app_id, build = None, None
        if allow_manual_manifest_probe and root.parent.name.casefold() == 'common' and root.parent.parent.name.casefold() == 'steamapps':
            for candidate_id in APP_IDS:
                try:
                    path, candidate_build = _manifest(root.parent.parent.parent, candidate_id, budget)
                    if path.resolve() == root:
                        app_id, build = candidate_id, candidate_build
                        break
                except (OSError, ValueError, UnicodeError):
                    pass
        pending.append((root, app_id, build, {'kind': 'manual', 'root': str(root)}))
    results, seen = [], {}
    for path, app_id, build, source in pending:
        budget.check()
        root = approved_root(path)
        try:
            file_id = identity(root)
        except OSError:
            file_id = 'missing:' + str(root).casefold()
        if file_id in seen:
            continue
        if len(results) >= MAX_CANDIDATES:
            break
        issues = inspect_selection(root, data_root)
        candidate = {'canonical_root': str(root), 'file_identity': file_id, 'channel': APP_IDS.get(app_id, 'unknown'),
            'app_id': app_id, 'build_id': build, 'valid': not issues, 'issues': issues,
            'fingerprint': _selection_fingerprint(), 'historical_observation': None, 'warnings': [],
            'exe_relative_path': EXE, 'source': source}
        seen[file_id] = candidate
        results.append(candidate)
        if progress:
            progress({'stage': 'directory', 'completed': len(results), 'total': min(len(pending), MAX_CANDIDATES)})
    return results
