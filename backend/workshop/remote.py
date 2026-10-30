"""Bounded, unauthenticated reads of Valve's official public surfaces."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit, parse_qs

import httpx

from .errors import WorkshopError
from .local import APP_ID, item_id, number, text

COMMUNITY = 'https://steamcommunity.com'
DETAIL_API = 'https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/'
SORTS = {'hot': 'trend', 'recent': 'lastupdated', 'new': 'mostrecent'}
IMAGE_HOSTS = frozenset({'images.steamusercontent.com', 'steamuserimages-a.akamaihd.net',
                         'cdn.akamai.steamstatic.com', 'cdn.fastly.steamstatic.com',
                         'shared.akamai.steamstatic.com', 'shared.fastly.steamstatic.com',
                         'community.akamai.steamstatic.com', 'community.fastly.steamstatic.com'})

VOID_TAGS = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'))


class _PublicHTML(HTMLParser):
    """Extract text and official-page data without evaluating scripts or links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.words = []
        self.ssr = []
        self.title_found = False
        self.author = None
        self.required_links = []

    def handle_starttag(self, tag, attrs):
        if len(self.stack) >= 256:
            raise WorkshopError('WORKSHOP_PAGE_CHANGED', 'Steam 返回的页面嵌套过深，请打开官方页面核对。', retryable=True)
        attrs = dict(attrs)
        classes = attrs.get('class', '').split()
        flags = dict(tag=tag, hidden=tag in ('script', 'style', 'iframe', 'object'),
                     ssr=tag == 'script' and attrs.get('id') == 'valve-ssr-data' and attrs.get('type') == 'application/json',
                     author='friendBlockContent' in classes,
                     required=attrs.get('id') == 'RequiredItems' or 'requiredItemsContainer' in classes)
        if 'workshopItemTitle' in classes:
            self.title_found = True
        if tag == 'a' and any(node['required'] for node in self.stack) and attrs.get('href'):
            self.required_links.append(attrs['href'])
        if tag not in VOID_TAGS:
            self.stack.append(flags)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1]['ssr']:
            self.ssr.append(data)
        if any(node['hidden'] for node in self.stack):
            return
        value = data.strip()
        if value:
            self.words.append(value)
            if self.author is None and self.stack and self.stack[-1]['author']:
                self.author = value


def _html(value):
    parser = _PublicHTML()
    parser.feed(value)
    parser.close()
    return parser


def page_url(identity):
    return f'{COMMUNITY}/sharedfiles/filedetails/?id={item_id(identity)}'


def safe_preview(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value) or '\\' in value:
        return None
    try:
        url = urlsplit(value)
        if url.scheme == 'https' and url.hostname in IMAGE_HOSTS and url.port in (None, 443) and not url.username and not url.password:
            return value
    except ValueError:
        pass
    return None


def plain(value, limit):
    value = text(value, limit * 4)
    result = ' '.join(_html(value).words)
    # Descriptions may be Steam BBCode; retain text, never render its links or tags.
    result = re.sub(r'\[/?(?:url|img|h[1-6]|b|i|u|strike|list|olist|\*|quote|code|table|tr|td)(?:=[^\]]*)?\]', '', result, flags=re.I)
    return text(result, limit)


def normalize(raw, *, source, authors=None, description_limit=240):
    identity = item_id(str(raw.get('publishedfileid', '')))
    if str(raw.get('consumer_app_id', raw.get('consumer_appid', ''))) != APP_ID:
        raise WorkshopError('WORKSHOP_WRONG_APP', '工坊条目不属于 Probably Stolen 正式版。')
    description = raw.get('description', raw.get('short_description', ''))
    rating = raw.get('star_rating')
    rating = float(rating) if isinstance(rating, (float, int)) and 0 <= rating <= 5 else None
    tags = raw.get('tags', [])
    tags = [plain(v.get('tag', '') if isinstance(v, dict) else v, 64) for v in tags[:16]] if isinstance(tags, list) else []
    return dict(workshop_id=identity, app_id=APP_ID, native_mod_id=None, title=plain(raw.get('title', ''), 256),
                description=plain(description, description_limit), description_truncated=len(str(description)) > description_limit,
                description_scope='detail' if description_limit >= 8000 else 'summary',
                author=plain((authors or {}).get(str(raw.get('creator')), ''), 128) or None,
                preview_url=safe_preview(raw.get('preview_url')), page_url=page_url(identity),
                file_size=number(raw.get('file_size')), time_updated=number(raw.get('time_updated')),
                subscriptions=number(raw.get('subscriptions')), rating=rating, tags=[v for v in tags if v],
                required_items=[], children_kind='collection' if raw.get('file_type') == 2 else 'unknown',
                local=None, metadata_source=source, available=True)


