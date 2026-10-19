<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { Gamepad2 } from 'lucide-vue-next'
import { canSelect, createInstallationController, issueText } from './controller.js'
const props = defineProps({ bridge: { type: Object, required: true } })
const emit = defineEmits(['selected'])
const state = reactive({})
const dialog = ref(null), trigger = ref(null)
const controller = createInstallationController(props.bridge, state, installation => { emit('selected', installation); if (dialog.value?.open) close() })
const selected = computed(() => state.installations.find(item => item.installation_id === state.selectedId))
const channel = value => value === 'demo' ? 'Steam Demo' : value === 'retail' || value === 'steam' ? 'Steam' : '手动安装'
const phase = computed(() => ({ queued: '等待读取', running: '正在查找游戏目录', cancelling: '正在取消', succeeded: '读取完成', cancelled: '已取消', failed: '读取失败' })[state.task?.state] || '正在选择目录')
async function open() { dialog.value.showModal(); await controller.refresh() }
function close() { dialog.value.close(); nextTick(() => trigger.value?.focus()) }
onMounted(() => controller.initialize())
onUnmounted(() => controller.dispose())
</script>

<template>
  <div class="installation-selector">
    <button ref="trigger" class="installation-trigger garden-sign" data-testid="installation-open" :data-selected-id="state.selectedId || ''" :aria-label="selected ? `Probably Stolen · ${channel(selected.channel)}，切换游戏安装` : '选择游戏安装'" @click="open"><span class="sign-art" aria-hidden="true"><Gamepad2 :size="18"/></span><span class="sign-text"><span data-testid="active-installation">{{ selected ? `Probably Stolen · ${channel(selected.channel)}` : '选择游戏安装' }}</span><small aria-hidden="true">{{ selected ? '切换安装' : '点这里开始' }}</small></span></button>
    <dialog ref="dialog" class="installation-dialog" aria-labelledby="installation-title" @cancel.prevent="close">
      <header><h2 id="installation-title">选择游戏安装</h2><button aria-label="关闭安装选择器" @click="close">×</button></header>
      <p class="installation-hint">启动时自动查找 Steam 中的游戏。也可以选择其他 Steam 库或直接选择 Probably Stolen 游戏目录。</p>
      <div class="installation-actions"><button :disabled="state.busy" data-testid="installation-auto" @click="controller.discoverAutomatic()">重新查找 Steam 安装</button><button :disabled="state.busy" data-testid="installation-steam" @click="controller.choose('steam')">从 Steam 目录发现</button><button :disabled="state.busy" data-testid="installation-manual" @click="controller.choose('manual')">手动选择安装目录</button></div>
      <div class="installation-content">
        <section v-if="state.installations.length"><h3>已保存的安装</h3><div v-for="item in state.installations" :key="item.installation_id" class="installation-row"><div><strong>Probably Stolen · {{ channel(item.channel) }}</strong><span v-if="item.installation_id === state.selectedId"> · 已选</span><details><summary>查看目录</summary><code>{{ item.canonical_root }}</code></details></div><button :disabled="state.busy" @click="controller.use(item.installation_id)">切换到此安装</button></div></section>
        <section v-if="state.candidates.length"><h3>发现的安装</h3><div v-for="candidate in state.candidates" :key="candidate.candidate_id" class="installation-row"><div><strong>Probably Stolen · {{ channel(candidate.channel) }}</strong><code>{{ candidate.canonical_root }}</code><p v-if="!canSelect(candidate)" class="installation-error">无法使用此目录</p><ul v-if="candidate.issues?.length"><li v-for="(issue, index) in candidate.issues" :key="index">{{ issueText(issue) }}</li></ul></div><button :disabled="state.busy || !canSelect(candidate)" data-testid="installation-select" @click="controller.select(candidate)">使用此安装</button></div></section>
        <p v-if="!state.installations.length && !state.candidates.length && !state.busy" class="installation-hint">尚无安装记录，请选择目录。</p>
      </div>
      <footer><div role="status"><span v-if="state.busy">{{ phase }}<template v-if="state.task?.total != null"> · {{ state.task.completed }} / {{ state.task.total }}</template></span><span v-else>{{ state.notice }}</span></div><button v-if="state.busy && state.task" :disabled="state.task.state === 'cancelling'" @click="controller.cancel">取消</button><button v-else @click="close">关闭</button></footer>
      <p v-if="state.error" role="alert" class="installation-error">{{ state.error }}</p>
    </dialog>
  </div>
</template>

<style scoped>
.installation-selector { display:flex; align-items:center; gap:8px; min-width:0; font-size:12px; }
.installation-label,.installation-hint { color:rgb(var(--rgb-text-dim,148 163 184)); }
button { font:inherit; cursor:pointer; color:inherit; border:1px solid rgba(var(--rgb-border-base,148,163,184),.18); background:rgba(var(--rgb-bg-overlay,255,255,255),.05); border-radius:8px; padding:5px 10px; }
button:hover { background:rgba(var(--rgb-bg-overlay,255,255,255),.1); }
button:disabled { opacity:.45; cursor:not-allowed; }
button:focus-visible,summary:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary,129 140 248)); outline-offset:2px; }
.installation-trigger { display:flex; align-items:center; justify-content:space-between; gap:16px; max-width:320px; }
.installation-dialog { color:rgb(var(--rgb-text-main,226 232 240)); background:rgb(var(--rgb-bg-surface,30 41 59)); border:1px solid rgba(var(--rgb-border-base,148,163,184),.18); border-radius:16px; padding:0; width:min(760px,94vw); max-height:calc(100vh - 2rem); font-size:12px; box-shadow:0 24px 70px #0005; }
.installation-dialog::backdrop { background:#0008; backdrop-filter:blur(8px); }
header,footer { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 20px; }
header { border-bottom:1px solid #ffffff12; } h2 { margin:0; font-size:18px; font-weight:800; } h3 { font-size:12px; margin:12px 0 8px; }
.installation-hint { margin:12px 20px; line-height:1.6; }.installation-actions { display:flex; gap:8px; flex-wrap:wrap; margin:0 20px 12px; }
.installation-content { overflow:auto; max-height:50vh; padding:0 20px; }.installation-row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 0; border-top:1px solid #ffffff12; }.installation-row>div { min-width:0; flex:1; }.installation-row>button { flex-shrink:0; }
p { line-height:1.6; margin:5px 0; } details { margin-top:5px; } summary { cursor:pointer; color:rgb(var(--rgb-text-dim,148 163 184)); } code { overflow-wrap:anywhere; font-size:11px; }.installation-error { color:rgb(var(--rgb-accent-danger,251 113 133)); margin:8px 20px; }.installation-row .installation-error { margin:5px 0; } footer { border-top:1px solid #ffffff12; margin-top:12px; }
</style>
