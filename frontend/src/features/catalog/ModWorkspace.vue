<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Package, Search, RefreshCw, X, Check, Undo2, TriangleAlert, Trash2, Sprout, Sun, Play, FileText, Gamepad2, LayoutGrid, List, ChevronLeft, ChevronRight } from 'lucide-vue-next'
import { canToggleMod, createModWorkspaceRuntime, fileTree, workspaceStateText } from './modWorkspaceRuntime.js'
import ModFileTree from './ModFileTree.vue'
import SelectMenu from '../../shared/components/desktop/SelectMenu.vue'
import { flip, flyLeaf } from './gardenMotion.js'

const props = defineProps({ bridge: Object, installation: Object, active: { type: Boolean, default: true } })
const emit = defineEmits(['logs', 'busy', 'state', 'launch'])
const state = reactive({}), root = ref(null), beds = ref(null), basketIcon = ref(null), searchInput = ref(null)
const runtime = createModWorkspaceRuntime(props.bridge, state)
const filter = ref('all'), openId = ref(null), confirmId = ref(null), bouncing = ref(false), arrivedId = ref(null)

// View preferences are UI-only and stay on this machine.
const PREFERENCE_KEY = 'psmm.mods.view'
const PAGE_SIZES = [24, 48, 96]
const stored = (() => { try { return JSON.parse(localStorage.getItem(PREFERENCE_KEY) || '{}') } catch { return {} } })()
const view = ref(stored.view === 'list' ? 'list' : 'cards')
const sort = ref(['name', 'state', 'files'].includes(stored.sort) ? stored.sort : 'name')
const pageSize = ref(PAGE_SIZES.includes(stored.pageSize) ? stored.pageSize : 24)
const page = ref(1)
const SORT_OPTIONS = [{ value: 'name', label: '名称' }, { value: 'state', label: '启用状态' }, { value: 'files', label: '文件数' }]
watch([view, sort, pageSize], () => { try { localStorage.setItem(PREFERENCE_KEY, JSON.stringify({ view: view.value, sort: sort.value, pageSize: pageSize.value })) } catch { /* storage unavailable */ } })

