import test from 'node:test'
import assert from 'node:assert/strict'
import {createSecurityRuntime,securityLabel,securityReason,securityEffect,securityRestoreAllowed,workshopSecurityItem,nativeScanLabel} from './securityRuntime.js'
import {createBridge} from '../../app/bridge/client.js'

const installation='11111111-1111-1111-1111-111111111111'
const item=(state='unscanned',extra={})=>({id:'mod_a',source:'workshop',workshop_id:'123',state,report_id:null,content_hash:null,...extra})
const status=(items=[item()],extra={})=>({installation_id:installation,scanning:false,items,records:[],operation:null,error:null,...extra})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function setup(respond,options={}){const state={},calls=[],timers=new Map();let timerId=0;const runtime=createSecurityRuntime({request:async(command,payload)=>{calls.push([command,payload]);return respond?.(command,payload)??status()}},state,{schedule(fn){const id=++timerId;timers.set(id,fn);return id},cancel(id){timers.delete(id)},...options});return {runtime,state,calls,timers,async tick(){const [id,fn]=timers.entries().next().value||[];assert.ok(fn);timers.delete(id);await fn()}}}

test('inactive check workspace makes zero calls until opened and stops all reads when left',async()=>{
  const app=setup(command=>command==='security.detail'?{item:item(),report:null}:status(),{active:false})
  await app.runtime.setInstallation(installation);app.runtime.inventory([{id:'mod_a',source:'workshop'}]);await app.runtime.select('mod_a')
  await app.runtime.refresh();await app.runtime.scan();await app.runtime.preview('mod_a','disable')
  assert.deepEqual(app.calls,[]);assert.equal(app.timers.size,0)
  await app.runtime.setActive(true);await Promise.resolve()
  assert.deepEqual(app.calls.map(c=>c[0]),['security.status','security.detail'])
  await app.runtime.setActive(false);const before=app.calls.length
  app.runtime.inventory([{id:'mod_a',source:'local',package_revision:'new'}]);await app.runtime.select('mod_a',true);await app.runtime.refresh()
  assert.equal(app.calls.length,before);assert.equal(app.timers.size,0);app.runtime.dispose()
})

test('leaving during initial status ignores completion without fetching a detail or polling',async()=>{
  const pending=deferred(),app=setup(()=>pending.promise,{active:false})
  await app.runtime.setInstallation(installation);await app.runtime.select('mod_a');const opening=app.runtime.setActive(true)
  await app.runtime.setActive(false);pending.resolve(status([item('scanning')],{scanning:true}));await opening
  assert.deepEqual(app.calls.map(c=>c[0]),['security.status']);assert.equal(app.state.status,null);assert.equal(app.timers.size,0);app.runtime.dispose()
})

test('observing an existing scan does not create a background polling owner after leaving',async()=>{
  const app=setup(()=>status([item('scanning')],{scanning:true}),{active:false})
  await app.runtime.setInstallation(installation);await app.runtime.setActive(true);assert.equal(app.timers.size,1)
  await app.runtime.setActive(false);assert.equal(app.timers.size,0);app.runtime.dispose()
})

test('explicit scan may finish while inactive using only activity and no completion report reads',async()=>{
  let done=false;const app=setup(command=>command==='security.activity'?{installation_id:installation,scanning:!done,operation:{state:done?'completed':'running',done:done?1:0,total:1}}:command==='security.scan'?status([item('scanning')],{scanning:true}):status())
  await app.runtime.setInstallation(installation);await app.runtime.scan(['mod_a']);await app.runtime.setActive(false)
  const before=app.calls.length;await app.tick();assert.equal(app.timers.size,1);done=true;await app.tick()
  assert.deepEqual(app.calls.slice(before).map(c=>c[0]),['security.activity','security.activity']);assert.equal(app.timers.size,0)
  assert.equal(app.state.status.scanning,false);await app.runtime.setActive(true)
  assert.equal(app.calls.at(-1)[0],'security.status');app.runtime.dispose()
})

test('scan accepted after leaving polls its explicit work but never reads a report',async()=>{
  const pending=deferred(),app=setup(command=>command==='security.scan'?pending.promise:status())
  await app.runtime.setInstallation(installation);const scanning=app.runtime.scan();await app.runtime.setActive(false)
  pending.resolve(status([item('scanning')],{scanning:true}));await scanning
  assert.equal(app.timers.size,1);assert.deepEqual(app.calls.map(c=>c[0]),['security.status','security.scan']);app.runtime.dispose()
})

