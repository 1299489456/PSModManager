"""Steam Workshop bridge surface; URL and installation authority stay in host."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator

from .native_api import Payload, installation

WorkshopId = Annotated[str, StringConstraints(strict=True, pattern=r'^[0-9]{1,20}$')]


class OptionalInstallation(Payload):
    installation_id: UUID | None = None


class Status(Payload):
    installation_id: UUID
    workshop_id: WorkshopId | None = None
    refresh: StrictBool = False


class Browse(OptionalInstallation):
    query: str = Field(default='', max_length=200)
    page: StrictInt = Field(default=1, ge=1, le=1000)
    sort: Literal['hot', 'recent', 'new', 'mine'] = 'hot'
    page_size: StrictInt = 24
    refresh: StrictBool = False

    @field_validator('page_size')
    @classmethod
    def supported_page_size(cls, value):
        if value not in (12, 24, 48):
            raise ValueError('Unsupported page size')
        return value


class Detail(OptionalInstallation):
    workshop_id: WorkshopId
    refresh: StrictBool = False


class Query(OptionalInstallation):
    ids: list[WorkshopId] = Field(min_length=1, max_length=30)
    refresh: StrictBool = False


class Open(Payload):
    workshop_id: WorkshopId | None = None
    intent: Literal['detail', 'subscribe', 'unsubscribe', 'workshop'] = 'detail'
    target: Literal['steam', 'browser'] = 'steam'


class Translate(OptionalInstallation):
    workshop_id: WorkshopId
    force_refresh: StrictBool = False


class Image(Payload):
    workshop_id: WorkshopId
    refresh: StrictBool = False


class Action(Payload):
    installation_id: UUID
    action: Literal['subscribe', 'unsubscribe', 'update']
    ids: list[WorkshopId] = Field(min_length=1, max_length=100)


def commands(context):
    def selected(p):
        return installation(context, p.installation_id) if p.installation_id else None

    def translate(request_id, payload):
        from backend.ai.workshop_translation import translation_source
        from backend.workshop import MESSAGES
        value = selected(payload)

        def source(check):
            check()
            detail = context.workshop.translation_detail(payload.workshop_id, value, check=check)
            check()
            return translation_source(detail, check=check)

        return context.ai.translate(request_id, source, MESSAGES,
            cache_id=payload.workshop_id, force_refresh=payload.force_refresh)

    return {
        'workshop.status': (Status, lambda _, p: context.workshop.status(selected(p), workshop_id=p.workshop_id, refresh=p.refresh)),
        'workshop.browse': (Browse, lambda _, p: context.workshop.browse(selected(p), query=p.query,
            page=p.page, sort=p.sort, page_size=p.page_size, refresh=p.refresh)),
        'workshop.query': (Query, lambda _, p: context.workshop.query(p.ids, selected(p), refresh=p.refresh)),
        'workshop.detail': (Detail, lambda _, p: context.workshop.detail(p.workshop_id, selected(p), refresh=p.refresh)),
        'workshop.image': (Image, lambda _, p: context.workshop.image(p.workshop_id, refresh=p.refresh)),
        'workshop.open': (Open, lambda _, p: context.workshop.open(p.workshop_id, intent=p.intent, target=p.target)),
        'workshop.capabilities': (OptionalInstallation, lambda _, p: context.workshop.capabilities(selected(p))),
        'workshop.action': (Action, lambda _, p: context.workshop.action(selected(p), action=p.action, ids=p.ids)),
        'workshop.translate': (Translate, translate),
    }
