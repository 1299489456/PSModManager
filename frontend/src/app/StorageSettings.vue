<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ArrowRight, CheckCircle2, FolderOpen, HardDrive, LoaderCircle, RotateCw, TriangleAlert } from 'lucide-vue-next'
import appIcon from '../shared/branding/psmm-icon-256.png?inline'

const props = defineProps({
  bridge: { type: Object, required: true },
  firstRun: { type: Boolean, default: false },
  blocked: { type: Boolean, default: false },
})
const emit = defineEmits(['ready', 'state'])
const status = ref(null), error = ref(''), busy = ref(false), editing = ref(false)
const target = ref(''), confirmedTarget = ref(''), targetInput = ref(null)
let disposed = false, timer
const firstUse = computed(() => props.firstRun || status.value?.first_run)
const phase = computed(() => status.value?.phase || 'idle')
const locked = computed(() => busy.value || phase.value !== 'idle')
const percent = computed(() => status.value?.total > 0
  ? Math.min(100, Math.max(0, Math.floor(status.value.completed / status.value.total * 100))) : null)
const blockedReason = '请先保存或撤销模组列表的修改，并等待当前操作完成。'

function remember(result) {
  if (disposed) return
  status.value = result
  emit('state', result)
  clearTimeout(timer)
  if (result.phase === 'copying') timer = setTimeout(refresh, 600)
}
function showError(cause) { error.value = cause?.message || '无法获取数据位置，请重试。' }
async function refresh() {
  try {
    const result = await props.bridge.request('app.storage.status', {})
    if (!disposed) { error.value = ''; remember(result) }
  } catch (cause) {
    if (disposed) return
    showError(cause)
    if (phase.value === 'copying') timer = setTimeout(refresh, 1000)
  }
}
async function openEditor() {
  editing.value = true; error.value = ''; confirmedTarget.value = ''
  await nextTick(); targetInput.value?.focus()
}
async function choose() {
  if (locked.value) return
  busy.value = true; error.value = ''
  try {
    const result = await props.bridge.request('app.storage.choose', {})
    if (result.target) target.value = result.target
  } catch (cause) { showError(cause) }
  finally { busy.value = false }
}
function review() {
  if (locked.value || props.blocked || !target.value.trim()) return
  error.value = ''; confirmedTarget.value = target.value.trim()
}
async function start() {
  if (locked.value || props.blocked || !confirmedTarget.value) return
  busy.value = true; error.value = ''
  try {
    remember(await props.bridge.request('app.storage.start', { target: confirmedTarget.value }))
    confirmedTarget.value = ''
  } catch (cause) { showError(cause) }
  finally { busy.value = false }
}
async function accept() {
  if (locked.value) return
  busy.value = true; error.value = ''
  try {
    const result = await props.bridge.request('app.storage.accept', {})
    remember(result); emit('ready')
  } catch (cause) { showError(cause) }
  finally { busy.value = false }
}
async function restart() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await props.bridge.request('app.storage.restart', {}) }
  catch (cause) { showError(cause); busy.value = false }
}
onMounted(refresh)
onUnmounted(() => { disposed = true; clearTimeout(timer) })
// Locations under the data directory are shown relative to it; the full path stays in the tooltip.
function insideRoot(path) {
  const root = String(status.value?.root || '').replace(/[\\/]+$/, '')
  const value = String(path || '')
  if (!root || value.length <= root.length || value.slice(0, root.length).toLowerCase() !== root.toLowerCase() || !/[\\/]/.test(value[root.length])) return value
  return '.' + value.slice(root.length)
}
</script>

