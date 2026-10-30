import { terminalStates } from '../../app/bridge/reducer.js'

export const securityAiWarnings = preview => (preview?.warnings || []).filter(warning => warning !== '仅包含已有报告的有界摘要和静态证据，不发送 DLL、完整源码或任意文件。')

// A preview is a local read. Only send() may contact the configured AI provider.
export function createSecurityAiRuntime(bridge, state = {}) {
  Object.assign(state,{scope:null,preview:null,loading:false,sent:false,busy:false,cancelling:false,error:'',notice:'',text:'',sessionId:null,taskId:null})
  let epoch=0,disposed=false,watcher=null
  const current=token=>!disposed&&token===epoch
  function detach(){void watcher?.dispose();watcher=null}
  async function cancelTask(id){if(id)await bridge.request('task.cancel',{task_id:id}).catch(()=>{})}
  function reset(scope){++epoch;const id=state.taskId;detach();Object.assign(state,{scope,preview:null,loading:false,sent:false,busy:false,cancelling:false,error:'',notice:'',text:'',sessionId:null,taskId:null});void cancelTask(id)}
  async function preview(){
    if(disposed||!state.scope||state.busy||state.loading)return false
    const token=++epoch;state.loading=true;state.error='';state.preview=null
    try{const value=await bridge.request('security.ai.preview',{...state.scope});if(!current(token))return false
      if(!value?.preview_id||!Array.isArray(value.messages)||value.report_id!==state.scope.report_id||value.mod_id!==state.scope.id)throw new Error('发送预览与当前报告不一致，请重新生成。')
      state.preview=value;state.sent=false;state.text='';state.notice='';return true
    }catch(error){if(current(token))state.error=error.message;return false}
    finally{if(current(token))state.loading=false}
  }
  async function readReply(token){
    if(!state.sessionId)return
    const id=state.sessionId
    try{const value=await bridge.request('ai.chat.get',{session_id:id});if(!current(token)||state.sessionId!==id)return
      const reply=value.messages?.filter(message=>message.role==='assistant').at(-1)
      if(typeof reply?.content==='string')state.text=reply.content
    }catch(error){if(current(token))state.error=error.message||'完整回复暂时无法读取。'}
  }
  async function send(){
    if(disposed||!state.preview||state.loading||state.busy||state.sent||!state.preview.provider?.configured)return false
    const token=++epoch;state.busy=true;state.error='';state.notice='';state.text=''
    function receive(event){if(!current(token))return
      const data=event.data||{};if(data.session_id)state.sessionId=data.session_id
      if(typeof data.text==='string'&&!data.full_text_in_session)state.text=data.text
      if(typeof data.text_delta==='string'&&data.text_offset===state.text.length)state.text+=data.text_delta
      if(!terminalStates.has(event.state))return
      state.busy=false;state.cancelling=false;state.taskId=null;detach()
      if(event.state==='cancelled')state.notice='已取消分析。'
      else if(event.state!=='succeeded'||data.error_code)state.error=data.message||event.message||'分析未完成。'
      else state.notice='分析完成'
      if(state.sessionId)void readReply(token)
    }
    try{const started=await bridge.request('security.ai.send',{preview_id:state.preview.preview_id})
      if(!current(token)){void cancelTask(started.task_id);return false}
      if(!started.task_id)throw new Error('分析任务未创建。')
      state.sent=true;state.taskId=started.task_id;receive(started)
      if(state.busy){const handle=bridge.watchTask(started.task_id,receive,()=>{if(current(token)){state.error='分析连接中断，可重新生成预览后重试。';state.busy=false;void cancelTask(state.taskId);state.taskId=null;detach()}});if(current(token)&&state.busy)watcher=handle;else void handle.dispose()}
      return true
    }catch(error){if(current(token)){state.busy=false;state.error=error.message;if(error.code==='AI_SECURITY_PREVIEW_CHANGED')state.preview=null}return false}
  }
  return {preview,send,setScope(scope){if(JSON.stringify(scope)!==JSON.stringify(state.scope))reset(scope)},
    async cancel(){if(!state.taskId||state.cancelling)return;state.cancelling=true;await cancelTask(state.taskId)},
    invalidate(){reset(state.scope)},dispose(){reset(null);disposed=true},
  }
}
