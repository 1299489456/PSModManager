import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx

from backend.workshop import IMAGE_HOSTS, MESSAGES, WorkshopError, WorkshopService
from backend.workshop.local import read_status
from backend.workshop.remote import PublicWorkshopClient, normalize, parse_browse, parse_detail_page, safe_preview

ID = '3828868413'


class Response:
    def __init__(self, value='', status=200):
        self.value = value.encode() if isinstance(value, str) else value
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_bytes(self, size):
        yield self.value


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def remote_item(**changes):
    return dict(publishedfileid=ID, result=1, consumer_app_id=4348910, title='物品详情',
                description='真实公开条目测试文本', preview_url='https://images.steamusercontent.com/ugc/1/a/',
                creator='76561190000000000', subscriptions=120, file_size='720116', time_updated=123, **changes)


def response_for(item=None):
    return Response(json.dumps({'response': {'publishedfiledetails': [item or remote_item()]}}, ensure_ascii=False))


def browse_html(items=None, *, query='', page=1, sort='trend', app=4348910, total=31):
    row = dict(state={'data': {'eresult': 1, 'current_page': page, 'total_count': total, 'total_pages': 2,
                              'results': items if items is not None else [remote_item()],
                              'creator_player_link_details': [{'public_data': {'steamid': '76561190000000000', 'persona_name': '作者'}}]}},
               queryKey=['workshop_browse', dict(appid=app, page=page, search_text=query, browse_sort=sort)])
    data = {'renderContext': json.dumps({'queryData': {'queries': [row]}})}
    return '<script id="valve-ssr-data" type="application/json">' + json.dumps(data) + '</script>'


@pytest.fixture
def installed(tmp_path):
    game = tmp_path / 'Steam/steamapps/common/Probably Stolen'
    game.mkdir(parents=True)
    root = game.parent.parent / 'workshop'
    package = root / 'content/4348910' / ID
    package.mkdir(parents=True)
    (package / 'manifest.xml').write_text('<Manifest><ID>item_details</ID><Name>物品详情</Name><Author>作者</Author>'
                                        '<ModVersion>0.4.1</ModVersion><Assemblies><Assembly>ItemDetails.dll</Assembly></Assemblies></Manifest>', encoding='utf-8')
    (package / 'ItemDetails.dll').write_bytes(b'unit-test-path-presence-only')
    acf = root / 'appworkshop_4348910.acf'
    acf.write_text('''"AppWorkshop"
{
 "appid" "4348910"
 "NeedsDownload" "0"
 "WorkshopItemsInstalled"
 {
  "3828868413"
  {
   "size" "720116"
   "manifest" "100"
   "timeupdated" "123"
  }
 }
 "WorkshopItemDetails"
 {
  "3828868413"
  {
   "latest_manifest" "100"
   "subscribedby" "PRIVATE_ACCOUNT_VALUE"
  }
 }
}
''', encoding='utf-8')
    return dict(canonical_root=str(game), app_id='4348910'), root, package, acf


def test_status_reads_real_acf_structure_without_claiming_subscription(installed):
    installation, root, package, acf = installed
    result = read_status(installation)
    row = result['items'][0]
    assert row['workshop_id'] == ID and row['native_mod_id'] == 'item_details'
    assert row['installed'] and row['content_state'] == 'installed'
    assert row['subscription_state'] == 'unknown' and row['progress'] is None
    assert row['update_available'] is False
    assert 'PRIVATE_ACCOUNT_VALUE' not in json.dumps(result)
    assert 'subscribedby' not in json.dumps(result)
    # Refresh must read Steam's actual change, not keep a fake completed operation.
    acf.write_text(acf.read_text().replace('"latest_manifest" "100"', '"latest_manifest" "101"'))
    refreshed = read_status(installation)
    assert refreshed['items'][0]['download_state'] == 'update_available'
    assert refreshed['downloads'] == []
    (package / 'ItemDetails.dll').unlink()
    row = read_status(installation)['items'][0]
    assert not row['installed'] and row['missing_assemblies'] == ['ItemDetails.dll']


