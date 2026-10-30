<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import Sheet from './GardenSheet.vue'
import Icon from './GardenIcon.vue'
import Report from './NativeSecurity.vue'
import { nameOf, sourceText, sizeText, dateText } from './nativeModel.js'
import { securityLabel, securityEffect, securityReason, securityRestoreAllowed, createSecurityRuntime } from './securityRuntime.js'
import { selectIds, toggleAll } from './listSelection.js'
import { createSecurityAiRuntime, securityAiWarnings } from './securityAiRuntime.js'
import { diagnosticMarkdown } from '../ai/diagnosticMarkdown.js'
const props=defineProps({bridge:Object,installation:Object,active:Boolean,mods:{type:Array,default:()=>[]},nativeRuntime:Object,dirty:Boolean,busy:Boolean})
const emit=defineEmits(['settings','changed','mutating'])
const selected=ref(null),view=ref('report'),selection=ref({list:'checks',ids:[],anchor:null}),query=ref(''),dialog=ref(null),record=ref(null)
const state=reactive({}),security=createSecurityRuntime(props.bridge,state,{active:false})
const installationId=computed(()=>props.installation?.installation_id)
const visibleMods=computed(()=>props.mods.filter(mod=>!query.value||`${nameOf(mod)} ${mod.id}`.toLowerCase().includes(query.value.toLowerCase())))
const selectedIds=computed(()=>selection.value.ids.filter(id=>props.mods.some(m=>m.id===id)))
const allSelected=computed(()=>visibleMods.value.length>0&&visibleMods.value.every(m=>selectedIds.value.includes(m.id)))
const writeAllowed=computed(()=>props.nativeRuntime?.state==='stopped'&&props.nativeRuntime.writable!==false&&!props.busy&&!state.mutating)
const canRestore=computed(()=>securityRestoreAllowed(props.nativeRuntime,{dirty:props.dirty,busy:props.busy||state.mutating,scanning:scanning.value}))
const byId=id=>props.mods.find(mod=>mod.id===id)
const aiState=reactive({}),ai=createSecurityAiRuntime(props.bridge,aiState)
const reportBody=ref(null),reply=ref(null)
const mod=computed(()=>props.mods.find(m=>m.id===selected.value)),item=computed(()=>state.items[selected.value]),detail=computed(()=>state.details[selected.value])
const scanning=computed(()=>state.starting||state.status?.scanning),operation=computed(()=>state.status?.operation)
const progress=computed(()=>operation.value?.total?Math.min(100,(operation.value.done||0)/operation.value.total*100):0)
const phaseNames={queued:'等待开始',reading:'读取文件',hashing:'读取文件',analyzing:'静态分析',static:'静态分析',native_scan:'原生检查',antivirus:'Defender 扫描',writing:'保存报告',completed:'检查完成',failed:'检查未完成'}
watch(selected,id=>{view.value='report';if(state.selectedId!==id)void security.select(id)})
watch(()=>[installationId.value,selected.value,item.value?.report_id],([installation_id,id,report_id])=>{view.value='report';ai.setScope(installation_id&&id&&report_id?{installation_id,id,report_id}:null)},{immediate:true})
watch(()=>props.mods,mods=>{security.inventory(mods);const ids=mods.map(m=>m.id);selection.value.ids=selection.value.ids.filter(id=>ids.includes(id));if(!ids.includes(selected.value))selected.value=ids[0]||null})
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
watch(()=>state.mutating,value=>emit('mutating',value),{immediate:true})
watch([selected,view],()=>nextTick(()=>{if(reportBody.value)reportBody.value.scrollTop=0}))
watch(()=>Boolean(aiState.text),visible=>{if(visible)nextTick(()=>reply.value?.scrollIntoView({block:'nearest'}))})
function choose(id,event={}){view.value='report';selected.value=id;selection.value=selectIds(selection.value,'checks',id,visibleMods.value.map(m=>m.id),event)}
function check(id){view.value='report';const ids=new Set(selectedIds.value);ids.has(id)?ids.delete(id):ids.add(id);selection.value={list:'checks',ids:[...ids],anchor:id};selected.value=id}
function selectAll(){selection.value=toggleAll(selection.value,'checks',visibleMods.value.map(m=>m.id))}
function keyboard(event){if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='a'){event.preventDefault();selection.value=toggleAll(selection.value,'checks',visibleMods.value.map(m=>m.id),true)}}
async function scan(all=false){if(all||selectedIds.value.length)await security.scan(all?undefined:selectedIds.value)}
async function preview(){view.value='ai';await ai.preview()}
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
    <div class="garden-intro"><div><h1>模组检查</h1><p>手动检查文件、查看变化与风险证据；与日常模组管理分开。</p></div><div class="garden-intro-actions"><button class="garden-btn sm" :disabled="state.loading" @click="security.refresh()"><Icon name="refresh" size="sm"/>刷新报告</button><button data-testid="security-records" class="garden-btn sm" @click="action({kind:'security-records'})"><Icon name="history" size="sm"/>处置记录</button></div></div>
    <div class="security-panel-tools">
      <span class="muted">已选 {{ selectedIds.length }} / {{ mods.length }} 个模组</span><span class="sp"></span>
      <button data-testid="security-start-selected" class="garden-btn green sm" :disabled="scanning||!selectedIds.length" @click="scan(false)"><Icon name="search" size="sm"/>检查选中 {{ selectedIds.length||'' }}</button>
      <button data-testid="security-start-all" class="garden-btn sm" :disabled="scanning||!mods.length" @click="scan(true)">检查全部 {{ mods.length||'' }}</button>
    </div>
    <div v-if="scanning||operation" class="security-panel-progress" data-testid="security-progress" role="status">
      <div><b>{{ scanning?'后台检查中':operation?.state==='completed'?'检查完成':'检查未完成' }}</b><span>{{ operation?.done??0 }} / {{ operation?.total??0 }}</span></div>
      <progress :value="progress" max="100" aria-label="模组检查进度"></progress>
      <p v-if="scanning">{{ operation?.current_name||operation?.current_id||'等待当前模组信息' }}<span v-if="operation?.phase"> · {{ phaseNames[operation.phase]||operation.phase }}</span><code v-if="operation?.current_file">{{ operation.current_file }}</code></p>
      <small v-if="scanning">可以切换页面继续管理；已开始的检查会在后台完成。</small>
    </div>
    <p v-if="state.error" class="care-note" role="alert">{{ state.error }}</p>
    <div class="security-panel-layout">
      <div class="security-report-sidebar"><div class="security-list-tools"><label class="garden-search"><Icon name="search" size="sm"/><input v-model="query" placeholder="搜索模组名称或 ID" aria-label="搜索检查列表"/></label><button data-testid="security-select-all" class="garden-link" :disabled="!visibleMods.length" @click="selectAll">{{ allSelected?'取消全选':'全选' }}</button></div>
      <nav class="security-report-list" aria-label="模组检查列表" tabindex="0" @keydown="keyboard">
        <div v-for="entry in visibleMods" :key="entry.id" class="security-report-entry" :class="{on:selected===entry.id}"><input type="checkbox" :data-testid="'security-pick-'+entry.id" :checked="selectedIds.includes(entry.id)" :aria-label="'选择 '+nameOf(entry)" @change="check(entry.id)"/><button :data-security-id="entry.id" :aria-current="selected===entry.id?'true':undefined" @click="choose(entry.id,$event)"><b>{{ nameOf(entry) }}</b><span>{{ sourceText(entry) }}</span><span class="tg" :class="securityLabel(state.items[entry.id]).tone">{{ securityLabel(state.items[entry.id]).label }}</span></button></div>
        <p v-if="!mods.length" class="muted">当前没有可检查的模组。</p><p v-else-if="!visibleMods.length" class="muted">没有匹配的模组。</p>
      </nav></div>
      <section ref="reportBody" class="security-panel-report" v-if="mod">
        <header class="security-report-head"><div><h3>{{ nameOf(mod) }}</h3><small>{{ mod.id }}</small></div><button v-if="view==='ai'" class="garden-btn sm" @click="view='report'">返回报告</button><button v-else data-testid="security-ai-preview" class="garden-btn sm" :disabled="!item?.report_id||state.detailLoading" @click="preview"><Icon name="sprout" size="sm"/>AI 解读报告…</button></header>
        <Report v-if="view==='report'" :mod="mod" :item="item" :detail="detail" :loading="state.detailLoading" :scanning="scanning" :write-allowed="writeAllowed" :dirty="dirty" @action="action"/>
        <section v-else class="security-ai" data-testid="security-ai-panel">
          <h3>发送前预览</h3><p class="muted">仅发送下列报告摘要与提示词；原始 DLL 留在本机。</p>
          <p v-if="aiState.loading" role="status">正在本地生成发送预览…</p>
          <template v-if="aiState.preview">
            <dl class="garden-facts"><dt>分析服务</dt><dd>{{ aiState.preview.provider.name||'尚未配置' }} · {{ aiState.preview.provider.model||'未选模型' }}</dd><dt>发送地址</dt><dd>{{ aiState.preview.provider.base_url||'未配置' }}</dd><dt>发送内容</dt><dd>{{ sizeText(aiState.preview.bytes) }}<template v-if="aiState.preview.truncated"> · 报告摘要已截断</template></dd></dl>
            <p v-for="(warning,i) in securityAiWarnings(aiState.preview)" :key="i" class="care-note">{{ warning }}</p>
            <details :open="!aiState.sent" class="security-tech security-send-preview"><summary>实际将发送的完整消息</summary><article v-for="(message,i) in aiState.preview.messages" :key="i"><b>{{ ({system:'系统要求',user:'报告与问题',assistant:'已有回复'})[message.role]||message.role }}</b><pre>{{ message.content }}</pre></article></details>
            <div class="note-act"><button data-testid="security-ai-send" class="garden-btn green sm" :disabled="aiState.busy||aiState.sent||!aiState.preview.provider.configured" @click="ai.send">{{ aiState.busy?'正在分析…':aiState.sent?'本次已发送':'确认发送并分析' }}</button><button v-if="!aiState.busy" class="garden-link" @click="preview">重新生成预览</button><button v-if="!aiState.preview.provider.configured" class="garden-link" @click="emit('settings')">配置 AI 分析服务</button><button v-if="aiState.busy" data-testid="security-ai-cancel" class="garden-link" :disabled="aiState.cancelling" @click="ai.cancel">取消分析</button></div>
          </template>
          <p v-if="aiState.error" class="care-note" role="alert">{{ aiState.error }} <button v-if="!aiState.busy" class="garden-link" @click="preview">重新预览</button></p><p v-if="aiState.notice" class="mint-note" role="status">{{ aiState.notice }}</p>
          <article ref="reply" v-if="aiState.text" class="security-ai-reply"><h3>AI 分析</h3><div v-html="diagnosticMarkdown(aiState.text)"></div></article>
        </section>
      </section>
    </div>
    <Sheet v-if="dialog==='defender'" title="Defender 无处置扫描" subtitle="显式调用本机引擎，管理器不上传样本。" @close="close"><p>{{ nameOf(mod) }}</p><div class="sign-note">扫描使用“不自动处置”模式。Defender 云保护遵循 Windows 设置，管理器不修改保护策略。</div><template #footer><span class="sp"></span><button class="garden-btn sm" @click="close">取消</button><button data-testid="security-defender-confirm" class="garden-btn green sm" :disabled="scanning" @click="security.scan([selected],true);close()">开始扫描</button></template></Sheet>
    <Sheet v-if="dialog==='dispose'" title="确认可恢复处置" :busy="state.mutating" @close="close"><div v-if="!state.preview&&!state.actionError" class="garden-empty">正在核对内容和依赖影响…</div><template v-if="state.preview"><div class="sign-note">{{ securityEffect(state.preview.effect) }}</div><div class="sec"><h3>会停用的模组 <small>{{ state.preview.affected_ids?.length||0 }}</small></h3><div v-for="id in state.preview.affected_ids" :key="id" class="li"><div class="ln"><b>{{ nameOf(byId(id)) }}</b><span>{{ id===state.preview.id?'选中模组':'依赖受影响的模组' }}</span></div></div></div><p class="muted">写入前创建恢复点；恢复不会自动启用或批准。</p><p v-if="dirty" class="care-note">先保存或放弃模组页的草稿，再重新预览处置。</p></template><p v-if="state.actionError" class="care-note" role="alert">{{ state.actionError }}</p><template #footer><span class="sp"></span><button class="garden-btn sm" :disabled="state.mutating" @click="close">取消</button><button data-testid="security-dispose-confirm" class="garden-btn clay sm" :disabled="!state.preview||state.mutating||!writeAllowed||scanning||dirty" @click="dispose">创建恢复点并处置</button></template></Sheet>
    <Sheet v-if="dialog==='records'" title="处置与恢复记录" subtitle="解除逻辑隔离或恢复移出的本地包，不自动启用或批准。" :busy="state.mutating" @close="close"><div v-if="!state.status?.records?.length" class="garden-empty">还没有处置记录。</div><div class="lst"><label v-for="entry in state.status?.records||[]" :key="entry.id" class="li"><input v-model="record" type="radio" :value="entry" :disabled="!entry.restorable"/><div class="ln"><b>{{ entry.name||entry.mod_id }} · {{ entry.mode==='disable'?'停用':entry.source==='workshop'?'工坊逻辑隔离':'本地包隔离' }}</b><span>{{ dateText(entry.created_at) }} · {{ ({prepared:'处置未确认',applied:'已处置',failed:'失败，请核对',restored:'已恢复'})[entry.status]||entry.status }}</span><span>{{ securityEffect(entry.effect) }}</span><span v-if="entry.error">{{ securityReason(entry.error) }}</span></div></label></div><p v-if="dirty" class="care-note">先保存或放弃模组页的草稿，再恢复处置。</p><p v-if="state.actionError" class="care-note" role="alert">{{ state.actionError }}</p><template #footer><span class="sp"></span><button class="garden-btn sm" :disabled="state.mutating" @click="close">关闭</button><button data-testid="security-restore-confirm" class="garden-btn green sm" :disabled="!record?.restorable||!canRestore" @click="restore">恢复选中处置</button></template></Sheet>
  </section></div>
</template>
