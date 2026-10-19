"""Reference date projection with self-authored bytes and test-only reference map."""
import hashlib

from backend.games.probably_stolen.historical import KNOWN_LOG_REFERENCES
from tests.integration.test_log_evidence import db, record, snapshot


def test_matching_reference_keeps_unknown_started_at_and_generation(db, monkeypatch):
    _,repo,installation=db
    source=repo.register_source(installation,'MelonLoader/Latest.log','latest')
    row=record(b'self-authored reference log\n')
    digest=hashlib.sha256(row['raw']).hexdigest()
    monkeypatch.setitem(KNOWN_LOG_REFERENCES,digest,'2026-09-24')
    repo.ingest(source,{},[row],snapshot(row))
    session=repo.sessions(installation)['entries'][0]
    assert session['historical_reference']==dict(date='2026-09-24',sha256=digest,basis='known_reference_hash')
    assert session['started_at'] is None and session['pid'] is None and session['generation_id'] is None
    assert session['association_confidence']=='unknown'


def test_unmatched_hash_never_invents_reference_date(db):
    _,repo,installation=db
    source=repo.register_source(installation,'MelonLoader/Latest.log','latest')
    row=record(b'not the actual historical log\n')
    repo.ingest(source,{},[row],snapshot(row))
    assert 'historical_reference' not in repo.sessions(installation)['entries'][0]


def test_new_session_after_proven_range_does_not_inherit_old_reference(db,monkeypatch):
    _,repo,installation=db
    source=repo.register_source(installation,'MelonLoader/Latest.log','latest')
    old=record(b'old self-authored fixture\n')
    digest=hashlib.sha256(old['raw']).hexdigest()
    monkeypatch.setitem(KNOWN_LOG_REFERENCES,digest,'2026-09-24')
    repo.ingest(source,{},[old],snapshot(old))
    newer=record(b'new session\n',epoch=old['epoch'],start=len(old['raw']))
    repo.ingest(source,{},[newer])
    sessions=repo.sessions(installation)['entries']
    assert 'historical_reference' in sessions[0]
    assert 'historical_reference' not in sessions[1]


def test_same_session_append_beyond_matched_snapshot_removes_reference(db,monkeypatch):
    _,repo,installation=db
    source=repo.register_source(installation,'MelonLoader/Latest.log','latest')
    old=record(b'complete self-authored reference\n')
    digest=hashlib.sha256(old['raw']).hexdigest()
    monkeypatch.setitem(KNOWN_LOG_REFERENCES,digest,'2026-09-24')
    repo.ingest(source,{},[old],snapshot(old))
    assert 'historical_reference' in repo.sessions(installation)['entries'][0]
    appended=record(b'later unverified text\n',epoch=old['epoch'],start=len(old['raw']),session=old['session'])
    repo.ingest(source,{},[appended])
    session=repo.sessions(installation)['entries'][0]
    assert 'historical_reference' not in session
    assert session['started_at'] is None and session['generation_id'] is None
