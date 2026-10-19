import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createLocalAiRuntime } from './localAiRuntime.js'

const id = '11111111-1111-4111-8111-111111111111'
const otherId = '22222222-2222-4222-8222-222222222222'
const settings = overrides => ({ provider: 'openai_compatible', base_url: 'http://127.0.0.1:1234/v1', model: 'chosen-model', configured: true, key_status: 'session', message: '', ...overrides })
const session = overrides => ({ session_id: id, messages: [{ role: 'user', content: 'question' }, { role: 'assistant', content: 'completed reply' }], state: 'idle', provider: 'openai_compatible', model: 'chosen-model', ...overrides })
const event = (state = 'running', data = {}, taskId = 'job') => ({ task_id: taskId, request_id: 'request', sequence: 1, state, data })
const evidence = (entries = [{ evidence_id: otherId, seq: 1, text: 'selected log line' }], total = entries.length) => ({ entries, total, next_cursor: entries.at(-1)?.seq || 0 })
const flush = async () => { await new Promise(resolve => setImmediate(resolve)) }

test('large stream increments are offset checked and final full-session fetch keeps complete reply', async () => {
  const text = '分析😀'.repeat(9000)
  const { runtime, state, watches } = setup(async command => command === 'ai.settings.get' ? settings()
    : command === 'ai.chat.get' ? session({ messages: [{ role: 'user', content: '分析' }, { role: 'assistant', content: text }] }) : event('queued'))
  await runtime.loadSettings()
  state.question = '分析'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: '分析😀' }))
  watches[0].update(event('running', { session_id: id, text_delta: '后续内容', text_offset: 4 }))
  watches[0].update(event('running', { session_id: id, text_delta: '后续内容', text_offset: 4 }))
  watches[0].update(event('running', { session_id: id, text_delta: '不完整跳帧', text_offset: 999 }))
  assert.equal(state.streamedText, '分析😀后续内容')
  watches[0].update(event('succeeded', { session_id: id, text: '', full_text_in_session: true, completed: true }))
  assert.equal(state.streamedText, '分析😀后续内容')
  await flush()
  assert.equal(state.messages.at(-1).content, text)
  assert.equal(state.streamedText, '')
  runtime.dispose()
})

