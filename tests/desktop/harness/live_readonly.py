"""One actual product WebView workflow for synthetic and authorized live inputs."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from scripts.verification.readonly import (load_authorization,validate_authorization,ReadonlyAudit,
    REQUIRED_FILES,TREES,EVIDENCE_FIELDS,snapshot_authorized,_check_path)

parser=argparse.ArgumentParser()
parser.add_argument('--evidence-root',required=True)
parser.add_argument('--mode',choices=['synthetic','real-readonly'],required=True)
args=parser.parse_args()
out=Path(args.evidence_root).absolute()
if not out.is_relative_to(ROOT/'docs/development/evidence/PS-01-07'):
    raise ValueError('READONLY_ISOLATED_EVIDENCE_REQUIRED')
_check_path(out);out.mkdir(parents=True,exist_ok=True)
if args.mode=='real-readonly':
    authorization=load_authorization()
else:
    from tests.desktop.harness.workbench_fixture import create
    game=create(out)
    (game/'Mods/ContentOnly.dll').rename(game/'Mods/PSItemPack_SanmuShiwushechipinPack1.dll')
    (game/'Mods/ShadowLow.dll').rename(game/'Mods/RatColonyOptimizer.dll')
    authorization=dict(task_id='PS-01-07',profile='real-readonly',operations=['read'],network=False,upload=False,
        valid_until='2099-01-01T00:00:00Z',excluded_data=['saves','credentials','personal_data'],
        budget=dict(max_files=5000,max_bytes=1024**3,max_seconds=120),resources=dict(installation_root=str(game),
        trees=list(TREES),files=list(REQUIRED_FILES),logs=[],evidence_fields=list(EVIDENCE_FIELDS),
        log_enumeration=['MelonLoader/Logs'],steam_manifests=[]))
    validate_authorization(authorization)
game=Path(authorization['resources']['installation_root'])
data=out/'isolated-data'
if game==data or game in data.parents or data in game.parents:
    raise ValueError('READONLY_SOURCE_DATA_OVERLAP')
guard=ReadonlyAudit(authorization)
guard.check_watch_scope()
before=snapshot_authorized(authorization)
for name,part in [('LOCALAPPDATA','local'),('APPDATA','roaming'),('TEMP','temp'),('TMP','temp')]:
    os.environ[name]=str(out/part);(out/part).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']='--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'
import webview
import win32gui
import win32ui
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi

report=dict(status='FAIL',mode=args.mode,ui_real_readonly='not_run',requests=[],checks=[],screenshots=[],
            source_before=before,source_after=None,game_started=False,network=False,upload=False,
            fixture_origin='self-authored' if args.mode=='synthetic' else 'explicitly authorized actual installation')
contexts=[];managers=[];windows=[];evidence={}
original_create,original_start=webview.create_window,webview.start


def factory(context):
    contexts.append(context);managers.append(context.jobs)
    context.installations.allow_manual_manifest_probe=bool(authorization['resources'].get('steam_manifests'))
    api=DesktopApi(context);dispatch=api.dispatch
    def observed(request):
        command=request.get('command')
        if command=='logs.watch':
            guard.check_watch_scope()
            if request.get('payload',{}).get('user_logs'):
                raise ValueError('READONLY_USER_LOGS_NOT_SELECTED')
        response=dispatch(request);value=response.get('data') or {}
        report['requests'].append(dict(command=command,ok=response.get('ok'),error=response.get('error',{}).get('code')))
        if response.get('ok'):
            if command in ('inventory.list','inventory.detail','diagnostics.explain'):
                evidence.setdefault(command,[]).append(value)
            elif command=='evidence.read':
                evidence.setdefault(command,[]).append({k:value.get(k) for k in ('evidence_id','excerpt_hash','byte_start','byte_end','source_id','epoch','total')})
            elif command in ('task.snapshot','task.events','task.subscribe'):
                snapshot=value.get('snapshot',value)
                result=snapshot.get('data') or {}
                if result.get('fingerprint'):report['current_fingerprint']=result['fingerprint']
        return response
    api.dispatch=observed
    return api


def wait_for(window,script,seconds=120):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=window.evaluate_js(script)
        if value:return value
        time.sleep(.05)
    raise AssertionError('READONLY_UI_TIMEOUT:'+script[:120])


def click(window,name):
    wait_for(window,"(()=>{const e=document.querySelector('[data-testid="+name+"]');return e&&!e.disabled})()")
    window.evaluate_js("document.querySelector('[data-testid="+name+"]').click()")


def screenshot(window,name):
    hwnd=window.native.Handle.ToInt64()
    left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,right-left,bottom-top)
    previous=memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/(name+'.png'))
        report['screenshots'].append(name+'.png')
    finally:
        memory.SelectObject(previous);win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def select_detail(window,name):
    window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=inventory-search]');e.value="+json.dumps(name)+";e.dispatchEvent(new Event('input',{bubbles:true}))})()")
    expression="Array.from(document.querySelectorAll('[data-testid=inventory-row]')).find(e=>e.innerText.includes("+json.dumps(name)+"))"
    wait_for(window,'Boolean('+expression+')');window.evaluate_js(expression+'.click()')
    wait_for(window,"document.querySelector('[data-testid=component-detail]')?.innerText.includes("+json.dumps(name)+')')


def observe(window):
    try:
        assert window.events.loaded.wait(30)
        wait_for(window,"Boolean(document.querySelector('[data-testid=scan-inventory]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)')==['dispatch']
        click(window,'installation-open');click(window,'installation-manual');click(window,'installation-select')
        wait_for(window,"!document.querySelector('dialog[open]')")
        report['checks'].append('actual visible selection and product discovery/select')
        click(window,'scan-inventory')
        wait_for(window,"['succeeded','failed','cancelled'].includes(document.querySelector('[data-testid=task-status]')?.dataset.state)")
        assert window.evaluate_js("document.querySelector('[data-testid=task-status]')?.dataset.scanStatus")=='complete','READONLY_SCAN_NOT_COMPLETE'
        report['checks'].append('actual scan -> LPAC helper -> product SQLite')
        screenshot(window,'live-overview')
        for name,label in [('PSItemPack_SanmuShiwushechipinPack1','content'),('RatColonyOptimizer','shadow')]:
            select_detail(window,name)
            expected='框架内容包' if label=='content' else '预计会优先加载高版本'
            wait_for(window,"document.querySelector('[data-testid=component-detail]')?.innerText.includes("+json.dumps(expected)+')')
            layers=wait_for(window,"document.querySelector('[data-testid=three-layer-state]')?.innerText")
            assert all(word in layers for word in ('配置','部署','运行'))
            screenshot(window,'live-'+label)
            report['checks'].append('visible search/detail/three layers: '+label)
        if args.mode=='real-readonly':
            select_detail(window,'WaterTower')
            wait_for(window,"document.querySelector('[data-testid=resource-coverage-warning]')?.innerText.includes('部分读取')")
            screenshot(window,'live-resource-coverage')
            report['checks'].append('real optional resource limit is visible without losing assembly identity')
        click(window,'diagnostics-tab');click(window,'refresh-diagnostics')
        if args.mode=='real-readonly':
            wait_for(window,"document.querySelector('[data-testid=historical-reference]')?.innerText.includes('2026-09-24')")
            report['checks'].append('visible historical reference date bound to exact original log hash')
        wait_for(window,"Boolean(document.querySelector('[data-testid=diagnostic-evidence]'))")
        click(window,'diagnostic-evidence')
        text=wait_for(window,"document.querySelector('[data-testid=evidence-window]')?.innerText")
        assert evidence.get('evidence.read')
        report['evidence_display_sha256']=hashlib.sha256(text.encode('utf-8')).hexdigest()
        report['checks'].append('visible diagnostic evidence link -> immutable stored byte window')
        # Screenshots prove navigation/layout; original log text is not a shareable artifact.
        window.evaluate_js("""(()=>{const e=document.querySelector('[data-testid=evidence-window]');
          e.style.visibility='hidden';const notice=document.createElement('p');
          notice.textContent='日志内容已遮盖；证据 ID 与片段 SHA256 见本地报告。';e.before(notice);
          window.__psmmRedactedPaintReady=false;
          requestAnimationFrame(()=>requestAnimationFrame(()=>{window.__psmmRedactedPaintReady=true}));})()""")
        wait_for(window,'window.__psmmRedactedPaintReady')
        screenshot(window,'live-evidence-redacted')
        report['ui_real_readonly']='passed';report['status']='PASS'
    except Exception as exc:
        report['error_type']=type(exc).__name__
        report['failure_location']=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)]
    finally:window.destroy()


def create_window(*args,**kwargs):
    kwargs.update(width=1280,height=720)
    window=original_create(*args,**kwargs);windows.append(window);return window


def start(*args,**kwargs):
    contexts[0].choose_installation_folder=lambda kind:str(game) if kind=='manual' else None
    return original_start(lambda:observe(windows[0]),**kwargs)


composition.DesktopApi=factory
webview.create_window,webview.start=create_window,start
sys.addaudithook(guard)
code=1
try:code=main.main(['--psmm-data-root',str(data)])
finally:
    report['app_exit_code']=code
    report['product_evidence']=evidence
    report['observed_reads']=sorted(guard.reads);report['observed_enumerations']=sorted(guard.enumerations)
    report['denied_operations']=guard.denied
    try:report['source_after']=snapshot_authorized(authorization)
    except Exception as exc:report['after_error']=type(exc).__name__;report['status']='FAIL'
    try:report['retained_dlls']=[p.relative_to(data).as_posix() for p in data.rglob('*.dll')]
    except Exception as exc:report['cleanup_error']=type(exc).__name__;report['retained_dlls']=['inspection_failed']
    if managers:
        with managers[0]._lock:
            report['cleanup']=dict(active_workers=sum(j.thread.is_alive() for j in managers[0]._jobs.values()),subscriptions=len(managers[0]._subscriptions))
    if code or guard.denied or report['retained_dlls'] or report['source_before']!=report['source_after'] or any(report.get('cleanup',{}).values()):
        report['status']='FAIL'
    (out/'live-readonly.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status=report['status'],checks=len(report['checks']),error_type=report.get('error_type'))))
raise SystemExit(0 if report['status']=='PASS' else 1)
