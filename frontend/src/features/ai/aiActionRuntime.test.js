import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createAiActionRuntime, readSuggestions } from './aiActionRuntime.js'

const sessionId = '11111111-1111-4111-8111-111111111111'
const installationId = '22222222-2222-4222-8222-222222222222'
const suggestionId = '33333333-3333-4333-8333-333333333333'
const otherId = '44444444-4444-4444-8444-444444444444'
const key = '55555555-5555-4555-8555-555555555555'
const releaseId = '66666666-6666-4666-8666-666666666666'
const card = overrides => ({ suggestion_id: suggestionId, installation_id: installationId, release_id: releaseId, manifest_hash: 'a'.repeat(64), display_name: '<img src=x> Mod', version_raw: '1.0', action: 'install', reason: '当前游戏尚未安装此 Mod。', ...overrides })
const row = (status = 'not_installed', overrides = {}) => ({ release_id: releaseId, manifest_hash: 'a'.repeat(64), display_name: 'Real metadata', version_raw: '1.0', file_count: 1, state: status, message: '', ...overrides })
const page = (items, offset = 0) => ({ items, offset, total: items.length + offset, next_offset: null })
const resolve = overrides => ({ request: { installation_id: installationId, release_id: releaseId, manifest_hash: 'a'.repeat(64), action: 'install', idempotency_key: key, ...overrides }, offset: 0 })
const tick = () => new Promise(done => setImmediate(done))

test('external workspace suggestion is passive until confirmed, preserves other Mod and shows checked result', async () => {
  const suggestion = { kind: 'workspace', suggestion_id: suggestionId, installation_id: installationId, workspace_id: releaseId,
    snapshot_id: 'b'.repeat(64), display_name: 'ExternalAnalysis', version_raw: null, action: 'disable', reason: '最小隔离实验。' }
  const item = { id: releaseId, release_id: null, display_name: 'ExternalAnalysis', state: 'enabled', manageable: true }
  const external = { items: [item, { id: otherId, display_name: 'Keep', manageable: true }], enabled_ids: [releaseId, otherId], snapshot_id: 'b'.repeat(64) }
  const { runtime, state, calls, watchers, changed } = setup((command, payload) => {
    if (command === 'ai.suggestion.resolve') return { kind: 'workspace', request: { installation_id: installationId, snapshot_id: 'b'.repeat(64), enabled_ids: [otherId], idempotency_key: key } }
    if (command === 'mod.workspace') return structuredClone(external)
    if (command === 'mod.workspace.save') return { task_id: 'workspace-save', state: 'running' }
  })
  runtime.setSuggestions(sessionId, installationId, [suggestion])
  assert.deepEqual(calls, [])
  const executing = runtime.execute(suggestionId)
  await tick()
  assert.deepEqual(calls.map(([command]) => command), ['ai.suggestion.resolve', 'mod.workspace', 'mod.workspace.save'])
  assert.deepEqual(calls.at(-1)[1].enabled_ids, [otherId])
  assert.equal(calls.at(-1)[1].idempotency_key, key)
  watchers[0].update({ task_id: 'workspace-save', state: 'succeeded', data: { completed: true, workspace: { ...external, enabled_ids: [otherId], snapshot_id: 'c'.repeat(64) } } })
  assert.equal(await executing, true)
  assert.equal(state.cards[0].status, 'completed')
  assert.match(state.cards[0].result, /恢复副本/)
  assert.deepEqual(changed, [suggestionId])
  runtime.dispose()
})

test('workspace suggestion rejects stale snapshot or a resolution affecting extra Mod', async () => {
  for (const snapshot of ['c'.repeat(64), 'b'.repeat(64)]) {
    const suggestion = { kind: 'workspace', suggestion_id: suggestionId, installation_id: installationId, workspace_id: releaseId,
      snapshot_id: 'b'.repeat(64), display_name: 'ExternalAnalysis', version_raw: null, action: 'disable', reason: '测试。' }
    const { runtime, state, calls } = setup(command => {
      if (command === 'ai.suggestion.resolve') return { kind: 'workspace', request: { installation_id: installationId, snapshot_id: 'b'.repeat(64), enabled_ids: [], idempotency_key: key } }
      if (command === 'mod.workspace') return { items: [{ id: releaseId, display_name: 'ExternalAnalysis', manageable: true, state: 'enabled' }, { id: otherId, display_name: 'Keep' }], enabled_ids: [releaseId, otherId], snapshot_id: snapshot }
    })
    runtime.setSuggestions(sessionId, installationId, [suggestion])
    assert.equal(await runtime.execute(suggestionId), false)
    assert.equal(state.cards[0].status, 'failed')
    assert.equal(calls.some(([command]) => command === 'mod.workspace.save'), false)
    runtime.dispose()
  }
})
function deferred() { let done; const promise = new Promise(resolve => { done = resolve }); return { promise, resolve: done } }
function setup(handler) {
  const state = {}, calls = [], watchers = [], changed = []
  let rows = [row()], actionCount = 0
  const bridge = {
    async request(command, payload) {
      calls.push([command, structuredClone(payload)])
      if (handler) { const value = handler(command, payload); if (value !== undefined) return value }
      if (command === 'ai.suggestion.resolve') return resolve()
      if (command === 'mod.list') return page(rows, payload.offset)
      if (command === 'mod.action') return { task_id: `mod-${++actionCount}`, state: 'running' }
      if (command === 'task.cancel') return { task_id: payload.task_id, state: 'cancelled' }
      throw new Error(command)
    },
    watchTask(taskId, update, error) {
      const watcher = { taskId, update, error, disposed: false, cancelled: false,
        async dispose() { this.disposed = true }, async cancel() { this.cancelled = true; update({ task_id: taskId, state: 'cancelled' }) } }
      watchers.push(watcher)
      return watcher
    },
  }
  const runtime = createAiActionRuntime(bridge, state, value => changed.push(value.suggestion_id))
  runtime.setSuggestions(sessionId, installationId, [card()])
  return { runtime, state, calls, watchers, changed, setRows: value => { rows = value } }
}