test('model length and cancellation can continue in the same scope without repeating original question', async () => {
  const { runtime, state, watches, calls } = setup(async command => command === 'ai.settings.get' ? settings()
    : command === 'ai.chat.get' ? session({ state: 'failed', incomplete: true, messages: [{ role: 'user', content: '原问题' }, { role: 'assistant', content: '已保留的部分分析' }] }) : event('queued'))
  await runtime.loadSettings()
  state.question = '原问题'
  await runtime.send()
  watches[0].update(event('succeeded', { session_id: id, error_code: 'AI_PROVIDER_TRUNCATED', text: '已保留的部分分析', message: '模型达到本次生成长度。' }))
  await flush()
  assert.equal(runtime.canContinue(), true)
  state.question = '用户尚未发送的草稿'
  assert.equal(await runtime.continueReply(), true)
  const payload = calls.filter(([command]) => command === 'ai.chat.send').at(-1)[1]
  assert.equal(payload.session_id, id)
  assert.match(payload.question, /中断的地方继续/)
  assert.equal(state.question, '用户尚未发送的草稿')
  runtime.dispose()
})
function deferred() { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
function setup(request, options = {}) {
  const state = {}, calls = [], watches = []
  const bridge = {
    async request(command, payload = {}) {
      calls.push([command, payload])
      if (request) return request(command, payload)
      if (command.startsWith('ai.settings.') || command === 'ai.credentials.clear') return settings()
      if (command === 'ai.chat.get') return session({ session_id: payload.session_id })
      if (command === 'ai.chat.export') return { text: 'redacted transcript' }
      if (command === 'ai.chat.clear') return { cleared: true }
      if (command === 'ai.logs.list') return evidence()
      if (command === 'task.cancel') return event('cancelled', { session_id: id }, payload.task_id)
      return event('queued')
    },
    watchTask(taskId, update, error) { const handle = { taskId, update, error, disposed: false, async dispose() { this.disposed = true } }; watches.push(handle); return handle },
  }
  return { state, calls, watches, bridge, runtime: createLocalAiRuntime(bridge, state, options) }
}

test('optional runtime creates no requests before opening and settings load never tests or sends', async () => {
  const { runtime, calls, state } = setup()
  assert.deepEqual(calls, [])
  assert.equal(runtime.canSend(), false)
  assert.equal(await runtime.send(), false)
  assert.equal(await runtime.testConnection(), false)
  await runtime.loadSettings()
  assert.deepEqual(calls, [['ai.settings.get', {}]])
  assert.equal(state.form.api_key, '')
  runtime.dispose()
})

test('query budget completion is a successful answer with scope notice, not a retry error', async () => {
  const { runtime, state, watches } = setup()
  await runtime.loadSettings()
  state.question = '结合环境分析'
  await runtime.send()
  watches[0].update(event('succeeded', { session_id: id, completed: true, text: '已有证据的分析。',
    query_notice: '已根据本轮取得的资料完成分析；未继续额外查询，可继续追问。' }))
  await flush()
  assert.equal(state.error, '')
  assert.equal(state.retryKind, null)
  assert.equal(state.incompleteReply, false)
  assert.match(state.notice, /可继续追问/)
  state.question = '请展开这条证据'
  assert.equal(runtime.canSend(), true)
  runtime.dispose()
})

test('unconfigured AI is optional and does not send network jobs', async () => {
  const { runtime, calls, state } = setup(async () => settings({ configured: false, key_status: 'missing', model: '' }))
  await runtime.loadSettings()
  state.question = 'test'
  assert.equal(runtime.canSend(), false)
  assert.equal(await runtime.testConnection(), false)
  assert.equal(await runtime.send(), false)
  assert.equal(calls.length, 1)
})

test('settings save includes an explicit new key then removes it from frontend state', async () => {
  const { runtime, calls, state } = setup()
  await runtime.loadSettings()
  state.form.api_key = 'synthetic-private-key'
  state.form.remember = true
  assert.equal(await runtime.saveSettings(), true)
  assert.deepEqual(calls[1], ['ai.settings.save', { provider: 'openai_compatible', base_url: 'http://127.0.0.1:1234/v1', model: 'chosen-model', remember: true, api_key: 'synthetic-private-key' }])
  assert.equal(state.form.api_key, '')
  assert.equal(Object.hasOwn(state.settings, 'api_key'), false)
})

test('empty key preserves current target credential and keyring fallback is visible', async () => {
  const { runtime, calls, state } = setup(async command => command === 'ai.settings.save' ? settings({ key_status: 'session', message: 'Windows 凭据不可用，密钥仅供本次会话使用。' }) : settings({ key_status: 'saved' }))
  await runtime.loadSettings()
  assert.equal(await runtime.saveSettings(), true)
  assert.equal(Object.hasOwn(calls[1][1], 'api_key'), false)
  assert.match(state.notice, /仅供本次会话/)
})

test('settings failures do not display raw exception details or retain password input', async () => {
  const { runtime, state } = setup(async command => { if (command === 'ai.settings.get') return settings(); throw new Error('secret-key/trace') })
  await runtime.loadSettings()
  state.form.api_key = 'synthetic-private-key'
  assert.equal(await runtime.saveSettings(), false)
  assert.equal(state.form.api_key, '')
  assert.equal(state.error.includes('secret'), false)
})

test('invalid model or remote HTTP never leaves the panel', async () => {
  const { runtime, calls, state } = setup()
  await runtime.loadSettings()
  for (const [field, value] of [['model', ''], ['base_url', 'http://provider.example/v1'], ['base_url', 'https://user:password@provider.example/v1'], ['base_url', 'https://provider.example/v1?key=secret']]) {
    state.form[field] = value
    assert.equal(await runtime.saveSettings(), false)
    state.form.model = 'chosen-model'
  }
  assert.equal(calls.length, 1)
})

test('clearing credentials calls only current backend target and reports resulting status', async () => {
  const { runtime, calls, state } = setup(async command => command === 'ai.credentials.clear' ? settings({ key_status: 'missing', configured: false }) : settings())
  await runtime.loadSettings()
  assert.equal(await runtime.clearCredentials(), true)
  assert.deepEqual(calls[1], ['ai.credentials.clear', {}])
  assert.equal(state.settings.key_status, 'missing')
  assert.equal(runtime.canSend(), false)
})

test('connection test happens only on click and sends no question or selected contents', async () => {
  const { runtime, calls, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'private question'
  state.selectedText = 'private selected content'
  assert.equal(await runtime.testConnection(), true)
  assert.deepEqual(calls[1], ['ai.test', {}])
  watches[0].update(event('succeeded', { completed: true, session_id: otherId, text: 'connected' }))
  assert.equal(state.sessionId, null)
  assert.equal(state.streamedText, 'connected')
  assert.match(state.notice, /连接测试完成/)
})

test('chat forwards only the current question and explicitly chosen text with no game metadata', async () => {
  const { runtime, calls, state, watches } = setup()
  await runtime.loadSettings()
  state.question = ' why this error? '
  state.selectedText = 'chosen log lines'
  state.selectedLabel = 'a small log excerpt'
  assert.equal(await runtime.send(), true)
  assert.deepEqual(calls[1], ['ai.chat.send', { question: 'why this error?', selected_text: 'chosen log lines', selected_label: 'a small log excerpt' }])
  assert.equal(state.pendingQuestion, 'why this error?')
  assert.equal(state.question, '')
  watches[0].update(event('running', { session_id: id, text: 'streaming ' }))
  assert.equal(state.streamedText, 'streaming ')
  assert.equal(state.sessionId, id)
  watches[0].update(event('running', { session_id: id, text: 'streaming text' }))
  assert.equal(state.streamedText, 'streaming text')
  watches[0].update(event('succeeded', { completed: true, session_id: id, text: 'completed reply' }))
  await flush()
  assert.equal(state.busy, false)
  assert.deepEqual(state.messages, session().messages)
  assert.equal(state.pendingQuestion, '')
  assert.equal(state.streamedText, '')
  assert.equal(watches[0].disposed, true)
})

test('follow-up sends only the current session identity and selected input', async () => {
  const { runtime, calls, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'first'
  await runtime.send()
  watches[0].update(event('succeeded', { session_id: id, text: 'reply' }))
  await flush()
  state.question = 'second'
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'second', session_id: id }])
})

