<script setup>
import { computed } from 'vue'
import Icon from './GardenIcon.vue'
import { itemOperationLabel } from './workshopRuntime.js'
const props=defineProps({state:Object,mods:{type:Array,default:()=>[]},showError:{type:Boolean,default:true}})
const operations=computed(()=>props.state?.status?.operations||[])
const attention=computed(()=>operations.value.filter(x=>['partial','failed','uncertain'].includes(x.state)).length)
const running=computed(()=>operations.value.filter(x=>['queued','running'].includes(x.state)).length)
const name=id=>props.mods.find(m=>m.workshop_id===id)?.name||props.state?.status?.items?.find(m=>m.workshop_id===id)?.title||id
</script>
<template>
  <div v-if="showError&&state?.error" class="care-note" role="alert">{{ state.error }}</div>
  <div v-if="state?.remainingCount" class="garden-notice" role="status">本批完成后继续更新剩余 {{ state.remainingCount }} 个模组；启用草稿会保留。</div>
  <div v-if="state?.queueNotice" class="care-note" role="status">{{ state.queueNotice }}</div>
  <details v-if="operations.length" class="garden-card workshop-activity" :open="attention>0"><summary><Icon name="download" size="sm"/>工坊操作记录 <span>{{ operations.length }}</span><small v-if="running"> · {{ running }} 项处理中</small><small v-if="attention" class="warn"> · {{ attention }} 项需核对</small></summary><div v-for="op in operations" :key="op.operation_id" class="workshop-operation"><b>{{ {subscribe:'订阅',unsubscribe:'取消订阅',update:'更新'}[op.action] }} · {{ {queued:'已提交',running:'处理中',completed:'已完成',partial:'部分失败',failed:'失败',uncertain:'结果待核对'}[op.state] || op.state }}</b><div v-for="item in op.items" :key="item.workshop_id" class="download-line"><span>{{ name(item.workshop_id) }}</span><span class="tg" :class="item.state==='failed'?'bad':item.state==='completed'?'on':'pend'">{{ itemOperationLabel(item,op.action) }}</span><span v-if="op.action==='update'&&typeof item.progress==='number'">{{ Math.round(item.progress*100) }}%</span><small v-if="item.error" class="operation-error">{{ item.error.message }}</small></div></div></details>
</template>
