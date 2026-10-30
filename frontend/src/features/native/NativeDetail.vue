<script setup>
import { computed } from 'vue'
import Icon from './GardenIcon.vue'
import Growth from './NativeGrowth.vue'
import DetailContent from './NativeDetailContent.vue'
import Cover from './NativeCover.vue'
import { nameOf, sourceText, sizeText, shortDate } from './nativeModel.js'
const props = defineProps({ mod: Object, mods: Array, order: Array, baseline: Array, issues: Array, tab: String, drawer: Boolean, expanded: Boolean, config: Object, busy: Boolean, writeAllowed: Boolean, remoteCover:String, coverLoading:Boolean,bridge:Object })
const emit = defineEmits(['tab', 'toggle', 'close', 'expand', 'action', 'goto', 'fix', 'save-config'])
const mod = computed(() => props.mod)
const enabled = computed(() => props.order.includes(mod.value?.id))
const issues = computed(() => props.issues.filter(i => i.id === mod.value?.id || i.dep === mod.value?.id))
const tabs = computed(() => [['overview', '概览', null], ['perm', '权限', mod.value?.capabilities?.length], ['deps', '依赖', issues.value.length || mod.value?.prerequisites?.length], ['config', '配置', props.config?.entries?.length], ['files', '文件', null], ['log', '运行记录', null]])
</script>
<template>
  <section v-if="!mod" class="bed detail" :class="{ open: drawer }"><div class="d-scroll" style="display:grid;place-items:center"><div class="garden-empty" style="border:0;background:none"><span class="garden-pot"><Icon name="sprout"/></span><b>选择一个模组</b><span>权限、依赖、配置、文件和运行记录都在这里。</span></div></div></section>
  <component :is="expanded ? 'div' : 'section'" v-else :class="expanded ? 'expanded-detail' : ['bed', 'detail', { open: drawer }]">
    <div :class="expanded ? 'expanded-scroll' : 'd-scroll'">
      <template v-if="!expanded">
        <div class="d-hero"><Cover :bridge="bridge" :workshop-id="mod.workshop_id" :src="mod.preview_url" :fallback="remoteCover" :loading="coverLoading" :title="nameOf(mod)" zoom @open="$emit('action',{kind:'image',url:$event})"/><div class="hero-tools"><button title="放大查看" @click="$emit('expand')"><Icon name="expand" size="sm"/></button><button title="关闭" @click="$emit('close')"><Icon name="x" size="sm"/></button></div></div>
        <div class="d-tags"><span class="tg dark">{{ mod.tags?.[0] || '原生模组' }}</span><span class="tg ghost">v{{ mod.version || '未知' }}</span><span class="tg" :class="mod.source === 'workshop' ? 'sky' : 'off'">{{ sourceText(mod) }}</span></div>
        <div class="d-title">{{ nameOf(mod) }}</div><div class="d-by"><span>{{ mod.author || '未注明作者' }}</span></div>
        <div class="stats"><div class="stat"><small>版本</small><b>{{ mod.version || '未知' }}</b></div><div class="stat"><small>适用游戏</small><b :title="(mod.game_versions || []).join('、')">{{ mod.game_versions?.join('、') || '未声明' }}</b></div><div class="stat"><small>大小</small><b>{{ sizeText(mod.size_bytes) }}</b></div><div class="stat"><small>更新</small><b>{{ shortDate(mod.updated_at) }}</b></div></div>
        <Growth :mod="mod" :order="order" :baseline="baseline"/>
        <div class="d-actions"><span class="on-label"><button class="leaf-switch" :aria-pressed="enabled" :disabled="busy" :aria-label="`${enabled ? '停用' : '启用'} ${nameOf(mod)}`" @click="$emit('toggle', $event)"><span></span></button>{{ enabled ? '已启用' : '未启用' }}</span><span style="flex:1"></span>
          <button class="garden-btn sm icon" title="配置" @click="$emit('tab', 'config')"><Icon name="gear" size="sm"/></button><button class="garden-btn sm icon" title="打开模组文件夹" @click="$emit('action', { kind: 'open', target: 'mod' })"><Icon name="folder" size="sm"/></button><button v-if="mod.workshop_id" class="garden-btn sm icon" title="在 Steam 中打开工坊页面" @click="$emit('action', { kind: 'workshop' })"><Icon name="steam" size="sm"/></button><button class="garden-btn sm icon" title="更多" @click="$emit('action', { kind: 'more', event: $event })"><Icon name="more" size="sm"/></button>
        </div>
        <div class="d-tabs garden-chips sm" role="tablist" aria-label="模组详情"><button v-for="[key,label,count] in tabs" :key="key" role="tab" :aria-selected="tab === key" :class="{ on: tab === key }" @click="$emit('tab', key)">{{ label }}<span v-if="count" class="b" :class="{ bad: key === 'deps' && issues.some(x => x.level === 'block') }">{{ count }}</span></button></div>
      </template>
      <div v-if="expanded" class="expanded-cover"><Cover :bridge="bridge" :workshop-id="mod.workshop_id" :src="mod.preview_url" :fallback="remoteCover" :loading="coverLoading" :title="nameOf(mod)"/></div><div v-if="expanded" class="expanded-grid"><DetailContent expanded segment="primary" :mod="mod" :mods="mods" :order="order" :baseline="baseline" :issues="issues" :tab="tab" :config="config" :busy="busy" :write-allowed="writeAllowed" @tab="$emit('tab',$event)" @action="$emit('action',$event)" @goto="$emit('goto',$event)" @fix="$emit('fix',$event)" @save-config="$emit('save-config',$event)"/><DetailContent expanded segment="secondary" :mod="mod" :mods="mods" :order="order" :baseline="baseline" :issues="issues" :tab="tab" :config="config" :busy="busy" :write-allowed="writeAllowed" @tab="$emit('tab',$event)" @action="$emit('action',$event)" @goto="$emit('goto',$event)" @fix="$emit('fix',$event)" @save-config="$emit('save-config',$event)"/></div>
      <DetailContent v-else :mod="mod" :mods="mods" :order="order" :baseline="baseline" :issues="issues" :tab="tab" :config="config" :busy="busy" :write-allowed="writeAllowed" @tab="$emit('tab',$event)" @action="$emit('action',$event)" @goto="$emit('goto',$event)" @fix="$emit('fix',$event)" @save-config="$emit('save-config',$event)"/>
    </div>
  </component>
</template>
