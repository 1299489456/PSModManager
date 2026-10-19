"""Stateful localhost model scenario driven by real returned query data."""
import json
import re


class AdaptiveDiagnosticScenario:
    def __init__(self, *, suggest=False, repeat=False, ignore_stop=False):
        self.suggest,self.repeat,self.ignore_stop=suggest,repeat,ignore_stop

    @staticmethod
    def results(body):
        names={call['id']:call['function']['name'] for row in body['messages'] if row['role']=='assistant'
               for call in row.get('tool_calls',[])}
        return [(names.get(row['tool_call_id']),json.loads(row['content'])) for row in body['messages'] if row['role']=='tool']

    def __call__(self, body, index):
        results=self.results(body)
        workspace=[value['data'] for name,value in results if name=='get_mod_workspace' and value.get('ok')]
        suggestions=[value['data']['suggestion'] for _,value in results if value.get('ok') and 'suggestion' in value.get('data',{})]
        if not body.get('tools'):
            if self.ignore_stop:
                return dict(calls=[('get_installation_summary',{})])
            count=sum(len(row['items']) for row in workspace)
            total=workspace[-1]['total'] if workspace else 0
            text=f'## 最终分析\n\n已查询环境和 {count}/{total} 个 Mod。'
            if workspace and workspace[-1]['next_offset'] is not None:
                text+='其余分页尚未查询，不据此认定不存在其他问题。'
            text+='\n\n证据：用户选定日志包含依赖异常；应核对 Mod 与当前构建的兼容和依赖，再以相同场景复测。'
            if suggestions:
                text+='\n\n已生成具体 Mod 的最小隔离建议，须用户点击后才会停用。'
            return dict(text=text)
        if self.repeat:
            return dict(calls=[('get_installation_summary',{}),('get_installation_summary',{})])
        question=next((row['content'] for row in reversed(body['messages']) if row['role']=='user'),'')
        if '[FOLLOWUP]' in question and workspace:
            row=workspace[-1]['items'][0]
            return dict(text=f"后续核对：{row['display_name']} 当前状态是 {row['state']}。")
        selected=re.search(r'"selected_evidence_id": "([a-f0-9-]{36})"',body['messages'][0]['content'])
        if selected and not any(name=='read_selected_log' for name,_ in results):
            return dict(calls=[('read_selected_log',dict(evidence_id=selected.group(1)))])
        if not workspace:
            return dict(calls=[('get_installation_summary',{}),('get_mod_workspace',dict(limit=1))])
        if self.suggest and not suggestions:
            row=workspace[0]['items'][0]
            return dict(calls=[('suggest_workspace_action',dict(workspace_id=row['id'],snapshot_id=workspace[0]['snapshot_id'],
                action='disable',reason='保留其他 Mod，单独停用此项复现；可重新启用。'))])
        last=workspace[-1]
        if last['next_offset'] is not None:
            return dict(calls=[('get_mod_workspace',dict(offset=last['next_offset'],limit=1))])
        return dict(text='已读取全部当前 Mod；按日志中的依赖异常做最小范围复测。')
