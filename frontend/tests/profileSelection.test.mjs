import test from 'node:test'
import assert from 'node:assert/strict'
import { selectRows, createProfileDrag, createPointerProfileDrag, createRowGesture, filterRows, insertionTarget } from '../src/features/profiles/profileSelection.js'
const profile = {profile_id:'p',revision:3,selections:[{release_id:'a',selected:true},{release_id:'b',selected:true},{release_id:'c',selected:true},{release_id:'d',selected:false}]}
test('profile case027 Ctrl toggles individual selection and Shift selects full contiguous range',()=>{
  const rows=profile.selections.slice(0,3)
  let result=selectRows(rows,[],null,'a'); result=selectRows(rows,result.selected,result.anchor,'c',{shiftKey:true})
  assert.deepEqual(result.selected,['a','b','c'])
  result=selectRows(rows,result.selected,result.anchor,'b',{ctrlKey:true}); assert.deepEqual(result.selected,['a','c'])
  result=selectRows(rows,result.selected,result.anchor,'b',{metaKey:true}); assert.deepEqual(result.selected,['a','c','b'])
})
test('profile case027 dragging a selected row transfers entire same-side selection once',()=>{
  const drag=createProfileDrag(); assert.deepEqual(drag.begin(profile,'a',['a','b','d']),['a','b'])
  assert.equal(drag.accepts(profile,true),false)
  assert.deepEqual(drag.take(profile,false),{release_ids:['a','b'],selected:false})
  assert.equal(drag.take(profile,false),null)
})
test('profile case027 dragging an unselected row transfers only that row',()=>{
  const drag=createProfileDrag(); assert.deepEqual(drag.begin(profile,'c',['a','b']),['c']); assert.deepEqual(drag.take(profile,false).release_ids,['c'])
})
test('profile case027 stale revision, other Profile, same side and foreign drops cannot edit',()=>{
  const drag=createProfileDrag(); assert.equal(drag.take(profile,false),null)
  drag.begin(profile,'a',['a']); assert.equal(drag.take({...profile,revision:4},false),null)
  drag.begin(profile,'a',['a']); assert.equal(drag.take({...profile,profile_id:'other'},false),null)
  drag.begin(profile,'a',['a']); assert.equal(drag.take(profile,true),null)
  drag.begin(profile,'a',['a']); drag.clear(); assert.equal(drag.take(profile,false),null)
})
test('profile case027 Shift with anchor outside current side never sweeps other side',()=>{
  assert.deepEqual(selectRows(profile.selections.slice(0,3),['d'],'d','b',{shiftKey:true}).selected,['b'])
})
test('profile case027 search matches names versions groups and Shift includes only visible rows',()=>{
  const rows=profile.selections.map((row,index)=>({...row,name:index===1?'hidden':'中文',version_raw:index===1?'v2':'v1',group:index===1?'工具':'资源'}))
  const names=row=>row.name
  assert.deepEqual(filterRows(rows,true,'V2',names).map(row=>row.release_id),['b'])
  assert.deepEqual(filterRows(rows,true,'工具',names).map(row=>row.release_id),['b'])
  const visible=filterRows(rows,true,'中文',names)
  assert.deepEqual(selectRows(visible,['a'],'a','c',{shiftKey:true}).selected,['a','c'])
})
test('profile case027 pressing an already selected row keeps Ctrl selection until click or native drag is known',()=>{
  const gesture=createRowGesture(), rows=profile.selections.slice(0,3), picked=['a','b']
  const press=gesture.press(rows,picked,'b','a',{button:0})
  assert.deepEqual(press.selected,['a','b'])
  const drag=createProfileDrag(); gesture.drag()
  drag.begin(profile,'a',press.selected)
  assert.deepEqual(drag.take(profile,false),{release_ids:['a','b'],selected:false})
  assert.deepEqual(gesture.click(rows,picked,'b','a').selected,['a','b'])
})
test('profile case027 a regular click collapses a multi-selection only after release',()=>{
  const gesture=createRowGesture(), rows=profile.selections.slice(0,3)
  assert.deepEqual(gesture.press(rows,['a','b'],'b','a').selected,['a','b'])
  assert.deepEqual(gesture.click(rows,['a','b'],'b','a').selected,['a'])
})
test('profile case027 Ctrl press adds once and release does not undo the selection',()=>{
  const gesture=createRowGesture(), rows=profile.selections.slice(0,3)
  const press=gesture.press(rows,['a'],'a','b',{ctrlKey:true})
  assert.deepEqual(press.selected,['a','b'])
  assert.deepEqual(gesture.click(rows,press.selected,press.anchor,'b',{ctrlKey:true}).selected,['a','b'])
  gesture.press(rows,['a','b'],'b','b',{ctrlKey:true})
  assert.deepEqual(gesture.click(rows,['a','b'],'b','b',{ctrlKey:true}).selected,['a'])
})
test('profile case027 Shift press selects range before dragging and click never collapses it',()=>{
  const gesture=createRowGesture(), rows=profile.selections.slice(0,3)
  const press=gesture.press(rows,['a'],'a','c',{shiftKey:true})
  assert.deepEqual(press.selected,['a','b','c'])
  gesture.drag()
  assert.deepEqual(gesture.click(rows,press.selected,press.anchor,'c').selected,['a','b','c'])
})
test('profile case027 right press preserves existing selection and clearing allows fresh keyboard click',()=>{
  const gesture=createRowGesture(), rows=profile.selections.slice(0,3)
  assert.deepEqual(gesture.press(rows,['a','b'],'b','c',{button:2}).selected,['a','b'])
  gesture.drag(); gesture.clear()
  assert.deepEqual(gesture.click(rows,['a','b'],'b','c').selected,['c'])
})
const pointer = (extra={}) => ({pointerId:4,button:0,buttons:1,clientX:10,clientY:10,...extra})
test('profile case027 pointer click below threshold never becomes an edit',()=>{
  const drag=createPointerProfileDrag()
  assert.equal(drag.press(profile,'a',['a'],pointer()),true)
  assert.equal(drag.move(profile,pointer({clientX:14})),null)
  assert.equal(drag.release(profile,4,false),null)
  assert.equal(drag.active,false)
})
test('profile case027 captured pointer transfers exact same-side multi-selection on release once',()=>{
  const drag=createPointerProfileDrag()
  drag.press(profile,'a',['a','b','d'],pointer())
  assert.deepEqual(drag.move(profile,pointer({clientX:30})),{started:true,release_ids:['a','b']})
  assert.deepEqual(drag.move(profile,pointer({clientX:50})),{started:false,release_ids:['a','b']})
  assert.equal(drag.accepts(profile,false),true)
  assert.deepEqual(drag.release(profile,4,false),{release_ids:['a','b'],selected:false})
  assert.equal(drag.release(profile,4,false),null)
})
test('profile case027 foreign pointer events cannot start or finish another pointer drag',()=>{
  const drag=createPointerProfileDrag()
  assert.equal(drag.press(profile,'a',['a'],pointer({button:2})),false)
  drag.press(profile,'a',['a'],pointer())
  assert.equal(drag.move(profile,pointer({pointerId:5,clientX:30})),null)
  drag.move(profile,pointer({clientX:30}))
  assert.equal(drag.release(profile,5,false),null)
  assert.equal(drag.active,true)
  assert.deepEqual(drag.release(profile,4,false),{release_ids:['a'],selected:false})
})
test('profile case027 same lane or release outside this editor cancels without edits',()=>{
  for(const target of [true,null,undefined]) {
    const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
    assert.equal(drag.release(profile,4,target),null);assert.equal(drag.active,false)
  }
})
test('profile case027 revision change, profile switch and lost primary button invalidate pointer drag',()=>{
  for(const changed of [{...profile,revision:4},{...profile,profile_id:'other'}]) {
    const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
    assert.equal(drag.release(changed,4,false),null)
  }
  const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer())
  assert.equal(drag.move(profile,pointer({clientX:30,buttons:0})),null)
  assert.equal(drag.pointerId,null)
})
test('profile case027 cancellation before release cannot commit and a fresh drag can follow',()=>{
  const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
  drag.cancel();assert.equal(drag.release(profile,4,false),null)
  drag.press(profile,'c',['a','b'],pointer());drag.move(profile,pointer({clientX:30}))
  assert.deepEqual(drag.release(profile,4,false),{release_ids:['c'],selected:false})
})
test('profile case027 object pointer target allows same-lane insert and preserves ordered moving block',()=>{
  const drag=createPointerProfileDrag();drag.press(profile,'a',['c','a'],pointer());drag.move(profile,pointer({clientX:30}))
  const target={selected:true,before_release_id:'b'}
  assert.equal(drag.accepts(profile,target),true)
  assert.deepEqual(drag.release(profile,4,target),{release_ids:['a','c'],...target})
  assert.equal(drag.release(profile,4,target),null)
})
test('profile case027 insertion geometry excludes moving rows and does not invent hidden anchors',()=>{
  const visible=[profile.selections[0],profile.selections[2]]
  const rects=[{release_id:'a',top:10,height:40},{release_id:'c',top:50,height:40}]
  assert.deepEqual(insertionTarget(visible,['a'],true,60,rects),{selected:true,before_release_id:'c'})
  assert.deepEqual(insertionTarget(visible,['a'],true,75,rects),{selected:true,before_release_id:null})
  assert.deepEqual(insertionTarget([],['a'],false,20,[]),{selected:false,before_release_id:null})
  assert.equal(insertionTarget(visible,['a'],true,60,[]),null)
  assert.equal(insertionTarget(visible,['a'],true,NaN,rects),null)
})
test('profile case027 pointer insertion rejects missing, moved, wrong-lane and malformed anchors',()=>{
  for(const target of [{selected:true,before_release_id:'missing'},{selected:true,before_release_id:'a'},
    {selected:true,before_release_id:'d'},{selected:false,before_release_id:'b'},
    {selected:true},{selected:true,before_release_id:null,external:true}]) {
    const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
    assert.equal(drag.accepts(profile,target),false)
    assert.equal(drag.release(profile,4,target),null)
  }
})
test('profile case027 object insertion is bound to the original profile revision and pointer',()=>{
  const target={selected:false,before_release_id:'d'}
  for(const changed of [{...profile,revision:4},{...profile,profile_id:'another'}]) {
    const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
    assert.equal(drag.accepts(changed,target),false)
    assert.equal(drag.release(changed,4,target),null)
  }
  const drag=createPointerProfileDrag();drag.press(profile,'a',['a'],pointer());drag.move(profile,pointer({clientX:30}))
  assert.equal(drag.release(profile,5,target),null)
  assert.deepEqual(drag.release(profile,4,target),{release_ids:['a'],...target})
})
