import json
from uuid import uuid4

import pytest

from backend.diagnostics.sessions import SessionTracker


EPOCH = str(uuid4())


def records(*lines, epoch=EPOCH, start=0):
    output = []
    for line in lines:
        raw = (line + "\n").encode()
        output.append(dict(epoch=epoch, byte_start=start, byte_end=start + len(raw), raw=raw,
                           text=line + "\n", complete_line=True, fragment=False, decode_errors=0))
        start += len(raw)
    return output


def test_history_without_marker_stays_unknown_and_never_binds_deployment():
    rows = SessionTracker("latest").classify(records("[12:30:12.123] Loading assembly: old.dll"))
    assert rows[0]["session"]["association_confidence"] == "unknown"
    assert rows[0]["session"]["generation_id"] is None
    assert rows[0]["session"]["started_at"] is None
    assert rows[0]["source_time"] == "12:30:12.123"


def test_banner_pid_and_reused_file_new_session():
    rows = SessionTracker("latest").classify(records("old text", "[00:00:00] [MelonLoader] MelonLoader v0.7.3",
                                                     "Process ID: 120", "PID = 121", "MelonLoader v0.7.3"))
    assert rows[0]["session"]["session_id"] != rows[1]["session"]["session_id"]
    assert rows[1]["session"]["session_id"] != rows[2]["session"]["session_id"]
    assert rows[2]["session"]["pid"] == 120
    assert rows[3]["session"]["pid"] == 121
    assert rows[2]["session"]["session_id"] != rows[3]["session"]["session_id"]
    assert rows[3]["session"]["session_id"] != rows[4]["session"]["session_id"]
    assert rows[2]["session"]["reason"] == "log_declared_unverified"
    assert all(row["session"]["generation_id"] is None for row in rows)


@pytest.mark.parametrize("stamp,expected", [("2026-01-02T03:04:05Z", "2026-01-02T03:04:05Z"),
    ("2026-01-02T03:04:05+08:00", "2026-01-01T19:04:05Z"), ("12:00:00", None),
    ("2026-99-99T03:04:05Z", None)])
def test_only_explicit_valid_zoned_time_can_start_session(stamp, expected):
    row = SessionTracker("latest").classify(records(f"[{stamp}] MelonLoader v0.7.3"))[0]
    assert row["session"]["started_at"] == expected


def test_tutorial_and_fragments_do_not_create_sessions_or_failures():
    input_rows = records("Tutorial: MelonLoader v0.7.3 startup", "If an error or 失败 occurs restart",
                         "MelonLoader v0.7.3", "System.Exception: fragment")
    for row in input_rows[2:]:
        row.update(fragment=True, complete_line=False)
    rows = SessionTracker("latest").classify(input_rows)
    assert len({row["session"]["session_id"] for row in rows}) == 1
    assert all(row["stage"] == "unknown" for row in rows)
    assert not any("confirmed_failure" in row for row in rows)
    assert "session" not in input_rows[0]


def test_exception_stack_group_and_stages_keep_exact_bytes():
    original = records("Loading assembly: 中文.dll", "System.InvalidOperationException: failed",
                       "   at Foo.Bar()", "---> System.Exception: inner", "--- End of inner exception stack trace ---",
                       "Initializing Mods", "ItemForge: Registered 3 items")
    rows = SessionTracker("latest").classify(original)
    assert len({row["event_group_id"] for row in rows[1:5]}) == 1
    assert rows[0]["event_group_id"] != rows[1]["event_group_id"]
    assert rows[5]["stage"] == "initialization"
    assert rows[6]["stage"] == "content"
    assert [(r["raw"], r["byte_start"], r["byte_end"]) for r in rows] == [(r["raw"], r["byte_start"], r["byte_end"]) for r in original]


def test_stack_group_is_bounded():
    rows = SessionTracker("latest").classify(records("System.Exception: bad", *[" at F()"] * 260))
    groups = [row["event_group_id"] for row in rows]
    assert max(groups.count(group) for group in set(groups)) == 128
    assert len(set(groups)) == 3


def test_persisted_state_preserves_session_and_stack_group():
    tracker = SessionTracker("latest")
    first = tracker.classify(records("MelonLoader v0.7.3", "PID: 12", "System.Exception: bad"))
    state = json.loads(json.dumps(tracker.export_state()))
    second = SessionTracker("latest", state).classify(records(" at F()", start=first[-1]["byte_end"]))[0]
    assert second["session"] == first[-1]["session"]
    assert second["event_group_id"] == first[-1]["event_group_id"]
    state["session"]["pid"] = 999
    assert tracker.export_state()["session"]["pid"] == 12


def test_sources_epochs_and_abandoned_fragment_are_isolated():
    tracker = SessionTracker("latest")
    old = tracker.classify(records("MelonLoader v0.7.3"))[0]
    new_epoch = str(uuid4())
    current = tracker.classify(records("PID: 4", epoch=new_epoch))[0]
    abandoned = records("MelonLoader v0.7.3")[0]
    abandoned.update(abandoned=True, complete_line=False, fragment=True)
    old_fragment = tracker.classify([abandoned])[0]
    assert old_fragment["session"]["session_id"] != old["session"]["session_id"]
    assert tracker.export_state()["session"] == current["session"]
    assert old["session"]["session_id"] != current["session"]["session_id"]
    other = SessionTracker("archive").classify(records("MelonLoader v0.7.3"))[0]
    assert old["session"]["session_id"] != other["session"]["session_id"]
    with pytest.raises(ValueError, match="SOURCE_MISMATCH"):
        SessionTracker("other", tracker.export_state())


@pytest.mark.parametrize("pid", ["0", "-1", "4294967296", "12 not a process record"])
def test_invalid_pid_does_not_claim_process(pid):
    row = SessionTracker("latest").classify(records("PID: " + pid))[0]
    assert row["session"]["pid"] is None
    assert row["session"]["association_confidence"] == "unknown"


@pytest.mark.parametrize("banner", ["MelonLoader v0.7.3 Open-Beta", "[MelonLoader] v0.7.3",
                                   "[MelonLoader] Version: 0.7.3"])
def test_explicit_loader_banner_variants(banner):
    row = SessionTracker("latest").classify(records(banner))[0]
    assert row["session"]["reason"] == "log_start_marker_unverified"
    assert row["stage"] == "bootstrap"