test('offline errors are visible and leave optional AI idle', async () => {
  const { runtime, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'test'
  await runtime.send()
  watches[0].update(event('failed', { error_code: 'AI_OFFLINE', message: '无法连接 AI 服务。' }))
  assert.equal(state.busy, false)
  assert.equal(state.error, '无法连接 AI 服务。')
})

test('cancel is bound to this job and preserves the cancelled state', async () => {
  const { runtime, calls, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'test'
  await runtime.send()
  assert.equal(await runtime.cancel(), true)
  await flush()
  assert.deepEqual(calls.find(([command]) => command === 'task.cancel'), ['task.cancel', { task_id: 'job' }])
  assert.equal(state.busy, false)
  assert.match(state.notice, /取消/)
  assert.equal(watches[0].disposed, true)
})

test('new session cancels and clears the prior session and ignores old streamed events', async () => {
  const { runtime, calls, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'old question'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: 'old partial' }))
  await runtime.newSession()
  watches[0].update(event('succeeded', { session_id: id, text: 'late reply' }))
  assert.equal(state.sessionId, null)
  assert.deepEqual(state.messages, [])
  assert.equal(state.streamedText, '')
  assert.equal(watches[0].disposed, true)
  assert.ok(calls.some(([command]) => command === 'task.cancel'))
  assert.ok(calls.some(([command, payload]) => command === 'ai.chat.clear' && payload.session_id === id))
})

test('new session cancels a job whose send response arrives after reset', async () => {
  const pending = deferred()
  const { runtime, calls, state, watches } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'ai.chat.send' ? pending.promise : {})
  await runtime.loadSettings()
  state.question = 'old question'
  const sending = runtime.send()
  await runtime.newSession()
  pending.resolve(event('queued', {}, 'late-job'))
  assert.equal(await sending, false)
  await flush()
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'late-job'))
  assert.equal(watches.length, 0)
  assert.equal(state.busy, false)
})

test('session retrieval returning after a reset cannot overwrite the new conversation', async () => {
  const pending = deferred()
  const { runtime, state, watches } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'ai.chat.get' ? pending.promise : command === 'ai.chat.clear' ? { cleared: true } : event('queued'))
  await runtime.loadSettings()
  state.question = 'old'
  await runtime.send()
  watches[0].update(event('succeeded', { session_id: id, text: 'old' }))
  await runtime.newSession()
  pending.resolve(session())
  await flush()
  assert.deepEqual(state.messages, [])
  assert.equal(state.sessionId, null)
})

test('export uses the backend redacted conversation text and ignores late exports', async () => {
  const { runtime, state, calls } = setup()
  await runtime.loadSettings()
  state.sessionId = id
  assert.equal(await runtime.exportSession(), true)
  assert.equal(state.exportText, 'redacted transcript')
  assert.equal(state.exportOpen, true)
  assert.deepEqual(calls.at(-1), ['ai.chat.export', { session_id: id }])
  const pending = deferred()
  const later = setup(async command => command === 'ai.chat.export' ? pending.promise : {})
  later.state.sessionId = id
  const exporting = later.runtime.exportSession()
  await later.runtime.newSession()
  pending.resolve({ text: 'old private text' })
  assert.equal(await exporting, false)
  assert.equal(later.state.exportText, '')
  assert.equal(later.state.exportOpen, false)
})

