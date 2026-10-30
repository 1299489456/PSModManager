import { onUnmounted, ref } from 'vue'
import { boxIds, intersects } from './listSelection.js'
export function useBoxSelection({getSelection,setSelection,canStart=()=>true,onFocus=()=>{}}){
  const rectangle=ref(null)
  let drag=null,frame=null
  function point(e,el){const b=el.getBoundingClientRect();return {x:e.clientX-b.left+el.scrollLeft,y:e.clientY-b.top+el.scrollTop}}
  function update(){
    if(!drag)return
    const {element,list,start,current,base,modifiers}=drag,b=element.getBoundingClientRect()
    if(current.clientY<b.top+26)element.scrollTop-=12
    else if(current.clientY>b.bottom-26)element.scrollTop+=12
    const p=point(current,element),box={left:Math.min(start.x,p.x),right:Math.max(start.x,p.x),top:Math.min(start.y,p.y),bottom:Math.max(start.y,p.y)}
    if(!drag.moved&&Math.abs(p.x-start.x)+Math.abs(p.y-start.y)<5)return
    drag.moved=true
    rectangle.value={list,style:{left:box.left+'px',top:box.top+'px',width:(box.right-box.left)+'px',height:(box.bottom-box.top)+'px'}}
    const hits=[...element.querySelectorAll('.row[data-id]')].filter(row=>{const r=row.getBoundingClientRect();return intersects(box,{left:r.left-b.left+element.scrollLeft,right:r.right-b.left+element.scrollLeft,top:r.top-b.top+element.scrollTop,bottom:r.bottom-b.top+element.scrollTop})}).map(row=>row.dataset.id)
    setSelection({list,ids:boxIds(base,hits,modifiers),anchor:hits[0]||null})
  }
  function tick(){update();if(drag)frame=requestAnimationFrame(tick)}
  function move(e){if(!drag||e.pointerId!==drag.pointerId)return;drag.current=e;update()}
  function end(e){if(!drag||e&&e.pointerId!==drag.pointerId)return;const d=drag;drag=null;cancelAnimationFrame(frame);rectangle.value=null;d.element.removeEventListener('pointermove',move);d.element.removeEventListener('pointerup',end);d.element.removeEventListener('pointercancel',end);if(d.element.hasPointerCapture?.(d.pointerId))d.element.releasePointerCapture(d.pointerId);if(d.moved)onFocus(d.list);d.element.focus({preventScroll:true})}
  function start(list,e){
    if(e.button!==0||e.pointerType==='touch'||!canStart()||e.target.closest('button,input,textarea,select,a,.row'))return
    const element=e.currentTarget,previous=getSelection();drag={element,list,pointerId:e.pointerId,start:point(e,element),current:e,base:previous.list===list?[...previous.ids]:[],modifiers:{ctrlKey:e.ctrlKey,metaKey:e.metaKey,shiftKey:e.shiftKey},moved:false}
    e.preventDefault();element.setPointerCapture(e.pointerId);element.addEventListener('pointermove',move);element.addEventListener('pointerup',end);element.addEventListener('pointercancel',end);frame=requestAnimationFrame(tick)
  }
  onUnmounted(()=>end())
  return {rectangle,start}
}
