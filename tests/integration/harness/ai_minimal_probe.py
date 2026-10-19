"""One explicitly authorized provider probe using only a newly owned fixture.

This module never reads a user's config, credentials, logs or installation.
The caller supplies an approved config and optional in-memory credential; only
the caller decides whether an external provider request is authorized.
"""
from pathlib import Path
import time

from tests.integration.harness.transaction_recovery import TransactionFixture,request
from tests.unit.test_ai_privacy import MemoryCredentials

MINIMAL_LOG=('PSModManager synthetic verification only\n'
    '[00:00:01] Probably Stolen / Unity IL2CPP / MelonLoader 0.7.3\n'
    '[00:00:02] ERROR SyntheticProbe: System.IO.FileNotFoundException: '
    "Could not load file or assembly 'SyntheticDependency'.\n")


def run_minimal_probe(root, config, *, key=None, timeout=195):
    root=Path(root).resolve()
    project=Path(__file__).resolve().parents[3]
    if not root.is_relative_to(project/'docs/development/evidence/20261016-followup-fixes/ai'):
        raise ValueError('Probe requires the owned follow-up AI evidence directory')
    fixture=TransactionFixture(root)
    try:
        fixture.context.ai.credentials._backend=MemoryCredentials()
        (fixture.game/'Mods/SyntheticProbe.dll').write_bytes(b'owned-synthetic-probe-placeholder')
        (fixture.game/'MelonLoader/Latest.log').write_text(MINIMAL_LOG,encoding='utf-8')
        fixture.context.ai.save(dict(provider='openai_compatible',base_url=config['base_url'],model=config['model']),key=key,remember=False)
        sources=fixture.call('ai.diagnostics.sources',dict(installation_id=fixture.installation))['sources']
        source=next(row for row in sources if row['relative_path']=='MelonLoader/Latest.log')
        preview=fixture.call('ai.diagnostics.preview',dict(installation_id=fixture.installation,
            source_ids=[source['source_id']],include_context=True))
        question=('这是仅有合成资源的功能验证。请实际查询 get_installation_summary 和 get_mod_workspace，'
            '结合选定合成日志分析 SyntheticDependency 缺失的原因，区分事实/推测并给出最小下一步。'
            '若需隔离，请只生成 SyntheticProbe 的操作建议，等待用户点击，不得执行。')
        initial=fixture.context.api.dispatch(request('ai.diagnostics.send',dict(snapshot_id=preview['snapshot_id'],question=question)))
        if not initial['ok']:
            return dict(ok=False,error_code=initial['error']['code'],message=initial['error']['message'])
        task_id=initial['data']['task_id']
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            terminal=fixture.context.jobs.snapshot(task_id)
            if terminal['state'] in {'succeeded','failed','cancelled'}:
                break
            time.sleep(.05)
        else:
            fixture.context.jobs.cancel(task_id)
            raise TimeoutError('Authorized synthetic probe exceeded its wait')
        events=fixture.context.jobs.subscribe(task_id)['events']
        names=[row['data']['tool_name'] for row in events if isinstance(row.get('data'),dict) and row['data'].get('tool_name')]
        result=terminal.get('data') or {}
        text=result.get('text','')
        if result.get('full_text_in_session'):
            text=fixture.context.ai.get(result['session_id'])['messages'][-1]['content']
        return dict(ok=terminal['state']=='succeeded' and result.get('completed') is True,
            state=terminal['state'],error_code=result.get('error_code'),message=result.get('message',''),
            query_notice=result.get('query_notice',''),tool_names=names,text=text,
            suggestions=result.get('suggestions',[]),synthetic_log=MINIMAL_LOG,
            actual_log_sent=False,actual_mod_sent=False,actual_save_sent=False,
            game_launched=False)
    finally:
        fixture.close()