test('leaving the optional AI page cancels active work and ignores later updates', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  state.question = 'test'
  await runtime.send()
  state.form.api_key = 'unsaved-private-key'
  runtime.dispose()
  runtime.dispose()
  watches[0].update(event('succeeded', { session_id: id, text: 'late' }))
  await flush()
  assert.equal(state.form.api_key, '')
  assert.equal(state.sessionId, null)
  assert.equal(watches[0].disposed, true)
  assert.equal(calls.filter(([command]) => command === 'task.cancel').length, 1)
  assert.equal(await runtime.send(), false)
})

test('leaving while settings load is pending does not retain credential or update state', async () => {
  const pending = deferred()
  const { runtime, state } = setup(async () => pending.promise)
  const loading = runtime.loadSettings()
  runtime.dispose()
  pending.resolve(settings({ api_key: 'must-not-be-copied' }))
  assert.equal(await loading, false)
  assert.equal(state.settings, null)
})

test('bridge watcher connection loss cancels server work and shows only bounded error', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  state.question = 'test'
  await runtime.send()
  watches[0].error(new Error('secret-key internal failure'))
  await flush()
  assert.equal(state.busy, false)
  assert.equal(state.error.includes('secret'), false)
  assert.ok(calls.some(([command]) => command === 'task.cancel'))
  assert.equal(watches[0].disposed, true)
})

test('legacy AI text rendering remains safe and the app uses the full diagnostic workspace', () => {
  const component = readFileSync(new URL('./LocalAiPanel.vue', import.meta.url), 'utf8')
  const app = readFileSync(new URL('../../app/App.vue', import.meta.url), 'utf8')
  assert.match(component, /\{\{ message\.content \}\}/)
  assert.match(component, /\{\{ state\.streamedText \}\}/)
  assert.equal(/v-html|localStorage|aiStore|markdown-it/.test(component), false)
  assert.match(app, /<LogDiagnosticsWorkspace\s/)
  assert.match(app, /v-show="workspaceTab==='ai'"/)
  assert.match(app, /workspaceTab = ref\('mods'\)/)
})

test('foreign task snapshots never replace the active streamed conversation', async () => {
  const { runtime, state, watches } = setup()
  await runtime.loadSettings()
  state.question = 'test'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: 'wrong' }, 'foreign-job'))
  assert.equal(state.streamedText, '')
  assert.equal(state.sessionId, null)
  assert.equal(state.busy, true)
})

test('late cancellation failure cannot display an old error in the new session', async () => {
  let reject
  const pending = new Promise((resolve, fail) => { reject = fail })
  const { runtime, state } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'task.cancel' ? pending : event('queued'))
  await runtime.loadSettings()
  state.question = 'old'
  await runtime.send()
  const cancelling = runtime.cancel()
  const restarting = runtime.newSession()
  reject(Object.assign(new Error('old cancellation failed'), { code: 'AI_CANCEL_FAILED' }))
  assert.equal(await cancelling, false)
  await restarting
  assert.equal(state.error, '')
  assert.equal(state.sessionId, null)
})

test('watcher setup failure cancels the accepted server job', async () => {
  const { runtime, bridge, state, calls } = setup()
  bridge.watchTask = () => { throw new Error('watch unavailable') }
  await runtime.loadSettings()
  state.question = 'test'
  assert.equal(await runtime.send(), false)
  await flush()
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'job'))
  assert.equal(state.busy, false)
})

test('choosing an installation alone never enables queries or sends game or log identities', async () => {
  const { runtime, state, calls } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id, canonical_root: 'synthetic/game' })
  state.question = 'ask without local queries'
  await runtime.send()
  assert.equal(state.allowQueries, false)
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'ask without local queries' }])
  assert.equal(calls.some(([command]) => command === 'ai.logs.list' || command === 'evidence.list'), false)
})

test('only enabling queries and selecting a listed log forwards fixed scope identities', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  assert.equal(await runtime.setAllowQueries(true), true)
  assert.deepEqual(calls.at(-1), ['ai.logs.list', { installation_id: id, after: 0, limit: 20 }])
  assert.equal(state.selectedEvidenceId, '')
  state.question = 'read names'
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'read names', installation_id: id }])
  watches[0].update(event('succeeded', { session_id: id, text: 'names' }))
  await flush()
  assert.equal(await runtime.selectEvidence('33333333-3333-4333-8333-333333333333'), false)
  assert.equal(await runtime.selectEvidence(otherId), true)
  assert.equal(state.sessionId, null)
  state.question = 'explain this selected log'
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'explain this selected log', installation_id: id, selected_evidence_id: otherId }])
  assert.equal(calls.some(([command]) => command === 'evidence.read'), false)
  assert.equal(calls.some(([command]) => command === 'evidence.list'), false)
})

