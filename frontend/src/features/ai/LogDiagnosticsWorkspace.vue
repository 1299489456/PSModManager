<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { createLocalAiRuntime } from './localAiRuntime.js'
import { createLogDiagnosticsRuntime, MAX_CACHED_PAGES } from './logDiagnosticsRuntime.js'
import { createAiActionRuntime } from './aiActionRuntime.js'
import { diagnosticMarkdown } from './diagnosticMarkdown.js'
import { safeWebLink } from '../../shared/lib/safeMarkdown.js'
import PlanCard from './PlanCard.vue'
import { Download, MessageSquarePlus, Paperclip, SendHorizontal, Sprout } from 'lucide-vue-next'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null }, autoAnalyzeToken: { type: Number, default: 0 }, active: { type: Boolean, default: true } })
const emit = defineEmits(['mod-changed'])
const state = reactive({}), logs = reactive({}), actionState = reactive({})
const settingsOpen = ref(false), sourceOpen = ref(false), attachOpen = ref(false), search = ref(''), onlyErrors = ref(false), messageList = ref(null)
const actions = createAiActionRuntime(props.bridge, actionState, () => emit('mod-changed'))
const runtime = createLocalAiRuntime(props.bridge, state, { evidencePicker: false, onSuggestions: actions.setSuggestions, onReset: actions.clear, isActionBusy: () => actionState.busy })
const diagnostics = createLogDiagnosticsRuntime(props.bridge, runtime, state, logs)
const working = computed(() => state.busy || actionState.busy)
const blocked = computed(() => working.value || logs.loading || logs.reading)
const excerpt = computed(() => logs.preview?.logs.find(row => row.source_id === logs.activeSourceId))
// Original view: every loaded page, oldest first, read as one continuous log.
const loaded = computed(() => {
  const pages = [...logs.pages].reverse()
  if (!pages.length) return null
  const first = pages[0], last = pages.at(-1)
  return { text: pages.map(page => page.text.endsWith('\n') ? page.text : page.text + '\n').join('').replace(/\n$/, ''), byte_start: first.byte_start, byte_end: last.byte_end,
    bytes_total: last.bytes_total ?? logs.pages[0].bytes_total, reachedStart: first.previous_before === null, skipped_long_line: pages.some(page => page.skipped_long_line) }
})
const activeLog = computed(() => logs.viewMode === 'analysis' ? excerpt.value : loaded.value)
const lines = computed(() => (activeLog.value?.text || '').split('\n').map((text, index) => ({ text, number: activeLog.value?.line_numbers?.[index] || (activeLog.value?.reachedStart === false ? '' : index + 1), error: /\b(error|exception|fatal|failed|failure)\b|错误|异常|失败/i.test(text) }))
  .filter(row => (!onlyErrors.value || row.error) && (!search.value || row.text.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()))))
