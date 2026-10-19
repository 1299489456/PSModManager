"""Actual suggestion buttons use the ordinary Mod consumer in a real WebView.

All installations/packages/data are owned fixtures; automated DOM is not manual
or trusted pointer acceptance. No game executable or external AI is started.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser()
parser.add_argument('--evidence-root',required=True)
parser.add_argument('--provider-url',required=True)
args=parser.parse_args()
out=Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT/'docs/development/evidence/PS-04-03')
uri=urlsplit(args.provider_url)
assert uri.scheme=='http' and uri.hostname=='127.0.0.1' and uri.path=='/v1'
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']='--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.app.paths import reject_reparse
from tests.integration.harness.transaction_recovery import TransactionFixture,tree

reject_reparse(out)
fixture=TransactionFixture(out)
game,data=fixture.game,fixture.data
release=fixture.publish({'Mods/suggestion.png':b'owned-desktop-suggestion-resource'},'桌面建议包')
other=fixture.publish({'Plugins/independent.png':b'independent-desktop-mod'},'保留的 Mod')
fixture.call('mod.action',dict(installation_id=fixture.installation,release_id=other['release_id'],
    manifest_hash=other['manifest_hash'],action='install',idempotency_key=str(uuid4())))
(out/'release.json').write_text(json.dumps(release),encoding='utf-8')
fixture.close()
baseline=tree(game)
target=game/'Mods/suggestion.png'
report=dict(status='FAIL',profile='desktop-local',checks=[],screenshots=[],commands=[],
    input_method='Automated DOM and visible viewport in actual WebView; no trusted pointer/manual/DPI claim')
contexts,windows,managers=[],[],[]
original_api=composition.DesktopApi
original_create,original_start=webview.create_window,webview.start

def factory(context):
    contexts.append(context);managers.append(context.jobs)
    api=original_api(context);dispatch=api.dispatch
    def observed(value):
        response=dispatch(value)
        row=dict(command=value.get('command'),ok=response.get('ok'))
        if value.get('command')=='mod.action':
            assert set(value['payload'])=={'installation_id','release_id','manifest_hash','action','idempotency_key'}
            row['action']=value['payload']['action']
        report['commands'].append(row)
        return response
    api.dispatch=observed
    return api
composition.DesktopApi=factory

def audit(event,values):
    if event in {'socket.bind','socket.connect'}:
        address=values[1]
        if not isinstance(address,tuple) or address[0] not in {'127.0.0.1','::1'}:
            raise PermissionError('Only owned localhost sockets')
    if event=='open' and str(values[0]).replace('\\','/').lower().startswith(('e:/workspace/github/rimcrow/','e:/entertainment/steam/')):
        raise PermissionError('No real game/source access')
sys.addaudithook(audit)

def element(name):return "document.querySelector('[data-testid="+name+"]')"

def wait(window,expression,timeout=40):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=window.evaluate_js(expression)
        if result:return result
        time.sleep(.05)
    raise AssertionError('WebView condition timed out: '+expression[:200])

def visible(window,selector):
    window.evaluate_js(selector+'.scrollIntoView({block:"center"})')
    wait(window,'(()=>{const e='+selector+';const r=e.getBoundingClientRect();const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.top>=0 && r.bottom<=innerHeight && r.left>=0 && r.right<=innerWidth && (hit===e || e.contains(hit))})()')

def click(window,name):
    selector=element(name)
    wait(window,f'Boolean({selector}) && !{selector}.disabled');visible(window,selector)
    window.evaluate_js(selector+'.click()')

def fill(window,name,value):
    selector=element(name)
    wait(window,f'Boolean({selector}) && !{selector}.disabled');visible(window,selector)
    window.evaluate_js(f"(()=>{{const e={selector};e.value={json.dumps(value)};e.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")

def screenshot(window,label):
    hwnd=window.native.Handle.ToInt64();left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,right-left,bottom-top);previous=memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/(label+'.png'))
        report['screenshots'].append(label+'.png')
    finally:
        memory.SelectObject(previous);win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)

def no_extra_flows():
    assert not any(row['command'].startswith(('plan.','profile.','assistant.','game.launch','config.save')) for row in report['commands'])
    assert (game/'UserData/save-sentinel.bin').read_bytes()==b'isolated-save-never-deployed'
    assert (game/'Plugins/independent.png').read_bytes()==b'independent-desktop-mod'
    assert (game/'Mods/foreign.json').read_bytes()==b'{"unknown":"keep"}'

def ask(window,verb):
    click(window,'ai-new-session');fill(window,'ai-question','桌面建议 '+verb);click(window,'ai-send')
    wait(window,element('ai-suggestion')+f"?.dataset.action==='{verb}' && !"+element('ai-progress'))
    assert window.evaluate_js(element('ai-suggestion-name')+'.innerText')=='桌面建议包'
    assert not window.evaluate_js('Boolean(window.__AISuggestionExecuted)')
    assert not window.evaluate_js(element('ai-suggestion')+'.querySelector("script")')
    assert 'desktop-action-secret' not in window.evaluate_js(element('ai-suggestion-reason')+'.innerText')
    no_extra_flows()
    return window.evaluate_js(element('ai-suggestion')+'.dataset.suggestionId')

def execute(window,state):
    click(window,'ai-suggestion-action')
    wait(window,element('ai-suggestion')+"?.dataset.status==='completed' && "+element('ai-action-notice')+"?.innerText.includes('完成')")
    assert target.exists()==(state=='enabled')
    if target.exists():assert target.read_bytes()==b'owned-desktop-suggestion-resource'
    assert window.evaluate_js(element('ai-suggestion-action')+'.disabled')
    count=sum(row['command']=='mod.action' for row in report['commands'])
    window.evaluate_js(element('ai-suggestion-action')+'.click()');time.sleep(.2)
    assert sum(row['command']=='mod.action' for row in report['commands'])==count
    no_extra_flows()

def observe():
    window=windows[0]
    try:
        assert window.events.loaded.wait(30)
        click(window,'installation-open')
        row="Array.from(document.querySelectorAll('.installation-content .installation-row')).find(r=>r.querySelector('code')?.textContent==="+json.dumps(str(game))+')'
        wait(window,'Boolean('+row+') && !('+row+").querySelector('button').disabled")
        visible(window,'('+row+").querySelector('button')")
        window.evaluate_js('('+row+").querySelector('button').click()")
        wait(window,"document.querySelector('.installation-dialog')?.open===false")
        wait(window,element('mod-manager')+"?.getAttribute('aria-busy')==='false' && !"+element('mod-refresh')+'.disabled')
        assert not any(row['command'].startswith('ai.') for row in report['commands'])
        report['checks'].append('Default management does not need AI')
        click(window,'workspace-ai');wait(window,element('ai-panel')+"?.getAttribute('aria-busy')==='false'")
        fill(window,'ai-base-url',args.provider_url);fill(window,'ai-model','synthetic-suggestions');click(window,'ai-save')
        wait(window,element('ai-panel')+"?.getAttribute('aria-busy')==='false'");click(window,'ai-allow-queries')
        before=tree(game);ask(window,'install');assert tree(game)==before
        assert not any(row['command']=='mod.action' for row in report['commands'])
        visible(window,element('ai-suggestion'));screenshot(window,'suggestion-before-click')
        report['checks'].append('Actual stream creates a plain redacted suggestion card without writing any Mod before a click')
        execute(window,'enabled');visible(window,element('ai-suggestion'));screenshot(window,'installed')
        report['checks'].append('One ordinary install click writes actual bytes and confirms refreshed state; repeated click is disabled')
        click(window,'workspace-mods')
        row="Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(r=>r.dataset.releaseId==="+json.dumps(release['release_id'])+')'
        wait(window,'('+row+")?.dataset.modState==='enabled' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        report['checks'].append('Default Mod list refreshes to actual installed state after AI adoption')
        click(window,'workspace-ai');wait(window,element('ai-panel')+"?.getAttribute('aria-busy')==='false'")
        click(window,'ai-allow-queries')
        before=tree(game);ask(window,'disable');assert tree(game)==before;execute(window,'disabled')
        before=tree(game);ask(window,'enable');assert tree(game)==before;execute(window,'enabled')
        report['checks'].append('Suggestion disable and enable use the same normal endpoint and preserve the other Mod and saves')
        click(window,'workspace-mods')
        wait(window,'('+row+")?.dataset.modState==='enabled' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        uninstall='('+row+').querySelector("[data-testid=mod-uninstall]")'
        wait(window,'!'+uninstall+'.disabled');visible(window,uninstall);window.evaluate_js(uninstall+'.click()')
        wait(window,'('+row+")?.dataset.modState==='not_installed' && "+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
        assert not target.exists();click(window,'workspace-ai');wait(window,element('ai-panel')+"?.getAttribute('aria-busy')==='false'")
        click(window,'ai-allow-queries');ask(window,'install')
        target.write_bytes(b'owned-unknown-occupant')
        click(window,'ai-suggestion-action')
        wait(window,element('ai-action-error')+"?.innerText.includes('已保留') && "+element('ai-suggestion')+"?.dataset.status==='failed'")
        assert target.read_bytes()==b'owned-unknown-occupant'
        visible(window,element('ai-suggestion'));screenshot(window,'ordinary-error')
        target.unlink();execute(window,'enabled')
        report['checks'].append('Ordinary unknown-file conflict is visible and retained; retry installs through the same service')
        ask(window,'disable')
        id=window.evaluate_js(element('ai-messages')+'.dataset.sessionId')
        sid=window.evaluate_js(element('ai-suggestion')+'.dataset.suggestionId')
        record=next(row for row in contexts[0].ai._sessions[id]['suggestions'] if row['suggestion_id']==sid)
        record['expires_at']=0
        count=sum(row['command']=='mod.action' for row in report['commands'])
        click(window,'ai-suggestion-action');wait(window,element('ai-action-error')+"?.innerText.includes('过期')")
        assert sum(row['command']=='mod.action' for row in report['commands'])==count and target.is_file()
        report['checks'].append('Expired selection reports an ordinary error without calling mod.action')
        ask(window,'disable')
        ready,resume=threading.Event(),threading.Event()
        def fault(point,details):
            if point=='transaction.ready':
                ready.set()
                assert resume.wait(15)
        contexts[0].deployments.fault=fault
        try:
            click(window,'ai-suggestion-action')
            assert ready.wait(15)
            click(window,'ai-action-cancel')
            wait(window,element('ai-suggestion-progress')+"?.innerText.includes('正在取消')")
        finally:
            resume.set()
        wait(window,element('ai-suggestion')+"?.dataset.status==='cancelled' && "+element('ai-action-error')+"?.innerText.includes('取消')")
        assert target.read_bytes()==b'owned-desktop-suggestion-resource'
        contexts[0].deployments.fault=None
        report['checks'].append('Actual accepted Mod action cancels before target writes and shows the cancelled result; checkpoint pause is a constructor test hook')
        no_extra_flows()
        assert {key:tree(game)[key] for key in baseline}==baseline
        assert sum(row['command']=='mod.action' for row in report['commands'])==7
        report['checks'].append('No Profile/plan approval page/native confirmation or game launch; all unrelated bytes preserved')
        report['status']='PASS'
    except Exception:
        report['error']=traceback.format_exc()
        try:report['failure_document']=window.evaluate_js('document.body.innerText')
        except Exception:pass
    finally:window.destroy()

def forbidden_confirmation(*args):raise AssertionError('Ordinary suggestion action must not open native approval')
def create(*values,**options):
    options.update(width=1280,height=900)
    window=original_create(*values,**options);window.create_confirmation_dialog=forbidden_confirmation;windows.append(window);return window
def start(*values,**options):return original_start(observe,**options)
webview.create_window,webview.start=create,start
report['app_exit_code']=main.main(['--psmm-data-root',str(data)])
with managers[0]._lock:
    report['cleanup']=dict(active_workers=sum(j.thread.is_alive() for j in managers[0]._jobs.values()),subscriptions=len(managers[0]._subscriptions))
if any(report['cleanup'].values()):report['status']='FAIL'
(out/'ai-handoff.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status=report['status'],checks=len(report['checks']),error=report.get('error'))))
raise SystemExit(0 if report['status']=='PASS' and report['app_exit_code']==0 else 1)
