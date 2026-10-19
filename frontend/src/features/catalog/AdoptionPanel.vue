<script setup>
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { createAdoptionStore } from './adoptionStore.js'
const props = defineProps({ bridge: { type: Object, required: true }, installation: Object })
const emit = defineEmits(['imported'])
const state = reactive({}), dialog = ref(null), trigger = ref(null)
const store = createAdoptionStore(props.bridge, state)
const selected = computed(() => new Set(state.selectedPaths))
const selectableFiles = computed(() => (state.preview?.files || []).filter(file => file.lifecycle === 'immutable'))
const selectedOnPage = computed(() => selectableFiles.value.filter(file => selected.value.has(file.relative_path)).length)
const allOnPage = computed(() => selectableFiles.value.length > 0 && selectedOnPage.value === selectableFiles.value.length)
const confirmationReason = computed(() => {
  if (state.busy) return '正在处理，请稍候。'
  if (!state.preview) return '请先扫描并读取清单。'
  if (!state.preview.total) return '没有找到文件，请检查安装或重新扫描。'
  if (!state.selectedPaths.length && !selectableFiles.value.length) return state.preview.total > state.preview.files.length
    ? '本页没有可接管的文件，请换页查看。' : '这些文件均不支持接管。'
  if (!state.selectedPaths.length && !state.displayName.trim()) return '请勾选要保存的文件，并填写包名称。'
  if (!state.selectedPaths.length) return '请勾选要保存的文件，或点击“全选本页”。'
  if (!state.displayName.trim()) return '已选择文件，请填写包名称。'
  return ''
})
const phase = computed(() => ({ queued: '等待处理', running: '正在核对文件', cancelling: '正在取消' })[state.task?.state] || '正在读取')
const lifecycle = value => ({ immutable:'可选择', shared_config:'共享配置，不支持接管', private_config:'私有配置，不支持接管', mutable_data:'可变数据，不支持接管', generated:'生成文件，不支持接管', unowned:'未知类型，不支持接管' })[value] || '未知类型，不支持接管'
function selectPage(value) { for (const file of selectableFiles.value) store.toggle(file.relative_path, value) }
function clearSelection() { for (const path of [...state.selectedPaths]) store.toggle(path, false) }
watch(() => props.installation?.installation_id, id => { void store.setInstallation(id || null) }, { immediate: true })
watch(() => state.result, result => { if (result) emit('imported', result) })
function close() { if (state.busy) return; dialog.value.close(); nextTick(() => trigger.value?.focus()) }
onUnmounted(() => store.dispose())
</script>

