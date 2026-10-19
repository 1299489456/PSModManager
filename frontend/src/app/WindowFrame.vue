<script setup>
// Garden title bar for the frameless window. Gestures are handed to Windows
// through the closed `window.control` command; outside the desktop it is inert.
import { onMounted, onUnmounted, ref } from 'vue'
import { Minus, Square, Copy, X } from 'lucide-vue-next'
import appIcon from '../shared/branding/psmm-icon-64.png?inline'

const props = defineProps({ bridge: { type: Object, required: true } })
const maximized = ref(false)
const edges = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
let syncTimer = 0, lastPress = { time: 0, x: 0, y: 0 }

async function control(op) {
  try {
    const result = await props.bridge.request('window.control', { op })
    if (typeof result?.maximized === 'boolean') maximized.value = result.maximized
  } catch { /* not running inside the desktop window */ }
}
// The first press enters Windows' move loop, which swallows the click, so a
// double-click is recognised here from two presses close in time and place.
function drag(event) {
  if (event.button !== 0 || event.target.closest('button')) return
  event.preventDefault()
  const now = performance.now(), again = now - lastPress.time < 400 && Math.abs(event.screenX - lastPress.x) < 6 && Math.abs(event.screenY - lastPress.y) < 6
  lastPress = again ? { time: 0, x: 0, y: 0 } : { time: now, x: event.screenX, y: event.screenY }
  void control(again ? 'toggle_maximize' : 'drag')
}
function resize(edge, event) {
  if (event.button !== 0 || maximized.value) return
  event.preventDefault()
  void control(`resize_${edge}`)
}
const sync = () => { clearTimeout(syncTimer); syncTimer = setTimeout(() => void control('state'), 120) }
onMounted(() => { window.addEventListener('resize', sync); if (window.pywebview?.api) void control('state'); else window.addEventListener('pywebviewready', sync, { once: true }) })
onUnmounted(() => { clearTimeout(syncTimer); window.removeEventListener('resize', sync) })
</script>

<template>
  <header class="garden-titlebar" :class="{ maximized }" data-testid="window-titlebar" @mousedown="drag">
    <img class="titlebar-logo" :src="appIcon" alt="" aria-hidden="true" draggable="false"/>
    <span class="titlebar-name">PSMM</span>
    <span class="titlebar-space"></span>
    <div class="titlebar-controls">
      <button type="button" class="titlebar-button" data-testid="window-minimize" aria-label="最小化" title="最小化" @click="control('minimize')"><Minus :size="16"/></button>
      <button type="button" class="titlebar-button" data-testid="window-maximize" :aria-label="maximized ? '还原' : '最大化'" :title="maximized ? '还原' : '最大化'" @click="control('toggle_maximize')"><Copy v-if="maximized" :size="13" class="restore-icon"/><Square v-else :size="13"/></button>
      <button type="button" class="titlebar-button close" data-testid="window-close" aria-label="关闭" title="关闭" @click="control('close')"><X :size="17"/></button>
    </div>
  </header>
  <template v-if="!maximized"><span v-for="edge in edges" :key="edge" :class="['window-edge', edge]" aria-hidden="true" @mousedown="resize(edge, $event)"></span></template>
</template>
