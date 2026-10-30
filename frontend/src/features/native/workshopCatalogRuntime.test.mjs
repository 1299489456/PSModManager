import test from 'node:test'
import assert from 'node:assert/strict'
import { createWorkshopCatalogRuntime, catalogStatusView } from './workshopCatalogRuntime.js'
import { workshopImageCache } from './workshopImageCache.js'
import { createWorkshopTranslationRuntime } from './workshopTranslationRuntime.js'
test('card selection is immediate, a slow previous detail cannot replace the latest, and repeat visits use cache',async()=>{
  const state={},pending={},calls=[];const runtime=createWorkshopCatalogRuntime({request:async(c,p)=>{calls.push([c,p]);return new Promise(r=>pending[p.workshop_id]=r)}},state);runtime.setInstallation('install');const first=runtime.select({workshop_id:'1',title:'第一项',preview_url:'cover'});assert.equal(state.selected.title,'第一项');assert.equal(state.detailLoading,true);const second=runtime.select({workshop_id:'2',title:'第二项'});pending['1']({workshop_id:'1',title:'第一项完整'});await first;assert.equal(state.selected.workshop_id,'2');pending['2']({workshop_id:'2',title:'第二项完整'});await second;runtime.back();await runtime.select('1');assert.equal(state.selected.title,'第一项完整');assert.equal(calls.length,2);runtime.dispose()
})
test('browsing uses selected page size and cannot overwrite after installation changes',async()=>{let release;const state={};const runtime=createWorkshopCatalogRuntime({request:async(c,p)=>{assert.equal(p.page_size,48);return new Promise(r=>release=r)}},state);runtime.setInstallation('a');const p=runtime.browse({page:2,page_size:48});runtime.setInstallation('b');release({items:[{workshop_id:'1'}]});await p;assert.equal(state.result,null);runtime.dispose()})
test('controlled image cache deduplicates reads and reuses the ready image across components',async()=>{let calls=0;const bridge={request:async()=>{calls++;return{state:'ready',data_url:'data:image/png;base64,AAAA'}}};const a=workshopImageCache(bridge),b=workshopImageCache(bridge);await Promise.all([a.load('1'),b.load('1')]);await b.load('1');assert.equal(calls,1);assert.equal(b.peek('1').state,'ready')})
test('changed preview provenance revalidates the host image while a warm unchanged cover stays local',async()=>{let calls=0;const cache=workshopImageCache({request:async()=>({state:'ready',data_url:`data:image/png;base64,${++calls}`})});await cache.load('1','old-url');await cache.load('1','old-url');assert.equal(calls,1);await cache.load('1','new-url');assert.equal(calls,2);assert.equal(cache.peek('1','old-url'),undefined);assert.equal(cache.peek('1','new-url').data_url,'data:image/png;base64,2')})
test('translation survives selection and pure display toggles; only explicit refresh creates another task',async()=>{
  const state={},calls=[];let update;const bridge={request:async(c,p)=>{calls.push([c,p]);return c==='ai.translation.cache'?(p.workshop_id==='1'&&calls.some(x=>x[0]==='workshop.translate')?{hit:true,translation:{description:'译文'}}:{hit:false}):c==='workshop.translate'?{task_id:'t',state:'queued'}:{}},watchTask:(id,fn)=>{update=fn;return{dispose(){}}}};const runtime=createWorkshopTranslationRuntime(bridge,state);await runtime.select('1');await runtime.translate();update({task_id:'t',state:'running',data:{part:'description',index:0,text:'译文'}});update({task_id:'t',state:'succeeded',data:{completed:true,translated:1}});state.show=false;await runtime.select('2');await runtime.select('1');assert.equal(state.show,false);assert.equal(runtime.text('description'),'译文');state.show=true;await runtime.translate();assert.equal(calls.filter(x=>x[0]==='workshop.translate').length,1);await runtime.translate(undefined,true);assert.equal(calls.filter(x=>x[0]==='workshop.translate').at(-1)[1].force_refresh,true);runtime.dispose()
})
test('persisted translation cache lookup displays a translation without a new AI task',async()=>{const state={},calls=[];const runtime=createWorkshopTranslationRuntime({request:async(c)=>{calls.push(c);return{hit:true,translation:{title:'译名',description:'译文'},model:'configured-model'}}},state);await runtime.select('1');assert.equal(runtime.text('title'),'译名');assert.equal(state.cached,true);assert.deepEqual(calls,['ai.translation.cache']);runtime.dispose()})
test('source or provider invalidation is checked again without sending a translation request',async()=>{let result={hit:true,translation:{description:'旧译文'}};const calls=[],state={};const runtime=createWorkshopTranslationRuntime({request:async c=>{calls.push(c);return result}},state);await runtime.select('1');result={hit:false,source_pending:true};await runtime.hydrateCache();assert.equal(runtime.text('description'),'旧译文');result={hit:false};await runtime.hydrateCache();assert.equal(runtime.text('description'),'');assert.equal(state.status,'idle');assert.ok(calls.every(c=>c==='ai.translation.cache'));runtime.dispose()})
test('a cached summary is completed automatically after the background detail fetch',async t=>{t.mock.timers.enable({apis:['setTimeout']});let calls=0;const state={},runtime=createWorkshopCatalogRuntime({request:async()=>++calls===1?{workshop_id:'1',description:'摘要',source_pending:true,refreshing:true}:{workshop_id:'1',description:'完整介绍',source_pending:false,refreshing:false}},state);await runtime.select({workshop_id:'1',title:'卡片'});assert.equal(state.selected.description,'摘要');assert.equal(state.detailLoading,true);t.mock.timers.tick(1000);await Promise.resolve();await Promise.resolve();assert.equal(state.selected.description,'完整介绍');assert.equal(state.detailLoading,false);runtime.dispose()})

