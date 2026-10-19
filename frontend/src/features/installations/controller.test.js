import test from 'node:test'
import assert from 'node:assert/strict'
import { canSelect, createInstallationController, issueText } from './controller.js'

test('candidate issues show actionable Chinese without exposing raw exception paths', () => {
  for (const code of ['Probably Stolen.exe', 'Probably Stolen_Data', 'GameAssembly', 'global-metadata', 'loader_missing', 'data_root_conflict', 'invalid_pe_header', 'invalid_metadata_header', 'reparse_point', 'read_size_limit', 'read_time_limit', 'file_drift', 'unknown_exception']) {
    const label = issueText(`${code}: FileNotFoundError: E:/private/path`)
    assert.match(label, /[\u4e00-\u9fff]/)
    assert.doesNotMatch(label, /FileNotFoundError|private|E:\//)
  }
  assert.match(issueText({ code: 'loader_missing' }), /MelonLoader/)
})

function setup(responses = {}) {
  const calls = [], state = {}, selected = []
  let update, onError, disposed = 0, cancelled = 0
  const bridge = {
    async request(command, payload) { calls.push([command, payload]); const response = responses[command]; if (response instanceof Error) throw response; return typeof response === 'function' ? response(payload) : response },
    watchTask(id, callback, error) { update = callback; onError = error; return { async dispose() { disposed++ }, async cancel() { cancelled++ } } },
  }
  const controller = createInstallationController(bridge, state, value => selected.push(value))
  return { controller, calls, state, selected, update: value => update(value), error: () => onError(), disposed: () => disposed, cancelled: () => cancelled }
}
const valid = { candidate_id: 'candidate', valid: true, fingerprint: { coverage: 'complete' } }
test('valid directory can be selected without fingerprint completeness', async () => {
  const app = setup()
  assert.equal(canSelect(valid), true)
  assert.equal(canSelect({ ...valid, fingerprint: { coverage: 'partial' } }), true)
  assert.equal(canSelect({ ...valid, fingerprint: undefined }), true)
  for (const candidate of [null, { ...valid, valid: false }]) {
    assert.equal(canSelect(candidate), false); await app.controller.select(candidate)
  }
  assert.equal(app.calls.length, 0)
})
test('native chooser cancellation does not discover', async () => {
  const app = setup({ 'installation.choose': { source_handle: null } })
  await app.controller.choose('manual')
  assert.equal(app.calls.length, 1); assert.equal(app.state.busy, false); assert.match(app.state.notice, /取消/)
})
test('discovery sends only authorized handle and consumes task results', async () => {
  const app = setup({ 'installation.choose': { source_handle: 'handle' }, 'installation.discover': { task_id: 'task', state: 'queued' } })
  await app.controller.choose('steam')
  assert.deepEqual(app.calls[1], ['installation.discover', { source_handles: ['handle'] }])
  assert.equal(app.state.busy, true)
  app.update({ task_id: 'task', state: 'succeeded', data: { candidates: [valid] } })
  assert.deepEqual(app.state.candidates, [valid]); assert.equal(app.state.busy, false)
})
test('selection emits real task DTO and refreshes saved list', async () => {
  const installation = { installation_id: 'saved' }
  const app = setup({ 'installation.select': { task_id: 'task', state: 'queued' }, 'installation.list': { installations: [installation], selected_installation_id: 'saved' } })
  await app.controller.select(valid)
  app.update({ task_id: 'task', state: 'succeeded', data: { installation } })
  await Promise.resolve()
  assert.deepEqual(app.selected, [installation]); assert.equal(app.state.selectedId, 'saved')
  assert.deepEqual(app.state.installations, [installation])
})
test('saved installations revalidate through use, not arbitrary paths', async () => {
  const app = setup({ 'installation.use': { task_id: 'task', state: 'queued' } })
  await app.controller.use('saved')
  assert.deepEqual(app.calls, [['installation.use', { installation_id: 'saved' }]])
  await app.controller.cancel(); assert.equal(app.cancelled(), 1)
  app.update({ state: 'cancelled' }); assert.equal(app.state.busy, false); assert.match(app.state.error, /取消/)
})
test('failed task and broken watcher show errors without selecting', async () => {
  const app = setup({ 'installation.select': { task_id: 'task', state: 'running' } })
  await app.controller.select(valid); app.update({ state: 'failed' })
  assert.equal(app.selected.length, 0); assert.match(app.state.error, /无法读取/)
  await app.controller.select(valid); app.error()
  assert.equal(app.state.busy, false); assert.match(app.state.error, /中断/)
})
test('unmount disposes owned watch and ignores late completion', async () => {
  const app = setup({ 'installation.select': { task_id: 'task', state: 'running' } })
  await app.controller.select(valid); await app.controller.dispose()
  app.update({ state: 'succeeded', data: { installation: { installation_id: 'late' } } })
  assert.equal(app.disposed(), 1); assert.equal(app.selected.length, 0)
})
test('already completed task is consumed without subscribing', async () => {
  const app = setup({ 'installation.discover': { state: 'succeeded', data: { candidates: [] } }, 'installation.choose': { source_handle: 'h' } })
  await app.controller.choose('manual')
  assert.equal(app.state.busy, false); assert.match(app.state.notice, /未发现/)
})
test('chooser errors and list errors remain visible', async () => {
  const app = setup({ 'installation.choose': new Error('denied'), 'installation.list': new Error('unavailable') })
  await app.controller.choose('manual'); assert.equal(app.state.busy, false); assert.match(app.state.error, /选择器/)
  await app.controller.refresh(); assert.match(app.state.error, /读取/)
})
test('revalidation drift preserves the selected installation and requests rediscovery', async () => {
  for (const command of ['installation.select', 'installation.use']) {
    const app = setup({ [command]: { task_id: 'task', state: 'running' } })
    app.state.selectedId = 'previous'
    if (command === 'installation.select') await app.controller.select(valid)
    else await app.controller.use('saved')
    app.update({ state: 'succeeded', data: { installation: null, fingerprint: null, error_code: 'INSTALLATION_CHANGED' } })
    assert.equal(app.state.selectedId, 'previous')
    assert.equal(app.state.busy, false)
    assert.equal(app.selected.length, 0)
    assert.equal(app.state.error, '所选游戏目录已移动或无法读取，请重新选择目录。')
    assert.equal(app.calls.length, 1)
  }
})
test('new process requires explicit use even for a unique saved registration', async () => {
  const installation = { installation_id: 'saved' }
  const app = setup({ 'installation.list': { installations: [installation], selected_installation_id: null } })
  await app.controller.refresh(); await app.controller.refresh()
  assert.deepEqual(app.selected, [])
  assert.equal(app.state.selectedId, null)
  assert.ok(app.calls.every(([command]) => command === 'installation.list'))
})
test('new process never silently chooses among multiple saved installations', async () => {
  const app = setup({ 'installation.list': { installations: [{ installation_id: 'one' }, { installation_id: 'two' }], selected_installation_id: null } })
  await app.controller.refresh()
  assert.equal(app.state.selectedId, null)
  assert.deepEqual(app.selected, [])
})

test('startup automatically discovers Steam and emits a unique installed game', async () => {
  const installation = { installation_id: 'automatic' }
  let listed = false
  const app = setup({
    'installation.list': () => {
      const response = listed ? { installations: [installation], selected_installation_id: 'automatic' } : { installations: [], selected_installation_id: null }
      listed = true
      return response
    },
    'installation.auto_discover': { state: 'succeeded', data: { candidates: [valid], installation } },
  })
  await app.controller.initialize()
  assert.deepEqual(app.calls.slice(0, 2), [['installation.list', undefined], ['installation.auto_discover', {}]])
  assert.deepEqual(app.selected, [installation])
  assert.equal(app.state.selectedId, 'automatic')
})

test('automatic discovery preserves choice when several games are found', async () => {
  const app = setup({
    'installation.list': { installations: [], selected_installation_id: null },
    'installation.auto_discover': { state: 'succeeded', data: { candidates: [valid, { ...valid, candidate_id: 'other' }] } },
  })
  await app.controller.initialize()
  assert.equal(app.state.candidates.length, 2)
  assert.equal(app.selected.length, 0)
  assert.equal(app.state.error, '')
})
