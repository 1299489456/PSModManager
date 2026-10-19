"""Real WebView online->native import->confirmed install using an owned Nexus fixture."""
import argparse
import ctypes
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
out = parser.parse_args().out.resolve()
assert any(out.is_relative_to(ROOT/'docs/development/evidence'/name) for name in
    ('20261016-mod-sharing-analysis-online', '20261016-followup-fixes', '20261016-garden-ui'))
out.mkdir(parents=True, exist_ok=False)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.games.probably_stolen import discovery
from backend.online import api as online_api
from backend.ai.credentials import CredentialStore
from tests.integration.harness.transaction_recovery import TransactionFixture, rpc
from tests.integration.test_content_catalog import compile_fixture
from tests.integration.harness.ai_provider import SyntheticAIProvider

fixture = TransactionFixture(out/'owned')
discovery.automatic_steam_roots = lambda: []
game, data, installation_id = fixture.game, fixture.data, fixture.installation
fixture.close()
dll = compile_fixture(out/'compiler', 'NexusOwnedMod')
archive = BytesIO()
with zipfile.ZipFile(archive, 'w') as stream:
    stream.writestr('NexusOwnedMod.dll', dll.read_bytes())
    stream.writestr('README.txt', b'Owned Nexus fixture documentation; do not deploy into game root.')
body = archive.getvalue()
body_bytes = body
report = dict(status='FAIL', checks=[], screenshots=[], requests=[], opened_pages=[], list_pages=[], scope='Actual WebView, synthetic game and Nexus transport; no real account/provider/network')
online_api.webbrowser.open = lambda url: report['opened_pages'].append(url) or True
windows = []
apis = []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start


class Vault:
    def get_password(self, *args): return None


