"""Versioned PSModManager indexes; payloads retain the validated public DTO."""
CURRENT_SCHEMA = 7
ENTITY_TABLES = {
    'Installation': ('installations', 'installation_id'),
    'RuntimeFingerprint': ('runtime_fingerprints', 'fingerprint_id'),
    'ModPackage': ('mod_packages', 'package_id'),
    'ModRelease': ('mod_releases', 'release_id'),
    'Component': ('components', 'component_id'),
    'Artifact': ('artifacts', 'sha256'),
    'FileEntry': ('file_entries', 'file_entry_id'),
    'Relation': ('relations', 'relation_id'),
    'Profile': ('profiles', 'profile_id'),
    'BaselineSnapshot': ('baseline_snapshots', 'snapshot_id'),
    'DeploymentGeneration': ('deployment_generations', 'generation_id'),
    'RuntimeSession': ('runtime_sessions', 'session_id'),
    'Observation': ('observations', 'observation_id'),
    'DiagnosticEvidence': ('diagnostic_evidence', 'evidence_id'),
    'ActionPlan': ('action_plans', 'plan_id'),
    'Approval': ('approvals', 'approval_id'),
    'Transaction': ('transactions', 'transaction_id'),
}
EXTRA_COLUMNS = {
    'Installation': ('canonical_root', 'file_identity', 'fingerprint_id'),
    'ModRelease': ('package_id', 'manifest_hash'),
    'Component': ('release_id', 'artifact_hash'),
    'FileEntry': ('release_id', 'source_hash', 'target_root_kind', 'relative_path'),
    'Relation': ('from_id', 'to_id'),
    'Profile': ('installation_id', 'revision'),
    'BaselineSnapshot': ('installation_id',),
    'DeploymentGeneration': ('installation_id', 'sequence', 'transaction_id'),
    'RuntimeSession': ('installation_id', 'generation_id'),
    'Observation': ('session_id', 'component_id'),
    'ActionPlan': ('installation_id', 'baseline_snapshot_id', 'fingerprint_id', 'base_generation', 'profile_id', 'profile_revision', 'plan_hash', 'idempotency_key'),
    'Approval': ('plan_id', 'plan_hash'),
    'Transaction': ('plan_id',),
}
_COLUMNS = {
    'Installation': 'canonical_root TEXT NOT NULL UNIQUE, file_identity TEXT NOT NULL UNIQUE, fingerprint_id TEXT REFERENCES runtime_fingerprints(fingerprint_id)',
    'ModRelease': 'package_id TEXT NOT NULL REFERENCES mod_packages(package_id), manifest_hash TEXT NOT NULL, UNIQUE(release_id, manifest_hash), UNIQUE(package_id, manifest_hash)',
    'Component': 'release_id TEXT NOT NULL REFERENCES mod_releases(release_id), artifact_hash TEXT NOT NULL REFERENCES artifacts(sha256)',
    'FileEntry': 'release_id TEXT NOT NULL REFERENCES mod_releases(release_id), source_hash TEXT NOT NULL REFERENCES artifacts(sha256), target_root_kind TEXT NOT NULL, relative_path TEXT NOT NULL, UNIQUE(release_id, target_root_kind, relative_path)',
    'Relation': 'from_id TEXT NOT NULL REFERENCES components(component_id), to_id TEXT REFERENCES components(component_id)',
    'Profile': 'installation_id TEXT NOT NULL REFERENCES installations(installation_id), revision INTEGER NOT NULL CHECK(revision >= 0)',
    'BaselineSnapshot': 'installation_id TEXT NOT NULL REFERENCES installations(installation_id)',
    'DeploymentGeneration': 'installation_id TEXT NOT NULL REFERENCES installations(installation_id), sequence INTEGER NOT NULL CHECK(sequence >= 1), transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id), UNIQUE(installation_id, sequence)',
    'RuntimeSession': 'installation_id TEXT NOT NULL REFERENCES installations(installation_id), generation_id TEXT REFERENCES deployment_generations(generation_id)',
    'Observation': 'session_id TEXT NOT NULL REFERENCES runtime_sessions(session_id), component_id TEXT REFERENCES components(component_id)',
    'ActionPlan': 'installation_id TEXT NOT NULL REFERENCES installations(installation_id), baseline_snapshot_id TEXT NOT NULL REFERENCES baseline_snapshots(snapshot_id), fingerprint_id TEXT NOT NULL REFERENCES runtime_fingerprints(fingerprint_id), base_generation TEXT REFERENCES deployment_generations(generation_id), profile_id TEXT NOT NULL REFERENCES profiles(profile_id), profile_revision INTEGER NOT NULL CHECK(profile_revision >= 0), plan_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, UNIQUE(plan_id, plan_hash)',
    'Approval': 'plan_id TEXT NOT NULL, plan_hash TEXT NOT NULL, FOREIGN KEY(plan_id, plan_hash) REFERENCES action_plans(plan_id, plan_hash)',
    'Transaction': 'plan_id TEXT NOT NULL REFERENCES action_plans(plan_id)',
}


