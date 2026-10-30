export function catalogStatusView(result,{pollingPaused=false}={}){
  if(!result)return null
  if(result.network_error)return {kind:'error',text:`公开目录读取失败：${result.network_error.message}${result.items?.length?' 当前保留已有条目。':''}`,retry:true}
  if(result.refreshing||result.loading)return {kind:'loading',text:pollingPaused?'公开目录仍在后台读取；可刷新列表查看进度。':result.items?.length?'正在后台更新公开目录，现有条目仍可操作。':'正在读取所选公开目录…',retry:pollingPaused}
  if(result.stale)return {kind:'cached',text:'当前显示已保存的公开目录。Steam 账号连接状态独立显示。',retry:true}
  return null
}
export function createWorkshopCatalogRuntime(bridge,state={}){
  Object.assign(state,{result:null,selected:null,listLoading:false,listRefreshing:false,listPollingPaused:false,detailLoading:false,error:'',installationId:null})
  const cache=new Map(),pending=new Map(),lists=new Map(),listPending=new Map()
  let epoch=0,detailEpoch=0,disposed=false,detailTimer=null,listTimer=null
  const scope=()=>state.installationId?{installation_id:state.installationId}:{}
  function setInstallation(id){++epoch;++detailEpoch;clearTimeout(detailTimer);clearTimeout(listTimer);Object.assign(state,{installationId:id||null,result:null,selected:null,listLoading:false,listRefreshing:false,listPollingPaused:false,detailLoading:false,error:''})}
  async function browse(args={}){
    const token=++epoch,request={...scope(),query:args.query||'',page:args.page||1,page_size:args.page_size||24,sort:args.sort||'hot'},key=JSON.stringify(request)
    clearTimeout(listTimer)
    state.result=lists.get(key)||null;state.listLoading=!state.result;state.listRefreshing=true;state.listPollingPaused=false;state.error=''
    let checks=0
    async function read(refresh=false){
      try{
        if(!listPending.has(key)){
          const work=bridge.request('workshop.browse',{...request,refresh}).then(value=>{lists.set(key,value);if(lists.size>32)lists.delete(lists.keys().next().value);return value}).finally(()=>{if(listPending.get(key)===work)listPending.delete(key)})
          listPending.set(key,work)
        }
        const result=await listPending.get(key)
        if(disposed||token!==epoch)return null
        state.result=result;state.listLoading=Boolean(result.loading&&!result.items?.length);state.listRefreshing=Boolean(result.refreshing||result.loading)
        if(state.listRefreshing){
          if(++checks<60)listTimer=setTimeout(()=>void read(),Math.max(250,Math.min(2000,result.retry_after_ms||750)))
          else{state.listPollingPaused=true;state.listLoading=false}
        }
        return result
      }catch(e){if(token===epoch&&!disposed){state.error=e.message;state.listLoading=false;state.listRefreshing=false}return null}
    }
    return read(args.refresh===true)
  }
  async function select(item,refresh=false){
    const id=typeof item==='string'?item:item.workshop_id,key=`${state.installationId||'public'}:${id}`,token=++detailEpoch,entry=cache.get(key)
    clearTimeout(detailTimer)
    const seed=typeof item==='object'?item:state.result?.items?.find(x=>x.workshop_id===id)
    state.selected=entry?.value||seed||{workshop_id:id,title:'正在读取条目…'};state.error=''
    if(!refresh&&entry&&!entry.value.source_pending&&!entry.value.refreshing&&Date.now()-entry.at<60000){state.detailLoading=false;return state.selected}
    state.detailLoading=true
    let checks=0
    function adopt(value){
      if(token!==detailEpoch||disposed)return
      state.selected=value;state.detailLoading=Boolean(value.source_pending)
      if((value.source_pending||value.refreshing)&&++checks<30)detailTimer=setTimeout(async()=>{
        try{const next=await bridge.request('workshop.detail',{...scope(),workshop_id:id});cache.set(key,{value:next,at:Date.now()});adopt(next)}catch(e){if(token===detailEpoch&&!disposed){state.detailLoading=false;state.error=e.message}}
      },1000)
      else if(value.source_pending){state.detailLoading=false;state.error='完整介绍尚未取得，请点击“刷新详情”重试。'}
    }
    try{
      if(!pending.has(key)){
        const work=bridge.request('workshop.detail',{...scope(),workshop_id:id,...(refresh?{refresh:true}:{})}).then(value=>{cache.set(key,{value,at:Date.now()});if(cache.size>96)cache.delete(cache.keys().next().value);return value}).finally(()=>{if(pending.get(key)===work)pending.delete(key)})
        pending.set(key,work)
      }
      const value=await pending.get(key)
      adopt(value)
      return value
    }catch(e){if(token===detailEpoch&&!disposed){state.error=e.message;state.detailLoading=false}return null}
    finally{if(token===detailEpoch&&!disposed&&!state.selected?.source_pending)state.detailLoading=false}
  }
  function back(){++detailEpoch;clearTimeout(detailTimer);state.selected=null;state.detailLoading=false;state.error=''}
  return {browse,select,back,setInstallation,dispose(){disposed=true;++epoch;++detailEpoch;clearTimeout(detailTimer);clearTimeout(listTimer);cache.clear();lists.clear()}}
}
