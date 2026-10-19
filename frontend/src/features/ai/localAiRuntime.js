import { terminalStates } from '../../app/bridge/reducer.js'
import { readSuggestions } from './aiActionRuntime.js'

const sessionPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const keyStates = new Set(['missing', 'saved', 'session', 'unavailable'])
const sessionStates = new Set(['idle', 'generating', 'failed', 'cancelled'])

function readSettings(value) {
  if (!value || value.provider !== 'openai_compatible' || typeof value.base_url !== 'string'
    || typeof value.model !== 'string' || typeof value.configured !== 'boolean'
    || !keyStates.has(value.key_status) || typeof value.message !== 'string') throw new Error('Invalid AI settings response')
  return { provider: value.provider, base_url: value.base_url, model: value.model, configured: value.configured, key_status: value.key_status, message: value.message }
}

function readSession(value, sessionId) {
  if (!value || value.session_id !== sessionId || !sessionStates.has(value.state)
    || !Array.isArray(value.messages) || value.messages.length > 1000
    || value.messages.some(item => !item || !['user', 'assistant'].includes(item.role) || typeof item.content !== 'string')
    || value.provider !== 'openai_compatible' || typeof value.model !== 'string') throw new Error('Invalid AI session response')
  return { ...value, messages: value.messages.map(item => ({ role: item.role, content: item.content })), suggestions: readSuggestions(value.suggestions) }
}

function messageFor(error, fallback) {
  return error?.code && typeof error.message === 'string' && error.message.length > 0 && error.message.length <= 300 ? error.message : fallback
}

