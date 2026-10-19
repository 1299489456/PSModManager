"""Read-only, revision-bound compatibility input shared by editor and plan store."""
from contextlib import contextmanager
import json
import sqlite3
import time
from uuid import UUID

from backend.contracts import ENTITY_MODELS
from backend.storage.schema import ENTITY_TABLES
from backend.storage.package_repository import PackageRepository, content_hash

MAX_RELEASES = 200
MAX_COMPONENTS = 1000
MAX_RELATIONS = 2000
MAX_FILES = 4000
MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 48 * 1024
MAX_LOGS = 2000
MAX_LIBRARY_BYTES = 512 * 1024 * 1024


class CompatibilityError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code='COMPATIBILITY_INPUT_INVALID'):
    if not condition:
        raise CompatibilityError(code)


def _identifier(value):
    try:
        _require(isinstance(value, str) and str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError):
        raise CompatibilityError('COMPATIBILITY_INPUT_INVALID') from None


def _budget(check):
    deadline = time.monotonic() + 120
    def bounded():
        check()
        _require(time.monotonic() <= deadline, 'COMPATIBILITY_LIMIT')
    return bounded


class _ReadStore:
    """Only the read protocol PackageRepository needs, backed by SQLite mode=ro."""
    def __init__(self, connection, library_root):
        self.connection = connection
        self.library_root = library_root

    def get(self, entity, identity):
        table, key = ENTITY_TABLES[entity]
        row = self.connection.execute(f'SELECT payload FROM {table} WHERE {key}=?', (str(identity),)).fetchone()
        if row is None:
            raise KeyError(entity)
        return ENTITY_MODELS[entity].model_validate_json(row['payload'])


def _rows(store, entity, limit, check):
    table, key = ENTITY_TABLES[entity]
    rows = store.connection.execute(f'SELECT payload FROM {table} ORDER BY {key} LIMIT ?', (limit + 1,))
    result, used = [], 0
    for row in rows:
        check()
        used += len(row['payload'].encode('utf-8'))
        _require(len(result) < limit and used <= MAX_INPUT_BYTES, 'COMPATIBILITY_LIMIT')
        result.append(ENTITY_MODELS[entity].model_validate_json(row['payload']).model_dump(mode='json'))
    return result


def _collect(store, profile_id, expected_revision, profile_kind, enable, disable, check):
    check()
    if profile_kind == 'draft':
        row = store.connection.execute('SELECT * FROM profile_drafts WHERE profile_id=?', (profile_id,)).fetchone()
        _require(row is not None, 'COMPATIBILITY_PROFILE_NOT_FOUND')
        current = json.loads(row['payload'])['current']
        installation_id, revision, selections = row['installation_id'], row['revision'], current['selections']
    else:
        profile = store.get('Profile', profile_id)
        installation_id, revision = str(profile.installation_id), profile.revision
        selections = [item.model_dump(mode='json') for item in profile.selections]
    _require(revision == expected_revision, 'COMPATIBILITY_STALE')
    _require(len(selections) <= MAX_RELEASES, 'COMPATIBILITY_LIMIT')
    installation = store.get('Installation', installation_id)
    fingerprint = store.get('RuntimeFingerprint', installation.fingerprint_id).model_dump(mode='json')
    releases = _rows(store, 'ModRelease', MAX_RELEASES, check)
    package_labels = {row['package_id']: row['display_name'] for row in _rows(store, 'ModPackage', MAX_RELEASES, check)}
    components = _rows(store, 'Component', MAX_COMPONENTS, check)
    relations = _rows(store, 'Relation', MAX_RELATIONS, check)
    files = _rows(store, 'FileEntry', MAX_FILES, check)
    from backend.diagnostics.evidence import _VISIBLE, _NOT_ALIAS
    from backend.diagnostics.explain import _bounded
    evidence, truncated = _bounded(store.connection.execute(_VISIBLE +
        'SELECT e.seq,e.payload,ls.payload AS session FROM log_evidence e JOIN log_sources s USING(source_id) '
        'JOIN log_sessions ls ON ls.session_id=e.session_id AND ls.installation_id=s.installation_id '
        'WHERE s.installation_id=? AND ' + _NOT_ALIAS + ' ORDER BY e.seq LIMIT ?',
        (installation_id, installation_id, MAX_LOGS + 1)), MAX_LOGS, 2 * 1024 * 1024)
    _require(not truncated, 'COMPATIBILITY_LIMIT')
    by_release = {row['release_id']: row for row in releases}
    for selection in selections:
        _require(selection['release_id'] in by_release
                 and by_release[selection['release_id']]['manifest_hash'] == selection['manifest_hash'],
                 'COMPATIBILITY_UNRESOLVED')
    _require(all(value in by_release for value in (*enable, *disable)), 'COMPATIBILITY_UNRESOLVED')
    data = dict(profile_id=profile_id, profile_kind=profile_kind, profile_revision=revision,
                installation_id=installation_id, fingerprint=fingerprint, selections=selections,
                releases=releases, components=components, relations=relations, files=files,
                requested_enable=list(enable), requested_disable=list(disable), evidence=evidence, package_labels=package_labels)
    _require(len(json.dumps(data, ensure_ascii=False).encode('utf-8')) <= MAX_INPUT_BYTES, 'COMPATIBILITY_LIMIT')
    return data


