<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { CircleCheck, TriangleAlert, RefreshCw } from 'lucide-vue-next'
import { createMelonLoaderRuntime, melonLoaderCheckText, melonLoaderProgress, melonLoaderStatusText } from './melonLoaderRuntime.js'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null } })
const state = reactive({}), dialog = ref(null), detailButton = ref(null)
const runtime = createMelonLoaderRuntime(props.bridge, state)
const working = computed(() => state.busy || state.refreshing || state.choosing)
const summary = computed(() => melonLoaderStatusText(state))
const overviewMessage = computed(() => {
  const message = state.snapshot?.message || ''
  return message.length > 100 ? '查看环境详情，定位缺少的文件或运行库。' : message
})
const onlineLabel = computed(() => `${state.snapshot?.status === 'installed' ? '重新下载安装' : '下载安装'} MelonLoader ${state.snapshot?.recommended_version || '0.7.3'}`)
const progressText = computed(() => state.task?.state === 'cancelling' ? '正在取消…' : state.task?.stage === 'downloading' || state.task?.stage === 'download' ? '正在下载 MelonLoader…' : state.refreshing ? '正在检测环境…' : '正在安装 MelonLoader…')
const progress = computed(() => melonLoaderProgress(state.task))
function openDetails() { dialog.value?.showModal() }
function closeDetails() { dialog.value?.close(); nextTick(() => detailButton.value?.focus()) }
watch(() => props.installation?.installation_id, id => { dialog.value?.close(); void runtime.setInstallation(id) }, { immediate: true })
onUnmounted(() => runtime.dispose())
</script>

<template>
  <section v-if="state.installationId" class="loader-panel" data-testid="loader-panel" :aria-busy="working">
    <button ref="detailButton" class="env-pill" :class="{ warn: !state.snapshot?.ready && !state.refreshing }" data-testid="loader-details" :title="state.snapshot?.ready ? '环境详情' : '环境与安装'" @click="openDetails">
      <CircleCheck v-if="state.snapshot?.ready" :size="15" class="ready" aria-hidden="true"/><TriangleAlert v-else-if="!state.refreshing" :size="15" class="warning" aria-hidden="true"/><RefreshCw v-else :size="15" class="garden-spin" aria-hidden="true"/>
      <strong role="status" data-testid="loader-status" :data-state="state.snapshot?.status || 'unknown'">{{ summary }}</strong>
    </button>
    <Teleport defer to="#garden-sign-slot">
      <div v-if="(state.busy && !state.choosing) || state.error || (state.snapshot && !state.snapshot.ready)" class="garden-sign-banner" data-testid="loader-banner">
        <TriangleAlert :size="20" aria-hidden="true"/>
        <div class="grow">
          <b>{{ summary }}</b> <span v-if="overviewMessage">{{ overviewMessage }}</span>
          <div v-if="state.busy && !state.choosing" class="loader-progress" role="status" data-testid="loader-progress" :data-state="state.task?.state || 'starting'"><span>{{ progressText }}</span><span v-if="progress.label">{{ progress.label }}</span><progress :value="progress.value" :max="progress.max"></progress><button class="garden-btn soft sm" :disabled="!state.task || state.task.state === 'cancelling'" @click="runtime.cancel">取消</button></div>
          <p v-if="state.error" class="loader-error" role="alert" data-testid="loader-error">{{ state.error }}</p>
        </div>
        <button v-if="state.snapshot && state.snapshot.status !== 'installed'" class="garden-btn amber sm" data-testid="loader-install" :disabled="!runtime.canInstall()" @click="runtime.install">{{ onlineLabel }}</button>
        <button class="garden-btn soft sm" @click="openDetails">查看环境</button>
      </div>
      <p v-else-if="state.notice" class="garden-notice loader-notice" role="status">{{ state.notice }}</p>
    </Teleport>
    <dialog ref="dialog" class="loader-dialog" aria-labelledby="loader-title" @cancel.prevent="closeDetails">
      <header><h2 id="loader-title">MelonLoader 与运行环境</h2><button aria-label="关闭环境详情" @click="closeDetails">×</button></header>
      <div class="dialog-body">
        <p class="dialog-summary">{{ summary }}</p>
        <p v-if="state.snapshot?.message" class="hint">{{ state.snapshot.message }}</p>
        <ul class="loader-checks" data-testid="loader-checks">
          <li v-for="check in state.snapshot?.checks || []" :key="check.id" :data-check="check.id" :data-status="check.status"><div><strong>{{ check.label }}</strong><span :class="['check-status', check.status]">{{ melonLoaderCheckText(check.status) }}</span></div><p>{{ check.detail }}</p><a v-if="check.help_url" :href="check.help_url" target="_blank" rel="noopener noreferrer">官方安装说明与下载 ↗</a></li>
        </ul>
        <div class="loader-install-options"><button class="primary" data-testid="loader-install-online" :disabled="!runtime.canInstall()" @click="runtime.install">{{ onlineLabel }}</button><button data-testid="loader-install-local" :disabled="!runtime.canInstall()" @click="runtime.installLocal">从本地 ZIP 安装</button><button data-testid="loader-refresh" :disabled="working" @click="runtime.refresh"><RefreshCw :size="15" aria-hidden="true"/>重新检测</button></div>
        <p class="hint">运行库请通过上方官方链接安装，完成后重新检测。</p>
        <p v-if="state.busy" role="status">{{ state.choosing ? '请选择 MelonLoader ZIP…' : progressText }}<template v-if="!state.choosing && progress.label"> · {{ progress.label }}</template></p>
        <progress v-if="state.busy && !state.choosing" :value="progress.value" :max="progress.max" aria-label="MelonLoader 操作进度"></progress>
        <p v-if="state.error" class="loader-error" role="alert">{{ state.error }}</p><p v-else-if="state.notice" class="loader-notice" role="status">{{ state.notice }}</p>
      </div>
      <footer><button v-if="state.busy && !state.choosing" :disabled="!state.task || state.task.state === 'cancelling'" @click="runtime.cancel">取消安装</button><button @click="closeDetails">关闭</button></footer>
    </dialog>
  </section>