test('changing installation cancels scoped work and ignores old responses while preserving draft text', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  await runtime.selectEvidence(otherId)
  state.question = 'old scoped question'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: 'old streamed text' }))
  state.question = 'unsent draft'
  state.selectedText = 'chosen pasted draft'
  state.selectedLabel = 'my selected text'
  await runtime.setInstallation({ installation_id: otherId })
  watches[0].update(event('succeeded', { session_id: id, text: 'late old response' }))
  assert.equal(state.question, 'unsent draft')
  assert.equal(state.selectedText, 'chosen pasted draft')
  assert.equal(state.selectedLabel, 'my selected text')
  assert.equal(state.streamedText, '')
  assert.equal(state.selectedEvidenceId, '')
  assert.equal(state.sessionId, null)
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'job'))
  assert.ok(calls.some(([command, payload]) => command === 'ai.chat.clear' && payload.session_id === id))
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'unsent draft', selected_text: 'chosen pasted draft', selected_label: 'my selected text', installation_id: otherId }])
})

test('disabling queries clears scoped history and next send excludes all scope identities', async () => {
  const { runtime, state, calls } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  await runtime.selectEvidence(otherId)
  state.sessionId = id
  state.messages = session().messages
  state.question = 'keep this draft'
  await runtime.setAllowQueries(false)
  assert.equal(state.question, 'keep this draft')
  assert.equal(state.sessionId, null)
  assert.deepEqual(state.messages, [])
  assert.deepEqual(state.evidenceEntries, [])
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'keep this draft' }])
})

test('new session keeps current query options and selected evidence but clears question and chat', async () => {
  const { runtime, state } = setup()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  await runtime.selectEvidence(otherId)
  state.sessionId = id
  state.question = 'old draft'
  await runtime.newSession()
  assert.equal(state.allowQueries, true)
  assert.equal(state.installationId, id)
  assert.equal(state.selectedEvidenceId, otherId)
  assert.equal(state.sessionId, null)
  assert.equal(state.question, '')
})

test('evidence failures are bounded, optional, and late results cannot cross installation scope', async () => {
  const pending = deferred()
  const { runtime, state, calls } = setup(async (command, payload) => {
    if (command === 'ai.settings.get') return settings()
    if (command === 'ai.logs.list') {
      if (payload.installation_id === id) return pending.promise
      throw new Error('secret file path from backend')
    }
    return event('queued')
  })
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  const enabling = runtime.setAllowQueries(true)
  await flush()
  await runtime.setInstallation({ installation_id: otherId })
  pending.resolve(evidence())
  await enabling
  assert.deepEqual(state.evidenceEntries, [])
  assert.equal(state.evidenceError.includes('secret'), false)
  assert.match(state.evidenceError, /无法读取/)
  state.question = 'query without log'
  assert.equal(runtime.canSend(), true)
  await runtime.send()
  assert.deepEqual(calls.at(-1), ['ai.chat.send', { question: 'query without log', installation_id: otherId }])
})

test('evidence paging appends only local identities and refresh clears old chosen log scope', async () => {
  const entries = Array.from({ length: 20 }, (_, index) => ({ evidence_id: `${String(index + 1).padStart(8, '0')}-1111-4111-8111-111111111111`, seq: index + 1, text: `<img src=x> line ${index}` }))
  const { runtime, state, calls } = setup(async (command, payload) => {
    if (command === 'ai.logs.list') return payload.after ? evidence([{ evidence_id: otherId, seq: 21, text: 'last' }], 21) : evidence(entries, 21)
    if (command === 'ai.chat.clear') return { cleared: true }
    return settings()
  })
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  assert.equal(state.evidenceHasMore, true)
  await runtime.moreEvidence()
  assert.equal(state.evidenceEntries.length, 21)
  assert.equal(state.evidenceHasMore, false)
  assert.deepEqual(calls.at(-1), ['ai.logs.list', { installation_id: id, after: 20, limit: 20 }])
  await runtime.selectEvidence(otherId)
  state.sessionId = id
  await runtime.refreshEvidence()
  await flush()
  assert.equal(state.selectedEvidenceId, '')
  assert.equal(state.sessionId, null)
  assert.equal(state.evidenceEntries.length, 20)
  assert.ok(calls.some(([command]) => command === 'ai.chat.clear'))
})