def _verify_library(store, data, release_ids, check):
    repository = PackageRepository(store, store.library_root)
    # Every library release presented as a closure candidate must have verified
    # bytes; a merely indexed or stale candidate must not enter the graph.
    hashes = {row['source_hash'] for row in data['files'] if row['release_id'] in release_ids}
    used = 0
    for digest in hashes:
        check()
        try:
            used += store.get('Artifact', digest).size
        except KeyError:
            raise CompatibilityError('COMPATIBILITY_UNRESOLVED') from None
        _require(used <= MAX_LIBRARY_BYTES, 'COMPATIBILITY_LIMIT')
    for release in data['releases']:
        if release['release_id'] not in release_ids:
            continue
        check()
        try:
            repository.release(release['release_id'], release['manifest_hash'], check=check)
        except (ValueError, OSError, KeyError) as exc:
            if isinstance(exc, (InterruptedError, CompatibilityError)):
                raise
            raise CompatibilityError('COMPATIBILITY_UNRESOLVED') from None


def _evaluate(store, data, check):
    from .graph import evaluate_graph, GraphError
    versions = {item['release_id']: item['version_raw'] for item in data['releases']}
    components = [item | {'version_raw': versions[item['release_id']]} for item in data['components']]
    try:
        report = evaluate_graph(components=components, relations=data['relations'], files=data['files'],
            fingerprint=data['fingerprint'], selected_release_ids=[item['release_id'] for item in data['selections'] if item['selected']],
            requested_enable=data['requested_enable'], requested_disable=data['requested_disable'], check=check)
    except GraphError as exc:
        raise CompatibilityError('COMPATIBILITY_LIMIT' if exc.code == 'GRAPH_LIMIT_EXCEEDED' else 'COMPATIBILITY_INPUT_INVALID') from None
    verified = sorted({item['release_id'] for item in data['selections']} | set(data['requested_enable'])
                      | set(data['requested_disable']) | set(report['enable_preview']['release_ids'])
                      | set(report['disable_preview']['affected_release_ids']))
    _verify_library(store, data, verified, check)
    _runtime(report, data, check)
    visible = set(verified) | {identity for finding in report['findings'] for identity in finding['release_ids']}
    report['release_labels'] = {row['release_id']: data['package_labels'][row['package_id']]
                                for row in data['releases'] if row['release_id'] in visible}
    binding = {key: data[key] for key in ('profile_id', 'profile_kind', 'profile_revision', 'installation_id')}
    binding.update(fingerprint_id=data['fingerprint']['fingerprint_id'], input_hash=content_hash(data), verified_release_ids=verified)
    report = dict(report) | {'binding': binding, 'compatibility_allows_plan': not report['blocking']}
    report['report_hash'] = content_hash(report)
    _require(len(json.dumps(report, ensure_ascii=False).encode('utf-8')) <= MAX_REPORT_BYTES, 'COMPATIBILITY_LIMIT')
    return report


