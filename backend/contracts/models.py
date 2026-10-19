"""Versioned, closed DTOs shared by product repositories and future services."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints, field_serializer, field_validator, model_validator

Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
Hash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
NonNegative = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[StrictInt, Field(ge=1)]
Confidence = Literal["confirmed", "inferred", "unknown"]
Observed = Literal["unknown", "discovered", "registered", "initialized", "content_registered", "shadowed", "delegated", "self_disabled", "failed"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)
    schema_version: Literal[1] = 1

    @field_validator("*", mode="before")
    @classmethod
    def rfc3339_times(cls, value, info):
        annotation = cls.model_fields[info.field_name].annotation
        if annotation in (datetime, datetime | None) and value is not None:
            if not isinstance(value, datetime) and not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value)):
                raise ValueError("timestamp must be an RFC3339 UTC string")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_is_integer(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def utc_times(cls, value):
        if isinstance(value, datetime) and (value.utcoffset() != timedelta(0)):
            raise ValueError("timestamp must be UTC RFC3339")
        return value


class Source(DTO):
    kind: Literal["author_manifest", "embedded_metadata", "loader_rule", "runtime_log", "user", "heuristic"]
    reference: Text
    observed_at: datetime


def relative_path(value: str) -> str:
    if not value or "\\" in value or ":" in value or value.startswith("/"):
        raise ValueError("path must be portable relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or part.rstrip(" .") != part or any(ord(c) < 32 or ord(c) == 127 or c in '<>"|?*' for c in part) for part in parts):
        raise ValueError("non-canonical relative path")
    if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p) for p in parts):
        raise ValueError("reserved Windows path")
    return value


class Installation(DTO):
    installation_id: UUID
    canonical_root: Text
    file_identity: Text
    channel: Text
    app_id: Text | None
    build_id: Text | None
    fingerprint_id: UUID

    @field_validator("canonical_root")
    @classmethod
    def absolute_root(cls, value):
        if not (PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()):
            raise ValueError("canonical_root must be absolute")
        return value


class RuntimeFingerprint(DTO):
    fingerprint_id: UUID
    game_version_raw: Text | None
    unity_version: Text | None
    loader_version: Text | None
    exe_hash: Hash | None
    game_assembly_hash: Hash | None
    metadata_hash: Hash | None
    observed_at: datetime
    source: Source
    coverage: Literal["partial", "complete"]

    @model_validator(mode="after")
    def complete_hashes(self):
        if self.coverage == "complete" and any(v is None for v in (self.exe_hash, self.game_assembly_hash, self.metadata_hash)):
            raise ValueError("complete fingerprint requires all strong hashes")
        return self


class ModPackage(DTO):
    package_id: UUID
    display_name: Text
    source: Source
    trust: Literal["unknown", "trusted", "untrusted"]


class ModRelease(DTO):
    release_id: UUID
    package_id: UUID
    version_raw: Text | None
    manifest_hash: Hash


class ContentId(DTO):
    framework: Text
    type: Text
    value: Text

    @property
    def namespace_key(self) -> tuple[str, str, str]:
        return self.framework, self.type, self.value


class Component(DTO):
    component_id: UUID
    release_id: UUID
    artifact_hash: Hash
    file_name: Text
    assembly_name: Text | None
    assembly_version: Text | None
    melon_info: dict[str, Any] | None
    roles: frozenset[Literal["melon_mod", "melon_plugin", "itemforge_content", "content", "library", "unknown"]]
    content_ids: list[ContentId]

    @field_validator("file_name")
    @classmethod
    def filename(cls, value):
        relative_path(value)
        if "/" in value:
            raise ValueError("file_name must be a basename")
        return value

    @field_validator("roles")
    @classmethod
    def nonempty_roles(cls, value):
        if not value:
            raise ValueError("roles cannot be empty; use unknown")
        return value

    @field_serializer("roles")
    def stable_roles(self, value):
        return sorted(value)


class Artifact(DTO):
    sha256: Hash
    size: NonNegative
    library_relative_path: Text
    _path = field_validator("library_relative_path")(relative_path)


class FileEntry(DTO):
    file_entry_id: UUID
    release_id: UUID
    source_hash: Hash
    target_root_kind: Literal["mods", "plugins", "user_data", "user_libs", "game_root"]
    relative_path: Text
    lifecycle: Literal["immutable", "shared_config", "private_config", "mutable_data", "generated", "unowned"]
    ownership: Literal["exclusive", "shared_identical", "external", "unknown"]
    _path = field_validator("relative_path")(relative_path)


class Relation(DTO):
    relation_id: UUID
    from_id: UUID
    to_id: UUID | None = None
    capability: Text | None = None
    kind: Literal["hard_dependency", "soft_dependency", "capability_requirement", "mutual_exclusion", "assembly_shadow", "file_conflict", "content_id_conflict", "runtime_interaction"]
    requirement_raw: Text | StrictInt | None
    source: Source
    confidence: Confidence
    fingerprint_scope: UUID | None

    @model_validator(mode="after")
    def one_target(self):
        if (self.to_id is None) == (self.capability is None):
            raise ValueError("relation requires exactly one to_id or capability")
        if self.kind == "capability_requirement" and self.capability is None:
            raise ValueError("capability_requirement must name a capability")
        return self


class Selection(DTO):
    release_id: UUID
    manifest_hash: Hash
    selected: StrictBool = True


class Profile(DTO):
    profile_id: UUID
    installation_id: UUID
    revision: NonNegative
    selections: list[Selection]
    config_overrides: dict[str, Any]
    display_order: list[UUID]
    workspace_order: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_selections(self):
        ids = [s.release_id for s in self.selections]
        if (len(ids) != len(set(ids)) or len(self.display_order) != len(set(self.display_order))
                or len(self.workspace_order) != len(set(self.workspace_order))):
            raise ValueError("duplicate selection or display order")
        return self


class SnapshotFile(DTO):
    relative_path: Text
    sha256: Hash
    size: NonNegative
    _path = field_validator("relative_path")(relative_path)


class BaselineSnapshot(DTO):
    snapshot_id: UUID
    installation_id: UUID
    scan_generation: NonNegative
    manifest_hash: Hash
    files: list[SnapshotFile]
    coverage: Literal["partial", "complete"]

    @model_validator(mode="after")
    def unique_files(self):
        paths = [f.relative_path.casefold() for f in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("snapshot contains duplicate normalized paths")
        return self


class DeploymentGeneration(DTO):
    generation_id: UUID
    installation_id: UUID
    sequence: Positive
    manifest_hash: Hash
    transaction_id: UUID
    committed_at: datetime


class RuntimeSession(DTO):
    session_id: UUID
    installation_id: UUID
    generation_id: UUID | None
    pid: Positive | None
    started_at: datetime | None
    log_file_ids: list[UUID]
    association_confidence: Confidence


class Observation(DTO):
    observation_id: UUID
    session_id: UUID
    component_id: UUID | None
    event_kind: Observed
    stage: Text
    evidence_ids: list[UUID]


class DiagnosticEvidence(DTO):
    evidence_id: UUID
    source_id: UUID
    epoch: NonNegative
    byte_start: NonNegative
    byte_end: NonNegative
    excerpt_hash: Hash
    source_time: datetime | None
    rule_id: Text | None
    component_ids: list[UUID]

    @model_validator(mode="after")
    def byte_range(self):
        if self.byte_end < self.byte_start:
            raise ValueError("byte_end precedes byte_start")
        return self


class PlanOperation(DTO):
    operation_id: UUID
    kind: Literal["install_owned", "replace_owned", "remove_owned", "edit_supported_config"]
    target_root_kind: Literal["mods", "plugins", "user_libs", "supported_config"]
    relative_path: Text
    expected_before: Hash | Literal["absent"]
    desired_after: Hash | Literal["absent"]
    owner_ids: list[UUID]
    _path = field_validator("relative_path")(relative_path)

    @model_validator(mode="after")
    def operation_hashes(self):
        if not self.owner_ids or len(self.owner_ids) != len(set(self.owner_ids)):
            raise ValueError("operation requires unique owners")
        if self.kind == "install_owned" and (self.expected_before != "absent" or self.desired_after == "absent"):
            raise ValueError("install requires absent before and content after")
        if self.kind == "remove_owned" and (self.expected_before == "absent" or self.desired_after != "absent"):
            raise ValueError("remove requires content before and absent after")
        if self.kind in {"replace_owned", "edit_supported_config"} and "absent" in (self.expected_before, self.desired_after):
            raise ValueError("replace/config edit requires before and after hashes")
        return self


class PlanCheck(DTO):
    kind: Text
    reference: Text
    expected: str | StrictInt | StrictBool | None


class ActionPlan(DTO):
    plan_id: UUID
    installation_id: UUID
    profile_id: UUID
    profile_revision: NonNegative
    base_generation: UUID | None
    baseline_snapshot_id: UUID
    baseline_manifest_hash: Hash
    fingerprint_id: UUID
    plan_hash: Hash
    idempotency_key: UUID
    operations: list[PlanOperation]
    preconditions: list[PlanCheck]
    verification_checks: list[PlanCheck]
    evidence_ids: list[UUID]
    disk_estimate_by_volume: dict[Text, NonNegative]
    backup_scope: list[Text]
    restart_required: StrictBool
    approval_scope: list[Text]
    expires_at: datetime


class Approval(DTO):
    approval_id: UUID
    plan_id: UUID
    plan_hash: Hash
    scope: list[Text]
    actor: Text
    expires_at: datetime


class Transaction(DTO):
    transaction_id: UUID
    plan_id: UUID
    state: Literal["planned", "approved", "staging", "ready", "applying", "committed", "rolling_back", "rolled_back", "failed", "cancelled", "recovery_required"]
    journal_path: Text
    target_generation: UUID
    last_sequence: NonNegative
    _path = field_validator("journal_path")(relative_path)


class StateSummary(DTO):
    desired: Literal["selected", "unselected"]
    deployed: Literal["absent", "matching", "drifted", "partial", "unmanaged"]
    observed: Observed
    session_id: UUID | None

    @model_validator(mode="after")
    def observations_require_session(self):
        if self.observed != "unknown" and self.session_id is None:
            raise ValueError("observed state requires a session")
        return self


class ErrorDTO(DTO):
    code: Text
    message: Text
    retryable: StrictBool
    details: dict[str, Any]
    evidence_ids: list[UUID]


class WriteRequest(DTO):
    request_id: Text
    command: Text
    payload: dict[str, Any]
    expected_revision: NonNegative | None = None


def validate_write(request: dict[str, Any], command: str, payload_type: type[DTO]) -> tuple[WriteRequest, DTO]:
    """Validate both the closed envelope and the consumer-selected closed payload.

    A route must explicitly choose its DTO; no arbitrary command/type reflection.
    """
    envelope = WriteRequest.model_validate(request)
    if envelope.command != command:
        raise ValueError("unsupported write command")
    return envelope, payload_type.model_validate(envelope.payload)


ENTITY_MODELS = {cls.__name__: cls for cls in (
    Installation, RuntimeFingerprint, ModPackage, ModRelease, Component, Artifact,
    FileEntry, Relation, Profile, BaselineSnapshot, DeploymentGeneration,
    RuntimeSession, Observation, DiagnosticEvidence, ActionPlan, Approval, Transaction,
)}
