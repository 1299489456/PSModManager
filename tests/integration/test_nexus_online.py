"""Nexus contract tests: no real account, credential store or remote requests."""
from io import BytesIO
import json
from types import SimpleNamespace
import time
import threading
from urllib.error import HTTPError

import pytest

from backend.app.paths import AppPaths
from backend.online.nexus import (API, GRAPHQL, MAX_DOWNLOAD, NexusError, NexusService, description_text, download_url,
    image_url, link_url, nxm_authorization)


class Vault:
    def __init__(self): self.values = {}
    def get_password(self, service, account): return self.values.get((service, account))
    def set_password(self, service, account, key): self.values[service, account] = key
    def delete_password(self, service, account): self.values.pop((service, account), None)


class Response(BytesIO):
    def __init__(self, value, headers=None):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.headers = headers or {}


@pytest.fixture
def service(tmp_path):
    requests = []
    queue = []
    def opener(request, timeout):
        requests.append(request)
        value = queue.pop(0)
        if isinstance(value, Exception): raise value
        return value
    grants = []
    def grant(kind, path):
        grants.append((kind, path))
        return {'source_handle': '00000000-0000-4000-8000-000000000001', 'display_name': 'download.zip'}
    vault = Vault()
    revoked = []
    value = NexusService(AppPaths(tmp_path/'private'), SimpleNamespace(grant_download=grant, revoke_download=lambda handle:revoked.append(handle)), credentials_backend=vault, opener=opener)
    return SimpleNamespace(value=value, requests=requests, queue=queue, grants=grants, revoked=revoked, vault=vault)


def configured(service):
    service.value.save('owned-personal-test-key', False)


def test_key_save_is_local_and_never_in_settings_or_config(service):
    result=service.value.save('owned-secret', True)
    assert service.requests == []
    assert 'owned-secret' not in json.dumps(result)
    assert result['configured'] and result['key_status']=='saved'
    assert not service.value.paths.config.exists()
    assert service.value.clear()['configured'] is False


def test_browse_requires_key_and_does_not_use_public_or_foreign_credentials(service):
    with pytest.raises(NexusError, match='NEXUS_KEY_REQUIRED'): service.value.browse('trending')
    assert service.requests == []


def card(mod_id=81, **extra):
    return {'modId': mod_id, 'name': 'Test Mod', 'version': '1.2', 'summary': 'A mod', 'author': 'test', 'status': 'published',
            'uploader': {'name': 'uploader', 'avatar': 'https://avatars.nexusmods.com/7/100'},
            'thumbnailLargeUrl': 'https://images.nexusmods.com/mod-images/9791/t/large/81.png',
            'pictureUrl': 'https://staticdelivery.nexusmods.com/mods/9791/images/81/81.png',
            'downloads': 42, 'endorsements': 5, 'updatedAt': '2026-10-18T00:00:00Z', 'modCategory': {'name': 'Gameplay'}} | extra


def test_browse_identifies_our_application_and_fixed_game(service):
    configured(service)
    service.queue.append(Response({'data': {'mods': {'totalCount': 173, 'nodes': [card()]}}}))
    result=service.value.browse('latest_updated')
    request=service.requests[0]
    assert request.full_url==GRAPHQL and request.get_method()=='POST'
    assert request.get_header('Application-name')=='PSModManager'
    body=json.loads(request.data)
    assert body['variables']['filter']['gameDomainName']==[{'value':'probablystolen','op':'EQUALS'}]
    assert body['variables']['offset']==0 and body['variables']['count']==20
    assert body['variables']['sort']==[{'updatedAt':{'direction':'DESC'}}]
    assert result['total']==173 and result['mods'][0]['mod_id']==81
    assert result['mods'][0]['thumbnail'].startswith('https://images.nexusmods.com/')
    assert 'owned-personal-test-key' not in json.dumps(result)