<template>
  <article class="garden-card wide storage-settings" :class="{ 'first-use': firstUse }" :data-testid="firstUse ? 'storage-first-run' : 'storage-settings'" :aria-busy="busy || phase === 'copying'">
    <template v-if="phase === 'idle'">
      <header class="storage-heading">
        <img v-if="firstUse" class="storage-mark app-mark" :src="appIcon" alt="" aria-hidden="true" draggable="false"/>
        <div>
          <span v-if="firstUse" class="storage-eyebrow">欢迎使用 PSMM</span>
          <h2><HardDrive v-if="!firstUse" :size="19" aria-hidden="true"/>{{ firstUse ? '给你的模组留个位置' : '数据位置' }}</h2>
          <p>{{ firstUse ? '包库、方案与缓存统一放在这里。可以选择其他盘，以后也能迁移。' : '包库、方案、配置与备份统一管理，换盘时一起迁移。' }}</p>
        </div>
      </header>
      <template v-if="status">
        <div class="storage-root"><span>{{ firstUse ? '默认数据目录' : '当前数据目录' }}</span><code data-testid="storage-root">{{ status.root }}</code></div>
        <dl v-if="!firstUse && !editing && status.locations?.length" class="garden-facts wide storage-locations" data-testid="storage-locations">
          <template v-for="location in status.locations" :key="location.label"><dt>{{ location.label }}</dt><dd :title="location.path">{{ insideRoot(location.path) }}</dd></template>
        </dl>

        <section v-if="confirmedTarget" class="storage-confirm" data-testid="storage-confirm" aria-labelledby="storage-confirm-title">
          <h3 id="storage-confirm-title">迁移前确认</h3>
          <dl class="garden-facts wide"><dt>迁移到</dt><dd>{{ confirmedTarget }}</dd></dl>
          <ul>
            <li>迁移包库、方案、配置、历史备份、缓存和日志，不改动游戏目录。</li>
            <li>下载和分析将停止；WebView 缓存在新目录重建。</li>
            <li>旧目录保留备份。确认新位置可用后，可清理其中的数据文件；请保留 .storage-redirect.json，供旧快捷方式和下载链接转向新目录。</li>
          </ul>
          <div class="storage-actions"><button class="garden-btn green" data-testid="storage-start" :disabled="busy || blocked" @click="start">开始迁移<ArrowRight :size="16" aria-hidden="true"/></button><button class="garden-btn soft" data-testid="storage-back" :disabled="busy" @click="confirmedTarget = ''">返回修改</button></div>
        </section>
        <form v-else-if="editing" class="storage-editor" @submit.prevent="review">
          <label for="storage-target">新的数据目录</label>
          <div class="storage-path-input"><input id="storage-target" ref="targetInput" v-model="target" data-testid="storage-target" placeholder="例如 D:\PSModManager" autocomplete="off" spellcheck="false" :disabled="busy" aria-describedby="storage-target-hint"/><button type="button" class="garden-btn" data-testid="storage-choose" :disabled="busy" @click="choose"><FolderOpen :size="17" aria-hidden="true"/>浏览</button></div>
          <p id="storage-target-hint" class="storage-hint">选择本机的独立空目录，或输入一个尚未创建的目录。</p>
          <div class="storage-actions"><button type="submit" class="garden-btn green" data-testid="storage-review" :disabled="busy || blocked || !target.trim()">下一步<ArrowRight :size="16" aria-hidden="true"/></button><button type="button" class="garden-btn soft" data-testid="storage-cancel" :disabled="busy" @click="editing = false; error = ''">取消</button></div>
        </form>
        <div v-else class="storage-actions">
          <button v-if="firstUse" class="garden-btn green" data-testid="storage-default" :disabled="busy" @click="accept">使用默认位置<ArrowRight :size="16" aria-hidden="true"/></button>
          <button class="garden-btn" :class="{ soft: firstUse }" data-testid="storage-change" :disabled="busy" @click="openEditor"><FolderOpen :size="17" aria-hidden="true"/>{{ firstUse ? '选择其他位置' : '更换数据目录' }}</button>
        </div>
        <p v-if="blocked && editing" class="storage-blocked" data-testid="storage-blocked"><TriangleAlert :size="17" aria-hidden="true"/>{{ blockedReason }}</p>
      </template>
      <p v-else-if="!error" class="storage-loading"><LoaderCircle class="garden-spin" :size="18" aria-hidden="true"/>正在读取数据位置…</p>
    </template>

    <section v-else-if="phase === 'copying'" class="storage-transfer" data-testid="storage-copying" aria-labelledby="storage-copy-title">
      <span class="storage-mark" aria-hidden="true"><LoaderCircle class="garden-spin" :size="29"/></span>
      <h2 id="storage-copy-title">正在迁移数据</h2>
      <p class="storage-do-not-close"><TriangleAlert :size="18" aria-hidden="true"/>请勿关闭窗口</p>
      <p>下载和分析已停止，正在复制本地数据。</p>
      <div class="storage-progress" data-testid="storage-progress" aria-live="polite"><progress v-if="percent !== null" :value="percent" max="100" aria-label="数据迁移进度"/><progress v-else aria-label="正在准备迁移"/><strong>{{ percent === null ? '准备中…' : `${percent}%` }}</strong></div>
      <p v-if="status.message" class="storage-hint">{{ status.message }}</p>
      <dl class="garden-facts wide"><dt>迁移到</dt><dd>{{ status.target }}</dd></dl>
    </section>

    <section v-else-if="phase === 'ready'" class="storage-transfer" data-testid="storage-complete" aria-labelledby="storage-ready-title">
      <span class="storage-mark" aria-hidden="true"><CheckCircle2 :size="29"/></span>
      <h2 id="storage-ready-title">数据已迁移，重启后使用新位置</h2>
      <p v-if="status.message" class="storage-result-message" data-testid="storage-result-message" role="status">{{ status.message }}</p>
      <dl class="garden-facts wide"><dt>新数据目录</dt><dd>{{ status.target }}</dd><dt>原目录备份</dt><dd>{{ status.previous_root || status.root }}</dd></dl>
      <p class="storage-hint">确认新位置可用后，可清理原目录的数据文件；请保留 .storage-redirect.json，供旧快捷方式和下载链接转向新目录。</p>
      <button class="garden-btn green" data-testid="storage-restart" :disabled="busy" @click="restart"><RotateCw :size="17" aria-hidden="true"/>{{ busy ? '正在重启…' : '重启并使用新目录' }}</button>
    </section>

    <section v-else-if="phase === 'failed'" class="storage-transfer failed" data-testid="storage-failed" aria-labelledby="storage-failed-title">
      <span class="storage-mark" aria-hidden="true"><TriangleAlert :size="29"/></span>
      <h2 id="storage-failed-title">迁移未完成，仍使用原目录</h2>
      <p class="storage-failure-message">{{ status.message }}</p>
      <dl class="garden-facts wide"><dt>原数据目录</dt><dd>{{ status.root }}</dd></dl>
      <button class="garden-btn" data-testid="storage-restart" :disabled="busy" @click="restart"><RotateCw :size="17" aria-hidden="true"/>{{ busy ? '正在重启…' : '重启并返回原目录' }}</button>
    </section>

    <div v-if="error" class="storage-error" data-testid="storage-error" role="alert"><TriangleAlert :size="18" aria-hidden="true"/><p>{{ error }}</p><button v-if="!status" class="garden-btn sm" data-testid="storage-retry" :disabled="busy" @click="refresh">重试</button></div>
  </article>
