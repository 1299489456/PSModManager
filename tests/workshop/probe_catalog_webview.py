"""Explicit WebView acceptance: recorded official metadata, controlled slow reads, no SDK writes."""
import argparse
from copy import deepcopy
import ctypes
import json
from pathlib import Path
import re
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app import composition
from backend.workshop import WorkshopError
from backend.workshop.cache import PublicCache
from tests.desktop.harness.workshop_fixture import prepare_workshop
import webview
import win32gui
import win32ui
from PIL import Image


def run(out, recorded):
    context, fixture = prepare_workshop(out)
    cache = PublicCache(recorded)
    hot = cache.get(('browse', '', 1, 'hot'))[1]
    recent = cache.get(('browse', '', 1, 'recent'))[1]
    context.workshop.cache.put(('browse', '', 1, 'hot'), hot, updated=time.time()-600)
    context.workshop._remember_items(hot['items'])
    gates = {name: threading.Event() for name in ('hot', 'recent', 'new')}
    calls = []
    def browse(**args):
        calls.append(deepcopy(args))
        assert gates[args['sort']].wait(120)
        if args['sort'] == 'new':
            raise WorkshopError('WORKSHOP_NETWORK_ERROR', '公开目录网络超时（隔离验收）', retryable=True)
        return deepcopy(hot if args['sort'] == 'hot' else recent)
    context.workshop.client.browse = browse
    report = dict(status='FAIL', scope='Actual WebView2/DesktopApi/service; recorded official public data; controlled slow HTTP and simulated SDK; no account actions', checks=[])
    report['frontend_assets'] = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', (ROOT/'frontend/dist/index.html').read_text(encoding='utf-8'))
    original_create, original_start = webview.create_window, webview.start
    windows = []
    def wait(window, js, timeout=40):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=window.evaluate_js(js)
            if value:
                return value
            time.sleep(.05)
        raise AssertionError('UI timeout: '+js)
    def text_button(window, label):
        js='Array.from(document.querySelectorAll("button")).find(b=>b.textContent.trim()==='+json.dumps(label)+')'
        wait(window,js+'&&!'+js+'.disabled')
        window.evaluate_js(js+'.click()')
    def screenshot(window, label):
        time.sleep(.35)  # Let WebView2 paint the DOM state before PrintWindow.
        hwnd=window.native.Handle.ToInt64()
        left,top,right,bottom=win32gui.GetWindowRect(hwnd)
        dc=win32gui.GetWindowDC(hwnd);source=win32ui.CreateDCFromHandle(dc);memory=source.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap();bitmap.CreateCompatibleBitmap(source,right-left,bottom-top);old=memory.SelectObject(bitmap)
        try:
            assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)
            Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/(label+'.png'))
        finally:
            memory.SelectObject(old);win32gui.DeleteObject(bitmap.GetHandle());memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)
    def observe():
        window=windows[0]
        try:
            wait(window,'document.querySelectorAll(".row[data-id]").length>0&&document.querySelector("[data-testid=mods-update-all]")&&!document.querySelector("[data-testid=mods-update-all]").disabled')
            window.evaluate_js('window.__uiErrors=[];addEventListener("error",e=>__uiErrors.push(e.message));addEventListener("unhandledrejection",e=>__uiErrors.push(String(e.reason)));document.querySelector("[data-testid=workshop-open]").click()')
            wait(window,'document.querySelectorAll(".ws-card").length===24')
            wait(window,'document.querySelector("[data-testid=workshop-connection-status]")?.textContent.includes("已连接本机 Steam")')
            catalog=wait(window,'document.querySelector("[data-testid=workshop-catalog-status]")?.textContent')
            assert '正在后台更新公开目录' in catalog and '重新连接' not in catalog
            wait(window,'Array.from(document.querySelectorAll(".ws-card-actions button")).some(b=>b.textContent.includes("订阅")&&!b.disabled)')
            screenshot(window,'connected-stale-catalog')
            report['checks'].append('SDK ready and subscription buttons usable while stale public catalog refreshes; no reconnect error')
            gates['hot'].set()
            wait(window,'!document.querySelector("[data-testid=workshop-catalog-status]")')
            first_hot=window.evaluate_js('document.querySelector(".ws-body b").textContent')
            start=time.perf_counter();text_button(window,'最近更新')
            wait(window,'document.querySelector("[data-testid=workshop-panel] .garden-chips .on")?.textContent.trim()==="最近更新"&&document.querySelectorAll(".ws-card").length===0')
            report['cold_filter_visible_seconds']=round(time.perf_counter()-start,4)
            assert report['cold_filter_visible_seconds']<1
            start=time.perf_counter();text_button(window,'最热门')
            wait(window,'document.querySelectorAll(".ws-card").length===24')
            report['cached_filter_visible_seconds']=round(time.perf_counter()-start,4)
            assert report['cached_filter_visible_seconds']<1
            gates['recent'].set();time.sleep(1)
            assert window.evaluate_js('document.querySelector("[data-testid=workshop-panel] .garden-chips .on").textContent.trim()')=='最热门'
            assert window.evaluate_js('document.querySelector(".ws-body b").textContent')==first_hot
            report['checks'].append('Cold filter selection is immediate; cached return is immediate; obsolete result cannot overwrite latest filter')
            text_button(window,'最新发布');gates['new'].set()
            wait(window,'document.querySelector("[data-testid=workshop-catalog-status]")?.textContent.includes("公开目录读取失败")')
            assert '已连接本机 Steam' in window.evaluate_js('document.querySelector("[data-testid=workshop-connection-status]").textContent')
            assert not window.evaluate_js('document.body.innerText.includes("重新连接")')
            screenshot(window,'catalog-error-not-session-error')
            report['checks'].append('Real transport error field remains visible as a catalog-only error; Steam stays ready')
            text_button(window,'最热门');wait(window,'document.querySelectorAll(".ws-card").length===24')
            assert not window.evaluate_js('document.querySelector("[data-testid=workshop-catalog-status]")')
            screenshot(window,'cached-catalog-ready')
            report['page_errors']=window.evaluate_js('window.__uiErrors')
            assert not report['page_errors'] and not context._ui_steam_fixture.calls
            report.update(status='PASS',steam_calls=[],catalog_requests=calls)
        except Exception:
            report['error']=traceback.format_exc()
            report['visible_text']=window.evaluate_js('document.body.innerText')
            screenshot(window,'failure')
        finally:
            for gate in gates.values():gate.set()
            window.destroy()
    def create(title,**options):
        window=original_create(title+' · 目录只读验收',**options);windows.append(window);return window
    context.initialize=lambda:None
    composition.DesktopContext=lambda _:context
    webview.create_window=create
    webview.start=lambda *args,**kwargs:original_start(observe,**kwargs)
    report['exit_code']=composition.run_desktop(str(context.paths.root))
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence-root',type=Path,required=True)
    parser.add_argument('--recorded-cache-root',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(run(args.evidence_root.resolve(),args.recorded_cache_root.resolve()))
