<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, useAttrs, useId, watch } from 'vue'
import { Check, ChevronDown } from 'lucide-vue-next'
defineOptions({ inheritAttrs: false })
const props = defineProps({ modelValue: { type: String, default: '' }, options: { type: Array, default: () => [] }, disabled: Boolean, placeholder: { type: String, default: '请选择' } })
const emit = defineEmits(['update:modelValue'])
const attrs = useAttrs(), root = ref(null), trigger = ref(null), menu = ref(null), open = ref(false), active = ref(0), position = ref({})
const menuId = useId()
const label = computed(() => props.options.find(option => option.value === props.modelValue)?.label || props.placeholder)
function close(focus = false) { open.value = false; if (focus) nextTick(() => trigger.value?.focus()) }
function focusOption() { nextTick(() => {
  const option = menu.value?.querySelectorAll('[role=option]')[active.value]
  if (!option) return
  option.focus({ preventScroll: true })
  if (option.offsetTop < menu.value.scrollTop) menu.value.scrollTop = option.offsetTop
  else if (option.offsetTop + option.offsetHeight > menu.value.scrollTop + menu.value.clientHeight) menu.value.scrollTop = option.offsetTop + option.offsetHeight - menu.value.clientHeight
}) }
function placeMenu() {
  if (!trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const height = Math.min(props.options.length * 38 + 12, 280, window.innerHeight - 24)
  const width = Math.min(Math.max(rect.width, 180), window.innerWidth - 24)
  position.value = { left: Math.max(12, Math.min(rect.left, window.innerWidth-width-12))+'px', width:width+'px', maxHeight:height+'px', top: (rect.bottom + height + 8 < window.innerHeight ? rect.bottom+4 : Math.max(12,rect.top-height-4))+'px' }
}
// A blurred or transformed ancestor becomes the containing block of a fixed
// element; measure where the menu landed and shift it onto the trigger.
function settle() { nextTick(() => {
  const box = menu.value?.getBoundingClientRect(), want = position.value
  if (!box || !want.left) return
  const dx = box.left - parseFloat(want.left), dy = box.top - parseFloat(want.top)
  if (Math.abs(dx) > .5 || Math.abs(dy) > .5) position.value = { ...want, left: `${parseFloat(want.left) - dx}px`, top: `${parseFloat(want.top) - dy}px` }
}) }
async function show(index = null) {
  if (props.disabled || !props.options.length) return
  placeMenu()
  active.value = index ?? Math.max(0, props.options.findIndex(option => option.value === props.modelValue))
  open.value = true; focusOption(); settle()
}
function choose(option) { emit('update:modelValue', option.value); close(true) }
function key(event) {
  if (event.key === 'Escape') { if (open.value) { event.preventDefault(); event.stopPropagation(); close(true) }; return }
  if (event.key === 'Tab') { if (open.value) trigger.value?.focus(); close(); return }
  if (!['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(event.key)) return
  event.preventDefault()
  if (!open.value) { show(event.key === 'End' ? props.options.length-1 : null); return }
  if (event.key === 'Enter' || event.key === ' ') { if (props.options[active.value]) choose(props.options[active.value]); return }
  active.value = event.key === 'Home' ? 0 : event.key === 'End' ? props.options.length-1 : (active.value+(event.key === 'ArrowDown' ? 1 : -1)+props.options.length)%props.options.length
  focusOption()
}
function outside(event) { if (!root.value?.contains(event.target)) close() }
function reposition(event) { if (open.value && !menu.value?.contains(event.target)) { placeMenu(); settle() } }
watch(() => props.disabled, value => { if (value) close() })
onMounted(() => { document.addEventListener('pointerdown', outside); window.addEventListener('resize', reposition); window.addEventListener('scroll',reposition,true) })
onUnmounted(() => { document.removeEventListener('pointerdown',outside); window.removeEventListener('resize',reposition); window.removeEventListener('scroll',reposition,true) })
</script>
<template>
  <span ref="root" class="select-menu" @keydown="key">
    <button ref="trigger" v-bind="attrs" class="select-trigger" type="button" :disabled="disabled" :data-value="modelValue" :aria-controls="menuId" aria-haspopup="listbox" :aria-expanded="open" @click="open ? close() : show()"><span>{{ label }}</span><ChevronDown :size="15" aria-hidden="true"/></button>
    <div v-if="open" :id="menuId" ref="menu" role="listbox" :aria-label="attrs['aria-label'] || '选项'" class="select-options" :style="position">
      <button v-for="(option,index) in options" :key="option.value" type="button" role="option" :data-value="option.value" :aria-selected="option.value === modelValue" :tabindex="index === active ? 0 : -1" @focus="active=index" @click="choose(option)"><span>{{ option.label }}</span><Check v-if="option.value===modelValue" :size="15" aria-hidden="true"/></button>
    </div>
  </span>
</template>
<style scoped>
.select-menu{display:inline-flex;min-width:0}.select-trigger{width:100%;justify-content:space-between;min-height:34px;gap:12px}.select-trigger>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.select-options{position:fixed;z-index:1000;overflow:auto;margin:0;padding:5px;border-radius:8px;border:1px solid rgb(var(--rgb-border-base)/.25);background:var(--bg-surface);box-shadow:0 8px 26px #0005;color:var(--text-main)}.select-options>button{display:flex;width:100%;min-height:36px;justify-content:space-between;text-align:left;white-space:normal;border:0;background:transparent;padding:7px 10px;border-radius:5px;font-size:14px}.select-options>button[aria-selected=true]{background:rgb(var(--rgb-accent-primary)/.14)}.select-options>button:focus-visible{outline:2px solid var(--accent-primary);outline-offset:-2px}.select-options>button:hover{background:rgb(var(--rgb-bg-overlay)/.1)}
</style>
