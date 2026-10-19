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
    file_id: StrictInt | None = Field(default=None, ge=1, le=2**53-1)


class Registration(Empty):
    replace: StrictBool = False


class Receipt(Empty):
    id: StrictStr = Field(min_length=1, max_length=64)


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
                if payload.file_id:
                    url += f'&file_id={payload.file_id}'
        return {'opened': bool(webbrowser.open(url))}

    def protocol(action, *args):
        from . import nxm_protocol
        try:
            return getattr(nxm_protocol, action)(context.paths.root, *args)
        except (ValueError, OSError):
            return {'ok': False, 'message': '无法更新官网链接接收设置，请检查程序位置与权限后重试。'}

    def inbox():
        return getattr(context, 'nxm_inbox', None)

    def choose_default(_, payload):
        from .nxm_protocol import open_protocol_settings
        try:
            return open_protocol_settings()
        except OSError:
            return {'ok': False, 'message': '无法打开默认应用设置，请在 Windows 设置中选择 nxm 链接的默认应用。'}

    def receive(task, receipt):
        from .nxm_protocol import parse_nxm
        if inbox() is None:
            raise NexusError('NEXUS_CLOSED')
        try:
            url = inbox().claim(receipt)
        except ValueError as exc:
            raise NexusError('NEXUS_LINK_EXPIRED' if getattr(exc, 'code', None) == 'NEXUS_LINK_EXPIRED'
                             else 'NEXUS_URL_REJECTED') from None
        success = False
        try:
            try:
                ids = parse_nxm(url)
            except ValueError as exc:
                raise NexusError('NEXUS_LINK_EXPIRED' if getattr(exc, 'code', None) == 'NEXUS_LINK_EXPIRED'
                                 else 'NEXUS_URL_REJECTED') from None
            result = context.nexus.download(ids['mod_id'], ids['file_id'], url,
                check=task.check_cancelled, commit=task.commit,
                progress=lambda done, total: task.progress(done, total, 'download', 'NEXUS_DOWNLOADING'))
            success = True
            return result
        finally:
            inbox().finish(receipt, success)

    def discard(_, payload):
        if inbox() is not None:
            inbox().discard(payload.id)
        return {'discarded': True}

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
            website_fallback=True,
            progress=lambda done, total: task.progress(done, total, 'download', 'NEXUS_DOWNLOADING')))),
        'nexus.protocol.status': (Empty, lambda _, p: protocol('protocol_status')),
        'nexus.protocol.register': (Registration, lambda _, p: protocol('register_protocol', p.replace)),
        'nexus.protocol.unregister': (Empty, lambda _, p: protocol('unregister_protocol')),
        'nexus.protocol.choose_default': (Empty, choose_default),
        'nexus.inbox.list': (Empty, lambda _, p: inbox().peek() if inbox() is not None else {'items': []}),
        'nexus.inbox.discard': (Receipt, discard),
        'nexus.inbox.download': (Receipt, lambda r, p: job(r, lambda task: receive(task, p.id))),
        'nexus.open': (Open, open_page),
    }
