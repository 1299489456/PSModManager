<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'
import { createImportController } from './controller.js'
import SelectMenu from '../../shared/components/desktop/SelectMenu.vue'
const props = defineProps({ bridge: { type: Object, required: true }, canAdd: Boolean, installation: Object, workspaceDirty: Boolean })
const emit = defineEmits(['imported', 'show-library', 'applied', 'share-source', 'opened', 'active'])
const state = reactive({}), dialog = ref(null), trigger = ref(null)
const controller = createImportController(props.bridge, state, { getInstallContext: () => ({
  installationId: props.installation?.installation_id, workspaceDirty: props.workspaceDirty,
}) })
watch(() => [props.installation?.installation_id, props.workspaceDirty], () => controller.contextChanged(), { flush: 'sync' })
watch(() => state.result, result => { if (result?.release_id) emit('imported', result) })
watch(() => state.applied, result => { if (result?.completed) emit('applied', result) })
const pending = ref([])
const comparison = value => ({ upgrade: '新版 · 可升级', downgrade: '旧版 · 将降级', same: '同版本 · 可重新安装', unknown: '版本关系待确认' })[value] || '待确认'
const packageOptions = computed(() => [...new Map((state.preview?.version_candidates || []).map(row => [row.package_id, row])).values()])
function showDialog() { dialog.value.showModal(); emit('active', true) }
async function openSource(source) {
  if (state.busy) { state.error = '当前导入仍在处理，请完成后再拖入文件。'; return }
  if (source.kind === 'modpack') { emit('share-source', source); return }
  // Drops and Nexus downloads can arrive while another page is shown.
  emit('opened')
  showDialog()
  await controller.openSource(source)
}
async function installSource(source) {
  if (state.busy) { state.error = '当前导入仍在处理，请完成后再安装。'; return false }
  if (source?.kind === 'modpack') { emit('share-source', source); return false }
  emit('opened')
  showDialog()
  return controller.installSource(source)
}
function nativeImport(event) {
  const detail = event.detail || {}, sources = detail.sources || []
  if (detail.errors?.length) { emit('opened'); showDialog(); state.error = detail.errors.map(row => `${row.display_name || '文件'}：${row.code}`).join('；') }
  if (!sources.length) return
  pending.value = sources.slice(1)
  void openSource(sources[0])
}
function nextSource() { const source = pending.value.shift(); if (source) void openSource(source) }
const phase = computed(() => state.task?.state === 'cancelling' ? '正在取消' : state.installStage || ({ queued: '等待处理', running: '正在处理文件' })[state.task?.state] || '正在读取')
const target = file => {
  const root = { mods: 'Mods', plugins: 'Plugins', user_libs: 'UserLibs', game_root: '游戏根目录' }[file.target_root_kind]
  return root ? `${root}/${file.relative_path}` : '未知目标'
}
const ownership = value => ({ exclusive: '独占', shared_identical: '相同内容共享', external: '外部文件', unknown: '未知归属' })[value] || '未知归属'
const lifecycle = value => ({ immutable: '不可变文件', shared_config: '共享配置', private_config: '私有配置', mutable_data: '可变数据', generated: '生成文件', unowned: '未归属' })[value] || '未知类型'
const role = value => ({ mod: '模组', plugin: '插件', dependency: '依赖', resource: '资源', config: '配置', unknown: '未知角色' })[value] || value
function close() { if (state.busy) return; dialog.value.close(); nextTick(() => trigger.value?.focus()) }
function showLibrary() { dialog.value.close(); emit('show-library', state.result) }
onMounted(() => window.addEventListener('psmm-native-import', nativeImport))
onUnmounted(() => { window.removeEventListener('psmm-native-import', nativeImport); controller.dispose() })
defineExpose({ openSource, installSource })
</script>

