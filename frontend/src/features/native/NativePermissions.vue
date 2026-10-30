<script setup>
import { computed } from 'vue'
import Icon from './GardenIcon.vue'
import { cap, capLevel, approvalLabel, runLabel, dateText } from './nativeModel.js'
const props = defineProps({ mod: Object, full: Boolean, order: Array, baseline: Array })
defineEmits(['details'])
const caps = computed(() => props.mod.capabilities || [])
const level = computed(() => capLevel(caps.value))
const approval = computed(() => approvalLabel(props.mod))
const scan = computed(() => props.mod.approval?.scan_status || 'unknown')
const scanKnown = computed(() => Array.isArray(props.mod.approval?.detected) && scan.value !== 'unknown')
const scanSource=computed(()=>props.mod.approval?.approval_source==='manager'?'管理器历史检测记录':props.mod.approval?.approval_source==='game_or_external'?'游戏或外部历史记录':'历史检测记录')
const run = computed(() => runLabel(props.mod, props.order.includes(props.mod.id), props.baseline.includes(props.mod.id)))
const rows = computed(() => [...new Set([...caps.value, ...(props.mod.approval?.detected || [])])])
</script>
<template>
  <div class="sec" :class="{ o2: !full }">
    <h3 v-if="!full">权限与信任<span class="sp"></span><button class="garden-link" @click="$emit('details')">查看详情</button></h3>
    <div class="perm-head" :class="level"><span class="garden-pot"><Icon :name="caps.length ? 'shield-alert' : 'shield-check'"/></span><div><h4>{{ caps.length ? `${level === 'high' ? '高权限' : '中等权限'} · ${caps.length} 项` : '没有声明额外权限' }}</h4><p>{{ approval.label }}<template v-if="mod.approval?.approved_at"> · {{ dateText(mod.approval.approved_at) }}</template><template v-else> · 不以权限数量推断批准结果</template></p></div></div>
    <div v-if="!full && caps.length" class="perm-chips"><span v-for="c in caps" :key="c" class="perm-chip" :class="cap(c).level" :title="cap(c).desc"><Icon :name="cap(c).icon" size="xs"/>{{ cap(c).label }}</span></div>
    <template v-if="full">
      <div v-if="rows.length" class="caps"><div v-for="c in rows" :key="c" class="cap" :class="[cap(c).level, { added: !caps.includes(c) }]"><span class="garden-pot"><Icon :name="cap(c).icon" size="sm"/></span><div><b>{{ cap(c).label }} <code>{{ c }}</code></b><p>{{ cap(c).desc }}</p></div><span class="tg" :class="!caps.includes(c) ? 'bad' : 'off'">{{ caps.includes(c) ? '已声明' : '未声明' }}{{ scanKnown && mod.approval.detected.includes(c) ? ' · 历史扫描' : '' }}</span></div></div>
      <div class="trust">
        <div class="ok"><small><Icon name="file-text" size="xs"/>清单声明</small><b>{{ caps.length }} 项</b><span>manifest.xml</span></div>
        <div><small><Icon name="search" size="xs"/>权限检查记录</small><b>{{ scanKnown ? scanSource : '尚未检查' }}</b><span>可在模组检查中手动分析当前内容</span></div>
        <div :class="approval.tone"><small><Icon name="shield" size="xs"/>本机批准</small><b>{{ approval.label }}</b><span>{{ approval.detail }}</span></div>
        <div :class="run.tone"><small><Icon name="sun" size="xs"/>最近一次启动</small><b>{{ run.label }}</b><span>{{ run.detail }}</span></div>
      </div>
    </template>
  </div>
  <div v-if="full" class="mint-note">启用只加入草稿，权限在这里查看。保存会记录你对当前内容的启用同意，并写入游戏兼容的批准记录，无需进入游戏重复批准。这不是安全检查通过；异常检查需在“模组检查”中手动运行。<br>这些权限不是系统沙箱：模组与游戏在同一个进程运行。创意工坊来源或未声明权限都不代表安全审计通过。</div>
</template>