def parse_browse(html, *, page, query, sort):
    """Read Valve SSR JSON as data; hashed CSS classes are deliberately not a contract."""
    try:
        root = json.loads(''.join(_html(html).ssr))
        context = root.get('renderContext', {})
        context = json.loads(context) if isinstance(context, str) else context
        data = context.get('queryData', {})
        data = json.loads(data) if isinstance(data, str) else data
        for entry in data.get('queries', []):
            key = entry.get('queryKey', [])
            if len(key) < 2 or key[0] != 'workshop_browse' or not isinstance(key[1], dict):
                continue
            params = key[1]
            if str(params.get('appid')) != APP_ID or params.get('page') != page or params.get('search_text', '') != query or params.get('browse_sort') != SORTS[sort]:
                continue
            result = entry.get('state', {}).get('data', {})
            if result.get('eresult') != 1 or not isinstance(result.get('results'), list):
                raise ValueError('unsuccessful_result')
            authors = {}
            for creator in result.get('creator_player_link_details', []):
                public = creator.get('public_data', {})
                authors[str(public.get('steamid'))] = public.get('persona_name', '')
            items = [normalize(row, source='steam_community', authors=authors) for row in result['results'][:30]]
            total = number(result.get('total_count'))
            pages = number(result.get('total_pages'))
            return dict(items=items, page=page, page_size=30, total=total,
                        has_more=page < pages if pages is not None else len(items) == 30, source='steam_community')
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        pass
    raise WorkshopError('WORKSHOP_PAGE_CHANGED', 'Steam 工坊页面结构变化或未返回所请求列表，请重试或打开官方页面。', retryable=True)


def parse_detail_page(html, identity):
    page = _html(html)
    if not page.title_found:
        raise WorkshopError('WORKSHOP_PAGE_CHANGED', 'Steam 未返回可识别的条目详情页。', retryable=True)
    author_name = plain(page.author, 128) or None
    required = []
    for link in page.required_links:
        try:
            url = urlsplit(link)
            candidate = parse_qs(url.query).get('id', [''])[0]
            if url.scheme == 'https' and url.hostname == 'steamcommunity.com' and not url.username and not url.password and url.port in (None, 443) and url.path == '/sharedfiles/filedetails/' and candidate != identity:
                candidate = item_id(candidate)
                if candidate not in required:
                    required.append(candidate)
        except (ValueError, WorkshopError):
            continue
    return dict(author=author_name, required_items=required[:128], children_kind='required_items', dependencies_observed=True)


