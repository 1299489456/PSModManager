import test from 'node:test'
import assert from 'node:assert/strict'
import { createBridge, BridgeError } from './client.js'
import { reduceTask } from './reducer.js'

const event = (sequence, state = 'running', task_id = 'task-a') => ({ task_id, request_id: 'origin-request', sequence, type: 'progress', state, completed: sequence, total: 10, stage: 'checking', message_code: 'CHECKING', data: {} })
const waitFor = async predicate => { for (let n = 0; n < 100; n++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 5)) } assert.fail('Timed out waiting for bridge lifecycle') }
const reply = (request, data) => ({ request_id: request.request_id, ok: true, data })

test('workspace commands accept bounded identities and deny paths or malformed drafts', async () => {
  const id = '11111111-1111-4111-8111-111111111111', seen = []
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  await bridge.request('mod.workspace', { installation_id: id })
  await bridge.request('mod.workspace.detail', { installation_id: id, id, offset: 0, limit: 200 })
  const save = { installation_id: id, snapshot_id: 'a'.repeat(64), enabled_ids: [id], idempotency_key: id }
  assert.deepEqual(await bridge.request('mod.workspace.save', save), save)
  for (const extra of [{ enabled_ids: [id,id] }, { enabled_ids: ['path.dll'] }, { snapshot_id: id }, { path: 'C:/game' }, { approval: true }]) await assert.rejects(bridge.request('mod.workspace.save', { ...save, ...extra }), BridgeError)
  await assert.rejects(bridge.request('mod.workspace.detail', { installation_id: id, id, limit: 201 }), BridgeError)
  assert.equal(seen.length, 3)
  await bridge.dispose()
})

test('diagnostics commands use bounded source IDs, frozen snapshot and explicit question', async () => {
  const id = '11111111-1111-4111-8111-111111111111'
  const bridge = createBridge({ transport: async request => reply(request, request.payload) })
  await bridge.request('ai.diagnostics.sources', {})
  await bridge.request('ai.diagnostics.preview', { installation_id: id, source_ids: [id], include_context: true })
  await bridge.request('ai.diagnostics.read', { source_id: id, before: 32768 })
  await bridge.request('ai.diagnostics.send', { snapshot_id: id, question: '分析加载失败', session_id: id })
  for (const source_ids of [[], [id,id], ['Latest.log']]) await assert.rejects(bridge.request('ai.diagnostics.preview', { source_ids }), BridgeError)
  await assert.rejects(bridge.request('ai.diagnostics.read', { source_id: id, before: -1 }), BridgeError)
  await assert.rejects(bridge.request('ai.diagnostics.send', { snapshot_id: id, question: '' }), BridgeError)
  await assert.rejects(bridge.request('ai.diagnostics.send', { snapshot_id: id, question: '分析', path: 'C:/game/log' }), BridgeError)
  await bridge.dispose()
})

test('ordinary mod actions forward only selected identities and a bounded action', async () => {
  const seen = [], id = '11111111-1111-4111-8111-111111111111'
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  const base = { installation_id: id, release_id: id, manifest_hash: 'a'.repeat(64), idempotency_key: id }
  for (const action of ['install', 'enable', 'disable', 'uninstall']) assert.deepEqual(await bridge.request('mod.action', { ...base, action }), { ...base, action })
  await bridge.request('mod.list', { installation_id: id, offset: 0, limit: 20 })
  for (const extra of [{ action: { op: 'install' } }, { action: 'launch' }, { action: true }, { path: 'C:/game' }, { profile_id: id }, { approved: true }, { idempotency_key: 'invalid' }, { manifest_hash: 'bad' }]) await assert.rejects(bridge.request('mod.action', { ...base, action: 'install', ...extra }), BridgeError)
  for (const extra of [{ limit: 21 }, { offset: -1 }, { root: 'C:/game' }, { skip_guard: true }]) await assert.rejects(bridge.request('mod.list', { installation_id: id, ...extra }), BridgeError)
  assert.equal(seen.length, 5)
  await bridge.dispose()
})

test('ordinary mod errors keep bounded backend messages', async () => {
  const bridge = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false, error: { code: 'MOD_BUSY', message: '游戏正在运行，请退出后重试。' } }) })
  await assert.rejects(bridge.request('mod.list', { installation_id: 'installation' }), error => error.code === 'MOD_BUSY' && error.message === '游戏正在运行，请退出后重试。')
  await bridge.dispose()
})

