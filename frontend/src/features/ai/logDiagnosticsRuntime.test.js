import test from 'node:test'
import assert from 'node:assert/strict'
import { createLocalAiRuntime } from './localAiRuntime.js'
import { createLogDiagnosticsRuntime } from './logDiagnosticsRuntime.js'
import { diagnosticMarkdown } from './diagnosticMarkdown.js'

const game = '11111111-1111-4111-8111-111111111111'
const source = '22222222-2222-4222-8222-222222222222'
const snapshot = '33333333-3333-4333-8333-333333333333'
const session = '44444444-4444-4444-8444-444444444444'
const settings = { provider: 'openai_compatible', model: 'model', base_url: 'http://127.0.0.1/v1', configured: true, key_status: 'session', message: '' }
const preview = { snapshot_id: snapshot, context: {}, warnings: [], logs: [{ source_id: source, label: '游戏日志', text: 'ERROR at line one', truncated: false }] }
const page = (extra = {}) => ({ source_id: source, text: 'original logs', byte_start: 100, byte_end: 200, bytes_total: 200, previous_before: 100, ...extra })
const tick = () => new Promise(resolve => setImmediate(resolve))

function setup(handler) {
  const aiState = {}, state = {}, calls = [], watchers = []
  const bridge = {
    async request(command, payload = {}) {
      calls.push({ command, payload })
      const override = await handler?.(command, payload)
      if (override !== undefined) return override
      if (command === 'ai.settings.get') return settings
      if (command === 'ai.diagnostics.sources') return { sources: [{ source_id: source, label: '游戏日志', kind: 'game', status: 'available', default: true }], warnings: [] }
      if (command === 'ai.diagnostics.preview') return structuredClone(preview)
      if (command === 'ai.diagnostics.read') return page(payload.before ? { byte_start: 0, byte_end: 100, previous_before: null, text: 'older logs' } : {})
      if (command === 'ai.chat.get') return { session_id: session, state: 'idle', messages: [{ role: 'assistant', content: '分析结论' }], provider: 'openai_compatible', model: 'model', suggestions: [] }
      if (command === 'ai.chat.clear') return { cleared: true }
      return { task_id: 'job', state: 'queued', data: {} }
    },
    watchTask(id, update) { watchers.push(update); return { async dispose() {} } },
  }
  const ai = createLocalAiRuntime(bridge, aiState, { evidencePicker: false })
  const runtime = createLogDiagnosticsRuntime(bridge, ai, aiState, state)
  return { runtime, ai, aiState, state, calls, watchers }
}

test('opening prepares locally readable logs and exact context without sending to provider', async () => {
  const { runtime, ai, state, aiState, calls } = setup()
  await ai.loadSettings()
  assert.equal(await runtime.load({ installation_id: game }), true)
  assert.equal(state.preview.snapshot_id, snapshot)
  assert.equal(state.pages[0].text, 'original logs')
  assert.equal(aiState.diagnosticSnapshotId, snapshot)
  assert.equal(calls.some(row => /send|ai.test/.test(row.command)), false)
  assert.equal(calls.some(row => row.command === 'ai.logs.list'), false)
})

test('default reader prefers available Latest.log and falls back to manager without changing selection or sending scope', async () => {
  const managerId = '66666666-6666-4666-8666-666666666666'
  for (const hasGameLog of [true, false]) {
    const sources = [
      { source_id: managerId, kind: 'manager', label: '管理器日志', relative_path: 'logs/psmodmanager.log', status: 'available', default: true },
      { source_id: source, kind: 'game', label: '游戏日志', relative_path: 'MelonLoader/Latest.log', status: hasGameLog ? 'available' : 'missing', default: hasGameLog },
    ]
    const chosen = sources.filter(row => row.default).map(row => row.source_id)
    const app = setup((command, payload) => {
      if (command === 'ai.diagnostics.sources') return { sources, warnings: [] }
      if (command === 'ai.diagnostics.preview') return { ...preview, logs: chosen.map(source_id => ({ ...preview.logs[0], source_id })) }
      if (command === 'ai.diagnostics.read') return page({ source_id: payload.source_id })
    })
    await app.ai.loadSettings()
    await app.runtime.load({ installation_id: game })
    assert.equal(app.state.activeSourceId, hasGameLog ? source : managerId)
    assert.deepEqual(app.state.selectedIds, chosen)
    assert.deepEqual(app.calls.find(row => row.command === 'ai.diagnostics.preview').payload.source_ids, chosen)
    assert.equal(app.calls.find(row => row.command === 'ai.diagnostics.read').payload.source_id, hasGameLog ? source : managerId)
    assert.equal(app.calls.some(row => row.command.endsWith('.send')), false)
  }
})

test('analysis starts a real diagnostic task, streams a result and follows up with same snapshot', async () => {
  const { runtime, ai, aiState, calls, watchers } = setup()
  await ai.loadSettings()
  await runtime.load({ installation_id: game })
  assert.equal(await runtime.analyze(), true)
  assert.equal(aiState.busy, true)
  assert.deepEqual(calls.at(-1).payload, { question: '请分析这些日志，找出游戏、Mod 或管理器的问题，引用相关日志行，说明最可能原因和下一步排查方法。', snapshot_id: snapshot })
  assert.equal(calls.at(-1).command, 'ai.diagnostics.send')
  watchers[0]({ task_id: 'job', state: 'running', stage: 'ai_stream', data: { session_id: session, text: '正在分析' } })
  assert.equal(aiState.streamedText, '正在分析')
  watchers[0]({ task_id: 'job', state: 'succeeded', data: { session_id: session, text: '分析结论', suggestions: [] } })
  await tick()
  aiState.question = '怎么解决？'
  await ai.send()
  assert.deepEqual(calls.at(-1).payload, { question: '怎么解决？', snapshot_id: snapshot, session_id: session })
})

