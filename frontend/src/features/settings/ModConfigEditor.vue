<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { createConfigEditorRuntime } from './configEditorRuntime.js'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null } })
const state = reactive({}), dialog = ref(null), trigger = ref(null)
const runtime = createConfigEditorRuntime(props.bridge, state)
const canSave = computed(() => runtime.canSave())
const busy = computed(() => state.loading || state.saving)
const optionLabel = value => ({ cover: '填满画面（裁剪）', fit: '完整显示（留边）', stretch: '拉伸铺满' })[value] || value

function close() {
  if (state.saving) return
  runtime.close()
  dialog.value?.close()
  nextTick(() => trigger.value?.focus())
}
function open() {
  if (!props.installation?.installation_id) return
  dialog.value.showModal()
  void runtime.open()
}
watch(() => props.installation?.installation_id, id => {
  runtime.setInstallation(id || null)
  dialog.value?.close()
}, { immediate: true })
onUnmounted(() => runtime.dispose())
</script>

<template>
  <div class="config-editor">
    <button ref="trigger" class="garden-btn soft" data-testid="config-open" :disabled="!installation?.installation_id" @click="open">配置</button>
    <dialog ref="dialog" class="config-dialog" data-testid="config-dialog" :data-dismiss="state.saving || Object.keys(runtime.changes()).length ? 'keep' : undefined" aria-labelledby="mod-config-title" @cancel.prevent="close">
      <header><div><h2 id="mod-config-title">PSWallpaper 配置</h2><p class="file-path">{{ state.document?.relative_path || 'UserData/PSWallpaper.cfg' }}</p></div><button aria-label="关闭配置" :disabled="state.saving" @click="close">×</button></header>
      <form @submit.prevent="runtime.save">
        <div class="config-body">
          <p class="intro">只修改文件中已有的受支持设置。其他配置和注释会保留。</p>
          <p v-if="state.loading" data-testid="config-loading" role="status">正在读取配置…</p>
          <p v-if="state.document?.message" data-testid="config-message" class="message">{{ state.document.message }}</p>
          <p v-if="state.document && !state.document.editable" class="message">此文件无法可靠编辑，请使用外部编辑器处理。</p>
          <fieldset v-if="state.document?.fields.length" :disabled="busy || !state.document.editable">
            <label v-for="field in state.document.fields" :key="field.key" class="config-field" :class="{ 'boolean-field': field.kind === 'boolean' }">
              <span>{{ field.label }}</span>
              <select v-if="field.kind === 'choice'" v-model="state.values[field.key]" :data-testid="`config-${field.key}`"><option v-for="option in field.options" :key="option" :value="option">{{ optionLabel(option) }}</option></select>
              <template v-else-if="field.kind === 'integer'"><input v-model.number="state.values[field.key]" type="number" :min="field.min" :max="field.max" step="1" required :data-testid="`config-${field.key}`" /><small>{{ field.min }}–{{ field.max }}</small></template>
              <input v-else-if="field.kind === 'boolean'" v-model="state.values[field.key]" type="checkbox" :data-testid="`config-${field.key}`" />
            </label>
          </fieldset>
          <label v-if="state.document?.editable" class="backup-option"><input v-model="state.backup" type="checkbox" data-testid="config-backup" :disabled="busy" /><span>保存前备份原文件</span></label>
          <p v-if="state.saving" data-testid="config-saving" role="status">正在保存配置…</p>
          <p v-if="state.notice" data-testid="config-notice" role="status">{{ state.notice }}</p>
          <p v-if="state.error" data-testid="config-error" class="error" role="alert">{{ state.error }}</p>
        </div>
        <footer><button type="button" data-testid="config-refresh" :disabled="busy" @click="runtime.read">重新读取</button><button type="submit" data-testid="config-save" class="primary-action" :disabled="!canSave">保存</button><button type="button" data-testid="config-close" :disabled="state.saving" @click="close">关闭</button></footer>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
button,input,select{font:inherit;color:inherit;border:1px solid rgb(var(--rgb-border-base)/.3);border-radius:8px;background:rgb(var(--rgb-bg-overlay)/.05);padding:8px 12px}button{min-height:40px;cursor:pointer}button:disabled,input:disabled,select:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent-primary);outline-offset:2px}.config-dialog{width:min(560px,94vw);max-height:calc(100vh - 2rem);padding:0;border:1px solid rgb(var(--rgb-border-base)/.2);border-radius:12px;color:var(--text-main);background:var(--bg-surface);box-shadow:0 24px 70px #0005;font-size:15px;line-height:1.6}.config-dialog::backdrop{background:#0008;backdrop-filter:blur(6px)}header,footer{display:flex;align-items:center;gap:10px;padding:16px 20px}header{justify-content:space-between;border-bottom:1px solid rgb(var(--rgb-border-base)/.18)}h2{margin:0;font-size:20px}header p{margin:4px 0 0;font-size:14px;color:var(--text-dim);overflow-wrap:anywhere}.config-body{padding:4px 20px 16px;max-height:calc(100vh - 240px);overflow:auto}p{margin:12px 0}.intro,.message,small{color:var(--text-dim)}fieldset{border:0;padding:0;margin:18px 0}.config-field{display:grid;grid-template-columns:1fr 200px;align-items:center;gap:8px;margin:16px 0}.config-field small{grid-column:2;font-size:14px}.boolean-field input{justify-self:start}.backup-option{display:flex;align-items:center;gap:10px;margin:18px 0 8px}input[type=checkbox]{appearance:auto;accent-color:var(--accent-primary);width:20px;height:20px;min-height:0;padding:0;margin:0}.error{color:var(--accent-danger)}footer{justify-content:flex-end;flex-wrap:wrap;border-top:1px solid rgb(var(--rgb-border-base)/.18)}footer>button:first-child{margin-right:auto}.primary-action{border-color:rgb(var(--rgb-accent-primary)/.6);background:rgb(var(--rgb-accent-primary)/.15);font-weight:600}@media(max-width:480px){.config-field{grid-template-columns:1fr}.config-field small{grid-column:1}.boolean-field{grid-template-columns:1fr auto}}
</style>
