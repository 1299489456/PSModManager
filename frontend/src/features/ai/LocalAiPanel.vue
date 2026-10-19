<script setup>
import { computed, onMounted, onUnmounted, reactive, watch } from 'vue'
import { createLocalAiRuntime } from './localAiRuntime.js'
import { createAiActionRuntime } from './aiActionRuntime.js'
import PlanCard from './PlanCard.vue'

const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null } })
const emit = defineEmits(['mod-changed'])
const state = reactive({})
const actionState = reactive({})
const actions = createAiActionRuntime(props.bridge, actionState, () => emit('mod-changed'))
const runtime = createLocalAiRuntime(props.bridge, state, { onSuggestions: actions.setSuggestions, onReset: actions.clear, isActionBusy: () => actionState.busy })
const working = computed(() => state.busy || actionState.busy)
const keyStatus = computed(() => ({ missing: '未配置密钥', saved: '已保存到 Windows 凭据', session: '仅本次管理器会话使用', unavailable: 'Windows 凭据不可用' })[state.settings?.key_status] || '尚未读取')
const taskStatus = computed(() => state.cancelling || state.task?.state === 'cancelling' ? '正在取消…' : state.taskKind === 'test' ? '正在测试连接…' : state.querying ? '正在查询所选范围…' : '正在等待回复…')
watch(() => props.installation?.installation_id, () => runtime.setInstallation(props.installation), { immediate: true })
onMounted(() => runtime.loadSettings())
onUnmounted(() => { runtime.dispose(); actions.dispose() })
</script>