test('ordinary questions remain available and do not accidentally attach log snapshots', async () => {
  const { runtime, ai, aiState, calls } = setup()
  await ai.loadSettings(); await runtime.load({ installation_id: game }); await runtime.chatOnly()
  aiState.question = '一般如何检查依赖？'
  await ai.send()
  assert.equal(calls.at(-1).command, 'ai.chat.send')
  assert.equal(Object.hasOwn(calls.at(-1).payload, 'snapshot_id'), false)
})

test('unconfigured, empty or unreadable logs give visible reasons and never send', async () => {
  const unconfigured = setup(command => command === 'ai.settings.get' ? { ...settings, configured: false } : undefined)
  await unconfigured.ai.loadSettings(); await unconfigured.runtime.load()
  assert.equal(await unconfigured.runtime.analyze(), false)
  assert.match(unconfigured.aiState.error, /配置 AI/)
  const empty = setup(command => command === 'ai.diagnostics.preview' ? { ...preview, logs: [{ ...preview.logs[0], text: '' }] } : undefined)
  await empty.ai.loadSettings(); await empty.runtime.load()
  assert.equal(await empty.runtime.analyze(), false)
  assert.match(empty.state.error, /没有内容/)
  const failed = setup(command => { if (command === 'ai.diagnostics.preview') throw new Error('private path') })
  await failed.ai.loadSettings(); await failed.runtime.load()
  assert.match(failed.state.error, /无法读取/)
  for (const instance of [unconfigured, empty, failed]) assert.equal(instance.calls.some(row => row.command.endsWith('.send')), false)
})

test('reader moves through older and newer pages without changing the analysis snapshot', async () => {
  const { runtime, ai, aiState, state, calls } = setup()
  await ai.loadSettings(); await runtime.load({ installation_id: game })
  await runtime.olderPage()
  assert.equal(state.pageIndex, 1)
  assert.equal(state.pages[1].text, 'older logs')
  assert.equal(aiState.diagnosticSnapshotId, snapshot)
  const count = calls.length
  runtime.newerPage()
  assert.equal(state.pageIndex, 0)
  await runtime.olderPage()
  assert.equal(calls.length, count)
  assert.equal(calls.some(row => row.command.endsWith('.send')), false)
})

test('continuous reader loads older pages once and stops at the start of the log', async () => {
  const { runtime, ai, aiState, state, calls } = setup()
  await ai.loadSettings(); await runtime.load({ installation_id: game })
  assert.equal(await runtime.loadOlder(), true)
  assert.deepEqual(state.pages.map(row => row.text), ['original logs', 'older logs'])
  const reads = calls.filter(row => row.command === 'ai.diagnostics.read')
  assert.equal(reads.at(-1).payload.before, 100)
  assert.equal(await runtime.loadOlder(), false)
  assert.equal(calls.filter(row => row.command === 'ai.diagnostics.read').length, reads.length)
  assert.equal(aiState.diagnosticSnapshotId, snapshot)
})

test('switching log tabs reuses loaded pages until the logs are refreshed', async () => {
  const other = '55555555-5555-4555-8555-555555555555'
  const { runtime, ai, state, calls } = setup()
  await ai.loadSettings(); await runtime.load({ installation_id: game })
  await runtime.loadOlder()
  const reads = () => calls.filter(row => row.command === 'ai.diagnostics.read').length
  const before = reads()
  await runtime.viewSource(other)
  await runtime.viewSource(source)
  assert.equal(reads(), before + 1)
  assert.equal(state.pages.length, 2)
  await runtime.reloadSource()
  assert.equal(state.pages.length, 1)
  assert.equal(reads(), before + 2)
})

test('changing selected scope drops stale preview responses', async () => {
  let pending
  const first = new Promise(resolve => { pending = resolve })
  let count = 0
  const app = setup(command => command === 'ai.diagnostics.preview' && ++count === 1 ? first : undefined)
  await app.ai.loadSettings()
  const oldLoad = app.runtime.load({ installation_id: game })
  await tick()
  await app.runtime.load(null)
  pending({ ...preview, snapshot_id: '55555555-5555-4555-8555-555555555555' })
  await oldLoad
  assert.equal(app.state.installationId, null)
  assert.equal(app.state.preview.snapshot_id, snapshot)
  assert.equal(app.aiState.diagnosticSnapshotId, snapshot)
})

test('Markdown formats evidence but cannot inject HTML or load remote images', () => {
  const html = diagnosticMarkdown('## 原因\n**缺少依赖**\n<script>alert(1)</script>\n![tracking](https://example.com/private.png)')
  assert.match(html, /<h2>原因<\/h2>/)
  assert.match(html, /<strong>缺少依赖<\/strong>/)
  assert.equal(html.includes('<script>'), false)
  assert.equal(html.includes('<img'), false)
})