test('optional game commands only forward the selected installation ID', async () => {
  const seen = []
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  for (const command of ['game.launch', 'game.status']) {
    assert.deepEqual(await bridge.request(command, { installation_id: 'installation' }), { installation_id: 'installation' })
    for (const extra of [{ path: 'C:/game.exe' }, { cwd: 'C:/Windows' }, { args: [] }, { shell: true }, { approved: true }]) await assert.rejects(bridge.request(command, { installation_id: 'installation', ...extra }), BridgeError)
  }
  assert.equal(seen.length, 2)
  await bridge.dispose()
})

test('game launch errors display the bounded backend user message', async () => {
  const bridge = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false, error: { code: 'GAME_LAUNCH_FAILED', message: '游戏程序不可用。' } }) })
  await assert.rejects(bridge.request('game.launch', { installation_id: 'installation' }), error => error.code === 'GAME_LAUNCH_FAILED' && error.message === '游戏程序不可用。')
  await bridge.dispose()
})

test('loader commands expose direct selected-installation actions and preserve useful errors', async () => {
  const seen = []
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  for (const command of ['loader.status', 'loader.install', 'loader.choose']) {
    assert.deepEqual(await bridge.request(command, { installation_id: 'installation' }), { installation_id: 'installation' })
    await assert.rejects(bridge.request(command, { installation_id: 'installation', path: 'C:/game' }), BridgeError)
  }
  const payload = { installation_id: 'installation', source_handle: 'selected-zip' }
  assert.deepEqual(await bridge.request('loader.install_local', payload), payload)
  await assert.rejects(bridge.request('loader.install_local', { ...payload, download_url: 'https://example.invalid' }), BridgeError)
  assert.equal(seen.length, 4)
  await bridge.dispose()
  const failed = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false,
    error: { code: 'LOADER_DOWNLOAD_FAILED', message: '下载失败，可改用本地 ZIP。' } }) })
  await assert.rejects(failed.request('loader.install', { installation_id: 'installation' }), error => error.code === 'LOADER_DOWNLOAD_FAILED' && error.message === '下载失败，可改用本地 ZIP。')
  await failed.dispose()
})

test('config saves use ordinary bounded fields without plans or caller paths', async () => {
  const seen = []
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  const base = { installation_id: 'installation', adapter_id: 'pswallpaper' }
  await bridge.request('config.read', base)
  const payload = { ...base, expected_revision: 'a'.repeat(64), changes: { WallpaperFit: 'fit', WallpaperBrightness: 80, LogToFile: false }, backup: false }
  assert.deepEqual(await bridge.request('config.save', payload), payload)
  for (const changes of [{ WallpaperBrightness: true }, { WallpaperBrightness: 101 }, { WallpaperFit: 'script' }, { LogToFile: 'false' }, { Unknown: 1 }, {}]) await assert.rejects(bridge.request('config.save', { ...payload, changes }), BridgeError)
  for (const extra of [{ path: 'C:/save' }, { approved: true }, { backup: 'false' }, { expected_revision: 1 }, { adapter_id: 'other' }]) await assert.rejects(bridge.request('config.save', { ...payload, ...extra }), BridgeError)
  assert.equal(seen.length, 2)
  await bridge.dispose()
})

test('config errors retain the backend user message without changing older command errors', async () => {
  const bridge = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false, error: { code: 'CONFIG_IO', message: '配置文件被占用。' } }) })
  await assert.rejects(bridge.request('config.read', { installation_id: 'installation', adapter_id: 'pswallpaper' }), error => error.code === 'CONFIG_IO' && error.message === '配置文件被占用。')
  await assert.rejects(bridge.request('app.info'), error => error.message === '本地操作未能完成，请重试或查看本地诊断。')
  await bridge.dispose()
})