class PublicWorkshopClient:
    def __init__(self, session=None):
        self.session = session or httpx.Client()

    def _read(self, method, url, **kwargs):
        # Endpoints come only from constants/generated IDs, never from item content.
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('steamcommunity.com', 'api.steampowered.com') or parsed.username or parsed.port not in (None, 443):
            raise WorkshopError('WORKSHOP_UNSAFE_URL', '拒绝访问非 Steam 官方地址。')
        try:
            with self.session.stream(method, url, timeout=httpx.Timeout(25, connect=8), follow_redirects=False,
                                     headers={'User-Agent': 'PSModManager Workshop', 'Accept-Language': 'zh-CN,en;q=0.8'}, **kwargs) as response:
                status = response.status_code
                if status in (401, 403):
                    raise WorkshopError('WORKSHOP_ACCESS_DENIED', 'Steam 拒绝公开访问，请使用官方页面；管理器不会绕过访问限制。')
                if status == 429:
                    raise WorkshopError('WORKSHOP_RATE_LIMITED', 'Steam 请求过于频繁，请稍后重试。', retryable=True)
                if status >= 500:
                    raise WorkshopError('WORKSHOP_UNAVAILABLE', 'Steam 暂时不可用，请稍后重试。', retryable=True)
                if status != 200:
                    raise WorkshopError('WORKSHOP_HTTP_ERROR', 'Steam 未返回所请求内容，请打开官方页面核对。', retryable=status == 408)
                chunks, size = [], 0
                for chunk in response.iter_bytes(65536):
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise WorkshopError('WORKSHOP_RESPONSE_LIMIT', 'Steam 返回内容超过读取上限。')
                    chunks.append(chunk)
                return b''.join(chunks).decode('utf-8-sig')
        except httpx.HTTPError:
            raise WorkshopError('WORKSHOP_NETWORK_ERROR', '无法连接 Steam。仍可查看本机模组，网络恢复后请重试。', retryable=True) from None
        except UnicodeError:
            raise WorkshopError('WORKSHOP_RESPONSE_INVALID', 'Steam 返回内容编码无效。', retryable=True) from None

    def browse(self, *, query, page, sort):
        html = self._read('GET', f'{COMMUNITY}/workshop/browse/', params=dict(appid=APP_ID, searchtext=query, p=page,
                          browsesort=SORTS[sort], actualsort=SORTS[sort], section='readytouseitems', numperpage=30, l='english'))
        return parse_browse(html, page=page, query=query, sort=sort)

    def query(self, ids):
        form = {'itemcount': len(ids), **{f'publishedfileids[{i}]': identity for i, identity in enumerate(ids)}}
        raw = self._read('POST', DETAIL_API, data=form)
        try:
            rows = json.loads(raw)['response']['publishedfiledetails']
            if not isinstance(rows, list):
                raise ValueError()
            values = {}
            for row in rows:
                identity = str(row.get('publishedfileid', ''))
                if identity not in ids or identity in values:
                    raise ValueError()
                if row.get('result') != 1:
                    values[identity] = normalize(dict(publishedfileid=identity, consumer_app_id=APP_ID), source='steam_public_api')
                    values[identity].update(available=False, error_code='WORKSHOP_ITEM_UNAVAILABLE')
                else:
                    values[identity] = normalize(row, source='steam_public_api', description_limit=8000 if len(ids) == 1 else 240)
            if set(values) != set(ids):
                raise ValueError()
            return [values[i] for i in ids]
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise WorkshopError('WORKSHOP_RESPONSE_INVALID', 'Steam 条目详情格式无效，请重试。', retryable=True) from None

    def detail_extras(self, identity):
        # httpx params replace a URL's existing query; keep the item ID explicit.
        return parse_detail_page(self._read('GET', f'{COMMUNITY}/sharedfiles/filedetails/',
                                            params={'id': item_id(identity), 'l': 'english'}), identity)

    def image(self, url):
        from .cache import MAX_IMAGE, image_type
        if not safe_preview(url):
            raise WorkshopError('WORKSHOP_UNSAFE_URL', '拒绝访问非 Steam 官方地址。')
        try:
            with self.session.stream('GET', url, timeout=httpx.Timeout(25, connect=8), follow_redirects=False) as response:
                if response.status_code != 200:
                    code = 'WORKSHOP_ACCESS_DENIED' if response.status_code in (401, 403) else 'WORKSHOP_HTTP_ERROR'
                    from .errors import MESSAGES
                    raise WorkshopError(code, MESSAGES[code], retryable=response.status_code >= 500)
                data = bytearray()
                for chunk in response.iter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE:
                        raise WorkshopError('WORKSHOP_RESPONSE_LIMIT', 'Steam 图片超过缓存大小限制。')
                if not image_type(data):
                    raise WorkshopError('WORKSHOP_RESPONSE_INVALID', 'Steam 未返回受支持的位图。')
                return bytes(data)
        except httpx.HTTPError:
            raise WorkshopError('WORKSHOP_NETWORK_ERROR', '无法连接 Steam 图片服务。', retryable=True) from None
