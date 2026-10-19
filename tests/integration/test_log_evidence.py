"""CASE-016/017 immutable log bytes, restart, scoped cursors and copy identity."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from backend.contracts import ENTITY_MODELS
from backend.diagnostics.evidence import EvidenceRepository
from backend.storage import Store
from backend.storage.migrations import open_database, file_sha256
from backend.storage.schema import MIGRATIONS

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/valid.json').read_text(encoding='utf-8'))


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / 'logs.sqlite3')
    for name in ('RuntimeFingerprint', 'Installation'):
        store.put(ENTITY_MODELS[name].model_validate(FIXTURE[name]))
    yield store, EvidenceRepository(store), FIXTURE['Installation']['installation_id']
    store.close()


def record(raw=b'hello\n', *, epoch=None, start=0, session=None):
    return dict(epoch=epoch or str(uuid4()), byte_start=start, byte_end=start + len(raw), raw=raw,
                text=raw.decode('utf-8', errors='replace'), complete_line=True, fragment=False,
                decode_errors=False, session=session or dict(session_id=str(uuid4()), pid=None,
                started_at=None, generation_id=None, association_confidence='unknown', reason='No launch marker'),
                event_group_id=str(uuid4()), stage='unknown', source_time=None)


def snapshot(row):
    return dict(epoch=row['epoch'], sha256=hashlib.sha256(row['raw']).hexdigest(), size=len(row['raw']))


def test_sqlite_restart_immutable_raw_windows_and_replay(db):
    store, repo, installation = db
    source = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    assert repo.register_source(installation, 'melonloader/LATEST.LOG', 'latest') == source
    item = record('中文\n'.encode())
    ids = repo.ingest(source, {'offset': 7}, [item])
    assert repo.ingest(source, {'offset': 7}, [item]) == []
    assert repo.read(installation, ids[0])['text'] == '中文\n'
    path = store.connection.execute('PRAGMA database_list').fetchone()[2]
    other = Store(path)
    try:
        restarted = EvidenceRepository(other)
        assert restarted.sources(installation)[0]['state'] == {'offset': 7}
        window = restarted.read(installation, ids[0], offset=1, limit=3)
        assert base64.b64decode(window['raw_base64']) == item['raw'][1:4]
        assert window['window_start'] == 1 and window['window_end'] == 4
        assert window['excerpt_hash'] == hashlib.sha256(item['raw']).hexdigest()
        assert window['next_offset'] == 4
    finally:
        other.close()


def test_old_epoch_evidence_survives_rotation_and_scope(db):
    _, repo, installation = db
    source = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    old = repo.ingest(source, {}, [record(b'old\n')])[0]
    repo.ingest(source, {}, [record(b'new\n')])
    assert repo.read(installation, old)['text'] == 'old\n'
    with pytest.raises(KeyError):
        repo.read(str(uuid4()), old)
    assert repo.list(installation)['total'] == 2


def test_full_snapshot_copy_dedup_and_pending_preserves_proven_range(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    archive = repo.register_source(installation, 'MelonLoader/Logs/old.log', 'archive')
    first, second = record(), record()
    ids = repo.ingest(latest, {}, [first], snapshot(first))
    copied = repo.ingest(archive, {}, [second], snapshot(second))
    assert repo.list(installation)['total'] == 1
    assert repo.sessions(installation)['total'] == 1
    assert repo.read(installation, copied[0])['raw_base64'] == repo.read(installation, ids[0])['raw_base64']
    # Appended residual bytes do not erase immutable proof of the older range.
    repo.ingest(archive, {'epoch': second['epoch'], 'offset': len(second['raw']) + 1}, [])
    assert repo.list(installation)['total'] == 1


def test_same_text_distinct_session_or_user_source_not_line_dedup(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    user = repo.register_source(installation, 'UserData/My Mod/run.log', 'user')
    for source in (latest, latest, user):
        row = record()
        repo.ingest(source, {}, [row], snapshot(row))
    assert repo.list(installation)['total'] == 3
    assert repo.sessions(installation)['total'] == 3


def test_equal_line_without_full_snapshot_never_dedup(db):
    _, repo, installation = db
    for path, kind in [('MelonLoader/Latest.log', 'latest'), ('MelonLoader/Logs/a.log', 'archive')]:
        source = repo.register_source(installation, path, kind)
        repo.ingest(source, {}, [record()])
    assert repo.list(installation)['total'] == 2


def test_independent_cursors_session_group_filters(db):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    rows = [record(str(i).encode()) for i in range(5)]
    repo.ingest(source, {}, rows)
    a = repo.list(installation, limit=2)
    b = repo.list(installation, after=a['next_cursor'], limit=2)
    assert [r['text'] for r in a['entries'] + b['entries']] == ['0', '1', '2', '3']
    assert repo.list(installation, limit=2) == a
    assert repo.list(installation, session_id=rows[2]['session']['session_id'])['total'] == 1
    assert repo.list(installation, group_id=rows[3]['event_group_id'])['entries'][0]['text'] == '3'
    assert len(repo.sessions(installation, limit=2)['entries']) == 2


@pytest.mark.parametrize('path,kind', [('C:/x.log','user'), ('../x.log','user'), ('UserData/../x.log','user'),
    ('UserData/a:ads.log','user'), ('UserData/a.log','latest'), ('MelonLoader/Logs/sub/x.log','archive'),
    ('MelonLoader/Other.log','latest'), ('UserData/a.dll','user'), ('/UserData/a.log','user')])
def test_source_path_policy(db, path, kind):
    with pytest.raises(ValueError):
        db[1].register_source(db[2], path, kind)


def test_capacity_is_atomic_checkpoint_not_silently_lost(db, monkeypatch):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    monkeypatch.setattr('backend.diagnostics.evidence.MAX_RECORDS', 1)
    with pytest.raises(ValueError, match='CAPACITY'):
        repo.ingest(source, {'offset': 50}, [record(), record()])
    assert repo.list(installation)['total'] == 0
    assert repo.sources(installation)[0]['state'] == {}


def test_byte_and_source_capacity_are_explicit(db, monkeypatch):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    monkeypatch.setattr('backend.diagnostics.evidence.MAX_SOURCES', 1)
    with pytest.raises(ValueError, match='CAPACITY'):
        repo.register_source(installation, 'UserData/next.log', 'user')
    monkeypatch.setattr('backend.diagnostics.evidence.MAX_BYTES', 2)
    with pytest.raises(ValueError, match='CAPACITY'):
        repo.ingest(source, {}, [record()])
    assert repo.list(installation)['total'] == 0


def test_wrapped_checkpoint_does_not_erase_historical_copy_proof(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    archive = repo.register_source(installation, 'MelonLoader/Logs/old.log', 'archive')
    first, second = record(), record()
    repo.ingest(latest, {}, [first], snapshot(first))
    repo.ingest(archive, {}, [second], snapshot(second))
    repo.ingest(archive, {'tailer': {'epoch': str(uuid4()), 'offset': 20}}, [])
    assert repo.list(installation)['total'] == 1
    repo.ingest(archive, {'tailer': {'epoch': second['epoch'], 'offset': 20}}, [])
    assert repo.list(installation)['total'] == 1


def test_archive_append_is_visible_without_repeating_prior_copy(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    archive = repo.register_source(installation, 'MelonLoader/Logs/old.log', 'archive')
    first, second = record(), record()
    repo.ingest(latest, {}, [first], snapshot(first))
    old_id = repo.ingest(archive, {}, [second], snapshot(second))[0]
    more = record(b'next\n', epoch=second['epoch'], start=len(second['raw']), session=second['session'])
    repo.ingest(archive, {}, [more])
    assert [e['text'] for e in repo.list(installation)['entries']] == ['hello\n', 'next\n']
    assert repo.read(installation, old_id)['text'] == 'hello\n'


def test_latest_append_preserves_archive_copy_range(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    archive = repo.register_source(installation, 'MelonLoader/Logs/old.log', 'archive')
    first, second = record(), record()
    repo.ingest(latest, {}, [first], snapshot(first))
    repo.ingest(archive, {}, [second], snapshot(second))
    more = record(b'new\n', epoch=first['epoch'], start=len(first['raw']), session=first['session'])
    combined = first['raw'] + more['raw']
    repo.ingest(latest, {}, [more], dict(epoch=first['epoch'], size=len(combined), sha256=hashlib.sha256(combined).hexdigest()))
    assert [e['text'] for e in repo.list(installation)['entries']] == ['hello\n', 'new\n']
    assert repo.sessions(installation)['total'] == 1


def test_archives_dedup_when_full_content_proven(db):
    _, repo, installation = db
    for name in ('a', 'b'):
        source = repo.register_source(installation, f'MelonLoader/Logs/{name}.log', 'archive')
        row = record()
        repo.ingest(source, {}, [row], snapshot(row))
    assert repo.list(installation)['total'] == 1


def test_page_only_decodes_requested_payloads(db, monkeypatch):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    repo.ingest(source, {}, [record() for _ in range(100)])
    original = json.loads
    decoded = []
    def count(value):
        decoded.append(value)
        return original(value)
    monkeypatch.setattr('backend.diagnostics.evidence.json.loads', count)
    assert len(repo.list(installation, limit=2)['entries']) == 2
    assert len(decoded) == 2
    decoded.clear()
    assert len(repo.sessions(installation, limit=3)['entries']) == 3
    assert len(decoded) == 3


def test_equal_first_line_with_different_whole_file_not_copy(db):
    _, repo, installation = db
    latest = repo.register_source(installation, 'MelonLoader/Latest.log', 'latest')
    archive = repo.register_source(installation, 'MelonLoader/Logs/old.log', 'archive')
    first, second = record(), record()
    repo.ingest(latest, {}, [first], snapshot(first))
    other_snapshot = snapshot(second) | {'size': 100, 'sha256': 'a' * 64}
    repo.ingest(archive, {}, [second], other_snapshot)
    assert repo.list(installation)['total'] == 2


def test_session_cannot_gain_deployment_or_mutate(db):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    row = record()
    repo.ingest(source, {}, [row])
    changed = record(session=row['session'] | {'pid': 22})
    with pytest.raises(ValueError, match='Immutable'):
        repo.ingest(source, {}, [changed])
    row = record()
    row['session']['generation_id'] = str(uuid4())
    with pytest.raises(ValueError, match='association'):
        repo.ingest(source, {}, [row])


def test_schema_three_backup_and_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / 'old.sqlite3'
    connection = open_database(path, target_version=3)
    connection.close()
    before = file_sha256(path)
    original = MIGRATIONS[4]
    def fail(connection):
        original(connection)
        raise RuntimeError('injected migration failure')
    monkeypatch.setitem(MIGRATIONS, 4, fail)
    with pytest.raises(RuntimeError):
        open_database(path)
    assert file_sha256(path) == before
    assert len(list(tmp_path.glob('*.before-v3.*.bak'))) == 1
    monkeypatch.setitem(MIGRATIONS, 4, original)
    connection = open_database(path)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 4
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    connection.close()
    assert len(list(tmp_path.glob('*.before-v3.*.bak'))) == 2


@pytest.mark.parametrize('offset,limit', [(-1,1),(0,0),(0,8193),(True,1),(0,False)])
def test_evidence_window_bounds(db, offset, limit):
    _, repo, installation = db
    source = repo.register_source(installation, 'UserData/test.log', 'user')
    evidence = repo.ingest(source, {}, [record()])[0]
    with pytest.raises(ValueError):
        repo.read(installation, evidence, offset=offset, limit=limit)
