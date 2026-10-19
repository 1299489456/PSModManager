"""Nexus personal-use integration; fixed API origin and bounded private downloads."""
import html
import json
import re
import socket
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from backend.ai.credentials import CredentialStore
from backend.app.identity import APP_VERSION
from backend.app.paths import reject_reparse

GAME = 'probablystolen'
API = 'https://api.nexusmods.com/v1/'
GRAPHQL = 'https://api.nexusmods.com/v2/graphql'
SITE = 'https://www.nexusmods.com'
MAX_DOWNLOAD = 64 * 1024 * 1024
PAGE_SIZE = 20
IMAGE_HOSTS = ('staticdelivery.nexusmods.com', 'images.nexusmods.com', 'avatars.nexusmods.com')
# Sort orders offered by the list; Nexus' v1 lists stop at 10 entries, v2 pages through everything.
VIEWS = {'latest_updated': {'updatedAt': {'direction': 'DESC'}}, 'latest_added': {'createdAt': {'direction': 'DESC'}},
         'downloads': {'downloads': {'direction': 'DESC'}}, 'endorsements': {'endorsements': {'direction': 'DESC'}}}
_CARD = ('modId name summary version author uploader{name avatar} pictureUrl thumbnailUrl thumbnailLargeUrl '
         'downloads endorsements createdAt updatedAt adultContent status modCategory{name}')
_LIST = ('query($filter:ModsFilter,$sort:[ModsSort!],$offset:Int,$count:Int){mods(filter:$filter,sort:$sort,offset:$offset,count:$count)'
         '{totalCount nodes{' + _CARD + '}}}')
_DETAIL = ('query($mod:ID!,$game:ID!,$files:ID!){mod(modId:$mod,gameId:$game){' + _CARD + ' description fileSize '
           'tags{name} modRequirements{nexusRequirements{nodes{modId modName url externalRequirement notes}}}}'
           'modFiles(modId:$files,gameId:$game){fileId name version category categoryId sizeInBytes detectedFileExtension primary date description}}')
MESSAGES = {
    'NEXUS_KEY_REQUIRED': '先填写自己的 Nexus 个人 API Key，再加载在线列表。',
    'NEXUS_AUTH_FAILED': 'Nexus 未接受此 API Key，请重新填写。',
    'NEXUS_DOWNLOAD_AUTH': '普通账户请在官网使用 Manual download，下载后选择本地文件安装。Premium 账户可直接下载；只有官网提供管理器下载入口时才能使用 nxm 授权链接。',
    'NEXUS_RATE_LIMIT': 'Nexus 请求次数已达上限，请稍后重试。',
    'NEXUS_UNAVAILABLE': '暂时无法连接 Nexus，请检查网络后重试。',
    'NEXUS_RESPONSE_INVALID': 'Nexus 返回的数据格式不完整，请稍后重试。',
    'NEXUS_FILE_UNSUPPORTED': '此文件不是 ZIP 或 DLL；请在官网手动下载并解压，再导入目录。',
    'NEXUS_FILE_LIMIT': '此文件超过当前 64 MiB 下载/导入上限。',
    'NEXUS_DOWNLOAD_INCOMPLETE': '文件下载不完整，未交给导入器，请重试。',
    'NEXUS_URL_REJECTED': '下载链接不是本游戏的有效 Nexus 授权链接。',
    'NEXUS_LINK_EXPIRED': '管理器下载链接已过期，请在官网重新生成。',
    'NEXUS_ACCOUNT_MISMATCH': '此下载链接属于另一个 Nexus 账户。',
    'NEXUS_IO_FAILED': '无法写入管理器下载目录，请检查权限和可用空间。',
    'NEXUS_CLOSED': '在线连接已关闭，请重新打开管理器。',
}


class NexusError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def text(value, limit=200):
    return str(value or '')[:limit]


def count(value):
    return value if type(value) is int and 0 <= value < 2**53 else 0


def image_url(value):
    """Only Nexus' own image hosts; the page CSP allows exactly these."""
    if not isinstance(value, str) or len(value) > 1024:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.hostname not in IMAGE_HOSTS or parsed.username or parsed.port not in (None, 443):
        return None
    return value