def _runtime(report, data, check):
    """Reuse stored, session-scoped diagnostics; never read live installation logs."""
    from backend.diagnostics.rules import evaluate
    selected = {row['release_id'] for row in data['selections'] if row['selected']} | set(data['requested_enable'])
    components = [row for row in data['components'] if row['release_id'] in selected]
    evidence = data['evidence']
    report['runtime_evidence'] = dict(coverage='unknown' if not evidence else 'stored_inputs_only',
        log_collection_coverage='unknown', record_count=len(evidence), diagnostic_count=0,
        current_deployment_verified=False)
    if not evidence:
        return
    _require(len(components) <= 512, 'COMPATIBILITY_LIMIT')
    entries = [dict(entry_id=row['component_id'], relative_path=row['file_name'], presence='present',
                    sha256=row['artifact_hash'], analysis=dict(assembly_name=row['assembly_name'],
                    assembly_version=row['assembly_version'], melon_info=row['melon_info'], roles=row['roles'])) for row in components]
    check()
    output = evaluate(entries, evidence, installation_id=data['installation_id'], generation=None,
                      fingerprint=data['fingerprint'], coverage='partial')
    _require(not output['summary']['truncated'], 'COMPATIBILITY_LIMIT')
    by_component = {row['component_id']: row for row in components}
    by_evidence = {row['evidence_id']: row for row in evidence}
    for diagnostic in output['diagnostics']:
        check()
        if not diagnostic['entry_ids'] or not diagnostic['evidence_ids']:
            continue
        related = [key for key in diagnostic['entry_ids'] if key in by_component]
        if not related:
            continue
        observation = by_evidence[diagnostic['evidence_ids'][0]]
        item = dict(category='runtime', severity='info' if diagnostic['classification'] in ('info', 'recovered', 'delegated') else 'warning',
            code=diagnostic['rule_id'], message='已记录会话中的运行提示，仅供核对；不证明当前部署仍存在同一问题。',
            component_ids=related, release_ids=sorted({by_component[key]['release_id'] for key in related}), relation_ids=[],
            source=dict(kind='runtime_log', reference='evidence:' + diagnostic['evidence_ids'][0], observed_at=observation.get('source_time')),
            scope=dict(fingerprint_id=data['fingerprint']['fingerprint_id'], source_id=observation['source_id'],
                       epoch=observation['epoch'], session_id=observation['session_id'], applicability='recorded_session_only'),
            reason_chain=dict(component_ids=related, evidence_ids=diagnostic['evidence_ids']), detail=diagnostic)
        item['id'] = content_hash(item)
        report['findings'].append(item)
        report['runtime_evidence']['diagnostic_count'] += 1
    report['runtime_evidence']['rule_version'] = output['rule_version']


def evaluate_store(store, profile_id, expected_revision, *, profile_kind='committed',
                   enable_release_ids=(), disable_release_ids=(), check=lambda: None):
    """Use an existing transaction; no new connection or nested BEGIN is opened."""
    check = _budget(check)
    _identifier(profile_id)
    _require(type(expected_revision) is int and expected_revision >= 0)
    _require(profile_kind in ('draft', 'committed'))
    _require(isinstance(enable_release_ids, (list, tuple)) and isinstance(disable_release_ids, (list, tuple)))
    enable, disable = tuple(enable_release_ids), tuple(disable_release_ids)
    _require(len(enable) <= MAX_RELEASES and len(disable) <= MAX_RELEASES, 'COMPATIBILITY_LIMIT')
    for value in (*enable, *disable):
        _identifier(value)
    _require(len(set(enable)) == len(enable) and len(set(disable)) == len(disable) and not set(enable) & set(disable))
    try:
        data = _collect(store, profile_id, expected_revision, profile_kind, enable, disable, check)
        return _evaluate(store, data, check)
    except KeyError:
        raise CompatibilityError('COMPATIBILITY_UNRESOLVED') from None


class CompatibilityService:
    def __init__(self, paths):
        self.paths = paths

    @contextmanager
    def _open(self):
        self.paths.check_owned_paths()
        connection = sqlite3.connect(self.paths.database.as_uri() + '?mode=ro', uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN')
            yield _ReadStore(connection, self.paths.root / 'library')
        finally:
            connection.close()

    def check(self, profile_id, expected_revision, *, profile_kind='draft',
              enable_release_ids=(), disable_release_ids=(), check=lambda: None):
        check = _budget(check)
        with self._open() as store:
            report = evaluate_store(store, profile_id, expected_revision, profile_kind=profile_kind,
                enable_release_ids=enable_release_ids, disable_release_ids=disable_release_ids, check=check)
        check()
        # A fresh read transaction detects edits/fingerprint advancement or
        # changed catalog facts which arrived while bytes were being verified.
        with self._open() as store:
            try:
                fresh = _collect(store, profile_id, expected_revision, profile_kind,
                                 tuple(enable_release_ids), tuple(disable_release_ids), check)
            except KeyError:
                raise CompatibilityError('COMPATIBILITY_STALE') from None
            _require(content_hash(fresh) == report['binding']['input_hash'], 'COMPATIBILITY_STALE')
            _verify_library(store, fresh, report['binding']['verified_release_ids'], check)
        return report

    def check_committed(self, profile_id, expected_revision, *, check=lambda: None):
        return self.check(profile_id, expected_revision, profile_kind='committed', check=check)