def test_browse_pages_and_searches_the_whole_catalogue(service):
    configured(service)
    service.queue.append(Response({'data': {'mods': {'totalCount': 173, 'nodes': [card(90 + i) for i in range(20)]}}}))
    result=service.value.browse('downloads', offset=40, query='  storage ')
    variables=json.loads(service.requests[0].data)['variables']
    assert variables['offset']==40 and variables['filter']['name']==[{'value':'storage','op':'WILDCARD'}]
    assert variables['sort']==[{'downloads':{'direction':'DESC'}}]
    assert result['offset']==40 and result['query']=='storage' and len(result['mods'])==20
    for bad in [dict(offset=-1), dict(offset=10001), dict(query='x'*101)]:
        with pytest.raises(NexusError): service.value.browse('downloads', **bad)
    with pytest.raises(NexusError): service.value.browse('everything')


def test_browse_rejects_graphql_errors_and_hides_unpublished(service):
    configured(service)
    service.queue.append(Response({'errors': [{'message': 'private detail'}]}))
    with pytest.raises(NexusError, match='NEXUS_RESPONSE_INVALID'): service.value.browse('latest_added')
    service.queue.append(Response({'data': {'mods': {'totalCount': 2, 'nodes': [card(1), card(2, status='hidden')]}}}))
    assert [row['mod_id'] for row in service.value.browse('latest_added')['mods']]==[1]


def test_images_only_from_nexus_hosts_and_links_only_http():
    assert image_url('https://staticdelivery.nexusmods.com/a.png')
    for bad in ['http://staticdelivery.nexusmods.com/a.png', 'https://evil.test/a.png', 'https://images.nexusmods.com.evil.test/a.png',
                'https://user@images.nexusmods.com/a.png', None, 'javascript:alert(1)']:
        assert image_url(bad) is None
    assert link_url('https://github.com/owner/repo')
    for bad in ['javascript:alert(1)', 'file:///C:/x', 'https://user:pw@x.test/', 'nxm://probablystolen/mods/1', 'https://a b', '']:
        with pytest.raises(NexusError, match='NEXUS_URL_REJECTED'): link_url(bad)


def test_description_keeps_bbcode_but_drops_html():
    text, truncated = description_text('[b]Title[/b]\n<br />\n<br />Line &amp; more<script>x</script>&#92;path')
    assert text == '[b]Title[/b]\n\nLine & morex\\path' and not truncated
    assert description_text('a' * 70000)[1] is True


def test_mod_detail_maps_requirements_and_public_files(service):
    configured(service)
    service.queue.append(Response({'data': {'game': {'id': '9791'}}}))
    files=[{'fileId': 11, 'name': 'Main', 'version': '2', 'category': 'MAIN', 'categoryId': 1, 'sizeInBytes': '2048', 'detectedFileExtension': 'zip', 'primary': 1, 'date': 5},
           {'fileId': 12, 'name': 'Old', 'version': '1', 'category': 'ARCHIVED', 'categoryId': 7, 'sizeInBytes': '1', 'detectedFileExtension': 'zip', 'primary': 0, 'date': 1},
           {'fileId': 13, 'name': 'Packed', 'version': '2', 'category': 'OPTIONAL', 'categoryId': 3, 'sizeInBytes': '9', 'detectedFileExtension': '7z', 'primary': 0, 'date': 4}]
    detail=card(description='[b]Hi[/b]\n<br />Text', tags=[{'name': 'Quality of Life'}],
                modRequirements={'nexusRequirements': {'nodes': [{'modId': '5', 'modName': 'Core', 'url': 'https://www.nexusmods.com/probablystolen/mods/5', 'externalRequirement': False, 'notes': ''},
                                                                 {'modId': '0', 'modName': 'Bad', 'url': 'javascript:alert(1)', 'externalRequirement': True}]}})
    service.queue.append(Response({'data': {'mod': detail, 'modFiles': files}}))
    result=service.value.mod(81)
    assert json.loads(service.requests[1].data)['variables']=={'mod': '81', 'game': '9791', 'files': '81'}
    assert result['description']=='[b]Hi[/b]\nText' and result['tags']==['Quality of Life']
    assert [row['file_id'] for row in result['files']]==[11, 13]
    assert [row['supported'] for row in result['files']]==[True, False] and result['files'][0]['size_bytes']==2048
    assert result['requirements'][0]['mod_id']==5 and result['requirements'][1]['url'] is None
    # The game id is resolved once per session.
    service.queue.append(Response({'data': {'mod': detail, 'modFiles': []}}))
    assert service.value.translation_source(81)==[('summary', 'A mod'), ('description', '[b]Hi[/b]\nText')]
    assert len(service.requests)==3