<template>
  <div class="import-selector" data-testid="native-import-zone">
    <button ref="trigger" class="garden-btn green" data-testid="import-open" @click="showDialog"><Plus :size="17" aria-hidden="true"/>导入</button>
    <dialog ref="dialog" class="import-dialog" aria-labelledby="import-title" @cancel.prevent="close" @close="emit('active', false)">
      <header><h2 id="import-title">{{ state.applied?.completed ? '安装完成' : state.installStage ? '安装 Mod' : '导入 Mod' }}</h2><button :disabled="state.busy" aria-label="关闭导入" @click="close">×</button></header>
      <div class="import-body">
        <p v-if="!state.installStage && !state.applied?.completed" class="hint">选择或拖入 DLL、目录、ZIP，先识别版本并加入包库，再确认安装或替换。分享整合包会打开方案预览。</p>
        <details class="import-help"><summary>支持的文件</summary><p>导入会保存独立副本，不会移动或修改原文件。游戏目录中的已有 Mod 会直接显示在主列表，无需重复导入。</p><p>展开后共 256 MiB，单个文件及 ZIP 不超过 64 MiB。不支持加密、分卷、嵌套压缩包或安装脚本。</p></details>
        <div class="actions"><button :disabled="state.busy" data-testid="import-dll" @click="controller.choose('dll')">选择 DLL</button><button :disabled="state.busy" data-testid="import-directory" @click="controller.choose('directory')">选择目录</button><button :disabled="state.busy" data-testid="import-zip" @click="controller.choose('zip')">选择 ZIP</button></div>
        <section v-if="state.preview" data-testid="import-preview">
          <label class="name">包名称 <input v-model="state.displayName" maxlength="200" :disabled="state.busy" /></label>
          <label class="name">版本 <input v-model="state.versionRaw" maxlength="200" placeholder="未识别，可手动填写" :disabled="state.busy" /></label>
          <p v-if="state.preview.version?.identity" class="hint">{{ state.preview.version.identity_source === 'embedded_metadata' ? '已按 DLL 内嵌标识识别 Mod' : '身份待确认' }} · {{ state.preview.version.version_source === 'filename_hint' ? '版本来自文件名，请核对' : '版本以 Mod 内嵌元数据为准' }}</p>
          <section v-if="state.preview.version_candidates?.length" class="version-review" aria-label="Mod 版本关系"><strong>包库中已有相关版本</strong><ul><li v-for="candidate in state.preview.version_candidates" :key="candidate.release_id">{{ candidate.display_name }} {{ candidate.version_raw || '版本未知' }} — {{ comparison(candidate.comparison) }}<small v-if="candidate.match === 'target_overlap'">目标 DLL 相同；请确认它们属于同一 Mod。</small></li></ul><label>关联到已有 Mod <SelectMenu v-model="state.packageId" class="package-choice" aria-label="归属的 Mod" :disabled="state.busy" :options="[{ value: '', label: '按内嵌标识识别 / 保留为独立 Mod' }, ...packageOptions.map(candidate => ({ value: candidate.package_id, label: candidate.display_name }))]"/></label></section>
          <p>共 {{ state.preview.total }} 个文件<span v-if="!state.preview.can_commit"> · 当前内容不能入库</span></p>
          <ul v-if="state.preview.warnings?.length" class="warnings"><li v-for="(warning, index) in state.preview.warnings" :key="index">{{ warning }}</li></ul>
          <div class="file-list"><table><thead><tr><th>文件</th><th>目标与角色</th><th>归属</th></tr></thead><tbody><tr v-for="file in state.preview.files" :key="file.path"><td><strong>{{ file.path }}</strong><small>{{ file.size }} 字节</small><details><summary>校验信息</summary><code>{{ file.sha256 }}</code><small>解析状态：{{ file.parser_status || '不适用' }}</small></details></td><td>{{ target(file) }}<small>{{ (file.roles || []).map(role).join('、') || '未知角色' }}</small></td><td>{{ ownership(file.ownership) }}<small>{{ lifecycle(file.lifecycle) }}</small></td></tr></tbody></table></div>
          <nav aria-label="导入文件分页"><button :disabled="state.busy || state.offset === 0" @click="controller.page(Math.max(0, state.offset - 20))">上一页</button><span>{{ state.offset + 1 }}–{{ state.offset + state.preview.files.length }} / {{ state.preview.total }}</span><button :disabled="state.busy || state.preview.next_offset == null" @click="controller.page(state.preview.next_offset)">下一页</button></nav>
        </section>
        <p v-if="state.busy" role="status" data-testid="import-progress">{{ phase }}<template v-if="state.task?.total != null"> · {{ state.task.completed }} / {{ state.task.total }}</template></p>
        <p v-if="state.notice" role="status">{{ state.notice }}</p>
        <section v-if="state.result" class="result" data-testid="import-result"><strong v-if="state.applied?.completed" data-testid="import-installed">已安装并启用 · {{ state.result.file_count }} 个文件</strong><strong v-else>已添加 Mod · {{ state.result.file_count }} 个文件</strong><p v-if="!state.busy && !state.applied?.completed">{{ canAdd ? '可在下方确认安装，或在模组卡片上打开开关后保存。' : '选择游戏后，在模组卡片上打开开关并保存即可启用。' }}</p><button v-if="canAdd" :disabled="state.busy" data-testid="import-show-library" @click="showLibrary">在列表中查看</button><details><summary>版本标识</summary><code>{{ state.result.release_id }}</code></details></section>
        <button v-if="state.result && installation && !state.updatePreview && !state.applied" data-testid="import-install-preview" :disabled="state.busy || workspaceDirty" @click="controller.previewUpdate(installation.installation_id)">安装 / 更新到游戏…</button>
        <section v-if="state.updatePreview" class="version-review" data-testid="import-update-preview"><strong>安装 {{ state.updatePreview.incoming.display_name }} {{ state.updatePreview.incoming.version_raw || '' }}</strong><ul><li v-for="item in state.updatePreview.replacements" :key="item.id">{{ item.display_name }} {{ item.version_raw || '版本未知' }} → {{ comparison(item.comparison) }}</li></ul><p>{{ state.updatePreview.message }}</p><p v-if="!state.updatePreview.replacements.length">{{ state.updatePreview.already_enabled ? '此版本已启用，可重新核对安装。' : '将安装并启用此 Mod。' }}</p><button data-testid="import-install-confirm" :disabled="state.busy || workspaceDirty || !installation" @click="controller.applyUpdate(installation?.installation_id)">确认{{ state.updatePreview.replacements.length ? '替换并启用' : '安装并启用' }}</button></section>
        <p v-if="workspaceDirty" class="hint">模组列表有未保存的更改，请先保存或撤销后再安装。</p>
        <p v-if="pending.length" class="hint">还有 {{ pending.length }} 个拖入文件待处理。<button :disabled="state.busy || !state.result && state.preview" @click="nextSource">处理下一个</button></p>
        <div v-if="state.error" role="alert" class="error-message" data-testid="import-error"><p>{{ state.error }}</p><small v-if="state.errorCode">错误编号：<code>{{ state.errorCode }}</code></small></div>
      </div>
      <footer><span class="hint">仅支持 DLL、目录和 ZIP，不执行安装脚本。</span><button v-if="state.busy && state.task" :disabled="state.task.state === 'cancelling'" @click="controller.cancel">取消导入</button><button v-if="state.preview" data-testid="import-commit" :disabled="state.busy || !state.preview.can_commit || !state.displayName.trim()" @click="controller.commit">添加到包库</button><button v-if="!state.busy" @click="close">关闭</button></footer>
    </dialog>
  </div>