test('suggestions are passive and fixed metadata cannot grant paths, shell, or another installation', () => {
  const { runtime, calls, state } = setup()
  assert.deepEqual(calls, [])
  assert.equal(state.cards[0].status, 'ready')
  for (const value of [card({ action: 'uninstall' }), card({ path: 'C:/game/GameAssembly.dll' }), card({ installation_id: otherId }), card({ manifest_hash: 'free/path' }), card({ command: 'shell' }), card({ release_id: 'any/path' }), card({ display_name: 'x'.repeat(201) }), card({ reason: 'x'.repeat(301) })]) assert.throws(() => readSuggestions([value], installationId))
  assert.throws(() => readSuggestions([card(), card()]))
  assert.throws(() => readSuggestions(Array.from({ length: 5 }, (_, index) => card({ suggestion_id: `0000000${index}-1111-4111-8111-111111111111` }))))
  runtime.dispose()
})

test('one click resolves registered metadata then uses only ordinary mod.action and validates refreshed state', async () => {
  const { runtime, state, calls, watchers, changed, setRows } = setup()
  const installing = runtime.execute(suggestionId)
  await tick()
  assert.equal(state.busy, true)
  assert.equal(await runtime.execute(suggestionId), false)
  assert.deepEqual(calls.map(([command]) => command), ['ai.suggestion.resolve', 'mod.list', 'mod.action'])
  assert.deepEqual(calls[0][1], { session_id: sessionId, suggestion_id: suggestionId })
  assert.deepEqual(calls[2][1], resolve().request)
  setRows([row('enabled')])
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await installing, true)
  assert.equal(state.cards[0].status, 'completed')
  assert.match(state.cards[0].result, /文件状态/)
  assert.deepEqual(changed, [suggestionId])
  assert.equal(await runtime.execute(suggestionId), false)
  assert.equal(calls.filter(([command]) => command === 'mod.action').length, 1)
  runtime.setSuggestions(sessionId, installationId, [card()])
  assert.equal(state.cards[0].status, 'completed')
  runtime.dispose()
})

test('target offset is loaded before ordinary action and metadata does not select arbitrary objects', async () => {
  const { runtime, calls, watchers, setRows } = setup((command, payload) => {
    if (command === 'ai.suggestion.resolve') return { ...resolve(), offset: 20 }
    if (command === 'mod.list' && payload.offset === 0) return page([row('enabled', { release_id: 'other' })])
  })
  const executing = runtime.execute(suggestionId)
  await tick()
  assert.deepEqual(calls.filter(([command]) => command === 'mod.list').map(([, payload]) => payload.offset), [0, 20])
  setRows([row('enabled')])
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await executing, true)
  runtime.dispose()
})

test('expired resolution and wrong identity never start a write request', async () => {
  for (const response of [Promise.reject(Object.assign(new Error('建议已过期，请重新提问。'), { code: 'AI_SUGGESTION_STALE' })), resolve({ installation_id: otherId }), resolve({ release_id: 'another-release' }), resolve({ action: 'disable' }), { ...resolve(), offset: -1 }, { ...resolve(), request: { ...resolve().request, path: 'C:/file' } }]) {
    const { runtime, state, calls } = setup(command => command === 'ai.suggestion.resolve' ? response : undefined)
    assert.equal(await runtime.execute(suggestionId), false)
    assert.equal(state.cards[0].status, 'failed')
    assert.equal(calls.some(([command]) => command === 'mod.action'), false)
    runtime.dispose()
  }
})

test('changed Mod state rejects an old suggestion without converting its action', async () => {
  const { runtime, calls, state, setRows } = setup()
  setRows([row('disabled')])
  assert.equal(await runtime.execute(suggestionId), false)
  assert.match(state.cards[0].error, /当前 Mod 状态/)
  assert.equal(calls.some(([command]) => command === 'mod.action'), false)
  runtime.dispose()
})

