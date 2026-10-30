<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import Sheet from './GardenSheet.vue'
import Icon from './GardenIcon.vue'
import Report from './NativeSecurity.vue'
import { nameOf, sourceText, sizeText, dateText, shortDate } from './nativeModel.js'
import { securityLabel, securityEffect, securityReason, securityRestoreAllowed, createSecurityRuntime } from './securityRuntime.js'
import { GROUPS, summarize, visibleChecks, quickTargets, findingCounts } from './securityOverview.js'
import { selectIds, toggleAll } from './listSelection.js'
import { createSecurityAiRuntime, securityAiWarnings } from './securityAiRuntime.js'
import { diagnosticMarkdown } from '../ai/diagnosticMarkdown.js'
const props=defineProps({bridge:Object,installation:Object,active:Boolean,mods:{type:Array,default:()=>[]},enabledIds:{type:Array,default:()=>[]},focus:Object,nativeRuntime:Object,dirty:Boolean,busy:Boolean})
const emit=defineEmits(['settings','changed','mutating','reveal'])
const selected=ref(null),view=ref('summary'),selection=ref({list:'checks',ids:[],anchor:null}),dialog=ref(null),record=ref(null),aiFocus=ref([])
const filter=reactive({query:'',group:'all',sort:'risk',enabledOnly:false})
const state=reactive({}),security=createSecurityRuntime(props.bridge,state,{active:false})
const installationId=computed(()=>props.installation?.installation_id)
const counts=computed(()=>summarize(props.mods,state.items))
const visibleMods=computed(()=>visibleChecks(props.mods,state.items,{...filter,enabledIds:props.enabledIds}))
const quick=computed(()=>quickTargets(props.mods,state.items))
const enabledSet=computed(()=>new Set(props.enabledIds))
const selectedIds=computed(()=>selection.value.ids.filter(id=>props.mods.some(m=>m.id===id)))
const allSelected=computed(()=>visibleMods.value.length>0&&visibleMods.value.every(m=>selectedIds.value.includes(m.id)))
const writeAllowed=computed(()=>props.nativeRuntime?.state==='stopped'&&props.nativeRuntime.writable!==false&&!props.busy&&!state.mutating)
const canRestore=computed(()=>securityRestoreAllowed(props.nativeRuntime,{dirty:props.dirty,busy:props.busy||state.mutating,scanning:scanning.value}))
const byId=id=>props.mods.find(mod=>mod.id===id)
const aiState=reactive({}),ai=createSecurityAiRuntime(props.bridge,aiState)
const reportBody=ref(null),reply=ref(null),list=ref(null)
const mod=computed(()=>props.mods.find(m=>m.id===selected.value)),item=computed(()=>state.items[selected.value]),detail=computed(()=>state.details[selected.value])
const label=computed(()=>securityLabel(item.value))
const findingTotal=computed(()=>findingCounts(detail.value?.report?.findings||[]))
const SHORT={'未发现高风险项':'未见高风险','原生检查未完成':'原生未完成','权限声明不完整':'权限未声明'}
const shortLabel=item=>{const value=securityLabel(item).label;return SHORT[value]||value}
const findingTitle=id=>detail.value?.report?.findings?.find(f=>f.id===id)?.title||id
const scanning=computed(()=>state.starting||state.status?.scanning),operation=computed(()=>state.status?.operation)
const progress=computed(()=>operation.value?.total?Math.min(100,(operation.value.done||0)/operation.value.total*100):0)
const phaseNames={queued:'等待开始',reading:'读取文件',hashing:'读取文件',analyzing:'静态分析',static:'静态分析',native_scan:'原生检查',antivirus:'Defender 扫描',verifying:'核对内容',writing:'保存报告',saving_report:'保存报告',completed:'检查完成',failed:'检查未完成'}
const tabs=computed(()=>[['summary','概要',null],['findings','风险证据',findingTotal.value.all||null],['content','内容',null],['diff','变化',null],['coverage','覆盖',null],['ai','AI 解读',null]])
watch(selected,id=>{if(view.value==='ai')view.value='summary';aiFocus.value=[];if(state.selectedId!==id)void security.select(id)})
watch(()=>[installationId.value,selected.value,item.value?.report_id,aiFocus.value.join()],([installation_id,id,report_id])=>{ai.setScope(installation_id&&id&&report_id?{installation_id,id,report_id,...(aiFocus.value.length?{finding_ids:[...aiFocus.value]}:{})}:null)},{immediate:true})
watch(()=>props.mods,mods=>{security.inventory(mods);const ids=mods.map(m=>m.id);selection.value.ids=selection.value.ids.filter(id=>ids.includes(id));if(!ids.includes(selected.value))selected.value=visibleMods.value[0]?.id||ids[0]||null})
watch([installationId,()=>props.active],([id,active])=>{
  // Reset synchronously while inactive: a fast page/installation change cannot
  // resume a stale watcher and start reading reports in an ordinary workspace.
  if(id!==state.installationId){
    void security.setActive(false);void security.setInstallation(id);security.inventory(props.mods)
    selection.value={list:'checks',ids:[],anchor:null};selected.value=props.mods[0]?.id||null
    dialog.value=null;record.value=null;void security.select(selected.value)
  }
  void security.setActive(active)
  if(!active&&aiState.busy)void ai.cancel()
},{immediate:true})
// Keep the selected row in view; the list re-sorts once reports arrive, so a pending
// reveal is repeated after the next status load.
let revealPending=false
function revealSelected(){nextTick(()=>list.value?.querySelector(`[data-security-id="${CSS.escape(selected.value||'')}"]`)?.scrollIntoView({block:'nearest'}))}
// "Check this mod" from the mods page only selects it here; it never starts a scan by itself.
watch(()=>props.focus?.seq,()=>{const id=props.focus?.id;if(!id||!byId(id))return;filter.query='';filter.group='all';selected.value=id;view.value='summary';revealPending=true;revealSelected()},{immediate:true})
watch(()=>state.status,()=>{if(revealPending&&!state.loading){revealPending=false;revealSelected()}})
watch(()=>[filter.group,filter.sort,filter.enabledOnly],revealSelected)
watch(()=>state.mutating,value=>emit('mutating',value),{immediate:true})
watch([selected,view],()=>nextTick(()=>{if(reportBody.value)reportBody.value.scrollTop=0}))
watch(()=>Boolean(aiState.text),visible=>{if(visible)nextTick(()=>reply.value?.scrollIntoView({block:'nearest'}))})
function choose(id,event={}){selected.value=id;selection.value=selectIds(selection.value,'checks',id,visibleMods.value.map(m=>m.id),event)}
function check(id){const ids=new Set(selectedIds.value);ids.has(id)?ids.delete(id):ids.add(id);selection.value={list:'checks',ids:[...ids],anchor:id};selected.value=id}
function selectAll(){selection.value=toggleAll(selection.value,'checks',visibleMods.value.map(m=>m.id))}
function keyboard(event){
  const ids=visibleMods.value.map(m=>m.id),key=event.key.toLowerCase()
  if((event.ctrlKey||event.metaKey)&&key==='a'){event.preventDefault();selection.value=toggleAll(selection.value,'checks',ids,true);return}
  if(['arrowdown','arrowup'].includes(key)&&ids.length){event.preventDefault();const at=Math.max(-1,ids.indexOf(selected.value));const next=ids[Math.min(ids.length-1,Math.max(0,at+(key==='arrowdown'?1:-1)))];if(event.shiftKey)check(next);else selected.value=next;nextTick(()=>list.value?.querySelector(`[data-security-id="${CSS.escape(next)}"]`)?.scrollIntoView({block:'nearest'}));return}
  if(key===' '&&selected.value){event.preventDefault();check(selected.value)}
}
async function scan(ids){if(ids===undefined||ids.length){await security.scan(ids);view.value='summary'}}
const preview=()=>ai.preview()
// Opening the AI tab only builds a local preview; the scope (whole report or chosen
// findings) must be applied before previewing, so a changed focus waits one tick.
async function openAi(ids){
  const same=ids===undefined||ids.join()===aiFocus.value.join()
  if(!same)aiFocus.value=[...ids]
  view.value='ai'
  if(!same)await nextTick()
  if(!same||!aiState.preview)await ai.preview()
}
async function action(event){
  if(event.kind==='security-scan'){await security.scan([selected.value]);return}
  if(event.kind==='security-refresh'){await security.refresh();await security.select(selected.value,true);return}
  if(event.kind==='security-defender'){dialog.value='defender';return}
  if(event.kind==='security-records'){record.value=null;dialog.value='records';await security.refresh();return}
  if(['security-disable','security-quarantine'].includes(event.kind)){dialog.value='dispose';await security.preview(selected.value,event.kind==='security-disable'?'disable':'quarantine')}
}
function close(){if(!state.mutating)dialog.value=null}
async function dispose(){if(!writeAllowed.value||props.dirty)return;const value=await security.disposePreview();if(value){dialog.value=null;emit('changed')}}
async function restore(){if(!canRestore.value||!record.value?.restorable)return;const value=await security.restore(record.value.id);if(value){record.value=null;emit('changed')}}
onUnmounted(()=>{ai.dispose();security.dispose();emit('mutating',false)})
</script>
<template>
  <div class="formal-ui"><section class="page on security-workspace" data-testid="security-workspace">
    <div class="garden-intro"><div><h1>模组检查</h1><p>需要排查时再检查：读取全部文件、静态分析代码，查看证据后由你决定如何处理。普通保存和启动不受影响。</p></div><div class="garden-intro-actions"><button class="garden-btn soft icon sm" title="刷新报告" :disabled="state.loading" @click="security.refresh()"><Icon name="refresh" size="sm"/></button><button data-testid="security-records" class="garden-btn sm" @click="action({kind:'security-records'})"><Icon name="history" size="sm"/>处置记录</button></div></div>

    <div class="security-overview">
      <div class="garden-chips sm" role="tablist" aria-label="按检查结果筛选"><button v-for="[key,text] in GROUPS" :key="key" role="tab" :aria-selected="filter.group===key" :class="{on:filter.group===key,care:key==='care'}" :data-group="key" @click="filter.group=key">{{ text }}<span>{{ counts[key] }}</span></button><button v-if="counts.scanning" class="sky-chip" disabled>检查中<span>{{ counts.scanning }}</span></button></div>
      <span class="sp"></span>
      <button v-if="quick.unscanned.length" class="garden-btn soft sm" :disabled="scanning" @click="scan(quick.unscanned)"><Icon name="search" size="sm"/>检查未检查的 {{ quick.unscanned.length }} 个</button>
      <button v-if="quick.stale.length" class="garden-btn soft sm" :disabled="scanning" @click="scan(quick.stale)"><Icon name="refresh" size="sm"/>重查已变化的 {{ quick.stale.length }} 个</button>
    </div>

    <div v-if="scanning||operation" class="security-panel-progress" :class="{done:!scanning}" data-testid="security-progress" role="status">
      <b>{{ scanning?'后台检查中':operation?.state==='completed'?'检查完成':'检查未完成' }}</b>
      <div class="garden-solar"><i :style="{width:(scanning&&!operation?.total?8:progress)+'%'}"></i></div>
      <span class="security-progress-count">{{ operation?.done??0 }} / {{ operation?.total??0 }}</span>
      <span v-if="scanning" class="security-progress-current" :title="operation?.current_file">{{ operation?.current_name||operation?.current_id||'正在准备' }}<template v-if="operation?.phase"> · {{ phaseNames[operation.phase]||operation.phase }}</template><code v-if="operation?.current_file">{{ operation.current_file }}</code></span>
      <span v-else-if="operation?.error" class="security-progress-current warn">{{ securityReason(operation.error) }}</span>
      <small v-if="scanning">可以切换页面，检查会在后台完成。</small>
    </div>
    <p v-if="state.error" class="care-note" role="alert">{{ state.error }}</p>

    <div class="security-panel-layout">
      <aside class="security-report-sidebar">
        <div class="security-list-tools"><label class="garden-search" :class="{has:filter.query}"><Icon name="search" size="sm"/><input v-model="filter.query" placeholder="搜索名称、ID、作者" aria-label="搜索检查列表"/><button class="search-clear" aria-label="清除搜索" @click="filter.query=''"><Icon name="x" size="xs"/></button></label>
          <div class="security-list-options"><span class="garden-select"><select v-model="filter.sort" aria-label="排序"><option value="risk">风险优先</option><option value="name">名称</option><option value="checked">最近检查</option></select></span><label class="security-toggle"><input v-model="filter.enabledOnly" type="checkbox"/>只看已启用</label><span class="sp"></span><button data-testid="security-select-all" class="garden-link" :disabled="!visibleMods.length" @click="selectAll">{{ allSelected?'取消全选':'全选' }}</button></div></div>
        <nav ref="list" class="security-report-list" aria-label="模组检查列表" tabindex="0" @keydown="keyboard">
          <div v-for="entry in visibleMods" :key="entry.id" class="security-report-entry" :class="[{on:selected===entry.id},'tone-'+securityLabel(state.items[entry.id]).tone]"><input type="checkbox" :data-testid="'security-pick-'+entry.id" :checked="selectedIds.includes(entry.id)" :aria-label="'选择 '+nameOf(entry)" @change="check(entry.id)"/><button :data-security-id="entry.id" :aria-current="selected===entry.id?'true':undefined" :title="nameOf(entry)" @click="choose(entry.id,$event)"><span class="security-entry-name"><b>{{ nameOf(entry) }}</b><i v-if="enabledSet.has(entry.id)" class="security-on-dot" title="已启用"></i></span><span class="security-entry-sub">{{ sourceText(entry) }} · v{{ entry.version||'?' }}<template v-if="state.items[entry.id]?.scanned_at"> · {{ shortDate(state.items[entry.id].scanned_at) }}</template></span><span class="tg" :class="securityLabel(state.items[entry.id]).tone" :title="securityLabel(state.items[entry.id]).label">{{ shortLabel(state.items[entry.id]) }}</span></button></div>
          <div v-if="!mods.length" class="garden-empty">还没有可检查的模组。</div><div v-else-if="!visibleMods.length" class="garden-empty">没有符合条件的模组。<button class="garden-link" @click="filter.query='';filter.group='all';filter.enabledOnly=false">清除筛选</button></div>
        </nav>
        <div class="security-list-foot"><span>已选 <b>{{ selectedIds.length }}</b> / {{ mods.length }}</span><span class="sp"></span><button data-testid="security-start-all" class="garden-btn soft sm" :disabled="scanning||!mods.length" @click="scan(undefined)">检查全部 {{ mods.length||'' }}</button><button data-testid="security-start-selected" class="garden-btn green sm" :disabled="scanning||!selectedIds.length" @click="scan(selectedIds)"><Icon name="search" size="sm"/>检查选中 {{ selectedIds.length||'' }}</button></div>
      </aside>

      <section ref="reportBody" class="security-panel-report" v-if="mod">
        <header class="security-report-head">
          <div class="security-report-title"><h3>{{ nameOf(mod) }}</h3><small><code>{{ mod.id }}</code> · {{ sourceText(mod) }} · v{{ mod.version||'未知' }}<template v-if="enabledSet.has(mod.id)"> · 已启用</template> · <button class="garden-link" @click="emit('reveal',mod.id)">在模组页查看</button></small></div>
          <div class="security-report-actions"><button data-testid="security-scan-mod" class="garden-btn green sm" :disabled="scanning" @click="action({kind:'security-scan'})"><Icon name="search" size="sm"/>{{ scanning?'正在检查…':item?.report_id?'重新检查':'检查' }}</button><button data-testid="security-ai-preview" class="garden-btn sm" :disabled="!item?.report_id||state.detailLoading" :title="item?.report_id?'先在本地生成发送预览，确认后才发送':'检查后才能解读'" @click="openAi([])"><Icon name="sprout" size="sm"/>AI 解读…</button></div>
        </header>
        <div class="security-tabs garden-chips sm" role="tablist" aria-label="检查报告"><button v-for="[key,text,count] in tabs" :key="key" role="tab" :aria-selected="view===key" :class="{on:view===key}" :disabled="key==='ai'&&!item?.report_id" @click="key==='ai'?openAi():view=key">{{ text }}<span v-if="count">{{ count }}</span></button></div>
        <Report v-if="view!=='ai'" :tab="view" :mod="mod" :item="item" :detail="detail" :loading="state.detailLoading" :scanning="scanning" :write-allowed="writeAllowed" :dirty="dirty" @tab="view=$event" @ai="openAi" @action="action"/>
        <section v-else class="security-ai" data-testid="security-ai-panel">
          <div class="security-ai-scope"><span>解读范围</span><button class="tg" :class="aiFocus.length?'ghost':'on'" :disabled="aiState.busy" @click="aiFocus.length&&openAi([])">整份报告</button><span v-for="id in aiFocus" :key="id" class="tg on" :title="id">{{ findingTitle(id) }}<button v-if="!aiState.busy" aria-label="移除" @click="openAi([])"><Icon name="x" size="xs"/></button></span><button v-if="!aiFocus.length&&findingTotal.all" class="garden-link" @click="view='findings'">或从风险证据中挑一条 →</button></div>
          <p class="muted">AI 只读取这份静态报告的有界摘要（文件、方法、IL 位置和规则说明），不会收到 DLL 或源码，也不能启停、隔离或批准模组。结论仍需你结合证据判断。</p>
          <p v-if="aiState.loading" role="status">正在本地生成发送预览…</p>
          <template v-if="aiState.preview">
            <dl class="garden-facts"><dt>分析服务</dt><dd>{{ aiState.preview.provider.name||'尚未配置' }} · {{ aiState.preview.provider.model||'未选模型' }}</dd><dt>发送地址</dt><dd>{{ aiState.preview.provider.base_url||'未配置' }}</dd><dt>发送内容</dt><dd>{{ sizeText(aiState.preview.bytes) }}<template v-if="aiState.preview.truncated"> · 报告较长，已按重要程度截取</template></dd></dl>
            <p v-for="(warning,i) in securityAiWarnings(aiState.preview)" :key="i" class="sign-note">{{ warning }}</p>
            <details :open="!aiState.sent" class="security-tech security-send-preview"><summary>实际将发送的完整消息</summary><article v-for="(message,i) in aiState.preview.messages" :key="i"><b>{{ ({system:'系统要求',user:'报告与问题',assistant:'已有回复'})[message.role]||message.role }}</b><pre>{{ message.content }}</pre></article></details>
            <div class="note-act"><button data-testid="security-ai-send" class="garden-btn green sm" :disabled="aiState.busy||aiState.sent||!aiState.preview.provider.configured" @click="ai.send">{{ aiState.busy?'正在分析…':aiState.sent?'本次已发送':'确认发送并分析' }}</button><button v-if="!aiState.busy" class="garden-link" @click="preview">重新生成预览</button><button v-if="!aiState.preview.provider.configured" class="garden-link" @click="emit('settings')">配置 AI 分析服务</button><button v-if="aiState.busy" data-testid="security-ai-cancel" class="garden-link" :disabled="aiState.cancelling" @click="ai.cancel">取消分析</button></div>
          </template>
          <p v-if="aiState.error" class="care-note" role="alert">{{ aiState.error }} <button v-if="!aiState.busy" class="garden-link" @click="preview">重新预览</button></p><p v-if="aiState.notice" class="mint-note" role="status">{{ aiState.notice }}</p>
          <article ref="reply" v-if="aiState.text" class="security-ai-reply"><h3>AI 分析<small v-if="aiState.busy"> · 正在生成…</small></h3><div class="security-ai-md" v-html="diagnosticMarkdown(aiState.text)"></div></article>
        </section>
      </section>
      <section v-else class="security-panel-report security-panel-empty"><div class="garden-empty"><b>选择左侧的模组</b><span>勾选多个后可以一起检查；报告只在你点击检查后生成。</span></div></section>
    </div>

    <Sheet v-if="dialog==='defender'" title="Defender 无处置扫描" subtitle="显式调用本机引擎，管理器不上传样本。" @close="close"><p>{{ nameOf(mod) }}</p><div class="sign-note">扫描使用“不自动处置”模式。Defender 云保护遵循 Windows 设置，管理器不修改保护策略。</div><template #footer><span class="sp"></span><button class="garden-btn sm" @click="close">取消</button><button data-testid="security-defender-confirm" class="garden-btn green sm" :disabled="scanning" @click="security.scan([selected],true);close()">开始扫描</button></template></Sheet>
    <Sheet v-if="dialog==='dispose'" title="确认可恢复处置" :busy="state.mutating" @close="close"><div v-if="!state.preview&&!state.actionError" class="garden-empty">正在核对内容和依赖影响…</div><template v-if="state.preview"><div class="sign-note">{{ securityEffect(state.preview.effect) }}</div><div class="sec"><h3>会停用的模组 <small>{{ state.preview.affected_ids?.length||0 }}</small></h3><div v-for="id in state.preview.affected_ids" :key="id" class="li"><div class="ln"><b>{{ nameOf(byId(id)) }}</b><span>{{ id===state.preview.id?'选中模组':'依赖受影响的模组' }}</span></div></div></div><p class="muted">写入前创建恢复点；恢复不会自动启用或批准。</p><p v-if="dirty" class="care-note">先保存或放弃模组页的草稿，再重新预览处置。</p></template><p v-if="state.actionError" class="care-note" role="alert">{{ state.actionError }}</p><template #footer><span class="sp"></span><button class="garden-btn sm" :disabled="state.mutating" @click="close">取消</button><button data-testid="security-dispose-confirm" class="garden-btn clay sm" :disabled="!state.preview||state.mutating||!writeAllowed||scanning||dirty" @click="dispose">创建恢复点并处置</button></template></Sheet>
    <Sheet v-if="dialog==='records'" title="处置与恢复记录" subtitle="解除逻辑隔离或恢复移出的本地包，不自动启用或批准。" :busy="state.mutating" @close="close"><div v-if="!state.status?.records?.length" class="garden-empty">还没有处置记录。</div><div class="lst"><label v-for="entry in state.status?.records||[]" :key="entry.id" class="li"><input v-model="record" type="radio" :value="entry" :disabled="!entry.restorable"/><div class="ln"><b>{{ entry.name||entry.mod_id }} · {{ entry.mode==='disable'?'停用':entry.source==='workshop'?'工坊逻辑隔离':'本地包隔离' }}</b><span>{{ dateText(entry.created_at) }} · {{ ({prepared:'处置未确认',applied:'已处置',failed:'失败，请核对',restored:'已恢复'})[entry.status]||entry.status }}</span><span>{{ securityEffect(entry.effect) }}</span><span v-if="entry.error">{{ securityReason(entry.error) }}</span></div></label></div><p v-if="dirty" class="care-note">先保存或放弃模组页的草稿，再恢复处置。</p><p v-if="state.actionError" class="care-note" role="alert">{{ state.actionError }}</p><template #footer><span class="sp"></span><button class="garden-btn sm" :disabled="state.mutating" @click="close">关闭</button><button data-testid="security-restore-confirm" class="garden-btn green sm" :disabled="!record?.restorable||!canRestore" @click="restore">恢复选中处置</button></template></Sheet>
  </section></div>
</template>