</template>

<style scoped>
button,input,select { font:inherit; color:inherit; border:1px solid rgb(var(--rgb-border-base)/.22); background:rgb(var(--rgb-bg-overlay)/.05); border-radius:8px; padding:6px 12px; }
.version-review{margin:16px 0;padding:14px;border-radius:10px;background:rgb(var(--rgb-accent-primary)/.07);line-height:1.7}.version-review ul{padding-left:20px}.version-review select{max-width:100%;margin:6px}
button { cursor:pointer; } button:disabled { opacity:.45; cursor:not-allowed; } button:focus-visible,input:focus-visible,summary:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }
.import-dialog { width:min(860px,94vw); max-height:calc(100vh - 2rem); padding:0; border-radius:16px; border:1px solid rgb(var(--rgb-border-base)/.2); color:rgb(var(--rgb-text-main)); background:rgb(var(--rgb-bg-surface)); box-shadow:0 24px 70px #0005; font-size:13px; }
.import-dialog::backdrop { background:#0008; backdrop-filter:blur(8px); } header,footer { display:flex; align-items:center; gap:12px; padding:16px 20px; } header { justify-content:space-between; border-bottom:1px solid rgb(var(--rgb-border-base)/.18); } h2 { margin:0; font-size:18px; } footer { flex-wrap:wrap; border-top:1px solid rgb(var(--rgb-border-base)/.18); } footer .hint { margin-right:auto; }
.import-body { padding:4px 20px 16px; overflow:auto; max-height:65vh; } .hint,small { color:rgb(var(--rgb-text-dim)); line-height:1.6; } .actions,nav { display:flex; gap:8px; align-items:center; margin:12px 0; } .name { display:flex; align-items:center; gap:10px; margin-top:18px; } .name input { flex:1; min-width:0; } .file-list { overflow:auto; } table { width:100%; border-collapse:collapse; table-layout:fixed; } th { text-align:left; } th:first-child { width:45%; } th,td { padding:10px 8px; border-bottom:1px solid rgb(var(--rgb-border-base)/.18); vertical-align:top; overflow-wrap:anywhere; } small { display:block; margin-top:4px; } strong { font-weight:600; } code { font-size:11px; overflow-wrap:anywhere; } summary { cursor:pointer; margin-top:6px; } .error-message { color:rgb(var(--rgb-accent-danger)); } .warnings { color:rgb(var(--rgb-text-main)); line-height:1.7; padding-left:20px; } nav { justify-content:flex-end; } .result { overflow-wrap:anywhere; }
</style>