class Response(BytesIO):
    def __init__(self, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        super().__init__(raw)
        self.headers = {'Content-Length': str(len(raw))}


NAMES = ['合成在线 Mod', 'AutoStorage Helper', 'Brewing Plus', 'Storage Colors'] + [f'Synthetic Mod {index:02d}' for index in range(5, 46)]
CATALOGUE = [{'modId': 81 if index == 0 else 100 + index, 'name': name, 'summary': ('Sorts loot into the right containers with one key. ' if index else '只测试在线浏览、下载和安全导入，不访问真实 Nexus。') + 'Works with saves.',
              'version': '2.0', 'author': '合成作者', 'uploader': {'name': 'owned-uploader', 'avatar': None}, 'status': 'published',
              'downloads': 5000 - index * 97, 'endorsements': 60 - index, 'createdAt': '2026-10-01T00:00:00Z', 'updatedAt': '2026-10-18T00:00:00Z',
              'modCategory': {'name': 'Gameplay' if index % 2 else 'Utilities'}} for index, name in enumerate(NAMES)]
DESCRIPTION = ('[size=5][b]Synthetic Helper[/b][/size]\n<br />\n<br />Tired of dragging loot into boxes one by one? Configure a container once.\n<br />'
               '[list]\n<br />[*][b]Right-click setup[/b]: open the settings panel.[/*]\n<br />[*]Press Ctrl+R to sort.[/*]\n<br />[/list]\n<br />'
               '[table][tr][th]Key[/th][th]Action[/th][/tr][tr][td]Ctrl+R[/td][td]Sort now[/td][/tr][/table]'
               '[url=https://example.invalid/guide]Read the guide[/url] &amp; enjoy.[img]https://example.invalid/shot.png[/img][script]alert(1)[/script]')


def graphql(request):
    body = json.loads(request.data)
    query, variables = body['query'], body['variables']
    if 'game(' in query:
        return Response({'data': {'game': {'id': '9791'}}})
    if 'mods(' in query:
        name = (variables['filter'].get('name') or [{}])[0].get('value', '').lower()
        rows = [row for row in CATALOGUE if name in row['name'].lower()]
        report['list_pages'].append({'offset': variables['offset'], 'query': name})
        page = rows[variables['offset']:variables['offset'] + variables['count']]
        return Response({'data': {'mods': {'totalCount': len(rows), 'nodes': page}}})
    if 'modFiles(' in query:
        mod = next(row for row in CATALOGUE if row['modId'] == variables['mod'])
        files = [{'fileId': 11, 'name': '合成主文件', 'version': '2.0', 'category': 'MAIN', 'categoryId': 1, 'sizeInBytes': str(len(body_bytes)), 'detectedFileExtension': 'zip', 'primary': 1, 'date': 1792000000},
                 {'fileId': 12, 'name': '合成可选包', 'version': '2.0', 'category': 'OPTIONAL', 'categoryId': 3, 'sizeInBytes': '9000', 'detectedFileExtension': '7z', 'primary': 0, 'date': 1791000000}]
        detail = mod | {'description': DESCRIPTION, 'tags': [{'name': 'Quality of Life'}, {'name': 'Utilities for Players'}],
                        'modRequirements': {'nexusRequirements': {'nodes': [{'modId': '0', 'modName': 'MelonLoader', 'url': 'https://melonwiki.xyz', 'externalRequirement': True, 'notes': '0.7.x'}]}}}
        return Response({'data': {'mod': detail, 'modFiles': files}})
    raise AssertionError('Unexpected GraphQL query')


def opener(request, timeout):
    url = request.full_url
    # Record only public endpoint identity; never API keys or signed URL queries.
    from urllib.parse import urlsplit
    endpoint = urlsplit(url).path
    report['requests'].append(endpoint)
    if endpoint == '/v2/graphql':
        assert request.get_header('Apikey') == 'owned-not-a-real-key'
        return graphql(request)
    if endpoint.endswith('/users/validate.json'):
        return Response({'user_id': 7, 'name': '本地合成账户', 'is_premium': True})
    if endpoint.endswith('/mods/latest_updated.json'):
        return Response([{'mod_id': 81, 'name': '合成在线 Mod', 'version': '2.0', 'summary': '只测试在线浏览、下载和安全导入，不访问真实 Nexus。', 'author': '合成作者'}])
    if endpoint.endswith('/files.json'):
        return Response({'files': [{'file_id': 11, 'name': '合成主文件', 'file_name': 'NexusOwnedMod.zip', 'version': '2.0', 'category_name': 'MAIN', 'size_in_bytes': len(body)}]})
    if endpoint.endswith('/files/11.json'):
        return Response({'file_id': 11, 'name': '合成主文件', 'file_name': 'NexusOwnedMod.zip', 'version': '2.0', 'size_in_bytes': len(body)})
    if endpoint.endswith('/download_link.json'):
        return Response([{'URI': 'https://premium-files.nexus-cdn.com/owned.zip'}])
    if endpoint.endswith('/owned.zip'):
        assert not any(key.lower() == 'apikey' for key in request.headers)
        return Response(body)
    raise AssertionError('Unexpected fixture endpoint')


def factory(context):
    context.nexus.credentials = CredentialStore(context.paths, Vault())
    context.nexus.opener = opener
    context.nexus.save('owned-not-a-real-key', False)
    api = original_api(context)
    rpc(api, 'installation.use', {'installation_id': installation_id})
    apis.append(api)
    return api


def wait(window, expression, timeout=60):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        if window.evaluate_js(expression): return
        time.sleep(.05)
    raise AssertionError('Timed out: '+expression)


def click(window, testid):
    selector=f"document.querySelector('[data-testid={testid}]')"
    wait(window, f'{selector} && !{selector}.disabled')
    window.evaluate_js(selector+'.click()')


def capture(window, name):
    time.sleep(.5)
    hwnd=window.native.Handle.ToInt64()
    left, top, right, bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd); source=win32ui.CreateDCFromHandle(dc)
    memory=source.CreateCompatibleDC(); bitmap=win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source,right-left,bottom-top); previous=memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/name)
        report['screenshots'].append(name)
    finally:
        memory.SelectObject(previous);win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def js(window, expression):
    return window.evaluate_js(expression)


