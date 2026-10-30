<script setup>
import { computed } from 'vue'
import Icon from './GardenIcon.vue'
import { cap, capLevel, sourceText, runLabel, usable } from './nativeModel.js'
import { downloadLabel } from './workshopRuntime.js'
const props = defineProps({ mod: Object, enabled: Boolean, baseline: Boolean, selected: Boolean, focused: Boolean, index: Number, issue: Object, simplified: Boolean, arriving: Boolean, download:Object })
defineEmits(['select', 'pick', 'toggle', 'move', 'drag', 'details'])
const run = computed(() => runLabel(props.mod, props.enabled, props.baseline))
const dirty = computed(() => props.enabled !== props.baseline)
const level = computed(() => capLevel(props.mod.capabilities || []))
</script>
<template>
  <div class="row" :class="{ sel: selected, focus: focused, 'off-row': !enabled, arrive: arriving }" :data-id="mod.id" :data-where="enabled ? 'en' : 'avail'" role="option" :aria-selected="selected" :draggable="usable(mod)" @click="$emit('select', $event)" @dblclick="!$event.target.closest('button') && $emit('toggle', $event)" @dragstart="$emit('drag', $event)">
    <button class="pick" :aria-label="`选择 ${mod.name}`" :aria-pressed="selected" @click.stop="$emit('pick', $event)"><Icon v-if="selected" name="check" size="xs"/></button>
    <span v-if="enabled" class="ord">{{ String(index + 1).padStart(2, '0') }}</span>
    <span v-if="!simplified" class="garden-pot">{{ (mod.name || mod.id).slice(0, 2) }}</span>
    <div class="meta"><span class="nm" :title="mod.name">{{ mod.name || mod.id }}</span><span class="sub">{{ sourceText(mod) }} · <template v-if="!simplified">{{ mod.author || '未注明作者' }} · </template>v{{ mod.version || '未知' }}<template v-if="enabled && mod.prerequisites?.length"> · 依赖 {{ mod.prerequisites.length }}</template></span><span v-if="!simplified&&mod.source==='workshop'" class="download-status" :class="downloadLabel(download).tone">{{ downloadLabel(download).text }}</span></div>
    <template v-if="!simplified">
      <span class="tags" :class="{ hideable: enabled }">
        <span v-if="issue" class="tg" :class="issue.level === 'block' ? 'bad' : 'pend'" :title="issue.message">{{ issue.level === 'block' ? '需处理' : '顺序' }}</span>
        <span v-if="dirty" class="tg pend">待保存</span>
        <span v-else-if="enabled" class="tg" :class="run.tone === 'ok' ? 'on' : run.tone === 'bad' ? 'bad' : 'off'" :title="run.detail">{{ run.key === 'running' ? '已加载' : run.label }}</span>
        <span v-else-if="mod.sources?.length > 1" class="tg pend">{{ mod.sources.length }} 个来源</span>
        <span v-if="level !== 'none'" class="perm-dot" :class="level" :title="`声明 ${mod.capabilities.length} 项权限：${mod.capabilities.map(c => cap(c).label).join('、')}`"><Icon name="shield" size="xs"/>{{ mod.capabilities.length }}</span>
      </span>
      <span v-if="enabled" class="moves"><button title="上移" @click.stop="$emit('move', 'up')"><Icon name="up" size="xs"/></button><button title="下移" @click.stop="$emit('move', 'down')"><Icon name="down" size="xs"/></button></span>
    </template>
    <button v-if="simplified" class="garden-btn soft icon xs" title="详情" @click.stop="$emit('details')"><Icon name="info" size="sm"/></button>
    <button class="leaf-switch" :aria-pressed="enabled" :aria-label="`${enabled ? '停用' : '启用'} ${mod.name}`" :disabled="!enabled && !usable(mod)" :title="enabled ? '停用（不删文件、不取消订阅）' : '启用'" @click.stop="$emit('toggle', $event)"><span></span></button>
  </div>
</template>