</template>

<style scoped>
.storage-settings{min-width:0;color:var(--g-text)}
.storage-settings.first-use{width:min(760px,100%);padding:30px 34px;background:linear-gradient(140deg,#fffdf6,#fff 58%,#f0fdf4)}
.storage-heading{display:flex;align-items:center;gap:18px;margin-bottom:20px}
.storage-heading h2{margin:0 0 7px;display:flex;align-items:center;gap:8px;font-size:19px;line-height:1.4}
.storage-heading p{margin:0;color:var(--g-text-2);font-size:14px;line-height:1.75}
.first-use .storage-heading h2{font-size:25px;letter-spacing:.02em}
.storage-eyebrow{display:block;margin-bottom:5px;color:var(--g-leaf-3);font-size:12px;font-weight:700;letter-spacing:.08em}
.storage-mark{width:60px;height:60px;flex:none;display:grid;place-items:center;border-radius:50% 50% 50% 18px;color:#fff;background:linear-gradient(140deg,var(--g-leaf),var(--g-leaf-2) 65%,#d4b84c);box-shadow:0 10px 20px -10px #22c55e70}
.storage-root{padding:14px 16px;border:1px solid #d7ecde;border-radius:16px;background:#f5faf6;margin-bottom:18px}
.storage-root>span{display:block;margin-bottom:5px;color:var(--g-text-3);font-size:12px}
.storage-root code{font-size:13px;line-height:1.6;color:var(--g-ink);user-select:text}
.storage-settings .storage-locations{margin:0 0 20px;font-size:13px;line-height:1.65;grid-template-columns:repeat(2,minmax(0,140px) minmax(0,1fr));column-gap:16px}
.storage-locations dd{font-family:Consolas,'Cascadia Mono',monospace;font-size:12.5px;color:var(--g-text-2)}
@media (max-width:1100px){.storage-settings .storage-locations{grid-template-columns:140px 1fr}}
.storage-locations dd,.storage-transfer dd,.storage-confirm dd{user-select:text}
.storage-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:16px}
.storage-editor{margin-top:2px}
.storage-editor label{display:block;margin-bottom:7px;font-weight:600;font-size:14px;color:var(--g-ink)}
.storage-path-input{display:flex;gap:8px}
.storage-path-input input{flex:1;min-width:0;height:42px;padding:0 13px;border:1px solid var(--g-line-2);border-radius:12px;background:#fff;color:var(--g-text);font-size:14px}
.storage-path-input input::placeholder{color:var(--g-text-3)}
.storage-hint{font-size:13px;line-height:1.7;color:var(--g-text-3);margin:9px 0 0;overflow-wrap:anywhere}
.storage-confirm{padding:18px 20px;border:1px solid #fde68a;border-radius:18px;background:#fffcef}
.storage-confirm h3{font-size:16px;margin:0 0 13px}
.storage-confirm ul{margin:12px 0 0;padding-left:19px;font-size:13.5px;line-height:1.8;color:var(--g-text-2)}
.storage-confirm li+li{margin-top:4px}
.storage-blocked,.storage-error{display:flex;align-items:flex-start;gap:8px;margin-top:14px;font-size:13.5px;line-height:1.7;color:#92400e}
.storage-blocked svg,.storage-error svg{flex:none;margin-top:3px}
.storage-error{padding:12px 14px;border:1px solid #fed7aa;background:#fff7ed;border-radius:12px;color:#9a3412}
.storage-error p{margin:0;overflow-wrap:anywhere;flex:1}
.storage-loading{display:flex;align-items:center;gap:8px;font-size:14px;color:var(--g-text-3)}
.storage-transfer{max-width:720px;margin:0 auto;padding:8px 0}
.storage-transfer>.storage-mark{margin-bottom:20px}
.storage-transfer h2{font-size:22px;line-height:1.5;margin:0 0 12px}
.storage-transfer>p:not(.storage-hint){margin:0 0 14px;font-size:14px;line-height:1.7;color:var(--g-text-2)}
.storage-transfer .storage-do-not-close{display:flex;align-items:center;gap:7px;width:fit-content;color:#92400e!important;border:1px solid #fde68a;border-radius:10px;background:#fffbeb;padding:7px 11px;font-weight:600}
.storage-transfer .garden-facts{margin:20px 0;font-size:13.5px;line-height:1.7}
.storage-transfer>.garden-btn{margin-top:8px}
.storage-progress{display:flex;gap:14px;align-items:center;margin:22px 0 8px}
.storage-progress progress{flex:1;min-width:0;height:12px;appearance:none;border:0;border-radius:10px;overflow:hidden;background:#dcfce7;accent-color:var(--g-leaf-2)}
.storage-progress progress::-webkit-progress-bar{background:#dcfce7;border-radius:10px}
.storage-progress progress::-webkit-progress-value{background:linear-gradient(90deg,#22c55e,#10b981);border-radius:10px}
.storage-progress strong{min-width:4em;font-size:14px;color:var(--g-ink);text-align:right;font-variant-numeric:tabular-nums}
.storage-mark.app-mark{width:68px;height:68px;border-radius:0;background:none;box-shadow:none;filter:drop-shadow(0 10px 14px rgba(20,83,45,.25))}
.failed>.storage-mark{background:linear-gradient(140deg,#fbbf24,#f59e0b);color:#78350f;box-shadow:none}
.storage-transfer .storage-failure-message{color:#9a3412!important;overflow-wrap:anywhere;white-space:pre-wrap}
.storage-transfer>p.storage-result-message{padding:12px 14px;border:1px solid #fde68a;border-radius:12px;background:#fffbeb;color:#92400e;white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:640px){.storage-settings.first-use{padding:24px 20px}.storage-heading{align-items:flex-start;gap:12px}.storage-mark{width:46px;height:46px}.first-use .storage-heading h2{font-size:21px}.storage-path-input{flex-wrap:wrap}.storage-path-input input{flex-basis:calc(100% - 100px)}.storage-confirm{padding:15px}.storage-settings .garden-facts{grid-template-columns:76px minmax(0,1fr)}}
</style>