test('scope changes cancel a pending accepted job and stale job or disposed evidence cannot attach', async () => {
  const pending = deferred()
  const { runtime, state, calls, watches } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'ai.chat.send' ? pending.promise : command === 'ai.logs.list' ? evidence() : {})
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  state.question = 'old ordinary question'
  const sending = runtime.send()
  await runtime.setAllowQueries(true)
  pending.resolve(event('queued', {}, 'late-job'))
  assert.equal(await sending, false)
  await flush()
  assert.equal(watches.length, 0)
  assert.equal(state.question, 'old ordinary question')
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'late-job'))
  const pendingEvidence = deferred()
  const other = setup(async command => command === 'ai.logs.list' ? pendingEvidence.promise : {})
  await other.runtime.setInstallation({ installation_id: id })
  const enabling = other.runtime.setAllowQueries(true)
  await flush()
  other.runtime.dispose()
  pendingEvidence.resolve(evidence())
  await enabling
  assert.deepEqual(other.state.evidenceEntries, [])
})

test('malformed evidence does not grant arbitrary log access and queries require a current game', async () => {
  const { runtime, state } = setup(async command => command === 'ai.logs.list' ? evidence([{ evidence_id: 'free/path', seq: 1, text: 'bad' }]) : {})
  assert.equal(await runtime.setAllowQueries(true), false)
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  assert.deepEqual(state.evidenceEntries, [])
  assert.equal(await runtime.selectEvidence('free/path'), false)
  assert.match(state.evidenceError, /无法读取/)
  await runtime.setInstallation(null)
  assert.equal(state.allowQueries, false)
})

const suggestion = () => ({ suggestion_id: '33333333-3333-4333-8333-333333333333', installation_id: id, release_id: '44444444-4444-4444-8444-444444444444', manifest_hash: 'a'.repeat(64), display_name: 'Mod', version_raw: '1.0', action: 'install', reason: '尚未安装。' })

test('completed conversation publishes passive suggestions and a new send clears them before contacting AI', async () => {
  const published = [], reset = []
  const { runtime, state, calls, watches } = setup(async (command, payload) => {
    if (command === 'ai.settings.get') return settings()
    if (command === 'ai.logs.list') return evidence()
    if (command === 'ai.chat.get') return session({ suggestions: [suggestion()] })
    return event('queued')
  }, { onSuggestions: (...args) => published.push(args), onReset: () => reset.push(true) })
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  state.question = 'suggest a mod'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: 'thinking', suggestions: [suggestion()] }))
  assert.deepEqual(state.suggestions, [])
  watches[0].update(event('succeeded', { session_id: id, text: 'suggestion', suggestions: [suggestion()] }))
  await flush()
  assert.deepEqual(state.suggestions, [suggestion()])
  assert.equal(published.at(-1)[0], id)
  assert.equal(published.at(-1)[1], id)
  assert.equal(calls.some(([command]) => command === 'mod.action' || command === 'ai.suggestion.resolve'), false)
  state.question = 'next question'
  const before = reset.length
  await runtime.send()
  assert.deepEqual(state.suggestions, [])
  assert.equal(reset.length, before + 1)
  runtime.dispose()
})

test('new session and scope changes invalidate suggestions while action busy blocks new sends and connection changes', async () => {
  let busy = true, resets = 0
  const { runtime, state, calls } = setup(undefined, { isActionBusy: () => busy, onReset: () => { resets += 1 } })
  await runtime.loadSettings()
  state.question = 'question'
  assert.equal(runtime.canSend(), false)
  assert.equal(await runtime.send(), false)
  assert.equal(await runtime.testConnection(), false)
  assert.equal(await runtime.saveSettings(), false)
  assert.equal(calls.length, 1)
  state.suggestions = [suggestion()]
  await runtime.newSession()
  assert.deepEqual(state.suggestions, [])
  assert.equal(resets, 1)
  busy = false
  state.suggestions = [suggestion()]
  await runtime.setInstallation({ installation_id: id })
  assert.deepEqual(state.suggestions, [])
  assert.equal(resets, 2)
  runtime.dispose()
})

test('late chat.get from old session cannot restore cleared suggestion cards', async () => {
  const pending = deferred(), published = []
  const { runtime, state, watches } = setup(async command => {
    if (command === 'ai.settings.get') return settings()
    if (command === 'ai.logs.list') return evidence()
    if (command === 'ai.chat.get') return pending.promise
    return event('queued')
  }, { onSuggestions: (...args) => published.push(args) })
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  state.question = 'old question'
  await runtime.send()
  watches[0].update(event('succeeded', { session_id: id, text: 'old', suggestions: [suggestion()] }))
  await runtime.newSession()
  const before = published.length
  pending.resolve(session({ suggestions: [suggestion()] }))
  await flush()
  assert.deepEqual(state.suggestions, [])
  assert.equal(published.length, before)
  runtime.dispose()
})

