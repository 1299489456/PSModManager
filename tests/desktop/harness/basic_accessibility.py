"""Actual basic Mod UI with owned keyboard input, import, files and two starts.

Only the picker supplies the owned directory. All preview, commit, list and
ordinary actions run through the live frontend bridge and backend services.
Current window dimensions and trusted keyboard events do not claim system DPI
or manual usability acceptance. No AI, networking, game or approved plan.
"""
import argparse
import ast
import ctypes
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root',required=True)
parser.add_argument('--phase',choices=('first','reopen'),required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert out.is_relative_to(ROOT/'docs/development/evidence/PS-05-01')
metadata = json.loads((out/'fixture.json').read_text(encoding='utf-8'))
game,data = Path(metadata['game']),Path(metadata['data'])
assert game.is_relative_to(out) and data.is_relative_to(out)
for key,name in [('LOCALAPPDATA','local'),('APPDATA','roaming'),('TEMP','temp'),('TMP','temp')]:
    (out/name).mkdir(exist_ok=True)
    os.environ[key]=str(out/name)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']='--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32process
import win32ui
from PIL import Image
from backend.app import composition
from backend.app.paths import reject_reparse
from tests.integration.harness.transaction_recovery import tree

reject_reparse(out)
started=time.monotonic()
report=dict(status='FAIL',profile='desktop-local',phase=args.phase,requests=[],checks=[],screenshots=[],
    operations=[],timings=[],window_layouts=[],input_method='Owned native Tab/Enter/Space/Escape plus automated DOM for other selections',
    scope='Actual main/WebView/bridge/SQLite/Chinese directory/files; no manual DPI or external-resource claim')
contexts,managers,windows=[],[],[]
original_api=composition.DesktopApi
original_create,original_start=webview.create_window,webview.start


def factory(context):
    contexts.append(context);managers.append(context.jobs)
    api=original_api(context)
    dispatch=api.dispatch
    def observed(value):
        before=time.monotonic()
        result=dispatch(value)
        row=dict(command=value.get('command'),ok=result.get('ok'),elapsed_ms=round((time.monotonic()-before)*1000,2))
        if value.get('command')=='mod.action':
            row['action']=value['payload']['action']
        report['requests'].append(row)
        return result
    api.dispatch=observed
    return api
composition.DesktopApi=factory


def audit(event,values):
    if event in ('socket.bind','socket.connect'):
        raise PermissionError('No Python networking in owned basic management')
    if event=='open' and str(values[0]).replace('\\','/').lower().startswith(('e:/workspace/github/rimcrow/','e:/entertainment/steam/')):
        raise PermissionError('No real game/source access')
sys.addaudithook(audit)


# Reuse only the existing bounded activation function's source. Executing the
# old history scenario or its module-level setup is explicitly avoided.
activation_source=ROOT/'tests/desktop/harness/rollback_history.py'
activation_ast=ast.parse(activation_source.read_text(encoding='utf-8'))
activation_node=next(node for node in activation_ast.body if isinstance(node,ast.FunctionDef) and node.name=='activate_window')
exec(compile(ast.Module(body=[activation_node],type_ignores=[]),str(activation_source),'exec'),globals())
report['activation_source_sha256']=hashlib.sha256(activation_source.read_bytes()).hexdigest()


def element(name):
    return "document.querySelector('[data-testid="+name+"]')"


def wait(window,expression,timeout=45):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=window.evaluate_js(expression)
        if result:
            return result
        time.sleep(.035)
    raise AssertionError('WebView condition timed out: '+expression[:240])


def visible(window,selector):
    window.evaluate_js(selector+'.scrollIntoView({block:"center"})')
    return wait(window,'(()=>{const e='+selector+';const r=e.getBoundingClientRect();const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth&&(h===e||e.contains(h))})()')


def click(window,selector):
    wait(window,'Boolean('+selector+')&&!'+selector+'.disabled')
    visible(window,selector)
    window.evaluate_js(selector+'.click()')


def fill(window,selector,value):
    visible(window,selector)
    window.evaluate_js('(()=>{const e='+selector+';e.value='+json.dumps(value)+';e.dispatchEvent(new Event("input",{bubbles:true}))})()')


def ready(window):
    wait(window,element('mod-manager')+"?.getAttribute('aria-busy')==='false'&&!"+element('mod-refresh')+'.disabled')


def owned_key(window,key):
    hwnd=window.native.Handle.ToInt64()
    assert win32gui.IsWindow(hwnd) and win32process.GetWindowThreadProcessId(hwnd)[1]==os.getpid()
    assert win32gui.GetForegroundWindow()==hwnd,'Never send input to a foreign foreground window'
    ctypes.windll.user32.keybd_event(key,0,0,0)
    ctypes.windll.user32.keybd_event(key,0,2,0)
    time.sleep(.045)


def tab_to(window,selector):
    visible(window,selector)
    activate_window(window.native.Handle.ToInt64())
    for count in range(90):
        if window.evaluate_js('document.activeElement===('+selector+')'):
            report.setdefault('keyboard_reach',[]).append(dict(selector=selector,tabs=count))
            return
        owned_key(window,0x09)
    raise AssertionError('Native Tab did not reach '+selector)


def keyboard_click(window,selector,key=0x0D):
    wait(window,'Boolean('+selector+')&&!'+selector+'.disabled')
    tab_to(window,selector)
    owned_key(window,key)


def row_for(release):
    return "Array.from(document.querySelectorAll('[data-testid=mod-row]')).find(r=>r.dataset.releaseId==="+json.dumps(release)+')'


def screenshot(window,label,selector):
    visible(window,selector)
    time.sleep(.2)
    hwnd=window.native.Handle.ToInt64()
    left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,right-left,bottom-top)
    previous=memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/(args.phase+'-'+label+'.png'))
        report['screenshots'].append(args.phase+'-'+label+'.png')
    finally:
        memory.SelectObject(previous);win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def measurement_start(window):
    window.evaluate_js('window.__measurement={start:performance.now(),frames:[],busy:[],errors:[]};')
    return time.monotonic()


