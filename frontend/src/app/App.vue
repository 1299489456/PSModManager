<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { createBridge } from './bridge/client.js'
import { terminalStates } from './bridge/reducer.js'
import InstallationSelector from '../features/installations/InstallationSelector.vue'
import ModWorkspace from '../features/native/NativeWorkspace.vue'
import GardenSymbols from '../features/native/GardenSymbols.vue'
import '../features/native/formal.css'
import { Sprout, FileText, Settings, ShieldCheck, Layers, Globe } from 'lucide-vue-next'
import WorkshopPanel from '../features/native/WorkshopPanel.vue'
import ProfilePanel from '../features/native/NativeProfiles.vue'
import { createWorkshopRuntime } from '../features/native/workshopRuntime.js'
import SecurityWorkspace from '../features/native/SecurityWorkspace.vue'
import GameLaunchButton from '../features/installations/GameLaunchButton.vue'
import LogDiagnosticsWorkspace from '../features/ai/LogDiagnosticsWorkspace.vue'
import WindowFrame from './WindowFrame.vue'
import AboutLicenses from './AboutLicenses.vue'
import AboutUpdates from './AboutUpdates.vue'
import appIcon from '../shared/branding/psmm-icon-256.png?inline'
import StorageSettings from './StorageSettings.vue'
import WorkshopSettings from './WorkshopSettings.vue'
import AiProviderSettings from '../features/ai/AiProviderSettings.vue'
import { reducedMotion } from '../features/catalog/gardenMotion.js'
const workspaceTab = ref('mods')
const aiOpened = ref(false)
const workshopOpened = ref(false)
const securityOpened=ref(false),securityMutating=ref(false)
function openSecurity(){securityOpened.value=true;workspaceTab.value='security'}
const workshopPanel = ref(null)
const nativeSnapshot = ref(null)
const importActive = ref(false)
const infoTab = ref('overview')
const aiRevision=ref(0)
function openAiSettings(){infoTab.value='ai';workspaceTab.value='settings'}
const workspaceBusy = ref(false)
const profileBusy = ref(false)
const modState = ref({ dirty: false, loaded: false, locked: false, changes: 0 })
const profilePanel = ref(null)
const modManager = ref(null)
const launcher = ref(null)
const processStatus=ref(null),launchBusy=ref(false)
const nav = ref(null)
const vine = ref(null)
const selectedInstallation = ref(null)
const appInfo = ref(null)
const storagePhase = ref('idle')
const storageGated = computed(() => Boolean(appInfo.value?.storage_first_run) || storagePhase.value !== 'idle')
const workspaceReady = computed(() => Boolean(appInfo.value) && !storageGated.value)
const error = ref('')
const task = ref(null)
const checking = ref(false)
const checkError = ref('')
const bridge = createBridge({ transport: request => window.pywebview.api.dispatch(request) })
const steamState = reactive({})
const steam = createWorkshopRuntime(bridge,steamState,{canWrite:()=>modState.value.loaded&&nativeSnapshot.value?.runtime?.state==='stopped'&&nativeSnapshot.value?.runtime?.writable!==false&&!workspaceBusy.value&&!profileBusy.value,blockReason:()=>!modState.value.loaded?'模组清单尚未读取完成。':workspaceBusy.value||profileBusy.value?'正在保存或整理本地工作区，请等待完成。':nativeSnapshot.value?.runtime?.message||'本地写入保护尚未解除，请刷新状态并查看日志原因。',changed:()=>modManager.value?.refreshInventory()})
watch(()=>selectedInstallation.value?.installation_id,id=>void steam.setInstallation(id))
let taskWatch
let watchedTaskId = null
let disposed = false
let bridgeTimeout
function go(tab) {
  // The info entry keeps its old toggle behaviour: pressing it again returns to the Mods.
  workspaceTab.value = tab === 'settings' && workspaceTab.value === 'settings' ? 'mods' : tab
}
function openDiagnostics() { aiOpened.value = true; workspaceTab.value = 'ai' }
async function openWorkshop(query) {
  workshopOpened.value = true; workspaceTab.value = 'workshop'
  await nextTick()
  if (typeof query === 'string') workshopPanel.value?.search(query)
}
async function revealMod(id, tab) {
  workspaceTab.value = 'mods'; await nextTick(); await modManager.value?.reveal(id, tab)
}
async function openImport() { workspaceTab.value = 'mods'; await nextTick(); modManager.value?.importOpen() }
async function applyProfile(ids) { workspaceTab.value = 'mods'; await nextTick(); modManager.value?.applyProfile(ids) }
async function showRecovery() { workspaceTab.value = 'mods'; await nextTick(); modManager.value?.recovery() }
const saveBeforeLaunch = async () => modManager.value ? await modManager.value.prepareLaunch() : false
async function launchChecked(result){modManager.value?.launchFinished(result);await modManager.value?.refreshInventory()}
function saved(){launcher.value?.clearError()}
function placeVine(grow) {
  const active = nav.value?.querySelector(`[data-tab="${workspaceTab.value}"]`), line = vine.value
  if (!line) return
  if (!active) { line.style.opacity = 0; return }
  line.style.opacity = 1; line.style.left = `${active.offsetLeft + 12}px`; line.style.width = `${Math.max(0, active.offsetWidth - 24)}px`
  if (grow && !reducedMotion()) { line.classList.remove('grow'); void line.offsetWidth; line.classList.add('grow') }
}
watch(workspaceTab, () => nextTick(() => placeVine(true)))
watch(appInfo, () => nextTick(() => placeVine(false)))
const resized = () => placeVine(false)
const taskLabel = computed(() => ({ queued: '等待检查', running: '正在检查', cancelling: '正在取消', succeeded: '本地工作空间检查完成', failed: '本地工作空间检查失败', cancelled: '检查已取消' })[task.value?.state] || '尚未检查')
const activeTask = computed(() => checking.value || (task.value && !terminalStates.has(task.value.state)))
const status = computed(() => error.value ? '连接失败' : appInfo.value ? '本地桌面已就绪' : '正在连接本地桌面…')
const details = computed(() => appInfo.value ? [
  ['产品', appInfo.value.name], ['版本', appInfo.value.version],
  ['数据目录', appInfo.value.data_root], ['配置文件', appInfo.value.config_path],
  ['数据库', appInfo.value.database_path], ['日志文件', appInfo.value.log_path],
  ['凭据服务', appInfo.value.keyring_service],
  ['更新来源', appInfo.value.update_source || '未配置（不检查更新）'],
] : [])
async function connect() {
  clearTimeout(bridgeTimeout)
  try {
    if (!window.pywebview?.api?.dispatch) throw new Error('本地桌面接口不可用，请通过 PSModManager 桌面入口启动。')
    const data = await bridge.request('app.info')
    if (disposed) return
    if (data?.name !== 'PSModManager' || !data.data_root) throw new Error('本地桌面返回的应用信息不完整。')
    appInfo.value = data
    error.value = ''
    if (data.latest_check_task_id) attachTask(data.latest_check_task_id)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}
function attachTask(taskId) {
  if (disposed || watchedTaskId === taskId) return
  void taskWatch?.dispose()
  watchedTaskId = taskId
  taskWatch = bridge.watchTask(taskId, snapshot => { task.value = snapshot }, () => { checkError.value = '检查连接中断，请重新检查。'; task.value = null; watchedTaskId = null })
}
async function checkWorkspace() {
  if (activeTask.value || disposed) return
  checking.value = true
  checkError.value = ''
  try {
    await taskWatch?.dispose()
    const started = await bridge.request('app.check')
    if (disposed) return
    task.value = started
    attachTask(started.task_id)
  } catch { checkError.value = '无法开始本地检查，请重试。'; task.value = null }
  finally { checking.value = false }
}
async function cancelCheck() {
  try { await taskWatch?.cancel() } catch { checkError.value = '取消请求未完成，请重试。' }
}
function cleanup() {
  if (disposed) return
  disposed = true
  window.removeEventListener('pywebviewready', connect)
  window.removeEventListener('beforeunload', cleanup)
  window.removeEventListener('resize', resized)
  clearTimeout(bridgeTimeout)
  steam.dispose()
  void bridge.dispose()
}
onMounted(() => {
  window.addEventListener('pywebviewready', connect)
  window.addEventListener('beforeunload', cleanup)
  window.addEventListener('resize', resized)
  if (window.pywebview?.api?.dispatch) connect()
  else bridgeTimeout = setTimeout(() => { error.value = '未连接到本地桌面。请关闭此窗口，并通过 PSModManager 桌面入口重新启动。' }, 15000)
})
onUnmounted(cleanup)
</script>

<template>
  <div class="garden-shell desktop-shell">
    <div class="garden-fronds" aria-hidden="true"></div>
    <WindowFrame :bridge="bridge" :version="appInfo?.version"/><GardenSymbols/>
    <p class="sr-only" role="status">{{ status }}</p>
    <header v-show="!storageGated" class="canopy">
      <nav ref="nav" class="canopy-nav" aria-label="工作区">
        <button data-testid="workspace-mods" data-tab="mods" :aria-current="workspaceTab === 'mods' ? 'page' : undefined" @click="go('mods')"><Sprout :size="17" aria-hidden="true"/>模组<span v-if="modState.changes" class="canopy-count" :aria-label="`${modState.changes} 项未保存`">{{ modState.changes }}</span></button>
        <button data-testid="profile-open" data-tab="profiles" :disabled="!appInfo" :aria-current="workspaceTab === 'profiles' ? 'page' : undefined" @click="go('profiles')"><Layers :size="17" aria-hidden="true"/>方案</button>
        <button data-testid="workshop-open" data-tab="workshop" :disabled="!appInfo" :aria-current="workspaceTab === 'workshop' ? 'page' : undefined" @click="openWorkshop()"><Globe :size="17" aria-hidden="true"/>创意工坊</button>
        <button data-testid="workspace-security" data-tab="security" :disabled="!appInfo" :aria-current="workspaceTab === 'security' ? 'page' : undefined" @click="openSecurity"><ShieldCheck :size="17" aria-hidden="true"/>模组检查</button>
        <button data-testid="workspace-ai" data-tab="ai" :aria-current="workspaceTab === 'ai' ? 'page' : undefined" @click="openDiagnostics"><FileText :size="17" aria-hidden="true"/>日志</button>
        <button class="info-button" data-testid="workspace-settings" data-tab="settings" :disabled="!appInfo" :aria-current="workspaceTab === 'settings' ? 'page' : undefined" aria-label="设置" @click="go('settings')"><Settings :size="17" aria-hidden="true"/>设置</button>
        <span ref="vine" class="canopy-vine" aria-hidden="true"><svg viewBox="0 0 100 14" preserveAspectRatio="none"><path d="M2 8 Q 14 2 26 8 T 50 8 T 74 8 T 98 8"/><ellipse cx="96" cy="5" rx="4" ry="2.4" transform="rotate(-30 96 5)"/></svg></span>
      </nav>
      <div class="canopy-right" :inert="workspaceBusy || profileBusy || securityMutating || steam.busy() || undefined">
        <span v-if="modState.running" class="env-pill warn">游戏运行中 · 只读</span>
        <InstallationSelector v-if="workspaceReady" :bridge="bridge" :identity="nativeSnapshot?.identity ? {...nativeSnapshot.identity, ...nativeSnapshot.paths} : null" @selected="selectedInstallation = $event"/>
      </div>
      <GameLaunchButton v-if="workspaceReady" ref="launcher" class="canopy-launch" :bridge="bridge" :installation="selectedInstallation" :disabled="modState.locked || !modState.loaded || steam.busy() || securityMutating" :before-launch="saveBeforeLaunch" :preparation-phase="modState.phase" @status="processStatus=$event" @busy="launchBusy=$event" :save-first="modState.dirty" :pending="modState.changes" @launch-checked="launchChecked"/>
    </header>
    <div class="garden-bed">
      <ModWorkspace v-if="workspaceReady" v-show="workspaceTab === 'mods'" ref="modManager" :active="workspaceTab === 'mods'" :process-status="processStatus" :launch-busy="launchBusy" @saved="saved" :bridge="bridge" :steam="steam" :steam-state="steamState" :installation="selectedInstallation" @logs="openDiagnostics" @ai-settings="openAiSettings" @workshop="openWorkshop" @busy="workspaceBusy = $event" @state="modState = $event" @snapshot="nativeSnapshot = $event" @launch="launcher?.launch()"/>
      <ProfilePanel v-if="workspaceReady" v-show="workspaceTab === 'profiles'" ref="profilePanel" :active="workspaceTab === 'profiles'" :bridge="bridge" :installation="selectedInstallation" :mods="nativeSnapshot?.mods || []" :current-order="nativeSnapshot?.enabled_ids || []" :current-rules="nativeSnapshot?.rules || []" :workspace-dirty="modState.dirty" :inert="workspaceBusy || undefined" @busy="profileBusy = $event" @apply="applyProfile" @reveal="revealMod" @recovery="showRecovery"/>
      <WorkshopPanel v-if="workspaceReady && workshopOpened" v-show="workspaceTab === 'workshop'" ref="workshopPanel" :active="workspaceTab === 'workshop'" :bridge="bridge" :steam="steam" :steam-state="steamState" :installation="selectedInstallation" :mods="nativeSnapshot?.mods || []" @ai-settings="openAiSettings" @logs="openDiagnostics" :ai-revision="aiRevision" @import="openImport" @reveal="revealMod" @refresh-mods="modManager?.refreshInventory()"/>
      <SecurityWorkspace v-if="workspaceReady && securityOpened" v-show="workspaceTab==='security'" :active="workspaceTab==='security'" :bridge="bridge" :installation="selectedInstallation" :mods="nativeSnapshot?.mods||[]" :native-runtime="nativeSnapshot?.runtime" :dirty="modState.dirty" :busy="workspaceBusy||profileBusy||steam.busy()||launchBusy" @mutating="securityMutating=$event" @changed="modManager?.refreshInventory()" @settings="openAiSettings"/>
      <LogDiagnosticsWorkspace v-if="workspaceReady && aiOpened" v-show="workspaceTab === 'ai'" :active="workspaceTab === 'ai'" :bridge="bridge" :installation="selectedInstallation" :settings-revision="aiRevision" @settings="openAiSettings" @mod-changed="modManager?.refresh()" @reveal-mod="revealMod"/>
      <section v-if="appInfo" v-show="storageGated || workspaceTab === 'settings'" class="garden-settings app-info" :class="{ 'storage-gated': storageGated }" aria-label="设置">
        <div v-if="!storageGated" class="garden-intro"><div><h1>设置</h1><p>PSMM v{{ appInfo.version }} · 数据位置、模组更新、AI 服务与应用信息。</p></div></div>
        <div v-if="!storageGated" class="garden-chips info-tabs" role="tablist" aria-label="应用信息">
          <button role="tab" data-testid="info-overview" :class="{ on: infoTab === 'overview' }" :aria-selected="infoTab === 'overview'" @click="infoTab = 'overview'">概览</button>
          <button role="tab" data-testid="info-ai" :class="{on:infoTab==='ai'}" :aria-selected="infoTab==='ai'" @click="infoTab='ai'">AI 服务</button><button id="info-updates-tab" role="tab" data-testid="info-updates" aria-controls="info-updates-panel" :class="{ on: infoTab === 'updates' }" :aria-selected="infoTab === 'updates'" @click="infoTab = 'updates'">更新记录</button>
          <button role="tab" data-testid="info-licenses" :class="{ on: infoTab === 'licenses' }" :aria-selected="infoTab === 'licenses'" @click="infoTab = 'licenses'">许可与致谢</button>
        </div>
        <AiProviderSettings v-if="!storageGated" v-show="infoTab==='ai'" :bridge="bridge" :active="workspaceTab==='settings'&&infoTab==='ai'" @changed="aiRevision++"/><AboutUpdates v-if="!storageGated && infoTab === 'updates'" :version="appInfo.version"/>
        <AboutLicenses v-if="!storageGated && infoTab === 'licenses'" :version="appInfo.version"/>
        <div v-show="storageGated || infoTab === 'overview'" class="garden-cards">
          <StorageSettings class="wide" :bridge="bridge" :first-run="appInfo.storage_first_run" :blocked="workspaceBusy || importActive || modState.dirty" @ready="connect" @state="storagePhase = $event.phase"/>
          <WorkshopSettings v-if="!storageGated" :bridge="bridge" :active="workspaceTab==='settings'&&infoTab==='overview'"/><article v-if="!storageGated" class="garden-card"><h2><ShieldCheck :size="18" aria-hidden="true"/>本地检查</h2><p>只读检查 PSMM 的数据记录，不改动游戏文件。</p>
            <div class="workspace-check"><button class="garden-btn" data-testid="workspace-check" :disabled="activeTask" @click="checkWorkspace">检查本地工作空间</button><button v-if="activeTask && task" class="garden-btn soft" data-testid="workspace-cancel" :disabled="task.state === 'cancelling'" @click="cancelCheck">取消检查</button></div>
            <div v-if="task" data-testid="workspace-progress" :data-task-id="task.task_id" :data-sequence="task.sequence" :data-state="task.state" role="status" class="check-progress"><span>{{ taskLabel }}</span><span v-if="task.total != null">{{ task.completed }} / {{ task.total }}</span><div v-if="!terminalStates.has(task.state)" class="garden-solar"><i :class="{ indeterminate: !task.total }" :style="{ width: task.total ? `${Math.round(task.completed / task.total * 100)}%` : '35%' }"></i></div></div>
            <p v-if="checkError" role="alert" class="check-error">{{ checkError }}</p></article>
          <article v-if="!storageGated" class="garden-card about-card"><img class="about-mark" :src="appIcon" alt="" aria-hidden="true"/><h2>关于</h2><p>PSMM（{{ appInfo.name }}）v{{ appInfo.version }} · Probably Stolen 的 Mod 管理器 · Windows x64 便携版 · MIT 许可证</p><dl class="garden-facts wide"><template v-for="[label, value] in details.filter(([label]) => ['产品', '版本', '凭据服务', '更新来源'].includes(label))" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></template></dl><div class="about-links"><button class="garden-link" data-testid="info-updates-link" @click="infoTab = 'updates'">查看更新记录</button><button class="garden-link" data-testid="info-licenses-link" @click="infoTab = 'licenses'">查看许可与致谢</button></div></article>
        </div>
      </section>
      <div v-if="!appInfo && !error" class="garden-connecting" role="status"><img class="connecting-mark" :src="appIcon" alt="" aria-hidden="true"/>正在连接本地桌面…</div>
    </div>
    <div v-if="error" class="error garden-error" role="alert">{{ error }}</div>
  </div>
</template>

<style scoped>
.storage-gated { max-width: none; }
.about-links { display: flex; flex-wrap: wrap; gap: 10px 22px; margin-top: 14px; }
.about-card { position: relative; }
.about-mark { float: right; width: 56px; height: 56px; margin: 0 0 8px 16px; filter: drop-shadow(0 8px 12px rgba(20,83,45,.2)); }
.connecting-mark { width: 72px; height: 72px; animation: garden-grow .9s ease-in-out infinite alternate; transform-origin: 50% 90%; }
.storage-gated > .garden-cards { display: flex; justify-content: center; padding-top: clamp(32px, 11vh, 100px); }
.storage-gated > .garden-cards > :deep(.storage-settings) { width: min(760px, 100%); }
</style>