test('cancelled generation never exposes provisional suggestions or executes ordinary actions', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  state.question = 'make a suggestion'
  await runtime.send()
  watches[0].update(event('cancelled', { session_id: id, suggestions: [suggestion()] }))
  assert.deepEqual(state.suggestions, [])
  assert.equal(calls.some(([command]) => command === 'mod.action' || command === 'ai.suggestion.resolve'), false)
  runtime.dispose()
})

test('failed chat offers an explicit retry of the original question and selected scope without automatic sends', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  await runtime.selectEvidence(otherId)
  state.sessionId = id
  state.question = ' original question '
  state.selectedText = 'chosen text'
  state.selectedLabel = 'selected lines'
  await runtime.send()
  const original = calls.find(([command]) => command === 'ai.chat.send')[1]
  watches[0].update(event('failed', { error_code: 'AI_RATE_LIMIT', message: '请求过于频繁，请稍后重试。' }))
  await flush()
  assert.equal(state.question, ' original question ')
  assert.equal(runtime.canRetry(), true)
  assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
  assert.equal(await runtime.retry(), true)
  assert.deepEqual(calls.filter(([command]) => command === 'ai.chat.send').at(-1)[1], original)
  assert.equal(runtime.canRetry(), false)
  watches[1].update(event('succeeded', { session_id: id, text: 'complete reply' }))
  await flush()
  assert.equal(state.retryKind, null)
  runtime.dispose()
})

for (const code of ['AI_OFFLINE', 'AI_RATE_LIMIT', 'AI_MODEL_UNAVAILABLE', 'AI_TIMEOUT', 'AI_OUTPUT_LIMIT']) {
  test(`${code} returns to idle with a manual retry and never resends by itself`, async () => {
    const { runtime, state, calls, watches } = setup()
    await runtime.loadSettings()
    state.question = 'question'
    await runtime.send()
    watches[0].update(event('failed', { error_code: code, message: '请求未完成，请稍后重试。' }))
    await flush()
    assert.equal(state.busy, false)
    assert.equal(runtime.canRetry(), true)
    assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
    runtime.dispose()
  })
}

test('connection test retry retains its empty payload and never sends the draft question or text', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  state.question = 'private unsent question'
  state.selectedText = 'private unsent text'
  await runtime.testConnection()
  watches[0].update(event('failed', { error_code: 'AI_TIMEOUT', message: '连接超时。' }))
  assert.equal(state.retryKind, 'test')
  assert.equal(await runtime.retry(), true)
  assert.deepEqual(calls.filter(([command]) => command === 'ai.test'), [['ai.test', {}], ['ai.test', {}]])
  assert.equal(calls.some(([command]) => command === 'ai.chat.send'), false)
  assert.equal(state.question, 'private unsent question')
  runtime.dispose()
})

for (const field of ['question', 'selectedText', 'selectedLabel']) {
  test(`editing ${field} after failure disables old retry and keeps the edited input`, async () => {
    const { runtime, state, calls, watches } = setup()
    await runtime.loadSettings()
    state.question = 'original question'
    state.selectedText = 'original text'
    state.selectedLabel = 'original label'
    await runtime.send()
    watches[0].update(event('failed', { error_code: 'AI_OFFLINE', message: '无法连接。' }))
    state[field] = 'edited draft'
    assert.equal(runtime.canRetry(), false)
    assert.equal(await runtime.retry(), false)
    assert.equal(state[field], 'edited draft')
    assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
    runtime.dispose()
  })
}

test('a late accepted send does not erase an edited question or retry the stale question', async () => {
  const pending = deferred()
  const { runtime, state, calls, watches } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'ai.chat.send' ? pending.promise : {})
  await runtime.loadSettings()
  state.question = 'original'
  const sending = runtime.send()
  state.question = 'new draft'
  pending.resolve(event('queued'))
  assert.equal(await sending, true)
  assert.equal(state.question, 'new draft')
  watches[0].update(event('failed', { error_code: 'AI_OFFLINE', message: '无法连接。' }))
  assert.equal(state.question, 'new draft')
  assert.equal(await runtime.retry(), false)
  assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
  runtime.dispose()
})