</template>

<style scoped>
.loader-panel { flex:none; margin:0 12px; padding:10px 14px; border:1px solid rgb(var(--rgb-border-base)/.2); border-radius:10px; color:rgb(var(--rgb-text-main)); font-size:14px; }
.loader-overview { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }.loader-summary { flex:1; min-width:200px; display:flex; flex-direction:column; gap:4px; }.loader-summary strong { font-size:15px; font-weight:600; }.loader-message,.hint { color:rgb(var(--rgb-text-dim)); line-height:1.5; }.loader-message { overflow-wrap:anywhere; }.ready,.loader-notice,.ok { color:rgb(var(--rgb-accent-success)); }.warning,.missing,.disabled,.loader-error { color:rgb(var(--rgb-accent-danger)); }.unknown { color:rgb(var(--rgb-text-dim)); }
button { display:inline-flex; align-items:center; justify-content:center; gap:6px; font:inherit; color:inherit; cursor:pointer; white-space:nowrap; padding:7px 11px; border:1px solid rgb(var(--rgb-border-base)/.24); background:rgb(var(--rgb-bg-overlay)/.05); border-radius:8px; }button:hover { background:rgb(var(--rgb-bg-overlay)/.1); }button:disabled { opacity:.45; cursor:not-allowed; }button:focus-visible,a:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:3px; }.primary { background:rgb(var(--rgb-accent-primary)/.12); border-color:rgb(var(--rgb-accent-primary)/.45); }
.loader-progress { display:flex; align-items:center; flex-wrap:wrap; gap:12px; margin-top:12px; }.loader-progress progress { width:160px; }.loader-progress button { margin-left:auto; }.loader-error,.loader-notice { margin:9px 0 0; overflow-wrap:anywhere; }
.loader-dialog { width:min(660px,92vw); max-height:calc(100vh - 2rem); padding:0; border:1px solid rgb(var(--rgb-border-base)/.2); border-radius:14px; color:rgb(var(--rgb-text-main)); background:rgb(var(--rgb-bg-surface)); font-size:15px; line-height:1.55; box-shadow:0 24px 70px #0005; }.loader-dialog::backdrop { background:#0008; backdrop-filter:blur(8px); }.loader-dialog header,.loader-dialog footer { display:flex; gap:12px; align-items:center; padding:16px 20px; }.loader-dialog header { justify-content:space-between; border-bottom:1px solid rgb(var(--rgb-border-base)/.18); }.loader-dialog footer { justify-content:flex-end; border-top:1px solid rgb(var(--rgb-border-base)/.18); }.loader-dialog h2 { margin:0; font-size:20px; }.dialog-body { padding:0 20px 18px; overflow:auto; max-height:calc(100vh - 210px); }.dialog-summary { margin-bottom:6px; font-weight:600; }.dialog-body>.hint { margin-top:6px; }.loader-checks { list-style:none; padding:0; margin:16px 0; }.loader-checks li { padding:13px 0; border-bottom:1px solid rgb(var(--rgb-border-base)/.16); }.loader-checks li>div { display:flex; align-items:center; justify-content:space-between; gap:12px; }.loader-checks strong { font-weight:600; }.loader-checks p { margin:5px 0; color:rgb(var(--rgb-text-dim)); overflow-wrap:anywhere; }.loader-checks a { color:rgb(var(--rgb-accent-primary)); font-size:14px; }.loader-install-options { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }.check-status { font-size:14px; white-space:nowrap; }
@media(max-width:640px) { .loader-overview>.primary { flex-basis:100%; }.loader-summary { min-width:160px; }.loader-panel { padding:12px; } }
</style>
