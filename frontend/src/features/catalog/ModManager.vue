<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { Package, RefreshCw } from 'lucide-vue-next'
import PackageDetails from '../profiles/PackageDetails.vue'
import { createModManagerRuntime, modStateText, modActions, modActionText } from './modManagerRuntime.js'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null } })
const state = reactive({}), list = ref(null)
const runtime = createModManagerRuntime(props.bridge, state)
let detailTrigger = null
function showDetail(item, event) {
  detailTrigger = event.currentTarget
  return runtime.showDetail(item)
}
async function closeDetail() {
  runtime.closeDetail()
  await nextTick()
  if (detailTrigger?.isConnected && !detailTrigger.disabled) detailTrigger.focus()
  detailTrigger = null
}
const rows = computed(() => {
  const query = state.query.trim().toLocaleLowerCase()
  return state.items.filter(item => !query || `${item.display_name} ${item.version_raw || ''}`.toLocaleLowerCase().includes(query))
})
const progressText = computed(() => state.task?.state === 'cancelling' ? '正在取消，请等待文件状态核对' : `正在${modActionText(state.action)}…`)
async function reveal(releaseId) {
  const found = await runtime.reveal(releaseId)
  if (found) {
    await nextTick()
    const row = [...(list.value?.querySelectorAll('[data-testid=mod-row]') || [])].find(item => item.dataset.releaseId === releaseId)
    row?.scrollIntoView({ block: 'nearest' }); row?.focus()
  }
  return found
}
watch(() => props.installation?.installation_id, id => runtime.setInstallation(id), { immediate: true })
onUnmounted(() => runtime.dispose())
defineExpose({ refresh: runtime.refresh, reveal })
</script>

<template>
  <main class="mod-manager" data-testid="mod-manager" :aria-busy="state.busy || state.loading">
    <header class="mod-toolbar"><div><h2><Package :size="21" aria-hidden="true"/>模组</h2><p>导入 Mod 后，点击安装即可使用。停用保留包库副本，卸载只移除该 Mod 的安装文件。</p></div><button data-testid="mod-refresh" :disabled="!state.installationId || state.busy || state.loading" @click="runtime.refresh()"><RefreshCw :size="16" aria-hidden="true"/>刷新</button></header>
    <p v-if="!state.installationId" class="empty" data-testid="mod-select-installation">先选择游戏安装，再安装或管理 Mod。</p>
    <template v-else>
      <div class="mod-filter"><label>搜索本页 <input v-model="state.query" data-testid="mod-search" placeholder="名称或版本" :disabled="state.busy" /></label><span>{{ state.total }} 个包库版本</span></div>
      <div v-if="state.busy" class="operation-progress" role="status" data-testid="mod-progress" :data-state="state.task?.state || 'starting'" :data-task-id="state.task?.task_id"><span>{{ progressText }}</span><span v-if="state.task?.total != null">{{ state.task.completed }} / {{ state.task.total }}</span><progress :value="state.task?.total ? state.task.completed : undefined" :max="state.task?.total || 1"></progress><button data-testid="mod-cancel" :disabled="!state.task || state.task.state === 'cancelling'" @click="runtime.cancel">取消</button></div>
      <p v-if="state.loading && !state.busy" class="hint" role="status" data-testid="mod-loading">正在核对 Mod 文件状态…</p>
      <p v-if="state.notice" class="notice" role="status" data-testid="mod-notice">{{ state.notice }}</p>
      <div v-if="state.error" class="error-message" role="alert" data-testid="mod-error"><p>{{ state.error }}</p><small v-if="state.errorCode">错误编号：{{ state.errorCode }}</small></div>
      <div ref="list" class="mod-list">
        <article v-for="item in rows" :key="item.release_id" data-testid="mod-row" :data-release-id="item.release_id" :data-mod-state="item.state" :data-state="item.state" :class="{ highlighted: item.release_id === state.focusReleaseId }" tabindex="-1">
          <div class="mod-name"><strong>{{ item.display_name || '未命名 Mod' }}</strong><small>版本 {{ item.version_raw ?? '未知' }} · {{ item.file_count }} 个文件</small><p v-if="item.message" class="mod-message">{{ item.message }}</p></div>
          <span class="mod-state" data-testid="mod-state" :data-state="item.state">{{ modStateText(item.state) }}</span>
          <div class="mod-actions"><button data-testid="mod-details" :disabled="state.busy || state.detailBusy" @click="showDetail(item,$event)">详情</button><button v-for="action in modActions(item.state)" :key="action" :data-testid="`mod-${action}`" :class="{ primary: action === 'install' || action === 'enable' }" :disabled="!runtime.canAct(item,action)" @click="runtime.perform(item,action)">{{ modActionText(action) }}</button></div>
        </article>
        <p v-if="!state.loading && !state.items.length" class="empty" data-testid="mod-empty">包库中还没有 Mod。点击上方“导入”添加 DLL、目录或 ZIP。</p>
        <p v-else-if="state.items.length && !rows.length" class="empty">本页没有匹配项。可清除搜索或切换分页。</p>
      </div>
      <nav v-if="state.total > 0" class="mod-pagination" aria-label="Mod 包库分页"><button data-testid="mod-prev" :disabled="state.busy || state.loading || state.offset === 0" @click="runtime.refresh(Math.max(0,state.offset-20))">上一页</button><span>{{ state.offset + 1 }}–{{ state.offset + state.items.length }} / {{ state.total }}</span><button data-testid="mod-next" :disabled="state.busy || state.loading || state.nextOffset == null" @click="runtime.refresh(state.nextOffset)">下一页</button></nav>
    </template>
    <PackageDetails v-if="state.detail" :detail="state.detail" :busy="state.detailBusy" @close="closeDetail" @page="runtime.showDetail(state.detail,$event)"/>
  </main>
