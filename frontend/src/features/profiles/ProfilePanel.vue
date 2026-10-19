<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { createProfileStore } from './profileStore.js'
import { createPointerProfileDrag, createRowGesture, selectRows, filterRows, insertionTarget } from './profileSelection.js'
import PackageDetails from './PackageDetails.vue'
import PlanPreview from './PlanPreview.vue'
import { CircleCheck, CirclePause, Ellipsis, FolderInput, Info, Plus, RefreshCw, Redo2, Search, Sprout, Undo2, X } from 'lucide-vue-next'
const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null }, workspaceDirty: Boolean, active: Boolean })
const emit = defineEmits(['applied'])
const state = reactive({}), root = ref(null), group = ref(''), picked = ref([])
const store = createProfileStore(props.bridge, state)
const drag = createPointerProfileDrag(), gesture = createRowGesture(), anchor = ref(null), dropTarget = ref(null), search = ref('')
const pointerOrigin = ref(null), draggingCount = ref(0)
const libraryDialog = ref(null), manifestDialog = ref(null), importName = ref(''), detailOrigin = ref(null)
const contextMenu = ref(null), menu = ref(null), contextOrigin = ref(null)
const planPreview = ref(false), planOrigin = ref(null)
const applyDialog = ref(null), nameDialog = ref(null), replaceDialog = ref(null), exportDialog = ref(null), deleteDialog = ref(null)
const naming = reactive({ mode: '', value: '', source: 'current' })
const moreOpen = ref(false), moreMenu = ref(null), moreButton = ref(null)
const pendingSource = ref(null)
const sides = [{ selected: true, label: '已启用', testid: 'profile-enabled-list' }, { selected: false, label: '已停用', testid: 'profile-disabled-list' }]
const namingText = {
  create: { title: '新方案', confirm: '创建' }, saveAs: { title: '另存为新方案', confirm: '另存为' },
  rename: { title: '重命名方案', confirm: '重命名' }, clone: { title: '复制方案', confirm: '复制' },
}
const releaseName = release => release.display_name || '未命名 Mod'
const selectionName = selection => selection.display_name || releaseName(state.packages.find(item => item.release_id === selection.release_id) || selection)
const sideRows = selected => filterRows(state.current?.selections || [], selected, search.value, selectionName)
const counts = computed(() => {
  const rows = state.current?.selections || []
  return { on: rows.filter(item => item.selected).length, off: rows.filter(item => !item.selected).length, missing: rows.filter(item => !item.resolved).length }
})
const applyBlocked = computed(() => !state.current ? '先选择一个方案'
  : state.current.dirty ? '先保存修改'
  : props.workspaceDirty ? '模组页有未保存的更改，先保存或撤销'
  : counts.value.missing ? `${counts.value.missing} 个 Mod 缺少文件，导入后再应用` : '')
const inProfile = release => state.current?.selections?.find(item => item.release_id === release.release_id)
const packetStatus = profile => profile.profile_id === state.current?.profile_id
  ? `${counts.value.on} 个启用 · 共 ${counts.value.on + counts.value.off} 个`
  : profile.dirty ? '有未保存的修改' : '已保存'
