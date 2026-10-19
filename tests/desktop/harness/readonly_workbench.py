"""Actual bundled workbench clicks -> real bridge/services/SQLite/WebView2.

Only the native folder chooser returns a controlled synthetic fixture path.
The observer records responses but never substitutes product results.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--evidence-root', required=True)
parser.add_argument('--phase', choices=['first', 'reopen'], required=True)
args = parser.parse_args()
out = Path(args.evidence_root).resolve()
assert any(out.is_relative_to(ROOT / 'docs/development/evidence' / task) for task in ('PS-01-07', 'PS-02-05'))
from backend.app.paths import reject_reparse
reject_reparse(out)
out.mkdir(parents=True, exist_ok=True)
for name, relative in [('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'), ('TEMP', 'temp'), ('TMP', 'temp')]:
    os.environ[name] = str(out / relative)
    (out / relative).mkdir(exist_ok=True)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = '--proxy-server=127.0.0.1:9 --proxy-bypass-list=<-loopback> --disable-background-networking --disable-component-update'

from tests.desktop.harness.workbench_fixture import create as create_fixture, hashes
import webview
import win32gui
import win32ui
import win32file
import win32con
from PIL import Image
import main
from backend.app import composition
from backend.app.api import DesktopApi

game = create_fixture(out) if args.phase == 'first' else out / '合成安装 %'
data = out / 'isolated-data'
report = dict(status='FAIL', phase=args.phase, checks=[], requests=[], progress=[],
              source_before=hashes(game), data_root=str(data), screenshots=[],
              scope='actual DOM clicks -> real product bridge/service/worker/SQLite; only native folder selection injected')
contexts, managers, windows = [], [], []
document_loads = 0
original_create, original_start = webview.create_window, webview.start


def factory(context):
    contexts.append(context)
    managers.append(context.jobs)
    api = DesktopApi(context)
    dispatch = api.dispatch

    def observed(request):
        started = time.monotonic()
        response = dispatch(request)
        data = response.get('data') or {}
        report['requests'].append(dict(command=request.get('command'), ok=response['ok'],
            request_id=response.get('request_id'), error_code=response.get('error', {}).get('code'),
            elapsed_ms=round((time.monotonic()-started)*1000, 3),
            task_id=data.get('task_id'), state=data.get('state'), status=data.get('status'),
            total=data.get('total'), generation=data.get('generation'), reused=data.get('reused')))
        if request.get('command') in ('task.snapshot', 'task.events', 'task.subscribe'):
            for event in data.get('events', []):
                report['progress'].append({key:event.get(key) for key in ('task_id', 'sequence', 'state', 'stage', 'completed', 'total', 'message_code')})
        return response

    api.dispatch = observed
    return api


composition.DesktopApi = factory


def audit(event, values):
    if event in ('socket.bind', 'socket.connect'):
        raise PermissionError('No Python business networking in workbench harness')
    if event == 'open':
        path = str(values[0]).replace('\\', '/').lower()
        if path.startswith(('e:/workspace/github/rimcrow/', 'e:/entertainment/steam/')):
            raise PermissionError('No real reference/game access')


sys.addaudithook(audit)


def wait_for(window, script, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = window.evaluate_js(script)
        if result: return result
        time.sleep(.05)
    raise AssertionError('UI timeout: ' + script[:160])


def click(window, testid):
    wait_for(window, f"(()=>{{const e=document.querySelector('[data-testid={testid}]');return e&&!e.disabled&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'}})()")
    if testid=='scan-inventory':
        window.evaluate_js("""(()=>{window.__previousScanTask=document.querySelector('[data-testid=task-status]')?.dataset.taskId;
          window.__scanFeedback=null;const started=performance.now();
          const button=document.querySelector('[data-testid=scan-inventory]');
          const observer=new MutationObserver(()=>{if(button.disabled){window.__scanFeedback=performance.now()-started;observer.disconnect()}});
          observer.observe(document.body,{subtree:true,attributes:true,childList:true});button.click()})()""")
        elapsed=wait_for(window,'window.__scanFeedback!==null ? {ms:window.__scanFeedback}:null')['ms']
        report.setdefault('button_feedback_ms',[]).append(elapsed)
        assert elapsed<=100, f'Scan feedback {elapsed}ms exceeds 100ms'
    else:
        window.evaluate_js(f"(()=>{{const e=document.querySelector('[data-testid={testid}]');e.focus();e.click()}})()")


def check(name):
    report['checks'].append(dict(name=name, status='PASS'))


def screenshot(window, name):
    # DOM presence precedes WebView compositor paint. Wait for two browser
    # frames and a short composition interval before capturing the native HWND.
    token=str(time.monotonic_ns())
    window.evaluate_js("""(()=>{const token="""+json.dumps(token)+""";
      window.__psmmCaptureReady=null;
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
        const popup=document.querySelector('[data-testid=theme-popup]');
        const rect=popup?.getBoundingClientRect();
        window.__psmmCaptureReady={token,paint_time:performance.now(),popup:popup?{
          display:getComputedStyle(popup).display,visibility:getComputedStyle(popup).visibility,
          opacity:getComputedStyle(popup).opacity,x:rect.x,y:rect.y,width:rect.width,height:rect.height}:null};
      }));})()""")
    barrier=wait_for(window,"window.__psmmCaptureReady?.token==="+json.dumps(token)+" ? window.__psmmCaptureReady : null",5)
    if name.endswith('theme-popup'):
        assert barrier['popup'] and barrier['popup']['width']>0 and barrier['popup']['height']>0,barrier
        assert barrier['popup']['visibility']=='visible' and barrier['popup']['opacity']=='1',barrier
    time.sleep(.18)
    report.setdefault('capture_barriers',[]).append(dict(name=name,**barrier,composition_wait_ms=180))
    hwnd = window.native.Handle.ToInt64()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right-left, bottom-top
    dc = win32gui.GetWindowDC(hwnd)
    source = win32ui.CreateDCFromHandle(dc)
    memory = source.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source, width, height)
    previous = memory.SelectObject(bitmap)
    filename = f'{args.phase}-{name}.png'
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd, memory.GetSafeHdc(), 2) == 1
        Image.frombuffer('RGB', (width,height), bitmap.GetBitmapBits(True), 'raw', 'BGRX', 0, 1).save(out / filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def search_select(window, name):
    window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=inventory-search]');e.value=" + json.dumps(name) + ";e.dispatchEvent(new Event('input',{bubbles:true}))})()")
    wait_for(window, "Array.from(document.querySelectorAll('[data-testid=inventory-row]')).some(e=>e.innerText.includes(" + json.dumps(name) + '))')
    window.evaluate_js("Array.from(document.querySelectorAll('[data-testid=inventory-row]')).find(e=>e.innerText.includes(" + json.dumps(name) + ')).click()')
    wait_for(window, "document.querySelector('[data-testid=component-detail]')?.innerText.includes(" + json.dumps(name) + ')')


def task_state(window, state, timeout=120):
    return wait_for(window, "(()=>{const e=document.querySelector('[data-testid=task-status]');return e?.dataset.state===" + json.dumps(state) + ' && e.dataset.taskId!==window.__previousScanTask && e.dataset.taskId})()', timeout)


def observe(window):
    locked = None
    try:
        assert window.events.loaded.wait(30)
        wait_for(window, "Boolean(document.querySelector('[data-testid=workspace-inventory]'))")
        click(window, 'workspace-inventory')
        wait_for(window, "Boolean(document.querySelector('[data-testid=scan-inventory]'))")
        assert window.evaluate_js('Object.keys(window.pywebview.api)') == ['dispatch']
        from System import Action
        from System.Drawing import Size
        window.native.Invoke(Action(lambda:setattr(window.native,'ClientSize',Size(1280,720))))
        hwnd = window.native.Handle.ToInt64()
        report['display'] = dict(os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)),
                                window_rect=list(win32gui.GetWindowRect(hwnd)),
                                client_rect=list(win32gui.GetClientRect(hwnd)),
                                webview=window.evaluate_js('({width:innerWidth,height:innerHeight,devicePixelRatio})'),
                                os_dpi_matrix='125/150/200 percent OS DPI not changed or claimed verified')
        if args.phase == 'first':
            click(window,'installation-open');click(window,'installation-manual');click(window,'installation-select')
            wait_for(window,"!document.querySelector('dialog[open]')")
            wait_for(window,"document.activeElement?.dataset.testid==='installation-open'")
            check('actual native-picker boundary then real discovery/select through visible UI')
            click(window,'scan-inventory')
            cancel_started=time.monotonic()
            click(window,'cancel-task');task_state(window,'cancelled')
            report['cancel_terminal_ms']=round((time.monotonic()-cancel_started)*1000,3)
            assert report['cancel_terminal_ms']<=5000
            check('visible scan cancellation reaches real job terminal')
            click(window,'scan-inventory');task_state(window,'succeeded')
        else:
            click(window,'installation-open')
            wait_for(window,"Array.from(document.querySelectorAll('dialog[open] button')).some(e=>e.innerText.trim()==='校验并使用'&&!e.disabled)")
            window.evaluate_js("Array.from(document.querySelectorAll('dialog[open] button')).find(e=>e.innerText.trim()==='校验并使用').click()")
            wait_for(window,"!document.querySelector('dialog[open]')")
            wait_for(window,"document.querySelector('[data-testid=installation-open]')?.innerText.includes('Probably Stolen')")
            assert any(row['command']=='installation.use' and row['ok'] for row in report['requests'])
            check('application restart explicitly revalidates saved installation through visible use action')
        wait_for(window,"document.querySelectorAll('[data-testid=inventory-row]').length>0")
        screenshot(window,'overview')
        window.evaluate_js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'f',ctrlKey:true,bubbles:true,cancelable:true}))")
        assert window.evaluate_js("document.activeElement?.dataset.testid")=='inventory-search'
        window.evaluate_js("(()=>{const e=document.querySelector('[data-testid=inventory-list]');e.focus();e.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}))})()")
        wait_for(window,"document.querySelector('[data-testid=component-detail]')?.innerText.includes('Unique41')")
        report['keyboard_input']='WebView DOM KeyboardEvent invokes actual product handlers; not OS-wide SendInput'
        check('real WebView Ctrl+F focus and virtual list End navigation; selector restores focus')
        search_select(window,'ContentOnly')
        wait_for(window,"document.querySelector('[data-testid=content-explanation]')?.innerText.includes('框架内容包')")
        wait_for(window,"document.querySelector('[data-testid=component-detail]')?.innerText.includes('ItemForge 内容包')")
        layers=wait_for(window,"document.querySelector('[data-testid=three-layer-state]')?.innerText")
        assert all(word in layers for word in ('配置','部署','运行'))
        screenshot(window,'content-detail')
        check('real inventory search and content-only detail retain three separate state layers')
        window.evaluate_js("Array.from(document.querySelectorAll('.detail-tabs button')).find(e=>e.innerText.trim()==='内容').click()")
        wait_for(window,"(()=>{const e=document.querySelector('[data-testid=component-detail]');return e?.innerText.includes('ItemForge.items.json')&&e.innerText.includes('所需接口版本：19')})()")
        screenshot(window,'content-manifest')
        check('actual inventory.detail response renders ItemForge role and embedded manifest RequiredRuntimeApi 19')
        window.evaluate_js("Array.from(document.querySelectorAll('.detail-tabs button')).find(e=>e.innerText.trim()==='详情').click()")
        search_select(window,'ShadowLow')
        wait_for(window,"document.querySelector('[data-testid=shadow-explanation]')?.innerText.includes('预计会优先加载高版本')")
        screenshot(window,'shadow-detail')
        check('lower AssemblyVersion selection explained without deleting files')
        click(window,'diagnostics-tab')
        click(window,'refresh-diagnostics')
        wait_for(window,"Boolean(document.querySelector('[data-testid=diagnostic-evidence]'))",45)
        click(window,'diagnostic-evidence')
        text=wait_for(window,"document.querySelector('[data-testid=evidence-window]')?.innerText")
        assert '2026-09-24' in text
        screenshot(window,'evidence-window')
        check('diagnostic evidence ID opens actual immutable original synthetic historical bytes')
        click(window,'theme-toggle')
        wait_for(window,"document.activeElement?.getAttribute('role')==='menuitemradio'")
        report['theme_menu']=window.evaluate_js("""(()=>{const menu=document.querySelector('[data-testid=theme-popup]');return {
          background:getComputedStyle(menu).backgroundColor,color:getComputedStyle(menu).color,
          native_select:!!document.querySelector('select[aria-label="界面主题"]'),
          option_count:menu.querySelectorAll('[role=menuitemradio]').length}})()""")
        assert report['theme_menu']['native_select'] is False and report['theme_menu']['option_count']==3
        assert report['theme_menu']['background'].startswith('rgb('),report['theme_menu']
        screenshot(window,'dark-theme-popup')
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}))")
        wait_for(window,"!document.querySelector('[data-testid=theme-popup]')&&document.activeElement?.dataset.testid==='theme-toggle'")
        click(window,'theme-toggle')
        wait_for(window,"document.activeElement?.getAttribute('role')==='menuitemradio'")
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true}))")
        wait_for(window,"document.activeElement?.dataset.testid==='theme-option-win11-mica'")
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))")
        wait_for(window,"document.documentElement.dataset.theme==='win11-mica'")
        wait_for(window,"document.activeElement?.dataset.testid==='theme-toggle'")
        report['readability']=window.evaluate_js("""(()=>{const row=document.querySelector('.row-label strong');return {
          font_family:getComputedStyle(document.documentElement).fontFamily,
          row_font_px:parseFloat(getComputedStyle(row).fontSize),
          evidence_font_px:parseFloat(getComputedStyle(document.querySelector('[data-testid=evidence-window]')).fontSize),
          theme:document.documentElement.dataset.theme}})()""")
        assert report['readability']['row_font_px']>=12 and report['readability']['evidence_font_px']>=12
        assert report['readability']['font_family'].startswith('"Segoe UI Variable"'),report['readability']
        status_metrics=window.evaluate_js("""Array.from(document.querySelectorAll('.row-status')).map(e=>({
          text:e.innerText,white_space:getComputedStyle(e).whiteSpace,height:e.getBoundingClientRect().height,
          row_height:e.closest('.inventory-row').getBoundingClientRect().height}))""")
        assert all(row['white_space']=='nowrap' and row['height']<row['row_height'] for row in status_metrics),status_metrics
        report['readability']['row_status']=status_metrics
        report['readability']['light_warning_contrast']=window.evaluate_js("""(()=>{
          const rgba=value=>{const n=value.match(/[\\d.]+/g).map(Number);return [n[0],n[1],n[2],n[3]??1]};
          const luminance=rgb=>rgb.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
          return [...document.querySelectorAll('.warning')].filter(e=>e.getClientRects().length).map(e=>{
            const chain=[];for(let p=e;p;p=p.parentElement)chain.unshift(p);
            let bg=[255,255,255];for(const p of chain){const c=rgba(getComputedStyle(p).backgroundColor);bg=bg.map((v,i)=>c[i]*c[3]+v*(1-c[3]))}
            const fg=rgba(getComputedStyle(e).color).slice(0,3),a=luminance(fg),b=luminance(bg);
            return {text:e.innerText,foreground:fg,background:bg,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
          })})()""")
        assert report['readability']['light_warning_contrast']
        assert all(item['ratio']>=4.5 for item in report['readability']['light_warning_contrast']),report['readability']['light_warning_contrast']
        screenshot(window,'light-theme-evidence')
        click(window,'theme-toggle')
        wait_for(window,"document.activeElement?.getAttribute('role')==='menuitemradio'")
        screenshot(window,'light-theme-popup')
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true}))")
        wait_for(window,"document.activeElement?.dataset.testid==='theme-option-obsidian-cyan'")
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}))")
        wait_for(window,"document.documentElement.dataset.theme==='obsidian-cyan'")
        click(window,'theme-toggle')
        wait_for(window,"document.activeElement?.getAttribute('role')==='menuitemradio'")
        window.evaluate_js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}))")
        wait_for(window,"!document.querySelector('[data-testid=theme-popup]')&&document.activeElement?.classList.contains('info-button')")
        window.evaluate_js("""(()=>{const input=document.querySelector('[data-testid=inventory-search]');input.value='';input.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        wait_for(window,"document.querySelector('[data-testid=inventory-list]').scrollHeight>document.querySelector('[data-testid=inventory-list]').clientHeight")
        window.evaluate_js("""(()=>{const list=document.querySelector('[data-testid=inventory-list]');list.scrollTop=list.scrollHeight;list.dispatchEvent(new Event('scroll'))})()""")
        wait_for(window,"Array.from(document.querySelectorAll('[data-testid=inventory-row]')).some(e=>e.innerText.includes('Unique41'))")
        check('visible theme change retains focus; readable text sizes and actual virtual scrolling verified')
        if args.phase == 'first':
            locked = win32file.CreateFile(str(game/'Mods/ContentOnly.dll'),win32con.GENERIC_READ,0,None,win32con.OPEN_EXISTING,0,None)
            click(window,'scan-inventory');task_state(window,'succeeded')
            wait_for(window,"document.querySelector('[data-testid=task-status]')?.dataset.scanStatus==='partial'")
            screenshot(window,'partial-scan')
            locked.Close();locked=None
            check('real locked DLL produces visible partial scan without replacing complete inventory')
            click(window,'scan-inventory');task_state(window,'succeeded')
            wait_for(window,"document.querySelector('[data-testid=task-status]')?.dataset.scanStatus==='complete'")
            before_loads=document_loads
            window.load_html(window.html)
            deadline=time.monotonic()+30
            while document_loads<=before_loads and time.monotonic()<deadline:time.sleep(.05)
            assert document_loads>before_loads
            click(window, 'workspace-inventory')
            wait_for(window,"document.querySelectorAll('[data-testid=inventory-row]').length>0")
            deadline=time.monotonic()+15
            while not any(r['command']=='logs.watch' and r['reused'] for r in report['requests']) and time.monotonic()<deadline:time.sleep(.05)
            assert any(r['command']=='logs.watch' and r['reused'] for r in report['requests'])
            # The embedded Profile editor also reloads its real package list.
            # Verify quiescence after that visible busy state and its actual job
            # finish, rather than measuring its legitimate in-flight worker.
            wait_for(window, "document.querySelector('[data-testid=profile-editor]')?.dataset.busy==='false'")
            assert not window.evaluate_js("document.querySelector('[data-testid=profile-error]')?.innerText")
            watcher_ids={r['task_id'] for r in report['requests'] if r['command']=='logs.watch' and r['reused']}
            assert len(watcher_ids)==1,watcher_ids
            deadline=time.monotonic()+15
            while True:
                with managers[0]._lock:
                    jobs=[dict(task_id=j.task_id,state=j.current['state'],stage=j.current['stage'],alive=j.thread.is_alive()) for j in managers[0]._jobs.values()]
                active={j['task_id'] for j in jobs if j['alive']}
                settled=all(j['state'] in ('succeeded','failed','cancelled') for j in jobs if j['task_id'] not in watcher_ids)
                if active==watcher_ids and settled:break
                assert time.monotonic()<deadline,dict(watcher_ids=sorted(watcher_ids),jobs=jobs)
                time.sleep(.02)  # Poll the actual completion condition; no fixed grace period.
            report['reload_jobs']=jobs
            report['reload_active_workers']=len(active)
            assert report['reload_active_workers']==1
            check('document reopen restores real inventory and reuses one live log watcher')
        report['zoom_checks']=[]
        for factor in (1.25,1.5,2.0):
            window.native.Invoke(Action(lambda: setattr(window.native.browser.webview,'ZoomFactor',factor)))
            time.sleep(.15)
            layout=window.evaluate_js("""(()=>{const area=document.querySelector('.sidebar-scroll');const panel=document.querySelector('.diagnostics-column');return {
              width:innerWidth,height:innerHeight,devicePixelRatio,sidebar_height:area.clientHeight,
              sidebar_width:area.clientWidth,panel_height:panel.clientHeight,
              body_overflow:document.body.scrollWidth>innerWidth+1,
              columns:document.querySelectorAll('.rim-workspace>.rim-panel').length}})()""")
            assert layout['sidebar_height']>=90,layout
            assert layout['columns']==4 and not layout['body_overflow'],layout
            report['zoom_checks'].append(dict(webview_zoom=factor,os_dpi=int(ctypes.windll.user32.GetDpiForWindow(hwnd)),viewport=layout))
            screenshot(window,'webview-zoom-'+str(factor))
        window.native.Invoke(Action(lambda:setattr(window.native.browser.webview,'ZoomFactor',1.0)))
        check('WebView zoom screenshots recorded separately from unchanged OS DPI')
        if args.phase=='first':
            request_count=len(report['requests'])
            window.evaluate_js("location.hash='ui-performance-synthetic'")
            wait_for(window,"Boolean(document.querySelector('[data-testid=ui-performance-fixture]'))")
            click(window,'run-ui-performance')
            result=wait_for(window,'window.__psmmUiBenchmark',45)
            report['ui_performance']=result
            assert result['status']=='PASS' and result['sample_count']==5000 and result['iterations']==60,result
            assert result['p95_ms']<=200 and result['max_rendered_rows']<=50,result
            assert result['scroll_and_keyboard']=='PASS',result
            # Unmount may release existing subscriptions. The fixture must never
            # substitute inventory or use any product command to seed its 5000 rows.
            fixture_commands=[row['command'] for row in report['requests'][request_count:]]
            assert all(command in ('task.events','task.unsubscribe') for command in fixture_commands),fixture_commands
            report['ui_performance']['commands_during_fixture']=fixture_commands
            screenshot(window,'synthetic-5000-ui-performance')
            check('independent synthetic 5000-row fixture uses production VirtualInventory: DOM input to rendered frame p95 <=200ms')
        report['status']='PASS'
    except Exception as exc:
        report['error_type']=type(exc).__name__
        report['error']=str(exc)[:500]
        report['failure_location']=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in traceback.extract_tb(exc.__traceback__)]
        try:
            report['failure_document']=window.evaluate_js('document.body.innerText').__str__()[:12000]
            screenshot(window,'failure')
        except Exception: pass
    finally:
        if locked is not None: locked.Close()
        window.destroy()


def create(*args,**kwargs):
    def loaded():
        global document_loads
        document_loads+=1
    kwargs.update(width=1280,height=720)
    window=original_create(*args,**kwargs)
    window.events.loaded+=loaded
    windows.append(window)
    return window


def start(*args,**kwargs):
    contexts[0].choose_installation_folder=lambda kind:str(game) if kind=='manual' else None
    return original_start(lambda:observe(windows[0]),**kwargs)


webview.create_window,webview.start=create,start
code=main.main(['--psmm-data-root',str(data)])
report['app_exit_code']=code
report['source_after']=hashes(game)
with sqlite3.connect((data/'psmodmanager.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    pointer=db.execute('SELECT installation_id,latest_complete FROM inventory_current').fetchone()
    if pointer:
        rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM scan_entries WHERE installation_id=? AND generation=?',pointer)]
        report['persisted_inventory']=dict(total=len(rows),managed=sum(r.get('status')=='managed' for r in rows),
            candidates=sum(r.get('prediction',{}).get('status')=='candidate' for r in rows),generation=pointer[1])
        if len(rows)!=45 or report['persisted_inventory']['candidates']!=43:report['status']='FAIL'
    else:report['status']='FAIL'
with managers[0]._lock:
    report['cleanup']=dict(active_workers=sum(j.thread.is_alive() for j in managers[0]._jobs.values()),
                           subscriptions=len(managers[0]._subscriptions))
if report['source_before']!=report['source_after'] or any(report['cleanup'].values()):report['status']='FAIL'
else:check('source hashes unchanged and all workers/subscriptions released on desktop close')
(out/f'{args.phase}-workbench.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status=report['status'],checks=len(report['checks']),app_exit_code=code,error=report.get('error'))))
raise SystemExit(0 if code==0 and report['status']=='PASS' else 1)
