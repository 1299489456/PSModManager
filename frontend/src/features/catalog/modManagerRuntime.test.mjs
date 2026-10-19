import test from 'node:test'
import assert from 'node:assert/strict'
import { createModManagerRuntime, modActions, modStateText } from './modManagerRuntime.js'

const releaseId = '11111111-1111-4111-8111-111111111111'
const row = (state = 'not_installed', extra = {}) => ({ release_id: releaseId, manifest_hash: 'a'.repeat(64), display_name: '测试 Mod', version_raw: null, file_count: 2, state, message: '', ...extra })
const page = (items = [row()], offset = 0, next_offset = null, total = items.length) => ({ items, offset, next_offset, total })
const tick = () => new Promise(resolve => setTimeout(resolve, 0))
function fixture(overrides = {}) {
  const calls = [], watchers = []
  let serial = 0, rows = [row()]
  const bridge = {
    calls, watchers,
    async request(command, payload) {
      calls.push([command, structuredClone(payload)])
      if (overrides[command]) return overrides[command](payload)
      if (command === 'mod.list') return page(rows, payload.offset)
      if (command === 'package.detail') return { release_id: payload.release_id, manifest_hash: payload.manifest_hash, files: [], offset: payload.offset }
      if (command === 'mod.action') return { task_id: `action-${++serial}`, state: 'running' }
      throw new Error(command)
    },
    watchTask(task_id, update, error) {
      const handle = { task_id, update, error, disposed: false, cancelled: false,
        async dispose() { this.disposed = true }, async cancel() { this.cancelled = true; update({ task_id, state: 'cancelled', data: {} }) } }
      watchers.push(handle); return handle
    },
    setRows(value) { rows = value },
  }
  const state = {}, runtime = createModManagerRuntime(bridge, state)
  return { bridge, state, runtime }
}

test('visible states expose only useful ordinary actions', () => {
  assert.deepEqual(modActions('not_installed'), ['install'])
  assert.deepEqual(modActions('enabled'), ['disable', 'uninstall'])
  assert.deepEqual(modActions('disabled'), ['enable', 'uninstall'])
  assert.deepEqual(modActions('drifted'), [])
  assert.deepEqual(modActions('unknown'), [])
  assert.equal(modStateText('enabled'), '已启用')
})

test('selected installation reads paged facts without Profile or approval calls', async () => {
  const { bridge, state, runtime } = fixture()
  assert.equal(await runtime.setInstallation('installation'), true)
  assert.equal(state.items[0].state, 'not_installed')
  assert.equal(runtime.canAct(state.items[0], 'install'), true)
  assert.deepEqual(bridge.calls, [['mod.list', { installation_id: 'installation', offset: 0, limit: 20 }]])
  runtime.dispose()
})

