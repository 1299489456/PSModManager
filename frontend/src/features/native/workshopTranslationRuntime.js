import { terminalStates } from '../../app/bridge/reducer.js'
export const joinTranslation = pieces => (pieces||[]).map((p,i)=>(i?p?.sep??'\n':'')+(p?.text??'')).join('')
export function createWorkshopTranslationRuntime(bridge,state={}) {
  Object.assign(state,{workshopId:null,status:'idle',pieces:{title:[],description:[]},done:0,total:0,error:'',truncated:false,show:true,cached:false,cacheLoading:false,model:''})
  let epoch=0,disposed=false,taskId=null,watcher=null
  let installation=null
  const cache=new Map(),cacheKey=()=>`${installation||'public'}:${state.workshopId}`
  function remember(){if(state.workshopId&&state.status==='done')cache.set(cacheKey(),{pieces:structuredClone({title:[...state.pieces.title].map(x=>x&&({...x})),description:[...state.pieces.description].map(x=>x&&({...x}))}),show:state.show,truncated:state.truncated,model:state.model})}
  const clearWatch=()=>{void watcher?.dispose();watcher=null}
  async function cancel(){++epoch;const id=taskId;taskId=null;clearWatch();if(state.status==='running')state.status='cancelled';if(id)await bridge.request('task.cancel',{task_id:id}).catch(()=>{})}
  async function lookup(token=epoch){
    const id=state.workshopId;if(!id||disposed||state.status==='running')return
    state.cacheLoading=true
    try{
      const result=await bridge.request('ai.translation.cache',{workshop_id:id,...(installation?{installation_id:installation}:{})})
      if(token!==epoch||disposed)return
      state.sourcePending=Boolean(result.source_pending)
      if(result.hit){Object.assign(state,{pieces:{title:[{text:result.translation?.title||''}],description:[{text:result.translation?.description||''}]},status:'done',cached:true,truncated:Boolean(result.truncated),model:result.model||''});remember()}
      else if(!state.sourcePending){cache.delete(cacheKey());Object.assign(state,{pieces:{title:[],description:[]},status:'idle',cached:false,truncated:false,model:''})}
    }catch{}finally{if(token===epoch&&!disposed)state.cacheLoading=false}
  }
  async function select(id,installationId=installation){
    if(id===state.workshopId&&installationId===installation)return
    remember();const cancellation=cancel(),token=epoch;installation=installationId
    Object.assign(state,{workshopId:id||null,status:'idle',pieces:{title:[],description:[]},done:0,total:0,error:'',truncated:false,show:true,cached:false,cacheLoading:false,sourcePending:false,model:''})
    const saved=id?cache.get(cacheKey()):null
    if(saved)Object.assign(state,saved,{status:'done',cached:true})
    await cancellation
    if(token!==epoch||disposed||!id)return
    await lookup(token)
  }
  async function translate(installationId,force=false){if(disposed||!state.workshopId||state.status==='running')return;if(state.status==='done'&&!force){state.show=true;return}const token=++epoch,id=state.workshopId;Object.assign(state,{status:'running',pieces:{title:[],description:[]},done:0,total:0,error:'',truncated:false,show:true,cached:false});const current=()=>!disposed&&token===epoch&&id===state.workshopId
    function receive(event){if(!current())return;const d=event.data||{};if(['title','description'].includes(d.part)&&Number.isSafeInteger(d.index)&&d.index>=0&&d.index<256){state.pieces[d.part][d.index]={text:d.text||'',sep:d.sep};state.done=event.completed||0;state.total=event.total||d.count||0}if(!terminalStates.has(event.state))return;taskId=null;clearWatch();if(event.state==='cancelled')state.status='cancelled';else if(event.state!=='succeeded'||d.error_code){state.status='error';state.error=d.message||event.message||'翻译未完成，请重试。'}else{state.status='done';state.truncated=Boolean(d.truncated);state.cached=Boolean(d.cached);state.model=d.model||'';remember()}}
    try{const started=await bridge.request('workshop.translate',{workshop_id:id,...(installationId?{installation_id:installationId}:{}),...(force?{force_refresh:true}:{})});if(!current()){if(started.task_id)await bridge.request('task.cancel',{task_id:started.task_id}).catch(()=>{});return}if(!started.task_id)throw new Error('翻译任务未创建。');taskId=started.task_id;receive(started);if(current()&&!terminalStates.has(started.state)){watcher=bridge.watchTask(taskId,receive,()=>{if(current()){state.status='error';state.error='翻译进度连接中断，请重试。';clearWatch()}})}}catch(e){if(current()){state.status='error';state.error=e.message||'翻译未完成，请重试。'}}
  }
  return {select,translate,cancel,text:part=>joinTranslation(state.pieces[part]),hydrateCache:lookup,invalidate(){cache.clear();const id=state.workshopId;state.workshopId=null;void select(id)},dispose(){disposed=true;void cancel()}}
}