test('readonly workbench commands forward bounded optional payloads without paths or write commands', async () => {
  const seen = []
  const bridge = createBridge({ transport: async request => { seen.push(request); return reply(request, request.payload) } })
  for (const [command, fields] of [
    ['inventory.scan', { force: false }], ['inventory.list', { generation: 2, limit: 50, offset: 0 }],
    ['inventory.detail', { entry_id: 'entry' }], ['inventory.contents', { entry_id: 'entry', limit: 20 }],
    ['inventory.graph', {}], ['diagnostics.explain', { snapshot_id: 'a'.repeat(64), session_id: null }],
    ['logs.watch', { user_logs: [] }], ['logs.sources', {}], ['session.list', { after: 0 }],
    ['evidence.list', { group_id: 'group', limit: 20 }], ['evidence.read', { evidence_id: 'evidence', limit: 8192 }],
  ]) assert.deepEqual(await bridge.request(command, { installation_id: 'installation', ...fields }), { installation_id: 'installation', ...fields })
  const count = seen.length
  for (const [command, fields] of [
    ['inventory.list', { limit: 51 }], ['inventory.scan', { force: 'false' }],
    ['diagnostics.explain', { rules: [] }], ['diagnostics.explain', { snapshot_id: 'bad' }],
    ['evidence.read', { evidence_id: 'evidence', limit: 8193 }], ['logs.watch', { user_logs: Array(9).fill('UserData/a.log') }],
    ['inventory.list', { generation: 0 }], ['inventory.list', { offset: -1 }], ['game.stop', {}],
  ]) await assert.rejects(bridge.request(command, { installation_id: 'installation', ...fields }), BridgeError)
  assert.equal(seen.length, count)
  await bridge.dispose()
})

