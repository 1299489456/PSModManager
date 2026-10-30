<script setup>
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import Icon from './GardenIcon.vue'
defineProps({ title: String, subtitle: String, wide: Boolean, busy: Boolean })
const emit = defineEmits(['close'])
const dialog = ref(null)
let previous
onMounted(async () => { previous = document.activeElement; await nextTick(); dialog.value.showModal() })
onUnmounted(() => previous?.isConnected && previous.focus({ preventScroll: true }))
function backdrop(e) { if (e.target === dialog.value) { const r = dialog.value.getBoundingClientRect(); if(e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) emit('close') } }
</script>
<template>
  <dialog ref="dialog" class="sheet native-sheet" :class="{ wide }" :aria-label="title" @cancel.prevent="!busy && emit('close')" @click="!busy && backdrop($event)">
    <div class="sh-head"><div><h2>{{ title }}</h2><p v-if="subtitle">{{ subtitle }}</p></div><button class="x" :disabled="busy" aria-label="关闭" @click="emit('close')"><Icon name="x" size="sm"/></button></div>
    <div class="sh-body"><slot/></div>
    <div v-if="$slots.footer" class="sh-foot"><slot name="footer"/></div>
  </dialog>
</template>