test('a late page-entry status cannot erase an explicitly accepted scan or its polling',async()=>{
  const pending=deferred(),app=setup(command=>command==='security.status'?pending.promise:status([item('scanning')],{scanning:true,operation:{state:'queued',done:0,total:1}}),{active:false})
  await app.runtime.setInstallation(installation);const opening=app.runtime.setActive(true)
  await app.runtime.scan(['mod_a']);pending.resolve(status());await opening
  assert.equal(app.state.status.scanning,true);assert.equal(app.state.status.operation.total,1);assert.equal(app.timers.size,1);app.runtime.dispose()
})

test('labels distinguish coverage, evidence and content changes without safety certification',()=>{
  for(const state of ['unscanned','scanning','reviewed','attention','blocked','incomplete','stale'])assert.ok(securityLabel(item(state)).label)
  assert.equal(securityLabel(item('reviewed')).label,'未发现高风险项')
  assert.equal(securityLabel(item('unavailable',{blocking:false,review_reasons:['SECURITY_RECORD_INVALID']})).label,'报告不可用')
  assert.equal(securityLabel(item('incomplete',{blocking_reasons:['SECURITY_HIGH_RISK']})).label,'高风险告警')
  assert.equal(securityLabel(item('blocked',{verdict:'high_risk'})).next,'请复核具体证据，并决定是否停用或隔离；报告本身不阻止普通操作。')
  assert.equal(securityLabel(item('stale',{verdict:'high_risk'})).label,'内容已变化')
  assert.equal(securityLabel(item('blocked',{disposition:{mode:'quarantine',status:'applied'}})).label,'已隔离')
  assert.equal(securityLabel(item('blocked',{disposition:{mode:'quarantine',status:'failed'}})).label,'处置待核对')
  assert.equal(securityLabel(item('scanning',{verdict:'high_risk'})).label,'检查中')
  assert.doesNotMatch(securityLabel(item('reviewed')).label,/安全认证|无毒/)
})
test('technical reasons retain file locations and workshop disposal never promises moving Steam files',()=>{
  assert.match(securityReason({path:'payload.zip',reason:'script_or_nested_archive_not_analyzed'}),/payload.zip.*未分析/)
  assert.equal(securityReason('SECURITY_HIGH_RISK'),'报告发现高风险组合，请结合用途复核；不会自动停用或阻止启动。')
  assert.match(securityEffect('logical_quarantine_files_remain'),/文件仍在 Steam 工坊/)
  assert.match(securityReason('source_resolution_unproven'),/无法确认实际加载来源/)
  assert.match(securityReason('source_ambiguous'),/重复包/)
  assert.match(securityReason('rule_evidence_limited'),/覆盖不完整/)
  assert.match(securityReason('embedded_payload_evidence_limited'),/覆盖不完整/)
  assert.match(securityReason('WORKER_PROTOCOL_INVALID'),/重启管理器/)
})
test('native rejection is not represented as a local no-high-risk success',()=>{
  for(const code of ['SECURITY_GAME_SCAN_INCOMPLETE','SECURITY_GAME_SCAN_REFUSED','SECURITY_UNDECLARED_CAPABILITIES']) {
    assert.equal(securityLabel(item('blocked',{verdict:'no_high_risk_findings',blocking_reasons:[code]})).tone,'bad')
    assert.doesNotMatch(securityReason(code),/^SECURITY_/)
  }
  assert.equal(nativeScanLabel(null).label,'尚未检查')
  assert.equal(nativeScanLabel({status:'scanned'}).label,'检查未完成')
  assert.equal(nativeScanLabel({status:'scanned',error:'timeout'}).label,'检查未完成')
  assert.equal(nativeScanLabel({status:'scanned',refused:true}).label,'原生检查拒绝')
  assert.equal(nativeScanLabel({status:'scanned',refused:false,undeclared:['network']}).label,'权限声明不完整')
  assert.equal(nativeScanLabel({status:'scanned',refused:false,error:null,undeclared:[]}).label,'原生检查通过')
})
test('remote card cannot inherit a local override or other content report',()=>{
  assert.equal(workshopSecurityItem([{id:'mod_a',source:'local',workshop_id:'123'}],{mod_a:item('reviewed')},'123'),null)
  const mods=[{id:'mod_a',source:'workshop',workshop_id:'123',package_revision:'new'}]
  assert.equal(workshopSecurityItem(mods,{mod_a:item('reviewed',{package_revision:'old'})},'123').item,undefined)
  assert.equal(workshopSecurityItem(mods,{mod_a:item('reviewed',{package_revision:'new'})},'123').item.state,'reviewed')
})
test('only restoration can attempt its pending recovery while ordinary writes remain blocked',()=>{
  const pending={state:'stopped',writable:false,blocking_reason:'NATIVE_RECOVERY_PENDING'}
  assert.equal(securityRestoreAllowed(pending),true)
  assert.equal(pending.writable,false)
  assert.equal(securityRestoreAllowed({state:'stopped',writable:true}),true)
  for(const state of ['running','unknown'])assert.equal(securityRestoreAllowed({...pending,state}),false)
  for(const blocking_reason of ['GUARD_RECOVERY_REQUIRED','GUARD_BUSY',null])assert.equal(securityRestoreAllowed({...pending,blocking_reason}),false)
  for(const flag of ['dirty','busy','scanning','externalChanged'])assert.equal(securityRestoreAllowed(pending,{[flag]:true}),false)
})
test('ordinary reads never start scanning, Defender, network or AI',async()=>{
  const app=setup(command=>command==='security.detail'?{item:item(),report:null,records:[]}:status());await app.runtime.setInstallation(installation);await app.runtime.select('mod_a');assert.deepEqual(app.calls.map(c=>c[0]),['security.status','security.detail']);assert.equal(app.timers.size,0);app.runtime.dispose()
})
test('scan progress polls only activity; completed scan fetches reports once',async()=>{
  let done=false;const app=setup(command=>command==='security.activity'?{installation_id:installation,scanning:!done,operation:{state:done?'completed':'running',done:done?1:0,total:1,current_id:done?null:'mod_a',current_name:done?null:'Mod A',phase:'static'}}:command==='security.scan'?status([item('scanning')],{scanning:true,operation:{state:'queued',done:0,total:1}}):status([item(done?'reviewed':'unscanned')]))
  await app.runtime.setInstallation(installation);await app.runtime.scan();await app.tick();assert.equal(app.state.status.operation.current_name,'Mod A');assert.equal(app.calls.filter(([c])=>c==='security.status').length,1)
  done=true;await app.tick();assert.equal(app.calls.filter(([c])=>c==='security.status').length,2);assert.equal(app.timers.size,0);assert.equal(app.state.items.mod_a.state,'reviewed');app.runtime.dispose()
})
test('background static scan polls and stops at terminal; Defender is only explicit',async()=>{
  let running=false;const app=setup(command=>command==='security.scan'?(running=true,status([item('scanning')],{scanning:true,operation:{state:'running',done:0,total:1}})):status([item(running?'reviewed':'unscanned')],{operation:running?{state:'completed',done:1,total:1}:null}));await app.runtime.setInstallation(installation);await app.runtime.scan(['mod_a','mod_a']);assert.deepEqual(app.calls.at(-1),['security.scan',{installation_id:installation,ids:['mod_a'],antivirus:false}]);assert.equal(app.state.starting,false);assert.equal(app.timers.size,1);assert.equal(await app.runtime.scan(),null);await app.tick();assert.equal(app.timers.size,0);assert.equal(app.state.items.mod_a.state,'reviewed');await app.runtime.scan(['mod_a'],true);assert.equal(app.calls.at(-1)[1].antivirus,true);app.runtime.dispose();assert.equal(app.timers.size,0)
})
test('all scan omits ids, preserves failure and never marks unknown content reviewed',async()=>{
  const app=setup(command=>{if(command==='security.scan')throw new Error('解析器不可用');return status()});await app.runtime.setInstallation(installation);await app.runtime.scan();assert.deepEqual(app.calls.at(-1)[1],{installation_id:installation,antivirus:false});assert.equal(app.state.error,'解析器不可用');assert.equal(app.state.items.mod_a.state,'unscanned');assert.equal(app.state.starting,false);app.runtime.dispose()
})
test('late status from a previous installation cannot replace current state',async()=>{
  const old=deferred(),app=setup((_,payload)=>payload.installation_id===installation?old.promise:status([],{installation_id:'other'}));const pending=app.runtime.setInstallation(installation);await app.runtime.setInstallation('other');old.resolve(status([item('reviewed')]));await pending;assert.equal(app.state.status.installation_id,'other');assert.deepEqual(app.state.items,{});app.runtime.dispose()
})
test('late detail from previous selection is not cached into the current view',async()=>{
  const first=deferred(),app=setup((command,payload)=>command==='security.detail'?payload.id==='mod_a'?first.promise:{item:{id:'mod_b'},report:{name:'B'}}:status([item(),{id:'mod_b'}]));await app.runtime.setInstallation(installation);const pending=app.runtime.select('mod_a');await app.runtime.select('mod_b');first.resolve({item:item(),report:{name:'A'}});await pending;assert.equal(app.state.selectedId,'mod_b');assert.equal(app.state.details.mod_a,undefined);assert.equal(app.state.details.mod_b.report.name,'B');app.runtime.dispose()
})
test('source changes clear displayed trust and invalidate old in-flight report',async()=>{
  const pending=deferred();let statusCount=0;const app=setup(command=>command==='security.detail'?pending.promise:++statusCount<=2?status([item('reviewed')]):status([item('unscanned',{source:'local'})]));await app.runtime.setInstallation(installation);app.runtime.inventory([{id:'mod_a',source:'workshop',package_revision:'a'}]);await Promise.resolve();await Promise.resolve();const read=app.runtime.select('mod_a');app.runtime.inventory([{id:'mod_a',source:'local',package_revision:'b'}]);pending.resolve({item:item('reviewed'),report:{name:'old source'}});await read;assert.equal(app.state.details.mod_a,undefined);app.runtime.dispose()
})
test('content/report changes invalidate detail cache and request a fresh report',async()=>{
  let version='a';const app=setup(command=>command==='security.detail'?{item:item('reviewed',{report_id:version,content_hash:version}),report:{name:version}}:status([item('reviewed',{report_id:version,content_hash:version})]));await app.runtime.setInstallation(installation);await app.runtime.select('mod_a');assert.equal(app.state.details.mod_a.report.name,'a');version='b';await app.runtime.refresh();await Promise.resolve();assert.equal(app.state.details.mod_a.report.name,'b');app.runtime.dispose()
})
test('a report for another active source cannot appear checked beside the current native inventory',async()=>{
  const old=item('reviewed',{source:'local',package_revision:'old'}),app=setup(command=>command==='security.detail'?{item:old,report:{name:'wrong source'}}:status([old]));await app.runtime.setInstallation(installation);app.runtime.inventory([{id:'mod_a',source:'workshop',workshop_id:'123',package_revision:'new'}]);await app.runtime.refresh();assert.equal(app.state.items.mod_a.state,'stale');assert.equal(await app.runtime.select('mod_a'),null);assert.equal(app.state.details.mod_a,undefined);assert.match(app.state.error,/来源或内容已变化/);app.runtime.dispose()
})
test('preview is read-only; confirmed disposition sends only opaque token and restore only record ID',async()=>{
  const app=setup((command,payload)=>command==='security.dispose.preview'?{preview_id:'p',id:payload.id,mode:payload.mode,affected_ids:['mod_a','dependent']}:['security.dispose','security.restore'].includes(command)?{record:{id:'r'},status:status()}:status());await app.runtime.setInstallation(installation);await app.runtime.preview('mod_a','quarantine');assert.equal(app.calls.filter(c=>c[0]==='security.dispose').length,0);await app.runtime.disposePreview();assert.deepEqual(app.calls.at(-1),['security.dispose',{installation_id:installation,preview_id:'p'}]);await app.runtime.restore('r');assert.deepEqual(app.calls.at(-1),['security.restore',{installation_id:installation,record_id:'r'}]);assert.equal(app.state.preview,null);app.runtime.dispose()
})
test('failed disposition preserves error and does not fake applied status',async()=>{
  const app=setup(command=>command==='security.dispose.preview'?{preview_id:'p'}:command==='security.dispose'?Promise.reject(new Error('内容已变化')):status());await app.runtime.setInstallation(installation);await app.runtime.preview('mod_a','quarantine');assert.equal(await app.runtime.disposePreview(),null);assert.equal(app.state.actionError,'内容已变化');assert.deepEqual(app.state.status.records,[]);app.runtime.dispose()
})
test('dispose clears timers and late accepted scan replies cannot mutate another view',async()=>{
  const pending=deferred(),app=setup(command=>command==='security.scan'?pending.promise:status());await app.runtime.setInstallation(installation);const scan=app.runtime.scan();app.runtime.dispose();const before=structuredClone(app.state);pending.resolve(status([item('scanning')],{scanning:true}));await scan;assert.deepEqual(app.state,before);assert.equal(app.timers.size,0)
})
test('security bridge validates native IDs, bounded scan, explicit booleans and opaque disposition tokens',async()=>{
  const calls=[],bridge=createBridge({transport:async req=>{calls.push(req);return {request_id:req.request_id,ok:true,data:{}}}})
  await bridge.request('security.scan',{installation_id:installation,ids:['mod_a'],antivirus:false});await bridge.request('security.dispose.preview',{installation_id:installation,id:'mod_a',mode:'quarantine'});await bridge.request('security.restore',{installation_id:installation,record_id:'f'.repeat(32)})
  for(const payload of [{ids:['../bad']},{ids:[]},{antivirus:'true'},{path:'C:/private'},{ids:Array.from({length:1025},(_,i)=>'m'+i)}])await assert.rejects(bridge.request('security.scan',{installation_id:installation,...payload}),{code:'INVALID_REQUEST'})
  await assert.rejects(bridge.request('security.dispose',{installation_id:installation,preview_id:'f'.repeat(32),enabled_ids:[]}),{code:'INVALID_REQUEST'});assert.equal(calls.length,3);await bridge.dispose()
})
