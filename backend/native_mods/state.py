"""Field-preserving ES3 typed JSON and explicit content-bound authorizations."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .errors import NativeModError
from .paths import safe, valid_id

MAX_JSON = 16 * 1024 * 1024
ORDER_TYPE = 'System.Collections.Generic.List`1[[System.String, mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089]],mscorlib'


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def decode(raw, code='NATIVE_STATE_FORMAT'):
    try:
        result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise NativeModError(code) from None


def encode(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def read_bytes(path, limit=MAX_JSON):
    path = safe(path)
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(raw) > limit:
        raise NativeModError('NATIVE_FILE_LIMIT')
    return raw


def revision(raw):
    # Content digest is the optimistic-concurrency token, not a safety verdict.
    return 'missing' if raw is None else hashlib.sha256(raw).hexdigest()


def check_revision(path, expected):
    if revision(read_bytes(path)) != expected:
        raise NativeModError('NATIVE_STATE_CHANGED')


def replace_bytes(path, raw, expected, check=lambda: None):
    path = safe(path)
    check_revision(path, expected)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe(path.parent)
    fd, name = tempfile.mkstemp(prefix='.psmm-native-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        check()
        safe(path)
        check_revision(path, expected)
        os.replace(temporary, path)
        if read_bytes(path) != raw:
            raise NativeModError('NATIVE_WRITE_READBACK_FAILED')
    finally:
        if temporary.exists():
            temporary.unlink()


def load_state(path):
    raw = read_bytes(path)
    doc = {} if raw is None else decode(raw)
    if any(not isinstance(v, dict) or not isinstance(v.get('__type'), str) or 'value' not in v for v in doc.values()):
        raise NativeModError('NATIVE_STATE_FORMAT')
    order = doc.get('modOrder')
    ids = [] if order is None else order.get('value')
    if order is not None and (order.get('__type') != ORDER_TYPE or not isinstance(ids, list)
            or not all(valid_id(x) for x in ids) or len(ids) != len(set(ids)) or len(ids) > 1024):
        raise NativeModError('NATIVE_STATE_FORMAT')
    trust = doc.get('modTrust')
    trust_doc = {'records': []}
    if trust is not None:
        if trust.get('__type') != 'string' or not isinstance(trust.get('value'), str):
            raise NativeModError('NATIVE_STATE_FORMAT')
        trust_doc = decode(trust['value'].encode('utf-8'))
        records = trust_doc.get('records')
        if not isinstance(records, list) or any(not isinstance(x, dict) or not valid_id(x.get('id')) for x in records):
            raise NativeModError('NATIVE_STATE_FORMAT')
        for record in records:
            for key in ('source', 'workshopId', 'manifestHash', 'combinedHash', 'gameVersion', 'approvedAt', 'updatedAt'):
                if record.get(key) is not None and not isinstance(record[key], str):
                    raise NativeModError('NATIVE_STATE_FORMAT')
            for key in ('files', 'declared', 'detected'):
                if record.get(key) is not None and (not isinstance(record[key], list)
                        or any(not isinstance(value, str) for value in record[key])):
                    raise NativeModError('NATIVE_STATE_FORMAT')
            if 'developerMode' in record and not isinstance(record['developerMode'], bool):
                raise NativeModError('NATIVE_STATE_FORMAT')
    return {'raw': raw, 'document': doc, 'enabled_ids': ids,
            'trust_document': trust_doc, 'revision': revision(raw)}


def with_order(state, ids):
    if not isinstance(ids, list) or len(ids) > 1024 or any(not valid_id(x) for x in ids) or len(ids) != len(set(ids)):
        raise NativeModError('NATIVE_INVALID_ORDER')
    doc = copy.deepcopy(state['document'])
    if 'modOrder' not in doc:
        doc['modOrder'] = {'__type': ORDER_TYPE, 'value': []}
    doc['modOrder']['value'] = list(ids)
    removed = set(state['enabled_ids']) - set(ids)
    if removed and 'modTrust' in doc:
        trust = copy.deepcopy(state['trust_document'])
        trust['records'] = [r for r in trust['records'] if r['id'] not in removed]
        doc['modTrust']['value'] = json.dumps(trust, ensure_ascii=False, separators=(',', ':'))
    return doc


def with_authorizations(document, authorizations):
    """Merge explicit current approvals without discarding unknown ES3 fields.

    Callers own content checks and authorization. Recovery and order-only save
    deliberately never call this helper. Like ModTrust.Approve, replace every
    duplicate of the approved ID; retain extensions on its first native record.
    """
    doc = copy.deepcopy(document)
    wrapper = doc.setdefault('modTrust', {'__type': 'string', 'value': '{"records":[]}'})
    trust = decode(wrapper['value'].encode('utf-8'))
    for approval in authorizations:
        ident = approval['id']
        previous = next((r for r in trust['records'] if r['id'] == ident), {})
        record = {**copy.deepcopy(previous), **copy.deepcopy(approval)}
        trust['records'] = [r for r in trust['records'] if r['id'] != ident]
        trust['records'].append(record)
    wrapper['value'] = json.dumps(trust, ensure_ascii=False, separators=(',', ':'))
    return doc


def load_config(path):
    raw = read_bytes(path)
    doc = {'entries': []} if raw is None else decode(raw, 'NATIVE_CONFIG_FORMAT')
    entries = doc.get('entries')
    if not isinstance(entries, list) or len(entries) > 4096 or any(not isinstance(e, dict)
            or not isinstance(e.get('key'), str) or not e['key'] or not isinstance(e.get('value'), str) for e in entries):
        raise NativeModError('NATIVE_CONFIG_FORMAT')
    if len({e['key'] for e in entries}) != len(entries):
        raise NativeModError('NATIVE_CONFIG_DUPLICATE_KEY')
    return raw, doc


def update_config(doc, entries):
    if not isinstance(entries, list) or len(entries) > 4096:
        raise NativeModError('NATIVE_CONFIG_FORMAT')
    result = copy.deepcopy(doc)
    found = {row['key']: row for row in result['entries']}
    seen = set()
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get('key'), str) or not item['key']
                or not isinstance(item.get('value'), str) or item['key'] in seen):
            raise NativeModError('NATIVE_CONFIG_FORMAT')
        seen.add(item['key'])
        if item['key'] in found:
            found[item['key']]['value'] = item['value']
        else:
            result['entries'].append({'key': item['key'], 'value': item['value']})
    return result