<template>
  <main class="local-ai" data-testid="ai-panel" :data-state="working ? state.task?.state || 'starting' : state.error ? 'failed' : 'idle'" :aria-busy="state.loading || state.saving || working">
    <header><h2>AI 助手（可选）</h2><p>可提问或粘贴需要解释的文本。AI 设置与 Mod 安装、启停、卸载相互独立。</p></header>
    <section class="ai-settings" aria-label="AI 连接设置">
      <h3>连接设置</h3>
      <div class="settings-grid">
        <label>协议 <input value="OpenAI 兼容协议（含本地服务）" readonly data-testid="ai-provider"/></label>
        <label>服务地址 <input v-model="state.form.base_url" type="url" maxlength="2048" data-testid="ai-base-url" :disabled="state.loading || state.saving || working" spellcheck="false" autocomplete="off"/></label>
        <label>模型 <input v-model="state.form.model" maxlength="200" data-testid="ai-model" :disabled="state.loading || state.saving || working" placeholder="填写服务支持的模型名称" spellcheck="false" autocomplete="off"/></label>
        <label>API key <input v-model="state.form.api_key" type="password" maxlength="4096" data-testid="ai-api-key" :disabled="state.loading || state.saving || working" :placeholder="state.settings?.key_status === 'saved' || state.settings?.key_status === 'session' ? '留空保留当前地址的密钥' : '本地免密服务可留空'" autocomplete="new-password"/></label>
      </div>
      <label class="remember"><input v-model="state.form.remember" type="checkbox" data-testid="ai-remember" :disabled="state.loading || state.saving || working"/>保存到 Windows 凭据；不勾选则只在本次管理器会话使用</label>
      <div class="settings-actions"><button data-testid="ai-save" :disabled="state.loading || state.saving || working" @click="runtime.saveSettings">{{ state.saving ? '正在保存…' : '保存设置' }}</button><button data-testid="ai-clear-key" :disabled="!state.settings || state.loading || state.saving || working" @click="runtime.clearCredentials">清除当前地址的密钥</button><button data-testid="ai-test" :disabled="!state.settings?.configured || state.loading || state.saving || working" @click="runtime.testConnection">测试连接</button><span data-testid="ai-key-status">{{ keyStatus }}</span></div>
      <p class="hint">只有点击“测试连接”、“发送”或“重试”时才联系服务。失败后不会自动重发；连接测试会发送一条固定的短问题。</p>
    </section>
    <section class="ai-conversation" aria-label="AI 会话">
      <div class="conversation-heading"><h3>本次会话</h3><button data-testid="ai-new-session" :disabled="actionState.busy" @click="runtime.newSession">新会话</button><button data-testid="ai-export" :disabled="!state.sessionId || state.busy || state.exporting" @click="runtime.exportSession">查看会话文本</button></div>
      <p class="send-target" data-testid="ai-target">发送目标：{{ state.settings?.base_url || '尚未配置' }} · 模型：{{ state.settings?.model || '尚未配置' }}</p>
      <p class="hint">发送当前问题和下方自选文本；同一会话会携带此前问答。不自动发送包、DLL、存档或全部日志。会话仅保留在本次管理器中。</p>
      <ol class="messages" data-testid="ai-messages" :data-session-id="state.sessionId || ''">
        <li v-for="(message, index) in state.messages" :key="index" :data-role="message.role" :data-testid="message.role === 'assistant' ? 'ai-response' : 'ai-user-message'"><strong>{{ message.role === 'user' ? '你' : 'AI' }}</strong><p class="plain-text">{{ message.content }}</p></li>
        <li v-if="state.pendingQuestion" data-testid="ai-user-message" data-role="user"><strong>你</strong><p class="plain-text">{{ state.pendingQuestion }}</p></li>
        <li v-if="state.streamedText" data-testid="ai-response" data-role="assistant"><strong>{{ state.taskKind === 'test' ? '连接测试' : 'AI' }}</strong><p class="plain-text">{{ state.streamedText }}</p></li>
      </ol>
      <p v-if="state.incompleteReply" class="hint" data-testid="ai-partial-reply">回复尚未完成，已保留接收到的内容。</p>
      <button v-if="state.incompleteReply && state.sessionId" data-testid="ai-continue" :disabled="!runtime.canContinue()" @click="runtime.continueReply">继续分析</button>
      <section v-if="actionState.cards.length" data-testid="ai-suggestions" aria-label="Mod 建议"><h3>Mod 建议</h3><p class="hint">点击动作按钮才会修改当前游戏的 Mod 文件。结果只表示 Mod 文件操作完成。</p><PlanCard v-for="card in actionState.cards" :key="card.suggestion_id" :suggestion="card" :busy="actionState.busy || state.busy" :active="actionState.activeId === card.suggestion_id" :cancelling="actionState.cancelling" @execute="actions.execute" @cancel="actions.cancel"/></section>
      <div v-if="state.busy" class="ai-progress" data-testid="ai-progress" :data-state="state.task?.state || 'starting'" :data-task-id="state.task?.task_id || ''" role="status"><span>{{ taskStatus }}</span><button data-testid="ai-cancel" :disabled="state.cancelling || state.task?.state === 'cancelling'" @click="runtime.cancel">取消</button></div>
      <form class="question-form" @submit.prevent="runtime.send">
        <section class="query-scope" aria-label="可选只读查询">
          <label class="remember"><input type="checkbox" data-testid="ai-allow-queries" :checked="state.allowQueries" :disabled="!state.installationId || actionState.busy" @change="runtime.setAllowQueries($event.target.checked)"/>允许 AI 查询当前游戏和包库，并给出 Mod 操作建议</label>
          <p v-if="!state.installationId" class="hint">先选择游戏安装后，可启用本地查询。普通提问不需要选择游戏。</p>
          <template v-if="state.allowQueries">
            <p class="hint">模型只读查询名称、版本和已支持的配置字段；建议需要你点击按钮执行。日志仅发送下方选中的一个片段，最多 4096 字节；不会发送 DLL、存档或全部日志。切换查询范围会开始新会话。</p>
            <div class="evidence-selector"><label>允许读取的日志片段（可选）<select data-testid="ai-evidence" :value="state.selectedEvidenceId" :disabled="state.evidenceLoading || actionState.busy" @change="runtime.selectEvidence($event.target.value)"><option value="">不发送日志</option><option v-for="entry in state.evidenceEntries" :key="entry.evidence_id" :value="entry.evidence_id">{{ entry.seq }} · {{ entry.label }}</option></select></label><button type="button" data-testid="ai-evidence-refresh" :disabled="state.evidenceLoading || actionState.busy" @click="runtime.refreshEvidence">{{ state.evidenceLoading ? '正在读取…' : '刷新片段' }}</button><button v-if="state.evidenceHasMore" type="button" data-testid="ai-evidence-more" :disabled="state.evidenceLoading || actionState.busy" @click="runtime.moreEvidence">更多片段</button></div>
            <p v-if="!state.evidenceLoading && !state.evidenceEntries.length && !state.evidenceError" class="hint" data-testid="ai-evidence-empty">当前没有本地记录的日志片段，也可继续查询其他信息。</p>
            <p v-if="state.evidenceError" class="ai-error" data-testid="ai-evidence-error" role="alert">{{ state.evidenceError }}</p>
          </template>
        </section>
        <label>当前问题 <textarea v-model="state.question" data-testid="ai-question" maxlength="2000" rows="3" :disabled="state.busy" placeholder="例如：这个 Mod 安装错误是什么意思？"></textarea></label>
        <details><summary>附上选中文本（可选）</summary><label>文本来源说明 <input v-model="state.selectedLabel" data-testid="ai-selected-label" maxlength="120" :disabled="state.busy" placeholder="例如：日志中的某一段"/></label><label>仅发送你粘贴的这段文本 <textarea v-model="state.selectedText" data-testid="ai-selected-text" maxlength="4000" rows="4" :disabled="state.busy"></textarea></label></details>
        <div class="send-actions"><span v-if="!state.settings?.configured" class="hint">先填写并保存连接设置，再使用 AI。</span><button type="submit" class="primary" data-testid="ai-send" :disabled="!runtime.canSend()">发送</button></div>
      </form>
      <section v-if="state.exportOpen" class="export-text" aria-label="会话文本"><label>会话文本（可选择复制）<textarea data-testid="ai-export-text" readonly :value="state.exportText" rows="8"></textarea></label><button data-testid="ai-close-export" @click="state.exportOpen=false">收起</button></section>
    </section>
    <p v-if="state.notice" class="notice" data-testid="ai-notice" role="status">{{ state.notice }}</p>
    <p v-if="state.error" class="ai-error" data-testid="ai-error" role="alert">{{ state.error }}</p>
    <div v-if="state.retryKind" class="settings-actions"><button data-testid="ai-retry" :disabled="!runtime.canRetry()" @click="runtime.retry">{{ state.retryKind === 'test' ? '重试连接测试' : '重试原请求' }}</button><span v-if="!runtime.canRetry() && !working" class="hint">重试仅针对原问题和发送范围；修改问题或选中文本后请点击“发送”。</span></div>
  </main>
