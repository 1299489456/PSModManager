"""Explicit import-version association and confirmed install/update commands."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from backend.profiles.transfer_api import job


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ImportVersion(Empty):
    preview_id: UUID
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    display_name: str = Field(min_length=1, max_length=200)
    version_raw: str | None = Field(default=None, min_length=1, max_length=200)
    package_id: UUID | None = None


class Preview(Empty):
    installation_id: UUID
    release_id: UUID
    manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class Apply(Preview):
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    replaced_ids: list[UUID] = Field(max_length=200)
    idempotency_key: UUID


def commands(context):
    return {
        'package.import_version': (ImportVersion, lambda rid,p: job(context, rid, lambda task: context.imports.commit(
            str(p.preview_id), p.preview_hash, p.display_name, p.version_raw,
            package_id=str(p.package_id) if p.package_id else None, check=task.check_cancelled, commit=task.commit))),
        'package.update_preview': (Preview, lambda rid,p: job(context, rid, lambda task: context.mod_updates.preview(
            str(p.installation_id), str(p.release_id), p.manifest_hash, check=task.check_cancelled))),
        'package.update_apply': (Apply, lambda rid,p: job(context, rid, lambda task: context.mod_updates.apply(
            str(p.installation_id), str(p.release_id), p.manifest_hash, p.snapshot_id,
            [str(value) for value in p.replaced_ids], str(p.idempotency_key), check=task.check_cancelled,
            commit=task.commit, progress=task.progress))),
    }
