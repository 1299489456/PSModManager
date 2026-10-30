"""Native package/state orchestration. Game owns execution and approval."""
from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
import re
import shutil
import threading
from uuid import uuid4

from backend.utils.json_io import write_json_atomic
from . import dependencies as deps
from .errors import NativeModError, issue
from .packages import (inspect_package, native_fingerprint, tree_files, tree_revision,
                       extract_zip, MAX_FILE)
from .paths import installation_dict, locations, relative, safe, valid_id
from .state import (read_bytes, revision, load_state, with_order, encode, decode,
                    replace_bytes, load_config, update_config)


def now():
    return datetime.now(timezone.utc).isoformat()


class NativeModService:
    def __init__(self, paths, *, write_guard=None, process_status=None):
        self.paths = paths
        self.write_guard = write_guard
        self.process_status = process_status
        self._lock = threading.RLock()
        self._fingerprints = {}
        self._previews = {}

    def _home(self, installation):
        row = installation_dict(installation)
        ident = str(row.get('installation_id', ''))
        if re.fullmatch(r'[A-Za-z0-9_-]{1,80}', ident) is None:
            raise NativeModError('NATIVE_INSTALLATION_REQUIRED')
        self.paths.check_owned_paths()
        home = safe(self.paths.root / 'native-mods' / ident)
        loc = locations(row)
        for target in (loc['game_root'], loc['persistent_root'], loc['workshop_root']):
            if target and (home == target or home in target.parents or target in home.parents):
                raise NativeModError('NATIVE_DATA_ROOT_CONFLICT')
        return home

    def _runtime(self, installation):
        if self.process_status is None:
            return {'state': 'unknown', 'writable': False}
        try:
            result = dict(self.process_status(installation))
            if result.get('state') not in ('stopped', 'running', 'unknown'):
                return {'state': 'unknown', 'writable': False}
            result['writable'] = result['state'] == 'stopped' and result.get('writable', True) is True
            return result
        except Exception:
            return {'state': 'unknown', 'writable': False}

    @staticmethod
    def _native_supported(installation, loc):
        if str(installation_dict(installation).get('app_id', '')) == '4349200':
            return False
        try:
            return (safe(loc['game_root'] / 'Probably Stolen_Data/Managed/Assembly-CSharp.dll').is_file()
                    and safe(loc['game_root'] / 'MonoBleedingEdge').is_dir())
        except (NativeModError, OSError):
            return False

    @contextmanager
    def _lease(self, installation, *, recovery=False):
        with self._lock:
            self._home(installation)
            if not self._native_supported(installation, locations(installation)):
                raise NativeModError('NATIVE_RUNTIME_UNSUPPORTED')
            if not recovery and self._pending(installation):
                raise NativeModError('NATIVE_RECOVERY_PENDING')
            if self.write_guard is None:
                raise NativeModError('NATIVE_PROCESS_UNKNOWN')
            with self.write_guard(installation) as lease:
                if not callable(getattr(lease, 'check', None)):
                    raise NativeModError('NATIVE_PROCESS_UNKNOWN')
                lease.check()
                yield lease

    def _inventory(self, loc):
        packages, issues = [], []
        for source, root in (('local', loc['mods_root']), ('workshop', loc['workshop_root'])):
            if root is None or not root.exists():
                continue
            safe(root)
            # Match native discovery order; do not sort folder names to claim a different winner.
            for folder in root.iterdir():
                if folder.is_file():
                    if source == 'local' and folder.suffix.lower() == '.dll':
                        issues.append(issue('NATIVE_LEGACY_LOOSE_DLL', severity='warning', path=folder.name))
                    continue
                try:
                    safe(folder)
                    if not folder.is_dir():
                        continue
                    package = inspect_package(folder, source, folder.name if source == 'workshop' else None)
                    packages.append(package)
                except (NativeModError, OSError) as exc:
                    issues.append(issue(getattr(exc, 'code', 'NATIVE_PACKAGE_UNREADABLE'),
                                        severity='warning', path=str(folder), source=source))
        return packages, issues

    def _approval(self, package, record):
        result = dict(state='unknown', declared=package['capabilities'], detected=[],
                      approved_at=None, updated_at=None, scan_status='unknown',
                      current_scan_status='not_scanned', game_version=None)
        if not record:
            return result
        result.update(detected=record.get('detected') or [], approved_at=record.get('approvedAt'),
                      updated_at=record.get('updatedAt'), game_version=record.get('gameVersion'),
                      scan_status='historical_record', historical_declared=record.get('declared') or [])
        if str(record.get('source', '')).lower() != package['source'] or str(record.get('workshopId', '')) != (package['workshop_id'] or '0'):
            result.update(state='changed', reason='source_changed')
            return result
        key = (package['folder_path'], package['package_revision'], package['manifest_revision'])
        fingerprint = self._fingerprints.get(key)
        if fingerprint is None:
            fingerprint = native_fingerprint(package)
            if len(self._fingerprints) >= 2048:
                self._fingerprints.clear()
            self._fingerprints[key] = fingerprint
        if record.get('combinedHash') == fingerprint:
            result['state'] = 'approved'
        elif record.get('developerMode') and package['source'] == 'local':
            result['state'] = 'developer_mode'
        else:
            result.update(state='updated_unverified', reason='files_changed')
        result['developer_mode'] = bool(record.get('developerMode'))
        return result

    def snapshot(self, installation):
        with self._lock:
            return self._snapshot(installation)

    def _snapshot(self, installation):
        loc = locations(installation)
        if not self._native_supported(installation, loc):
            runtime = self._runtime(installation)
            runtime.update(writable=False, blocking_reason='NATIVE_RUNTIME_UNSUPPORTED')
            return dict(installation_id=str(installation_dict(installation).get('installation_id', '')),
                        revision='unsupported', ready=False, enabled_ids=[], mods=[], rules=[],
                        issues=[issue('NATIVE_RUNTIME_UNSUPPORTED')], runtime=runtime,
                        paths={k: str(v) if v is not None and k in ('game_root', 'mods_root') else None for k, v in loc.items()},
                        log={'status': 'unavailable', 'source': 'Player.log', 'by_id': {}, 'entries': []},
                        runtime_reconciliation={'status': 'unavailable', 'source': 'Player.log', 'by_id': {}, 'entries': []})
        packages, issues = self._inventory(loc)
        try:
            state = load_state(loc['state_file'])
        except NativeModError as exc:
            state = {'enabled_ids': [], 'trust_document': {'records': []},
                     'revision': revision(read_bytes(loc['state_file']))}
            issues.append(issue(exc.code))
        records = {r['id']: r for r in reversed(state['trust_document']['records'])}
        grouped = {}
        for package in packages:
            grouped.setdefault(package['id'], []).append(package)
        mods = []
        for ident, sources in grouped.items():
            mod = copy.deepcopy(sources[0])
            mod['sources'] = [{k: p[k] for k in ('source_id', 'source', 'workshop_id', 'folder_path', 'version', 'package_revision')}
                              | {'active': i == 0} for i, p in enumerate(sources)]
            if sum(p['source'] == 'local' for p in sources) > 1:
                mod['issues'].append(issue('NATIVE_DUPLICATE_LOCAL_ID', ident))
            elif len(sources) > 1:
                mod['issues'].append(issue('NATIVE_SOURCE_SHADOWED', ident, severity='warning'))
            mod['target_enabled'] = ident in state['enabled_ids']
            mod['approval'] = self._approval(mod, records.get(ident))
            mods.append(mod)
        lower_ids = {}
        for mod in mods:
            previous = lower_ids.setdefault(mod['id'].casefold(), mod['id'])
            if previous != mod['id']:
                mod['issues'].append(issue('NATIVE_ID_CASE_COLLISION', mod['id'], other_id=previous))
                next(m for m in mods if m['id'] == previous)['issues'].append(issue('NATIVE_ID_CASE_COLLISION', previous, other_id=mod['id']))
        # Imported by the owning diagnostics module; evidence stays scoped to Player.log.
        from backend.diagnostics.native_log import read_runtime_summary
        log = read_runtime_summary(loc['persistent_root'], state['enabled_ids'], grouped.keys())
        for mod in mods:
            evidence = log.get('by_id', {}).get(mod['id'], {})
            mod['run'] = {**evidence, 'state': evidence.get('runtime_state', 'unknown'), 'log_path': str(loc['log_path'])}
            mod['pending_restart'] = evidence.get('restart_pending')
        data_facts = []
        for folder in (loc['config_root'], loc['persistent_root'] / 'ModData'):
            try:
                data_facts.append((str(folder), tree_files(folder)))
            except (NativeModError, OSError) as exc:
                issues.append(issue(getattr(exc, 'code', 'NATIVE_DATA_UNREADABLE')))
        rules = self.rules_read(installation)
        pending = self._pending(installation)
        if pending:
            issues.append(issue('NATIVE_RECOVERY_PENDING', recovery_ids=pending))
        token = revision(encode({'state': state['revision'], 'rules': rules['revision'], 'packages': [(p['source_id'], p['folder_path'], p['package_revision'], p['manifest_revision']) for p in packages], 'data': data_facts}))
        runtime = self._runtime(installation)
        if pending:
            runtime.update(writable=False, blocking_reason='NATIVE_RECOVERY_PENDING')
        return dict(installation_id=str(installation_dict(installation).get('installation_id', '')),
                    revision=token, ready=not any(x['severity'] == 'error' for x in issues), enabled_ids=state['enabled_ids'], mods=mods, issues=issues,
                    runtime=runtime, paths={k: str(v) if v is not None else None for k, v in loc.items()},
                    log=log, runtime_reconciliation=log, rules=rules['rules'])

    def detail(self, installation, id):
        snap = self.snapshot(installation)
        mod = next((m for m in snap['mods'] if m['id'] == id), None)
        if mod is None:
            raise NativeModError('NATIVE_MOD_NOT_FOUND')
        mod['required_by'] = [m['id'] for m in snap['mods'] if id in m['prerequisites']]
        loc = locations(installation)
        mod['data_locations'] = [{'kind': kind, 'path': str(path), 'exists': path.exists()}
                                 for kind, path in (('native_config', loc['config_root'] / (id + '.json')),
                                                    ('native_data', loc['persistent_root'] / 'ModData' / id))]
        mod['patch_intersections'] = None
        mod['patch_coverage'] = 'not_scanned'
        return {'revision': snap['revision'], 'mod': mod}

    def validate(self, installation, enabled_ids, rules=None):
        snap = self.snapshot(installation)
        result = deps.analyze(snap['mods'], enabled_ids, snap['rules'] if rules is None else rules)
        result['issues'] += [x for x in snap['issues'] if x['severity'] == 'error']
        result['blocking'] = any(x['severity'] == 'error' for x in result['issues'])
        return result

    def enable_preview(self, installation, enabled_ids, id):
        snap = self.snapshot(installation)
        closure = deps.enable_closure(snap['mods'], enabled_ids, id)
        result = deps.analyze(snap['mods'], closure['enabled_ids'], snap['rules'])
        result['enabled_ids'] = result['sorted_ids']
        return result

    def disable_preview(self, installation, enabled_ids, id):
        return deps.disable_closure(self.snapshot(installation)['mods'], enabled_ids, id)

    def _expect(self, installation, expected):
        snap = self._snapshot(installation)
        if not isinstance(expected, str) or snap['revision'] != expected:
            raise NativeModError('NATIVE_STATE_CHANGED')
        return snap

    def _new_recovery(self, installation, kind, label, *, metadata=None):
        folder = safe(self._home(installation) / 'recovery' / uuid4().hex)
        folder.mkdir(parents=True)
        # Preparing only writes manager-owned backups. Game mutation must first
        # persist a prepared marker; interrupted backup creation cannot lock the game.
        record = dict(id=folder.name, kind=kind, label=label, created_at=now(), status='preparing', **(metadata or {}))
        write_json_atomic(folder / 'record.json', record)
        return folder, record

    @staticmethod
    def _backup(folder, raw, name='before.bin'):
        if raw is not None:
            with safe(folder / name).open('xb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())

    @staticmethod
    def _finish(folder, record, status='committed', **values):
        record.update(status=status, **values)
        write_json_atomic(safe(folder / 'record.json'), record)

    def save(self, installation, expected_revision, enabled_ids):
        with self._lease(installation) as lease:
            snap = self._expect(installation, expected_revision)
            result = deps.analyze(snap['mods'], enabled_ids, snap['rules'])
            result['issues'] += [x for x in snap['issues'] if x['severity'] == 'error']
            if any(x['severity'] == 'error' for x in result['issues']):
                raise NativeModError('NATIVE_PLAN_BLOCKED', {'issues': result['issues']})
            loc = locations(installation)
            state = load_state(loc['state_file'])
            doc = with_order(state, enabled_ids)
            folder, record = self._new_recovery(installation, 'state', '保存模组启用与顺序', metadata={'existed': state['raw'] is not None})
            self._backup(folder, state['raw'])
            self._finish(folder, record, 'prepared')
            try:
                def check():
                    lease.check()
                    self._expect(installation, expected_revision)
                replace_bytes(loc['state_file'], encode(doc), state['revision'], check)
                self._finish(folder, record)
            except Exception:
                self._finish(folder, record, 'failed')
                raise
            return {**self._snapshot(installation), 'recovery_id': record['id'], 'saved': True}

    def config_read(self, installation, id):
        if not valid_id(id):
            raise NativeModError('NATIVE_INVALID_ID')
        loc = locations(installation)
        if not self._native_supported(installation, loc):
            raise NativeModError('NATIVE_RUNTIME_UNSUPPORTED')
        path = safe(loc['config_root'] / relative(id + '.json'))
        raw, doc = load_config(path)
        return dict(id=id, revision=revision(raw), exists=raw is not None, path=str(path),
                    entries=[{'key': r['key'], 'value': r['value'], 'type': 'unknown'} for r in doc['entries']])

    def config_save(self, installation, id, expected_revision, entries):
        if not valid_id(id):
            raise NativeModError('NATIVE_INVALID_ID')
        with self._lease(installation) as lease:
            if not any(m['id'] == id for m in self._snapshot(installation)['mods']):
                raise NativeModError('NATIVE_MOD_NOT_FOUND')
            path = safe(locations(installation)['config_root'] / relative(id + '.json'))
            raw, doc = load_config(path)
            if revision(raw) != expected_revision:
                raise NativeModError('NATIVE_STATE_CHANGED')
            updated = update_config(doc, entries)
            folder, record = self._new_recovery(installation, 'config', '保存原生配置', metadata={'mod_id': id, 'existed': raw is not None})
            self._backup(folder, raw)
            self._finish(folder, record, 'prepared')
            try:
                replace_bytes(path, encode(updated), expected_revision, lease.check)
                self._finish(folder, record)
            except Exception:
                self._finish(folder, record, 'failed'); raise
            return {**self.config_read(installation, id), 'recovery_id': record['id']}

    def import_preview(self, installation, source_path):
        with self._lock:
            if not self._native_supported(installation, locations(installation)):
                raise NativeModError('NATIVE_RUNTIME_UNSUPPORTED')
            source = safe(Path(source_path))
            home = self._home(installation)
            if not source.exists() or source == home or source in home.parents or home in source.parents:
                raise NativeModError('NATIVE_IMPORT_SOURCE')
            folder = safe(home / 'staging' / uuid4().hex)
            folder.mkdir(parents=True)
            payload = folder / 'package'
            payload.mkdir()
            try:
                if source.is_dir():
                    before = tree_files(source)
                    for row in before:
                        target = safe(payload / row['path'])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(safe(source / row['path']), target)
                    if tree_revision(tree_files(source)) != tree_revision(before):
                        raise NativeModError('NATIVE_STATE_CHANGED')
                elif source.suffix.lower() == '.zip':
                    source_before = source.stat()
                    extract_zip(source, payload)
                    source_after = source.stat()
                    # Reading can update access time; it is not a content change.
                    if any(getattr(source_before, key) != getattr(source_after, key)
                           for key in ('st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_dev', 'st_ino')):
                        raise NativeModError('NATIVE_STATE_CHANGED')
                else:
                    raise NativeModError('NATIVE_PACKAGE_REQUIRED')
                package = inspect_package(payload, 'local', check_binaries=True)
                snap = self._snapshot(installation)
                issues = list(package['issues'])
                if any(m['id'] == package['id'] and any(s['source'] == 'local' for s in m['sources']) for m in snap['mods']):
                    issues.append(issue('NATIVE_LOCAL_ALREADY_EXISTS', package['id']))
                target = locations(installation)['mods_root'] / relative(package['id'])
                if target.exists():
                    issues.append(issue('NATIVE_TARGET_EXISTS', package['id']))
                token = folder.name
                self._previews[token] = {'installation_id': snap['installation_id'], 'folder': folder,
                                         'package': package, 'revision': snap['revision'], 'issues': issues}
                return {'preview_id': token, 'revision': snap['revision'], 'package': package, 'issues': issues,
                        'blocking': any(x['severity'] == 'error' for x in issues)}
            except Exception:
                # Only this freshly allocated staging directory; never the selected source.
                self._remove_owned_tree(folder, home / 'staging')
                raise

    @staticmethod
    def _remove_owned_tree(folder, parent):
        safe(folder); safe(parent)
        if folder.parent != parent or folder.resolve().parent != parent.resolve():
            raise NativeModError('NATIVE_UNSAFE_PATH')
        tree_files(folder)
        shutil.rmtree(folder)

    def import_commit(self, installation, preview_id, expected_revision):
        with self._lease(installation) as lease:
            snap = self._expect(installation, expected_revision)
            preview = self._previews.get(preview_id)
            if not preview or preview['installation_id'] != snap['installation_id']:
                raise NativeModError('NATIVE_PREVIEW_EXPIRED')
            if preview['revision'] != expected_revision:
                raise NativeModError('NATIVE_STATE_CHANGED')
            if any(x['severity'] == 'error' for x in preview['issues']):
                raise NativeModError('NATIVE_PLAN_BLOCKED', {'issues': preview['issues']})
            package = inspect_package(preview['folder'] / 'package', 'local', check_binaries=True)
            if package['package_revision'] != preview['package']['package_revision'] or package['manifest_revision'] != preview['package']['manifest_revision']:
                raise NativeModError('NATIVE_STATE_CHANGED')
            target = safe(locations(installation)['mods_root'] / relative(package['id']))
            if target.exists():
                raise NativeModError('NATIVE_TARGET_EXISTS')
            target.parent.mkdir(parents=True, exist_ok=True)
            folder, record = self._new_recovery(installation, 'import', '导入本地原生包', metadata={'mod_id': package['id'], 'folder_name': package['id']})
            # Stage on the target filesystem so directory publication is one rename.
            target_stage = safe(target.parent.parent / ('.psmm-native-stage-' + uuid4().hex))
            try:
                shutil.copytree(preview['folder'] / 'package', target_stage)
                self._finish(folder, record, 'prepared', package_revision=tree_revision(tree_files(target_stage)))
                lease.check(); safe(target); self._expect(installation, expected_revision)
                if target.exists():
                    raise NativeModError('NATIVE_TARGET_EXISTS')
                target_stage.rename(target)
                installed = inspect_package(target, 'local')
                self._finish(folder, record, package_revision=installed['package_revision'])
                self._previews.pop(preview_id, None)
                self._remove_owned_tree(preview['folder'], self._home(installation) / 'staging')
            except Exception:
                if target_stage.exists():
                    self._remove_owned_tree(target_stage, target.parent.parent)
                self._finish(folder, record, 'failed'); raise
            return {**self._snapshot(installation), 'recovery_id': record['id']}

    def recovery_list(self, installation):
        root = self._home(installation) / 'recovery'
        items = []
        if root.exists():
            for folder in safe(root).iterdir():
                raw = read_bytes(safe(folder) / 'record.json')
                if raw:
                    record = decode(raw, 'NATIVE_RECOVERY_INVALID')
                    items.append({k: record.get(k) for k in ('id', 'kind', 'created_at', 'label', 'status', 'mod_id')}
                                 | {'restorable': record.get('status') != 'preparing'})
        return {'items': sorted(items, key=lambda r: r['created_at'] or '', reverse=True)}

    def _pending(self, installation):
        return [r['id'] for r in self.recovery_list(installation)['items']
                if r['status'] in ('prepared', 'failed') and r['kind'] not in ('profile', 'rules', 'mod_data')]

    @staticmethod
    def _prior(folder, record, *, key='existed', name='before.bin'):
        raw = read_bytes(folder / name)
        if record.get(key) and raw is None:
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        return raw if record.get(key) else None

    def _record(self, installation, id):
        if not isinstance(id, str) or re.fullmatch(r'[a-f0-9]{32}', id) is None:
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        folder = safe(self._home(installation) / 'recovery' / id)
        raw = read_bytes(folder / 'record.json')
        if raw is None:
            raise NativeModError('NATIVE_RECOVERY_NOT_FOUND')
        return folder, decode(raw, 'NATIVE_RECOVERY_INVALID')

    def recovery_restore(self, installation, id, expected_revision):
        folder, record = self._record(installation, id)
        if record.get('status') == 'preparing':
            raise NativeModError('NATIVE_RECOVERY_INCOMPLETE')
        if record['kind'] == 'rules':
            return self._restore_rules(installation, folder, record)
        if record['kind'] == 'profile':
            return self._restore_profile(installation, folder, record)
        with self._lease(installation, recovery=True) as lease:
            self._expect(installation, expected_revision)
            kind = record['kind']
            loc = locations(installation)
            if kind == 'state':
                prior = self._prior(folder, record)
                if record.get('status') in ('prepared', 'failed') and read_bytes(loc['state_file']) == prior:
                    self._finish(folder, record, 'recovered')
                    return {**self._snapshot(installation), 'recovery_id': record['id']}
                previous = load_state(folder / 'before.bin')['enabled_ids'] if record.get('existed') else []
                current = load_state(loc['state_file'])
                snap = self._snapshot(installation)
                result = deps.analyze(snap['mods'], previous, snap['rules'])
                if result['blocking']:
                    raise NativeModError('NATIVE_PLAN_BLOCKED', {'issues': result['issues']})
                backup, journal = self._new_recovery(installation, 'state', '恢复启用与顺序', metadata={'existed': current['raw'] is not None})
                self._backup(backup, current['raw'])
                self._finish(backup, journal, 'prepared')
                replace_bytes(loc['state_file'], encode(with_order(current, previous)), current['revision'], lease.check)
                self._finish(backup, journal)
            elif kind == 'config':
                mod_id = record.get('mod_id')
                if not valid_id(mod_id):
                    raise NativeModError('NATIVE_RECOVERY_INVALID')
                target = safe(loc['config_root'] / relative(mod_id + '.json'))
                current = read_bytes(target)
                previous = self._prior(folder, record)
                if record.get('status') in ('prepared', 'failed') and current == previous:
                    self._finish(folder, record, 'recovered')
                    return {**self._snapshot(installation), 'recovery_id': record['id']}
                backup, journal = self._new_recovery(installation, 'config', '恢复原生配置', metadata={'mod_id': mod_id, 'existed': current is not None})
                self._backup(backup, current)
                self._finish(backup, journal, 'prepared')
                if previous is None:
                    lease.check()
                    if revision(read_bytes(target)) != revision(current):
                        raise NativeModError('NATIVE_STATE_CHANGED')
                    if target.exists():
                        target.unlink()
                else:
                    load_config(folder / 'before.bin')
                    replace_bytes(target, previous, revision(current), lease.check)
                self._finish(backup, journal)
            elif kind in ('import', 'local_remove', 'source_switch'):
                backup, journal = self._restore_package(installation, folder, record, lease)
            elif kind in ('mod_data', 'mod_data_restore'):
                backup, journal = self._restore_data(installation, folder, record, lease)
            else:
                raise NativeModError('NATIVE_RECOVERY_UNSUPPORTED')
            if record.get('status') in ('prepared', 'failed'):
                self._finish(folder, record, 'recovered')
            return {**self._snapshot(installation), 'recovery_id': journal['id']}

    def _profiles(self, installation):
        path = safe(self._home(installation) / 'profiles.json')
        raw = read_bytes(path)
        doc = {'format': 'psmm.native.profiles.v1', 'items': []} if raw is None else decode(raw, 'NATIVE_PROFILE_FORMAT')
        if doc.get('format') != 'psmm.native.profiles.v1' or not isinstance(doc.get('items'), list):
            raise NativeModError('NATIVE_PROFILE_FORMAT')
        return path, raw, doc

    def profile_list(self, installation):
        with self._lock:
            _, raw, doc = self._profiles(installation)
            return {'items': doc['items'], 'revision': revision(raw)}

    def _write_profiles(self, installation, path, raw, doc):
        folder, record = self._new_recovery(installation, 'profile', '修改原生方案', metadata={'existed': raw is not None})
        self._backup(folder, raw)
        replace_bytes(path, encode(doc), revision(raw))
        self._finish(folder, record)
        return record['id']

    @staticmethod
    def _portable_sources(sources, ids):
        if not isinstance(sources, list) or len(sources) > 1024:
            raise NativeModError('NATIVE_PROFILE_FORMAT')
        result, seen = [], set()
        for row in sources:
            if not isinstance(row, dict):
                raise NativeModError('NATIVE_PROFILE_FORMAT')
            ident, source, workshop, version = (row.get(k) for k in ('id', 'source', 'workshop_id', 'version'))
            if (not valid_id(ident) or ident not in ids or ident in seen or source not in ('local', 'workshop', None)
                    or version is not None and (not isinstance(version, str) or len(version) > 4096)):
                raise NativeModError('NATIVE_PROFILE_FORMAT')
            if workshop is not None and (source != 'workshop' or not isinstance(workshop, str)
                    or re.fullmatch(r'[1-9][0-9]{0,19}', workshop) is None or int(workshop) > 2**64 - 1):
                raise NativeModError('NATIVE_PROFILE_FORMAT')
            seen.add(ident)
            # Portable plans never carry a sender's absolute paths, config or approval.
            result.append({'id': ident, 'source': source, 'workshop_id': workshop, 'version': version})
        return result

    def _profile_row(self, installation, name, enabled_ids, rules=None, sources=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise NativeModError('NATIVE_PROFILE_NAME')
        ids = deps.ordered_ids(enabled_ids)
        snap = self._snapshot(installation)
        available = {m['id']: m for m in snap['mods']}
        rules = snap['rules'] if rules is None else rules
        deps.analyze([], [], rules)
        if sources is None:
            sources = [{'id': ident, 'source': available.get(ident, {}).get('source'),
                        'workshop_id': available.get(ident, {}).get('workshop_id'),
                        'version': available.get(ident, {}).get('version')} for ident in ids]
        return dict(name=name.strip(), enabled_ids=ids,
                    sources=self._portable_sources(sources, ids),
                    rules=[{'before': r['before'], 'after': r['after']} for r in rules], updated_at=now())

    def profile_create(self, installation, name, enabled_ids, rules=None, sources=None):
        with self._lock:
            path, raw, doc = self._profiles(installation)
            row = {**self._profile_row(installation, name, enabled_ids, rules, sources), 'id': uuid4().hex, 'created_at': now()}
            if len(doc['items']) >= 1000:
                raise NativeModError('NATIVE_PROFILE_LIMIT')
            doc['items'].append(row)
            recovery_id = self._write_profiles(installation, path, raw, doc)
            return {'profile': row, 'items': doc['items'], 'recovery_id': recovery_id}

    def profile_update(self, installation, id, name, enabled_ids, rules=None, sources=None):
        with self._lock:
            path, raw, doc = self._profiles(installation)
            row = next((r for r in doc['items'] if r['id'] == id), None)
            if row is None:
                raise NativeModError('NATIVE_PROFILE_NOT_FOUND')
            values = self._profile_row(installation, name, enabled_ids,
                                       row.get('rules', []) if rules is None else rules, sources)
            if sources is None:
                previous = {r['id']: r for r in row.get('sources', [])}
                values['sources'] = self._portable_sources(
                    [previous.get(r['id'], r) for r in values['sources']], values['enabled_ids'])
            row.update(values)
            recovery_id = self._write_profiles(installation, path, raw, doc)
            return {'profile': row, 'items': doc['items'], 'recovery_id': recovery_id}

    def profile_delete(self, installation, id):
        with self._lock:
            path, raw, doc = self._profiles(installation)
            if not any(r['id'] == id for r in doc['items']):
                raise NativeModError('NATIVE_PROFILE_NOT_FOUND')
            doc['items'] = [r for r in doc['items'] if r['id'] != id]
            recovery_id = self._write_profiles(installation, path, raw, doc)
            return {'items': doc['items'], 'recovery_id': recovery_id}

    def profile_preview(self, installation, id):
        profile = next((r for r in self.profile_list(installation)['items'] if r['id'] == id), None)
        if profile is None:
            raise NativeModError('NATIVE_PROFILE_NOT_FOUND')
        snap = self.snapshot(installation)
        current = {m['id']: m for m in snap['mods']}
        result = deps.analyze(snap['mods'], profile['enabled_ids'], profile.get('rules', []))
        return {**result, 'profile': profile, 'preview_only': True,
                'missing_ids': [i for i in profile['enabled_ids'] if i not in current],
                'source_changes': [p['id'] for p in profile['sources'] if p['id'] in current and
                                   (p['source'], p['workshop_id'], p.get('version')) !=
                                   (current[p['id']]['source'], current[p['id']]['workshop_id'], current[p['id']].get('version'))]}

    @staticmethod
    def _migration_source(legacy_profile):
        if not isinstance(legacy_profile, dict):
            raise NativeModError('NATIVE_PROFILE_FORMAT')
        document, names, seen = legacy_profile, {}, set()
        for _ in range(5):
            if id(document) in seen:
                break
            seen.add(id(document))
            releases = document.get('releases', [])
            if isinstance(releases, list) and len(releases) <= 1024:
                for release in releases:
                    if (isinstance(release, dict) and isinstance(release.get('release_id'), str)
                            and isinstance(release.get('display_name'), str)):
                        names[release['release_id']] = release['display_name']
            # Old editor get/export use selections; shared bundles wrap profile,
            # and persisted drafts wrap data.current. Never inspect undo history.
            keys = [key for key in ('selections', 'enabled_ids', 'mods') if key in document]
            if keys:
                if len(keys) != 1 or not isinstance(document[keys[0]], list) or len(document[keys[0]]) > 1024:
                    raise NativeModError('NATIVE_PROFILE_FORMAT')
                return document[keys[0]], keys[0], names
            wrappers = [document[key] for key in ('profile', 'manifest', 'data', 'current')
                        if isinstance(document.get(key), dict)]
            if len(wrappers) != 1:
                break
            document = wrappers[0]
        raise NativeModError('NATIVE_PROFILE_FORMAT')

    def migration_preview(self, installation, legacy_profile):
        entries, collection, names = self._migration_source(legacy_profile)
        current = {m['id'] for m in self.snapshot(installation)['mods']}
        enabled, skipped = [], []
        explanations = {
            'unverified_native_identity': '旧包引用尚未关联到已验证的原生 ID，不能根据名称或 release_id 猜测。',
            'disabled_in_legacy_profile': '该项在旧方案中已停用，本次不加入启用草稿。',
            'invalid_enabled_state': '旧条目的启用标记不是布尔值，不能确定是否应启用。',
            'invalid_legacy_entry': '旧条目结构无法识别。',
            'native_mod_not_installed': '明确指定的原生 ID 尚未安装。',
            'duplicate_native_id': '该原生 ID 已加入草稿，不重复加入。',
        }
        for index, row in enumerate(entries):
            item = row if isinstance(row, dict) else {}
            release = item.get('release_id')
            if collection == 'selections' and isinstance(row, str):
                release = row
            release = release if isinstance(release, str) else None
            ident = item.get('native_id')
            if isinstance(row, str) and collection != 'selections':
                ident = row
                # An old mods array can contain release UUIDs, not native IDs.
                if collection == 'mods' and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', row):
                    ident, release = None, row
            label = next((v for v in (item.get('display_name'), item.get('name'), names.get(release), release, ident)
                          if isinstance(v, str) and v.strip()), f'旧条目 {index + 1}')
            flags = [item[key] for key in ('selected', 'enabled') if key in item]
            reason = None
            if any(type(flag) is not bool for flag in flags):
                reason = 'invalid_enabled_state'
            elif False in flags:
                reason = 'disabled_in_legacy_profile'
            elif not isinstance(row, (dict, str)):
                reason = 'invalid_legacy_entry'
            elif not valid_id(ident):
                reason = 'unverified_native_identity'
            elif ident not in current:
                reason = 'native_mod_not_installed'
            elif ident in enabled:
                reason = 'duplicate_native_id'
            if reason is None:
                enabled.append(ident)
            else:
                skipped.append({'index': index, 'native_id': ident if valid_id(ident) else None,
                                'display_name': label[:200], 'release_id': release[:128] if release else None,
                                'reason': reason, 'reason_text': explanations[reason]})
        return {**self.validate(installation, enabled), 'skipped': skipped, 'preview_only': True}

    def _restore_profile(self, installation, folder, record):
        with self._lock:
            path, raw, _ = self._profiles(installation)
            previous = self._prior(folder, record)
            doc = decode(previous, 'NATIVE_PROFILE_FORMAT') if previous else {'format': 'psmm.native.profiles.v1', 'items': []}
            if doc.get('format') != 'psmm.native.profiles.v1' or not isinstance(doc.get('items'), list):
                raise NativeModError('NATIVE_PROFILE_FORMAT')
            recovery_id = self._write_profiles(installation, path, raw, doc)
            return {'items': doc['items'], 'recovery_id': recovery_id}

    def _local_transition(self, installation, id, expected_revision, *, switch=False):
        with self._lease(installation) as lease:
            snap = self._expect(installation, expected_revision)
            mod = next((m for m in snap['mods'] if m['id'] == id), None)
            if not mod or mod['source'] != 'local':
                raise NativeModError('NATIVE_LOCAL_NOT_FOUND')
            if len([s for s in mod['sources'] if s['source'] == 'local']) != 1:
                raise NativeModError('NATIVE_DUPLICATE_LOCAL_ID')
            if switch and not any(s['source'] == 'workshop' for s in mod['sources']):
                raise NativeModError('NATIVE_WORKSHOP_COPY_REQUIRED')
            affected = deps.disable_closure(snap['mods'], snap['enabled_ids'], id)
            if not switch and len(affected['affected_ids']) > (1 if id in snap['enabled_ids'] else 0):
                raise NativeModError('NATIVE_DEPENDENTS_ENABLED', {'affected_ids': affected['affected_ids']})
            loc = locations(installation)
            state = load_state(loc['state_file'])
            desired = snap['enabled_ids'] if switch else [x for x in snap['enabled_ids'] if x != id]
            doc = with_order(state, desired)
            # Switching source requires game review even when target remains enabled.
            if 'modTrust' in doc:
                trust = copy.deepcopy(state['trust_document'])
                trust['records'] = [r for r in trust['records'] if r['id'] != id]
                doc['modTrust']['value'] = json.dumps(trust, ensure_ascii=False, separators=(',', ':'))
            source = safe(Path(mod['folder_path']))
            if source.parent != loc['mods_root']:
                raise NativeModError('NATIVE_UNSAFE_PATH')
            folder, record = self._new_recovery(installation, 'source_switch' if switch else 'local_remove',
                                                '改用工坊来源' if switch else '移除本地模组',
                                                metadata={'mod_id': id, 'folder_name': source.name})
            self._backup(folder, state['raw'], 'state-before.bin')
            # Copy into recovery first; moving between volumes is not atomic.
            shutil.copytree(source, folder / 'package')
            if tree_revision(tree_files(source)) != mod['package_revision']:
                raise NativeModError('NATIVE_STATE_CHANGED')
            self._finish(folder, record, 'prepared')
            parked = safe(source.parent.parent / ('.psmm-native-removed-' + uuid4().hex))
            try:
                lease.check(); self._expect(installation, expected_revision)
                source.rename(parked)
                replace_bytes(loc['state_file'], encode(doc), state['revision'], lease.check)
                self._finish(folder, record)
                self._remove_owned_tree(parked, source.parent.parent)
            except Exception:
                if parked.exists() and not source.exists():
                    parked.rename(source)
                self._finish(folder, record, 'failed'); raise
            return {**self._snapshot(installation), 'recovery_id': record['id']}

    def local_remove(self, installation, id, expected_revision):
        return self._local_transition(installation, id, expected_revision)

    def source_switch(self, installation, id, expected_revision, source='workshop'):
        if source != 'workshop':
            raise NativeModError('NATIVE_SOURCE_SWITCH_UNSUPPORTED')
        return self._local_transition(installation, id, expected_revision, switch=True)

    def _restore_package(self, installation, folder, record, lease):
        loc = locations(installation)
        mod_id = record.get('mod_id')
        if not valid_id(mod_id):
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        name = relative(record.get('folder_name', ''))
        if '/' in name:
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        target = safe(loc['mods_root'] / name)
        if record['kind'] == 'import':
            if not target.exists() and record.get('status') in ('prepared', 'failed'):
                self._finish(folder, record, 'recovered')
                return folder, record
            if not target.exists() or tree_revision(tree_files(target)) != record.get('package_revision'):
                raise NativeModError('NATIVE_STATE_CHANGED')
            snap = self._snapshot(installation)
            affected = deps.disable_closure(snap['mods'], snap['enabled_ids'], mod_id)
            if len(affected['affected_ids']) > (1 if mod_id in snap['enabled_ids'] else 0):
                raise NativeModError('NATIVE_DEPENDENTS_ENABLED', {'affected_ids': affected['affected_ids']})
            backup, journal = self._new_recovery(installation, 'local_remove', '撤销本地导入',
                                                 metadata={'mod_id': mod_id, 'folder_name': name})
            shutil.copytree(target, backup / 'package')
            state = load_state(loc['state_file'])
            self._backup(backup, state['raw'], 'state-before.bin')
            self._finish(backup, journal, 'prepared')
            doc = with_order(state, [x for x in state['enabled_ids'] if x != mod_id])
            if 'modTrust' in doc:
                trust = copy.deepcopy(state['trust_document'])
                trust['records'] = [r for r in trust['records'] if r['id'] != mod_id]
                doc['modTrust']['value'] = json.dumps(trust, ensure_ascii=False, separators=(',', ':'))
            lease.check()
            if tree_revision(tree_files(target)) != record.get('package_revision'):
                self._finish(backup, journal, 'failed')
                raise NativeModError('NATIVE_STATE_CHANGED')
            parked = safe(target.parent.parent / ('.psmm-native-removed-' + uuid4().hex))
            target.rename(parked)
            try:
                replace_bytes(loc['state_file'], encode(doc), state['revision'], lease.check)
            except Exception:
                parked.rename(target); self._finish(backup, journal, 'failed'); raise
            self._remove_owned_tree(parked, target.parent.parent)
            self._finish(backup, journal)
            return backup, journal
        if target.exists() and record.get('status') in ('prepared', 'failed'):
            original = folder / 'package'
            if original.exists():
                # Exact byte equality is recovery identity evidence; never overwrite drift.
                a, b = tree_files(original), tree_files(target)
                if [r['path'] for r in a] == [r['path'] for r in b] and all(
                        read_bytes(original / r['path'], MAX_FILE) == read_bytes(target / r['path'], MAX_FILE) for r in a):
                    self._finish(folder, record, 'recovered')
                    return folder, record
        if target.exists():
            raise NativeModError('NATIVE_TARGET_EXISTS')
        package = inspect_package(folder / 'package', 'local', check_binaries=True)
        if package['id'] != mod_id or any(x['severity'] == 'error' for x in package['issues']):
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        backup, journal = self._new_recovery(installation, 'import', '恢复本地包（不恢复批准）',
                                             metadata={'mod_id': mod_id, 'folder_name': name})
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = safe(target.parent.parent / ('.psmm-native-stage-' + uuid4().hex))
        try:
            shutil.copytree(folder / 'package', staged)
            self._finish(backup, journal, 'prepared', package_revision=tree_revision(tree_files(staged)))
            lease.check(); safe(target)
            if target.exists():
                raise NativeModError('NATIVE_TARGET_EXISTS')
            staged.rename(target)
            self._finish(backup, journal, package_revision=tree_revision(tree_files(target)))
        except Exception:
            if staged.exists():
                self._remove_owned_tree(staged, target.parent.parent)
            self._finish(backup, journal, 'failed'); raise
        return backup, journal

    def rules_read(self, installation):
        with self._lock:
            path = safe(self._home(installation) / 'rules.json')
            raw = read_bytes(path)
            doc = {'rules': []} if raw is None else decode(raw, 'NATIVE_INVALID_RULE')
            rules = doc.get('rules')
            deps.analyze([], [], rules)  # Validate rule shapes, even when inactive.
            return {'rules': rules, 'revision': revision(raw)}

    def rules_save(self, installation, expected_revision, rules):
        with self._lock:
            path = safe(self._home(installation) / 'rules.json')
            raw = read_bytes(path)
            if revision(raw) != expected_revision:
                raise NativeModError('NATIVE_STATE_CHANGED')
            deps.analyze([], [], rules)
            doc = {'rules': []} if raw is None else decode(raw, 'NATIVE_INVALID_RULE')
            doc['rules'] = copy.deepcopy(rules)
            folder, record = self._new_recovery(installation, 'rules', '保存排序规则', metadata={'existed': raw is not None})
            self._backup(folder, raw)
            replace_bytes(path, encode(doc), expected_revision)
            self._finish(folder, record)
            return {**self.rules_read(installation), 'recovery_id': record['id']}

    def _restore_rules(self, installation, folder, record):
        raw = self._prior(folder, record)
        previous = decode(raw, 'NATIVE_INVALID_RULE')['rules'] if raw is not None else []
        current = self.rules_read(installation)
        return self.rules_save(installation, current['revision'], previous)

    def recovery_create(self, installation, id):
        if not valid_id(id):
            raise NativeModError('NATIVE_INVALID_ID')
        with self._lease(installation) as lease:
            if not any(m['id'] == id for m in self._snapshot(installation)['mods']):
                raise NativeModError('NATIVE_MOD_NOT_FOUND')
            folder, record = self._snapshot_data(installation, id, lease)
            return {'recovery_id': record['id'], 'items': self.recovery_list(installation)['items'],
                    'coverage': ['ModConfigs/' + id + '.json', 'ModData/' + id],
                    'excluded': ['custom_UserData', 'game_saves', 'approval_records']}

    def _snapshot_data(self, installation, id, lease):
        loc = locations(installation)
        config = safe(loc['config_root'] / relative(id + '.json'))
        data = safe(loc['persistent_root'] / 'ModData' / relative(id))
        raw = read_bytes(config)
        facts = tree_files(data)
        folder, record = self._new_recovery(installation, 'mod_data', '配置和原生数据快照',
                                            metadata={'mod_id': id, 'config_existed': raw is not None,
                                                      'data_existed': data.exists()})
        self._backup(folder, raw, 'config.bin')
        if data.exists():
            shutil.copytree(data, folder / 'data')
        lease.check()
        if read_bytes(config) != raw or tree_revision(tree_files(data)) != tree_revision(facts):
            self._finish(folder, record, 'failed'); raise NativeModError('NATIVE_STATE_CHANGED')
        self._finish(folder, record)
        return folder, record

    def _restore_data(self, installation, folder, record, lease):
        ident = record.get('mod_id')
        if not valid_id(ident):
            raise NativeModError('NATIVE_RECOVERY_INVALID')
        loc = locations(installation)
        config = safe(loc['config_root'] / relative(ident + '.json'))
        data = safe(loc['persistent_root'] / 'ModData' / relative(ident))
        previous = self._prior(folder, record, key='config_existed', name='config.bin')
        if previous is not None:
            load_config(folder / 'config.bin')
        if record.get('data_existed'):
            if not safe(folder / 'data').is_dir():
                raise NativeModError('NATIVE_RECOVERY_INVALID')
            tree_files(folder / 'data')
        backup, journal = self._snapshot_data(installation, ident, lease)
        self._finish(backup, journal, 'prepared', kind='mod_data_restore')
        current = read_bytes(config)
        facts = tree_files(data)
        # Prepare replacement tree alongside the target before touching current data.
        data.parent.mkdir(parents=True, exist_ok=True)
        stage = safe(data.parent / ('.psmm-' + uuid4().hex))
        parked = safe(data.parent / ('.psmm-old-' + uuid4().hex))
        published = False
        try:
            if record.get('data_existed'):
                shutil.copytree(folder / 'data', stage)
            lease.check(); safe(data)
            if tree_revision(tree_files(data)) != tree_revision(facts):
                raise NativeModError('NATIVE_STATE_CHANGED')
            if data.exists():
                data.rename(parked)
            if stage.exists():
                stage.rename(data)
                published = True
            if previous is None:
                lease.check()
                if read_bytes(config) != current:
                    raise NativeModError('NATIVE_STATE_CHANGED')
                if config.exists():
                    config.unlink()
            else:
                replace_bytes(config, previous, revision(current), lease.check)
            if parked.exists():
                self._remove_owned_tree(parked, data.parent)
            self._finish(backup, journal)
        except Exception:
            if published and data.exists() and (parked.exists() or not journal.get('data_existed')):
                self._remove_owned_tree(data, data.parent)
            if parked.exists():
                parked.rename(data)
            self._finish(backup, journal, 'failed'); raise
        finally:
            if stage.exists():
                self._remove_owned_tree(stage, data.parent)
        return backup, journal