const interactive = event => !!event.target.closest('button,input,summary,details,a')
function applySelection(result) { picked.value = result.selected; anchor.value = result.anchor }
function pressRow(event, row) {
  if (state.busy || interactive(event) || event.button !== 0) return
  clearDrag()
  applySelection(gesture.press(sideRows(row.selected), picked.value, anchor.value, row.release_id, event))
  const visible = new Set(sideRows(row.selected).map(item => item.release_id))
  if (drag.press(state.current, row.release_id, picked.value.filter(id => visible.has(id)), event)) pointerOrigin.value = event.currentTarget
}
function selectRow(event, row) {
  if (state.busy || interactive(event)) return
  applySelection(gesture.click(sideRows(row.selected), picked.value, anchor.value, row.release_id, event))
}
function keyboardRow(event, row) {
  if (state.busy || interactive(event)) return
  applySelection(selectRows(sideRows(row.selected), picked.value, anchor.value, row.release_id, event))
}
function rowKey(event, row) {
  if (event.target !== event.currentTarget || state.busy) return
  if (event.key === ' ') { event.preventDefault(); keyboardRow(event, row) }
  else if (event.key === 'Enter') { event.preventDefault(); void store.setEnabled([row.release_id], !row.selected) }
  else if (event.key === 'F10' && event.shiftKey) { event.preventDefault(); void openContext(event, row) }
}
function pointerTarget(event) {
  const target = document.elementFromPoint(event.clientX, event.clientY)
  const lane = target?.closest('[data-testid=profile-enabled-list],[data-testid=profile-disabled-list]')
  if (!lane || lane.closest('[data-testid=profile-editor]') !== root.value) return null
  const selected = lane.dataset.testid === 'profile-enabled-list'
  const rows = [...lane.querySelectorAll('[data-testid=profile-selection]')]
  const rects = rows.map(row => { const box = row.getBoundingClientRect(); return { release_id: row.dataset.releaseId, top: box.top, height: box.height } })
  return insertionTarget(sideRows(selected), picked.value, selected, event.clientY, rects)
}
function movePointer(event) {
  if (drag.pointerId !== event.pointerId) return
  if (state.busy || !pointerOrigin.value?.isConnected) { clearDrag(); return }
  const result = drag.move(state.current, event)
  if (!result) { if (drag.pointerId == null) clearDrag(); return }
  if (result.started) {
    closeContext(false); gesture.drag(); picked.value = result.release_ids; draggingCount.value = result.release_ids.length
    try { pointerOrigin.value.setPointerCapture(event.pointerId) } catch { clearDrag(); return }
  }
  event.preventDefault()
  const target = pointerTarget(event)
  dropTarget.value = target && drag.accepts(state.current, target) ? target : null
}
function releasePointer(event) {
  if (drag.pointerId !== event.pointerId) return
  const wasActive = drag.active, target = pointerTarget(event)
  const action = !state.busy ? drag.release(state.current, event.pointerId, target) : null
  clearDrag(event.pointerId)
  if (wasActive) event.preventDefault()
  if (action) void store.move(action.release_ids, action.selected, action.before_release_id)
}
function clearDrag(capturedId = drag.pointerId) {
  drag.cancel(); dropTarget.value = null; draggingCount.value = 0
  const origin = pointerOrigin.value; pointerOrigin.value = null
  if (capturedId != null && origin?.hasPointerCapture?.(capturedId)) origin.releasePointerCapture(capturedId)
}
function cancelPointer(event) { if (event.pointerId === drag.pointerId) clearDrag() }
function cancelWithEscape(event) {
  if (event.key !== 'Escape') return
  if (drag.pointerId != null) { event.preventDefault(); clearDrag() }
  else if (moreOpen.value) { event.preventDefault(); closeMore(true) }
}
async function openContext(event, row) {
  event.preventDefault()
  if (state.busy) return
  if (!picked.value.includes(row.release_id)) { picked.value = [row.release_id]; anchor.value = row.release_id }
  const visible = new Set(sideRows(row.selected).map(item => item.release_id))
  picked.value = picked.value.filter(id => visible.has(id))
  const rect = event.currentTarget.getBoundingClientRect()
  contextOrigin.value = event.currentTarget
  contextMenu.value = { row, x: Math.min(event.clientX || rect.left + 20, window.innerWidth - 210), y: Math.min(event.clientY || rect.top + 20, window.innerHeight - 160) }
  await nextTick(); menu.value?.querySelector('button')?.focus()
}
function closeContext(restore = true) { contextMenu.value = null; if (restore) contextOrigin.value?.focus() }
function outsidePress(event) {
  if (contextMenu.value && !menu.value?.contains(event.target)) closeContext(false)
  if (moreOpen.value && !moreMenu.value?.contains(event.target) && !moreButton.value?.contains(event.target)) closeMore()
}
function menuKeys(event, container, close) {
  if (event.key === 'Escape') { event.preventDefault(); close(); return }
  if (event.key === 'Tab') { close(false); return }
  const buttons = [...container.querySelectorAll('button:not(:disabled)')]
  const index = buttons.indexOf(document.activeElement)
  const next = { ArrowDown: (index + 1) % buttons.length, ArrowUp: (index - 1 + buttons.length) % buttons.length, Home: 0, End: buttons.length - 1 }[event.key]
  if (next != null) { event.preventDefault(); buttons[next]?.focus() }
}
function contextToggle() { const selected = !contextMenu.value.row.selected; closeContext(); return store.setEnabled(picked.value, selected) }
function contextDetails() { const row = contextMenu.value.row; closeContext(); return showDetails(row) }
async function toggleMore() {
  if (moreOpen.value) { closeMore(); return }
  moreOpen.value = true
  await nextTick(); moreMenu.value?.querySelector('button:not(:disabled)')?.focus()
}
function closeMore(restore = false) { moreOpen.value = false; if (restore) moreButton.value?.focus() }
function fromMore(action) { closeMore(); return action() }
async function showDetails(release) { detailOrigin.value = document.activeElement; await store.details(release) }
function closeDetails() { state.packageDetail = null; nextTick(() => detailOrigin.value?.isConnected && detailOrigin.value.focus()) }
function uniqueName(base) {
  const names = new Set(state.profiles.map(item => item.name))
  if (!names.has(base)) return base
  for (let index = 2; ; index++) if (!names.has(`${base} ${index}`)) return `${base} ${index}`
}
async function openNaming(mode, source = props.workspaceDirty ? 'blank' : 'current') {
  closeMore()
  const current = state.current?.name || ''
  Object.assign(naming, { mode, source, value: mode === 'create' ? uniqueName('我的 Mod 方案') : mode === 'rename' ? current : uniqueName(`${current} 副本`) })
  await nextTick()
  nameDialog.value?.showModal()
  nameDialog.value?.querySelector('input[type=text]')?.select()
}
function closeNaming() { nameDialog.value?.close() }
async function confirmNaming() {
  const value = naming.value.trim(), { mode, source } = naming
  if (!value || state.busy || mode === 'create' && source === 'current' && props.workspaceDirty) return
  closeNaming()
  if (mode === 'create' && source === 'current') { if (await store.capture(value)) emit('applied'); return }
  if (mode === 'create') { await store.create(value); if (state.current && !state.error) await openLibrary(); return }
  if (mode === 'saveAs') await store.saveAs(value)
  else if (mode === 'rename') await store.rename(value)
  else if (mode === 'clone') await store.clone(value)
}
function openReplace() { closeMore(); replaceDialog.value?.showModal() }
function openDelete() { closeMore(); deleteDialog.value?.showModal() }
function closeDelete() { if (!state.busy) { deleteDialog.value?.close(); nextTick(() => moreButton.value?.focus()) } }
async function confirmDelete() {
  if (!state.current || state.busy) return
  const result = await store.deleteCurrent()
  if (result) { deleteDialog.value?.close(); await nextTick(); (moreButton.value || root.value?.querySelector('[data-testid=profile-new]'))?.focus() }
}
async function confirmReplace() {
  if (!state.current || state.busy) return
  replaceDialog.value?.close()
  if (await store.capture(state.current.name, true)) emit('applied')
}
async function addRelease(release) {
  const profile = await store.add(release)
  if (!profile) return
  search.value = ''
  closeLibrary()
  await nextTick()
  picked.value = [release.release_id]
  const row = [...root.value.querySelectorAll('[data-testid=profile-selection]')].find(item => item.dataset.releaseId === release.release_id)
  row?.scrollIntoView({ block: 'nearest' }); row?.focus({ preventScroll: true })
}
async function openLibrary() {
  if (state.busy) return
  await store.packages(0)
  if (!state.error) { await nextTick(); libraryDialog.value?.showModal() }
}
function closeLibrary() { if (state.busy) return; libraryDialog.value?.close() }
function cancelImport() { if (state.busy) return; manifestDialog.value?.close(); state.importPreview = null; state.sourceHandle = null; state.notice = '已取消导入。' }
async function importManifest(apply = false) { const profile = await store.importCommit(importName.value, apply); if (profile) { search.value = ''; picked.value = []; if (apply && profile.completed) emit('applied') } }
async function applySaved() { if (await store.apply()) emit('applied') }
async function openSource(source) {
  pendingSource.value = source
  if (!state.installationId || state.busy) return
  pendingSource.value = null
  return store.openSource(source)
}
async function revealLibrary() { await store.refresh(); await openLibrary() }
async function refresh() { if (!state.installationId) return; await store.refresh(); if (!state.error) await store.packages(state.packageOffset) }
function previewPlan() { closeMore(); if (state.busy || !state.current || state.current.dirty || state.current.profile_revision == null) return; planOrigin.value = moreButton.value; planPreview.value = true }
function closePlan() { planPreview.value = false; nextTick(() => planOrigin.value?.isConnected && planOrigin.value.focus()) }
watch(() => state.importPreview, async preview => {
  if (!preview) { manifestDialog.value?.close(); return }
  importName.value = preview.name || '导入的方案'
  await nextTick(); manifestDialog.value?.showModal()
})
watch(() => props.installation?.installation_id, async id => {
  planPreview.value = false
  clearDrag(); closeContext(false); closeMore()
  libraryDialog.value?.close(); manifestDialog.value?.close(); nameDialog.value?.close(); replaceDialog.value?.close(); deleteDialog.value?.close()
  state.packageDetail = null; search.value = ''
  await store.setInstallation(id || null); picked.value = []; gesture.clear(); await refresh()
  if (id && pendingSource.value) await openSource(pendingSource.value)
}, { immediate: true })
// Coming back to the page picks up changes saved on the Mods page in the meantime.
watch(() => props.active, active => { if (active && state.installationId && !state.busy) void refresh() })
watch(() => state.current?.profile_id, () => { picked.value = []; closeContext(false); deleteDialog.value?.close() })
watch(() => state.busy, busy => { if (!busy && state.installationId && pendingSource.value) void openSource(pendingSource.value) })
watch(() => state.applyPreview, async preview => { if (!preview) applyDialog.value?.close(); else { await nextTick(); applyDialog.value?.showModal() } })
watch(() => state.exportText, async text => { if (!text) exportDialog.value?.close(); else { await nextTick(); exportDialog.value?.showModal() } })
watch(() => [state.current?.profile_id, state.current?.revision, state.current?.profile_revision, state.current?.dirty], () => { planPreview.value = false })
watch(search, () => { clearDrag(); gesture.clear(); anchor.value = null; picked.value = []; closeContext(false) })
watch(() => [state.current?.profile_id, state.current?.revision, state.busy], () => { clearDrag(); anchor.value = null })
onMounted(() => { document.addEventListener('pointerdown', outsidePress); document.addEventListener('pointermove', movePointer, { passive: false }); document.addEventListener('pointerup', releasePointer); document.addEventListener('pointercancel', cancelPointer); document.addEventListener('keydown', cancelWithEscape) })
onUnmounted(() => { clearDrag(); document.removeEventListener('pointerdown', outsidePress); document.removeEventListener('pointermove', movePointer); document.removeEventListener('pointerup', releasePointer); document.removeEventListener('pointercancel', cancelPointer); document.removeEventListener('keydown', cancelWithEscape); store.dispose() })
defineExpose({ refresh, revealLibrary, openSource })
</script>
<template>
  <main ref="root" class="garden-profiles" data-testid="profile-editor" :data-busy="state.busy" :aria-busy="state.busy" aria-label="方案">
    <div class="garden-intro">
      <div><h1>方案</h1><p>方案记住一组 Mod 的启用状态和具体版本，可以随时切换、应用到游戏或分享给别人。</p></div>
      <div v-if="installation" class="garden-intro-actions">
        <button class="garden-btn soft icon" data-testid="profile-refresh" :disabled="state.busy" title="刷新" aria-label="刷新" @click="refresh"><RefreshCw :size="16"/></button>
        <button class="garden-btn" data-testid="profile-share-import" :disabled="state.busy" title="导入方案清单或整合包" @click="store.chooseImport"><FolderInput :size="16"/>导入</button>
      </div>
    </div>
    <div class="profile-feedback" aria-live="polite">
      <p v-if="state.busy" role="status" data-testid="profile-progress" class="working"><Sprout :size="15" class="garden-seedling" aria-hidden="true"/>{{ state.task?.state === 'cancelling' ? '正在取消…' : state.task?.stage === 'capturing' ? '首次保存：正在保留 Mod 文件副本…' : '正在处理…' }}<template v-if="state.task?.total != null"> {{ state.task.completed }} / {{ state.task.total }} 个文件</template><span v-if="state.task?.stage === 'capturing' && state.task.data?.mod"> · {{ state.task.data.mod }}</span><button v-if="state.task" class="garden-link" :disabled="state.task.state === 'cancelling'" @click="store.cancel">取消</button></p>
      <p v-else-if="state.notice" data-testid="profile-notice" role="status" class="garden-notice">{{ state.notice }}</p>
      <p v-if="state.error" data-testid="profile-error" role="alert" class="profile-error">{{ state.error }}</p>
    </div>

    <div v-if="!installation" class="garden-empty"><span class="garden-pot lg"><Sprout :size="28"/></span><b>先选择游戏安装</b><span>在右上角选择游戏后，就能保存和切换这台电脑上的 Mod 方案。</span><span v-if="pendingSource">已收到「{{ pendingSource.display_name || '整合包' }}」，选择游戏后继续导入。</span></div>

    <div v-else class="profile-garden">
      <aside class="seed-shelf" aria-label="我的方案">
        <h2 class="garden-bed-label">我的方案<span>{{ state.profiles.length }}</span></h2>
        <div class="packets" data-testid="profile-select" :data-value="state.current?.profile_id || ''">
          <button v-for="profile in state.profiles" :key="profile.profile_id" class="seed-packet" :class="{ on: profile.profile_id === state.current?.profile_id }" :aria-current="profile.profile_id === state.current?.profile_id ? 'true' : undefined" :disabled="state.busy" @click="profile.profile_id !== state.current?.profile_id && store.select(profile.profile_id)">
            <span class="packet-art" aria-hidden="true"><Sprout :size="18"/></span>
            <span class="packet-text"><b>{{ profile.name }}</b><small :class="{ dirty: profile.dirty }">{{ packetStatus(profile) }}</small></span>
          </button>
        </div>
        <button class="seed-packet add" data-testid="profile-new" :disabled="state.busy" @click="openNaming('create')"><Plus :size="17" aria-hidden="true"/>新方案</button>
      </aside>

      <div v-if="!state.current" class="garden-empty profile-start">
        <span class="garden-pot lg"><Sprout :size="28"/></span><b>还没有方案</b>
        <span>把模组页现在的启用状态存成第一个方案，或者从空白开始挑选 Mod。</span>
        <div class="start-actions"><button class="garden-btn green" :disabled="state.busy || workspaceDirty" @click="openNaming('create', 'current')">从当前模组列表创建</button><button class="garden-btn" data-testid="profile-create-default" :disabled="state.busy" @click="openNaming('create', 'blank')">新建空白方案</button></div>
        <span v-if="workspaceDirty" class="hint">模组页有未保存的更改，保存后才能从当前列表创建。</span>
      </div>

      <section v-else class="profile-sheet" aria-labelledby="profile-current-name">
        <header class="sheet-head">
          <div class="sheet-title">
            <h2 id="profile-current-name" data-testid="profile-current-name">{{ state.current.name }}</h2>
            <p data-testid="profile-state" :data-revision="state.current.revision" :data-dirty="state.current.dirty"><span class="tg" :class="state.current.dirty ? 'pend' : 'on'">{{ state.current.dirty ? '有未保存的修改' : '已保存' }}</span>{{ counts.on }} 个启用 · {{ counts.off }} 个停用<template v-if="counts.missing"> · <em>{{ counts.missing }} 个缺少文件</em></template></p>
          </div>
          <div class="sheet-actions">
            <button class="garden-btn soft icon sm" data-testid="profile-undo" :disabled="state.busy || !state.current.can_undo" title="撤销" aria-label="撤销" @click="store.undo"><Undo2 :size="16"/></button>
            <button class="garden-btn soft icon sm" data-testid="profile-redo" :disabled="state.busy || !state.current.can_redo" title="重做" aria-label="重做" @click="store.redo"><Redo2 :size="16"/></button>
            <template v-if="state.current.dirty">
              <button class="garden-btn" data-testid="profile-save-as" :disabled="state.busy" @click="openNaming('saveAs')">另存为…</button>
              <button class="garden-btn green" data-testid="profile-save" :disabled="state.busy" @click="store.save">保存</button>
            </template>
            <button v-else class="garden-btn green" data-testid="profile-apply" :disabled="state.busy || Boolean(applyBlocked)" :title="applyBlocked" @click="store.previewApply">应用到游戏</button>
            <div class="more">
              <button ref="moreButton" class="garden-btn icon" data-testid="profile-more" :disabled="state.busy" aria-label="更多操作" title="更多操作" aria-haspopup="menu" :aria-expanded="moreOpen" @click="toggleMore"><Ellipsis :size="18"/></button>
              <div v-if="moreOpen" ref="moreMenu" class="garden-menu" role="menu" aria-label="方案操作" @keydown="menuKeys($event, moreMenu, closeMore)">
                <button role="menuitem" data-testid="profile-rename-open" @click="openNaming('rename')">重命名</button>
                <button role="menuitem" data-testid="profile-clone-open" @click="openNaming('clone')">复制为新方案</button>
                <button role="menuitem" data-testid="profile-capture-replace" :disabled="workspaceDirty" :title="workspaceDirty ? '模组页有未保存的更改，先保存或撤销' : ''" @click="openReplace">用当前模组列表覆盖</button>
                <hr>
                <button role="menuitem" data-testid="profile-share-manifest" @click="fromMore(() => store.exportShare('manifest'))">导出清单</button>
                <button role="menuitem" data-testid="profile-share-export" @click="fromMore(() => store.exportShare('bundle'))">导出整合包（含 Mod 文件）</button>
                <button role="menuitem" data-testid="profile-export-preview" @click="fromMore(store.exportPreview)">查看清单内容</button>
                <hr>
                <button role="menuitem" data-testid="profile-plan-preview" :disabled="state.current.dirty || state.current.profile_revision == null" :title="state.current.dirty ? '先保存修改' : ''" @click="previewPlan">部署预览</button>
                <hr>
                <button role="menuitem" class="danger" data-testid="profile-delete" @click="openDelete">删除方案…</button>
              </div>
            </div>
          </div>
        </header>

        <div class="sheet-tools">
          <label class="garden-search" :class="{ active: search }"><Search :size="17" aria-hidden="true"/><input v-model="search" data-testid="profile-search" type="search" aria-label="搜索方案中的 Mod" placeholder="搜索 Mod 名称、版本或分组"/><button v-if="search" type="button" class="search-clear" aria-label="清除搜索" @click="search = ''"><X :size="15"/></button></label>
          <button class="garden-btn" data-testid="profile-library-open" :disabled="state.busy" @click="openLibrary"><Plus :size="16"/>添加 Mod</button>
        </div>

        <div class="lanes">
          <section v-for="side in sides" :key="String(side.selected)" :data-testid="side.testid" :class="['lane', { off: !side.selected, 'drop-target': dropTarget?.selected === side.selected, 'drop-at-end': dropTarget?.selected === side.selected && dropTarget.before_release_id === null }]">
            <h3><component :is="side.selected ? CircleCheck : CirclePause" :size="16" aria-hidden="true"/>{{ side.label }}<span>{{ sideRows(side.selected).length }}</span></h3>
            <div class="rows" role="listbox" :aria-label="`${side.label} Mod`" aria-multiselectable="true">
              <article v-for="selection in sideRows(side.selected)" :key="selection.release_id" data-testid="profile-selection" :data-release-id="selection.release_id" :data-selected="selection.selected" :data-resolved="selection.resolved" :aria-selected="picked.includes(selection.release_id)" :aria-label="`${selectionName(selection)}，${selection.version_raw ?? '版本未知'}`" :class="{ picked: picked.includes(selection.release_id), missing: !selection.resolved, 'insert-before': dropTarget?.before_release_id === selection.release_id }" :draggable="false" role="option" tabindex="0" @pointerdown="pressRow($event,selection)" @click="selectRow($event,selection)" @keydown="rowKey($event,selection)" @contextmenu="openContext($event,selection)" @dragstart.prevent @lostpointercapture="cancelPointer">
                <input v-model="picked" class="seed-pick" data-testid="profile-pick" type="checkbox" :value="selection.release_id" :aria-label="`选择 ${selectionName(selection)}`" :disabled="state.busy"/>
                <div class="row-label"><strong>{{ selectionName(selection) }}</strong><small>{{ selection.version_raw ?? '版本未知' }}<span v-if="selection.group"> · {{ selection.group }}</span></small><small v-if="!selection.resolved" class="resolve-hint">缺少此版本的文件，重新导入后刷新</small></div>
                <button class="row-info" data-testid="profile-selection-detail" :disabled="state.busy" title="文件详情" :aria-label="`${selectionName(selection)} 的文件详情`" @click="showDetails(selection)"><Info :size="16"/></button>
                <button class="leaf-switch" data-testid="profile-toggle" :aria-pressed="selection.selected" :disabled="state.busy" :title="selection.selected ? '停用' : '启用'" :aria-label="`${selection.selected ? '停用' : '启用'} ${selectionName(selection)}`" @click="store.setEnabled([selection.release_id],!selection.selected)"><span></span></button>
              </article>
              <div v-if="!sideRows(side.selected).length" class="lane-empty">{{ search ? '没有匹配的 Mod' : side.selected ? '把 Mod 拖到这里启用' : '把 Mod 拖到这里停用' }}</div>
            </div>
          </section>
        </div>
        <p class="lane-hint" :role="draggingCount ? 'status' : undefined" :data-testid="draggingCount ? 'profile-drag-status' : undefined">
          <template v-if="draggingCount">正在拖动 {{ draggingCount }} 项，{{ dropTarget === null ? '移到任一列中' : '松开放到标记位置' }}；按 Esc 取消。</template>
          <template v-else>拖动调整顺序或跨栏启停 · Ctrl 多选 · Shift 连选 · 列表顺序只用于整理，游戏加载顺序由 MelonLoader 和依赖决定</template>
        </p>
        <div v-if="picked.length" class="selection-bar" role="toolbar" aria-label="所选 Mod">
          <span>已选 {{ picked.length }} 项</span>
          <button data-testid="profile-batch-enable" :disabled="state.busy" @click="store.setEnabled(picked,true)">启用</button>
          <button data-testid="profile-batch-disable" :disabled="state.busy" @click="store.setEnabled(picked,false)">停用</button>
          <details class="group-menu"><summary>分组</summary><div><input v-model="group" data-testid="profile-group" aria-label="分组名称" placeholder="留空则移出分组" maxlength="80" :disabled="state.busy"/><button data-testid="profile-apply-group" :disabled="state.busy" @click="store.setGroup(picked,group)">确定</button></div></details>
          <button class="clear" aria-label="取消选择" title="取消选择" @click="picked = []"><X :size="15"/></button>
        </div>
      </section>
    </div>

    <dialog ref="nameDialog" class="sheet-dialog name-dialog" data-testid="profile-name-dialog" aria-labelledby="profile-name-title" @cancel.prevent="closeNaming">
      <form v-if="naming.mode" @submit.prevent="confirmNaming">
        <header><h2 id="profile-name-title">{{ namingText[naming.mode].title }}</h2><button type="button" class="close" aria-label="关闭" @click="closeNaming"><X :size="18"/></button></header>
        <div class="sheet-body">
          <label class="field">方案名称<input v-model="naming.value" type="text" data-testid="profile-name" maxlength="120" autocomplete="off"/></label>
          <fieldset v-if="naming.mode === 'create'" class="source-choice"><legend>内容</legend>
            <label :class="{ on: naming.source === 'current', disabled: workspaceDirty }"><input v-model="naming.source" type="radio" value="current" :disabled="workspaceDirty"/><span><b>当前模组列表</b><small>{{ workspaceDirty ? '模组页有未保存的更改，保存后才能使用。' : '记录模组页已保存的启停和版本，也会把游戏目录里已有的 Mod 复制一份进包库。' }}</small></span></label>
            <label :class="{ on: naming.source === 'blank' }"><input v-model="naming.source" type="radio" value="blank"/><span><b>空白方案</b><small>创建后从包库挑选要加入的 Mod。</small></span></label>
          </fieldset>
          <p v-else-if="naming.mode === 'saveAs'" class="hint">修改会保存到新方案，「{{ state.current?.name }}」保持上次保存的样子。</p>
        </div>
        <footer><button type="button" @click="closeNaming">取消</button><button type="submit" class="primary" :data-testid="naming.mode === 'create' ? (naming.source === 'current' ? 'profile-capture-new' : 'profile-create') : naming.mode === 'saveAs' ? 'profile-save-as-confirm' : naming.mode === 'rename' ? 'profile-rename' : 'profile-clone'" :disabled="state.busy || !naming.value.trim() || naming.mode === 'create' && naming.source === 'current' && workspaceDirty">{{ namingText[naming.mode].confirm }}</button></footer>
      </form>
    </dialog>

    <dialog ref="deleteDialog" class="sheet-dialog" data-testid="profile-delete-dialog" aria-labelledby="profile-delete-title" @cancel.prevent="closeDelete">
      <header><h2 id="profile-delete-title">删除「{{ state.current?.name }}」？</h2><button class="close" aria-label="关闭" :disabled="state.busy" @click="closeDelete"><X :size="18"/></button></header>
      <div class="sheet-body"><p>此方案{{ state.current?.dirty ? '及未保存的修改' : '' }}会被删除，无法撤销。游戏中的 Mod、包库文件和部署历史都会保留。</p><p v-if="state.error" role="alert" class="profile-error">{{ state.error }}</p></div>
      <footer><button data-testid="profile-delete-cancel" autofocus :disabled="state.busy" @click="closeDelete">取消</button><button class="danger" data-testid="profile-delete-confirm" :disabled="state.busy" @click="confirmDelete">{{ state.busy ? '正在删除…' : '删除方案' }}</button></footer>
    </dialog>

    <dialog ref="replaceDialog" class="sheet-dialog" aria-labelledby="profile-replace-title" @cancel.prevent="replaceDialog.close()">
      <header><h2 id="profile-replace-title">用当前模组列表覆盖「{{ state.current?.name }}」？</h2><button class="close" aria-label="关闭" @click="replaceDialog.close()"><X :size="18"/></button></header>
      <div class="sheet-body"><p>方案里的启停和版本会换成模组页当前已保存的状态，方案名称不变。这个操作不会改动游戏文件。</p></div>
      <footer><button @click="replaceDialog.close()">取消</button><button class="primary" data-testid="profile-capture-replace-confirm" :disabled="state.busy" @click="confirmReplace">覆盖</button></footer>
    </dialog>

    <dialog ref="libraryDialog" class="sheet-dialog wide" data-testid="profile-library" aria-labelledby="library-title" @cancel.prevent.stop="closeLibrary">
      <header><div><h2 id="library-title">从包库添加</h2><p class="hint">{{ state.current ? `加入「${state.current.name}」并启用。同一份文件可以给多个方案使用。` : '先创建一个方案，再添加 Mod。' }}</p></div><button class="close" aria-label="关闭包库" :disabled="state.busy" @click="closeLibrary"><X :size="18"/></button></header>
      <div class="sheet-body">
        <div class="library-rows">
          <article v-for="release in state.packages" :key="release.release_id" data-testid="profile-package" :data-release-id="release.release_id">
            <span class="garden-pot" aria-hidden="true"><Sprout :size="20"/></span>
            <div><strong>{{ releaseName(release) }}</strong><small>{{ release.version_raw ?? '版本未知' }} · {{ release.integrity === 'verified' ? '文件完好' : '文件校验失败' }}</small></div>
            <button class="row-info" data-testid="profile-package-detail" :disabled="state.busy" title="文件详情" aria-label="文件详情" @click="showDetails(release)"><Info :size="16"/></button>
            <button :class="{ primary: !inProfile(release) }" data-testid="profile-add" :disabled="state.busy || !state.current || release.integrity !== 'verified' || inProfile(release)?.selected" @click="addRelease(release)">{{ !inProfile(release) ? '添加' : inProfile(release).selected ? '已加入' : '启用' }}</button>
          </article>
          <p v-if="!state.packages.length" class="hint">包库中还没有 Mod。在模组页点击“导入”添加 DLL、文件夹或 ZIP。</p>
        </div>
        <p v-if="state.error" role="alert" class="profile-error">{{ state.error }}</p>
      </div>
      <footer><span class="pager">第 {{ state.packageOffset / 20 + 1 }} 页</span><button :disabled="state.busy || state.packageOffset === 0" @click="store.packages(Math.max(0,state.packageOffset-20))">上一页</button><button :disabled="state.busy || state.packages.length < 20" @click="store.packages(state.packageOffset+20)">下一页</button></footer>
    </dialog>

    <dialog v-if="state.importPreview" ref="manifestDialog" data-testid="profile-import-preview" class="sheet-dialog wide" aria-labelledby="manifest-title" @cancel.prevent.stop="cancelImport">
      <header><div><h2 id="manifest-title">{{ state.importPreview.includes_mods ? '导入 Mod 整合包' : '导入方案清单' }}</h2><p class="hint">包含 {{ state.importPreview.selections?.length || 0 }} 个 Mod 版本，确认后会新建一个方案。</p></div><button class="close" :disabled="state.busy" aria-label="取消导入" @click="cancelImport"><X :size="18"/></button></header>
      <div class="sheet-body">
        <ul class="preview-list"><li v-for="selection in state.importPreview.selections || []" :key="selection.release_id"><strong>{{ selectionName(selection) }}</strong><span>{{ selection.version_raw ?? '版本未知' }}</span><span class="tg" :class="selection.resolved ? 'on' : 'bad'">{{ selection.resolved ? '文件可用' : '缺少文件' }}</span></li></ul>
        <label class="field">方案名称<input v-model="importName" aria-label="导入方案名称" maxlength="120" :disabled="state.busy"/></label>
        <p class="hint">“导入并应用”会按这个方案启停 Mod，并停用其中没有的 Mod；包库里的旧版本和其他方案都会保留，不会改动存档。缺少文件的版本会留在方案里，补齐后再应用。</p>
        <p v-if="state.error" role="alert" class="profile-error">{{ state.error }}</p>
      </div>
      <footer><button :disabled="state.busy" @click="cancelImport">取消</button><button data-testid="profile-import-commit" :disabled="state.busy || !importName.trim()" @click="importManifest(false)">只导入</button><button class="primary" data-testid="profile-import-apply" :disabled="state.busy || workspaceDirty || !importName.trim() || state.importPreview.missing_count > 0" @click="importManifest(true)">{{ state.busy ? '正在导入…' : '导入并应用' }}</button></footer>
    </dialog>

    <dialog ref="applyDialog" class="sheet-dialog wide" aria-labelledby="profile-apply-title" @cancel.prevent="state.applyPreview = null">
      <template v-if="state.applyPreview">
        <header><div><h2 id="profile-apply-title">应用「{{ state.current?.name }}」</h2><p class="hint">没有列入方案的 Mod 会停用；包库和其他方案保留。</p></div><button class="close" aria-label="关闭" :disabled="state.busy" @click="state.applyPreview = null"><X :size="18"/></button></header>
        <div class="sheet-body"><ul class="preview-list"><li v-for="item in state.applyPreview.changes" :key="item.id"><span class="tg" :class="item.enable ? 'on' : 'off'">{{ item.enable ? '启用' : '停用' }}</span><strong>{{ item.display_name }}</strong><span>{{ item.version_raw || '' }}</span></li></ul><p v-if="!state.applyPreview.changes.length" class="hint">游戏里的 Mod 已经和方案一致。</p><p v-if="state.error" role="alert" class="profile-error">{{ state.error }}</p></div>
        <footer><button :disabled="state.busy" @click="state.applyPreview = null">取消</button><button class="primary" data-testid="profile-apply-confirm" :disabled="state.busy" @click="applySaved">应用</button></footer>
      </template>
    </dialog>

    <dialog ref="exportDialog" class="sheet-dialog wide" aria-labelledby="profile-export-title" @cancel.prevent="state.exportText = ''" @close="state.exportText = ''">
      <header><h2 id="profile-export-title">清单内容</h2><button class="close" aria-label="关闭" @click="state.exportText = ''"><X :size="18"/></button></header>
      <div class="sheet-body"><textarea data-testid="profile-export-text" :value="state.exportText" readonly rows="14" aria-label="清单内容"/></div>
    </dialog>

    <PackageDetails v-if="state.packageDetail" :detail="state.packageDetail" :busy="state.busy" @close="closeDetails" @page="store.details(state.packageDetail,$event)"/>
    <PlanPreview v-if="planPreview && state.current && installation" :bridge="bridge" :profile="state.current" :installation="installation" @close="closePlan"/>
    <div v-if="contextMenu" ref="menu" class="garden-menu context" role="menu" aria-label="所选 Mod 操作" data-testid="profile-context-menu" :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }" @keydown="menuKeys($event, menu, closeContext)"><button role="menuitem" data-testid="profile-context-toggle" @click="contextToggle">{{ contextMenu.row.selected ? '停用' : '启用' }}所选 {{ picked.length }} 项</button><button role="menuitem" @click="contextDetails">文件详情</button><button role="menuitem" :disabled="!state.current?.can_undo" @click="closeContext();store.undo()">撤销上次修改</button></div>
  </main>
</template>
<style scoped src="./profileEditor.css"></style>
