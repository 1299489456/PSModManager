"""Closed desktop commands for explicit Nexus operations."""
from typing import Literal
import webbrowser

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .nexus import GAME, SITE, MESSAGES, NexusError, link_url


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Settings(Empty):
    api_key: str = Field(min_length=1, max_length=4096)
    remember: StrictBool = True


class Browse(Empty):
    view: Literal['latest_updated', 'latest_added', 'downloads', 'endorsements', 'trending'] = 'latest_updated'
    offset: StrictInt = Field(default=0, ge=0, le=10000)
    query: StrictStr = Field(default='', max_length=100)


class Mod(Empty):
    mod_id: StrictInt = Field(ge=1, le=2**53-1)


class Download(Mod):
    file_id: StrictInt = Field(ge=1, le=2**53-1)
    nxm_url: str = Field(default='', max_length=4096)


class Open(Empty):
    page: Literal['game', 'keys', 'mod', 'files', 'link']
    mod_id: StrictInt | None = Field(default=None, ge=1, le=2**53-1)
    url: StrictStr | None = Field(default=None, max_length=2048)


def commands(context):
    def job(request_id, action):
        def worker(task):
            try:
                return {'ok': True, **action(task)}
            except NexusError as exc:
                return {'ok': False, 'code': exc.code, 'message': MESSAGES[exc.code]}
            except OSError:
                return {'ok': False, 'code': 'NEXUS_UNAVAILABLE', 'message': MESSAGES['NEXUS_UNAVAILABLE']}
        return context.jobs.start(request_id, worker)

    def open_page(_, payload):
        if payload.page == 'link':
            # A link the user clicked in a Mod description; http(s) only, opened by the system browser.
            url = link_url(payload.url)
        elif payload.page == 'keys':
            url = SITE + '/settings/api-keys'
        elif payload.page == 'game':
            url = SITE + '/games/' + GAME + '/mods'
        else:
            if not payload.mod_id:
                raise NexusError('NEXUS_URL_REJECTED')
            url = f'{SITE}/{GAME}/mods/{payload.mod_id}'
            if payload.page == 'files':
                url += '?tab=files'
        webbrowser.open(url)
        return {'opened': True}

    return {
        'nexus.settings.get': (Empty, lambda _, p: context.nexus.settings()),
        'nexus.settings.save': (Settings, lambda _, p: context.nexus.save(p.api_key, p.remember)),
        'nexus.settings.clear': (Empty, lambda _, p: context.nexus.clear()),
        'nexus.account': (Empty, lambda r, p: job(r, lambda task: {'account': context.nexus.account(task.check_cancelled)})),
        'nexus.browse': (Browse, lambda r, p: job(r, lambda task: context.nexus.browse(
            p.view, task.check_cancelled, offset=p.offset, query=p.query))),
        'nexus.mod': (Mod, lambda r, p: job(r, lambda task: context.nexus.mod(p.mod_id, task.check_cancelled))),
        'nexus.translate': (Mod, lambda r, p: context.ai.translate(
            r, lambda check: context.nexus.translation_source(p.mod_id, check), MESSAGES)),
        'nexus.files': (Mod, lambda r, p: job(r, lambda task: context.nexus.files(p.mod_id, task.check_cancelled))),
        'nexus.download': (Download, lambda r, p: job(r, lambda task: context.nexus.download(
            p.mod_id, p.file_id, p.nxm_url, check=task.check_cancelled, commit=task.commit,
            progress=lambda done, total: task.progress(done, total, 'download', 'NEXUS_DOWNLOADING')))),
        'nexus.open': (Open, open_page),
    }