</template>

<style scoped>
.mod-manager { flex:1; min-height:0; overflow:auto; padding:24px clamp(20px,4vw,48px); font-size:15px; color:rgb(var(--rgb-text-main)); }
.mod-toolbar { display:flex; justify-content:space-between; gap:20px; align-items:flex-start; margin-bottom:20px; }
h2 { display:flex; align-items:center; gap:9px; margin:0 0 8px; font-size:21px; font-weight:650; }
.mod-toolbar p,.hint,.empty,.mod-filter>span,small,.mod-message { color:rgb(var(--rgb-text-dim)); line-height:1.6; }
.mod-toolbar p { margin:0; max-width:720px; }.mod-toolbar>button { display:flex; align-items:center; gap:7px; }
button,input { font:inherit; color:inherit; border:1px solid rgb(var(--rgb-border-base)/.2); background:rgb(var(--rgb-bg-overlay)/.05); border-radius:8px; padding:7px 12px; }
button { cursor:pointer; white-space:nowrap; } button:hover { background:rgb(var(--rgb-bg-overlay)/.1); } button:disabled { opacity:.45; cursor:not-allowed; }
button:focus-visible,input:focus-visible,article:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }
.primary { border-color:rgb(var(--rgb-accent-primary)/.45); background:rgb(var(--rgb-accent-primary)/.12); }
.mod-filter { display:flex; align-items:center; flex-wrap:wrap; gap:16px; margin:16px 0; font-size:14px; }.mod-filter label { display:flex; align-items:center; gap:10px; }.mod-filter input { width:230px; }
.mod-list { border:1px solid rgb(var(--rgb-border-base)/.16); border-radius:12px; overflow:hidden; }.mod-list article { display:flex; align-items:center; gap:20px; padding:16px 20px; border-bottom:1px solid rgb(var(--rgb-border-base)/.14); }.mod-list article:last-child { border-bottom:0; }.mod-list article.highlighted { background:rgb(var(--rgb-accent-primary)/.08); }
.mod-name { min-width:0; flex:1; }.mod-name strong { display:block; font-size:17px; font-weight:600; overflow-wrap:anywhere; }.mod-name small { display:block; font-size:14px; margin-top:5px; }.mod-message { margin:5px 0 0; font-size:14px; overflow-wrap:anywhere; }
.mod-state { font-size:14px; white-space:nowrap; color:rgb(var(--rgb-text-dim)); }.mod-state[data-state=enabled] { color:rgb(var(--rgb-accent-success)); }.mod-state[data-state=drifted],.mod-state[data-state=unknown] { color:rgb(var(--rgb-accent-danger)); }.mod-actions { display:flex; flex-wrap:wrap; gap:8px; }
.empty { padding:24px 20px; margin:0; }.mod-pagination { display:flex; align-items:center; justify-content:flex-end; gap:16px; margin-top:16px; font-size:14px; }
.operation-progress { display:flex; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 16px; margin:14px 0; border:1px solid rgb(var(--rgb-border-base)/.18); border-radius:8px; }.operation-progress progress { width:160px; }.operation-progress button { margin-left:auto; }
.notice { color:rgb(var(--rgb-accent-success)); }.error-message { color:rgb(var(--rgb-accent-danger)); overflow-wrap:anywhere; }.error-message p { margin-bottom:4px; }.error-message small { color:inherit; }
@media(max-width:760px) { .mod-list article { flex-wrap:wrap; gap:12px; }.mod-name { flex-basis:70%; }.mod-actions { margin-left:auto; }.mod-filter input { width:180px; } }
</style>
