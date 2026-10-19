"""Closed proposals and canonical plan hashing shared by UI and AI consumers."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .models import ActionPlan, Hash, NonNegative
from backend.storage.package_repository import content_hash


class PlanProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    idempotency_key: UUID


class PlanIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plan_id: UUID
    plan_hash: Hash


class PlanPage(PlanIdentity):
    offset: Annotated[StrictInt, Field(ge=0, le=128)] = 0
    limit: Annotated[StrictInt, Field(ge=1, le=20)] = 20


class PlanApply(PlanIdentity):
    approval_id: UUID | None = None


class ModAction(BaseModel):
    """One ordinary click; paths and internal plans are chosen by the host."""
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID
    release_id: UUID
    manifest_hash: Hash
    action: Literal['install', 'enable', 'disable', 'uninstall']
    idempotency_key: UUID


class ModWorkspaceSave(BaseModel):
    """One saved loading-list draft, not caller-supplied filesystem operations."""
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID
    snapshot_id: Hash
    enabled_ids: Annotated[list[UUID], Field(max_length=200)]
    idempotency_key: UUID


class RollbackProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID
    transaction_id: UUID
    expected_generation: UUID
    idempotency_key: UUID


class OwnedFile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_root_kind: str
    relative_path: str
    sha256: Hash
    size: NonNegative
    owner_ids: Annotated[list[UUID], Field(min_length=1, max_length=200)]


def plan_digest(plan: ActionPlan) -> str:
    """Hash every public field, including identities, order, scope and expiry."""
    return content_hash(plan.model_dump(mode='json', exclude={'plan_hash'}))