test('request uses versioned dispatch and validates correlation', async () => {
  let seen
  const bridge = createBridge({ transport: async request => { seen = request; return reply(request, { name: 'PSModManager' }) } })
  assert.equal((await bridge.request('app.info')).name, 'PSModManager')
  assert.equal(seen.schema_version, 1)
  assert.match(seen.request_id, /^[0-9a-f-]{36}$/)
  const bad = createBridge({ transport: async () => ({ request_id: 'another-request', ok: true, data: {} }) })
  await assert.rejects(bad.request('app.info'), error => error.code === 'INVALID_RESPONSE')
})
test('unknown commands and extra or invalid parameters never reach transport', async () => {
  let calls = 0
  const bridge = createBridge({ transport: async () => { calls++ } })
  for (const [command, payload] of [['shell', {}], ['app.check', { path: 'secret' }], ['task.events', { subscription_id: 'sub', after_sequence: -1 }], ['task.cancel', { task_id: '' }]]) await assert.rejects(bridge.request(command, payload), BridgeError)
  assert.equal(calls, 0)
})
test('window control only forwards the fixed title bar operations', async () => {
  const sent = []
  const bridge = createBridge({ transport: async request => { sent.push(request); return reply(request, { maximized: false }) } })
  for (const op of ['state', 'minimize', 'toggle_maximize', 'close', 'drag', 'resize_se']) await bridge.request('window.control', { op })
  assert.deepEqual(sent.map(row => row.payload.op), ['state', 'minimize', 'toggle_maximize', 'close', 'drag', 'resize_se'])
  for (const payload of [{}, { op: 'move' }, { op: 'drag', x: 1 }, { op: 1 }]) await assert.rejects(bridge.request('window.control', payload), BridgeError)
  assert.equal(sent.length, 6)
})
test('backend and transport errors do not expose raw messages', async () => {
  const bridge = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false, error: { code: 'INTERNAL_ERROR', message: 'secret path/token' } }) })
  await assert.rejects(bridge.request('app.check'), error => error.code === 'INTERNAL_ERROR' && !error.message.includes('secret'))
  const broken = createBridge({ transport: async () => { throw new Error('secret path/token') } })
  await assert.rejects(broken.request('app.check'), error => error.code === 'TRANSPORT_UNAVAILABLE' && !error.message.includes('secret'))
})
test('reducer rejects duplicate, reordered, foreign task and request events', () => {
  const current = reduceTask(null, event(3), 'task-a')
  for (const candidate of [event(3), event(2), event(4, 'running', 'task-b'), { ...event(4), request_id: 'foreign' }, event(-1), event(4, 'unknown')]) assert.equal(reduceTask(current, candidate, 'task-a'), current)
  assert.equal(reduceTask(null, event(0), 'task-a'), null)
  assert.equal(reduceTask(null, { ...event(1), request_id: null }, 'task-a'), null)
  assert.equal(reduceTask(current, event(4), 'task-a').sequence, 4)
})
test('terminal is delivered once and cannot be replaced', () => {
  const terminal = reduceTask(null, event(5, 'succeeded'), 'task-a')
  assert.equal(reduceTask(terminal, event(6, 'failed'), 'task-a'), terminal)
  assert.equal(reduceTask(terminal, event(7, 'running'), 'task-a'), terminal)
})
test('subscribe then deduplicated events complete once and release subscription', async () => {
  const commands = [], updates = []
  const bridge = createBridge({ pollInterval: 1, transport: async request => {
    commands.push(request.command)
    if (request.command === 'task.subscribe') return reply(request, { subscription_id: 'sub', snapshot: event(1), events: [event(1)] })
    if (request.command === 'task.events') return reply(request, { subscription_id: 'sub', snapshot: event(4, 'succeeded'), events: [event(2), event(2), event(1), event(4, 'succeeded'), event(5, 'failed')] })
    return reply(request, {})
  } })
  bridge.watchTask('task-a', value => updates.push(value))
  await waitFor(() => commands.includes('task.unsubscribe'))
  assert.deepEqual(updates.map(value => value.sequence), [1, 2, 4])
  assert.equal(commands.filter(command => command === 'task.unsubscribe').length, 1)
  await bridge.dispose()
})
test('expired subscription reconnects with last sequence and authoritative snapshot', async () => {
  let subscriptions = 0
  const cursors = [], updates = []
  const bridge = createBridge({ pollInterval: 1, transport: async request => {
    if (request.command === 'task.subscribe') {
      cursors.push(request.payload.after_sequence)
      subscriptions++
      return reply(request, { subscription_id: `sub-${subscriptions}`, snapshot: event(subscriptions === 1 ? 3 : 9, subscriptions === 1 ? 'running' : 'succeeded'), events: [], resync_required: subscriptions > 1 })
    }
    if (request.command === 'task.events') return { request_id: request.request_id, ok: false, error: { code: 'SUBSCRIPTION_EXPIRED' } }
    return reply(request, {})
  } })
  bridge.watchTask('task-a', value => updates.push(value))
  await waitFor(() => updates.at(-1)?.state === 'succeeded')
  assert.deepEqual(cursors, [0, 3])
  assert.equal(updates.at(-1).sequence, 9)
  await bridge.dispose()
})
test('dispose while subscription response is pending releases late subscription', async () => {
  let resolveSubscribe
  const released = []
  const bridge = createBridge({ transport: request => {
    if (request.command === 'task.subscribe') return new Promise(resolve => { resolveSubscribe = data => resolve(reply(request, data)) })
    released.push(request.payload.subscription_id)
    return Promise.resolve(reply(request, {}))
  } })
  const watch = bridge.watchTask('task-a', () => assert.fail('disposed watcher updated'))
  await watch.dispose()
  resolveSubscribe({ subscription_id: 'late', snapshot: event(1), events: [] })
  await waitFor(() => released.length === 1)
  assert.deepEqual(released, ['late'])
  await bridge.dispose()
})
test('repeated mounting and disposal recycles subscriptions without polling leaks', async () => {
  const active = new Set()
  let count = 0, calls = 0
  for (let n = 0; n < 3; n++) {
    const bridge = createBridge({ pollInterval: 2, transport: async request => {
      calls++
      if (request.command === 'task.subscribe') { const subscription_id = `sub-${++count}`; active.add(subscription_id); return reply(request, { subscription_id, snapshot: event(1), events: [] }) }
      if (request.command === 'task.unsubscribe') active.delete(request.payload.subscription_id)
      return reply(request, { subscription_id: request.payload.subscription_id, snapshot: event(1), events: [] })
    } })
    bridge.watchTask('task-a', () => {})
    await waitFor(() => active.size === 1)
    await bridge.dispose()
    await bridge.dispose()
    assert.equal(active.size, 0)
    assert.throws(() => bridge.watchTask('task-a', () => {}), BridgeError)
  }
  const previous = calls
  await new Promise(resolve => setTimeout(resolve, 15))
  assert.equal(calls, previous)
})
test('cancelling old task is bound to its task ID and cannot affect new task', async () => {
  const cancelled = [], updates = []
  const bridge = createBridge({ pollInterval: 1000, transport: async request => {
    if (request.command === 'task.subscribe') return reply(request, { subscription_id: request.payload.task_id, snapshot: event(1, 'running', request.payload.task_id), events: [] })
    if (request.command === 'task.cancel') { cancelled.push(request.payload.task_id); return reply(request, event(2, 'cancelled', request.payload.task_id)) }
    return reply(request, {})
  } })
  const old = bridge.watchTask('task-a', () => {})
  const current = bridge.watchTask('task-b', value => updates.push(value))
  await waitFor(() => current.snapshot !== null)
  await old.cancel()
  assert.deepEqual(cancelled, ['task-a'])
  assert.equal(current.snapshot.state, 'running')
  assert.deepEqual(updates.map(value => value.task_id), ['task-b'])
  await bridge.dispose()
})
test('closed bridge rejects fresh commands while releasing owned subscriptions', async () => {
  const commands = []
  const bridge = createBridge({ transport: async request => {
    commands.push(request.command)
    return reply(request, { subscription_id: 'owned', snapshot: event(1), events: [] })
  } })
  const watcher = bridge.watchTask('task-a', () => {})
  await waitFor(() => watcher.snapshot !== null)
  await bridge.dispose()
  for (const command of ['app.info', 'app.check']) await assert.rejects(bridge.request(command), error => error.code === 'BRIDGE_CLOSED')
  await assert.rejects(bridge.request('task.unsubscribe', { subscription_id: 'other' }), error => error.code === 'BRIDGE_CLOSED')
  assert.deepEqual(commands, ['task.subscribe', 'task.unsubscribe'])
})