test('partial failed reply stays explicitly incomplete and cannot publish suggestions from a late get', async () => {
  const published = []
  const { runtime, state, watches } = setup(async command => {
    if (command === 'ai.settings.get') return settings()
    if (command === 'ai.logs.list') return evidence()
    if (command === 'ai.chat.get') return session({ messages: [{ role: 'user', content: 'question' }, { role: 'assistant', content: 'partial' }], suggestions: [suggestion()] })
    return event('queued')
  }, { onSuggestions: (...args) => published.push(args) })
  await runtime.loadSettings()
  await runtime.setInstallation({ installation_id: id })
  await runtime.setAllowQueries(true)
  state.question = 'question'
  await runtime.send()
  watches[0].update(event('running', { session_id: id, text: 'partial' }))
  watches[0].update(event('failed', { session_id: id, text: 'partial', error_code: 'AI_STREAM_INTERRUPTED', message: '回复中断。' }))
  await flush()
  assert.equal(state.incompleteReply, true)
  assert.equal(state.messages.at(-1).content, 'partial')
  assert.equal(state.question, 'question')
  assert.deepEqual(state.suggestions, [])
  assert.deepEqual(published, [])
  runtime.dispose()
})

for (const change of ['installation', 'queries', 'evidence', 'new-session', 'settings', 'credentials', 'dispose']) {
  test(`${change} invalidates the failed retry instead of silently changing its sending scope`, async () => {
    const { runtime, state, calls, watches } = setup()
    await runtime.loadSettings()
    await runtime.setInstallation({ installation_id: id })
    await runtime.setAllowQueries(true)
    state.question = 'question'
    await runtime.send()
    watches[0].update(event('failed', { error_code: 'AI_OFFLINE', message: '无法连接。' }))
    if (change === 'installation') await runtime.setInstallation({ installation_id: otherId })
    if (change === 'queries') await runtime.setAllowQueries(false)
    if (change === 'evidence') await runtime.selectEvidence(otherId)
    if (change === 'new-session') await runtime.newSession()
    if (change === 'settings') await runtime.saveSettings()
    if (change === 'credentials') await runtime.clearCredentials()
    if (change === 'dispose') runtime.dispose()
    assert.equal(runtime.canRetry(), false)
    assert.equal(state.retryKind, null)
    assert.equal(await runtime.retry(), false)
    assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
    runtime.dispose()
  })
}

test('ordinary suggestion action busy prevents retry without starting another AI or Mod task', async () => {
  let busy = false
  const { runtime, state, calls, watches } = setup(undefined, { isActionBusy: () => busy })
  await runtime.loadSettings()
  state.question = 'question'
  await runtime.send()
  watches[0].update(event('failed', { error_code: 'AI_OFFLINE', message: '无法连接。' }))
  busy = true
  assert.equal(runtime.canRetry(), false)
  assert.equal(await runtime.retry(), false)
  assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
  assert.equal(calls.some(([command]) => command === 'mod.action'), false)
  busy = false
  assert.equal(runtime.canRetry(), true)
  runtime.dispose()
})

test('cancelling while task acceptance is pending preserves the draft and cancels the late accepted job', async () => {
  const pending = deferred()
  const { runtime, state, calls, watches } = setup(async command => command === 'ai.settings.get' ? settings() : command === 'ai.chat.send' ? pending.promise : {})
  await runtime.loadSettings()
  state.question = 'question'
  const sending = runtime.send()
  assert.equal(state.task, null)
  assert.equal(await runtime.cancel(), true)
  assert.equal(state.busy, false)
  assert.equal(state.question, 'question')
  assert.equal(state.notice, '已取消请求。')
  assert.equal(runtime.canRetry(), false)
  pending.resolve(event('queued', {}, 'late-job'))
  assert.equal(await sending, false)
  await flush()
  assert.equal(watches.length, 0)
  assert.deepEqual(calls.filter(([command]) => command === 'task.cancel'), [['task.cancel', { task_id: 'late-job' }]])
  runtime.dispose()
})

test('explicit cancel preserves accepted request draft and partial feedback without exposing retry', async () => {
  const { runtime, state, calls, watches } = setup()
  await runtime.loadSettings()
  state.question = 'question'
  await runtime.send()
  watches[0].update(event('running', { text: 'partial' }))
  await runtime.cancel()
  await flush()
  assert.equal(state.incompleteReply, true)
  assert.equal(state.question, 'question')
  assert.equal(state.retryKind, null)
  assert.equal(await runtime.retry(), false)
  assert.equal(calls.filter(([command]) => command === 'ai.chat.send').length, 1)
  runtime.dispose()
})

test('AI page exposes manual retry and incomplete feedback and allows cancellation before a task id exists', () => {
  const component = readFileSync(new URL('./LocalAiPanel.vue', import.meta.url), 'utf8')
  assert.match(component, /data-testid="ai-retry"[^>]*:disabled="!runtime\.canRetry\(\)"[^>]*@click="runtime\.retry"/)
  assert.match(component, /data-testid="ai-partial-reply"/)
  assert.match(component, /回复尚未完成，已保留接收到的内容/)
  assert.equal(/data-testid="ai-cancel"[^>]*:disabled="!state\.task/.test(component), false)
})