def test_directory_is_not_account_subscription(installed):
    installation, _, _, acf = installed
    acf.unlink()
    result = read_status(installation)
    assert result['items'][0]['installed']
    assert result['items'][0]['subscription_state'] == 'unknown'
    assert result['issues'] == ['WORKSHOP_ACF_MISSING']


@pytest.mark.parametrize('content', ['"AppWorkshop" { "appid" "7" }',
                                  '"AppWorkshop" { "appid" "4348910" "appid" "4348910" }',
                                  '"AppWorkshop" { "appid" "4348910"'])
def test_invalid_acf_does_not_destroy_local_inventory(installed, content):
    installation, _, _, acf = installed
    acf.write_text(content)
    result = read_status(installation)
    assert 'WORKSHOP_ACF_INVALID' in result['issues']
    assert result['items'][0]['installed']
    assert result['items'][0]['evidence']['acf_installed_record'] is False


@pytest.mark.parametrize('xml', ['<!DOCTYPE Manifest [<!ENTITY x SYSTEM "file:///secret">]><Manifest><ID>&x;</ID></Manifest>',
                              '<Manifest><ID>x</ID><Assemblies><Assembly>../outside.dll</Assembly></Assemblies></Manifest>',
                              '<Manifest><ID>x</ID><Assemblies><Assembly>C:/outside.dll</Assembly></Assemblies></Manifest>'])
def test_manifest_does_not_follow_external_entity_or_path(installed, xml):
    installation, _, package, _ = installed
    (package / 'manifest.xml').write_text(xml)
    row = read_status(installation)['items'][0]
    assert not row['installed'] and row['issues'] == ['WORKSHOP_MANIFEST_UNREADABLE']


def test_native_root_names_and_moved_game(tmp_path, installed):
    installation, _, package, _ = installed
    p = package / 'manifest.xml'
    p.write_text(p.read_text(encoding='utf-8').replace('Manifest', 'Mod'), encoding='utf-8')
    assert read_status(installation)['items'][0]['installed']
    assert read_status(dict(canonical_root=str(tmp_path / 'manual')))['issues'] == ['STEAM_LIBRARY_NOT_DETECTED']
    with pytest.raises(WorkshopError, match='4348910'):
        read_status(dict(canonical_root=str(tmp_path), app_id='4349200'))


def test_ssr_browse_maps_public_data_and_pagination():
    result = parse_browse(browse_html(query='物品'), query='物品', page=1, sort='hot')
    assert result['items'][0]['title'] == '物品详情'
    assert result['items'][0]['author'] == '作者'
    assert result['total'] == 31 and result['has_more']
    assert '76561190000000000' not in json.dumps(result)


@pytest.mark.parametrize('options', [dict(app=7), dict(page=2), dict(query='different'), dict(sort='mostrecent')])
def test_ssr_never_uses_an_unrelated_prefetched_query(options):
    with pytest.raises(WorkshopError) as error:
        parse_browse(browse_html(**options), query='', page=1, sort='hot')
    assert error.value.code == 'WORKSHOP_PAGE_CHANGED'


@pytest.mark.parametrize('url', ['http://images.steamusercontent.com/a', 'https://images.steamusercontent.com.evil.test/a',
                              'https://user@images.steamusercontent.com/a', 'https://127.0.0.1/a',
                              'file:///C:/secret', 'javascript:alert(1)', 'https://images.steamusercontent.com:444/a'])
def test_preview_url_whitelist(url):
    assert safe_preview(url) is None


def test_user_content_is_plain_text_and_wrong_app_rejected():
    row = remote_item()
    row.update(description='<script>steal()</script><b>正文</b>[url=https://evil.test]文字[/url]', title='<img onerror=alert(1)>标题')
    result = normalize(row, source='test')
    assert result['title'] == '标题' and result['description'] == '正文 文字'
    row['consumer_app_id'] = 294100
    with pytest.raises(WorkshopError) as error:
        normalize(row, source='test')
    assert error.value.code == 'WORKSHOP_WRONG_APP'


