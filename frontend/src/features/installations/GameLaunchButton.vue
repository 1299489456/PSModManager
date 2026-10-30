<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { Play, Gamepad2, RefreshCw } from 'lucide-vue-next'
import { createGameLaunchRuntime, gameLaunchStatusText } from './gameLaunchRuntime.js'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null }, disabled: Boolean, beforeLaunch: Function, saveFirst: Boolean, pending: { type: Number, default: 0 } })
const emit = defineEmits(['launch-checked'])
const state = reactive({})
const preparing = ref(false)
const runtime = createGameLaunchRuntime(props.bridge, state)
const statusText = computed(() => gameLaunchStatusText(state))
const running = computed(() => state.snapshot?.state === 'running')
const rays = Array.from({ length: 12 }, (_, index) => { const angle = index * Math.PI / 6; return { x1: 31 + 25 * Math.cos(angle), y1: 31 + 25 * Math.sin(angle), x2: 31 + 30 * Math.cos(angle), y2: 31 + 30 * Math.sin(angle) } })
watch(() => props.installation?.installation_id, id => runtime.setInstallation(id), { immediate: true })
onUnmounted(() => runtime.dispose())
async function launch() {
  if (props.disabled || preparing.value || !runtime.canLaunch()) return
  preparing.value = true
  const installationId = props.installation?.installation_id
  try {
    if (props.beforeLaunch && await props.beforeLaunch() !== true) return
    if (installationId === props.installation?.installation_id) {
      await runtime.launch()
      if (installationId === props.installation?.installation_id) emit('launch-checked')
    }
  } catch { state.error = '启动前保存未完成，请先保存 Mod 选择后再试。' }
  finally { preparing.value = false }
}
defineExpose({ launch })
</script>

<template>
  <div class="game-launch" :class="{ running }" :aria-busy="state.busy || state.refreshing">
    <button type="button" class="sun-button" data-testid="game-launch" :disabled="disabled || preparing || !runtime.canLaunch()" :title="state.installationId ? '启动当前选定安装的游戏程序' : '请先选择游戏安装'" @click="launch">
      <span class="sun-disc" aria-hidden="true"><svg class="sun-rays" viewBox="0 0 62 62"><line v-for="(ray, index) in rays" :key="index" :x1="ray.x1" :y1="ray.y1" :x2="ray.x2" :y2="ray.y2"/></svg><Gamepad2 v-if="running" :size="18"/><Play v-else :size="18"/></span>
      <span class="sun-label">{{ running ? '游戏运行中' : preparing ? '正在准备…' : saveFirst ? '保存并启动' : '启动游戏' }}</span>
    </button>
    <div class="sun-meta">
      <span v-if="state.installationId" role="status" class="game-launch-status" data-testid="game-launch-status" :data-state="state.busy ? 'starting' : state.snapshot?.state || 'not_started'">{{ saveFirst && !running && pending ? `先应用 ${pending} 项更改` : statusText }}</span>
      <button type="button" class="game-launch-refresh" data-testid="game-launch-refresh" :disabled="!state.installationId || state.busy || state.refreshing" aria-label="刷新游戏进程状态" title="刷新游戏进程状态" @click="runtime.refresh"><RefreshCw :size="13"/></button>
    </div>
    <span v-if="state.error" role="alert" class="game-launch-error">{{ state.error }}</span>
  </div>
</template>
