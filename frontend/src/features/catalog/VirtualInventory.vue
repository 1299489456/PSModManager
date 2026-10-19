<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { FileCode2, Package, Search, ListFilter } from 'lucide-vue-next'
import { rowHeight, virtualWindow } from './controller.js'
const props = defineProps({ rows: Array, selectedIds: Array, focusedId: String, title: String, candidate: Boolean, query: String })
const emit = defineEmits(['select', 'query', 'all'])
const viewport = ref(null), scrollTop = ref(0), height = ref(600)
const context = ref(null), menu = ref(null)
function showMenu(event, row) {
  emit('select', row.entry_id, {})
  const bounds = viewport.value.getBoundingClientRect()
  context.value = { row, x: Math.max(8,Math.min(event.clientX || bounds.left+20,window.innerWidth-220)), y:Math.max(8,Math.min(event.clientY || bounds.top+20,window.innerHeight-120)) }
}
function dismiss(event) { if (!menu.value?.contains(event.target)) context.value = null }
function closeMenu() { context.value=null; viewport.value?.focus() }
function menuKey(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeMenu() }
  if (event.key === 'Tab') context.value=null
  if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
    event.preventDefault(); const buttons=[...menu.value.querySelectorAll('button')]; const index=buttons.indexOf(document.activeElement)
    buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus()
  }
}
watch(context, value => { if (value) requestAnimationFrame(() => menu.value?.querySelector('button')?.focus()) })
let observer
const visible = computed(() => virtualWindow(props.rows, scrollTop.value, height.value))
watch(() => props.query, () => { if (viewport.value) viewport.value.scrollTop = 0; scrollTop.value = 0 })
function key(event) {
  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) { const row=props.rows.find(item=>item.entry_id===props.focusedId); if (row) { event.preventDefault(); showMenu(event,row) }; return }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') { event.preventDefault(); emit('all'); return }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End', ' ', 'Enter'].includes(event.key) || !props.rows.length) return
  event.preventDefault()
  let index = props.rows.findIndex(row => row.entry_id === props.focusedId)
  if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = props.rows.length - 1
  else if (event.key === 'ArrowDown') index = Math.min(props.rows.length - 1, index + 1)
  else if (event.key === 'ArrowUp') index = Math.max(0, index - 1)
  else index = Math.max(0, index)
  emit('select', props.rows[index].entry_id, event)
  const top = index * rowHeight
  if (top < viewport.value.scrollTop) viewport.value.scrollTop = top
  else if (top + rowHeight > viewport.value.scrollTop + height.value) viewport.value.scrollTop = top + rowHeight - height.value
}
onMounted(() => { observer = new ResizeObserver(entries => { height.value = entries[0].contentRect.height }); observer.observe(viewport.value); document.addEventListener('pointerdown',dismiss) })
onUnmounted(() => { observer?.disconnect(); document.removeEventListener('pointerdown',dismiss) })
const fileName = row => row.relative_path.split(/[\\/]/).pop()
</script>
<template>
  <section :class="['rim-panel inventory-column', { 'candidate-column': candidate }]">
    <header class="column-heading"><span class="heading-marker"></span><strong>{{ title }}</strong><span class="count-pill">{{ rows.length }}</span><ListFilter :size="14" class="dim" /></header>
    <div class="list-search" v-if="!candidate"><Search :size="14"/><input data-testid="inventory-search" aria-label="搜索库存文件与程序集" :value="query" placeholder="搜索名称、程序集…" @input="emit('query', $event.target.value)" @keydown.esc="emit('query', '')"/><kbd>Ctrl F</kbd></div>
    <div v-else class="column-scope"><span class="status-dot"></span>预计加载 · 尚未经游戏运行验证</div>
    <div ref="viewport" :data-testid="candidate ? 'candidate-list' : 'inventory-list'" class="virtual-list" role="listbox" aria-multiselectable="true" :aria-label="title" tabindex="0" @keydown="key" @scroll="scrollTop = $event.target.scrollTop">
      <div :style="{height: visible.totalHeight + 'px', position: 'relative'}">
        <div :style="{ transform: `translateY(${visible.offset}px)` }">
          <div v-for="row in visible.rows" :key="row.entry_id" :data-testid="candidate ? 'candidate-row' : 'inventory-row'" :data-entry-id="row.entry_id" role="option" :aria-selected="selectedIds.includes(row.entry_id)" :class="['inventory-row', { selected: selectedIds.includes(row.entry_id), focused: focusedId === row.entry_id }]" :style="{height: rowHeight + 'px'}" @click="emit('select', row.entry_id, $event)" @contextmenu.prevent="showMenu($event,row)">
            <span class="selection-check" aria-hidden="true">{{ selectedIds.includes(row.entry_id) ? '✓' : '' }}</span>
            <Package v-if="!row.assembly_name" :size="17" class="row-icon"/><FileCode2 v-else :size="17" class="row-icon"/>
            <div class="row-label"><strong :title="fileName(row)">{{ fileName(row) }}</strong><span>{{ row.assembly_name || '身份未知' }}<template v-if="row.assembly_version"> · {{ row.assembly_version }}</template></span></div>
            <span v-if="row.presence === 'missing'" class="row-status warning">缺失</span><span v-else-if="row.prediction?.reasons?.includes('assembly_version_shadowed')" class="row-status warning" title="同名程序集有更高版本，预计优先加载高版本">有更高版本</span><span v-else-if="!row.assembly_name" class="row-status dim">未知</span>
          </div>
        </div>
      </div>
      <div v-if="!rows.length" class="column-empty"><Package :size="30"/><p>{{ candidate ? '暂无预计加载的模组' : '没有找到匹配文件' }}</p><small>{{ candidate ? '扫描后显示预计加载的模组' : '选择安装并扫描，或调整搜索条件' }}</small></div>
    </div>
    <footer class="list-footer"><span>{{ candidate ? '加载结果以游戏日志为准' : 'Ctrl / Shift 多选 · ↑ ↓ 导航' }}</span><span>{{ selectedIds.length }} 项选中</span></footer>
    <div v-if="context" ref="menu" role="menu" aria-label="文件操作" class="inventory-menu" :style="{left:context.x+'px',top:context.y+'px'}" @keydown="menuKey"><button role="menuitem" @click="emit('select',context.row.entry_id,{});closeMenu()">查看文件详情</button><button v-if="!candidate" role="menuitem" @click="emit('query',fileName(context.row));closeMenu()">筛选此文件</button><button v-if="!candidate && query" role="menuitem" @click="emit('query','');closeMenu()">清除筛选</button></div>
  </section>
</template>
<style scoped>
.inventory-menu{position:fixed;z-index:60;min-width:200px;padding:5px;background:var(--bg-surface);border:1px solid rgb(var(--rgb-border-base)/.25);border-radius:8px;box-shadow:0 8px 25px #0005}.inventory-menu button{width:100%;display:flex;justify-content:flex-start;min-height:36px;font-size:14px;border:0;background:transparent}
</style>
