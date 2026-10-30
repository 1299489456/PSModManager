<script setup>
import { computed } from 'vue'
import Icon from './GardenIcon.vue'
import { approvalLabel, runLabel, sourceText } from './nativeModel.js'
const props = defineProps({ mod: Object, order: Array, baseline: Array })
const stones = computed(() => {
  const en = props.order.includes(props.mod.id), was = props.baseline.includes(props.mod.id), trust = approvalLabel(props.mod), run = runLabel(props.mod, en, was)
  return [
    { label: props.mod.installed === false ? '未安装' : '已安装', detail: sourceText(props.mod), icon: 'basket', tone: props.mod.installed === false ? '' : 'ok' },
    { label: en !== was ? en ? '将启用' : '将停用' : en ? '已启用' : '未启用', detail: en !== was ? '保存后生效' : en ? `第 ${props.order.indexOf(props.mod.id) + 1} 位` : '在左栏', icon: 'sprout', tone: en !== was ? 'wait' : en ? 'ok' : '' },
    { ...trust, icon: 'shield' }, { ...run, icon: 'sun' },
  ]
})
</script>
<template><div class="growth"><div v-for="(stone, i) in stones" :key="i" class="stone" :class="stone.tone"><i><Icon :name="stone.tone === 'ok' && i !== 3 ? 'check' : stone.icon"/></i><b>{{ stone.label }}</b><small :title="stone.detail">{{ stone.detail }}</small></div></div></template>
