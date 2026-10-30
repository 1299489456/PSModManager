<script setup>
import { computed, onUnmounted, reactive, watch } from 'vue'
import { Play, Gamepad2, RefreshCw } from 'lucide-vue-next'
import { createGameLaunchRuntime, gameLaunchStatusText } from './gameLaunchRuntime.js'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null }, disabled: Boolean, beforeLaunch: Function, preparationPhase:String, saveFirst: Boolean, pending: { type: Number, default: 0 } })
const emit = defineEmits(['launch-checked','status','busy'])
const state = reactive({})
const runtime = createGameLaunchRuntime(props.bridge, state)
const statusText = computed(() => gameLaunchStatusText(state))
const running = computed(() => state.snapshot?.state === 'running')
const preparationText=computed(()=>props.preparationPhase==='validating'?'核对启用条件…':'正在保存模组…')
const rays = Array.from({ length: 12 }, (_, index) => { const angle = index * Math.PI / 6; return { x1: 31 + 25 * Math.cos(angle), y1: 31 + 25 * Math.sin(angle), x2: 31 + 30 * Math.cos(angle), y2: 31 + 30 * Math.sin(angle) } })
watch(() => props.installation?.installation_id, id => runtime.setInstallation(id), { immediate: true })
watch(()=>state.snapshot,value=>emit('status',value))
watch(()=>state.busy,value=>emit('busy',value),{immediate:true})
onUnmounted(() => runtime.dispose())
async function launch() {
  if (props.disabled || !runtime.canLaunch()) return false
  const installationId = props.installation?.installation_id
  const started=await runtime.launch(props.beforeLaunch)
  if (installationId === props.installation?.installation_id) emit('launch-checked',{started,error:state.error,errorCode:state.errorCode,snapshot:state.snapshot})
  return started
}
defineExpose({ launch,refresh:runtime.refresh,clearError:runtime.clearError })
</script>

<template>
  <div class="game-launch" :class="{ running }" :aria-busy="state.busy || state.refreshing">
    <button type="button" class="sun-button" data-testid="game-launch" :disabled="disabled || !runtime.canLaunch()" :title="state.installationId ? '保存当前模组选择，然后启动游戏' : '请先选择游戏安装'" @click="launch">
      <span class="sun-disc" aria-hidden="true"><svg class="sun-rays" viewBox="0 0 62 62"><line v-for="(ray, index) in rays" :key="index" :x1="ray.x1" :y1="ray.y1" :x2="ray.x2" :y2="ray.y2"/></svg><Gamepad2 v-if="running" :size="18"/><Play v-else :size="18"/></span>
      <span class="sun-label">{{ state.phase==='preparing' ? preparationText : state.busy ? '正在启动…' : running ? '游戏运行中' : saveFirst ? '保存并启动' : '启动游戏' }}</span>
    </button>
    <div class="sun-meta">
      <span v-if="state.installationId" role="status" class="game-launch-status" data-testid="game-launch-status" :data-state="state.busy ? 'starting' : state.snapshot?.state || 'not_started'">{{ state.busy ? statusText : saveFirst && !running && pending ? `先应用 ${pending} 项更改` : statusText }}</span>
      <button type="button" class="game-launch-refresh" data-testid="game-launch-refresh" :disabled="!state.installationId || state.busy || state.refreshing" aria-label="刷新游戏进程状态" title="刷新游戏进程状态" @click="runtime.refresh"><RefreshCw :size="13"/></button>
    </div>
    <span v-if="state.error" role="alert" class="game-launch-error">{{ state.error }}</span>
  </div>
</template>
