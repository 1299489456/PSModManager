"""Read Steam-owned records without inferring the current account's subscriptions."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

from backend.app.paths import reject_reparse
from backend.games.probably_stolen.discovery import parse_vdf
from .errors import WorkshopError

APP_ID = '4348910'
MAX_ITEMS = 1024


def now():
    return datetime.now(timezone.utc).isoformat()


def item_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value) or int(value) > 2**64 - 1:
        raise WorkshopError('WORKSHOP_INVALID_ID', '工坊 ID 必须是有效的数字条目 ID。')
    return value


def number(value):
    try:
        if isinstance(value, bool):
            return None
        result = int(value)
        return result if 0 <= result <= 2**64 - 1 else None
    except (TypeError, ValueError, OverflowError):
        return None


def text(value, limit=512):
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if c >= ' ' or c in '\n\t')[:limit].strip()


def bounded_read(path, limit):
    reject_reparse(path)
    if path.stat().st_size > limit:
        raise ValueError('file_size_limit')
    with path.open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file_size_limit')
    return data


def manifest_summary(directory):
    """Only metadata and declared-path presence; full native inspection owns loadability."""
    raw = bounded_read(directory / 'manifest.xml', 2 * 1024 * 1024)
    if b'<!doctype' in raw.lower() or b'<!entity' in raw.lower() or b'\x00' in raw:
        raise ValueError('unsafe_xml')
    root = ET.fromstring(raw)
    native_id = text(root.findtext('ID'), 128)
    # Match the formal runtime/native backend's ID grammar, without conflating
    # this identifier with the decimal Workshop item ID.
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}', native_id):
        raise ValueError('invalid_native_id')
    declared = root.findall('./Assemblies/Assembly')
    if not 1 <= len(declared) <= 256:
        raise ValueError('assembly_limit')
    missing = []
    for element in declared:
        name = (element.text or '').strip().replace('\\', '/')
        relative = PurePosixPath(name)
        if not name or relative.is_absolute() or any(p in ('', '.', '..') or ':' in p for p in name.split('/')):
            raise ValueError('invalid_assembly_path')
        path = directory.joinpath(*relative.parts)
        reject_reparse(path)
        if not path.is_file():
            missing.append(name)
    description = root.findtext('Description') or ''
    return dict(native_mod_id=native_id, title=text(root.findtext('Name'), 256) or native_id,
                author=text(root.findtext('Author'), 128) or None,
                mod_version=text(root.findtext('ModVersion'), 80) or None,
                description=text(description, 240), description_truncated=len(description) > 240,
                declared_assembly_count=len(declared), missing_assemblies=missing)


def workshop_root(installation):
    if not isinstance(installation, dict) or not isinstance(installation.get('canonical_root'), str):
        raise WorkshopError('WORKSHOP_INSTALLATION_REQUIRED', '请先选择游戏安装目录。')
    if installation.get('app_id') not in (None, APP_ID):
        raise WorkshopError('WORKSHOP_WRONG_GAME', '创意工坊仅支持正式版 AppID 4348910。')
    root = Path(installation['canonical_root'])
    if not root.is_absolute():
        raise WorkshopError('WORKSHOP_INVALID_INSTALLATION', '游戏安装路径必须是宿主确认的绝对路径。')
    reject_reparse(root)
    if root.parent.name.casefold() != 'common' or root.parent.parent.name.casefold() != 'steamapps':
        return None
    target = root.parent.parent / 'workshop'
    reject_reparse(target)
    return target


def read_status(installation, *, workshop_id=None, skip_content_ids=()):
    if workshop_id is not None:
        item_id(workshop_id)
    result = dict(app_id=APP_ID, observed_at=now(), local_available=False, items=[], downloads=[], issues=[])
    try:
        root = workshop_root(installation)
    except (OSError, ValueError):
        result['issues'].append('WORKSHOP_UNSAFE_OR_UNREADABLE_PATH')
        return result
    if root is None:
        result['issues'].append('STEAM_LIBRARY_NOT_DETECTED')
        return result
    acf = root / f'appworkshop_{APP_ID}.acf'
    data = {}
    try:
        # Reuse the repository's bounded, duplicate-rejecting KeyValues parser.
        data = parse_vdf(bounded_read(acf, 1024 * 1024).decode('utf-8-sig')).get('appworkshop', {})
        if not isinstance(data, dict) or str(data.get('appid')) != APP_ID:
            raise ValueError('app_id_mismatch')
        result['local_available'] = True
        result['acf_observed_at'] = datetime.fromtimestamp(acf.stat().st_mtime, timezone.utc).isoformat()
    except FileNotFoundError:
        result['issues'].append('WORKSHOP_ACF_MISSING')
    except (OSError, ValueError, UnicodeError):
        data = {}
        result['issues'].append('WORKSHOP_ACF_INVALID')
    installed = data.get('workshopitemsinstalled', {})
    details = data.get('workshopitemdetails', {})
    if not isinstance(installed, dict) or not isinstance(details, dict):
        installed, details = {}, {}
        result['issues'].append('WORKSHOP_ACF_INVALID')
    content = root / 'content' / APP_ID
    directories = {}
    try:
        reject_reparse(content)
        if content.is_dir():
            result['local_available'] = True
            for entry in content.iterdir():
                if len(directories) >= MAX_ITEMS:
                    result['issues'].append('WORKSHOP_ITEM_LIMIT')
                    break
                if re.fullmatch(r'[1-9][0-9]{0,19}', entry.name) and int(entry.name) <= 2**64 - 1:
                    reject_reparse(entry)
                    if entry.is_dir():
                        directories[entry.name] = entry
    except (OSError, ValueError):
        result['issues'].append('WORKSHOP_CONTENT_UNREADABLE')
    record_ids = {key for key in (*installed, *details) if re.fullmatch(r'[1-9][0-9]{0,19}', key) and int(key) <= 2**64 - 1}
    all_ids = set(directories) | record_ids
    if len(all_ids) > MAX_ITEMS and 'WORKSHOP_ITEM_LIMIT' not in result['issues']:
        result['issues'].append('WORKSHOP_ITEM_LIMIT')
    ids = sorted(all_ids, key=int)[:MAX_ITEMS]
    if workshop_id is not None:
        ids = [workshop_id]
    for identity in ids:
        record = installed.get(identity, {})
        detail = details.get(identity, {})
        record = record if isinstance(record, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        row = dict(workshop_id=identity, native_mod_id=None, title=None, author=None, mod_version=None,
                   description='', installed=False, content_state='missing', subscription_state='unknown',
                   download_state='unknown', update_available=None, progress=None,
                   file_size=number(record.get('size')), time_updated=number(record.get('timeupdated')),
                   evidence=dict(acf_installed_record=bool(record), acf_item_record=bool(detail),
                                 directory_present=identity in directories, account_subscription_verified=False), issues=[])
        if identity in directories:
            row['content_state'] = 'incomplete'
            if identity in skip_content_ids:
                row['content_state'] = 'updating'
                result['items'].append(row)
                continue
            try:
                summary = manifest_summary(directories[identity])
                row.update(summary)
                row['installed'] = not summary['missing_assemblies']
                row['content_state'] = 'installed' if row['installed'] else 'incomplete'
                row['download_state'] = 'installed' if row['installed'] else 'unknown'
                row['package_path'] = str(directories[identity])
            except (OSError, ValueError, ET.ParseError):
                row['issues'].append('WORKSHOP_MANIFEST_UNREADABLE')
        if record and detail.get('latest_manifest') and record.get('manifest'):
            row['update_available'] = record['manifest'] != detail['latest_manifest']
            if row['update_available']:
                row['download_state'] = 'update_available'
        # NeedsDownload is app-wide, not proof this particular item is queued or
        # transferring. Neither manifest mismatch nor staging files prove an
        # active download. Keep the per-item progress/queue unknown.
        result['items'].append(row)
    result['steam_record'] = {key: number(data.get(key)) for key in ('needsupdate', 'needsdownload', 'timelastupdated', 'lastbuildid')}
    return result
