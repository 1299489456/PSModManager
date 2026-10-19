"""Closed desktop commands for local previews and explicit AI diagnosis."""
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .log_diagnostics import LogDiagnostics


class DiagnosticScope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID | None = None


class DiagnosticPreview(DiagnosticScope):
    source_ids: list[UUID] = Field(min_length=1, max_length=3)
    include_context: StrictBool = True


class DiagnosticSend(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    session_id: UUID | None = None


class DiagnosticRead(DiagnosticScope):
    source_id: UUID
    before: StrictInt | None = Field(default=None, ge=0, le=2**53-1)


def diagnostic_commands(context):
    service = LogDiagnostics(context)
    return {
        'ai.diagnostics.sources': (DiagnosticScope, lambda _, p: service.sources(str(p.installation_id) if p.installation_id else None)),
        'ai.diagnostics.preview': (DiagnosticPreview, lambda _, p: service.preview(
            str(p.installation_id) if p.installation_id else None, [str(value) for value in p.source_ids], p.include_context)),
        'ai.diagnostics.read': (DiagnosticRead, lambda _, p: service.read(
            str(p.installation_id) if p.installation_id else None, str(p.source_id), p.before)),
        'ai.diagnostics.send': (DiagnosticSend, lambda request_id, p: service.send(
            request_id, str(p.snapshot_id), p.question, str(p.session_id) if p.session_id else None)),
    }