def test_details_page_required_ids_are_not_native_ids_or_external_urls():
    html = '<div class="workshopItemTitle">title</div><div class="friendBlockContent">Author<br>Offline</div>'
    html += '<div id="RequiredItems"><a href="https://steamcommunity.com/sharedfiles/filedetails/?id=3829024993">Need</a>'
    html += '<a href="https://evil.test/sharedfiles/filedetails/?id=3828949943">No</a></div>'
    result = parse_detail_page(html, ID)
    assert result == dict(author='Author', required_items=['3829024993'], children_kind='required_items', dependencies_observed=True)


@pytest.mark.parametrize('status,code,retry', [(403, 'WORKSHOP_ACCESS_DENIED', False), (401, 'WORKSHOP_ACCESS_DENIED', False),
                                            (429, 'WORKSHOP_RATE_LIMITED', True), (503, 'WORKSHOP_UNAVAILABLE', True),
                                            (302, 'WORKSHOP_HTTP_ERROR', False)])
def test_http_errors_do_not_follow_redirect_or_bypass_denial(status, code, retry):
    session = Session(Response(status=status))
    with pytest.raises(WorkshopError) as error:
        PublicWorkshopClient(session).query([ID])
    assert error.value.code == code and error.value.retryable is retry
    assert len(session.calls) == 1 and session.calls[0][2]['follow_redirects'] is False


def test_query_cache_stays_honestly_stale_and_local_is_refreshed(installed, tmp_path):
    installation, _, package, _ = installed
    session = Session(response_for(), httpx.ReadTimeout('test timeout'))
    service = WorkshopService(SimpleNamespace(root=tmp_path), session=session)
    assert service.query([ID], installation)['stale'] is False
    (package / 'ItemDetails.dll').unlink()
    result = service.query([ID], installation, refresh=True)
    assert result['stale']
    service._fetches[('query', ID)].exception(timeout=2)
    result = service.query([ID], installation)
    assert result['network_error']['retryable']
    assert not result['items'][0]['local']['installed']
    assert len(session.calls) == 2


def test_offline_local_remains_usable(installed, tmp_path):
    installation = installed[0]
    service = WorkshopService(SimpleNamespace(root=tmp_path), session=Session(httpx.ConnectError('test offline'), httpx.ConnectError('still offline')))
    result = service.query([ID], installation)
    assert result['source'] == 'local_manifest' and result['stale']
    mine = service.browse(installation, sort='mine')
    assert mine['items'][0]['local']['subscription_state'] == 'unknown'


def test_partial_detail_failure_does_not_fake_dependency_knowledge(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path), session=Session(response_for(), Response(status=403)))
    result = service.detail(ID)
    assert result['available'] and result['children_kind'] == 'unknown'
    service._fetches[('extras', ID)].exception(timeout=2)
    result = service.detail(ID)
    assert result['details_error']['code'] == 'WORKSHOP_ACCESS_DENIED'


@pytest.mark.parametrize('identity', ['../123', '123&url=bad', '0', '１２３', '18446744073709551616', 123])
def test_id_validation_before_any_handoff_or_network(tmp_path, identity):
    calls = []
    service = WorkshopService(SimpleNamespace(root=tmp_path), session=Session(), opener=calls.append)
    with pytest.raises(WorkshopError):
        service.open(identity, intent='subscribe')
    assert not calls


def test_official_handoff_never_claims_successful_subscription(tmp_path):
    calls = []
    service = WorkshopService(SimpleNamespace(root=tmp_path), opener=calls.append)
    result = service.open(ID, intent='unsubscribe')
    assert calls == ['steam://url/CommunityFilePage/' + ID]
    assert result['state'] == 'handoff_opened' and result['confirmation_required']
    assert result['subscription_state'] == 'unknown' and 'unsubscribed' not in result
    assert service.open(intent='workshop', target='browser')['url'] == 'https://steamcommunity.com/workshop/browse/?appid=4348910'
    assert not service.capabilities()['anonymous_download']['available']
    with pytest.raises(WorkshopError) as error:
        service.anonymous_download(ID)
    assert error.value.code == 'WORKSHOP_ANONYMOUS_UNAVAILABLE'