def migrate_to_1(connection):
    for entity, (table, key) in ENTITY_TABLES.items():
        extra = ', ' + _COLUMNS[entity] if entity in _COLUMNS else ''
        connection.execute(f'CREATE TABLE {table} ({key} TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)){extra})')
    connection.execute('''CREATE TABLE profile_selections (
        profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
        release_id TEXT NOT NULL, manifest_hash TEXT NOT NULL,
        selected INTEGER NOT NULL CHECK(selected IN (0,1)),
        PRIMARY KEY(profile_id, release_id),
        FOREIGN KEY(release_id,manifest_hash) REFERENCES mod_releases(release_id,manifest_hash))''')


def migrate_to_2(connection):
    connection.execute('''CREATE TABLE profile_history (
        profile_id TEXT NOT NULL REFERENCES profiles(profile_id),
        revision INTEGER NOT NULL CHECK(revision >= 0),
        payload TEXT NOT NULL CHECK(json_valid(payload)),
        PRIMARY KEY(profile_id, revision))''')
    connection.execute('INSERT INTO profile_history SELECT profile_id, revision, payload FROM profiles')
    for action in ('INSERT', 'UPDATE'):
        connection.execute(f'''CREATE TRIGGER profile_history_{action.lower()} AFTER {action} ON profiles
            BEGIN INSERT INTO profile_history(profile_id,revision,payload)
            VALUES(NEW.profile_id,NEW.revision,NEW.payload); END''')


def migrate_to_3(connection):
    connection.execute('''CREATE TABLE scan_generations (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        generation INTEGER NOT NULL CHECK(generation > 0),
        status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed','superseded')),
        policy TEXT NOT NULL CHECK(json_valid(policy)),
        coverage TEXT NOT NULL CHECK(json_valid(coverage)),
        errors TEXT NOT NULL CHECK(json_valid(errors)),
        started_at TEXT NOT NULL, finished_at TEXT,
        PRIMARY KEY(installation_id,generation))''')
    connection.execute('''CREATE TABLE inventory_current (
        installation_id TEXT PRIMARY KEY REFERENCES installations(installation_id),
        latest_requested INTEGER NOT NULL, latest_attempt INTEGER, latest_complete INTEGER,
        FOREIGN KEY(installation_id,latest_requested) REFERENCES scan_generations(installation_id,generation),
        FOREIGN KEY(installation_id,latest_attempt) REFERENCES scan_generations(installation_id,generation),
        FOREIGN KEY(installation_id,latest_complete) REFERENCES scan_generations(installation_id,generation))''')
    connection.execute('''CREATE TABLE scan_entries (
        installation_id TEXT NOT NULL, generation INTEGER NOT NULL,
        path_key TEXT NOT NULL, entry_id TEXT NOT NULL,
        payload TEXT NOT NULL CHECK(json_valid(payload)),
        PRIMARY KEY(installation_id,generation,path_key),
        FOREIGN KEY(installation_id,generation) REFERENCES scan_generations(installation_id,generation))''')
    connection.execute('''CREATE TABLE inventory_tags (
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        path_key TEXT NOT NULL, tags TEXT NOT NULL CHECK(json_valid(tags)),
        PRIMARY KEY(installation_id,path_key))''')
    connection.execute('''CREATE TABLE inventory_parser_cache (
        sha256 TEXT NOT NULL, parser_version TEXT NOT NULL,
        metadata TEXT NOT NULL CHECK(json_valid(metadata)),
        PRIMARY KEY(sha256,parser_version))''')


