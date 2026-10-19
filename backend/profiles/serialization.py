"""Portable manifest v2: fixed fields, bounded plain text, no filesystem references.

Unknown author versions remain byte-for-byte text when portable. Unsafe values
are rejected, never silently rewritten to null. Source references are excluded.
"""
from datetime import datetime
import hashlib
import json
import re
import unicodedata
from uuid import UUID

MAX_BYTES = 256 * 1024
MAX_SELECTIONS = 200
MAX_GROUPS = 50
SOURCE_KINDS = {'author_manifest', 'embedded_metadata', 'loader_rule', 'runtime_log', 'user', 'heuristic', 'unknown'}


class ProfileError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code='PROFILE_INVALID_REQUEST'):
    if not condition:
        raise ProfileError(code)


def keys(value, required):
    require(type(value) is dict and set(value) == set(required), 'PROFILE_MANIFEST_INVALID')


def text(value, limit=120):
    require(isinstance(value, str) and 0 < len(value) <= limit and value == value.strip(), 'PROFILE_MANIFEST_UNSAFE')
    require(all(c.isalnum() or c in ' ._+-()' for c in value)
            and not any(unicodedata.category(c).startswith('C') for c in value)
            and not re.search(r'(?i)password|passwd|secret|token|credential|bearer|authorization|api.?key', value),
            'PROFILE_MANIFEST_UNSAFE')
    return value


def identifier(value):
    require(isinstance(value, str), 'PROFILE_MANIFEST_INVALID')
    try:
        require(str(UUID(value)) == value, 'PROFILE_MANIFEST_INVALID')
    except (ValueError, TypeError, AttributeError):
        raise ProfileError('PROFILE_MANIFEST_INVALID') from None
    return value


def version(value):
    require(isinstance(value, str) and 0 < len(value) <= 200, 'PROFILE_MANIFEST_UNSAFE')
    require(not any(unicodedata.category(c).startswith('C') for c in value)
            and not any(c in value for c in '\\/:@=<>|')
            and not re.search(r'(?i)password|passwd|secret|token|credential|bearer|authorization|api.?key', value),
            'PROFILE_MANIFEST_UNSAFE')
    return value


def selection(value):
    keys(value, ('release_id', 'manifest_hash', 'selected', 'version_raw', 'source', 'group'))
    identifier(value['release_id'])
    require(isinstance(value['manifest_hash'], str) and re.fullmatch('[a-f0-9]{64}', value['manifest_hash']), 'PROFILE_MANIFEST_INVALID')
    require(type(value['selected']) is bool, 'PROFILE_MANIFEST_INVALID')
    if value['version_raw'] is not None:
        version(value['version_raw'])
    if value['group'] is not None:
        text(value['group'], 80)
    keys(value['source'], ('kind', 'observed_at'))
    require(value['source']['kind'] in SOURCE_KINDS, 'PROFILE_MANIFEST_INVALID')
    stamp = value['source']['observed_at']
    if stamp is not None:
        require(isinstance(stamp, str) and len(stamp) <= 40, 'PROFILE_MANIFEST_INVALID')
        try:
            require(datetime.fromisoformat(stamp.replace('Z', '+00:00')).tzinfo is not None, 'PROFILE_MANIFEST_INVALID')
        except ValueError:
            raise ProfileError('PROFILE_MANIFEST_INVALID') from None
    return value


def validate(value):
    keys(value, ('schema_version', 'name', 'groups', 'selections'))
    require(type(value['schema_version']) is int and value['schema_version'] == 2, 'PROFILE_MANIFEST_INVALID')
    text(value['name'])
    require(type(value['groups']) is list and len(value['groups']) <= MAX_GROUPS, 'PROFILE_MANIFEST_INVALID')
    for group in value['groups']:
        text(group, 80)
    require(len(set(value['groups'])) == len(value['groups']), 'PROFILE_MANIFEST_INVALID')
    require(type(value['selections']) is list and len(value['selections']) <= MAX_SELECTIONS, 'PROFILE_MANIFEST_INVALID')
    seen = set()
    for item in value['selections']:
        selection(item)
        require(item['release_id'] not in seen and (item['group'] is None or item['group'] in value['groups']), 'PROFILE_MANIFEST_INVALID')
        seen.add(item['release_id'])
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'PROFILE_MANIFEST_INVALID')
        value[key] = item
    return value


def decode(raw):
    require(isinstance(raw, bytes) and len(raw) <= MAX_BYTES, 'PROFILE_MANIFEST_INVALID')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ProfileError('PROFILE_MANIFEST_INVALID')))
        return validate(value)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise ProfileError('PROFILE_MANIFEST_INVALID') from None


def encode(value):
    raw = json.dumps(validate(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    require(len(raw) <= MAX_BYTES, 'PROFILE_MANIFEST_INVALID')
    return raw


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()
