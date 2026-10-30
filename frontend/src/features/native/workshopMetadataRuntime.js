export function createWorkshopMetadataRuntime(bridge,state={}) {
  Object.assign(state,{items:{},loading:false,error:''})
  let epoch=0,disposed=false,installationId=null
  async function load(id,ids,refresh=false){const token=++epoch;if(id!==installationId){installationId=id;state.items={}};const unique=[...new Set(ids)].filter(x=>/^\d{1,20}$/.test(x));const needed=refresh?unique:unique.filter(x=>!state.items[x]);state.error='';if(!needed.length){state.loading=false;return}state.loading=true;try{for(let i=0;i<needed.length;i+=30){const result=await bridge.request('workshop.query',{...(id?{installation_id:id}:{}),ids:needed.slice(i,i+30),refresh});if(token!==epoch||disposed)return;for(const item of result.items||[])state.items[item.workshop_id]=item;if(result.network_error)state.error=result.network_error.message||'部分工坊封面暂未连接'}}catch(error){if(token===epoch&&!disposed)state.error=error.message||'工坊封面暂不可用'}finally{if(token===epoch&&!disposed)state.loading=false}}
  return{load,dispose(){disposed=true;++epoch}}
}