test('ordinary failure may retry the same card with its original idempotency key', async () => {
  const { runtime, state, calls, watchers, setRows } = setup()
  let executing = runtime.execute(suggestionId)
  await tick()
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { error_code: 'SPACE_REQUIRED', message: '可用空间不足。' } })
  assert.equal(await executing, false)
  assert.equal(state.cards[0].error, '可用空间不足。')
  executing = runtime.execute(suggestionId)
  await tick()
  setRows([row('enabled')])
  watchers[1].update({ task_id: 'mod-2', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await executing, true)
  assert.deepEqual(calls.filter(([command]) => command === 'mod.action').map(([, request]) => request.idempotency_key), [key, key])
  runtime.dispose()
})

test('completed task followed by failed refresh never reports successful installation', async () => {
  let reads = 0
  const { runtime, state, watchers, changed } = setup(command => {
    if (command === 'mod.list' && ++reads > 1) throw Object.assign(new Error('无法读取文件状态。'), { code: 'IO_ERROR' })
  })
  const executing = runtime.execute(suggestionId)
  await tick()
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await executing, false)
  assert.equal(state.cards[0].result, '')
  assert.equal(state.cards[0].status, 'failed')
  assert.deepEqual(changed, [])
  runtime.dispose()
})

test('ordinary cancellation waits for task settlement and records a cancellation rather than success', async () => {
  const { runtime, state, watchers, changed } = setup()
  const executing = runtime.execute(suggestionId)
  await tick()
  assert.equal(await runtime.cancel(), true)
  assert.equal(await executing, false)
  assert.equal(watchers[0].cancelled, true)
  assert.equal(state.cards[0].status, 'cancelled')
  assert.match(state.cards[0].error, /已取消/)
  assert.deepEqual(changed, [])
  runtime.dispose()
})

test('cancelling pending resolution or changing scope cannot attach an old action', async () => {
  for (const change of ['cancel', 'clear', 'dispose']) {
    const pending = deferred()
    const { runtime, state, calls } = setup(command => command === 'ai.suggestion.resolve' ? pending.promise : undefined)
    const executing = runtime.execute(suggestionId)
    await runtime[change]()
    pending.resolve(resolve())
    assert.equal(await executing, false)
    assert.equal(calls.some(([command]) => command === 'mod.action'), false)
    if (change === 'cancel') assert.equal(state.cards[0].status, 'cancelled')
    else assert.deepEqual(state.cards, [])
    runtime.dispose()
  }
})

test('late mod.action acceptance after clearing a conversation is cancelled without attaching a watcher', async () => {
  const pending = deferred()
  const { runtime, state, calls, watchers } = setup(command => command === 'mod.action' ? pending.promise : undefined)
  const executing = runtime.execute(suggestionId)
  await tick()
  runtime.clear()
  runtime.setSuggestions(otherId, otherId, [card({ installation_id: otherId })])
  pending.resolve({ task_id: 'late-job', state: 'running' })
  assert.equal(await executing, false)
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'late-job'))
  assert.equal(watchers.length, 0)
  assert.equal(state.sessionId, otherId)
  assert.equal(state.cards[0].status, 'ready')
  runtime.dispose()
})

test('leaving the page cancels active action and ignores late completion in another session', async () => {
  const { runtime, state, watchers, changed } = setup()
  const executing = runtime.execute(suggestionId)
  await tick()
  runtime.dispose()
  assert.equal(await executing, false)
  assert.equal(watchers[0].cancelled, true)
  assert.equal(watchers[0].disposed, true)
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.deepEqual(state.cards, [])
  assert.deepEqual(changed, [])
})

test('lost progress connection cancels the accepted ordinary job and shows no completed result', async () => {
  const { runtime, state, watchers, calls } = setup()
  const executing = runtime.execute(suggestionId)
  await tick()
  watchers[0].error(new Error('watch unavailable'))
  assert.equal(await executing, false)
  assert.ok(calls.some(([command, payload]) => command === 'task.cancel' && payload.task_id === 'mod-1'))
  assert.equal(state.cards[0].status, 'failed')
  assert.equal(state.cards[0].result, '')
  runtime.dispose()
})

test('failed cancel leaves the action visible and allows retry instead of trapping a disabled cancel button', async () => {
  const { runtime, state, watchers, setRows } = setup()
  const executing = runtime.execute(suggestionId)
  await tick()
  watchers[0].cancel = async () => { throw new Error('transport unavailable') }
  assert.equal(await runtime.cancel(), false)
  assert.equal(state.busy, true)
  assert.equal(state.cancelling, false)
  assert.match(state.cards[0].error, /取消请求未完成/)
  setRows([row('enabled')])
  watchers[0].update({ task_id: 'mod-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await executing, true)
  runtime.dispose()
})

test('suggestion card text is escaped and button flows have no plan approval or file path UI', () => {
  const component = readFileSync(new URL('./PlanCard.vue', import.meta.url), 'utf8')
  assert.match(component, /\{\{ suggestion\.display_name/)
  assert.match(component, /\{\{ suggestion\.reason/)
  assert.match(component, /\$emit\('execute', suggestion\.suggestion_id\)/)
  assert.equal(/v-html|plan_hash|approval_id|批准|路径/.test(component), false)
})
