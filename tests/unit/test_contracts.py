"""Closed DTO boundary, namespace and state invariants (synthetic only)."""
import copy
import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.contracts import ENTITY_MODELS, Component, ErrorDTO, ModRelease, Relation, RuntimeFingerprint, StateSummary, validate_write

FIXTURES = Path(__file__).parents[1] / "fixtures" / "contracts"
VALID = json.loads((FIXTURES / "valid.json").read_text(encoding="utf-8"))
INVALID = json.loads((FIXTURES / "invalid.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", list(ENTITY_MODELS))
def test_complete_valid_fixture_roundtrips(name):
    model = ENTITY_MODELS[name].model_validate(VALID[name])
    assert ENTITY_MODELS[name].model_validate_json(model.model_dump_json()) == model
    assert set(VALID) == set(ENTITY_MODELS)


@pytest.mark.parametrize("case", INVALID, ids=lambda c: c["name"])
def test_complete_invalid_fixture_rejected(case):
    with pytest.raises(ValidationError):
        ENTITY_MODELS[case["entity"]].model_validate(case["input"])


def test_raw_unknown_and_integer_api_versions_are_not_semver():
    assert RuntimeFingerprint.model_validate(VALID["RuntimeFingerprint"]).game_version_raw is None
    assert ModRelease.model_validate(VALID["ModRelease"]).version_raw == "api19-local"
    assert Relation.model_validate(VALID["Relation"]).requirement_raw == 19


def test_multirole_component_and_namespaces_do_not_merge():
    component = Component.model_validate(VALID["Component"])
    assert component.roles == {"melon_mod", "itemforge_content"}
    assert len({c.namespace_key for c in component.content_ids}) == 2
    changed = copy.deepcopy(VALID["Component"])
    changed["content_ids"][1]["framework"] = "itemforge"
    changed["content_ids"][1]["type"] = "OtherId"
    assert len({c.namespace_key for c in Component.model_validate(changed).content_ids}) == 2
    assert component.model_dump(mode="json")["roles"] == ["itemforge_content", "melon_mod"]


@pytest.mark.parametrize("desired,deployed,observed", itertools.product(
    ("selected", "unselected"), ("absent", "matching", "drifted", "partial", "unmanaged"),
    ("unknown", "discovered", "registered", "initialized", "content_registered", "shadowed", "delegated", "self_disabled", "failed")))
def test_three_state_layers_are_independent(desired, deployed, observed):
    data = dict(desired=desired, deployed=deployed, observed=observed, session_id=VALID["RuntimeSession"]["session_id"])
    result = StateSummary.model_validate(data)
    assert (result.desired, result.deployed, result.observed) == (desired, deployed, observed)


def test_unknown_not_promoted_and_observation_requires_session():
    result = StateSummary(desired="selected", deployed="matching", observed="unknown", session_id=None)
    assert result.observed == "unknown"
    with pytest.raises(ValidationError):
        StateSummary(desired="selected", deployed="matching", observed="registered", session_id=None)


@pytest.mark.parametrize("path", ["../a", "a/../b", "/a", "C:/a", "C:a", "//server/share", "a\\b", "a:stream", "NUL", "com1.dll", "a.", "a ", "a//b", "a/./b", "a?b", "a*b", "a<b", "a>b", 'a"b', "a|b", "a\x7fb"])
def test_paths_reject_ambiguous_windows_inputs(path):
    data = dict(VALID["FileEntry"], relative_path=path)
    with pytest.raises(ValidationError):
        ENTITY_MODELS["FileEntry"].model_validate(data)


@pytest.mark.parametrize("timestamp", [1727481600, 1727481600.0, True, "2026-09-28", "2026-09-28 00:00:00Z", "2026-09-28T00:00:00+08:00"])
def test_timestamp_requires_rfc3339_utc(timestamp):
    with pytest.raises(ValidationError):
        RuntimeFingerprint.model_validate(dict(VALID["RuntimeFingerprint"], observed_at=timestamp))


@pytest.mark.parametrize("version", [True, "1", 1.0, 0, 2])
def test_schema_is_known_integer(version):
    with pytest.raises(ValidationError):
        ModRelease.model_validate(dict(VALID["ModRelease"], schema_version=version))


def test_write_envelope_and_nested_payload_are_closed():
    request = dict(schema_version=1, request_id="fixture", command="profile.update", payload=VALID["Profile"])
    envelope, profile = validate_write(request, "profile.update", ENTITY_MODELS["Profile"])
    assert profile.revision == 0 and envelope.request_id == "fixture"
    for bad in (dict(request, execute_shell="x"), dict(request, payload=dict(VALID["Profile"], execute_shell="x"))):
        with pytest.raises(ValidationError):
            validate_write(bad, "profile.update", ENTITY_MODELS["Profile"])
    with pytest.raises(ValueError):
        validate_write(request, "package.import", ENTITY_MODELS["Profile"])
    nested = copy.deepcopy(request)
    nested["payload"]["selections"][0]["download_url"] = "https://example.invalid"
    with pytest.raises(ValidationError):
        validate_write(nested, "profile.update", ENTITY_MODELS["Profile"])


def test_error_dto_closed_and_preserves_evidence():
    error = ErrorDTO(code="INVALID_REQUEST", message="Invalid request", retryable=False, details={}, evidence_ids=[])
    assert error.model_dump(mode="json", exclude={"schema_version"}) == dict(code="INVALID_REQUEST", message="Invalid request", retryable=False, details={}, evidence_ids=[])
    with pytest.raises(ValidationError):
        ErrorDTO(**dict(error.model_dump(), retryable="false"))


def test_same_raw_version_different_content_identity_is_preserved():
    first = ModRelease.model_validate(VALID["ModRelease"])
    second = ModRelease.model_validate(dict(VALID["ModRelease"], manifest_hash="b" * 64, release_id="00000000-0000-4000-8000-000000000099"))
    assert first.version_raw == second.version_raw
    assert first.release_id != second.release_id and first.manifest_hash != second.manifest_hash


def test_snapshot_rejects_duplicate_windows_targets():
    value = copy.deepcopy(VALID["BaselineSnapshot"])
    value["files"].append(dict(value["files"][0], relative_path="mods/example.DLL"))
    with pytest.raises(ValidationError):
        ENTITY_MODELS["BaselineSnapshot"].model_validate(value)


@pytest.mark.parametrize("changes", [dict(kind="remove_owned"), dict(kind="replace_owned"), dict(owner_ids=[]), dict(desired_after="absent"), dict(target_root_kind="game_root")])
def test_plan_operations_reject_incoherent_or_unbounded_writes(changes):
    value = copy.deepcopy(VALID["ActionPlan"])
    value["operations"][0].update(changes)
    with pytest.raises(ValidationError):
        ENTITY_MODELS["ActionPlan"].model_validate(value)
