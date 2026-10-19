"""Actual Vue/bridge AI with an owned localhost provider and synthetic game.

Only the native package source picker is replaced. Input uses automated DOM
events, not trusted pointer input or manual acceptance. No real game launches.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=('first', 'reopen'), required=True)
parser.add_argument('--provider-url', required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT / 'docs/development/evidence/PS-04-01')
provider_uri = urlsplit(args.provider_url)
assert provider_uri.scheme == 'http' and provider_uri.hostname == '127.0.0.1' and provider_uri.path == '/v1'
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.ai.redaction import redact
from backend.app import composition
from backend.app.identity import KEYRING_SERVICE
from tests.integration.harness.transaction_recovery import TransactionFixture, durable_json, tree
from tests.integration.test_content_catalog import compile_fixture

OWNED_KEY = 'desktop-owned-synthetic-ai-key-PS0401'
SELECTED = ('token=desktop-selected-token\nProxy-Authorization: Bearer desktop-selected-proxy\n'
            'https://name:desktop-url-pass@local.test/path?token=desktop-query-token&plain=yes\n' + OWNED_KEY)
game, data, source = out / 'synthetic-game', out / 'private-data', out / 'owned-mod-source'
target = game / 'Mods/optional-ai-independent.dll'
owner = dict(root=str(out), synthetic=True, owner='PSModManager.CASE039.desktop.owned.v1')
if args.phase == 'first':
    fixture = TransactionFixture(out)
    fixture.close()
    (source / 'Mods').mkdir(parents=True)
    shutil.copyfile(compile_fixture(out / 'compiler', 'AiDesktopMod'), source / 'Mods' / target.name)
    (game / 'MelonLoader/Latest.log').write_text('private-log-must-not-send', encoding='utf-8')
    durable_json(out / 'desktop-owner.json', owner)
else:
    assert json.loads((out / 'desktop-owner.json').read_text(encoding='utf-8')) == owner
source_before = tree(source)
report = dict(profile='desktop-local', phase=args.phase, status='FAIL', checks=[], requests=[], screenshots=[],
    input_method='Automated DOM events in actual WebView; no trusted pointer or manual acceptance claim',
    scope='Optional text AI, local HTTP/SSE, native credential, direct Mod independence and actual process reopening')
contexts, managers, windows = [], [], []
original_api = composition.DesktopApi
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = original_api(context)
    dispatch = api.dispatch

    def observed(request):
        response = dispatch(request)
        row = dict(command=request.get('command'), ok=response.get('ok'), error_code=(response.get('error') or {}).get('code'))
        if request.get('command') in {'task.snapshot', 'task.subscribe', 'task.events', 'task.cancel'}:
            response_data = response.get('data') or {}
            snapshot = response_data.get('snapshot', response_data)
            if snapshot.get('state') in ('succeeded', 'failed', 'cancelled'):
                row.update(state=snapshot['state'], task_id=snapshot.get('task_id'),
                           task_error_code=(snapshot.get('data') or {}).get('error_code'))
        # Neither raw request payloads nor provider/model text enters evidence.
        report['requests'].append(row)
        return response

    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in {'socket.bind', 'socket.connect'}:
        address = values[1]
        # Windows asyncio creates a localhost socket pair for its event-loop
        # self-pipe. Permit those internal endpoints as well as the provider;
        # every actual provider request is checked by the owning parent test.
        if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
            raise PermissionError('Only localhost sockets are allowed')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real source or game access')


sys.addaudithook(audit)


def wait(window, expression, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('WebView condition timed out: ' + expression[:240])


def element(name):
    return "document.querySelector('[data-testid=" + name + "]')"


def click(window, name, parent='document'):
    selector = f"{parent}.querySelector('[data-testid={name}]')"
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    window.evaluate_js(selector + '.click()')


def fill(window, name, value):
    selector = element(name)
    wait(window, f'Boolean({selector}) && !{selector}.disabled')
    window.evaluate_js(f"(()=>{{const input={selector};input.value={json.dumps(value)};input.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")


def screenshot(window, label):
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right-left, bottom-top
    dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(dc)
    memory = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    previous = memory.SelectObject(bitmap)
    filename = f'{args.phase}-{label}.png'
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (width, height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, dc)


def checkpoint(name):
    durable_json(out / (name + '-checkpoint.json'), dict(checkpoint=name))
    deadline = time.monotonic() + 10
    ack = out / (name + '-ack.json')
    while not ack.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert ack.exists() and json.loads(ack.read_text(encoding='utf-8'))['http_requests'] == 0


def select(window):
    click(window, 'installation-open')
    selector = "Array.from(document.querySelectorAll('.installation-content .installation-row')).find(row=>row.querySelector('code')?.textContent===" + json.dumps(str(game)) + ')'
    wait(window, 'Boolean(' + selector + ')')
    window.evaluate_js('(' + selector + ").querySelector('button').click()")
    wait(window, element('mod-refresh') + " && !" + element('mod-refresh') + ".disabled && " + element('mod-manager') + "?.getAttribute('aria-busy')==='false'")


def mod_action(window, release_id, action, expected):
    row = "Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(row=>row.dataset.releaseId===" + json.dumps(release_id) + ')'
    click(window, 'mod-' + action, '(' + row + ')')
    wait(window, '(' + row + f")?.dataset.modState==='{expected}' && " + element('mod-manager') + "?.getAttribute('aria-busy')==='false'")
    assert not window.evaluate_js(element('mod-error') + '?.innerText')
    assert target.exists() == (expected == 'enabled')
    if expected == 'enabled':
        assert target.read_bytes() == (source / 'Mods' / target.name).read_bytes()
    assert (game / 'UserData/save-sentinel.bin').read_bytes() == b'isolated-save-never-deployed'
    assert (game / 'Mods/foreign.json').read_bytes() == b'{"unknown":"keep"}'


def ai_idle(window):
    wait(window, element('ai-panel') + "?.getAttribute('aria-busy')==='false'")


def send(window, question, selected=''):
    fill(window, 'ai-question', question)
    fill(window, 'ai-selected-text', selected)
    click(window, 'ai-send')


def vault_key():
    assistant = contexts[0].ai
    return assistant.credentials._backend.get_password(KEYRING_SERVICE, assistant.credentials.account(assistant._config()))


def normal_answer(window):
    wait(window, element('ai-response') + "?.innerText.includes('合成回答') && !" + element('ai-progress'))
    text = window.evaluate_js(element('ai-response') + '.innerText')
    assert '<script>window.__PSMMAIExecuted=true</script>' in text and '[已隐藏]' in text
    assert not window.evaluate_js('Boolean(window.__PSMMAIExecuted)')
    assert not window.evaluate_js(element('ai-messages') + '.querySelector("script")')
    for secret in (OWNED_KEY, 'synthetic-response-token', 'synthetic-response-proxy'):
        assert secret not in text
    assert not window.evaluate_js(element('ai-error') + '?.innerText')


def observe():
    window = windows[0]
    try:
        assert window.events.loaded.wait(30)
        select(window)
        assert not any(r['command'].startswith('ai.') for r in report['requests'])
        assert not window.evaluate_js(element('ai-panel'))
        if args.phase == 'first':
            click(window, 'import-open')
            click(window, 'import-directory')
            wait(window, element('import-preview') + '?.innerText.includes(' + json.dumps(target.name) + ')')
            click(window, 'import-commit')
            wait(window, element('import-result') + "?.innerText.includes('1 个文件')")
            click(window, 'import-show-library')
            release_id = wait(window, "document.querySelector('[data-testid=mod-row]')?.dataset.releaseId")
            durable_json(out / 'release.json', dict(release_id=release_id))
            mod_action(window, release_id, 'install', 'enabled')
            mod_action(window, release_id, 'uninstall', 'not_installed')
            assert not any(r['command'].startswith('ai.') for r in report['requests'])
            checkpoint('default-local')
            report['checks'].append('Default Mod tab imports, installs and uninstalls without AI configuration, AI request or HTTP traffic')
            screenshot(window, 'local-independent')
        else:
            release_id = json.loads((out / 'release.json').read_text(encoding='utf-8'))['release_id']
        click(window, 'workspace-ai')
        ai_idle(window)
        if args.phase == 'first':
            fill(window, 'ai-base-url', args.provider_url)
            fill(window, 'ai-model', 'synthetic-text-model')
            fill(window, 'ai-api-key', OWNED_KEY)
            window.evaluate_js(element('ai-remember') + '.click()')
            click(window, 'ai-save')
            wait(window, element('ai-key-status') + "?.innerText==='已保存到 Windows 凭据' && " + element('ai-api-key') + ".value===''")
            ai_idle(window)
            assert vault_key() == OWNED_KEY
            checkpoint('settings-saved')
            assert OWNED_KEY not in (data / 'config.json').read_text(encoding='utf-8')
            report['checks'].append('AI settings save uses owned native compound credentials, clears password input and contacts no provider')
            click(window, 'ai-test')
            wait(window, element('ai-notice') + "?.innerText==='连接测试完成。'")
            ai_idle(window)
            report['checks'].append('Explicit connection test completes through actual bridge, task subscription and localhost HTTP/SSE')
            fill(window, 'ai-selected-label', '框选片段')
            send(window, '解释这个报错。', SELECTED)
            wait(window, element('ai-progress') + ' && ' + element('ai-response') + "?.innerText.includes('合成') && !" + element('ai-response') + ".innerText.includes('合成回答')", timeout=12)
            report['checks'].append('Streamed intermediate answer is visible while the actual request remains running')
            normal_answer(window)
            click(window, 'ai-export')
            exported = wait(window, element('ai-export-text') + '?.value')
            assert '解释这个报错。' in exported and '框选片段' in exported and '[已隐藏]' in exported
            for secret in (OWNED_KEY, 'desktop-selected-token', 'desktop-selected-proxy', 'desktop-url-pass', 'desktop-query-token',
                           'synthetic-response-token', 'synthetic-response-proxy'):
                assert secret not in exported
            report['checks'].append('Completed answer is plain text with no script execution; displayed history and exported session redact selected and returned secrets')
            screenshot(window, 'answer-export')
            click(window, 'ai-new-session')
            send(window, '[[401]]')
            message = wait(window, element('ai-error') + '?.innerText')
            ai_idle(window)
            assert '认证' in message and OWNED_KEY not in message and 'provider body' not in message
            assert not window.evaluate_js(element('ai-response'))
            report['checks'].append('Actual HTTP 401 has a visible bounded authentication error without provider body or credential echo')
            click(window, 'ai-new-session')
            send(window, '[[stall]]')
            task_id = wait(window, element('ai-progress') + '?.dataset.taskId')
            click(window, 'ai-cancel')
            wait(window, element('ai-notice') + "?.innerText==='已取消请求。' && !" + element('ai-progress'))
            assert any(row.get('state') == 'cancelled' and row.get('task_id') == task_id for row in report['requests'])
            click(window, 'ai-new-session')
            wait(window, element('ai-messages') + ".dataset.sessionId==='' && !" + element('ai-messages') + '.children.length')
            report['checks'].append('Actual stalled SSE cancels to terminal state and new session clears visible prior conversation')
            click(window, 'workspace-mods')
            mod_action(window, release_id, 'install', 'enabled')
            mod_action(window, release_id, 'uninstall', 'not_installed')
            report['checks'].append('Ordinary actual Mod installation and removal remain usable after AI authentication error and cancellation')
        else:
            assert window.evaluate_js(element('ai-base-url') + '.value') == args.provider_url
            assert window.evaluate_js(element('ai-model') + '.value') == 'synthetic-text-model'
            assert window.evaluate_js(element('ai-api-key') + '.value') == ''
            assert window.evaluate_js(element('ai-key-status') + '.innerText') == '已保存到 Windows 凭据'
            assert vault_key() == OWNED_KEY
            assert not window.evaluate_js(element('ai-messages') + '.children.length')
            assert window.evaluate_js(element('ai-messages') + '.dataset.sessionId') == ''
            report['checks'].append('New actual manager process restores provider/model and owned native credential without echoing password or persisting conversations')
            send(window, '重开后仍可提问。')
            normal_answer(window)
            click(window, 'ai-clear-key')
            wait(window, element('ai-key-status') + "?.innerText==='未配置密钥' && " + element('ai-api-key') + ".value===''")
            assert vault_key() is None
            click(window, 'ai-new-session')
            send(window, '本地服务清除密钥后可以免密提问。')
            normal_answer(window)
            report['checks'].append('Clear key removes the owned native compound record; localhost remains usable and final request has no Authorization header')
            screenshot(window, 'reopened-cleared')
        assert all(not row['command'].startswith(('assistant.', 'plan.', 'profile.')) for row in report['requests'])
        assert tree(source) == source_before
        assert (game / 'MelonLoader/Latest.log').read_text(encoding='utf-8') == 'private-log-must-not-send'
        report['checks'].append('AI remains text only: no plan/tool requests, automatic local file payload, source mutation or real game launch')
        report['status'] = 'PASS'
    except Exception:
        report['error'] = traceback.format_exc()
        try:
            report['failure_document'] = redact(window.evaluate_js('document.body.innerText'), (OWNED_KEY,))
        except Exception:
            pass
    finally:
        window.destroy()


def create(*values, **options):
    options.update(width=1280, height=900)
    window = original_create(*values, **options)
    windows.append(window)
    return window


def start(*values, **options):
    contexts[0].choose_import_source = lambda kind: str(source) if kind == 'directory' else None
    return original_start(observe, **options)


webview.create_window, webview.start = create, start
report['app_exit_code'] = main.main(['--psmm-data-root', str(data)])
with managers[0]._lock:
    report['cleanup'] = dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),
        subscriptions=len(managers[0]._subscriptions))
if tree(source) != source_before or any(report['cleanup'].values()):
    report['status'] = 'FAIL'
report['source_unchanged'] = tree(source) == source_before
(out / (args.phase + '-ai-session.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(dict(status=report['status'], app_exit_code=report['app_exit_code'], checks=len(report['checks']), error=report.get('error'))))
raise SystemExit(0 if report['status'] == 'PASS' and report['app_exit_code'] == 0 else 1)
