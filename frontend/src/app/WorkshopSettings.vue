<script setup>
import { ref,watch } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
const props=defineProps({bridge:Object,active:Boolean})
const value=ref(null),busy=ref(false),error=ref('')
async function load(){busy.value=true;try{value.value=await props.bridge.request('app.workshop.settings.get');error.value=''}catch(e){error.value=e.message}finally{busy.value=false}}
async function save(auto_update){busy.value=true;try{value.value=await props.bridge.request('app.workshop.settings.set',{auto_update});error.value=''}catch(e){error.value=e.message}finally{busy.value=false}}
watch(()=>props.active,v=>{if(v&&!busy.value)void load()},{immediate:true})
</script>
<template><article class="garden-card workshop-settings" data-testid="workshop-settings"><h2><RefreshCw :size="18"/>模组更新</h2><label><span><b>管理器自动更新模组</b><small>仅控制管理器发起的更新；Steam 仍可能自行更新订阅模组。</small></span><input data-testid="workshop-auto-update" type="checkbox" :checked="value?.auto_update===true" :disabled="!value||busy" @change="save($event.target.checked)"/></label><p>开启后，管理器会在 Steam 会话可用、游戏已退出且允许写入时下载更新。关闭后停止提交后续项；Steam 已接收的下载可能继续。</p><p v-if="value?.paused_reason" class="check-error" role="status">自动更新已暂停：{{ value.paused_reason }}</p><p v-if="error" class="check-error" role="alert">{{ error }} <button class="garden-link" @click="load">重试</button></p></article></template>
<style scoped>.workshop-settings h2{display:flex;gap:8px;align-items:center}.workshop-settings label{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:14px 0}.workshop-settings b{font-size:14px}.workshop-settings small{display:block;font-size:12px;line-height:1.6;color:var(--g-text-2);margin-top:5px}.workshop-settings input{width:20px;height:20px;accent-color:var(--g-green);flex:none}.workshop-settings p{font-size:13px;line-height:1.7;color:var(--g-text-2)}</style>
