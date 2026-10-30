"""Closed provider management and host-sourced translation-cache lookups."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StringConstraints

from .native_api import Payload, installation

ProviderId = Annotated[str, StringConstraints(strict=True, pattern=r'^(default|[a-f0-9]{32})$')]
WorkshopId = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9]{1,20}$')]


class Selected(Payload):
    provider_id: ProviderId


class Save(Payload):
    provider_id: ProviderId | None = None
    name: str = Field(min_length=1, max_length=80)
    provider: Literal['openai_compatible'] = 'openai_compatible'
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    remember: StrictBool = True


class Bind(Selected):
    purpose: Literal['analysis', 'translation']


class Cache(Payload):
    workshop_id: WorkshopId
    installation_id: UUID | None = None


def commands(context):
    def cached(_, payload):
        from backend.ai.workshop_translation import translation_source
        value = installation(context, payload.installation_id) if payload.installation_id else None
        detail = context.workshop.cached_detail(payload.workshop_id, value)
        if detail is None:
            return dict(hit=False, source_pending=True)
        source = translation_source(detail)
        return context.ai.cached_translation(payload.workshop_id, source)

    return {
        'ai.providers.list': (Payload, lambda _, p: context.ai.provider_settings()),
        'ai.providers.save': (Save, lambda _, p: context.ai.save_provider(p.provider_id, p.name,
            dict(provider=p.provider, base_url=p.base_url, model=p.model), p.api_key, p.remember)),
        'ai.providers.bind': (Bind, lambda _, p: context.ai.bind_provider(p.purpose, p.provider_id)),
        'ai.providers.delete': (Selected, lambda _, p: context.ai.delete_provider(p.provider_id)),
        'ai.providers.clear_key': (Selected, lambda _, p: context.ai.clear_provider_key(p.provider_id)),
        'ai.providers.test': (Selected, lambda rid, p: context.ai.test(rid, p.provider_id)),
        'ai.translation.cache': (Cache, cached),
    }