def migrate_to_4(connection):
    connection.execute('''CREATE TABLE log_sources (
        source_id TEXT PRIMARY KEY NOT NULL,
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        path_key TEXT NOT NULL, relative_path TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('latest','archive','user')),
        state TEXT NOT NULL CHECK(json_valid(state)),
        UNIQUE(installation_id,path_key))''')
    connection.execute('''CREATE TABLE log_sessions (
        session_id TEXT PRIMARY KEY NOT NULL,
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        payload TEXT NOT NULL CHECK(json_valid(payload)))''')

    connection.execute('''CREATE TABLE log_evidence (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        evidence_id TEXT NOT NULL UNIQUE,
        source_id TEXT NOT NULL REFERENCES log_sources(source_id),
        epoch TEXT NOT NULL, byte_start INTEGER NOT NULL CHECK(byte_start >= 0),
        byte_end INTEGER NOT NULL CHECK(byte_end >= byte_start),
        excerpt_hash TEXT NOT NULL, raw BLOB NOT NULL,
        session_id TEXT NOT NULL REFERENCES log_sessions(session_id),
        event_group_id TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
        UNIQUE(source_id,epoch,byte_start,byte_end,excerpt_hash))''')
    connection.execute('''CREATE TABLE log_snapshots (
        source_id TEXT NOT NULL REFERENCES log_sources(source_id),
        epoch TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size >= 0),
        PRIMARY KEY(source_id,epoch,size,sha256))''')
    connection.execute('CREATE INDEX log_evidence_session ON log_evidence(session_id,seq)')
    connection.execute('CREATE INDEX log_evidence_source ON log_evidence(source_id,epoch,seq)')


def migrate_to_5(connection):
    connection.execute('''CREATE TABLE package_adoptions (
        snapshot_id TEXT NOT NULL REFERENCES baseline_snapshots(snapshot_id),
        release_id TEXT NOT NULL REFERENCES mod_releases(release_id),
        adopted_at TEXT NOT NULL,
        PRIMARY KEY(snapshot_id,release_id))''')


def migrate_to_6(connection):
    connection.execute('''CREATE TABLE profile_drafts (
        profile_id TEXT PRIMARY KEY NOT NULL,
        installation_id TEXT NOT NULL REFERENCES installations(installation_id),
        revision INTEGER NOT NULL CHECK(revision >= 0),
        committed_revision INTEGER CHECK(committed_revision >= 0),
        payload TEXT NOT NULL CHECK(json_valid(payload)))''')
    connection.execute('''CREATE TABLE profile_editor_selection (
        installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(installation_id),
        profile_id TEXT NOT NULL REFERENCES profile_drafts(profile_id))''')


def migrate_to_7(connection):
    # A current pointer is evidence of a completed cross-resource commit. Legacy
    # generations and baselines alone must never manufacture that evidence.
    connection.execute('''CREATE TABLE deployment_current (
        installation_id TEXT PRIMARY KEY NOT NULL REFERENCES installations(installation_id),
        generation_id TEXT NOT NULL REFERENCES deployment_generations(generation_id),
        manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64 AND manifest_hash NOT GLOB '*[^0-9a-f]*'),
        manifest_file_hash TEXT NOT NULL CHECK(length(manifest_file_hash)=64 AND manifest_file_hash NOT GLOB '*[^0-9a-f]*'))''')


MIGRATIONS = {1: migrate_to_1, 2: migrate_to_2, 3: migrate_to_3, 4: migrate_to_4, 5: migrate_to_5, 6: migrate_to_6, 7: migrate_to_7}
ALL_COLUMNS = {name: (key, *EXTRA_COLUMNS.get(name, ())) for name, (_, key) in ENTITY_TABLES.items()}
