"""Actual WebView + localhost SSE + owned game; no external model or game run."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
parser=argparse.ArgumentParser()
parser.add_argument('--evidence-root',required=True)
args=parser.parse_args()
out=Path(args.evidence_root).resolve()
assert any(out.is_relative_to(ROOT/path) for path in ('docs/development/evidence/20261016-mod-sharing-analysis-online','docs/development/evidence/20261016-followup-fixes/ai','docs/development/evidence/20261016-garden-ui'))
out.mkdir(parents=True,exist_ok=True)
for key,name in [('LOCALAPPDATA','local'),('APPDATA','roaming'),('TEMP','temp'),('TMP','temp')]:
    (out/name).mkdir(exist_ok=True)
    os.environ[key]=str(out/name)
os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']='--disable-background-networking --disable-component-update'

import main
import webview
import win32gui
import win32ui
from PIL import Image
from backend.app import composition
from backend.games.probably_stolen import discovery
discovery.automatic_steam_roots=lambda: []
from tests.integration.harness.transaction_recovery import TransactionFixture,tree
from tests.integration.harness.ai_tool_provider import ScriptedAIProvider
from tests.integration.harness.adaptive_analysis_provider import AdaptiveDiagnosticScenario
from tests.unit.test_ai_privacy import MemoryCredentials

LONG_TEXT='# 分析结论\n\n这是本机合成服务提供的长回复，用于验证完整接收与显示。\n\n'+''.join(
    f'### 证据 {index+1}\n\nLatest.log 的合成异常需要结合 Mod 版本、依赖和实际加载记录核对。文件已启用不代表游戏内已加载。请先完成最小范围排查，再用新日志复测。\n\n' for index in range(180))+'长回复完整结束。'
report=dict(status='FAIL',checks=[],screenshots=[],input_method='Actual WebView DOM with visible hit-tested controls; not manual/DPI acceptance')
fixture=TransactionFixture(out/'fixture')
game,data=fixture.game,fixture.data
external=game/'Mods/ExternalAnalysis.dll'
external.write_bytes(b'owned-external-analysis-mod')
for index in range(3):
    (game/f'Mods/ZZAnalysisExtra{index}.dll').write_bytes(b'owned-extra-query-pagination-mod')
(game/'MelonLoader/Latest.log').write_text('[12:00:00] Probably Stolen / IL2CPP\n[12:00:01] ERROR ExternalAnalysis dependency missing\n',encoding='utf-8')
baseline=tree(game)
fixture.close()
query_scenario=AdaptiveDiagnosticScenario(suggest=True)
NARRATIVE_TEXT='## 详细分析\n\n关键异常 SyntheticDependencyNotFoundException，需要核对依赖程序集和当前游戏构建。\n\n'+('这是正常的分析正文，应与后续建议一并保留，而不是被简短收束覆盖。\n\n'*75)


def scripted(body,index):
    latest=next((row['content'] for row in reversed(body['messages']) if row['role']=='user'),'')
    if '[QUERIES]' in latest:
        return query_scenario(body,index)
    if '[NARRATIVE]' in latest:
        results=[json.loads(row['content']) for row in body['messages'] if row['role']=='tool']
        if not results:
            return dict(text='先核对当前环境与 Mod。',calls=[('get_installation_summary',{}),('get_mod_workspace',{})])
        if 'suggestion' in results[-1].get('data',{}):
            return dict(text='建议已准备，请确认后复测。')
        value=next(row['data'] for row in results if 'snapshot_id' in row.get('data',{}))
        row=next(item for item in value['items'] if item['display_name']=='ExternalAnalysis')
        return dict(text=NARRATIVE_TEXT,calls=[('suggest_workspace_action',dict(workspace_id=row['id'],snapshot_id=value['snapshot_id'],
            action='disable',reason='保留完整分析，并由用户确认是否单独停用复测。'))])
    if '[LONG]' in latest:
        return dict(text=LONG_TEXT)
    if '[TRUNCATED]' in latest:
        return dict(text='已定位到缺少程序集的异常。接下来需要',mode='length')
    if '刚才中断' in latest:
        return dict(text='继续分析完成：核对 Mod 的依赖和游戏构建，然后用相同场景复测。')
    if '[CANCEL]' in latest:
        return dict(text='停止前已接收到的分析内容。',mode='stall_after_text')
    if '[DISABLE]' in latest or '[ENABLE]' in latest:
        results=[json.loads(row['content']) for row in body['messages'] if row['role']=='tool']
        if not results:
            return dict(calls=[('get_mod_workspace',{})])
        value=results[-1]['data']
        if 'suggestion' in value:
            return dict(text='已准备具体 Mod 的操作建议，请确认后执行；其他 Mod 保持不变。')
        row=next(item for item in value['items'] if item['display_name']=='ExternalAnalysis')
        return dict(calls=[('suggest_workspace_action',dict(workspace_id=row['id'],snapshot_id=value['snapshot_id'],
            action='disable' if '[DISABLE]' in latest else 'enable',reason='隔离合成异常，保留恢复副本和其他 Mod。'))])
    return dict(text='合成分析已完成。')


contexts,windows,managers,auto_tasks=[],[],[],[]
original_api,original_create,original_start=composition.DesktopApi,webview.create_window,webview.start
def api_factory(context):
    context.ai.credentials._backend=MemoryCredentials()
    context.ai.save(dict(provider='openai_compatible',base_url=provider.base_url,model='localhost-analysis'))
    contexts.append(context)
    managers.append(context.jobs)
    api=original_api(context)
    dispatch=api.dispatch
    def observed(request):
        response=dispatch(request)
        if request.get('command')=='installation.auto_discover' and response.get('ok'):
            auto_tasks.append(response['data']['task_id'])
        return response
    api.dispatch=observed
    return api
composition.DesktopApi=api_factory


def element(name):
    return "document.querySelector('[data-testid="+name+"]')"


def wait(window,expression,timeout=45):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=window.evaluate_js(expression)
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('WebView timed out: '+expression[:260])


def click_selector(window,selector):
    wait(window,'Boolean('+selector+') && !('+selector+').disabled')
    window.evaluate_js('('+selector+').scrollIntoView({block:"center"})')
    wait(window,'(()=>{const e='+selector+';const r=e.getBoundingClientRect();const h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&(e===h||e.contains(h))})()')
    window.evaluate_js('('+selector+').click()')


def click(window,name):
    click_selector(window,element(name))


def fill(window,name,value):
    selector=element(name)
    wait(window,'Boolean('+selector+') && !'+selector+'.disabled')
    window.evaluate_js('(()=>{const e='+selector+';e.value='+json.dumps(value)+';e.dispatchEvent(new Event("input",{bubbles:true}))})()')


def shot(window,name):
    time.sleep(.35)
    hwnd=window.native.Handle.ToInt64()
    left,top,right,bottom=win32gui.GetWindowRect(hwnd)
    dc=win32gui.GetWindowDC(hwnd)
    source=win32ui.CreateDCFromHandle(dc)
    memory=source.CreateCompatibleDC()
    bitmap=win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source,right-left,bottom-top)
    previous=memory.SelectObject(bitmap)
    try:
        assert ctypes.windll.user32.PrintWindow(hwnd,memory.GetSafeHdc(),2)==1
        filename=name+'.png'
        Image.frombuffer('RGB',(right-left,bottom-top),bitmap.GetBitmapBits(True),'raw','BGRX',0,1).save(out/filename)
        report['screenshots'].append(filename)
    finally:
        memory.SelectObject(previous)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC();source.DeleteDC();win32gui.ReleaseDC(hwnd,dc)


def idle(window):
    wait(window,element('ai-panel')+"?.getAttribute('aria-busy')==='false' && !"+element('ai-progress'))


def ask(window,question):
    click(window,'ai-new-session')
    fill(window,'ai-question',question)
    click(window,'ai-send')


def observe():
    window=windows[0]
    try:
        assert window.events.loaded.wait(30)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and (not auto_tasks or any(managers[0].snapshot(task)['state'] not in {'succeeded','failed','cancelled'} for task in auto_tasks)):
            time.sleep(.05)
        assert auto_tasks and all(managers[0].snapshot(task)['state']=='succeeded' for task in auto_tasks)
        wait(window,'!'+element('installation-auto')+'.disabled')
        wait(window,element('installation-open')+"?.dataset.selectedId==="+json.dumps(fixture.installation))
        wait(window,"document.querySelector('.installation-dialog')?.open===false")
        wait(window,element('mod-workspace')+"?.getAttribute('aria-busy')==='false'")
        click(window,'workspace-ai')
        idle(window)
        assert window.evaluate_js("document.querySelector('.workspace-heading h2').textContent")=='日志'
        assert window.evaluate_js("document.querySelector('.conversation-heading h3').textContent")=='智能分析'
        fill(window,'ai-question','[LONG] 请分析日志')
        click(window,'diagnostic-analyze')
        wait(window,element('ai-response')+"?.innerText.includes('长回复完整结束。') && !"+element('ai-progress'))
        idle(window)
        assert not window.evaluate_js(element('ai-error')+'?.innerText')
        count=window.evaluate_js(element('ai-response')+'.innerText.length')
        assert count>10000,count
        assert tree(game)==baseline
        shot(window,'01-long-reply-complete')
        report['checks'].append(dict(name='long_reply',visible_characters=count,bytes=len(LONG_TEXT.encode('utf-8')),completed=True))

        ask(window,'[TRUNCATED] 分析异常')
        wait(window,element('ai-error')+"?.innerText.includes('模型达到') && "+element('ai-continue'))
        idle(window)
        shot(window,'02-model-length-can-continue')
        click(window,'ai-continue')
        wait(window,"Array.from(document.querySelectorAll('[data-testid=ai-response]')).some(e=>e.innerText.includes('继续分析完成')) && !"+element('ai-progress'))
        idle(window)
        assert window.evaluate_js("Array.from(document.querySelectorAll('[data-testid=ai-response]')).some(e=>e.innerText.includes('已定位到缺少程序集'))")
        report['checks'].append(dict(name='model_length_manual_continuation',partial_retained=True))

        ask(window,'[CANCEL] 保留部分回复')
        wait(window,element('ai-response')+"?.innerText.includes('停止前已接收到') && "+element('ai-progress'))
        click(window,'ai-cancel')
        idle(window)
        wait(window,element('ai-continue')+' && !'+element('ai-continue')+'.disabled')
        assert window.evaluate_js(element('ai-response')+".innerText.includes('停止前已接收到')")
        shot(window,'03-cancel-retains-reply')
        report['checks'].append(dict(name='cancel',partial_retained=True,can_continue=True))

        ask(window,'[QUERIES] 请逐页查询 Mod 并给出可确认的隔离建议')
        wait(window,element('ai-response')+"?.innerText.includes('最终分析') && "+element('ai-suggestion')+"?.dataset.action==='disable' && !"+element('ai-progress'))
        idle(window)
        assert not window.evaluate_js(element('ai-error')+'?.innerText')
        assert window.evaluate_js(element('ai-notice')+"?.innerText.includes('可继续追问')")
        assert tree(game)==baseline
        shot(window,'04-bounded-query-final-analysis')
        click(window,'ai-suggestion-action')
        wait(window,element('ai-suggestion')+"?.dataset.status==='completed'")
        assert not external.exists()
        ask(window,'[ENABLE] 重新启用 ExternalAnalysis')
        wait(window,element('ai-suggestion')+"?.dataset.action==='enable' && !"+element('ai-progress'))
        click(window,'ai-suggestion-action')
        wait(window,element('ai-suggestion')+"?.dataset.status==='completed'")
        assert tree(game)==baseline
        report['checks'].append(dict(name='bounded_query_completion',adaptive_pagination=True,final_answer=True,
            scope_notice=True,safe_suggestion_confirmed=True,restored_bytes=True))

        ask(window,'[DISABLE] 停用 ExternalAnalysis 排查')
        wait(window,element('ai-suggestion')+"?.dataset.action==='disable' && !"+element('ai-progress'))
        idle(window)
        assert tree(game)==baseline
        shot(window,'04-external-mod-confirm-preview')
        click(window,'ai-suggestion-action')
        wait(window,element('ai-suggestion')+"?.dataset.status==='completed'")
        assert not external.exists()
        assert window.evaluate_js(element('ai-action-notice')+".innerText.includes('恢复副本')")
        shot(window,'05-external-mod-disabled')
        click(window,'workspace-mods')
        wait(window,"Array.from(document.querySelectorAll('[data-testid=workspace-mod-row]')).some(e=>e.innerText.includes('ExternalAnalysis')&&e.dataset.state==='disabled')")
        click(window,'workspace-ai')
        ask(window,'[ENABLE] 重新启用 ExternalAnalysis')
        wait(window,element('ai-suggestion')+"?.dataset.action==='enable' && !"+element('ai-progress'))
        click(window,'ai-suggestion-action')
        wait(window,element('ai-suggestion')+"?.dataset.status==='completed'")
        assert tree(game)==baseline
        report['checks'].append(dict(name='workspace_mod_action',query=True,preview_no_writes=True,disable=True,enable=True,workspace_refreshed=True,restored_bytes=True))
        ask(window,'[NARRATIVE] 请完整分析后生成建议')
        wait(window,element('ai-response')+"?.innerText.includes('建议已准备，请确认后复测。') && !"+element('ai-progress'))
        idle(window)
        narrative=window.evaluate_js(element('ai-response')+'.innerText')
        assert 'SyntheticDependencyNotFoundException' in narrative and '先核对当前环境' in narrative
        assert len(narrative)>2500 and tree(game)==baseline
        shot(window,'06-analysis-before-tool-kept-with-final')
        click(window,'ai-export')
        wait(window,element('ai-export-text')+"?.value.includes('SyntheticDependencyNotFoundException')")
        report['checks'].append(dict(name='analysis_before_tool_preserved',visible_characters=len(narrative),
            detailed_analysis=True,short_final=True,suggestion_retained=True,export_kept=True))
        assert all(not row['headers'].get('Authorization') for row in provider.requests)
        report['provider_requests']=len(provider.requests)
        report['status']='PASS'
    except Exception:
        report['error']=traceback.format_exc()
        try:
            report['document']=window.evaluate_js('document.body.innerText')
            shot(window,'failure')
        except Exception:
            pass
    finally:
        window.destroy()


def create(*values,**options):
    options.update(width=1360,height=980)
    window=original_create(*values,**options)
    windows.append(window)
    return window


def start_webview(*values,**options):
    return original_start(observe,**options)


webview.create_window,webview.start=create,start_webview
with ScriptedAIProvider([scripted]*30,fragment_size=250,delay=.001) as provider:
    report['app_exit_code']=main.main(['--psmm-data-root',str(data)])
    report['provider_errors']=provider.errors
if managers:
    report['cleanup']=dict(active_workers=sum(job.thread.is_alive() for job in managers[0]._jobs.values()),subscriptions=len(managers[0]._subscriptions))
    if any(report['cleanup'].values()):
        report['status']='FAIL'
(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(status=report['status'],error=report.get('error'),screenshots=len(report['screenshots']))))
raise SystemExit(0 if report['status']=='PASS' and report['app_exit_code']==0 else 1)
