"""Real WebView online->native import->confirmed install using an owned Nexus fixture."""
import argparse
import ctypes
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--nxm-flow', action='store_true')
options = parser.parse_args()
out = options.out.resolve()
assert any(out.is_relative_to(ROOT/'docs/development/evidence'/name) for name in
    ('20261016-mod-sharing-analysis-online', '20261016-followup-fixes', '20261016-garden-ui', '20261018-ui-controls', '20261018-nxm-downloads'))
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
from backend.online import nxm_protocol
from backend.ai.credentials import CredentialStore
from tests.integration.harness.transaction_recovery import TransactionFixture, rpc
from tests.integration.test_content_catalog import compile_fixture
from tests.integration.test_metadata_worker import BUILD
from tests.integration.harness.ai_provider import SyntheticAIProvider

fixture = TransactionFixture(out/'owned')
discovery.automatic_steam_roots = lambda: []
game, data, installation_id = fixture.game, fixture.data, fixture.installation
fixture.close()
def release(version):
    folder = out/'compiler'/version
    dll = compile_fixture(folder, 'NexusOwnedMod', version)
    source = folder/'Fixture.cs'
    source.write_text(source.read_text(encoding='utf-8').replace('"0.46D"', f'"{version}"'), encoding='utf-8')
    BUILD.compile_source(source, dll, library=True)
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w') as stream:
        stream.writestr('NexusOwnedMod.dll', dll.read_bytes())
        stream.writestr('README.txt', b'Owned Nexus fixture documentation; do not deploy into game root.')
    return dll, archive.getvalue()


dll, body = release('2.0.0.0')
old_dll, old_body = release('1.0.0.0')
body_bytes = body
report = dict(status='FAIL', checks=[], screenshots=[], requests=[], opened_pages=[], list_pages=[], scope='Actual WebView, synthetic game and Nexus transport; no real account/provider/network')
online_api.webbrowser.open = lambda url: report['opened_pages'].append(url) or True
windows = []
apis = []
original_api, original_create, original_start = composition.DesktopApi, webview.create_window, webview.start
fail_next_account = False
if options.nxm_flow:
    protocol_state = dict(available=True, owned=False, registered=False, conflict=True)
    nxm_protocol.protocol_status = lambda root: dict(protocol_state)
    def register(root, replace=False):
        if replace:
            protocol_state.update(owned=True, registered=True, conflict=False)
        return dict(protocol_state)
    nxm_protocol.register_protocol = register


class Vault:
    def get_password(self, *args): return None


class Response(BytesIO):
    def __init__(self, value):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        super().__init__(raw)
        self.headers = {'Content-Length': str(len(raw))}


NAMES = ['合成在线 Mod', 'AutoStorage Helper', 'Brewing Plus', 'Storage Colors'] + [f'Synthetic Mod {index:02d}' for index in range(5, 46)]
CATALOGUE = [{'modId': 81 if index == 0 else 100 + index, 'name': name, 'summary': ('Sorts loot into the right containers with one key. ' if index else '**合成在线测试**：[使用指南](https://example.invalid/summary)。') + 'Works with saves.',
              'version': '2.0', 'author': '合成作者', 'uploader': {'name': 'owned-uploader', 'avatar': None}, 'status': 'published',
              'downloads': 5000 - index * 97, 'endorsements': 60 - index, 'createdAt': '2026-10-01T00:00:00Z', 'updatedAt': '2026-10-18T00:00:00Z',
              'modCategory': {'name': 'Gameplay' if index % 2 else 'Utilities'}} for index, name in enumerate(NAMES)]
