import test from 'node:test'
import assert from 'node:assert/strict'
import { createCompatibilityStore, reasonCount, releaseLabel, candidateSummary } from '../src/features/profiles/compatibilityStore.js'
const report = { blocking: true, findings: [{ id: 'one', category: 'dependency', severity: 'blocker', message: '缺少依赖', source: 'assembly_ref', scope: { fingerprint: 'hash' }, reason_chain: ['a', 'b'] }], enable_preview: { release_ids: ['a','b'], added_release_ids: ['b'], complete: true, candidate_blocking: true, candidate_finding_codes: ['MISSING_DEPENDENCY'], candidate_unknown_count: 1, candidate_plan_ready: false }, disable_preview: { release_ids: ['a'], affected_release_ids: ['b'], candidate_blocking: false, candidate_finding_codes: [], candidate_unknown_count: 1, candidate_plan_ready: false }, unknowns: ['loader'], cycles: [] }
test('compatibility case015 dictionary reason chains count relations and preserve named releases', () => {
  assert.equal(reasonCount({component_ids:['a','b'],relation_ids:['r1']}),1)
  assert.equal(reasonCount(['r1','r2']),2)
  assert.equal(releaseLabel({r1:'中文模组'},'r1'),'中文模组')
  assert.equal(releaseLabel({},'uuid'),'未知发布版本')
})
test('compatibility case026 complete closure still reports candidate blockers and uncertain compatibility', () => {
  assert.match(candidateSummary({complete:true,candidate_blocking:true,candidate_finding_codes:['FILE_CONFLICT']}),/集合完整；候选组合仍有阻断/)
  assert.match(candidateSummary({complete:true,candidate_blocking:false,candidate_finding_codes:[]}),/不代表运行兼容/)
  assert.match(candidateSummary({complete:false}),/阻断状态未确认/)
})
function fixture(request = async () => ({ task_id: 't', state: 'running' })) {
  const state = {}, calls = [], updates = []; let cancellations = 0, disposals = 0, error
  const store = createCompatibilityStore({ async request(command,payload) { calls.push({command,payload}); return request(command,payload) }, watchTask(id,update,onError) { updates.push(update); error = onError; return { async cancel() { cancellations++ }, async dispose() { disposals++ } } } },state)
  store.setProfile({ profile_id: 'p', revision: 3 })
  return {state,store,calls,finish(data=report,status='succeeded',index=updates.length-1) { updates[index]({state:status,data}) }, fail() {error()}, get cancellations(){return cancellations}, get disposals(){return disposals} }
}
test('compatibility case015 retains dependency reasons, evidence scope and unknowns without claiming runtime confirmation', async () => {
  const f=fixture(); await f.store.check(); f.finish(); assert.equal(f.state.status,'ready'); assert.deepEqual(f.state.report,report)
  assert.deepEqual(f.calls[0],{command:'compatibility.check',payload:{profile_id:'p',expected_revision:3,profile_kind:'draft'}})
  assert.deepEqual(f.state.report.findings[0].reason_chain,['a','b']); assert.equal(f.state.report.findings[0].scope.fingerprint,'hash')
})
test('compatibility case026 enable closure preview is complete and never writes selections', async () => {
  const f=fixture(); await f.store.check('enable',['a','a']); f.finish()
  assert.deepEqual(f.calls[0].payload.enable_release_ids,['a']); assert.deepEqual(f.state.report.enable_preview.release_ids,['a','b'])
  assert.equal(f.calls.length,1); assert.equal(f.calls[0].command,'compatibility.check')
})
test('compatibility case026 disable impact preview preserves full affected set without editing Profile', async () => {
  const f=fixture(); await f.store.check('disable',['a']); f.finish()
  assert.deepEqual(f.calls[0].payload.disable_release_ids,['a']); assert.deepEqual(f.state.report.disable_preview.affected_release_ids,['b']); assert.equal(f.calls.length,1)
})
test('compatibility case015 case026 revision change invalidates report and ignores late task completion', async () => {
  const f=fixture(); await f.store.check(); f.store.setProfile({profile_id:'p',revision:4}); f.finish()
  assert.equal(f.state.status,'stale'); assert.equal(f.state.report,null); assert.equal(f.state.busy,false); assert.equal(f.cancellations,1)
  await f.store.check(); f.finish(); assert.equal(f.state.status,'ready'); assert.equal(f.calls[1].payload.expected_revision,4)
})
test('compatibility case026 profile switch discards prior findings including file conflicts', async () => {
  const f=fixture(); await f.store.check(); f.finish({...report,findings:[{category:'file',severity:'blocker',code:'FILE_CONFLICT'}]})
  f.store.setProfile({profile_id:'other',revision:3}); assert.equal(f.state.report,null); assert.equal(f.state.status,'stale')
})
test('compatibility case015 duplicate click cannot start competing checks', async () => {
  const f=fixture(); await f.store.check(); await f.store.check(); assert.equal(f.calls.length,1)
  await f.store.check('invalid'); assert.equal(f.calls.length,1)
})
test('compatibility case015 cancellation clears checking without inventing a result', async () => {
  const f=fixture(); await f.store.check(); await f.store.cancel(); f.finish(null,'cancelled')
  assert.equal(f.cancellations,1); assert.equal(f.state.status,'cancelled'); assert.equal(f.state.report,null); assert.equal(f.state.error,'')
})
test('compatibility case015 rejected report and subscription failure remain errors', async () => {
  const f=fixture(); await f.store.check(); f.finish({error_code:'LIMIT',message:'报告超过限制'})
  assert.equal(f.state.status,'error'); assert.equal(f.state.error,'报告超过限制')
  await f.store.check(); f.fail(); assert.equal(f.state.status,'error'); assert.equal(f.state.report,null)
})
test('compatibility case026 unmount releases task subscription and suppresses late results', async () => {
  const f=fixture(); await f.store.check(); await f.store.dispose(); f.finish()
  assert.equal(f.disposals,1); assert.equal(f.cancellations,1); assert.equal(f.state.report,null)
})
test('compatibility case015 immediate task result is handled without a second subscription', async () => {
  const f=fixture(async()=>({task_id:'t',state:'succeeded',data:report})); await f.store.check(); assert.equal(f.state.status,'ready'); assert.deepEqual(f.state.report,report)
})
test('compatibility case026 switching profile before initial response cancels old task without subscribing', async () => {
  let resolve
  const f=fixture(command => command === 'compatibility.check' ? new Promise(done => {resolve=done}) : Promise.resolve({state:'cancelled'}))
  const checking=f.store.check(); await new Promise(done=>setImmediate(done))
  f.store.setProfile({profile_id:'new',revision:1}); resolve({task_id:'old',state:'running'}); await checking
  assert.deepEqual(f.calls[1],{command:'task.cancel',payload:{task_id:'old'}})
  assert.equal(f.disposals,0); assert.equal(f.state.report,null)
})
test('compatibility case026 unmount before initial response cancels old task without subscribing', async () => {
  let resolve
  const f=fixture(command => command === 'compatibility.check' ? new Promise(done => {resolve=done}) : Promise.resolve({state:'cancelled'}))
  const checking=f.store.check(); await new Promise(done=>setImmediate(done)); await f.store.dispose()
  resolve({task_id:'old',state:'running'}); await checking
  assert.equal(f.calls[1].command,'task.cancel'); assert.equal(f.disposals,0)
})
test('compatibility case015 subscription error disposes only its own generation watcher', async () => {
  const f=fixture(); await f.store.check(); f.fail(); assert.equal(f.disposals,1)
  await f.store.check(); f.finish(); assert.equal(f.state.status,'ready'); assert.equal(f.disposals,1)
})
test('compatibility case015 start failure explains missing versions and supports a new check', async () => {
  let first=true
  const f=fixture(async()=>{
    if(first) { first=false; throw Object.assign(new Error('unresolved'),{code:'COMPATIBILITY_UNRESOLVED'}) }
    return {task_id:'t2',state:'succeeded',data:{...report,findings:[],blocking:false}}
  })
  await f.store.check()
  assert.equal(f.state.status,'error'); assert.equal(f.state.busy,false)
  assert.match(f.state.error,/导入对应版本/)
  await f.store.check()
  assert.equal(f.state.status,'ready'); assert.equal(f.state.report.blocking,false); assert.equal(f.state.error,'')
})
test('compatibility case015 safe bridge errors remain visible instead of a silent click', async () => {
  const f=fixture(async()=>{throw new Error('当前检查服务不可用，请重试。')})
  await f.store.check()
  assert.equal(f.state.error,'当前检查服务不可用，请重试。'); assert.equal(f.state.status,'error')
})
