const bridges=new WeakMap()
export function workshopImageCache(bridge){
  if(bridges.has(bridge))return bridges.get(bridge)
  const images=new Map(),pending=new Map()
  const runtime={peek:(id,version='')=>images.get(id)?.version===version?images.get(id).result:undefined,async load(id,version=''){
    if(images.get(id)?.version===version)return images.get(id).result
    const key=`${id}:${version}`
    if(!pending.has(key))pending.set(key,bridge.request('workshop.image',{workshop_id:id}).then(result=>{if(result.state==='ready'&&result.data_url){images.set(id,{version,result});if(images.size>96)images.delete(images.keys().next().value)}return result}).finally(()=>pending.delete(key)))
    return pending.get(key)
  }}
  bridges.set(bridge,runtime);return runtime
}