DESCRIPTION = ('[size=5][b]Synthetic Helper[/b][/size]\n<br />\n<br />Tired of dragging loot into boxes one by one? Configure a container once.\n<br />'
               '[list]\n<br />[*][b]Right-click setup[/b]: open the settings panel.[/*]\n<br />[*]Press Ctrl+R to sort.[/*]\n<br />[/list]\n<br />'
               '[table][tr][th]Key[/th][th]Action[/th][/tr][tr][td]Ctrl+R[/td][td]Sort now[/td][/tr][/table]'
               '[url=https://example.invalid/guide]Read the guide[/url] &amp; enjoy.[img]https://example.invalid/shot.png[/img][script]alert(1)[/script]\n\n'
               '## Markdown instructions\n\n- First step\n- Second step\n\n'
               '| Setting | Value |\n| --- | --- |\n| Mode | Safe |\n\n'
               '```json\n{"enabled": true}\n```\n\n'
               '[Markdown guide](https://example.invalid/markdown)\n\n'
               '[unsafe](javascript:alert(1)) <script>window.__unsafeMarkdown=1</script>')


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
        mod = next(row for row in CATALOGUE if row['modId'] == int(variables['mod']))
        files = [{'fileId': 11, 'name': '合成主文件', 'version': '2.0', 'category': 'MAIN', 'categoryId': 1, 'sizeInBytes': str(len(body_bytes)), 'detectedFileExtension': 'zip', 'primary': 1, 'date': 1792000000},
                 {'fileId': 12, 'name': '合成可选包', 'version': '2.0', 'category': 'OPTIONAL', 'categoryId': 3, 'sizeInBytes': '9000', 'detectedFileExtension': '7z', 'primary': 0, 'date': 1791000000},
                 {'fileId': 13, 'name': '合成旧版本', 'version': '1.0', 'category': 'OLD_VERSION', 'categoryId': 4, 'sizeInBytes': str(len(old_body)), 'detectedFileExtension': 'zip', 'primary': 0, 'date': 1790000000, 'description': '**兼容旧配置**\n\n- 保留原有选项'}]
        detail = mod | {'description': DESCRIPTION, 'tags': [{'name': 'Quality of Life'}, {'name': 'Utilities for Players'}],
                        'modRequirements': {'nexusRequirements': {'nodes': [{'modId': '0', 'modName': 'MelonLoader', 'url': 'https://melonwiki.xyz', 'externalRequirement': True, 'notes': '0.7.x'}]}}}
        return Response({'data': {'mod': detail, 'modFiles': files}})
    raise AssertionError('Unexpected GraphQL query')


def opener(request, timeout):
    global fail_next_account
    url = request.full_url
    # Record only public endpoint identity; never API keys or signed URL queries.
    from urllib.parse import urlsplit
    endpoint = urlsplit(url).path
    report['requests'].append(endpoint)
    if endpoint == '/v2/graphql':
        assert request.get_header('Apikey') == 'owned-not-a-real-key'
        return graphql(request)
    if endpoint.endswith('/users/validate.json'):
        if fail_next_account:
            fail_next_account = False
            raise OSError('synthetic offline')
        return Response({'user_id': 7, 'name': '本地合成账户', 'is_premium': not options.nxm_flow})
    if endpoint.endswith('/mods/latest_updated.json'):
        return Response([{'mod_id': 81, 'name': '合成在线 Mod', 'version': '2.0', 'summary': '只测试在线浏览、下载和安全导入，不访问真实 Nexus。', 'author': '合成作者'}])
    if endpoint.endswith('/files.json'):
        return Response({'files': [{'file_id': 11, 'name': '合成主文件', 'file_name': 'NexusOwnedMod.zip', 'version': '2.0', 'category_name': 'MAIN', 'size_in_bytes': len(body)}]})
    if endpoint.endswith(('/files/11.json', '/files/13.json')):
        old = endpoint.endswith('/files/13.json')
        return Response({'file_id': 13 if old else 11, 'name': '合成旧版本' if old else '合成主文件', 'file_name': 'NexusOwnedMod.zip', 'version': '1.0' if old else '2.0', 'size_in_bytes': len(old_body if old else body)})
    if endpoint.endswith('/download_link.json'):
        filename = 'owned-13.zip' if '/files/13/' in endpoint else 'owned.zip'
        return Response([{'URI': 'https://premium-files.nexus-cdn.com/' + filename}])
    if endpoint.endswith(('/owned.zip', '/owned-13.zip')):
        assert not any(key.lower() == 'apikey' for key in request.headers)
        return Response(old_body if endpoint.endswith('/owned-13.zip') else body)
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