test('profile bridge carries revisions and installation-bound native preview without arbitrary paths', async () => {
  const requests = []
  const bridge = createBridge({ transport: async request => {
    requests.push(request)
    return reply(request, { profile_id: 'profile', revision: 2 })
  } })
  await bridge.request('profile.edit', { profile_id: 'profile', expected_revision: 1, action: { op: 'undo' } })
  await bridge.request('profile.import_preview', { source_handle: 'source', installation_id: 'installation' })
  await bridge.request('profile.save_export', { profile_id: 'profile' })
  assert.equal(requests[0].payload.expected_revision, 1)
  for (const payload of [
    { profile_id: 'profile', expected_revision: true, action: { op: 'undo' } },
    { profile_id: 'profile', expected_revision: -1, action: { op: 'undo' } },
    { profile_id: 'profile', expected_revision: 1, action: [] },
  ]) await assert.rejects(bridge.request('profile.edit', payload), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('profile.save_export', { profile_id: 'profile', path: 'C:/private/file.json' }), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('profile.import_preview', { source_handle: 'source' }), error => error.code === 'INVALID_REQUEST')
  assert.equal(requests.length, 3)
  await bridge.dispose()
})

test('compatibility bridge only accepts revision-bound read-only preview arguments', async () => {
  const requests = []
  const bridge = createBridge({ transport: async request => { requests.push(request); return reply(request, { task_id: 'check' }) } })
  await bridge.request('compatibility.check', { profile_id: 'profile', expected_revision: 2, enable_release_ids: ['release'], disable_release_ids: [], profile_kind: 'draft' })
  for (const extra of [{ path: 'C:/game' }, { profile_kind: 'runtime' }, { disable_release_ids: 'release' }, { enable_release_ids: Array(201).fill('r') }, { expected_revision: true }, { apply: true }]) {
    await assert.rejects(bridge.request('compatibility.check', { profile_id: 'profile', expected_revision: 2, ...extra }), error => error.code === 'INVALID_REQUEST')
  }
  assert.equal(requests.length, 1)
  await bridge.dispose()
})

test('case027 real bridge bounds adoption and release details without frontend roots', async () => {
  const requests = [], id = '11111111-1111-4111-8111-111111111111', hash = 'a'.repeat(64)
  const bridge = createBridge({ transport: async request => { requests.push(request); return reply(request, {}) } })
  await bridge.request('package.adopt_preview', { installation_id: id })
  await bridge.request('package.adopt_files', { snapshot_id: id, offset: 20, limit: 20 })
  await bridge.request('package.adopt', { snapshot_id: id, manifest_hash: hash, paths: ['Mods/中文 %.dll'], display_name: '本地包' })
  await bridge.request('package.detail', { release_id: id, manifest_hash: hash, offset: 0, limit: 20 })
  for (const extra of [{ root: 'C:/game' }, { limit: 21 }, { offset: -1 }, { manifest_hash: 'bad' }]) {
    await assert.rejects(bridge.request('package.detail', { release_id: id, manifest_hash: hash, ...extra }), error => error.code === 'INVALID_REQUEST')
  }
  for (const paths of [[], 'Mods/test.dll', Array(129).fill('Mods/test.dll')]) {
    await assert.rejects(bridge.request('package.adopt', { snapshot_id: id, manifest_hash: hash, paths, display_name: '本地包' }), error => error.code === 'INVALID_REQUEST')
  }
  await assert.rejects(bridge.request('package.adopt_files', { snapshot_id: hash }), error => error.code === 'INVALID_REQUEST')
  assert.equal(requests.length, 4)
  await bridge.dispose()
})