<template>
  <div class="adoption-selector">
    <button ref="trigger" data-testid="adoption-open" :disabled="!installation" @click="dialog.showModal()">接管</button>
    <dialog ref="dialog" class="adoption-dialog" aria-labelledby="adoption-title" @cancel.prevent="close">
      <header><h2 id="adoption-title">接管现有文件</h2><button :disabled="state.busy" aria-label="关闭接管" @click="close">×</button></header>
      <div class="adoption-body">
        <p class="intro">将游戏中已有的 Mod 加入包库，原文件会保留。</p>
        <div class="preview-actions"><button data-testid="adoption-scan-button" :disabled="state.busy || !installation" @click="store.preview()">{{ state.preview ? '重新读取清单' : '扫描并读取清单' }}</button></div>
        <section v-if="state.preview" data-testid="adoption-preview">
          <div class="name-fields">
            <label class="name-field"><span>包名称 <strong class="required">必填</strong></span><input v-model="state.displayName" data-testid="adoption-name" maxlength="200" required aria-required="true" aria-describedby="adoption-name-help" :disabled="state.busy" placeholder="例如：常用 Mod" /><span id="adoption-name-help" class="hint">这些文件将在包库中显示为一个包。</span></label>
            <label class="version-field"><span>版本 <span class="hint">选填</span></span><input v-model="state.version" data-testid="adoption-version" maxlength="200" :disabled="state.busy" placeholder="例如：1.0" /></label>
          </div>
          <div class="selection-heading"><h3>选择要保存的文件</h3><strong data-testid="adoption-selected-count">已选 {{ state.selectedPaths.length }} 项</strong></div>
          <div class="selection-toolbar"><button data-testid="adoption-select-page" :disabled="state.busy || !selectableFiles.length || allOnPage" @click="selectPage(true)">全选本页</button><button data-testid="adoption-clear-selection" :disabled="state.busy || !state.selectedPaths.length" @click="clearSelection">清空选择</button><span class="hint">本页可选 {{ selectableFiles.length }} 项 · 每次最多 128 项</span></div>
          <p v-if="!state.preview.total" class="empty-files">没有找到可接管的文件。</p>
          <div v-else class="file-list" aria-label="接管文件清单">
            <article v-for="file in state.preview.files" :key="file.relative_path" class="file-row" :class="{ 'is-selected': selected.has(file.relative_path), 'is-unavailable': file.lifecycle !== 'immutable' }">
              <label class="file-choice"><input type="checkbox" data-testid="adoption-file-checkbox" :data-path="file.relative_path" :aria-label="`选择 ${file.relative_path}`" :checked="selected.has(file.relative_path)" :disabled="state.busy || file.lifecycle !== 'immutable'" @change="store.toggle(file.relative_path, $event.target.checked)" /><span class="file-name"><strong>{{ file.relative_path }}</strong><span class="hint">{{ lifecycle(file.lifecycle) }} · {{ file.size }} 字节</span></span><span class="selection-label">{{ file.lifecycle !== 'immutable' ? '不可选' : selected.has(file.relative_path) ? '已选择' : '选择' }}</span></label>
              <details class="file-details"><summary>文件详情与归属</summary><div class="ownership-summary"><template v-if="file.owners?.matching?.length"><strong>相同内容共享 · {{ file.owners.matching.length }} 个版本</strong><code v-for="owner in file.owners.matching" :key="owner" data-testid="adoption-owner" data-kind="matching">{{ owner }}</code></template><span v-else>尚未加入包库</span><template v-if="file.owners?.conflicting?.length"><strong class="warning">{{ file.owners.conflicting.length }} 个已登记版本与当前内容不同</strong><code v-for="owner in file.owners.conflicting" :key="owner" data-testid="adoption-owner" data-kind="conflicting">{{ owner }}</code><p>请核对变化后再决定是否作为新版本入库。</p></template><p class="hint">文件校验值</p><code>{{ file.sha256 }}</code></div></details>
            </article>
          </div>
          <p class="hint selection-note">勾选方框或点击文件名即可选择。配置、日志、存档和未知类型不能加入包库。</p>
          <nav aria-label="接管文件分页"><button data-testid="adoption-previous" :disabled="state.busy || state.offset === 0" @click="store.page(Math.max(0,state.offset-20))">上一页</button><span>{{ state.preview.total ? state.offset+1 : 0 }}–{{ state.offset+state.preview.files.length }} / {{ state.preview.total }}</span><button data-testid="adoption-next" :disabled="state.busy || state.preview.next_offset == null" @click="store.page(state.preview.next_offset)">下一页</button></nav>
        </section>
        <p v-if="state.busy" role="status" data-testid="adoption-progress">{{ state.operation === 'scan' ? '正在扫描文件' : phase }}<template v-if="state.task?.total != null"> · {{ state.task.completed }} / {{ state.task.total }}</template></p>
        <p v-if="state.notice" role="status">{{ state.notice }}</p>
        <div v-if="state.result" data-testid="adoption-result" :data-release-id="state.result.release.release_id"><strong>已将 {{ state.confirmedFileCount }} 个文件加入包库。</strong><details><summary>查看版本记录</summary><code>{{ state.result.release.release_id }}</code></details></div>
        <p v-if="state.error" role="alert" data-testid="adoption-error" class="error">{{ state.error }}</p>
        <ul v-if="state.issues?.length" class="scan-issues"><li v-for="(issue,index) in state.issues" :key="index"><strong>{{ issue.path || '扫描' }}</strong>：{{ issue.reason }}<small v-if="issue.code">{{ issue.code }}</small></li></ul>
      </div>
      <footer><div class="confirmation-status"><strong v-if="state.preview">已选择 {{ state.selectedPaths.length }} 个文件</strong><p v-if="state.preview" id="adoption-confirmation-reason" data-testid="adoption-confirmation-reason" role="status">{{ confirmationReason || '可以确认入库。原文件会保留。' }}</p><span v-else class="hint">加入包库后，可在模组页管理。</span></div><button v-if="state.busy" data-testid="adoption-cancel" :disabled="state.task?.state === 'cancelling'" @click="store.cancel">取消接管</button><button v-if="state.preview" data-testid="adoption-commit" class="primary-action" :disabled="Boolean(confirmationReason)" :title="confirmationReason || '将所选文件加入包库'" aria-describedby="adoption-confirmation-reason" @click="store.commit">确认所选文件入库</button><button v-if="!state.busy" data-testid="adoption-close" @click="close">关闭</button></footer>
    </dialog>
  </div>
</template>

