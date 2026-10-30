"""Manager-owned evidence storage. Corrupt authority is never silently reset."""
import json
import re
import threading
from pathlib import Path

from backend.native_mods.paths import installation_dict, locations, safe
from backend.native_mods.state import read_bytes, decode, encode, revision, replace_bytes
from .errors import SecurityError
from .rules import RULE_VERSION

TOKEN = re.compile(r'[0-9a-f]{32}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


def scope(package):
    return json.dumps([package['id'], package['source'], package.get('workshop_id'),
                       str(Path(package['folder_path']).absolute()).casefold()], separators=(',', ':'))


class EvidenceStore:
    def __init__(self, paths):
        self.paths = paths
        self._cache_lock = threading.RLock()

    def home(self, installation):
        ident = str(installation_dict(installation).get('installation_id', ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident):
            raise SecurityError('SECURITY_INVALID_REQUEST')
        self.paths.check_owned_paths()
        home = safe(self.paths.root / 'mod-security' / ident)
        for target in locations(installation).values():
            if target is not None and (home == target or home in target.parents or target in home.parents):
                raise SecurityError('SECURITY_INVALID_REQUEST')
        return home

    def load(self, installation):
        path = self.home(installation) / 'state.json'
        try:
            raw = read_bytes(path)
            if raw is None:
                # A vanished index must not hide already persisted quarantine.
                if path.parent.exists() and any(path.parent.iterdir()):
                    raise ValueError()
                return dict(schema_version=1, reports={}, report_integrity={}, records=[], pending={}), 'missing'
            doc = decode(raw)
            if (doc.get('schema_version') != 1 or not isinstance(doc.get('reports'), dict)
                    or not isinstance(doc.get('records'), list) or len(doc['records']) > 4096
                    or not isinstance(doc.get('pending'), dict) or not isinstance(doc.get('report_integrity'), dict)):
                raise ValueError()
            for key, value in doc['pending'].items():
                if not isinstance(key, str) or not isinstance(value, str) or not TOKEN.fullmatch(value):
                    raise ValueError()
            for key, value in doc['reports'].items():
                parts = json.loads(key)
                if (not isinstance(parts, list) or len(parts) != 4 or not TOKEN.fullmatch(value)
                        or not HASH.fullmatch(doc['report_integrity'].get(value, ''))):
                    raise ValueError()
            for row in doc['records']:
                if (not isinstance(row, dict) or not TOKEN.fullmatch(row.get('id', ''))
                        or row.get('status') not in {'prepared', 'applied', 'failed', 'restored'}
                        or row.get('mode') not in {'disable', 'quarantine'}
                        or not isinstance(row.get('scope'), str)
                        or row.get('source') not in {'local', 'workshop'}
                        or not isinstance(row.get('mod_id'), str)
                        or not isinstance(row.get('recovery_ids'), list)
                        or any(not isinstance(x, str) or not TOKEN.fullmatch(x) for x in row['recovery_ids'])
                        or (row.get('package_recovery_id') is not None and
                            (not isinstance(row['package_recovery_id'], str) or not TOKEN.fullmatch(row['package_recovery_id'])))):
                    raise ValueError()
                parts = json.loads(row['scope'])
                if not isinstance(parts, list) or len(parts) != 4 or parts[:3] != [row['mod_id'], row['source'], row.get('workshop_id')]:
                    raise ValueError()
            return doc, revision(raw)
        except SecurityError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
            raise SecurityError('SECURITY_RECORD_INVALID') from exc
        except Exception as exc:
            # Native safe/decode errors have their own vocabulary, not UI authority.
            raise SecurityError('SECURITY_RECORD_INVALID') from exc

    def save(self, installation, doc, expected):
        try:
            raw = encode(doc)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError()
            replace_bytes(self.home(installation) / 'state.json', raw, expected)
            return revision(raw)
        except Exception as exc:
            raise SecurityError('SECURITY_IO') from exc

    def report(self, installation, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise SecurityError('SECURITY_RECORD_INVALID')
        try:
            state, _ = self.load(installation)
            raw = read_bytes(self.home(installation) / 'reports' / (token + '.json'))
            if revision(raw) != state['report_integrity'].get(token):
                raise ValueError()
            doc = decode(raw or b'')
            if (doc.get('report_id') != token or doc.get('schema_version') != 1
                    or not isinstance(doc.get('scope'), str) or not isinstance(doc.get('files'), list)
                    or not isinstance(doc.get('coverage'), dict) or not isinstance(doc.get('findings'), list)
                    or doc.get('verdict') not in {'incomplete', 'high_risk', 'review_required', 'no_high_risk_findings'}
                    or not isinstance(doc.get('blocking_reasons'), list)):
                raise ValueError()
            if doc.get('content_hash') is not None and not HASH.fullmatch(doc['content_hash']):
                raise ValueError()
            for row in doc['files']:
                if (not isinstance(row, dict) or not isinstance(row.get('path'), str)
                        or not isinstance(row.get('size'), int) or row['size'] < 0
                        or not HASH.fullmatch(row.get('sha256', ''))):
                    raise ValueError()
            return doc
        except Exception as exc:
            raise SecurityError('SECURITY_RECORD_INVALID') from exc

    def put_report(self, installation, report, state, expected):
        # Establish index first, so an interrupted first scan is recoverable as
        # unscanned rather than mistaking an orphan report for authority.
        if expected == 'missing':
            expected = self.save(installation, state, expected)
        target = self.home(installation) / 'reports' / (report['report_id'] + '.json')
        raw = encode(report)
        if len(raw) > 16 * 1024 * 1024:
            raise SecurityError('SECURITY_INCOMPLETE')
        replace_bytes(target, raw, 'missing')
        state['reports'][report['scope']] = report['report_id']
        state['report_integrity'][report['report_id']] = revision(raw)
        state['pending'].pop(report['scope'], None)
        self.save(installation, state, expected)

    def cache(self, digest, result=None):
        with self._cache_lock:
            return self._cache(digest, result)

    def approval_cache(self, content_hash, scanner_identity, result=None):
        """Native approval evidence is bound to both the package and game code."""
        if not HASH.fullmatch(content_hash) or not HASH.fullmatch(scanner_identity):
            raise SecurityError('SECURITY_INVALID_REQUEST')
        with self._cache_lock:
            path = safe(self.paths.root / 'mod-security-analysis' /
                        f'{content_hash}.approval-{scanner_identity}.v1.json')
            def valid(value):
                return (isinstance(value, dict) and value.get('status') == 'scanned'
                        and value.get('scanner_identity') == scanner_identity and value.get('error') is None
                        and type(value.get('refused')) is bool and isinstance(value.get('capabilities'), list)
                        and all(v in {'files', 'network', 'process', 'native', 'dynamic-code', 'reflection-external'}
                                for v in value['capabilities'])
                        and isinstance(value.get('hard_flags'), list) and isinstance(value.get('findings'), list)
                        and isinstance(value.get('files'), list))
            if result is None:
                try:
                    envelope = decode(read_bytes(path) or b'')
                    value = envelope.get('result')
                    if valid(value) and revision(encode(value)) == envelope.get('integrity'):
                        return value
                except Exception:
                    pass
                return None
            compact = {key: result.get(key) for key in ('status', 'scanner_identity', 'files', 'capabilities',
                        'hard_flags', 'findings', 'refused', 'methods_scanned', 'error')}
            if not valid(compact):
                raise SecurityError('SECURITY_GAME_SCAN_INCOMPLETE')
            raw = encode(dict(result=compact, integrity=revision(encode(compact))))
            if len(raw) <= 1024 * 1024:
                replace_bytes(path, raw, revision(read_bytes(path)))
            return compact

    def _cache(self, digest, result=None):
        if not HASH.fullmatch(digest):
            return None
        # Wire protocol and analyzer schema are separate cache-key dimensions.
        path = safe(self.paths.root / 'mod-security-analysis' / f'{digest}.{RULE_VERSION}.v2-s2.json')
        if result is None:
            try:
                envelope = decode(read_bytes(path) or b'')
                doc = envelope.get('result')
                if not isinstance(doc, dict) or revision(encode(doc)) != envelope.get('integrity'):
                    return None
                sec = (doc.get('metadata') or {}).get('security') or {}
                return doc if doc.get('sha256') == digest and sec.get('schema_version') == 2 and sec.get('analyzer') == 'psmm-static-security-v2' else None
            except Exception:
                return None  # Cache is never authority: parse anew.
        compact = {k: result.get(k) for k in ('status', 'sha256', 'error', 'metadata')}
        raw = encode(dict(result=compact, integrity=revision(encode(compact))))
        if len(raw) <= 8 * 1024 * 1024:
            replace_bytes(path, raw, revision(read_bytes(path)))
        return result
