export function selectIds(selection,list,id,visible,event={}) {
  const same=selection.list===list,base=same?selection.ids:[]
  if(event.shiftKey&&same&&visible.includes(selection.anchor)){
    const a=visible.indexOf(selection.anchor),b=visible.indexOf(id),range=visible.slice(Math.min(a,b),Math.max(a,b)+1)
    return {list,ids:event.ctrlKey||event.metaKey?[...new Set([...base,...range])]:range,anchor:selection.anchor}
  }
  return {list,ids:same&&(event.ctrlKey||event.metaKey)?base.includes(id)?base.filter(x=>x!==id):[...base,id]:[id],anchor:id}
}
export function toggleAll(selection,list,visible,force=false){
  const base=selection.list===list?selection.ids:[],all=!force&&visible.length>0&&visible.every(id=>base.includes(id))
  return {list,ids:all?base.filter(id=>!visible.includes(id)):[...new Set([...base,...visible])],anchor:visible[0]||null}
}
export function boxIds(base,hits,event={}){
  if(event.ctrlKey||event.metaKey)return [...base.filter(id=>!hits.includes(id)),...hits.filter(id=>!base.includes(id))]
  return event.shiftKey?[...new Set([...base,...hits])]:hits
}
export const intersects=(a,b)=>a.left<=b.right&&a.right>=b.left&&a.top<=b.bottom&&a.bottom>=b.top