<style scoped>
button,input { font:inherit; color:inherit; border:1px solid rgb(var(--rgb-border-base)/.32); background:rgb(var(--rgb-bg-overlay)/.05); border-radius:8px; padding:8px 14px; } button { cursor:pointer; min-height:42px; } button:disabled { opacity:.5; cursor:not-allowed; } button:focus-visible,input:focus-visible,summary:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }
.adoption-dialog { width:min(960px,94vw); max-height:calc(100vh - 2rem); padding:0; border-radius:16px; border:1px solid rgb(var(--rgb-border-base)/.2); color:rgb(var(--rgb-text-main)); background:rgb(var(--rgb-bg-surface)); box-shadow:0 24px 70px #0005; font-size:15px; line-height:1.55; } .adoption-dialog::backdrop { background:#0008; backdrop-filter:blur(8px); } header,footer { display:flex; align-items:center; gap:12px; padding:16px 20px; } header { justify-content:space-between; border-bottom:1px solid rgb(var(--rgb-border-base)/.18); } h2 { margin:0; font-size:20px; } h3 { margin:0; font-size:16px; } footer { flex-wrap:wrap; border-top:1px solid rgb(var(--rgb-border-base)/.18); }
.adoption-body { padding:4px 20px 16px; overflow:auto; max-height:calc(100vh - 240px); } .intro { margin:12px 0; } .hint,small { color:rgb(var(--rgb-text-dim)); font-size:15px; line-height:1.55; } .preview-actions,.selection-toolbar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .scan-issues { padding-left:20px; line-height:1.65; overflow-wrap:anywhere; }
.name-fields { display:grid; grid-template-columns:2fr 1fr; gap:16px; margin:20px 0; } .name-fields label { display:flex; flex-direction:column; gap:6px; min-width:0; } .name-fields input { min-width:0; width:100%; box-sizing:border-box; min-height:42px; } .required { margin-left:6px; color:rgb(var(--rgb-accent-primary)); font-size:15px; font-weight:600; } .selection-heading { display:flex; flex-wrap:wrap; gap:12px; justify-content:space-between; align-items:center; margin-bottom:10px; } .selection-heading>strong { color:rgb(var(--rgb-accent-primary)); } .selection-toolbar { margin-bottom:10px; }
.file-list { max-height:270px; overflow:auto; border:1px solid rgb(var(--rgb-border-base)/.26); border-radius:10px; } .file-row + .file-row { border-top:1px solid rgb(var(--rgb-border-base)/.2); } .file-row.is-selected { background:rgb(var(--rgb-accent-primary)/.1); } .file-choice { display:flex; align-items:center; gap:12px; min-height:68px; padding:12px 14px 8px; cursor:pointer; } .file-choice input[type=checkbox] { appearance:auto; accent-color:rgb(var(--rgb-accent-primary)); flex:none; width:22px; height:22px; margin:0; padding:0; cursor:pointer; } .file-name { flex:1; min-width:0; display:flex; flex-direction:column; gap:3px; overflow-wrap:anywhere; } .selection-label { flex:none; font-weight:600; } .is-selected .selection-label { color:rgb(var(--rgb-accent-primary)); } .is-unavailable .file-choice,.is-unavailable input { cursor:not-allowed; } .is-unavailable .selection-label { color:rgb(var(--rgb-text-dim)); } .file-details { margin:0 14px 10px 48px; } .file-details summary { color:rgb(var(--rgb-text-dim)); } .ownership-summary { margin-top:8px; padding:10px 12px; border-radius:8px; background:rgb(var(--rgb-bg-overlay)/.04); } .ownership-summary p { margin:8px 0 4px; } .selection-note { margin:10px 0; } .empty-files { padding:20px; text-align:center; }
small,code { display:block; margin-top:4px; } code { font-size:15px; overflow-wrap:anywhere; } strong { font-weight:600; } summary { cursor:pointer; } nav { display:flex; gap:10px; align-items:center; justify-content:flex-end; margin-top:12px; } .error { color:rgb(var(--rgb-accent-danger)); } .warning { display:block; margin-top:6px; color:rgb(var(--rgb-text-main)); } .confirmation-status { flex:1; min-width:230px; } .confirmation-status p { margin:4px 0 0; color:rgb(var(--rgb-text-dim)); } .primary-action { border-color:rgb(var(--rgb-accent-primary)/.7); background:rgb(var(--rgb-accent-primary)/.15); font-weight:600; } @media(max-width:650px) { .name-fields { grid-template-columns:1fr; } .adoption-body { max-height:calc(100vh - 290px); } .file-choice { gap:8px; padding-left:10px; padding-right:10px; } }
</style>
