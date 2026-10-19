"""Typed repositories over the same SQLite connection owned by the desktop."""
from contextlib import contextmanager
import json

from backend.contracts import ENTITY_MODELS, Profile, StateSummary
from .exporting import profile_manifest
from .migrations import open_database
from .schema import ENTITY_TABLES, EXTRA_COLUMNS


class StorageConflict(ValueError):
    """A stable identity or optimistic revision already has different content."""


def encoded(model):
    data = model.model_dump(mode='json')
    if 'roles' in data:
        data['roles'] = sorted(data['roles'])
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Store:
    def __init__(self, path, *, library_root=None, verification_check=lambda: None, validate=True, verify_library_bytes=True):
        self.connection = open_database(path, validate=validate)
        self.library_root = library_root
        self.verification_check = verification_check
        self.verify_library_bytes = verify_library_bytes
        self.inventory = InventoryRepository(self)
        self.profiles = ProfileRepository(self)
        self.plans = PlanRepository(self)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        # A savepoint composes repository operations into an outer atomic unit.
        self.connection.execute('SAVEPOINT repository_write')
        try:
            yield
            self.connection.execute('RELEASE repository_write')
        except BaseException:
            self.connection.execute('ROLLBACK TO repository_write')
            self.connection.execute('RELEASE repository_write')
            raise

    def get(self, entity, identity):
        table, key = ENTITY_TABLES[entity]
        row = self.connection.execute(f'SELECT payload FROM {table} WHERE {key}=?', (str(identity),)).fetchone()
        if row is None:
            raise KeyError(f'{entity} not found')
        return ENTITY_MODELS[entity].model_validate_json(row['payload'])

    def list(self, entity, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid pagination')
        table, key = ENTITY_TABLES[entity]
        return [ENTITY_MODELS[entity].model_validate_json(row['payload']) for row in
                self.connection.execute(f'SELECT payload FROM {table} ORDER BY {key} LIMIT ? OFFSET ?', (limit, offset))]

    def put(self, model):
        entity = type(model).__name__
        if entity not in ENTITY_MODELS or type(model) is not ENTITY_MODELS[entity]:
            raise ValueError('Unsupported DTO')
        # Revalidate mutable nested data; model_construct/copy cannot bypass writes.
        model = ENTITY_MODELS[entity].model_validate(model.model_dump(mode='json'))
        payload = encoded(model)
        data = json.loads(payload)
        table, key = ENTITY_TABLES[entity]
        columns = (key, *EXTRA_COLUMNS.get(entity, ()))
        with self.transaction():
            existing = self.connection.execute(f'SELECT payload FROM {table} WHERE {key}=?', (data[key],)).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise StorageConflict('Immutable identity already stores different content')
                if entity == 'Profile' or (entity == 'ActionPlan' and self.library_root is not None):
                    self._references(model)
                return self.get(entity, data[key])
            self._references(model)
            self.connection.execute(
                f'INSERT INTO {table} ({",".join(columns)},payload) VALUES ({",".join("?" for _ in (*columns,"payload"))})',
                (*[data[c] for c in columns], payload))
            if entity == 'Profile':
                self.profiles._selections(model)
        return self.get(entity, data[key])

    def _references(self, model):
        entity = type(model).__name__
        if entity == 'Profile':
            if self.library_root is not None:
                from .package_repository import PackageRepository
                repository = PackageRepository(self, self.library_root)
                for selection in model.selections:
                    repository.release(selection.release_id, selection.manifest_hash, check=self.verification_check,
                                       verify_bytes=self.verify_library_bytes)
            for component_id in model.display_order:
                self.get('Component', component_id)
        elif entity == 'ActionPlan':
            for evidence_id in model.evidence_ids:
                self.get('DiagnosticEvidence', evidence_id)
            snapshot = self.get('BaselineSnapshot', model.baseline_snapshot_id)
            profile = self.get('Profile', model.profile_id)
            installation = self.get('Installation', model.installation_id)
            if (snapshot.coverage != 'complete' or snapshot.installation_id != model.installation_id
                    or snapshot.manifest_hash != model.baseline_manifest_hash
                    or installation.fingerprint_id != model.fingerprint_id
                    or profile.installation_id != model.installation_id or profile.revision != model.profile_revision):
                raise StorageConflict('Plan baseline or profile revision is not current and complete')
            if model.base_generation is not None:
                if self.get('DeploymentGeneration', model.base_generation).installation_id != model.installation_id:
                    raise StorageConflict('Plan generation belongs to another installation')
            ordinary = any(p.kind == 'execution_policy' and p.expected == 'ordinary-mods-v1' for p in model.preconditions)
            if self.library_root is not None and (self.verify_library_bytes or not ordinary):
                # Production writes share the same verified compatibility
                # input as the editor, including direct Store.put callers.
                # DTO-only stores have no filesystem library to validate.
                from backend.profiles.compatibility import evaluate_store
                report = evaluate_store(self, str(model.profile_id), model.profile_revision,
                                        check=self.verification_check)
                # Ordinary Mod clicks keep compatibility as advice. The planner
                # still rejects actual target collisions and unsupported files.
                direct = any(p.kind == 'direct_mod_action' for p in model.preconditions)
                if report['blocking'] and not direct:
                    raise StorageConflict('Compatibility blocks plan preparation')
        elif entity == 'DeploymentGeneration':
            transaction = self.get('Transaction', model.transaction_id)
            plan = self.get('ActionPlan', transaction.plan_id)
            if (transaction.state != 'committed' or transaction.target_generation != model.generation_id
                    or plan.installation_id != model.installation_id):
                raise StorageConflict('Only a committed transaction produces its target generation')
        elif entity == 'Observation':
            for evidence_id in model.evidence_ids:
                self.get('DiagnosticEvidence', evidence_id)
        elif entity == 'DiagnosticEvidence':
            for component_id in model.component_ids:
                self.get('Component', component_id)
        elif entity == 'Relation' and model.fingerprint_scope is not None:
            self.get('RuntimeFingerprint', model.fingerprint_scope)
        elif entity == 'RuntimeSession' and model.generation_id is not None:
            if self.get('DeploymentGeneration', model.generation_id).installation_id != model.installation_id:
                raise StorageConflict('Session generation belongs to another installation')


class InventoryRepository:
    ENTITIES = frozenset(ENTITY_MODELS) - {'Profile', 'ActionPlan', 'Approval', 'Transaction', 'DeploymentGeneration'}

    def __init__(self, store):
        self._store = store

    def add(self, model):
        if type(model).__name__ not in self.ENTITIES:
            raise ValueError('DTO does not belong to inventory')
        return self._store.put(model)

    def list(self, entity, **pagination):
        if entity not in self.ENTITIES:
            raise ValueError('DTO does not belong to inventory')
        return self._store.list(entity, **pagination)

    def state(self, component_id, profile_id, session_id, *, deployed):
        component = self._store.get('Component', component_id)
        profile = self._store.get('Profile', profile_id)
        desired = any(s.selected and s.release_id == component.release_id for s in profile.selections)
        events = []
        if session_id is not None:
            session = self._store.get('RuntimeSession', session_id)
            if session.installation_id != profile.installation_id:
                raise ValueError('Session belongs to another installation')
            rows = self._store.connection.execute(
                'SELECT payload FROM observations WHERE component_id=? AND session_id=? ORDER BY rowid',
                (str(component_id), str(session_id)))
            events = [ENTITY_MODELS['Observation'].model_validate_json(r['payload']).event_kind for r in rows]
        observed = 'failed' if 'failed' in events else (events[-1] if events else 'unknown')
        return StateSummary(desired='selected' if desired else 'unselected', deployed=deployed,
                            observed=observed, session_id=session_id)


class ProfileRepository:
    def __init__(self, store):
        self._store = store

    def _selections(self, profile):
        self._store.connection.executemany(
            'INSERT INTO profile_selections(profile_id,release_id,manifest_hash,selected) VALUES (?,?,?,?)',
            [(str(profile.profile_id), str(s.release_id), s.manifest_hash, int(s.selected)) for s in profile.selections])

    def create(self, profile):
        if type(profile) is not Profile:
            raise ValueError('Expected Profile DTO')
        return self._store.put(profile)

    def update(self, profile, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected a nonnegative revision')
        profile = Profile.model_validate(profile.model_dump(mode='json'))
        if profile.revision != expected_revision + 1:
            raise StorageConflict('Profile revision must advance exactly once')
        with self._store.transaction():
            self._store._references(profile)
            old = self._store.get('Profile', profile.profile_id)
            if old.installation_id != profile.installation_id:
                raise StorageConflict('Profile cannot change installation')
            changed = self._store.connection.execute(
                'UPDATE profiles SET revision=?,payload=? WHERE profile_id=? AND revision=?',
                (profile.revision, encoded(profile), str(profile.profile_id), expected_revision))
            if changed.rowcount != 1:
                raise StorageConflict('Profile revision changed')
            self._store.connection.execute('DELETE FROM profile_selections WHERE profile_id=?', (str(profile.profile_id),))
            self._selections(profile)
        return self._store.get('Profile', profile.profile_id)

    def export(self, profile_id):
        with self._store.transaction():
            profile = self._store.get('Profile', profile_id)
            self._store._references(profile)
            releases = {str(s.release_id): self._store.get('ModRelease', s.release_id) for s in profile.selections}
            packages = {str(r.package_id): self._store.get('ModPackage', r.package_id) for r in releases.values()}
            return profile_manifest(profile, releases, packages)


class PlanRepository:
    ENTITIES = {'ActionPlan', 'Approval', 'Transaction', 'DeploymentGeneration'}

    def __init__(self, store):
        self._store = store

    def add(self, model):
        entity = type(model).__name__
        if entity not in self.ENTITIES:
            raise ValueError('DTO does not belong to plans')
        return self._store.put(model)