test('real task lifecycle waits for completion then confirms actual list state', async () => {
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  assert.equal(state.busy, true)
  assert.equal(runtime.canAct(state.items[0], 'install'), false)
  assert.equal(await runtime.perform(state.items[0], 'install'), false)
  const [, payload] = bridge.calls.find(([command]) => command === 'mod.action')
  assert.deepEqual(Object.keys(payload).sort(), ['action', 'idempotency_key', 'installation_id', 'manifest_hash', 'release_id'])
  assert.match(payload.idempotency_key, /^[0-9a-f-]{36}$/)
  bridge.setRows([row('enabled')])
  bridge.watchers[0].update({ task_id: 'action-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await installing, true)
  assert.equal(state.notice, '安装完成。')
  assert.equal(state.items[0].state, 'enabled')
  assert.equal(state.busy, false)
  assert.ok(bridge.watchers[0].disposed)
  runtime.dispose()
})

test('disable, enable and uninstall each send one ordinary action and verify their resulting state', async () => {
  for (const [before, action, after] of [['enabled', 'disable', 'disabled'], ['disabled', 'enable', 'enabled'], ['enabled', 'uninstall', 'not_installed']]) {
    const { bridge, state, runtime } = fixture()
    bridge.setRows([row(before)])
    await runtime.setInstallation('installation')
    const executing = runtime.perform(state.items[0], action)
    await tick()
    bridge.setRows([row(after)])
    bridge.watchers[0].update({ task_id: 'action-1', state: 'succeeded', data: { completed: true, state: after } })
    assert.equal(await executing, true)
    assert.equal(state.items[0].state, after)
    assert.equal(bridge.calls.filter(([command]) => command === 'mod.action').length, 1)
    assert.equal(bridge.calls.find(([command]) => command === 'mod.action')[1].action, action)
    runtime.dispose()
  }
})

test('list jobs use bridge task snapshots rather than treating a task as the list', async () => {
  const { bridge, state, runtime } = fixture({ 'mod.list': () => ({ task_id: 'list', state: 'running' }) })
  const reading = runtime.setInstallation('installation')
  await tick()
  assert.equal(state.loading, true)
  bridge.watchers[0].update({ task_id: 'list', state: 'succeeded', data: page() })
  assert.equal(await reading, true)
  assert.equal(state.items.length, 1)
  assert.equal(state.loading, false)
  assert.ok(bridge.watchers[0].disposed)
  runtime.dispose()
})

test('business errors inside succeeded task stay failed and refresh facts', async () => {
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  bridge.watchers[0].update({ task_id: 'action-1', state: 'succeeded', data: { error_code: 'SPACE_REQUIRED', message: '可用空间不足。' } })
  assert.equal(await installing, false)
  assert.equal(state.notice, '')
  assert.equal(state.error, '可用空间不足。')
  assert.equal(state.errorCode, 'SPACE_REQUIRED')
  assert.equal(bridge.calls.filter(([command]) => command === 'mod.list').length, 2)
  assert.equal(state.items[0].state, 'not_installed')
  runtime.dispose()
})

test('permission request failure is visible and never produces success', async () => {
  const error = Object.assign(new Error('文件没有写入权限。'), { code: 'PERMISSION_DENIED' })
  const { state, runtime } = fixture({ 'mod.action': () => { throw error } })
  await runtime.setInstallation('installation')
  assert.equal(await runtime.perform(state.items[0], 'install'), false)
  assert.equal(state.error, '文件没有写入权限。')
  assert.equal(state.notice, '')
  assert.equal(state.busy, false)
  runtime.dispose()
})

test('completion without matching actual state is not shown as finished', async () => {
  const { state, runtime } = fixture({ 'mod.action': () => ({ task_id: 'done', state: 'succeeded', data: { completed: true, state: 'enabled' } }) })
  await runtime.setInstallation('installation')
  assert.equal(await runtime.perform(state.items[0], 'install'), false)
  assert.match(state.error, /不一致/)
  assert.equal(state.notice, '')
  runtime.dispose()
})

test('explicit cancellation remains a cancellation and refreshes the resulting state', async () => {
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  assert.equal(await runtime.cancel(), true)
  assert.equal(await installing, false)
  assert.equal(bridge.watchers[0].cancelled, true)
  assert.equal(state.notice, '')
  assert.match(state.error, /已取消/)
  assert.equal(state.items[0].state, 'not_installed')
  runtime.dispose()
})

test('installation change detaches old action without cancelling authorized work or overwriting new state', async () => {
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('first')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  const watcher = bridge.watchers[0]
  await runtime.setInstallation('second')
  assert.equal(await installing, false)
  watcher.update({ task_id: 'action-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(state.installationId, 'second')
  assert.equal(state.items[0].state, 'not_installed')
  assert.equal(state.notice, '')
  assert.equal(watcher.cancelled, false)
  assert.equal(watcher.disposed, true)
  runtime.dispose()
})

test('late command response after switching installations cannot attach an old watcher', async () => {
  let resolveAction
  const { bridge, state, runtime } = fixture({ 'mod.action': () => new Promise(resolve => { resolveAction = resolve }) })
  await runtime.setInstallation('first')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  await runtime.setInstallation('second')
  resolveAction({ task_id: 'old', state: 'running' })
  assert.equal(await installing, false)
  assert.equal(bridge.watchers.length, 0)
  assert.equal(state.installationId, 'second')
  runtime.dispose()
})

test('failed list refresh disables stale row actions until a successful refresh', async () => {
  let fail = false
  const { state, runtime } = fixture({ 'mod.list': () => { if (fail) throw new Error('读取失败。'); return page() } })
  await runtime.setInstallation('installation')
  fail = true
  assert.equal(await runtime.refresh(), false)
  assert.equal(state.stale, true)
  assert.equal(runtime.canAct(state.items[0], 'install'), false)
  fail = false
  await runtime.refresh()
  assert.equal(state.stale, false)
  assert.equal(runtime.canAct(state.items[0], 'install'), true)
  runtime.dispose()
})

test('import reveal clears search and locates a release on a later bounded page', async () => {
  const { bridge, state, runtime } = fixture({ 'mod.list': payload => payload.offset === 0 ? page([row('enabled', { release_id: 'other' })], 0, 20, 21) : page([row()], 20, null, 21) })
  await runtime.setInstallation('installation')
  state.query = 'old query'
  assert.equal(await runtime.reveal(releaseId), true)
  assert.equal(state.query, '')
  assert.equal(state.offset, 20)
  assert.equal(state.focusReleaseId, releaseId)
  assert.ok(bridge.calls.every(([, payload]) => payload.limit === 20))
  runtime.dispose()
})

test('file details bind selected immutable release and close invalidates its pending job', async () => {
  const { bridge, state, runtime } = fixture({ 'package.detail': () => ({ task_id: 'detail', state: 'running' }) })
  await runtime.setInstallation('installation')
  const reading = runtime.showDetail(state.items[0])
  await tick()
  runtime.closeDetail()
  assert.equal(await reading, false)
  bridge.watchers[0].update({ task_id: 'detail', state: 'succeeded', data: { ...row(), files: [] } })
  assert.equal(state.detail, null)
  assert.equal(bridge.watchers[0].disposed, true)
  const [, payload] = bridge.calls.find(([command]) => command === 'package.detail')
  assert.deepEqual(payload, { release_id: releaseId, manifest_hash: 'a'.repeat(64), offset: 0, limit: 20 })
  runtime.dispose()
})

test('dispose releases progress polling and ignores late completion', async () => {
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.perform(state.items[0], 'install')
  await tick()
  runtime.dispose()
  assert.equal(await installing, false)
  bridge.watchers[0].update({ task_id: 'action-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(state.notice, '')
  assert.equal(bridge.watchers[0].disposed, true)
  assert.equal(bridge.watchers[0].cancelled, false)
})

test('malformed list state cannot become an actionable row', async () => {
  const { state, runtime } = fixture({ 'mod.list': () => page([row('pending-runtime-review')]) })
  assert.equal(await runtime.setInstallation('installation'), false)
  assert.equal(state.items.length, 0)
  assert.equal(state.stale, true)
  assert.match(state.error, /不完整/)
  runtime.dispose()
})

test('suggestion consumer can supply a stable idempotency key to the ordinary action', async () => {
  const stableKey = '22222222-2222-4222-8222-222222222222'
  const { bridge, state, runtime } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.perform(state.items[0], 'install', stableKey)
  await tick()
  assert.equal(bridge.calls.find(([command]) => command === 'mod.action')[1].idempotency_key, stableKey)
  bridge.setRows([row('enabled')])
  bridge.watchers[0].update({ task_id: 'action-1', state: 'succeeded', data: { completed: true, state: 'enabled' } })
  assert.equal(await installing, true)
  runtime.dispose()
})