def link_url(value):
    """External links from Mod pages open in the system browser: http(s) only, no credentials."""
    if not isinstance(value, str) or not 0 < len(value) <= 2048 or any(c.isspace() for c in value):
        raise NexusError('NEXUS_URL_REJECTED')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise NexusError('NEXUS_URL_REJECTED')
    return value


def description_text(raw, limit=60000):
    """Nexus descriptions are BBCode with stray HTML line breaks and entities; keep BBCode only."""
    value = str(raw or '').replace('\r\n', '\n')
    value = re.sub(r'\n?<br\s*/?>', '\n', value, flags=re.I)
    value = re.sub(r'<[^<>]{1,200}>', '', value)
    value = html.unescape(value)
    value = re.sub(r'\n{3,}', '\n\n', value).strip()
    return value[:limit], len(value) > limit


def positive(value):
    if type(value) is not int or not 0 < value < 2**53:
        raise NexusError('NEXUS_RESPONSE_INVALID')
    return value


def download_url(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith('.' + domain)
                       for domain in ('nexusmods.com', 'nexus-cdn.com'))):
        raise NexusError('NEXUS_URL_REJECTED')
    return value


def nxm_authorization(value, mod_id, file_id, user_id):
    parsed = urlsplit(value)
    expected = f'/mods/{mod_id}/files/{file_id}'
    if parsed.scheme != 'nxm' or parsed.netloc != GAME or parsed.path != expected or parsed.fragment:
        raise NexusError('NEXUS_URL_REJECTED')
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {'key', 'expires', 'user_id'} or any(len(v) != 1 for v in query.values()):
        raise NexusError('NEXUS_URL_REJECTED')
    key = query['key'][0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', key) or not query['expires'][0].isdigit():
        raise NexusError('NEXUS_URL_REJECTED')
    if int(query['expires'][0]) <= time.time():
        raise NexusError('NEXUS_LINK_EXPIRED')
    if query['user_id'][0] != str(user_id):
        raise NexusError('NEXUS_ACCOUNT_MISMATCH')
    return {'key': key, 'expires': query['expires'][0]}


class NexusService:
    def __init__(self, paths, imports, *, credentials_backend=None, opener=None):
        self.paths, self.imports = paths, imports
        self.credentials = CredentialStore(paths, credentials_backend)
        self.config = {'provider': 'nexus_personal', 'base_url': API}
        self.opener = opener or build_opener(NoRedirect()).open
        self._account = None
        self._game = None
        self._lock = threading.RLock()
        self._closed = False
        self._responses = set()
        self._downloads = {}

    def _check(self, check=lambda: None):
        check()
        if self._closed:
            raise NexusError('NEXUS_CLOSED')

    @staticmethod
    def _abort_response(response):
        # Interrupt an active socket read before closing its buffered stream.
        raw = getattr(getattr(response, 'fp', None), 'raw', None)
        transport = getattr(raw, '_sock', None)
        if transport is not None:
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        threading.Thread(target=response.close, daemon=True, name='PSMM-Nexus-close').start()

    def _io(self, action, check):
        """Wait cancellably for a single open/read; abandoned results never write files."""
        self._check(check)
        done, abandoned, result = threading.Event(), threading.Event(), []
        def work():
            try:
                value = action()
                result.append((True, value))
            except BaseException as exc:
                result.append((False, exc))
            finally:
                done.set()
            if (abandoned.is_set() or self._closed) and result[0][0] and hasattr(result[0][1], 'close'):
                self._abort_response(result[0][1])
        threading.Thread(target=work, daemon=True, name='PSMM-Nexus-io').start()
        try:
            while not done.wait(.04):
                self._check(check)
            self._check(check)
            success, value = result[0]
            if not success:
                raise value
            return value
        except BaseException:
            abandoned.set()
            if done.is_set() and result[0][0] and hasattr(result[0][1], 'close'):
                self._abort_response(result[0][1])
            raise

    def _open(self, request, check):
        response = self._io(lambda: self.opener(request, timeout=25), check)
        with self._lock:
            if self._closed:
                self._abort_response(response)
                raise NexusError('NEXUS_CLOSED')
            self._responses.add(response)
        return response

    def _release(self, response):
        with self._lock:
            self._responses.discard(response)
        self._abort_response(response)

    def settings(self):
        self._check()
        with self._lock:
            key, status = self.credentials.get(self.config)
            return {'configured': bool(key), 'key_status': status, 'game': GAME,
                    'account': self._account, 'mode': 'personal_use'}

    def save(self, api_key, remember):
        self._check()
        key = api_key.strip()
        if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise NexusError('NEXUS_AUTH_FAILED')
        with self._lock:
            notice = self.credentials.set(self.config, key, remember)
            self._account = None
        return {**self.settings(), 'notice': notice}

    def clear(self):
        self._check()
        with self._lock:
            try:
                self.credentials.clear(self.config)
            except Exception:
                raise NexusError('NEXUS_IO_FAILED') from None
            self._account = None
        return self.settings()

    def _json(self, path, check=lambda: None, *, body=None):
        self._check(check)
        key, _ = self.credentials.get(self.config)
        if not key:
            raise NexusError('NEXUS_KEY_REQUIRED')
        headers = {'apikey': key, 'Accept': 'application/json',
            'Application-Name': 'PSModManager', 'Application-Version': APP_VERSION,
            'User-Agent': 'PSModManager/' + APP_VERSION}
        if body is None:
            request = Request(API + path, headers=headers)
        else:
            request = Request(GRAPHQL, data=json.dumps(body).encode(), method='POST',
                              headers={**headers, 'Content-Type': 'application/json'})
        try:
            response = self._open(request, check)
            try:
                raw = self._io(lambda: response.read(4 * 1024 * 1024 + 1), check)
            finally:
                self._release(response)
            if len(raw) > 4 * 1024 * 1024:
                raise NexusError('NEXUS_RESPONSE_INVALID')
            return json.loads(raw)
        except HTTPError as exc:
            raise NexusError({401: 'NEXUS_AUTH_FAILED', 403: 'NEXUS_DOWNLOAD_AUTH',
                429: 'NEXUS_RATE_LIMIT'}.get(exc.code, 'NEXUS_UNAVAILABLE')) from None
        except (URLError, TimeoutError, OSError):
            raise NexusError('NEXUS_UNAVAILABLE') from None
        except (ValueError, UnicodeError):
            raise NexusError('NEXUS_RESPONSE_INVALID') from None

    def account(self, check=lambda: None):
        data = self._json('users/validate.json', check)
        if not isinstance(data, dict):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        result = {'user_id': positive(data.get('user_id')), 'name': text(data.get('name'), 100),
                  'premium': data.get('is_premium') is True}
        self._account = result
        return result

    def _graphql(self, query, variables, check):
        data = self._json('', check, body={'query': query, 'variables': variables})
        if not isinstance(data, dict) or not isinstance(data.get('data'), dict) or data.get('errors'):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        return data['data']

    @staticmethod
    def _card(item):
        if not isinstance(item, dict):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        mod_id = positive(item.get('modId'))
        uploader = item.get('uploader') if isinstance(item.get('uploader'), dict) else {}
        category = item.get('modCategory') if isinstance(item.get('modCategory'), dict) else {}
        return {'mod_id': mod_id, 'name': text(item.get('name'), 160), 'summary': text(item.get('summary'), 600),
                'version': text(item.get('version'), 80), 'author': text(item.get('author') or uploader.get('name'), 100),
                'uploader': text(uploader.get('name'), 100), 'avatar': image_url(uploader.get('avatar')),
                'thumbnail': image_url(item.get('thumbnailLargeUrl')) or image_url(item.get('thumbnailUrl')),
                'picture': image_url(item.get('pictureUrl')), 'category': text(category.get('name'), 80),
                'downloads': count(item.get('downloads')), 'endorsements': count(item.get('endorsements')),
                'created': text(item.get('createdAt'), 40), 'updated': text(item.get('updatedAt'), 40),
                'adult': item.get('adultContent') is True, 'url': f'{SITE}/{GAME}/mods/{mod_id}'}

    def browse(self, view, check=lambda: None, *, offset=0, query=''):
        if view == 'trending':
            view = 'endorsements'
        if view not in VIEWS or type(offset) is not int or not 0 <= offset <= 10000 or len(query) > 100:
            raise NexusError('NEXUS_RESPONSE_INVALID')
        query = query.strip()
        filters = {'gameDomainName': [{'value': GAME, 'op': 'EQUALS'}], 'adultContent': [{'value': False, 'op': 'EQUALS'}]}
        if query:
            filters['name'] = [{'value': query, 'op': 'WILDCARD'}]
        data = self._graphql(_LIST, {'filter': filters, 'sort': [VIEWS[view]], 'offset': offset, 'count': PAGE_SIZE}, check)
        page = data.get('mods')
        if not isinstance(page, dict) or not isinstance(page.get('nodes'), list):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        rows = [self._card(item) for item in page['nodes'][:PAGE_SIZE]
                if isinstance(item, dict) and item.get('status') in (None, 'published')]
        return {'mods': rows, 'total': count(page.get('totalCount')), 'offset': offset,
                'page_size': PAGE_SIZE, 'view': view, 'query': query, 'game': GAME}

    def _game_id(self, check):
        if self._game is None:
            data = self._graphql('query($d:String!){game(domainName:$d){id}}', {'d': GAME}, check)
            game = data.get('game')
            try:
                self._game = str(positive(int(str(game.get('id')))))
            except (AttributeError, ValueError):
                raise NexusError('NEXUS_RESPONSE_INVALID') from None
        return self._game

    def mod(self, mod_id, check=lambda: None):
        """Detail page: card fields, cleaned BBCode description, requirements and the public file list."""
        game = self._game_id(check)
        data = self._graphql(_DETAIL, {'mod': str(mod_id), 'game': game, 'files': str(mod_id)}, check)
        item = data.get('mod')
        if not isinstance(item, dict) or item.get('modId') != mod_id:
            raise NexusError('NEXUS_RESPONSE_INVALID')
        result = self._card(item)
        result['description'], result['description_truncated'] = description_text(item.get('description'))
        result['tags'] = [text(tag.get('name'), 60) for tag in (item.get('tags') or [])[:24] if isinstance(tag, dict) and tag.get('name')]
        nodes = (((item.get('modRequirements') or {}).get('nexusRequirements') or {}).get('nodes') or [])[:30]
        result['requirements'] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            url = node.get('url')
            try:
                url = link_url(url)
            except NexusError:
                url = None
            result['requirements'].append({'name': text(node.get('modName'), 160), 'url': url,
                'external': node.get('externalRequirement') is True, 'notes': text(node.get('notes'), 300),
                'mod_id': int(node['modId']) if str(node.get('modId') or '').isdigit() else None})
        files = data.get('modFiles') if isinstance(data.get('modFiles'), list) else []
        rows = []
        for file in files[:200]:
            if not isinstance(file, dict) or file.get('categoryId') in (6, 7):
                continue
            extension = text(file.get('detectedFileExtension'), 12).lower().lstrip('.')
            size = file.get('sizeInBytes')
            size = int(size) if isinstance(size, (int, str)) and str(size).isdigit() else None
            rows.append({'file_id': positive(file.get('fileId')), 'name': text(file.get('name'), 160),
                'version': text(file.get('version'), 80), 'category': text(file.get('category'), 40),
                'primary': bool(file.get('primary')), 'size_bytes': size, 'extension': extension,
                'uploaded': count(file.get('date')), 'description': text(file.get('description'), 400),
                # Unknown types may still be ZIP/DLL; the download step checks the real file name.
                'supported': extension in ('zip', 'dll', '')})
        order = {'MAIN': 0, 'UPDATE': 1, 'OPTIONAL': 2, 'MISCELLANEOUS': 3, 'OLD_VERSION': 4}
        rows.sort(key=lambda row: (not row['primary'], order.get(row['category'], 5), -row['uploaded']))
        result['files'] = rows[:40]
        return result

    def translation_source(self, mod_id, check=lambda: None):
        """Text the AI may translate: fetched here, never supplied by the page."""
        detail = self.mod(mod_id, check)
        return [('summary', detail['summary']), ('description', detail['description'])]

    def files(self, mod_id, check=lambda: None):
        data = self._json(f'games/{GAME}/mods/{mod_id}/files.json', check)
        if not isinstance(data, dict) or not isinstance(data.get('files'), list):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        rows = []
        for item in data['files'][:40]:
            if not isinstance(item, dict):
                raise NexusError('NEXUS_RESPONSE_INVALID')
            if item.get('category_id') in (6, 7):
                continue
            name = text(item.get('file_name'), 180)
            rows.append({'file_id': positive(item.get('file_id')), 'name': text(item.get('name'), 160),
                'filename': name, 'version': text(item.get('version'), 80),
                'category': text(item.get('category_name'), 60), 'size_bytes': item.get('size_in_bytes'),
                'supported': Path(name).suffix.lower() in ('.zip', '.dll')})
        return {'mod_id': mod_id, 'files': rows, 'truncated': len(data['files']) > 40}

    def download(self, mod_id, file_id, nxm_url='', *, check=lambda: None, progress=lambda *a: None, commit=None, website_fallback=False):
        self._check(check)
        account = self.account(check)
        params = nxm_authorization(nxm_url, mod_id, file_id, account['user_id']) if nxm_url else {}
        if not account['premium'] and not params:
            if website_fallback:
                return {'requires_website': True, 'mod_id': mod_id, 'file_id': file_id}
            raise NexusError('NEXUS_DOWNLOAD_AUTH')
        info = self._json(f'games/{GAME}/mods/{mod_id}/files/{file_id}.json', check)
        if not isinstance(info, dict):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        extension = Path(text(info.get('file_name'))).suffix.lower()
        if extension not in ('.zip', '.dll'):
            raise NexusError('NEXUS_FILE_UNSUPPORTED')
        if type(info.get('size_in_bytes')) is int and info['size_in_bytes'] > MAX_DOWNLOAD:
            raise NexusError('NEXUS_FILE_LIMIT')
        suffix = '?' + urlencode(params) if params else ''
        links = self._json(f'games/{GAME}/mods/{mod_id}/files/{file_id}/download_link.json' + suffix, check)
        if not isinstance(links, list) or not links or not isinstance(links[0].get('URI'), str):
            raise NexusError('NEXUS_RESPONSE_INVALID')
        url = download_url(links[0]['URI'])
        folder = self.paths.root / 'downloads' / 'nexus' / uuid4().hex
        reject_reparse(folder)
        folder.mkdir(parents=True, exist_ok=False)
        target = folder / ('download' + extension)
        grant = None
        try:
            self._check(check)
            response = None
            for _ in range(5):
                try:
                    response = self._open(Request(url, headers={'User-Agent': 'PSModManager/' + APP_VERSION}), check)
                    break
                except HTTPError as exc:
                    if exc.code in (301, 302, 303, 307, 308) and exc.headers.get('Location'):
                        url = download_url(urljoin(url, exc.headers['Location']))
                    else:
                        raise NexusError('NEXUS_UNAVAILABLE') from None
            if response is None:
                raise NexusError('NEXUS_URL_REJECTED')
            try:
                header = response.headers.get('Content-Length')
                expected = int(header) if header is not None else None
                if expected is not None and not 0 <= expected <= MAX_DOWNLOAD:
                    raise NexusError('NEXUS_FILE_LIMIT')
                received = 0
                with target.open('xb') as output:
                    while True:
                        self._check(check)
                        chunk = self._io(lambda: response.read(128 * 1024), check)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > MAX_DOWNLOAD:
                            raise NexusError('NEXUS_FILE_LIMIT')
                        output.write(chunk)
                        progress(received, expected if expected is None or received <= expected else None)
            finally:
                self._release(response)
            if received == 0 or (expected is not None and received != expected):
                raise NexusError('NEXUS_DOWNLOAD_INCOMPLETE')
            grant = self.imports.grant_download(extension[1:], str(target))
            self._check(check)
            if commit is not None:
                commit(lambda: None)
            self._downloads[target] = grant['source_handle']
            return {**grant, 'mod_id': mod_id, 'file_id': file_id,
                    'kind': extension[1:],
                    'display_name': text(info.get('name'), 160), 'version_raw': text(info.get('version'), 80),
                    'source': 'nexus', 'bytes': received}
        except BaseException:
            if grant:
                self.imports.revoke_download(grant['source_handle'])
            if target.is_file():
                target.unlink()
            folder.rmdir()
            raise

    def stop(self):
        with self._lock:
            self._closed = True
            responses = list(self._responses)
        for response in responses:
            self._abort_response(response)

    def close(self):
        self.stop()
        for target, handle in list(self._downloads.items()):
            self.imports.revoke_download(handle)
            reject_reparse(target)
            if target.is_file():
                target.unlink()
            if target.parent.is_dir():
                target.parent.rmdir()
            self._downloads.pop(target)
        self.credentials.close()