export function createLocalAiRuntime(bridge, state = {}, options = {}) {
  let disposed = false, settingsSequence = 0, chatSequence = 0, evidenceSequence = 0, scopeSequence = 0, watcher = null
  let attempt = null, retryAttempt = null
  Object.assign(state, {
    settings: null, form: { provider: 'openai_compatible', base_url: 'https://api.openai.com/v1', model: '', api_key: '', remember: false },
    loading: false, saving: false, busy: false, cancelling: false, task: null, taskKind: null, error: '', notice: '', retryKind: null, incompleteReply: false,
    question: '', selectedText: '', selectedLabel: '', sessionId: null, messages: [], suggestions: [], pendingQuestion: '', streamedText: '',
    exportText: '', exportOpen: false, exporting: false,
    installationId: null, allowQueries: false, selectedEvidenceId: '', evidenceEntries: [], evidenceLoading: false,
    evidenceError: '', evidenceCursor: 0, evidenceHasMore: false, querying: false,
    diagnosticSnapshotId: null,
  })
  const currentChat = token => !disposed && token === chatSequence
  const currentSettings = token => !disposed && token === settingsSequence
  const actionBusy = () => options.isActionBusy?.() === true
  function clearRetry() { retryAttempt = null; state.retryKind = null }
  function retryScope() {
    return { settingsSequence, installationId: state.installationId, allowQueries: state.allowQueries, selectedEvidenceId: state.selectedEvidenceId,
      diagnosticSnapshotId: state.diagnosticSnapshotId,
      provider: state.settings?.provider, base_url: state.settings?.base_url, model: state.settings?.model }
  }
  function sameRetryScope(previous) {
    const current = retryScope()
    return Object.keys(current).every(key => current[key] === previous[key])
  }
  function canRetry() {
    return !disposed && state.retryKind !== null && retryAttempt !== null && Boolean(state.settings?.configured)
      && !state.loading && !state.saving && !state.busy && !actionBusy() && sameRetryScope(retryAttempt.scope)
      && (retryAttempt.kind !== 'chat' || state.question === retryAttempt.input.question
        && state.selectedText === retryAttempt.input.selectedText && state.selectedLabel === retryAttempt.input.selectedLabel)
  }
  function offerRetry(kind) {
    if (!attempt || attempt.kind !== kind || state.cancelling) { clearRetry(); return }
    restoreQuestion()
    retryAttempt = attempt
    state.retryKind = kind
  }
  function restoreQuestion() {
    if (attempt?.kind === 'chat' && attempt.clearedQuestion && state.question === '') state.question = attempt.input.question
  }
  function clearSuggestions() { state.suggestions = []; options.onReset?.() }
  function useSuggestions(value, sessionId) {
    state.suggestions = readSuggestions(value, state.allowQueries ? state.installationId : null)
    options.onSuggestions?.(sessionId, state.installationId, state.suggestions)
  }

  function useSettings(value) {
    state.settings = readSettings(value)
    Object.assign(state.form, { provider: state.settings.provider, base_url: state.settings.base_url, model: state.settings.model, api_key: '', remember: state.settings.key_status === 'saved' })
    state.notice = state.settings.message
  }

  async function loadSettings() {
    if (disposed || state.loading || state.saving) return false
    clearRetry()
    const token = ++settingsSequence
    state.loading = true
    state.error = ''
    try {
      const result = await bridge.request('ai.settings.get')
      if (!currentSettings(token)) return false
      useSettings(result)
      return true
    } catch (error) {
      if (currentSettings(token)) state.error = messageFor(error, '无法读取 AI 设置。Mod 管理仍可正常使用。')
      return false
    } finally { if (currentSettings(token)) state.loading = false }
  }

  function validSettings() {
    if (!state.form.model.trim() || state.form.model.length > 200) return false
    try {
      const url = new URL(state.form.base_url.trim())
      return state.form.base_url.length <= 2048 && !/[?#]/.test(state.form.base_url) && !url.username && !url.password
        && (url.protocol === 'https:' || url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))
    } catch { return false }
  }

  async function updateSettings(clearKey = false) {
    if (disposed || state.loading || state.saving || state.busy || actionBusy()) return false
    clearRetry()
    if (!clearKey && !validSettings()) {
      state.error = '请填写模型和有效地址；HTTP 仅支持本机地址，地址不能包含账号、密码、查询参数或片段。'
      return false
    }
    const token = ++settingsSequence
    state.saving = true
    state.error = ''
    state.notice = ''
    const payload = { provider: 'openai_compatible', base_url: state.form.base_url.trim(), model: state.form.model.trim(), remember: Boolean(state.form.remember) }
    if (state.form.api_key) payload.api_key = state.form.api_key
    try {
      const result = await bridge.request(clearKey ? 'ai.credentials.clear' : 'ai.settings.save', clearKey ? {} : payload)
      if (!currentSettings(token)) return false
      useSettings(result)
      state.notice ||= clearKey ? '已清除当前地址的密钥。' : 'AI 设置已保存。'
      return true
    } catch (error) {
      if (currentSettings(token)) state.error = messageFor(error, clearKey ? '未能清除密钥，请重试。' : 'AI 设置未能保存，请检查地址和凭据后重试。')
      return false
    } finally { if (currentSettings(token)) { state.saving = false; state.form.api_key = '' } }
  }

  function canSend() {
    return !disposed && Boolean(state.settings?.configured) && !state.loading && !state.saving && !state.busy && !actionBusy()
      && state.question.trim().length > 0 && state.question.length <= 2000 && state.selectedText.length <= 4000 && state.selectedLabel.length <= 120
  }

  function detach() {
    const old = watcher
    watcher = null
    if (old) void old.dispose().catch(() => {})
  }

  async function cancelTask(taskId) {
    if (!taskId) return
    try { await bridge.request('task.cancel', { task_id: taskId }) } catch { /* Closed panels must still release their subscription. */ }
  }

  async function refreshSession(token, sessionId, allowSuggestions) {
    try {
      const result = readSession(await bridge.request('ai.chat.get', { session_id: sessionId }), sessionId)
      if (!currentChat(token) || state.sessionId !== sessionId) return false
      state.messages = result.messages
      if (typeof result.incomplete === 'boolean') state.incompleteReply = result.incomplete
      if (allowSuggestions && result.state === 'idle') useSuggestions(result.suggestions, sessionId)
      state.pendingQuestion = ''
      state.streamedText = ''
      return true
    } catch (error) {
      if (currentChat(token)) state.error ||= messageFor(error, '对话内容暂时无法导出，请重试。')
      return false
    }
  }

  function consumeTask(snapshot, token, kind) {
    if (!currentChat(token) || state.task?.task_id && snapshot?.task_id !== state.task.task_id) return
    state.task = snapshot
    const data = snapshot?.data || {}
    if (kind === 'chat' && typeof data.session_id === 'string' && sessionPattern.test(data.session_id)) state.sessionId = data.session_id
    if (typeof data.text === 'string' && !data.full_text_in_session) state.streamedText = data.text
    if (typeof data.text_delta === 'string' && Number.isSafeInteger(data.text_offset) && data.text_offset === state.streamedText.length) state.streamedText += data.text_delta
    state.querying = kind === 'chat' && snapshot?.stage === 'ai_tool'
    if (!terminalStates.has(snapshot?.state)) return
    state.busy = false
    detach()
    const failed = snapshot.state === 'failed' || Boolean(data.error_code)
    const cancelled = snapshot.state === 'cancelled'
    state.incompleteReply = (failed || cancelled) && Boolean(state.streamedText)
    if (failed) { state.error = typeof data.message === 'string' && data.message.length <= 300 ? data.message : 'AI 请求未完成，请检查设置或网络后重试。'; offerRetry(kind) }
    else if (cancelled) { state.notice = '已取消请求。'; restoreQuestion(); clearRetry() }
    else {
      clearRetry()
      state.notice = kind === 'test' ? '连接测试完成。' : typeof data.query_notice === 'string' && data.query_notice.length <= 300 && data.query_notice ? data.query_notice : '回复已完成。'
      if (kind === 'chat' && state.sessionId) {
        try { useSuggestions(data.suggestions, state.sessionId) }
        catch { state.error = '建议返回不完整，请重新提问后再试。'; clearSuggestions() }
      }
    }
    state.cancelling = false
    if (kind === 'chat' && state.sessionId) void refreshSession(token, state.sessionId, !failed && !cancelled)
  }

  async function startTask(kind, previous = null) {
    if (disposed || state.loading || state.saving || state.busy || actionBusy() || !state.settings?.configured || kind === 'chat' && !canSend()) return false
    const token = ++chatSequence
    state.busy = true
    state.cancelling = false
    state.task = null
    state.taskKind = kind
    state.error = ''
    state.notice = ''
    state.streamedText = ''
    state.incompleteReply = false
    state.querying = false
    state.exportOpen = false
    state.exportText = ''
    if (kind === 'chat') clearSuggestions()
    let payload = {}
    if (kind === 'chat') {
      payload = { question: state.question.trim() }
      if (state.selectedText) payload.selected_text = state.selectedText
      if (state.selectedLabel) payload.selected_label = state.selectedLabel
      if (state.sessionId) payload.session_id = state.sessionId
      if (state.allowQueries && state.installationId) {
        payload.installation_id = state.installationId
        if (state.selectedEvidenceId) payload.selected_evidence_id = state.selectedEvidenceId
      }
      if (state.diagnosticSnapshotId) {
        payload = { question: payload.question, snapshot_id: state.diagnosticSnapshotId }
        if (state.sessionId) payload.session_id = state.sessionId
      }
    }
    if (previous) payload = { ...previous.payload }
    attempt = { kind, payload, scope: retryScope(), input: { question: state.question, selectedText: state.selectedText, selectedLabel: state.selectedLabel }, clearedQuestion: false }
    clearRetry()
    let taskId = null
    try {
      const started = await bridge.request(kind === 'test' ? 'ai.test' : payload.snapshot_id ? 'ai.diagnostics.send' : 'ai.chat.send', payload)
      if (!started || typeof started.task_id !== 'string' || !started.task_id) throw new Error('Invalid AI task response')
      taskId = started.task_id
      if (!currentChat(token)) { void cancelTask(started.task_id); return false }
      state.task = started
      if (kind === 'chat') {
        state.pendingQuestion = payload.question
        if (state.question === attempt.input.question) { state.question = ''; attempt.clearedQuestion = true }
      }
      consumeTask(started, token, kind)
      if (state.busy) {
        const handle = bridge.watchTask(started.task_id, snapshot => consumeTask(snapshot, token, kind), error => {
          if (!currentChat(token)) return
          state.busy = false
          state.incompleteReply = Boolean(state.streamedText)
          state.error = messageFor(error, 'AI 连接中断，请重试。')
          offerRetry(kind)
          state.cancelling = false
          void cancelTask(started.task_id)
          detach()
        })
        if (currentChat(token) && state.busy) watcher = handle
        else void handle.dispose().catch(() => {})
      }
      return true
    } catch (error) {
      if (taskId) void cancelTask(taskId)
      if (currentChat(token)) { state.busy = false; state.error = messageFor(error, '无法发送 AI 请求，请检查设置或网络后重试。'); offerRetry(kind); state.cancelling = false }
      return false
    }
  }

  async function cancelRequest() {
    if (disposed || !state.busy || state.cancelling) return false
    clearRetry()
    state.cancelling = true
    if (!state.task?.task_id) {
      ++chatSequence
      detach()
      Object.assign(state, { busy: false, cancelling: false, task: null, pendingQuestion: '', querying: false, notice: '已取消请求。', error: '' })
      return true
    }
    const token = chatSequence
    try {
      const result = await bridge.request('task.cancel', { task_id: state.task.task_id })
      if (currentChat(token)) consumeTask(result, token, state.taskKind)
      return true
    } catch (error) {
      if (currentChat(token)) { state.cancelling = false; state.error = messageFor(error, '未能取消请求，请重试。') }
      return false
    }
  }

  function canContinue() {
    return state.incompleteReply && Boolean(state.sessionId) && state.messages.some(row => row.role === 'assistant')
      && Boolean(state.settings?.configured) && !state.busy && !state.loading && !state.saving && !actionBusy() && !disposed
  }

  async function continueReply() {
    if (!canContinue()) return false
    const draft = state.question
    state.question = '请从刚才中断的地方继续分析，不要重复已有内容；先完成尚未讲完的结论和下一步。'
    const started = await startTask('chat')
    if (started && draft && !state.question) state.question = draft
    return started
  }

  async function resetSession(keepDraft = false, notice = '已开始新对话。') {
    if (disposed) return false
    const previousSession = state.sessionId, previousTask = state.busy ? state.task?.task_id : null
    const token = ++chatSequence
    detach()
    clearRetry()
    attempt = null
    clearSuggestions()
    Object.assign(state, { busy: false, cancelling: false, task: null, taskKind: null, sessionId: null, messages: [], pendingQuestion: '', streamedText: '', incompleteReply: false, querying: false, exportText: '', exportOpen: false, exporting: false, error: '', notice })
    if (!keepDraft) Object.assign(state, { question: '', selectedText: '', selectedLabel: '' })
    await cancelTask(previousTask)
    if (previousSession) {
      try { await bridge.request('ai.chat.clear', { session_id: previousSession }) }
      catch (error) { if (currentChat(token)) state.error = messageFor(error, '新对话已打开，旧对话暂未清除。') }
    }
    return true
  }

  function clearEvidence() {
    ++evidenceSequence
    Object.assign(state, { selectedEvidenceId: '', evidenceEntries: [], evidenceLoading: false, evidenceError: '', evidenceCursor: 0, evidenceHasMore: false })
  }

  async function loadEvidence(more = false) {
    if (disposed || !state.allowQueries || !state.installationId || state.evidenceLoading || more && !state.evidenceHasMore) return false
    if (!more && state.selectedEvidenceId) {
      state.selectedEvidenceId = ''
      void resetSession(true, '日志选择已清除，已开始新对话。')
    }
    const token = ++evidenceSequence, installationId = state.installationId, after = more ? state.evidenceCursor : 0
    state.evidenceLoading = true
    state.evidenceError = ''
    try {
      const result = await bridge.request('ai.logs.list', { installation_id: installationId, after, limit: 20 })
      if (disposed || token !== evidenceSequence || !state.allowQueries || state.installationId !== installationId) return false
      if (!result || !Array.isArray(result.entries) || result.entries.length > 20 || !Number.isSafeInteger(result.total) || result.total < 0
        || !Number.isSafeInteger(result.next_cursor) || result.next_cursor < after
        || result.entries.some(entry => !entry || !sessionPattern.test(entry.evidence_id) || !Number.isSafeInteger(entry.seq) || entry.seq <= after || typeof entry.text !== 'string')) throw new Error('Invalid evidence list response')
      const entries = result.entries.map(entry => ({ evidence_id: entry.evidence_id, seq: entry.seq, label: entry.text.replace(/\s+/g, ' ').slice(0, 100) || '空日志片段' }))
      state.evidenceEntries = more ? [...state.evidenceEntries, ...entries] : entries
      state.evidenceCursor = result.next_cursor
      state.evidenceHasMore = entries.length === 20 && result.next_cursor > after && state.evidenceEntries.length < result.total
      return true
    } catch (error) {
      if (!disposed && token === evidenceSequence) {
        if (!more) { state.evidenceEntries = []; state.evidenceHasMore = false; state.evidenceCursor = 0 }
        state.evidenceError = messageFor(error, '无法读取本地日志片段，可继续提问或稍后刷新。')
      }
      return false
    } finally { if (!disposed && token === evidenceSequence) state.evidenceLoading = false }
  }

  async function setInstallation(installation) {
    if (disposed) return false
    const id = installation?.installation_id
    const installationId = typeof id === 'string' && sessionPattern.test(id) ? id : null
    if (state.installationId === installationId) return false
    const token = ++scopeSequence
    state.installationId = installationId
    state.diagnosticSnapshotId = null
    if (!installationId) state.allowQueries = false
    clearEvidence()
    await resetSession(true, '当前游戏已更改，已开始新对话。')
    if (!disposed && token === scopeSequence && state.allowQueries && options.evidencePicker !== false) await loadEvidence()
    return true
  }

  async function setAllowQueries(enabled) {
    if (disposed) return false
    const allowed = enabled === true && Boolean(state.installationId)
    if (state.allowQueries === allowed) return false
    const token = ++scopeSequence
    state.allowQueries = allowed
    clearEvidence()
    await resetSession(true, '查询范围已更改，已开始新对话。')
    if (!disposed && token === scopeSequence && allowed && options.evidencePicker !== false) await loadEvidence()
    return true
  }

  async function selectEvidence(evidenceId) {
    if (disposed || !state.allowQueries || !state.installationId || typeof evidenceId !== 'string'
      || evidenceId && !state.evidenceEntries.some(entry => entry.evidence_id === evidenceId) || state.selectedEvidenceId === evidenceId) return false
    ++scopeSequence
    clearRetry()
    attempt = null
    state.selectedEvidenceId = evidenceId
    await resetSession(true, '日志选择已更改，已开始新对话。')
    return true
  }

  async function exportSession() {
    if (disposed || state.busy || state.exporting || !state.sessionId) return false
    const token = chatSequence, id = state.sessionId
    state.exporting = true
    state.error = ''
    try {
      const result = await bridge.request('ai.chat.export', { session_id: id })
      if (!result || typeof result.text !== 'string') throw new Error('Invalid AI export response')
      if (!currentChat(token) || state.sessionId !== id) return false
      state.exportText = result.text
      state.exportOpen = true
      return true
    } catch (error) {
      if (currentChat(token)) state.error = messageFor(error, '无法导出对话内容，请重试。')
      return false
    } finally { if (currentChat(token)) state.exporting = false }
  }

  async function setDiagnostic(snapshotId) {
    if (disposed || snapshotId !== null && (typeof snapshotId !== 'string' || !sessionPattern.test(snapshotId))) return false
    if (snapshotId === state.diagnosticSnapshotId) return false
    const token = ++scopeSequence
    await resetSession(true, snapshotId ? '日志已就绪，可以开始分析。' : '已切换为不附带日志的提问。')
    if (disposed || token !== scopeSequence) return false
    state.diagnosticSnapshotId = snapshotId
    return true
  }

  function dispose() {
    if (disposed) return
    const taskId = state.busy ? state.task?.task_id : null
    disposed = true
    ++settingsSequence
    ++chatSequence
    ++evidenceSequence
    ++scopeSequence
    clearRetry()
    attempt = null
    clearSuggestions()
    state.form.api_key = ''
    detach()
    void cancelTask(taskId)
  }

  return { loadSettings, saveSettings: () => updateSettings(false), clearCredentials: () => updateSettings(true), canSend, canRetry, canContinue, continueReply, retry: () => canRetry() ? startTask(retryAttempt.kind, retryAttempt) : Promise.resolve(false), send: () => startTask('chat'), testConnection: () => startTask('test'), cancel: cancelRequest, newSession: () => resetSession(), setInstallation, setAllowQueries, setDiagnostic, selectEvidence, refreshEvidence: () => loadEvidence(), moreEvidence: () => loadEvidence(true), exportSession, dispose }
}