def accept_first_run(window, timeout=20):
    """New data roots open on the storage choice; keep the default location."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = window.evaluate_js("document.querySelector('[data-testid=storage-default]') ? 'first' : !document.querySelector('[data-testid=storage-first-run]') && document.querySelector('[data-testid=mod-workspace]')?.offsetParent ? 'ready' : ''")
        if state == 'first':
            window.evaluate_js("document.querySelector('[data-testid=storage-default]').click()")
        elif state == 'ready':
            return
        time.sleep(.1)
    raise AssertionError('Workspace did not open after the storage choice')


def observe():
    window=windows[0]
    provider = SyntheticAIProvider(chunks=['## 合成助手\n\n只需配置一次容器。\n\n- 自动整理\n- [b]保留配置[/b]\n\n`Ctrl+R`'])
    provider.__enter__()
    try:
        window.events.loaded.wait()
        accept_first_run(window)
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
        wait(window,"document.querySelectorAll('[data-testid=nexus-card]').length>=20")
        js(window,"document.querySelector('[data-testid=nexus-dialog]').scrollTop=0")
        report['checks'].append('Search asks Nexus for matching names and clearing restores the full list')
        assert js(window,"document.querySelector('[data-testid=nexus-card] .summary strong').innerText")=='合成在线测试'
        js(window,"document.querySelector('[data-testid=nexus-card] .summary a').click()")
        assert not js(window,"Boolean(document.querySelector('[data-testid=nexus-detail]'))")
        assert report['opened_pages'][-1]=='https://example.invalid/summary'
        report['checks'].append('Card summary renders Markdown and its link opens safely without opening Mod details')
        click(window,'nexus-install-latest')
        wait(window,"document.querySelector('[data-testid=import-installed]') || document.querySelector('[data-testid=import-preview]') && !document.querySelector('[data-testid=import-progress]')")
        assert js(window,"Boolean(document.querySelector('[data-testid=import-installed]'))"), js(window,"document.querySelector('[data-testid=native-import-zone] dialog').innerText")
        assert '/v1/games/probablystolen/mods/81/files/11.json' in report['requests']
        assert not js(window,"document.querySelector('[data-testid=nexus-versions]').open")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==dll.read_bytes()
        assert not (game/'README.txt').exists()
        capture(window,'nexus-card-installed.png')
        report['checks'].append('Card main install button downloads latest main file 11 and directly installs/enables a new safe package, with actual synthetic DLL bytes verified')
        js(window,"[...document.querySelectorAll('[data-testid=native-import-zone] dialog footer button')].find(b=>b.innerText==='关闭').click()")
        click(window,'nexus-open')
        js(window,"document.querySelector('[data-testid=nexus-card] .install-arrow').click()")
        wait(window,"document.querySelector('[data-testid=nexus-choose-version]')")
        wait(window,"(()=>{const r=document.querySelector('[data-testid=nexus-choose-version]').getBoundingClientRect(),p=document.querySelector('[data-testid=nexus-dialog]').getBoundingClientRect();return r.top>=Math.max(p.top,0) && r.bottom<=Math.min(p.bottom,innerHeight)})()")
        capture(window,'nexus-install-menu.png')
        click(window,'nexus-choose-version')
        wait(window,"document.querySelector('[data-testid=nexus-versions]').open && document.querySelectorAll('[data-testid=nexus-versions] .file-row').length===3")
        assert not js(window,"Boolean(document.querySelector('[data-testid=nexus-detail]'))")
        assert js(window,"document.querySelectorAll('[data-testid=nexus-versions] .file-row')[1].querySelector('button').disabled")
        old_text=js(window,"document.querySelectorAll('[data-testid=nexus-versions] .file-row')[2].innerText")
        assert 'v1.0' in old_text and '旧版本' in old_text and 'KB' in old_text and '兼容旧配置' in old_text
        assert js(window,"Boolean(document.querySelector('[data-testid=nexus-versions] .file-description strong'))")
        capture(window,'nexus-version-picker.png')
        js(window,"document.querySelectorAll('[data-testid=nexus-versions] .file-row')[2].querySelector('button').click()")
        wait(window,"document.querySelector('[data-testid=import-update-preview]') && !document.querySelector('[data-testid=import-progress]')")
        assert '/v1/games/probablystolen/mods/81/files/13.json' in report['requests']
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==dll.read_bytes(), 'Selecting an older version must not bypass replacement confirmation'
        assert '降级' in js(window,"document.querySelector('[data-testid=import-update-preview]').innerText")
        capture(window,'nexus-downgrade-confirmation.png')
        report['checks'].append('Split menu lists versions, metadata and Markdown notes; unsupported files disabled; old file 13 requires explicit downgrade confirmation and keeps current DLL untouched')
        click(window,'import-install-confirm')
        wait(window,"document.querySelector('[data-testid=import-installed]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==old_dll.read_bytes()
        report['checks'].append('Confirming the explicitly selected older version installs its real synthetic v1 DLL')
        js(window,"[...document.querySelectorAll('[data-testid=native-import-zone] dialog footer button')].find(b=>b.innerText==='关闭').click()")
        click(window,'nexus-open')
        rpc(apis[0],'ai.settings.save',{'provider':'openai_compatible','base_url':provider.base_url,'model':'synthetic-translator','remember':False})
        js(window,"document.querySelector('[data-testid=nexus-card]').click()")
        wait(window,"document.querySelector('[data-testid=nexus-description]')?.innerText.includes('Tired of dragging loot')")
        text=js(window,"document.querySelector('[data-testid=nexus-description]').innerText")
        assert 'Ctrl+R' in text and '[b]' not in text and '[script]alert(1)[/script]' in text and not js(window,"Boolean(document.querySelector('[data-testid=nexus-description] script'))")
        assert js(window,"document.querySelector('[data-testid=nexus-description] table th').innerText")=='Key'
        links=js(window,"[...document.querySelectorAll('[data-testid=nexus-description] a')].map(a=>a.innerText)")
        assert all(label in links for label in ('Read the guide', '图片：example.invalid', 'Markdown guide')), links
        assert js(window,"document.querySelector('[data-testid=nexus-description] h2').innerText")=='Markdown instructions'
        assert js(window,"document.querySelectorAll('[data-testid=nexus-description] table').length") == 2
        assert 'enabled' in js(window,"document.querySelector('[data-testid=nexus-description] pre code').innerText")
        assert 'First step' in js(window,"document.querySelector('[data-testid=nexus-description]').innerText")
        assert not js(window,"window.__unsafeMarkdown || Boolean(document.querySelector('[data-testid=nexus-description] a[href^=\"javascript:\"]'))")
        assert not js(window,"Boolean(document.querySelector('[data-testid=nexus-description] img'))"), 'images outside Nexus hosts must not load'
        wait(window,"document.querySelectorAll('[data-testid=nexus-files] .file-row').length===3")
        assert '手动下载' in js(window,"document.querySelectorAll('[data-testid=nexus-files] .file-row')[1].innerText")
        capture(window,'mod-detail.png')
        js(window,"document.querySelector('[data-testid=nexus-description] h2').scrollIntoView({block:'start'})")
        capture(window,'mod-markdown.png')
        report['checks'].append('Card opens a detail page: BBCode rendered safely (table, list, links; unknown tags literal; foreign image as link), files with 7z marked manual')
        js(window,"document.querySelector('[data-testid=nexus-description] .bb-link').click()")
        assert report['opened_pages'][-1]=='https://example.invalid/guide'
        click(window,'nexus-translate')
        wait(window,"document.querySelector('[data-testid=nexus-description]')?.innerText.includes('只需配置一次容器') && !document.querySelector('.translate .working')")
        assert js(window,"document.querySelector('.translated-note')")
        assert js(window,"document.querySelector('[data-testid=nexus-description] h2').innerText")=='合成助手'
        assert js(window,"document.querySelector('[data-testid=nexus-description] code').innerText")=='Ctrl+R'
        assert js(window,"[...document.querySelectorAll('[data-testid=nexus-description] li')].some(li=>li.innerText==='保留配置')"), 'Inline BBCode must stay inside its Markdown list item'
        sent=[row['body']['messages'] for row in provider.requests]
        assert sent and all(messages[0]['role']=='system' for messages in sent) and any('Tired of dragging loot' in messages[1]['content'] for messages in sent)
        js(window,"document.querySelector('.about').scrollIntoView({block:'start'})")
        capture(window,'mod-detail-translated.png')
        js(window,"[...document.querySelectorAll('.segmented button')].find(b=>b.innerText==='原文').click()")
        wait(window,"document.querySelector('[data-testid=nexus-description]').innerText.includes('Tired of dragging loot')")
        report['checks'].append('AI translation (localhost provider) replaces the description with the translation; 原文 switches back')
        click(window,'nexus-official-link')
        assert report['opened_pages'][-1]=='https://www.nexusmods.com/probablystolen/mods/81'
        report['checks'].append('The official page is offered once, at the bottom of the detail page')
        js(window,"document.querySelector('[data-testid=nexus-files] .file-row button.primary').click()")
        wait(window,"document.querySelector('[data-testid=import-update-preview]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==old_dll.read_bytes()
        assert '升级' in js(window,"document.querySelector('[data-testid=import-update-preview]').innerText")
        capture(window,'confirm-install.png')
        click(window,'import-install-confirm')
        wait(window,"document.querySelector('[data-testid=import-installed]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes()==dll.read_bytes()
        assert not (game/'README.txt').exists()
        assert (game/'UserData/save-sentinel.bin').read_bytes()==b'isolated-save-never-deployed'
        assert (game/'Mods/foreign.json').read_bytes()==b'{"unknown":"keep"}'
        report['checks'].append('User-confirmed upgrade writes the selected Mod DLL, keeps README out of the game and preserves unrelated files/save sentinel')
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


owned_links = {}
def owned_link(file, key):
    return owned_links.setdefault((file, key), f'nxm://probablystolen/mods/81/files/{file}?key={key}&expires={int(time.time()+600)}&user_id=7')


def send_link(file, key):
    # Real entry point and same-user IPC; no registry/browser/account is touched.
    result = subprocess.run([sys.executable, str(ROOT/'main.py'), '--psmm-data-root', str(data),
                             '--nxm', owned_link(file, key)], cwd=ROOT, capture_output=True, timeout=15)
    assert result.returncode == 0, 'Owned secondary instance did not hand off'


def observe_nxm():
    global fail_next_account
    window = windows[0]
    try:
        window.events.loaded.wait()
        wait(window, "document.querySelector('[data-testid=nexus-received]')")
        assert not report['requests'], 'Cold-start URI must not automatically contact Nexus'
        capture(window, 'cold-start-confirmation.png')
        js(window, "document.querySelector('[data-testid=nexus-received] .garden-link').click()")
        wait(window, "!document.querySelector('[data-testid=nexus-received]')")
        report['checks'].append('Cold-start URI opens Nexus and waits for explicit confirmation; ignored receipt is discarded')
        js(window, "document.querySelector('[data-testid=nexus-protocol-options] summary').click()")
        click(window, 'nexus-register')
        wait(window, "document.querySelector('.handoff-confirm [class*=green]')")
        assert not protocol_state['owned']
        capture(window, 'protocol-conflict.png')
        js(window, "document.querySelector('.handoff-confirm .green').click()")
        wait(window, "document.querySelector('.handoff-head').innerText.includes('已启用')")
        assert protocol_state['owned']
        report['checks'].append('Register UI asks before replacing existing handler; fixture registration only')
        click(window, 'nexus-browse')
        wait(window, "document.querySelector('[data-testid=nexus-install-latest]')")
        click(window, 'nexus-install-latest')
        wait(window, "document.querySelector('[data-testid=nexus-website-wait]')")
        assert report['opened_pages'][-1].endswith('?tab=files&file_id=11')
        assert not any(path.endswith('/download_link.json') for path in report['requests'])
        capture(window, 'website-authorization.png')
        report['checks'].append('Free latest install opens selected website file without calling download URL API before authorization')
        window.minimize()
        time.sleep(.3)
        assert win32gui.IsIconic(window.native.Handle.ToInt64())
        send_link(11, 'owned-latest')
        wait(window, "document.querySelector('[data-testid=import-installed]')")
        assert not win32gui.IsIconic(window.native.Handle.ToInt64())
        assert win32gui.GetWindowRect(window.native.Handle.ToInt64()) == win32gui.GetWindowRect(window.native.webview.Handle.ToInt64())
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == dll.read_bytes()
        capture(window, 'callback-installed.png')
        report['checks'].append('Separate process forwards matching authorization, restores minimized WebView correctly, downloads and actually installs the selected v2 DLL')
        count = sum(path.endswith('/download_link.json') for path in report['requests'])
        send_link(11, 'owned-latest')
        time.sleep(1.3)
        assert sum(path.endswith('/download_link.json') for path in report['requests']) == count
        assert not apis[0]._context.nxm_inbox.peek()['items']
        report['checks'].append('Duplicate protocol callback does not download or install again')
        js(window, "[...document.querySelectorAll('[data-testid=native-import-zone] dialog footer button')].find(b=>b.innerText==='关闭').click()")
        click(window, 'nexus-open')
        js(window, "document.querySelector('[data-testid=nexus-card] .install-arrow').click()")
        click(window, 'nexus-choose-version')
        wait(window, "document.querySelector('[data-testid=nexus-versions]').open && document.querySelectorAll('[data-testid=nexus-versions] .file-row').length===3")
        js(window, "document.querySelectorAll('[data-testid=nexus-versions] .file-row')[2].querySelector('button').click()")
        wait(window, "document.querySelector('[data-testid=nexus-website-wait]')")
        assert report['opened_pages'][-1].endswith('?tab=files&file_id=13')
        send_link(13, 'owned-downgrade')
        wait(window, "document.querySelector('[data-testid=import-update-preview]') && !document.querySelector('[data-testid=import-progress]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == dll.read_bytes()
        assert '降级' in js(window, "document.querySelector('[data-testid=import-update-preview]').innerText")
        capture(window, 'callback-downgrade-review.png')
        click(window, 'import-install-confirm')
        wait(window, "document.querySelector('[data-testid=import-installed]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == old_dll.read_bytes()
        report['checks'].append('Version-specific web callback preserves downgrade confirmation; only confirmation writes selected v1 DLL')
        js(window, "[...document.querySelectorAll('[data-testid=native-import-zone] dialog footer button')].find(b=>b.innerText==='关闭').click()")
        send_link(11, 'owned-manual-retry')
        wait(window, "document.querySelector('[data-testid=nexus-received]')")
        fail_next_account = True
        js(window, "document.querySelector('[data-testid=nexus-received] .green').click()")
        wait(window, "document.querySelector('[data-testid=nexus-error]') && document.querySelector('[data-testid=nexus-received]')")
        capture(window, 'callback-retry.png')
        js(window, "document.querySelector('[data-testid=nexus-received] .green').click()")
        wait(window, "document.querySelector('[data-testid=import-update-preview]') && !document.querySelector('[data-testid=import-progress]')")
        click(window, 'import-install-confirm')
        wait(window, "document.querySelector('[data-testid=import-installed]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == dll.read_bytes()
        assert (game/'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
        assert (game/'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'
        report['checks'].append('Unsolicited URI requires confirmation; simulated network failure retains receipt for manual retry and successful upgrade preserves unrelated files')
        js(window, "[...document.querySelectorAll('[data-testid=native-import-zone] dialog footer button')].find(b=>b.innerText==='关闭').click()")
        click(window, 'nexus-open')
        click(window, 'nexus-install-latest')
        wait(window, "document.querySelector('[data-testid=nexus-website-wait]')")
        assert 'Manual download' in js(window, "document.querySelector('[data-testid=nexus-website-wait]').innerText")
        assert 'Download with Manager' not in js(window, "document.querySelector('[data-testid=nexus-website-wait]').innerText")
        capture(window, 'manual-download-guidance.png')
        local_zip = out / 'manually-downloaded-old-version.zip'
        local_zip.write_bytes(old_body)
        apis[0]._context.choose_import_source = lambda kind: str(local_zip) if kind == 'zip' else None
        count = len(report['requests'])
        click(window, 'nexus-import-zip')
        wait(window, "document.querySelector('[data-testid=import-update-preview]') && !document.querySelector('[data-testid=import-progress]')")
        assert '降级' in js(window, "document.querySelector('[data-testid=import-update-preview]').innerText")
        assert len(report['requests']) == count, 'Local import must not request Nexus authorization'
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == dll.read_bytes()
        capture(window, 'manual-local-downgrade-review.png')
        click(window, 'import-install-confirm')
        wait(window, "document.querySelector('[data-testid=import-installed]')")
        assert (game/'Mods/NexusOwnedMod.dll').read_bytes() == old_dll.read_bytes()
        assert local_zip.read_bytes() == old_body
        report['checks'].append('Manual download UI hands locally picked ZIP through real grant/import/install: actual v1 overrides website v2 identity, downgrade requires confirmation, source remains unchanged; file picker selection stubbed')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try: capture(window, 'failure.png')
        except Exception: pass
    finally:
        window.destroy()


composition.DesktopApi=factory
webview.create_window=create
webview.start=lambda *values,**kwargs:original_start(observe_nxm if options.nxm_flow else observe,**kwargs)
report['exit_code']=main.main(['--psmm-data-root',str(data)] + (['--nxm', owned_link(13, 'owned-cold')] if options.nxm_flow else []))
assert not list((data/'downloads/nexus').rglob('download.*')), 'Closing must reclaim private Nexus download originals'
report['checks'].append('Closing revokes download grants and reclaims originals while retaining installed files and library')
(out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
raise SystemExit(0 if report['status']=='PASS' and report['exit_code']==0 else 1)