</template>

<style scoped>
.local-ai { flex:1; min-height:0; width:100%; overflow-y:auto; padding:24px clamp(20px,4vw,48px); font-size:15px; color:rgb(var(--rgb-text-main)); max-width:1180px; margin:auto; }
h2 { margin:0 0 8px; font-size:21px; } h3 { margin:0 0 14px; font-size:17px; } header>p,.hint,.send-target,.settings-actions>span { color:rgb(var(--rgb-text-dim)); line-height:1.6; font-size:14px; }
.ai-settings,.ai-conversation { border:1px solid rgb(var(--rgb-border-base)/.18); border-radius:12px; padding:20px; margin-top:20px; }.settings-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
label { display:flex; flex-direction:column; gap:7px; font-size:14px; } button,input,textarea,select { font:inherit; color:inherit; border:1px solid rgb(var(--rgb-border-base)/.22); background:rgb(var(--rgb-bg-overlay)/.05); border-radius:8px; padding:8px 12px; }
input,textarea,select { min-width:0; width:100%; box-sizing:border-box; } select option { color:rgb(var(--rgb-text-main)); background:rgb(var(--rgb-bg-surface)); } textarea { resize:vertical; } button { cursor:pointer; white-space:nowrap; } button:hover { background:rgb(var(--rgb-bg-overlay)/.1); } button:disabled,input:disabled,textarea:disabled,select:disabled { opacity:.5; cursor:not-allowed; }
button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible { outline:2px solid rgb(var(--rgb-accent-primary)); outline-offset:2px; }.primary { background:rgb(var(--rgb-accent-primary)/.12); border-color:rgb(var(--rgb-accent-primary)/.45); }
.remember { flex-direction:row; align-items:center; margin:16px 0; }.remember input { width:auto; }.settings-actions,.conversation-heading,.ai-progress { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }.conversation-heading h3 { margin:0 auto 0 0; }
.send-target { overflow-wrap:anywhere; }.messages { margin:20px 0; padding:0; list-style:none; }.messages li { padding:14px; margin:10px 0; background:rgb(var(--rgb-bg-overlay)/.04); border-radius:9px; }.messages li[data-role=user] { border-left:3px solid rgb(var(--rgb-accent-primary)/.45); }.messages strong { font-size:14px; }.plain-text { white-space:pre-wrap; overflow-wrap:anywhere; user-select:text; margin:8px 0 0; line-height:1.65; }
.ai-progress { margin:12px 0; }.ai-progress button { margin-left:auto; }.question-form { display:grid; gap:14px; } details { font-size:14px; } summary { cursor:pointer; color:rgb(var(--rgb-text-dim)); margin-bottom:10px; } details label { margin:10px 0; }.send-actions { display:flex; gap:16px; align-items:center; justify-content:flex-end; }.send-actions .hint { margin-right:auto; }
.export-text { margin-top:18px; }.export-text button { margin-top:8px; }.notice { color:rgb(var(--rgb-accent-success)); overflow-wrap:anywhere; }.ai-error { color:rgb(var(--rgb-accent-danger)); overflow-wrap:anywhere; }
.query-scope { border-top:1px solid rgb(var(--rgb-border-base)/.18); padding-top:2px; }.query-scope .remember { margin:12px 0 8px; }.query-scope .hint { margin:8px 0; }.evidence-selector { display:flex; align-items:flex-end; gap:10px; flex-wrap:wrap; }.evidence-selector label { flex:1; min-width:min(260px,100%); }
@media(max-width:760px) { .settings-grid { grid-template-columns:1fr; }.ai-settings,.ai-conversation { padding:14px; }.settings-actions>span { flex-basis:100%; } }
</style>