def download_responses(service, premium=True, body=b'owned-download', length=None):
    service.queue.extend([
        Response({'user_id':7,'name':'Owned account','is_premium':premium}),
        Response({'file_id':11,'file_name':'TestMod.zip','version':'2','name':'Test Mod'}),
        Response([{'URI':'https://premium-files.nexus-cdn.com/owned/TestMod.zip?token=private-download-token'}]),
        Response(body, {'Content-Length':str(len(body) if length is None else length)}),
    ])


def test_premium_download_returns_only_granted_source_not_url_or_path_or_key(service):
    configured(service);download_responses(service)
    result=service.value.download(81,11)
    assert result['source_handle'] and result['kind']=='zip' and result['bytes']==14
    assert service.grants[0][0]=='zip'
    assert 'apikey' not in {key.lower() for key in service.requests[-1].headers}
    public=json.dumps(result)
    assert all(secret not in public for secret in ['private-download-token','owned-personal-test-key',str(service.value.paths.root)])


def test_free_account_needs_website_authorization(service):
    configured(service)
    service.queue.append(Response({'user_id':7,'name':'Owned account','is_premium':False}))
    with pytest.raises(NexusError,match='NEXUS_DOWNLOAD_AUTH'): service.value.download(81,11)
    assert len(service.requests)==1 and not service.grants


def test_free_authorized_nxm_is_bound_to_game_file_account_and_expiry(service):
    configured(service);download_responses(service,premium=False)
    url=f'nxm://probablystolen/mods/81/files/11?key=owned-token&expires={int(time.time()+300)}&user_id=7'
    result=service.value.download(81,11,url)
    assert result['source_handle']
    assert 'key=owned-token' in service.requests[2].full_url
    assert 'owned-token' not in json.dumps(result)


@pytest.mark.parametrize('url',[
    'http://premium-files.nexus-cdn.com/file.zip','https://127.0.0.1/file.zip',
    'https://nexusmods.com.evil.test/file.zip','https://user:secret@nexusmods.com/file.zip',
    'https://nexusmods.com:8080/file.zip','file:///C:/private',
])
def test_download_origin_allowlist(url):
    with pytest.raises(NexusError):download_url(url)


@pytest.mark.parametrize('change,code',[
    ('game','NEXUS_URL_REJECTED'),('file','NEXUS_URL_REJECTED'),
    ('expired','NEXUS_LINK_EXPIRED'),('account','NEXUS_ACCOUNT_MISMATCH'),
])
def test_nxm_rejects_unrelated_or_expired_authorizations(change,code):
    game='skyrim' if change=='game' else 'probablystolen'
    file=12 if change=='file' else 11
    expires=int(time.time()+(-10 if change=='expired' else 300))
    user=8 if change=='account' else 7
    with pytest.raises(NexusError,match=code):
        nxm_authorization(f'nxm://{game}/mods/81/files/{file}?key=owned&expires={expires}&user_id={user}',81,11,7)


@pytest.mark.parametrize('length,code',[(MAX_DOWNLOAD+1,'NEXUS_FILE_LIMIT'),(25,'NEXUS_DOWNLOAD_INCOMPLETE')])
def test_invalid_size_never_grants_or_leaves_partial_download(service,length,code):
    configured(service);download_responses(service,length=length)
    with pytest.raises(NexusError,match=code):service.value.download(81,11)
    assert service.grants==[]
    assert not list(service.value.paths.root.rglob('download.zip'))


