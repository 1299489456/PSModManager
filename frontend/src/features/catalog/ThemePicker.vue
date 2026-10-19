<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { Check, ChevronDown, Palette } from 'lucide-vue-next'
import { selectTheme, themeChoices, themeState } from './theme.js'
import { themeMenuIndex } from './themeMenu.js'
const root = ref(null), trigger = ref(null), open = ref(false)
const current = computed(() => themeState.current)
const active = ref(0)
async function focusOption() { await nextTick(); root.value?.querySelectorAll('[role=menuitemradio]')[active.value]?.focus() }
async function show(last = false) {
  open.value = true
  active.value = last ? themeChoices.length - 1 : Math.max(0, themeChoices.findIndex(theme => theme.id === current.value))
  await focusOption()
}
function close(restore = true) { open.value = false; if (restore) void nextTick(() => trigger.value?.focus()) }
async function choose(theme) { if (await selectTheme(theme.id)) close() }
function key(event) {
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault(); active.value = themeMenuIndex(active.value, event.key, themeChoices.length); void focusOption()
  } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close() }
  else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(themeChoices[active.value]) }
  else if (event.key === 'Tab') {
    event.preventDefault()
    const menu = root.value?.querySelector('[role=menu]')
    const controls = [...document.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]')].filter(element => !menu?.contains(element) && element.getClientRects().length)
    const index = controls.indexOf(trigger.value)
    const target = controls[index + (event.shiftKey ? -1 : 1)] || trigger.value
    close(false); void nextTick(() => target?.focus())
  }
}
function outside(event) { if (open.value && !root.value?.contains(event.target)) close(false) }
onMounted(() => document.addEventListener('pointerdown', outside, true))
onUnmounted(() => document.removeEventListener('pointerdown', outside, true))
</script>
<template>
  <div ref="root" class="theme-popover-root">
    <button ref="trigger" class="theme-trigger" aria-label="界面主题" aria-haspopup="menu" :aria-expanded="open" aria-controls="psmm-theme-menu" data-testid="theme-toggle" @click="open ? close() : show()" @keydown.down.prevent="show()" @keydown.up.prevent="show(true)" @keydown.esc.prevent="close()"><Palette :size="18"/><span>{{ themeChoices.find(theme => theme.id === current)?.name }}</span><ChevronDown :size="12"/></button>
    <div v-if="open" id="psmm-theme-menu" class="theme-menu" role="menu" aria-label="选择界面主题" data-testid="theme-popup" @keydown="key">
      <div class="theme-menu-title">界面主题</div>
      <button v-for="(theme,index) in themeChoices" :key="theme.id" role="menuitemradio" :aria-checked="current === theme.id" :disabled="themeState.saving" :tabindex="active === index ? 0 : -1" :data-testid="`theme-option-${theme.id}`" @focus="active=index" @click="choose(theme)">
        <span class="theme-swatch" :style="{background:theme.tokens.bg.deep,borderColor:theme.tokens.text.dim}"><i :style="{background:theme.tokens.accent.primary}"></i></span><span>{{ theme.name }}</span><Check v-if="current === theme.id" :size="15"/><span v-else class="theme-check-space"></span>
      </button>
      <small v-if="themeState.error" role="alert" :style="{color:'var(--accent-warn)'}">{{ themeState.error }}</small>
      <small v-else>{{ themeState.saving ? '正在保存主题…' : '↑ ↓ 选择 · Enter 确认 · Esc 关闭' }}</small>
    </div>
  </div>
</template>