const locked = computed(() => state.loading || state.busy)
const isEnabled = item => state.enabledIds.includes(item.id)
const pending = item => isEnabled(item) !== state.baseEnabled.includes(item.id)
const needsCare = item => ['unknown', 'drifted'].includes(item.state) || item.manageable === false || Boolean(item.message)
const canMove = item => !locked.value && canToggleMod(item)
const enableAll = computed(() => runtime.bulkSelection(true))
const disableAll = computed(() => runtime.bulkSelection(false))
const sourceRank = item => item.source === 'external' ? 1 : 0
const query = computed(() => state.query.trim().toLocaleLowerCase())
const matches = computed(() => state.items.filter(item => !query.value || `${item.display_name} ${item.version_raw || ''}`.toLocaleLowerCase().includes(query.value)))
const visible = computed(() => {
  const rows = matches.value.filter(item => filter.value === 'on' ? isEnabled(item) : filter.value === 'off' ? !isEnabled(item) : filter.value === 'care' ? needsCare(item) : true)
  const byName = (a, b) => a.display_name.localeCompare(b.display_name, 'zh-CN', { numeric: true, sensitivity: 'base' })
  const order = { name: byName, state: (a, b) => Number(isEnabled(b)) - Number(isEnabled(a)) || byName(a, b), files: (a, b) => b.file_count - a.file_count || byName(a, b) }[sort.value]
  return [...rows].sort((a, b) => sourceRank(a) - sourceRank(b) || order(a, b))
})
const pageCount = computed(() => Math.max(1, Math.ceil(visible.value.length / pageSize.value)))
const pageItems = computed(() => visible.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const groups = computed(() => [
  { key: 'library', label: '包库中的 Mod', icon: Sprout, items: pageItems.value.filter(item => item.source !== 'external'), total: visible.value.filter(item => item.source !== 'external').length },
  { key: 'external', label: '游戏目录中发现', icon: Gamepad2, items: pageItems.value.filter(item => item.source === 'external'), total: visible.value.filter(item => item.source === 'external').length },
].filter(group => group.items.length))
const pageNumbers = computed(() => {
  const total = pageCount.value, current = page.value, wanted = new Set([1, total, current - 1, current, current + 1])
  const numbers = [...wanted].filter(n => n >= 1 && n <= total).sort((a, b) => a - b)
  return numbers.flatMap((n, i) => i && n - numbers[i - 1] > 1 ? ['…', n] : [n])
})
const rangeText = computed(() => {
  if (!visible.value.length) return ''
  const start = (page.value - 1) * pageSize.value + 1, end = Math.min(visible.value.length, page.value * pageSize.value)
  return `第 ${start}–${end} 个，共 ${visible.value.length} 个`
})
const counts = computed(() => ({ all: state.items.length, on: state.enabledIds.length, off: state.items.length - state.enabledIds.length, care: state.items.filter(needsCare).length }))
const changes = computed(() => state.items.filter(pending))
const opened = computed(() => state.items.find(item => item.id === openId.value) || null)
const tree = computed(() => opened.value && state.focusedId === opened.value.id ? fileTree(state.detail?.files || []) : [])
const progress = computed(() => state.task?.data?.unit === 'files' ? state.task.data : state.task)
const stageText = computed(() => state.task?.state === 'cancelling' ? '正在取消并核对文件…' : ({ preparing: '正在保留原有 Mod 副本…', staging: '正在准备文件…', applying: '正在应用 Mod 变更…' })[state.task?.stage] || '正在保存 Mod 状态…')
const progressPercent = computed(() => progress.value?.total ? Math.round(progress.value.completed / progress.value.total * 100) : null)
const showBasket = computed(() => state.dirty || state.busy)

function stateLabel(item) {
  if (pending(item)) return isEnabled(item) ? '待保存 · 启用' : '待保存 · 停用'
  return workspaceStateText(item.state)
}
function tagClass(item) {
  if (pending(item)) return 'pend'
  return ({ enabled: 'on', disabled: 'off', not_installed: 'off', drifted: 'bad', unknown: 'bad' })[item.state] || 'off'
}
const sourceText = item => item.source === 'external' ? '游戏目录中已有，未保存到包库' : item.source === 'library' ? '本地包库' : '管理器记录'

watch(() => props.installation?.installation_id, id => { openId.value = null; confirmId.value = null; filter.value = 'all'; page.value = 1; void runtime.setInstallation(id) }, { immediate: true })
watch(() => state.busy, value => emit('busy', value))
watch(() => [state.dirty, state.loaded, locked.value, changes.value.length], ([dirty, loaded, isLocked, count]) => emit('state', { dirty, loaded, locked: isLocked, changes: count }), { immediate: true })
watch([query, filter, sort, pageSize], () => { page.value = 1 })
watch(pageCount, total => { if (page.value > total) page.value = total })
watch(pageItems, items => { if (openId.value && !items.some(item => item.id === openId.value)) { openId.value = null; confirmId.value = null } })

function setFilter(key) { if (filter.value !== key) void flip(beds.value, () => { filter.value = key }) }
function setView(mode) { if (view.value !== mode) { openId.value = null; confirmId.value = null; view.value = mode } }
function goPage(number) {
  const next = Math.min(pageCount.value, Math.max(1, number))
  if (next === page.value) return
  page.value = next; openId.value = null; confirmId.value = null
  void nextTick(() => root.value?.querySelector('.garden-toolbar')?.scrollIntoView({ block: 'nearest' }))
}
function clearSearch() { state.query = ''; searchInput.value?.focus() }
function open(item, event) {
  if (event?.target.closest('button,input,a,summary,select')) return
  confirmId.value = null
  if (openId.value === item.id) {
    const target = event?.target, card = event?.currentTarget
    const detail = card?.querySelector('.plant-open')
    if (target === card && detail && event.clientY < detail.getBoundingClientRect().top) close()
    return
  }
  void flip(beds.value, () => { openId.value = item.id })
  void runtime.select(item.id)
}
function close() { confirmId.value = null; void flip(beds.value, () => { openId.value = null }) }
function toggle(item, switchElement) {
  if (!canMove(item)) return
  const basketWasOpen = showBasket.value
  if (!runtime.move([item.id], !isEnabled(item)) || !pending(item)) return
  const fly = () => flyLeaf(switchElement, basketIcon.value, () => { bouncing.value = false; void nextTick(() => { bouncing.value = true }) })
  if (basketWasOpen) fly(); else setTimeout(fly, 420)
}
function cardKey(event, item) {
  if (event.target !== event.currentTarget) return
  if (event.key === ' ') { event.preventDefault(); toggle(item, event.currentTarget.querySelector('.leaf-switch')) }
  else if (event.key === 'Enter') { event.preventDefault(); open(item) }
  else if (event.key === 'Escape' && openId.value) { event.preventDefault(); close() }
  else if (event.key.startsWith('Arrow')) {
    event.preventDefault()
    const cards = [...(beds.value?.querySelectorAll('[data-mod-id]') || [])], index = cards.indexOf(event.currentTarget)
    cards[index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1)]?.focus()
  }
}
async function uninstall(item) {
  if (await runtime.uninstall(item)) confirmId.value = null
}
async function reveal(id) {
  const found = await runtime.reveal(id)
  if (!found) return
  filter.value = 'all'; state.query = ''
  await nextTick()
  const index = visible.value.findIndex(item => item.id === found)
  if (index >= 0) page.value = Math.floor(index / pageSize.value) + 1
  openId.value = found; arrivedId.value = found
  await nextTick()
  root.value?.querySelector(`[data-mod-id="${found}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  setTimeout(() => { if (arrivedId.value === found) arrivedId.value = null }, 1600)
}
const typing = () => { const element = document.activeElement; return element?.tagName === 'TEXTAREA' || element?.tagName === 'SELECT' || (element?.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(element.type)) }
function keyboard(event) {
  if (!props.active || document.querySelector('dialog[open]')) return
  const key = event.key.toLowerCase()
  if (key === 'escape' && openId.value) { event.preventDefault(); close() }
  else if ((event.ctrlKey || event.metaKey) && key === 's') { event.preventDefault(); void runtime.save() }
  else if ((event.ctrlKey || event.metaKey) && key === 'f') { event.preventDefault(); searchInput.value?.focus(); searchInput.value?.select() }
  else if ((event.ctrlKey || event.metaKey) && key === 'z' && !typing()) { event.preventDefault(); runtime.undo() }
  else if (!typing() && event.key === 'PageDown') { event.preventDefault(); goPage(page.value + 1) }
  else if (!typing() && event.key === 'PageUp') { event.preventDefault(); goPage(page.value - 1) }
}
onMounted(() => window.addEventListener('keydown', keyboard))
onUnmounted(() => { runtime.dispose(); window.removeEventListener('keydown', keyboard) })
defineExpose({ refresh: runtime.refresh, reveal, save: runtime.save, discard: runtime.discard })
</script>

<template>
  <main ref="root" class="garden-mods" data-testid="mod-workspace" :aria-busy="locked">
    <div class="garden-intro">
      <div>
        <h1>模组</h1>
        <p v-if="installation && state.loaded"><b>{{ counts.on }}</b> 个已启用 · {{ counts.off }} 个未启用 · 开关后点击“保存”才会改动游戏文件</p>
        <p v-else-if="installation">正在读取游戏目录中的 Mod…</p>
        <p v-else>先在右上角选择游戏安装。</p>
      </div>
      <div class="garden-intro-actions">
        <div v-if="installation" class="workspace-bulk-actions" role="group" aria-label="批量启用状态">
          <button type="button" class="garden-btn soft sm" data-testid="workspace-enable-all" :disabled="locked || !state.loaded || !enableAll.ids.length && !enableAll.ambiguous" title="启用整个列表中的可用 Mod，点击保存后生效" @click="runtime.setAllEnabled(true)">全部启用</button>
          <button type="button" class="garden-btn soft sm" data-testid="workspace-disable-all" :disabled="locked || !state.loaded || !disableAll.ids.length" title="禁用整个列表中的可管理 Mod，点击保存后生效" @click="runtime.setAllEnabled(false)">全部禁用</button>
        </div>
        <button class="garden-btn soft icon" data-testid="workspace-refresh" :disabled="locked || state.dirty || !installation" :title="state.dirty ? '先保存或撤销未保存的更改' : '重新读取游戏目录和包库'" aria-label="刷新" @click="runtime.refresh"><RefreshCw :size="16"/></button>
        <slot name="actions"></slot>
      </div>
    </div>
    <div id="garden-sign-slot"></div>
    <p class="garden-notice" role="status">{{ state.busy ? '' : state.notice }}</p>
    <div v-if="state.error" class="garden-care-banner" role="alert" data-testid="workspace-error"><TriangleAlert :size="18" aria-hidden="true"/><span>{{ state.error }}</span><button v-if="state.dirty && !state.busy" class="garden-btn soft sm" @click="runtime.discard(); runtime.refresh()">放弃更改并重新读取</button></div>

    <div v-if="!installation" class="garden-dawn">
      <div class="garden-sunrise" aria-hidden="true"></div>
      <div class="garden-welcome">
        <h2>开始管理 Probably Stolen 的 Mod</h2>
        <p>点击右上角的游戏安装选择游戏目录。PSMM 只改动 Mods、Plugins、UserLibs，不会改动存档。</p>
        <ol class="garden-stones"><li class="cur"><b>1</b>选择游戏</li><li><b>2</b>确认 MelonLoader</li><li><b>3</b>启用 Mod</li></ol>
      </div>
    </div>

    <template v-else>
      <div class="garden-toolbar">
        <label class="garden-search" :class="{ active: state.query }">
          <Search :size="17" aria-hidden="true"/>
          <input ref="searchInput" v-model="state.query" data-testid="mod-search" type="search" aria-label="搜索 Mod" placeholder="搜索 Mod 名称或版本"/>
          <span v-if="state.query" class="search-count" role="status">{{ visible.length }} 个结果</span>
          <button v-if="state.query" type="button" class="search-clear" aria-label="清除搜索" @click="clearSearch"><X :size="15"/></button>
          <kbd v-else aria-hidden="true">Ctrl F</kbd>
        </label>
        <div class="garden-chips" role="group" aria-label="筛选">
          <button :class="{ on: filter === 'all' }" :aria-pressed="filter === 'all'" @click="setFilter('all')">全部<span>{{ counts.all }}</span></button>
          <button :class="{ on: filter === 'on' }" :aria-pressed="filter === 'on'" @click="setFilter('on')">已启用<span>{{ counts.on }}</span></button>
          <button :class="{ on: filter === 'off' }" :aria-pressed="filter === 'off'" @click="setFilter('off')">未启用<span>{{ counts.off }}</span></button>
          <button v-if="counts.care" class="care" :class="{ on: filter === 'care' }" :aria-pressed="filter === 'care'" @click="setFilter('care')">需处理<span>{{ counts.care }}</span></button>
        </div>
        <div class="garden-toolbar-end">
          <div class="garden-select"><span>排序</span><SelectMenu v-model="sort" aria-label="排序方式" :options="SORT_OPTIONS"/></div>
          <div class="garden-view-switch" role="group" aria-label="显示方式">
            <button type="button" :class="{ on: view === 'cards' }" :aria-pressed="view === 'cards'" title="卡片" aria-label="卡片视图" @click="setView('cards')"><LayoutGrid :size="16"/></button>
            <button type="button" :class="{ on: view === 'list' }" :aria-pressed="view === 'list'" title="列表" aria-label="列表视图" @click="setView('list')"><List :size="16"/></button>
          </div>
        </div>
      </div>
      <div class="garden-wrap">
        <div>
          <div ref="beds" class="garden-beds" data-testid="mod-library-list">
            <p v-if="state.loading && !state.items.length" class="garden-empty"><span class="garden-seedling"><Sprout :size="28"/></span>正在读取 Mod…</p>
            <div v-else-if="!state.items.length" class="garden-empty"><span class="garden-pot lg"><Sprout :size="28"/></span><b>还没有 Mod</b><span>点击“导入”添加 DLL、文件夹或 ZIP。<br>游戏 Mods 目录中已有的 Mod 会自动显示在这里。</span></div>
            <div v-else-if="!visible.length" class="garden-empty"><b>没有找到匹配的 Mod</b><span v-if="state.query">没有名称或版本包含“{{ state.query }}”的 Mod。</span><button class="garden-link" @click="state.query = ''; setFilter('all')">清除搜索和筛选</button></div>
            <section v-for="group in groups" :key="group.key" class="garden-bed-group" :aria-label="group.label">
              <h2 class="garden-bed-label"><component :is="group.icon" :size="15" aria-hidden="true"/>{{ group.label }}<span>{{ group.total }}</span></h2>
              <div class="garden-plants" :class="{ list: view === 'list' }">
                <article v-for="(item, index) in group.items" :key="item.id" class="plant" :class="{ off: !isEnabled(item), pend: pending(item), care: needsCare(item), open: openId === item.id, arrive: arrivedId === item.id }"
                  data-testid="workspace-mod-row" :data-mod-id="item.id" :data-state="item.state" tabindex="0" :aria-expanded="openId === item.id" :aria-label="item.display_name" :style="{ '--i': Math.min(index, 14) }"
                  @click="open(item, $event)" @keydown="cardKey($event, item)">
                  <div class="plant-top" :class="{ 'collapse-area': openId === item.id }" :title="openId === item.id ? '点击空白处收起详情' : undefined" @click.self.stop="openId === item.id ? close() : open(item)">
                    <span class="garden-pot" aria-hidden="true"><Package :size="20"/></span>
                    <span class="plant-sprout" aria-hidden="true"><svg viewBox="0 0 30 34" width="30" height="34" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 33V17"/><path d="M15 19C15 12 10 8 3 8c0 7 5 11 12 11z" fill="#bbf7d0"/><path d="M15 15c0-7 5-11 12-11 0 7-5 11-12 11z" fill="#86efac"/></svg></span>
                    <button type="button" class="leaf-switch" :aria-pressed="isEnabled(item)" :aria-label="`${isEnabled(item) ? '停用' : '启用'} ${item.display_name}`" :title="item.manageable === false ? '此项无法由 PSMM 启用或停用' : ''" :disabled="!canMove(item)" @click.stop="toggle(item, $event.currentTarget)"><span></span></button>
                    <button v-if="openId === item.id" type="button" class="garden-btn soft icon sm plant-close" aria-label="收起详情" @click.stop="close"><X :size="16"/></button>
                  </div>
                  <strong class="plant-name">{{ item.display_name }}</strong>
                  <span class="plant-sub">{{ item.version_raw ? `v${item.version_raw}` : '本地 Mod' }} · {{ item.file_count }} 个文件</span>
                  <div class="plant-tags"><span :class="['tg', tagClass(item)]"><Check v-if="tagClass(item) === 'on'" :size="12" aria-hidden="true"/>{{ stateLabel(item) }}</span><span v-if="item.message || item.manageable === false" class="tg bad"><TriangleAlert :size="12" aria-hidden="true"/>需处理</span></div>
                  <div v-if="openId === item.id" class="plant-open" data-testid="mod-detail" @click.stop>
                    <div>
                      <div v-if="item.message" class="garden-care-note"><b>需处理</b>{{ item.message }}</div>
                      <h3>基本信息</h3>
                      <dl class="garden-facts"><dt>状态</dt><dd>{{ workspaceStateText(item.state) }}</dd><dt>来源</dt><dd>{{ sourceText(item) }}</dd><dt>文件</dt><dd>{{ item.file_count }} 个</dd><template v-if="item.version_raw"><dt>版本</dt><dd>v{{ item.version_raw }}</dd></template></dl>
                      <div v-if="confirmId === item.id" class="garden-confirm" role="alert"><TriangleAlert :size="16" aria-hidden="true"/><span>将从游戏目录移除它的 {{ item.file_count }} 个文件。包库中的副本会保留，之后可以重新启用。</span><button class="garden-btn soft sm" @click="confirmId = null">取消</button><button class="garden-btn clay-fill sm" data-testid="workspace-uninstall-confirm" :disabled="locked" @click="uninstall(item)">确认卸载</button></div>
                      <div v-else class="plant-actions">
                        <button v-if="item.release_id && ['enabled', 'disabled'].includes(item.state)" class="garden-btn clay sm" data-testid="workspace-uninstall" :disabled="locked || state.dirty" :title="state.dirty ? '先保存或撤销当前更改' : '移除安装文件，保留包库副本'" @click="confirmId = item.id"><Trash2 :size="14"/>卸载</button>
                        <button class="garden-link" @click="emit('logs')"><FileText :size="14"/>查看日志</button>
                      </div>
                    </div>
                    <div class="plant-files">
                      <h3>包含文件<small>{{ state.detail?.total ?? item.file_count }} 个</small></h3>
                      <p v-if="state.detailBusy" class="garden-muted" role="status">正在读取文件列表…</p>
                      <div v-else class="garden-tree"><ModFileTree :nodes="tree"/></div>
                    </div>
                  </div>
                </article>
              </div>
            </section>
          </div>
          <nav v-if="visible.length" class="garden-pager" aria-label="Mod 分页" data-testid="mod-pager">
            <span class="pager-range">{{ rangeText }}</span>
            <div class="pager-pages">
              <button type="button" class="pager-step" :disabled="page <= 1" aria-label="上一页" title="上一页（PageUp）" @click="goPage(page - 1)"><ChevronLeft :size="16"/></button>
              <template v-for="(number, index) in pageNumbers" :key="`${number}-${index}`">
                <span v-if="number === '…'" class="pager-gap" aria-hidden="true">…</span>
                <button v-else type="button" :class="{ on: number === page }" :aria-current="number === page ? 'page' : undefined" :aria-label="`第 ${number} 页`" @click="goPage(number)">{{ number }}</button>
              </template>
              <button type="button" class="pager-step" :disabled="page >= pageCount" aria-label="下一页" title="下一页（PageDown）" @click="goPage(page + 1)"><ChevronRight :size="16"/></button>
            </div>
            <div class="garden-select"><span>每页</span><SelectMenu :model-value="String(pageSize)" aria-label="每页数量" :options="PAGE_SIZES.map(size => ({ value: String(size), label: String(size) }))" @update:model-value="pageSize = Number($event)"/></div>
          </nav>
        </div>

        <aside class="garden-basket" :class="{ show: showBasket }" data-testid="workspace-basket" aria-label="待保存的更改" :aria-hidden="!showBasket">
          <div class="basket-card" :class="{ bounce: bouncing }" @animationend="bouncing = false">
            <template v-if="state.busy">
              <div class="basket-head"><span class="basket-icon"><Sun :size="20" class="garden-spin" aria-hidden="true"/></span><div><b>正在保存…</b><small>{{ stageText }}</small></div></div>
              <div class="garden-solar" role="progressbar" :aria-valuenow="progressPercent ?? undefined" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: progressPercent == null ? '35%' : `${progressPercent}%` }" :class="{ indeterminate: progressPercent == null }"></i></div>
              <p class="basket-count" role="status"><template v-if="progress?.total != null">{{ progress.completed }} / {{ progress.total }}</template></p>
              <button class="garden-btn soft wide" :disabled="state.task?.state === 'cancelling'" @click="runtime.cancel">取消</button>
            </template>
            <template v-else>
              <div class="basket-head"><span ref="basketIcon" class="basket-icon"><Package :size="20" aria-hidden="true"/></span><div><b>待保存 {{ changes.length }} 项</b><small>保存后才会改动游戏文件</small></div></div>
              <ul class="basket-list"><li v-for="item in changes" :key="item.id"><span :class="['v', isEnabled(item) ? 'on' : 'off']">{{ isEnabled(item) ? '启用' : '停用' }}</span><span>{{ item.display_name }}</span></li></ul>
              <div class="basket-actions">
                <button class="garden-btn soft" :disabled="locked || !state.dirty" @click="runtime.discard"><Undo2 :size="15"/>撤销</button>
                <button class="garden-btn" data-testid="workspace-save" :disabled="locked || !state.dirty" @click="runtime.save">保存<kbd>Ctrl S</kbd></button>
                <button class="garden-btn green wide" :disabled="locked || !state.dirty" @click="emit('launch')"><Play :size="15"/>保存并启动</button>
              </div>
            </template>
          </div>
        </aside>
      </div>
    </template>
  </main>
</template>

<style scoped>
.workspace-bulk-actions{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.plant-top.collapse-area{cursor:pointer}
</style>
