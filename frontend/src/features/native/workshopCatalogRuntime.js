export function createWorkshopCatalogRuntime(bridge,state={}){
  Object.assign(state,{result:null,selected:null,listLoading:false,detailLoading:false,error:'',installationId:null})
  const cache=new Map(),pending=new Map()
  let epoch=0,detailEpoch=0,disposed=false,detailTimer=null
  const scope=()=>state.installationId?{installation_id:state.installationId}:{}
  function setInstallation(id){++epoch;++detailEpoch;clearTimeout(detailTimer);Object.assign(state,{installationId:id||null,result:null,selected:null,listLoading:false,detailLoading:false,error:''})}
  async function browse(args){const token=++epoch;state.listLoading=true;state.error='';try{const result=await bridge.request('workshop.browse',{...scope(),...args});if(token===epoch&&!disposed)state.result=result;return result}catch(e){if(token===epoch&&!disposed)state.error=e.message;return null}finally{if(token===epoch&&!disposed)state.listLoading=false}}
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
      if(!pending.has(key)||refresh)pending.set(key,bridge.request('workshop.detail',{...scope(),workshop_id:id,...(refresh?{refresh:true}:{})}).then(value=>{cache.set(key,{value,at:Date.now()});if(cache.size>96)cache.delete(cache.keys().next().value);return value}).finally(()=>pending.delete(key)))
      const value=await pending.get(key)
      adopt(value)
      return value
    }catch(e){if(token===detailEpoch&&!disposed){state.error=e.message;state.detailLoading=false}return null}
    finally{if(token===detailEpoch&&!disposed&&!state.selected?.source_pending)state.detailLoading=false}
  }
  function back(){++detailEpoch;clearTimeout(detailTimer);state.selected=null;state.detailLoading=false;state.error=''}
  return {browse,select,back,setInstallation,dispose(){disposed=true;++epoch;++detailEpoch;clearTimeout(detailTimer);cache.clear()}}
}