const canLoadOlder = computed(() => logs.viewMode === 'original' && Boolean(loaded.value) && !loaded.value.reachedStart && logs.pages.length < MAX_CACHED_PAGES)
const logScroll = ref(null)
let pinnedToEnd = true
async function loadOlder() {
  const list = logScroll.value
  if (!list || !canLoadOlder.value || logs.pageReading) return
  const before = list.scrollHeight
  if (await diagnostics.loadOlder()) {
    await nextTick()
    // Keep the lines the reader was looking at in place after prepending.
    list.scrollTop += list.scrollHeight - before
  }
}
function logScrolled() {
  const list = logScroll.value
  if (!list) return
  pinnedToEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 40
  if (list.scrollTop < 240) void loadOlder()
}
async function fillAndPin() {
  await nextTick()
  const list = logScroll.value
  if (!list || logs.viewMode !== 'original') return
  if (pinnedToEnd) list.scrollTop = list.scrollHeight
  // Short pages: keep reading until the view can scroll.
  if (list.scrollHeight <= list.clientHeight + 240 && canLoadOlder.value) await loadOlder()
}
watch(() => [logs.activeSourceId, logs.viewMode], () => { pinnedToEnd = true; void fillAndPin() })
watch(() => logs.pages.length, (count, previous) => { if (!previous || count < previous) { pinnedToEnd = true }; void fillAndPin() })
const hasLogContent = computed(() => logs.preview?.logs.some(row => row.text.trim()))
const context = computed(() => logs.preview?.context || {})
const attachLabel = computed(() => state.diagnosticSnapshotId ? `已附带 ${logs.preview?.logs.length || 0} 份日志${logs.includeContext && logs.installationId ? ' + Mod 状态' : ''}` : '未附带日志')
const taskStatus = computed(() => state.cancelling ? '正在取消…' : state.taskKind === 'test' ? '正在测试连接…' : state.querying ? 'AI 正在查询 Mod 与环境信息…' : state.streamedText ? 'AI 正在生成分析…' : '正在等待 AI 回复…')
const keyStatus = computed(() => ({ saved: '密钥已保存在 Windows 凭据', session: '密钥仅供本次会话使用', unavailable: 'Windows 凭据不可读', missing: '未保存密钥' })[state.settings?.key_status] || '')
const modState = value => ({ enabled: '启用', disabled: '停用', not_installed: '未安装', drifted: '文件有变化', unknown: '状态未知' })[value] || value
const sizeText = value => value < 1024 ? `${value} B` : `${(value / 1024).toFixed(1)} KB`
async function analyze() { if (!state.settings?.configured) settingsOpen.value = true; await diagnostics.analyze() }
async function send() { if (!state.settings?.configured) { settingsOpen.value = true; state.error = '先配置 AI 服务和模型。'; return }; await runtime.send() }
async function saveSettings() { if (await runtime.saveSettings()) settingsOpen.value = false }
async function openTextLink(event) {
  const anchor = event.target.closest?.('a')
  if (!anchor) return
  event.preventDefault()
  const url = safeWebLink(anchor.getAttribute('href'))
  if (url) {
    try { await props.bridge.request('nexus.open', { page: 'link', url }) }
    catch { state.error = '这个链接无法打开。' }
  }
}
watch(() => props.installation?.installation_id, () => diagnostics.load(props.installation), { immediate: true })
watch(() => props.autoAnalyzeToken, () => { if (!blocked.value) void diagnostics.load(props.installation) })
const closeAttach = event => { if (attachOpen.value && !event.target.closest?.('.attach')) attachOpen.value = false }
let keepLatestReply = true, replyReadOffset = 0, scrollFrame = 0, listWidth = 0, listHeight = 0, resizeObserver, disposed = false
const latestReply = list => [...list.querySelectorAll('.message.assistant')].at(-1)
function rememberReplyPosition() {
  const list = messageList.value
  // Hidden/resizing containers can emit scroll events without a reader scrolling.
  if (!props.active || scrollFrame || !list?.clientHeight || list.clientWidth !== listWidth || list.clientHeight !== listHeight) return
  const reply = latestReply(list)
  if (!reply) return
  const viewport = list.getBoundingClientRect(), bounds = reply.getBoundingClientRect()
  keepLatestReply = bounds.top < viewport.bottom - 64 && bounds.bottom > viewport.top + 24
  replyReadOffset = Math.max(0, viewport.top + 12 - bounds.top)
}
async function restoreReplyPosition(fromStart = false) {
  if (fromStart) { keepLatestReply = true; replyReadOffset = 0 }
  if (!props.active || !keepLatestReply || disposed) return
  if (scrollFrame > 0) cancelAnimationFrame(scrollFrame)
  scrollFrame = -1
  await nextTick()
  if (disposed) return
  if (scrollFrame > 0) cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    const list = messageList.value
    if (props.active && list?.clientHeight) {
      const reply = latestReply(list)
      if (reply) list.scrollTop += reply.getBoundingClientRect().top - list.getBoundingClientRect().top - 12 + replyReadOffset
      else if (!state.messages.length && !state.streamedText) list.scrollTop = 0
    }
    // Ignore the scroll event caused by the restoration itself.
    scrollFrame = requestAnimationFrame(() => { scrollFrame = 0 })
  })
}
watch(() => state.streamedText, (text, previous) => {
  // Reveal the new reply once, then leave the reader's scroll position alone.
  if (text && !previous) void restoreReplyPosition(true)
})
watch(() => state.busy, (busy, previous) => { if (!busy && previous) void restoreReplyPosition() })
watch(() => state.messages, () => {
  if (!state.messages.length && !state.streamedText) { keepLatestReply = true; replyReadOffset = 0 }
  if (!state.busy) void restoreReplyPosition()
})
watch(() => props.active, active => { if (active) void restoreReplyPosition() })
onMounted(() => {
  document.addEventListener('pointerdown', closeAttach)
  void runtime.loadSettings()
  resizeObserver = new ResizeObserver(() => {
    const list = messageList.value
    if (!list?.clientWidth || !list.clientHeight) return
    if (list.clientWidth === listWidth && list.clientHeight === listHeight) return
    listWidth = list.clientWidth; listHeight = list.clientHeight
    void restoreReplyPosition()
  })
  resizeObserver.observe(messageList.value)
})
onUnmounted(() => {
  disposed = true
  document.removeEventListener('pointerdown', closeAttach)
  resizeObserver?.disconnect()
  if (scrollFrame > 0) cancelAnimationFrame(scrollFrame)
  diagnostics.dispose(); runtime.dispose(); actions.dispose()
})
</script>