def test_download_redirect_cannot_leave_nexus_or_forward_apikey(service):
    configured(service);download_responses(service)
    service.queue[-1]=HTTPError('https://premium-files.nexus-cdn.com/file',302,'redirect',{'Location':'http://localhost/private'},None)
    with pytest.raises(NexusError,match='NEXUS_URL_REJECTED'):service.value.download(81,11)
    assert len(service.requests)==4 and service.grants==[]


def test_api_rate_limit_is_public_code_without_exception_or_key(service):
    configured(service)
    service.queue.append(HTTPError(GRAPHQL,429,'private exception',{},None))
    with pytest.raises(NexusError,match='NEXUS_RATE_LIMIT'):service.value.browse('trending')


def test_close_reclaims_downloads_and_revokes_private_handles(service):
    configured(service);download_responses(service)
    result=service.value.download(81,11)
    assert list(service.value.paths.root.rglob('download.zip'))
    service.value.close()
    assert service.revoked==[result['source_handle']]
    assert not list(service.value.paths.root.rglob('download.zip'))


def test_cancel_between_grant_and_receipt_removes_original_and_handle(service):
    configured(service);download_responses(service)
    cancelled=threading.Event()
    original=service.value.imports.grant_download
    def grant(kind,path):
        result=original(kind,path);cancelled.set();return result
    service.value.imports.grant_download=grant
    def check():
        if cancelled.is_set():raise RuntimeError('owned-cancel')
    with pytest.raises(RuntimeError,match='owned-cancel'):
        service.value.download(81,11,check=check)
    assert len(service.revoked)==1
    assert not list(service.value.paths.root.rglob('download.zip'))


def test_stop_releases_job_waiting_for_connection_and_never_starts_next_request(service):
    from backend.runtime.jobs import JobManager
    from backend.online.api import commands, Browse
    started,release,finished=threading.Event(),threading.Event(),threading.Event()
    calls=[]
    def blocked(request,timeout):
        calls.append(request.full_url);started.set();release.wait(3)
        finished.set();return Response([])
    configured(service);service.value.opener=blocked
    jobs=JobManager(close_timeout=.5)
    context=SimpleNamespace(jobs=jobs,nexus=service.value)
    _,handler=commands(context)['nexus.browse']
    initial=handler('owned-job',Browse(view='trending'))
    assert started.wait(1)
    service.value.stop()
    before=time.monotonic()
    jobs.close()
    assert time.monotonic()-before<.5
    service.value.close();release.set();assert finished.wait(1)
    assert len(calls)==1
    with pytest.raises(NexusError,match='NEXUS_CLOSED'):service.value.account()


def test_key_button_opens_current_web_settings_not_retired_authenticated_endpoint(monkeypatch):
    from backend.online.api import commands, Open
    urls = []
    monkeypatch.setattr('backend.online.api.webbrowser.open', urls.append)
    _, handler = commands(SimpleNamespace())['nexus.open']
    assert handler('owned-login', Open(page='keys')) == {'opened': True}
    assert urls == ['https://www.nexusmods.com/settings/api-keys']
    assert 'users/myaccount' not in urls[0]


def test_description_links_open_only_http_urls(monkeypatch):
    from backend.online.api import commands, Open
    urls = []
    monkeypatch.setattr('backend.online.api.webbrowser.open', urls.append)
    _, handler = commands(SimpleNamespace())['nexus.open']
    handler('owned-link', Open(page='link', url='https://github.com/owner/repo'))
    assert urls == ['https://github.com/owner/repo']
    for url in ['file:///C:/Windows', 'javascript:alert(1)', None]:
        with pytest.raises(NexusError, match='NEXUS_URL_REJECTED'):
            handler('owned-link', Open(page='link', url=url))
    assert len(urls) == 1