def measurement_end(window,label,before):
    value=window.evaluate_js('(()=>{const m=window.__measurement;return {frame_count:m.frames.length,max_frame_gap_ms:m.frames.length>1?Math.max(...m.frames.slice(1).map((t,i)=>t-m.frames[i])):null,busy_observed:m.busy.length>0,busy_feedback_ms:m.busy.length?m.busy[0]-m.start:null,error_feedback_ms:m.errors.length?m.errors[0]-m.start:null}})()')
    value.update(action=label,elapsed_ms=round((time.monotonic()-before)*1000,2))
    report['operations'].append(value)
    return value


def preserved():
    actual=tree(game)
    assert {key:actual[key] for key in metadata['baseline']}==metadata['baseline']
    assert tree(Path(metadata['import_source']))==metadata['source_baseline']
    assert not any(row['command'].startswith(('ai.','plan.','profile.','assistant.','game.launch')) for row in report['requests'])


def verify_files(present):
    for name,value in metadata['files'].items():
        path=game/name
        assert path.exists()==present,(name,present)
        if present:
            assert path.stat().st_size==value['size']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==value['sha256']
    preserved()


def perform(window,release,action,state):
    row='('+row_for(release)+')'
    selector=row+'.querySelector("[data-testid=mod-'+action+']")'
    wait(window,'Boolean('+selector+')&&!'+selector+'.disabled')
    tab_to(window,selector)
    before=measurement_start(window)
    owned_key(window,0x20 if action=='disable' else 0x0D)
    wait(window,row+"?.dataset.modState==="+json.dumps(state)+'&&'+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
    assert not window.evaluate_js(element('mod-error')+'?.innerText')
    assert window.evaluate_js(element('mod-notice')+"?.innerText.includes('完成')")
    measurement_end(window,action,before)
    verify_files(state=='enabled')


def observe():
    window=windows[0]
    try:
        assert window.events.loaded.wait(30)
        wait(window,'Boolean('+element('installation-open')+')')
        window.evaluate_js("window.__keys=[];window.__clicks=[];document.addEventListener('keydown',e=>window.__keys.push({key:e.key,trusted:e.isTrusted,testid:e.target.closest('[data-testid]')?.dataset.testid||'',tag:e.target.tagName}),true);document.addEventListener('click',e=>window.__clicks.push({trusted:e.isTrusted,testid:e.target.closest('[data-testid]')?.dataset.testid||''}),true);function beat(t){window.__measurement?.frames.push(t);requestAnimationFrame(beat)}requestAnimationFrame(beat);new MutationObserver(()=>{const m=window.__measurement;if(!m)return;if(document.querySelector('[data-testid=mod-manager]')?.getAttribute('aria-busy')==='true')m.busy.push(performance.now());if(document.querySelector('[data-testid=mod-error]'))m.errors.push(performance.now())}).observe(document.body,{childList:true,subtree:true,attributes:true});")
        click(window,element('installation-open'))
        selection="Array.from(document.querySelectorAll('.installation-content .installation-row')).find(r=>r.querySelector('code')?.textContent==="+json.dumps(str(game))+')'
        click(window,'('+selection+")?.querySelector('button')")
        wait(window,"document.querySelector('.installation-dialog')?.open===false")
        ready(window)
        report['first_ready_ms']=round((time.monotonic()-started)*1000,2)
        if args.phase=='reopen':
            imported=json.loads((out/'imported.json').read_text(encoding='utf-8'))
            release=imported['release_id']
            found=False
            for _ in range(4):
                if window.evaluate_js('Boolean('+row_for(release)+')'):
                    found=True;break
                click(window,element('mod-next'));ready(window)
            assert found
            report['reopened_state']=window.evaluate_js('('+row_for(release)+').dataset.modState')
            assert report['reopened_state']=='enabled'
            assert metadata['imported_name'] in window.evaluate_js('('+row_for(release)+').innerText')
            verify_files(True)
            fill(window,element('mod-search'),metadata['imported_name'])
            wait(window,"document.querySelectorAll('[data-testid=mod-row]').length===1")
            screenshot(window,'enabled-reopen',row_for(release))
            perform(window,release,'uninstall','not_installed')
            actual=tree(game)
            assert {key:value for key,value in actual.items() if value!='<directory>'}=={
                key:value for key,value in metadata['baseline'].items() if value!='<directory>'}
            report['checks'].append('Second actual main/WebView sees persisted enabled Chinese package; native Enter uninstalls only its verified files')
        else:
            pagination=[];long_release=metadata['releases'][1]['release_id']
            for index in range(3):
                pagination.append(window.evaluate_js("document.querySelectorAll('[data-testid=mod-row]').length"))
                assert window.evaluate_js("document.querySelector('.mod-filter>span').innerText.includes('60')")
                if window.evaluate_js('Boolean('+row_for(long_release)+')'):
                    report['long_name_page']=index
                if index<2:
                    before=time.monotonic();click(window,element('mod-next'));ready(window)
                    report['timings'].append(dict(operation='next-page',elapsed_ms=round((time.monotonic()-before)*1000,2)))
            report['pagination_counts']=pagination
            assert pagination==[20,20,20] and window.evaluate_js(element('mod-next')+'.disabled')
            while window.evaluate_js("document.querySelector('.mod-pagination span').innerText.split('–')[0]")!='1':
                click(window,element('mod-prev'));ready(window)
            for _ in range(report['long_name_page']):
                click(window,element('mod-next'));ready(window)
            before=time.monotonic();fill(window,element('mod-search'),'基础样本 中文模组 001')
            wait(window,"document.querySelectorAll('[data-testid=mod-row]').length===1")
            report['timings'].append(dict(operation='page-local-filter',elapsed_ms=round((time.monotonic()-before)*1000,2)))
            assert window.evaluate_js('Boolean('+row_for(long_release)+')')
            for size in ((1280,720),(1440,900)):
                window.resize(*size)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    left,top,right,bottom=win32gui.GetWindowRect(window.native.Handle.ToInt64())
                    if (right-left,bottom-top)==size:
                        break
                    time.sleep(.025)
                else:
                    raise AssertionError('Owned native window did not reach requested size '+str(size))
                time.sleep(.15)
                visible(window,row_for(long_release))
                layout=window.evaluate_js('(()=>{const row='+row_for(long_release)+';const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}};const main=document.querySelector(".mod-manager"),shell=document.querySelector(".desktop-shell");return {viewport:[innerWidth,innerHeight],document_width:document.documentElement.scrollWidth,body_width:document.body.scrollWidth,row_width:row.getBoundingClientRect().width,action_right:row.querySelector(".mod-actions").getBoundingClientRect().right,name:row.querySelector("strong").textContent,shell_scroll_top:shell.scrollTop,main_scroll_top:main.scrollTop,main_overflow_y:getComputedStyle(main).overflowY,header:rect(document.querySelector(".topbar")),footer:rect(document.querySelector(".statusbar"))}})()')
                layout['requested_window']=list(size);report['window_layouts'].append(layout)
                assert layout['document_width']<=layout['viewport'][0]+1 and layout['body_width']<=layout['viewport'][0]+1,layout
                assert layout['action_right']<=layout['viewport'][0]+1,layout
                assert layout['shell_scroll_top']==0 and layout['main_overflow_y']=='auto',layout
                for region in ('header','footer'):
                    assert 0<=layout[region]['top']<layout[region]['bottom']<=layout['viewport'][1],layout
                screenshot(window,str(size[0])+'x'+str(size[1])+'-long-chinese',row_for(long_release))
            before=time.monotonic()
            detail_selector='('+row_for(long_release)+').querySelector("[data-testid=mod-details]")'
            keyboard_click(window,detail_selector)
            wait(window,element('profile-package-details')+'?.open')
            report['timings'].append(dict(operation='details-open',elapsed_ms=round((time.monotonic()-before)*1000,2)))
            assert window.evaluate_js("document.querySelector('#package-detail-title').innerText.includes('基础样本 中文模组 001')")
            assert window.evaluate_js("document.activeElement?.getAttribute('aria-label')==='关闭文件详情'")
            owned_key(window,0x1B)
            wait(window,'!'+element('profile-package-details'))
            report['detail_return_focus']=window.evaluate_js('document.activeElement===('+detail_selector+')')
            assert report['detail_return_focus'],'Escape must return focus to the calling detail button'
            report['checks'].append('60 registered packages use 20-row pages; page-local filtering and long Chinese text remain usable at both current window sizes')
            contexts[0].choose_import_source=lambda kind:metadata['import_source'] if kind=='directory' else None
            before_tree=tree(game)
            keyboard_click(window,element('import-open'))
            click(window,element('import-directory'))
            wait(window,element('import-preview')+"?.innerText.includes('中文样本.dll')")
            assert window.evaluate_js(element('import-preview')+'.querySelectorAll("tbody tr").length')==3
            assert tree(game)==before_tree
            fill(window,element('import-preview')+'.querySelector("input")',metadata['imported_name'])
            click(window,element('import-commit'))
            wait(window,element('import-result')+"?.innerText.includes('已加入包库')&&!"+element('import-progress'))
            release=window.evaluate_js(element('import-result')+'.querySelector("code").textContent')
            assert len(release)==36
            file_count=window.evaluate_js(element('import-result')+'.querySelector("strong").innerText.match(/\\d+/)?.[0]')
            assert file_count and int(file_count)==len(metadata['files'])
            imported=dict(release_id=release,file_count=int(file_count),name=metadata['imported_name'])
            report['imported']=imported
            (out/'imported.json').write_text(json.dumps(imported,ensure_ascii=False,indent=2),encoding='utf-8')
            assert tree(game)==before_tree
            click(window,element('import-show-library'))
            wait(window,"document.querySelector('.import-dialog')?.open===false")
            ready(window);wait(window,'Boolean('+row_for(release)+')')
            fill(window,element('mod-search'),metadata['imported_name'])
            wait(window,"document.querySelectorAll('[data-testid=mod-row]').length===1")
            screenshot(window,'chinese-import-ready',row_for(release))
            conflict=game/'Mods/中文样本.dll'
            assert not conflict.exists()
            conflict.write_bytes(b'owned unmanaged target must remain unchanged')
            conflict_tree=tree(game)
            conflict_button='('+row_for(release)+').querySelector("[data-testid=mod-install]")'
            tab_to(window,conflict_button)
            before=measurement_start(window)
            owned_key(window,0x20)
            wait(window,element('mod-error')+'?.innerText&&'+element('mod-manager')+"?.getAttribute('aria-busy')==='false'")
            message=window.evaluate_js(element('mod-error')+'.innerText')
            assert len(message)>6 and conflict.read_bytes()==b'owned unmanaged target must remain unchanged'
            after_conflict=tree(game)
            # Parent validation may create empty destination folders. The
            # collision still must leave every actual file byte unchanged.
            assert {key:value for key,value in after_conflict.items() if value!='<directory>'}=={
                key:value for key,value in conflict_tree.items() if value!='<directory>'}
            report['conflict_added_empty_directories']=[key for key,value in after_conflict.items()
                if key not in conflict_tree and value=='<directory>']
            value=measurement_end(window,'install-conflict',before);value['visible_error']=message
            screenshot(window,'unmanaged-conflict-readable',element('mod-error'))
            # This one fixture-owned unmanaged file was created above, and is
            # removed to model the user resolving that specific collision.
            assert conflict.is_relative_to(game) and conflict.read_bytes()==b'owned unmanaged target must remain unchanged'
            conflict.unlink()
            click(window,element('mod-refresh'));ready(window)
            perform(window,release,'install','enabled')
            perform(window,release,'disable','disabled')
            perform(window,release,'enable','enabled')
            screenshot(window,'enabled-before-close',row_for(release))
            report['checks'].append('Actual Chinese-directory preview/commit is passive; unmanaged collision is visible and preserved; retry installs, Space disables and Enter enables actual files')
            report['checks'].append('Native Tab reaches primary buttons, trusted Enter/Space activate them, and Escape closes details with focus restored')
        preserved()
        report['trusted_keys']=window.evaluate_js('window.__keys')
        report['trusted_clicks']=window.evaluate_js('window.__clicks')
        assert all(row['trusted'] for row in report['trusted_keys'])
        assert any(row['key']=='Tab' for row in report['trusted_keys'])
        report['status']='PASS'
    except Exception:
        report['error']=traceback.format_exc()
        try:
            report['failure_document']=window.evaluate_js('document.body.innerText')
            report['trusted_keys']=window.evaluate_js('window.__keys')
            screenshot(window,'failure',element('workspace-mods'))
        except Exception:
            pass
    finally:
        window.destroy()


def create(*values,**options):
    options.update(width=1280,height=720)
    window=original_create(*values,**options)
    window.create_confirmation_dialog=lambda *args:(_ for _ in ()).throw(AssertionError('No native approval for ordinary management'))
    windows.append(window)
    return window


def start(*values,**options):
    return original_start(observe,**options)


webview.create_window,webview.start=create,start
report['app_exit_code']=main.main(['--psmm-data-root',str(data)])
with managers[0]._lock:
    report['cleanup']=dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),subscriptions=len(managers[0]._subscriptions))
report['webview_lifecycle']=windows[0]._psmm_callback_lifecycle.stats
if any(report['cleanup'].values()):
    report['status']='FAIL'
(out/(args.phase+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status=report['status'],phase=args.phase,error=report.get('error')),ensure_ascii=False))
faulthandler.dump_traceback_later(15,repeat=True)
raise SystemExit(0 if report['status']=='PASS' and report['app_exit_code']==0 else 1)