<template>
  <main class="diagnostic-workspace" data-testid="ai-panel" :aria-busy="blocked">
    <header class="workspace-heading">
      <h2>日志</h2>
      <p>查看游戏和 PSMM 的日志，需要时让 AI 帮你找出原因。</p>
    </header>
    <div class="workspace-columns">
      <section class="log-pane" aria-label="日志阅读">
        <div class="pane-heading log-heading"><div v-if="logs.preview?.logs.length" class="log-tabs" role="tablist" aria-label="日志文件"><button v-for="log in logs.preview.logs" :key="log.source_id" role="tab" :aria-selected="logs.activeSourceId === log.source_id" :class="{ active: logs.activeSourceId === log.source_id }" @click="diagnostics.viewSource(log.source_id)">{{ log.label }}</button></div><h3 v-else>日志文件</h3><button data-testid="diagnostic-sources-toggle" :aria-expanded="sourceOpen" @click="sourceOpen = !sourceOpen">{{ logs.selectedIds.length }} 个来源 <span aria-hidden="true">⌄</span></button><button data-testid="diagnostic-refresh" :disabled="blocked" @click="diagnostics.load(installation)">{{ logs.loading || logs.reading ? '读取中…' : '刷新日志' }}</button></div>
        <section v-if="sourceOpen" class="source-picker" aria-label="选择日志来源">
          <label v-for="source in logs.sources" :key="source.source_id" class="source-option"><input type="checkbox" :data-testid="`diagnostic-source-${source.kind}`" :checked="logs.selectedIds.includes(source.source_id)" :disabled="blocked || source.status !== 'available'" @change="diagnostics.toggleSource(source.source_id, $event.target.checked)"/><span>{{ source.label }}<small>{{ source.relative_path }}</small></span><small>{{ source.status === 'missing' ? '尚未生成' : source.status === 'unreadable' ? '无法读取' : sizeText(source.bytes_total) }}</small></label>
          <p class="muted">每次最多选择 3 份日志。日志有更新时点击“刷新日志”。</p>
        </section>
        <div class="log-control-bar">
          <div class="log-tools"><input v-model="search" type="search" placeholder="在已加载的日志中查找" aria-label="查找日志" data-testid="diagnostic-search"/><label class="checkbox"><input v-model="onlyErrors" type="checkbox"/>只看错误</label></div>
          <div class="log-view-options"><div class="log-mode-switch"><button :class="{ active: logs.viewMode === 'original' }" @click="logs.viewMode = 'original'">完整日志</button><button data-testid="diagnostic-analysis-material" :class="{ active: logs.viewMode === 'analysis' }" @click="logs.viewMode = 'analysis'">发送给 AI 的内容</button></div><span v-if="logs.viewMode === 'original' && logs.pageReading" role="status">正在读取…</span></div>
        </div>
        <p v-if="activeLog" class="log-range" data-testid="diagnostic-range"><template v-if="logs.viewMode === 'analysis'">{{ activeLog.selection_mode === 'error_context' ? `包含 ${activeLog.error_matches} 处错误及上下文` : '日志末尾内容' }} · 读取 {{ sizeText(activeLog.scanned_bytes) }} / {{ sizeText(activeLog.bytes_total) }}{{ activeLog.truncated ? ' · 内容已截断' : '' }} · 行号对应发送内容</template><template v-else>已加载 {{ sizeText(activeLog.byte_end - activeLog.byte_start) }} / {{ sizeText(activeLog.bytes_total) }}{{ activeLog.reachedStart ? ' · 已到开头' : ' · 向上滚动加载更早内容' }}</template> · 已隐藏个人信息</p>
        <div ref="logScroll" class="log-scroll" data-testid="diagnostic-log-text" tabindex="0" aria-label="日志内容" @scroll.passive="logScrolled">
          <p v-if="logs.viewMode === 'original' && activeLog && lines.length" class="log-edge" data-testid="diagnostic-log-edge" role="status">{{ logs.pageReading ? '正在加载更早的日志…' : activeLog.reachedStart ? '已到日志开头' : logs.pages.length >= MAX_CACHED_PAGES ? '已加载的内容较多，更早的部分请在日志文件中查看' : '向上滚动加载更早的日志' }}</p>
          <p v-if="logs.loading || logs.reading || (logs.pageReading && !logs.pages.length)" class="empty-state" role="status">正在读取日志…</p>
          <p v-else-if="!activeLog" class="empty-state">{{ logs.sources.length ? '选择日志来源后即可查看和分析。' : '还没有可用的日志。选择游戏后可读取 MelonLoader 日志，管理器日志随时可以查看。' }}</p>
          <p v-else-if="!activeLog.text.trim()" class="empty-state">这份日志还是空的。运行游戏后刷新，或切换日志来源。</p>
          <p v-else-if="!lines.length" class="empty-state">没有匹配的日志行。清除搜索或关闭“只看错误”。</p>
          <div v-for="line in lines" v-else :key="line.number" class="log-line" :class="{ 'error-line': line.error }"><span class="line-number" aria-hidden="true">{{ line.number }}</span><code>{{ line.text || ' ' }}</code></div>
        </div>
        <p v-if="activeLog?.skipped_long_line" class="warning">部分超长的行无法完整显示，已略去。</p>
        <p v-if="logs.error" class="error" data-testid="diagnostic-error" role="alert">{{ logs.error }}</p>
        <p v-for="warning in [...logs.warnings, ...(logs.preview?.warnings || [])]" :key="warning" class="warning">{{ warning }}</p>
      </section>
      <section class="conversation-pane" aria-label="智能分析对话">
        <div class="pane-heading conversation-heading"><h3>智能分析</h3><button class="model-chip" data-testid="ai-settings-toggle" :aria-expanded="settingsOpen" :title="state.settings?.base_url || ''" @click="settingsOpen = !settingsOpen"><span data-testid="ai-target">{{ state.settings?.configured ? state.settings.model : '配置 AI 服务' }}</span><span aria-hidden="true">{{ settingsOpen ? '⌃' : '⌄' }}</span></button><span class="heading-space"></span><button class="ghost" data-testid="ai-new-session" :disabled="actionState.busy" title="开始新对话" @click="runtime.newSession"><MessageSquarePlus :size="16" aria-hidden="true"/>新对话</button><button class="ghost" data-testid="ai-export" :disabled="!state.sessionId || working" title="导出对话" @click="runtime.exportSession"><Download :size="16" aria-hidden="true"/>导出</button><button class="primary" data-testid="diagnostic-analyze" :disabled="blocked || !hasLogContent" @click="analyze">分析日志</button></div>
      <section v-if="settingsOpen" class="connection-settings" aria-label="AI 连接设置">
        <div class="settings-grid">
          <label>服务地址<input v-model="state.form.base_url" data-testid="ai-base-url" type="url" maxlength="2048" :disabled="working || state.saving" autocomplete="off" spellcheck="false"/></label>
          <label>模型<input v-model="state.form.model" data-testid="ai-model" maxlength="200" :disabled="working || state.saving" placeholder="服务支持的模型名称" autocomplete="off" spellcheck="false"/></label>
          <label>API key<input v-model="state.form.api_key" data-testid="ai-api-key" type="password" maxlength="4096" :disabled="working || state.saving" placeholder="留空保留密钥；本地免密服务可留空" autocomplete="new-password"/></label>
        </div>
        <div class="settings-actions"><label class="checkbox"><input v-model="state.form.remember" data-testid="ai-remember" type="checkbox" :disabled="working || state.saving"/>记住密钥（Windows 凭据）</label><span>{{ keyStatus }}</span><button data-testid="ai-save" :disabled="working || state.saving" @click="saveSettings">{{ state.saving ? '保存中…' : '保存设置' }}</button><button data-testid="ai-test" :disabled="!state.settings?.configured || working || state.saving" @click="runtime.testConnection">测试连接</button><button data-testid="ai-clear-key" :disabled="!state.settings || working || state.saving" @click="runtime.clearCredentials">清除密钥</button></div>
        <p class="muted">支持 OpenAI 兼容协议和本地服务。保存设置不会连接服务。</p>
      </section>
        <div v-if="!state.settings?.configured && !state.loading" class="configure-prompt"><span>配置 AI 服务后，直接分析左侧日志。</span><button @click="settingsOpen = true">配置服务</button></div>
        <div ref="messageList" class="conversation-scroll" data-testid="ai-messages" :data-session-id="state.sessionId || ''" aria-live="polite" aria-relevant="additions text" @scroll.passive="rememberReplyPosition" @click="openTextLink">
          <div v-if="!state.messages.length && !state.pendingQuestion && !state.streamedText" class="conversation-empty"><h4>遇到了什么问题？</h4><p>分析左侧日志，或描述游戏、Mod、管理器的异常。</p></div>
          <article v-for="(message, index) in state.messages" :key="index" class="message" :class="message.role" :data-role="message.role" :data-testid="message.role === 'assistant' ? 'ai-response' : 'ai-user-message'"><span v-if="message.role === 'assistant'" class="message-avatar" aria-hidden="true"><Sprout :size="16"/></span><div class="bubble"><strong :class="{ 'sr-only': message.role === 'user' }">{{ message.role === 'user' ? '你' : 'AI' }}</strong><div class="formatted-reply" v-html="diagnosticMarkdown(message.content)"></div></div></article>
          <article v-if="state.pendingQuestion" class="message user" data-testid="ai-user-message"><div class="bubble"><strong class="sr-only">你</strong><p>{{ state.pendingQuestion }}</p></div></article>
          <article v-if="state.streamedText" class="message assistant" data-role="assistant" data-testid="ai-response"><span class="message-avatar" aria-hidden="true"><Sprout :size="16"/></span><div class="bubble"><strong>{{ state.taskKind === 'test' ? '连接测试' : 'AI' }}</strong><div class="formatted-reply" v-html="diagnosticMarkdown(state.streamedText)"></div></div></article>
          <p v-if="state.incompleteReply" class="warning reply-note">回复中断，以上为已收到的内容。</p>
          <section v-if="actionState.cards.length" class="suggestions" data-testid="ai-suggestions"><h4>建议操作</h4><PlanCard v-for="card in actionState.cards" :key="card.suggestion_id" :suggestion="card" :busy="working" :active="actionState.activeId === card.suggestion_id" :cancelling="actionState.cancelling" @execute="actions.execute" @cancel="actions.cancel"/></section>
        </div>
        <div v-if="state.busy || state.error || state.notice || state.retryKind || (state.incompleteReply && state.sessionId)" class="conversation-status">
          <div v-if="state.busy" class="ai-progress" data-testid="ai-progress" role="status"><span class="progress-dot"></span>{{ taskStatus }}<button data-testid="ai-cancel" :disabled="state.cancelling" @click="runtime.cancel">停止</button></div>
          <p v-if="state.error" class="error" data-testid="ai-error" role="alert">{{ state.error }}</p><p v-else-if="state.notice && !state.busy" class="notice" data-testid="ai-notice" role="status">{{ state.notice }}</p>
          <div v-if="state.retryKind || (state.incompleteReply && state.sessionId)" class="status-actions"><button v-if="state.incompleteReply && state.sessionId" class="retry-button primary" data-testid="ai-continue" :disabled="!runtime.canContinue()" @click="runtime.continueReply">继续分析</button><button v-if="state.retryKind" class="retry-button" data-testid="ai-retry" :disabled="!runtime.canRetry()" @click="runtime.retry">重试原请求</button></div>
        </div>
        <form class="composer" @submit.prevent="send"><div class="composer-box"><label for="diagnostic-question" class="sr-only">向 AI 提问</label><textarea id="diagnostic-question" v-model="state.question" data-testid="ai-question" maxlength="2000" rows="2" :disabled="working" placeholder="描述遇到的问题，或继续追问分析结果…" @keydown.ctrl.enter.prevent="send"></textarea><div class="composer-actions">
          <details class="attach" data-testid="diagnostic-context" :open="attachOpen" @toggle="attachOpen = $event.target.open"><summary :class="{ ready: state.diagnosticSnapshotId }"><Paperclip :size="14" aria-hidden="true"/>{{ attachLabel }}</summary>
            <div class="attach-panel">
              <h4>发送给 AI 的内容</h4>
              <div class="attach-choice"><button v-if="state.diagnosticSnapshotId" type="button" :disabled="blocked" @click="diagnostics.chatOnly(); attachOpen = false">不附带日志</button><button v-else-if="logs.preview" type="button" class="primary" :disabled="blocked" @click="runtime.setDiagnostic(logs.preview.snapshot_id); attachOpen = false">附带当前日志</button><span v-if="logs.preview?.captured_at" class="muted">日志读取于 {{ new Date(logs.preview.captured_at).toLocaleTimeString() }}，刷新日志会开始新对话。</span></div>
              <label class="checkbox"><input type="checkbox" :checked="logs.includeContext" :disabled="blocked || !logs.installationId" data-testid="ai-allow-queries" @change="diagnostics.setContext($event.target.checked)"/>附带游戏、MelonLoader 和 Mod 状态，允许 AI 查询并给出操作建议</label>
              <dl v-if="context.installation"><dt>游戏</dt><dd>{{ context.installation.game }} {{ context.installation.game_version || '版本未识别' }}</dd><dt>MelonLoader</dt><dd>{{ context.installation.loader_version || '版本未识别' }}</dd><dt>Unity</dt><dd>{{ context.installation.unity_version || '版本未识别' }}</dd></dl>
              <ul v-if="context.mods?.items?.length" class="context-mods"><li v-for="(mod, index) in context.mods.items" :key="index"><span>{{ mod.display_name }} {{ mod.version_raw }}</span><span>{{ modState(mod.state) }}</span></li></ul>
              <p v-if="context.mods" class="muted">{{ context.mods.scope }}{{ context.mods.truncated ? '，当前列表已截断。' : '。' }}</p>
              <p class="muted">每份日志从末尾 256 KB 中优先选取错误及上下文，最多 8 KB / 240 行。左侧切到“发送给 AI 的内容”可以看到实际发送的文字。</p>
            </div>
          </details>
          <span class="composer-hint">Ctrl + Enter 发送</span><button type="submit" class="primary send" data-testid="ai-send" :disabled="!runtime.canSend() || logs.reading || logs.loading" aria-label="发送"><SendHorizontal :size="16" aria-hidden="true"/>发送</button></div></div></form>
      </section>
    </div>
    <section v-if="state.exportOpen" class="export-overlay" role="dialog" aria-modal="true" aria-label="导出对话" @click.self="state.exportOpen = false"><div><header><h3>导出对话</h3><button @click="state.exportOpen = false">关闭</button></header><textarea data-testid="ai-export-text" readonly :value="state.exportText" aria-label="可复制的对话内容"></textarea></div></section>
  </main>