const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()}
test('catalog freshness is not a failed Steam connection',()=>{
  const cached={items:[{}],stale:true,refreshing:true}
  assert.equal(catalogStatusView(cached).kind,'loading')
  assert.equal(catalogStatusView(cached).retry,false)
  assert.doesNotMatch(catalogStatusView(cached).text,/重新连接|网络读取未完成/)
  assert.equal(catalogStatusView({...cached,stale:false,refreshing:false}),null)
  const error=catalogStatusView({...cached,refreshing:false,network_error:{message:'公开网络超时'}})
  assert.equal(error.kind,'error');assert.match(error.text,/公开目录读取失败.*公开网络超时/)
})
test('cold directory polls until ready without reconnecting Steam',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const calls=[],state={},runtime=createWorkshopCatalogRuntime({request:async(c,p)=>{calls.push([c,p]);return calls.length===1?{items:[],loading:true,refreshing:true,retry_after_ms:750}:{items:[{workshop_id:'1'}],loading:false,refreshing:false}}},state)
  await runtime.browse({sort:'recent'});assert.equal(state.listLoading,true)
  t.mock.timers.tick(750);await flush()
  assert.equal(state.result.items[0].workshop_id,'1');assert.equal(state.listLoading,false)
  assert.ok(calls.every(([c,p])=>c==='workshop.browse'&&p.sort==='recent'&&!p.refresh))
  runtime.dispose()
})
test('switching filters immediately adopts that filter cache, and last request wins',async()=>{
  const state={},pending=[],runtime=createWorkshopCatalogRuntime({request:(c,p)=>new Promise(resolve=>pending.push({p,resolve}))},state)
  const hot=runtime.browse({sort:'hot'});pending[0].resolve({items:[{title:'hot'}]});await hot
  const recent=runtime.browse({sort:'recent'});assert.equal(state.result,null);assert.equal(state.listLoading,true)
  const back=runtime.browse({sort:'hot'});assert.equal(state.result.items[0].title,'hot');assert.equal(state.listLoading,false)
  pending[1].resolve({items:[{title:'recent'}]});await recent
  assert.equal(state.result.items[0].title,'hot')
  pending[2].resolve({items:[{title:'hot-new'}]});await back;assert.equal(state.result.items[0].title,'hot-new')
  runtime.dispose()
})
test('repeated requests coalesce and obsolete poll cannot change a newer filter',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const state={},pending=[],runtime=createWorkshopCatalogRuntime({request:(c,p)=>new Promise(resolve=>pending.push({p,resolve}))},state)
  const one=runtime.browse({sort:'hot'}),two=runtime.browse({sort:'hot',refresh:true})
  assert.equal(pending.length,1)
  pending[0].resolve({items:[{title:'hot'}],refreshing:true,retry_after_ms:750});await Promise.all([one,two])
  const next=runtime.browse({sort:'new'});pending[1].resolve({items:[{title:'new'}]});await next
  t.mock.timers.tick(2000);await flush()
  assert.equal(pending.length,2);assert.equal(state.result.items[0].title,'new')
  runtime.dispose()
})
test('explicit refresh shares an in-flight detail rather than racing duplicate requests',async()=>{
  let resolve,calls=0;const state={},runtime=createWorkshopCatalogRuntime({request:()=>{calls++;return new Promise(r=>resolve=r)}},state)
  const first=runtime.select({workshop_id:'1',title:'card'}),second=runtime.select('1',true)
  assert.equal(calls,1);resolve({workshop_id:'1',title:'complete'});await Promise.all([first,second])
  assert.equal(state.selected.title,'complete');runtime.dispose()
})