def observe():
    window=windows[0]
    provider = SyntheticAIProvider(chunks=['[size=5][b]合成助手[/b][/size]\n厌倦了一件件把战利品拖进箱子？只需配置一次容器。'])
    provider.__enter__()
    try:
        window.events.loaded.wait()
        wait(window,"document.querySelector('[data-testid=nexus-open]')")
        click(window,'nexus-open')
        wait(window,"!document.querySelector('[data-testid=nexus-browse]').disabled")
        assert not report['requests'], 'Opening the page must not contact Nexus'
        report['checks'].append('Opening the Nexus page reads local settings without network')
        js(window,"document.querySelector('.account summary').click()")
        click(window,'nexus-get-key')
        wait(window,"!document.querySelector('[data-testid=nexus-get-key]').disabled")
        assert report['opened_pages']==['https://www.nexusmods.com/settings/api-keys']
        capture(window,'nexus-key-login-entry.png')
        js(window,"document.querySelector('.account summary').click()")
        report['checks'].append('Visible login/key button opens the current Nexus web settings page')
        click(window,'nexus-browse')
        wait(window,"document.querySelectorAll('[data-testid=nexus-card]').length===20")
        assert '共有 45 个 Mod' in js(window,"document.querySelector('[data-testid=nexus-dialog] .garden-intro p').innerText")
        capture(window,'online-mods.png')
        for expected in (40, 45):
            js(window,"document.querySelector('[data-testid=nexus-dialog] .grid-end').scrollIntoView({block:'end'})")
            wait(window,f"document.querySelectorAll('[data-testid=nexus-card]').length==={expected}")
        wait(window,"document.querySelector('[data-testid=nexus-dialog] .grid-end').innerText.includes('已显示全部 45 个 Mod')")
        offsets=[page['offset'] for page in report['list_pages']][-3:]
        assert offsets==[0,20,40], offsets
        report['checks'].append('Scrolling to the end loads pages of 20 until all 45 Mods are shown (offsets 0, 20, 40)')
        js(window,"(()=>{const e=document.querySelector('[data-testid=nexus-search]');e.value='storage';e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))})()")
        wait(window,"document.querySelectorAll('[data-testid=nexus-card]').length===2 && document.querySelector('[data-testid=nexus-dialog] .garden-intro p').innerText.includes('2 个 Mod')")
        capture(window,'online-search.png')
        js(window,"document.querySelector('[aria-label=清除搜索]').click()")
        wait(window,"document.querySelectorAll('[data-testid=nexus-card]').length===20")
        report['checks'].append('Search asks Nexus for matching names and clearing restores the full list')
        rpc(apis[0],'ai.settings.save',{'provider':'openai_compatible','base_url':provider.base_url,'model':'synthetic-translator','remember':False})
        js(window,"document.querySelector('[data-testid=nexus-card]').click()")
        wait(window,"document.querySelector('[data-testid=nexus-description]')?.innerText.includes('Tired of dragging loot')")
        text=js(window,"document.querySelector('[data-testid=nexus-description]').innerText")
        assert 'Ctrl+R' in text and '[b]' not in text and '[script]alert(1)[/script]' in text and not js(window,"Boolean(document.querySelector('[data-testid=nexus-description] script'))")
        assert js(window,"document.querySelector('[data-testid=nexus-description] table th').innerText")=='Key'
        assert js(window,"[...document.querySelectorAll('[data-testid=nexus-description] .bb-link')].map(a=>a.innerText).join('|')")=='Read the guide|图片：example.invalid'
        assert not js(window,"Boolean(document.querySelector('[data-testid=nexus-description] img'))"), 'images outside Nexus hosts must not load'
        wait(window,"document.querySelectorAll('[data-testid=nexus-files] .file-row').length===2")
        assert '手动下载' in js(window,"document.querySelectorAll('[data-testid=nexus-files] .file-row')[1].innerText")
        capture(window,'mod-detail.png')
        report['checks'].append('Card opens a detail page: BBCode rendered safely (table, list, links; unknown tags literal; foreign image as link), files with 7z marked manual')
        js(window,"document.querySelector('[data-testid=nexus-description] .bb-link').click()")
        assert report['opened_pages'][-1]=='https://example.invalid/guide'
        click(window,'nexus-translate')
        wait(window,"document.querySelector('[data-testid=nexus-description]')?.innerText.includes('只需配置一次容器') && !document.querySelector('.translate .working')")
        assert js(window,"document.querySelector('.translated-note')")
        sent=[row['body']['messages'] for row in provider.requests]
        assert sent and all(messages[0]['role']=='system' for messages in sent) and any('Tired of dragging loot' in messages[1]['content'] for messages in sent)
        capture(window,'mod-detail-translated.png')
        js(window,"[...document.querySelectorAll('.segmented button')].find(b=>b.innerText==='原文').click()")
        wait(window,"document.querySelector('[data-testid=nexus-description]').innerText.includes('Tired of dragging loot')")
        report['checks'].append('AI translation (localhost provider) replaces the description with the translation; 原文 switches back')
        click(window,'nexus-official-link')
        assert report['opened_pages'][-1]=='https://www.nexusmods.com/probablystolen/mods/81'
        report['checks'].append('The official page is offered once, at the bottom of the detail page')
        js(window,"document.querySelector('[data-testid=nexus-files] .file-row button.primary').click()")
        wait(window,"document.querySelector('[data-testid=import-preview]')")
        wait(window,"document.querySelector('[data-testid=import-preview] .name input').value === '合成在线 Mod'")
        capture(window,'download-import-preview.png')
        report['checks'].append('Download from the detail page opens the existing import preview on the Mods page')
        click(window,'import-commit')
        wait(window,"document.querySelector('[data-testid=import-result]')")
        click(window,'import-install-preview')
        wait(window,"document.querySelector('[data-testid=import-update-preview]')")
        capture(window,'confirm-install.png')
        click(window,'import-install-confirm')
        wait(window,"document.body.innerText.includes('已应用到游戏') || document.body.innerText.includes('已安装并启用')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==dll.read_bytes()
        assert not (game/'README.txt').exists()
        assert (game/'UserData/save-sentinel.bin').read_bytes()==b'isolated-save-never-deployed'
        assert (game/'Mods/foreign.json').read_bytes()==b'{"unknown":"keep"}'
        report['checks'].append('User-confirmed install writes only Mod DLL, keeps README in library and preserves unrelated files/save sentinel')
        report['status']='PASS'
    except Exception:
        report['error']=traceback.format_exc()
        try:
            report['jobs']=[{k: job.current.get(k) for k in ('state','stage','message_code','completed','total')} for job in apis[0]._context.jobs._jobs.values()][-6:]
            report['ai_requests']=len(provider.requests)
            report['translation_state']=js(window,"JSON.stringify([...document.querySelectorAll('.translate')].map(e=>e.innerText))")
        except Exception as exc: report['debug_error']=repr(exc)
        try: capture(window,'failure.png')
        except Exception: pass
    finally:
        provider.__exit__(None, None, None)
        window.destroy()


def create(title, **options):
    window=original_create(title, **options);windows.append(window);return window


composition.DesktopApi=factory
webview.create_window=create
webview.start=lambda *values,**options:original_start(observe,**options)
report['exit_code']=main.main(['--psmm-data-root',str(data)])
assert not list((data/'downloads/nexus').rglob('download.*')), 'Closing must reclaim private Nexus download originals'
report['checks'].append('Closing revokes download grants and reclaims originals while retaining installed files and library')
(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
raise SystemExit(0 if report['status']=='PASS' and report['exit_code']==0 else 1)