test('case030 deployment and assistant apply forward only approved immutable bindings', async () => {
  const requests = [], id = '11111111-1111-4111-8111-111111111111', hash = 'a'.repeat(64)
  const bridge = createBridge({ transport: async request => { requests.push(request); return reply(request, {}) } })
  const binding = { plan_id: id, plan_hash: hash, approval_id: id }
  for (const command of ['plan.apply', 'assistant.apply_plan']) {
    assert.deepEqual(await bridge.request(command, binding), {})
    for (const extra of [{ approval_id: undefined }, { approval_id: 'free/path' }, { approval_id: 'z1111111-1111-4111-8111-111111111111' }, { approved: true }, { actor: 'AI' }, { paths: ['Mods/test.dll'] }, { root: 'C:/game' }]) {
      await assert.rejects(bridge.request(command, { ...binding, ...extra }), error => error.code === 'INVALID_REQUEST')
    }
  }
  await assert.rejects(bridge.request('assistant.approve_plan', binding), error => error.code === 'INVALID_REQUEST')
  assert.equal(requests.length, 2); assert.deepEqual(requests.map(request => request.payload), [binding, binding])
  await bridge.dispose()
})

test('case032 deployment status and recovery accept only installation identity and optional bound plan', async () => {
  const requests = [], id = '11111111-1111-4111-8111-111111111111'
  const bridge = createBridge({ transport: async request => { requests.push(request); return reply(request, {}) } })
  await bridge.request('deployment.status', { installation_id: id })
  await bridge.request('deployment.status', { installation_id: id, plan_id: id })
  await bridge.request('deployment.recover', { installation_id: id })
  for (const command of ['deployment.status', 'deployment.recover']) {
    for (const extra of [{ paths: ['Mods/file.dll'] }, { force: true }, { skip_guard: true }, { generation: id }, { root: 'C:/game' }]) await assert.rejects(bridge.request(command, { installation_id: id, ...extra }), error => error.code === 'INVALID_REQUEST')
  }
  await assert.rejects(bridge.request('deployment.status', { installation_id: id, plan_id: 'free/path' }), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('deployment.recover', { installation_id: id, plan_id: id }), error => error.code === 'INVALID_REQUEST')
  assert.equal(requests.length, 3); await bridge.dispose()
})

test('case034 history, rollback and recheck expose bounded identities without path or force authority', async () => {
  const requests = [], id = '11111111-1111-4111-8111-111111111111', hash = 'a'.repeat(64)
  const bridge = createBridge({ transport: async request => { requests.push(request); return reply(request, {}) } })
  await bridge.request('deployment.history', { installation_id: id, offset: 0, limit: 10 })
  await bridge.request('deployment.rollback.create', { installation_id: id, transaction_id: id, expected_generation: id, idempotency_key: id })
  await bridge.request('deployment.rollback', { plan_id: id, plan_hash: hash, approval_id: id })
  await bridge.request('deployment.conflicts.recheck', { installation_id: id })
  for (const [command, base] of [
    ['deployment.history', { installation_id: id }],
    ['deployment.conflicts.recheck', { installation_id: id }],
    ['deployment.rollback.create', { installation_id: id, transaction_id: id, expected_generation: id, idempotency_key: id }],
    ['deployment.rollback', { plan_id: id, plan_hash: hash, approval_id: id }],
  ]) for (const extra of [{ path: 'C:/game' }, { force: true }, { skip_guard: true }, { actor: 'AI' }]) await assert.rejects(bridge.request(command, { ...base, ...extra }), error => error.code === 'INVALID_REQUEST')
  for (const extra of [{ offset: -1 }, { offset: true }, { limit: 11 }, { limit: 0 }, { limit: true }]) await assert.rejects(bridge.request('deployment.history', { installation_id: id, ...extra }), error => error.code === 'INVALID_REQUEST')
  for (const key of ['transaction_id', 'expected_generation', 'idempotency_key']) await assert.rejects(bridge.request('deployment.rollback.create', { installation_id: id, transaction_id: id, expected_generation: id, idempotency_key: id, [key]: '../free-path' }), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('deployment.rollback', { plan_id: id, plan_hash: hash }), error => error.code === 'INVALID_REQUEST')
  assert.equal(requests.length, 4); await bridge.dispose()
})