</template>

<style scoped>
.log-pane{position:relative}.source-picker{position:absolute;top:56px;left:10px;right:10px;z-index:20;background:rgb(var(--rgb-bg-surface));border:1px solid rgb(var(--rgb-border-base)/.2);border-radius:10px;box-shadow:0 12px 28px #0006}.diagnostic-workspace .log-scroll,.diagnostic-workspace .conversation-scroll{min-height:0}
.diagnostic-workspace{position:relative}.connection-settings{position:absolute;top:80px;left:26px;right:26px;z-index:30;background:rgb(var(--rgb-bg-surface));box-shadow:0 16px 42px #0007;max-height:calc(100% - 110px)}
.log-line code{font-size:13px}.formatted-reply :deep(p code),.formatted-reply :deep(li code){display:inline;white-space:normal}
.log-view-options,.log-pagination{display:flex;align-items:center;gap:6px;padding:0 16px 10px;flex-wrap:wrap}.log-view-options button,.log-pagination button{font-size:12px;padding:5px 8px}.log-view-options .active{background:rgb(var(--rgb-accent-primary)/.1);border-color:rgb(var(--rgb-accent-primary)/.45)}.log-view-options>span{font-size:11px;color:rgb(var(--rgb-text-dim));margin-left:auto}.log-pagination{padding-top:8px;padding-bottom:8px;border-top:1px solid rgb(var(--rgb-border-base)/.12)}.log-pagination button:last-child{margin-left:auto}
.diagnostic-workspace{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column;padding:22px 26px 18px;color:rgb(var(--rgb-text-main));font-size:14px;overflow:hidden;gap:16px}.workspace-heading,.pane-heading,.settings-actions,.analysis-bar,.composer-actions,.ai-progress{display:flex;align-items:center;gap:10px}.workspace-heading{justify-content:space-between}.workspace-heading h2{font-size:23px;letter-spacing:-.4px;margin:0 0 5px}.workspace-heading p{margin:0;color:rgb(var(--rgb-text-dim))}.pane-heading{padding:15px 16px 12px;flex-wrap:wrap}.pane-heading h3{font-size:16px;margin:0 auto 0 0}.workspace-columns{min-height:0;flex:1;display:grid;grid-template-columns:minmax(0,1.08fr) minmax(0,1fr);gap:18px}.log-pane,.conversation-pane{min-height:0;min-width:0;display:flex;flex-direction:column;background:rgb(var(--rgb-bg-surface)/.6);border:1px solid rgb(var(--rgb-border-base)/.16);border-radius:14px;overflow:hidden}button,input,textarea{font:inherit;color:inherit;border:1px solid rgb(var(--rgb-border-base)/.22);border-radius:8px;background:rgb(var(--rgb-bg-overlay)/.035);padding:8px 11px}button{cursor:pointer;white-space:nowrap}button:hover{background:rgb(var(--rgb-bg-overlay)/.09)}button:disabled,input:disabled,textarea:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid rgb(var(--rgb-accent-primary));outline-offset:2px}.primary{background:rgb(var(--rgb-accent-primary)/.17);border-color:rgb(var(--rgb-accent-primary)/.48);font-weight:600}.primary:hover{background:rgb(var(--rgb-accent-primary)/.25)}.checkbox{display:flex;align-items:center;gap:7px;line-height:1.5}.checkbox input{width:auto;margin:0;accent-color:rgb(var(--rgb-accent-primary))}.muted,.send-target{font-size:12px;color:rgb(var(--rgb-text-dim));line-height:1.65}.connection-settings{border:1px solid rgb(var(--rgb-border-base)/.18);border-radius:10px;padding:14px 16px;max-height:36vh;overflow:auto}.settings-grid{display:grid;grid-template-columns:1.2fr .8fr 1fr;gap:12px}.settings-grid label{display:grid;gap:6px}.settings-grid input{width:100%;min-width:0}.settings-actions{flex-wrap:wrap;margin-top:12px}.settings-actions>span{color:rgb(var(--rgb-text-dim));font-size:12px;margin-right:auto}.connection-settings>p{margin:8px 0 0}.source-picker{border-block:1px solid rgb(var(--rgb-border-base)/.12);padding:8px 16px;max-height:220px;overflow:auto}.source-option{display:flex;align-items:center;gap:9px;padding:8px 0}.source-option>span{flex:1;min-width:0}.source-option small{display:block;color:rgb(var(--rgb-text-dim));font-size:12px;overflow-wrap:anywhere}.source-option input{accent-color:rgb(var(--rgb-accent-primary))}.log-tabs{display:flex;gap:6px;overflow-x:auto;padding:0 16px 10px}.log-tabs button{font-size:13px;padding:7px 9px}.log-tabs .active{border-color:rgb(var(--rgb-accent-primary)/.45);background:rgb(var(--rgb-accent-primary)/.1);color:rgb(var(--rgb-accent-primary))}.log-tools{display:flex;gap:12px;align-items:center;padding:0 16px 8px}.log-tools input[type=search]{min-width:0;flex:1}.log-tools .checkbox{white-space:nowrap;font-size:12px}.log-range{margin:0 16px 10px;font-size:12px;color:rgb(var(--rgb-text-dim));line-height:1.5}.log-scroll{flex:1;min-height:160px;overflow:auto;background:rgb(var(--rgb-bg-inset)/.6);padding:12px 0;user-select:text}.log-line{display:flex;align-items:flex-start;line-height:1.7;font-size:13px;min-height:22px}.line-number{flex:none;user-select:none;color:rgb(var(--rgb-text-dim)/.6);width:44px;text-align:right;padding-right:12px;font-variant-numeric:tabular-nums}.log-line code{font-family:Consolas,'Cascadia Code',monospace;white-space:pre-wrap;overflow-wrap:anywhere;min-width:0;padding-right:14px}.error-line{background:rgb(var(--rgb-accent-danger)/.07)}.error-line code{color:rgb(var(--rgb-accent-danger))}.empty-state{padding:28px 24px;line-height:1.8;color:rgb(var(--rgb-text-dim));margin:0}.context-summary{padding:13px 16px;border-top:1px solid rgb(var(--rgb-border-base)/.12);max-height:32%;overflow:auto}.context-summary summary{cursor:pointer;font-size:13px;font-weight:600}.context-summary .checkbox{font-size:13px;margin:12px 0}.context-summary dl{display:grid;grid-template-columns:85px 1fr;gap:7px;font-size:13px}.context-summary dt{color:rgb(var(--rgb-text-dim))}.context-summary dd{margin:0}.context-mods{list-style:none;padding:0;margin:12px 0}.context-mods li{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:4px 0}.configure-prompt{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 16px 10px;padding:10px 12px;background:rgb(var(--rgb-accent-primary)/.08);border-radius:9px;font-size:13px}.configure-prompt button{font-size:12px}.analysis-bar{padding:0 16px 12px;border-bottom:1px solid rgb(var(--rgb-border-base)/.12);justify-content:space-between}.analysis-bar>span{color:rgb(var(--rgb-text-dim));font-size:12px}.analysis-bar .scope-ready{color:rgb(var(--rgb-accent-primary))}.conversation-scroll{flex:1;min-height:140px;overflow:auto;padding:18px 18px 10px;user-select:text}.conversation-empty{text-align:center;margin:auto;padding:35px 8px;color:rgb(var(--rgb-text-dim));line-height:1.7}.assistant-symbol{font-size:32px;color:rgb(var(--rgb-accent-primary));margin-bottom:12px}.conversation-empty h4{font-size:16px;color:rgb(var(--rgb-text-main));margin:0 0 10px}.conversation-empty p{font-size:13px;margin:8px 0}.message{margin:0 0 20px;font-size:14px;line-height:1.75}.message>strong{display:block;font-size:12px;color:rgb(var(--rgb-text-dim));margin-bottom:6px}.message.user{padding:12px 14px;border-radius:10px;background:rgb(var(--rgb-accent-primary)/.07)}.message p{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.formatted-reply{overflow-wrap:anywhere;line-height:1.8}.formatted-reply :deep(p){margin:6px 0 12px}.formatted-reply :deep(h1),.formatted-reply :deep(h2),.formatted-reply :deep(h3){font-size:16px;margin:18px 0 8px}.formatted-reply :deep(ul),.formatted-reply :deep(ol){padding-left:22px}.formatted-reply :deep(pre){overflow:auto;white-space:pre-wrap;background:rgb(var(--rgb-bg-inset)/.6);padding:12px;border-radius:8px;font-size:12px}.formatted-reply :deep(code){font-family:Consolas,monospace;font-size:.95em}.formatted-reply :deep(blockquote){border-left:3px solid rgb(var(--rgb-accent-primary)/.5);margin:12px 0;padding-left:12px;color:rgb(var(--rgb-text-dim))}.formatted-reply :deep(a){color:rgb(var(--rgb-accent-primary))}.formatted-reply :deep(table){border-collapse:collapse;font-size:13px;display:block;overflow:auto}.formatted-reply :deep(td),.formatted-reply :deep(th){padding:6px 10px;border:1px solid rgb(var(--rgb-border-base)/.2)}.ai-progress{font-size:12px;padding:8px 16px;color:rgb(var(--rgb-accent-primary))}.ai-progress button{margin-left:auto;font-size:12px}.progress-dot{width:7px;height:7px;flex:none;border-radius:50%;background:currentColor;animation:pulse 1.5s infinite}.composer{padding:10px 16px 0;border-top:1px solid rgb(var(--rgb-border-base)/.12)}.composer textarea{width:100%;box-sizing:border-box;resize:vertical;min-height:80px;max-height:160px;line-height:1.6}.composer-actions{padding-top:8px;gap:8px;justify-content:flex-end;flex-wrap:wrap}.composer-actions>button:not(.primary){font-size:12px;margin-right:auto;padding:5px 7px}.composer-actions>span{font-size:11px;color:rgb(var(--rgb-text-dim))}.send-target{margin:8px 16px 12px;overflow-wrap:anywhere;font-size:11px}.error,.warning,.notice{margin:8px 16px;font-size:13px;line-height:1.6;overflow-wrap:anywhere}.error{color:rgb(var(--rgb-accent-danger))}.warning{color:rgb(var(--rgb-text-dim))}.notice{color:rgb(var(--rgb-accent-success))}.retry-button{align-self:flex-start;margin:0 16px 8px;font-size:12px}.suggestions h4{font-size:14px}.export-overlay{position:fixed;inset:0;background:#0008;z-index:1000;display:grid;place-items:center;padding:24px}.export-overlay>div{width:min(760px,100%);background:rgb(var(--rgb-bg-surface));padding:20px;border-radius:14px}.export-overlay header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.export-overlay h3{margin:0}.export-overlay textarea{width:100%;height:60vh;resize:none;box-sizing:border-box;font-size:13px;line-height:1.7}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@keyframes pulse{50%{opacity:.35}}@media(prefers-reduced-motion:reduce){.progress-dot{animation:none}}@media(max-width:1050px){.diagnostic-workspace{padding:16px}.workspace-columns{gap:12px;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.pane-heading{padding:12px}.log-tools{gap:7px}.composer-actions>span{display:none}}@media(max-width:760px){.diagnostic-workspace{overflow:auto}.workspace-columns{display:flex;flex-direction:column;flex:none}.log-pane{min-height:440px}.conversation-pane{min-height:620px}.settings-grid{grid-template-columns:1fr}.workspace-heading{align-items:flex-start}.workspace-heading h2{font-size:20px}.settings-toggle{font-size:12px;max-width:48%;overflow:hidden;text-overflow:ellipsis}.context-summary{max-height:220px}}
.workspace-heading h2{margin:0}.log-mode-switch{display:flex;gap:6px}.conversation-heading{border-bottom:1px solid rgb(var(--rgb-border-base)/.12);flex-wrap:nowrap;gap:8px}.conversation-heading h3{margin-right:0}.analysis-scope{margin-right:auto;color:rgb(var(--rgb-text-dim));font-size:12px;white-space:nowrap}.analysis-scope.scope-ready{color:rgb(var(--rgb-accent-primary))}.send-target,.composer-actions>span,.log-view-options>span{font-size:12px}.formatted-reply :deep(h1:first-child),.formatted-reply :deep(h2:first-child),.formatted-reply :deep(h3:first-child){margin-top:0}
/* Short desktop windows keep their space for evidence and answers, not chrome. */
@media(max-height:800px) and (min-width:761px){
  .diagnostic-workspace{padding:8px 18px 12px;gap:8px}
  .workspace-heading{min-height:36px;flex-shrink:0}
  .workspace-heading h2{font-size:18px}
  .settings-toggle{padding:6px 10px;font-size:13px}
  .connection-settings{top:50px;left:18px;right:18px;max-height:calc(100% - 64px)}
  .workspace-columns{gap:14px}
  .pane-heading{padding:9px 12px;gap:8px;flex-shrink:0}
  .pane-heading button{padding:5px 9px;line-height:20px}
  .pane-heading h3{font-size:15px}
  .source-picker{top:49px}
  .log-tabs{padding:0 12px 8px;flex-shrink:0}
  .log-tabs button{padding:5px 9px;line-height:20px}
  .log-control-bar{display:grid;grid-template-columns:auto minmax(120px,1fr) auto;align-items:center;gap:9px;padding:0 12px 8px;flex-shrink:0}
  .log-tools,.log-view-options{display:contents}
  .log-mode-switch{grid-column:1;grid-row:1;gap:5px}
  .log-mode-switch button{padding:5px 8px;line-height:20px;font-size:12px}
  .log-tools input[type=search]{grid-column:2;grid-row:1;padding:6px 8px;line-height:20px;font-size:14px;width:100%}
  .log-tools .checkbox{grid-column:3;grid-row:1;font-size:12px}
  .log-view-options>span{display:none}
  .log-range{margin:0 12px 8px;line-height:18px;flex-shrink:0}
  .log-scroll{padding:8px 0}
  .log-pagination{padding:6px 12px;flex-shrink:0}
  .log-pagination button{padding:4px 8px;line-height:20px}
  .context-summary{padding:8px 12px;flex-shrink:0}
  .context-summary summary{line-height:20px}
  .conversation-scroll{padding:12px 14px 8px}
  .conversation-empty{padding:20px 8px}
  .message{margin-bottom:16px}
  .composer{padding:8px 12px 0;flex-shrink:0}
  .composer textarea{height:56px;min-height:56px;max-height:112px;padding:7px 10px;line-height:20px;font-size:14px}
  .composer-actions{padding-top:6px;gap:8px}
  .composer-actions>button{padding:5px 9px;line-height:20px}
  .composer-actions>button:not(.primary){padding:5px 7px;font-size:12px}
  .composer-actions>span{font-size:12px}
  .send-target{margin:4px 12px 8px;line-height:18px;font-size:12px;flex-shrink:0}
  .error,.warning,.notice{margin:6px 12px;font-size:13px;line-height:20px;flex-shrink:0}
}
</style>