def test_failed_opener_does_not_claim_handoff(tmp_path):
    service = WorkshopService(SimpleNamespace(root=tmp_path), opener=lambda url: False)
    with pytest.raises(WorkshopError) as error:
        service.open(ID)
    assert error.value.code == 'WORKSHOP_OPEN_FAILED'


def test_safe_bridge_exports_cover_errors():
    assert MESSAGES['WORKSHOP_ACCESS_DENIED']
    assert MESSAGES['WORKSHOP_RESPONSE_LIMIT']
    assert 'images.steamusercontent.com' in IMAGE_HOSTS
    assert safe_preview('https://images.steamusercontent.com/ugc/1/a/')


@pytest.mark.parametrize('response,code', [
    (Response(b'x' * (4 * 1024 * 1024 + 1)), 'WORKSHOP_RESPONSE_LIMIT'),
    (Response(b'\xff\xfe'), 'WORKSHOP_RESPONSE_INVALID'),
    (Response('{"response":{}}'), 'WORKSHOP_RESPONSE_INVALID'),
])
def test_bounded_invalid_remote_content(response, code):
    with pytest.raises(WorkshopError) as error:
        PublicWorkshopClient(Session(response)).query([ID])
    assert error.value.code == code


def test_unavailable_item_retains_common_dto_fields():
    row = PublicWorkshopClient(Session(response_for(dict(publishedfileid=ID, result=9)))).query([ID])[0]
    assert row['available'] is False and row['error_code'] == 'WORKSHOP_ITEM_UNAVAILABLE'
    assert row['preview_url'] is None and row['required_items'] == [] and row['children_kind'] == 'unknown'


def test_page_depth_is_bounded():
    with pytest.raises(WorkshopError) as error:
        parse_detail_page('<div>' * 300, ID)
    assert error.value.code == 'WORKSHOP_PAGE_CHANGED'


def test_local_card_description_is_bounded(installed):
    installation, _, package, _ = installed
    manifest = package / 'manifest.xml'
    manifest.write_text(manifest.read_text(encoding='utf-8').replace('</Manifest>', '<Description>' + '长' * 2000 + '</Description></Manifest>'), encoding='utf-8')
    row = read_status(installation)['items'][0]
    assert len(row['description']) == 240 and row['description_truncated'] is True


def test_httpx_details_request_keeps_item_id():
    session = Session(Response('<div class="workshopItemTitle">title</div>'))
    PublicWorkshopClient(session).detail_extras(ID)
    method, url, kwargs = session.calls[0]
    with httpx.Client() as client:
        request = client.build_request(method, url, params=kwargs['params'])
    assert request.url.params['id'] == ID and request.url.params['l'] == 'english'


def test_app_download_flag_is_not_a_per_item_queue(installed):
    installation, _, package, acf = installed
    acf.write_text(acf.read_text().replace('"NeedsDownload" "0"', '"NeedsDownload" "1"'))
    (package / 'ItemDetails.dll').unlink()
    result = read_status(installation)
    assert result['steam_record']['needsdownload'] == 1
    assert result['items'][0]['download_state'] == 'unknown' and result['downloads'] == []


def test_native_metadata_preserves_game_id_and_assembly_whitespace(installed):
    installation, _, package, _ = installed
    manifest = package / 'manifest.xml'
    manifest.write_text('<Mod><ID>-example</ID><Assemblies><Assembly> ItemDetails.dll </Assembly></Assemblies></Mod>')
    row = read_status(installation)['items'][0]
    assert row['native_mod_id'] == '-example' and row['installed']
    manifest.write_text('<Mod><ID>example</ID><Assemblies /></Mod>')
    assert read_status(installation)['items'][0]['installed'] is False
