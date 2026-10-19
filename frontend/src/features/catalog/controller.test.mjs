import test from 'node:test'
import assert from 'node:assert/strict'
import { performance } from 'node:perf_hooks'
import { createWorkbenchController, indexEntries, filterEntries, virtualWindow, rowHeight } from './controller.js'

const row = (n, extra = {}) => ({ entry_id: `entry-${n}`, relative_path: `Mods/中文 模组 ${n}%.dll`, assembly_name: `Assembly${n}`, tags: [], presence: 'present', prediction: { status: n % 2 ? 'candidate' : 'excluded' }, ...extra })
function bridgeWith(overrides = {}) {
  const calls = [], watchers = []
  return { calls, watchers,
    async request(command, payload = {}) {
      calls.push([command, structuredClone(payload)])
      if (overrides[command]) return overrides[command](payload)
      if (command === 'inventory.list') return { entries: [row(1), row(2)], generation: 7, next_offset: null, total: 2, status: 'complete' }
      if (command === 'logs.watch') return { task_id: 'logs', state: 'running' }
      if (command === 'diagnostics.explain') return { diagnostics: [], summary: {}, snapshot_id: 'snap', next_offset: null, coverage: 'complete' }
      if (command === 'logs.sources') return { sources: [] }
      if (command === 'session.list') return { entries: [] }
      if (command === 'inventory.detail') return { entry_id: payload.entry_id, analysis: { content_ids: [], content_id_count: 0 } }
      if (command === 'inventory.graph') return { relations: [], next_offset: null }
      if (command === 'inventory.scan') return { task_id: 'scan', state: 'running' }
      throw new Error(command)
    },
    watchTask(task_id, update, error) {
      const handle = { task_id, update, error, disposed: false, cancelled: false, async dispose() { this.disposed = true }, async cancel() { this.cancelled = true; update({ task_id, state: 'cancelled' }) } }
      watchers.push(handle); return handle
    },
  }
}
const pause = () => new Promise(resolve => setTimeout(resolve, 0))

test('5000 exact synthetic entries filter p95 below 200ms; bounded virtual DOM window', () => {
  const entries = indexEntries(Array.from({ length: 5000 }, (_, index) => row(index)))
  const timings = []
  for (let i = 0; i < 120; i++) { const start = performance.now(); const result = filterEntries(entries, `中文 ${i % 10}`); assert.ok(result.length > 0); timings.push(performance.now() - start) }
  timings.sort((a,b) => a-b); const p95 = timings[Math.ceil(timings.length * .95) - 1]
  console.log(JSON.stringify({ benchmark: 'workbench_filter_5000', sample_count: 5000, iterations: 120, p95_ms: p95, node: process.version, platform: process.platform, arch: process.arch }))
  assert.ok(p95 < 200)
  assert.equal(filterEntries(entries, '中文 25%')[0].relative_path, 'Mods/中文 模组 25%.dll')
  assert.equal(filterEntries(entries, '', 'candidate').length, 2500)
  const view = virtualWindow(entries, 32000, 600)
  assert.ok(view.rows.length <= 28); assert.equal(view.totalHeight, 5000 * rowHeight)
})

test('inventory pagination pins generation, reload keeps stored observations', async () => {
  const bridge = bridgeWith({ 'inventory.list': payload => ({ entries: [row(payload.offset)], generation: 7, next_offset: payload.offset === 0 ? 50 : null, total: 2 }) })
  const controller = createWorkbenchController(bridge)
  await controller.choose({ installation_id: 'install' })
  assert.equal(controller.state.entries.length, 2)
  const pages = bridge.calls.filter(([command]) => command === 'inventory.list')
  assert.equal(pages[1][1].generation, 7)
  await controller.choose({ installation_id: 'install' })
  assert.equal(bridge.calls.filter(([command]) => command === 'logs.watch').length, 1)
  await controller.dispose(); assert.ok(bridge.watchers.every(watch => watch.disposed))
  assert.ok(!bridge.watchers.some(watch => watch.cancelled), 'unmount unsubscribes rather than cancels reusable log worker')
})
test('missing historical entries remain in inventory but not direct-load candidates', () => {
  const rows = indexEntries([row(1, { presence: 'missing' }), row(3)])
  assert.equal(filterEntries(rows).length, 2)
  assert.deepEqual(filterEntries(rows, '', 'candidate').map(item => item.entry_id), ['entry-3'])
})

test('scan cancellation keeps prior entries; retry starts real command again', async () => {
  const bridge = bridgeWith(), controller = createWorkbenchController(bridge)
  await controller.choose({ installation_id: 'install' }); await controller.scan()
  assert.equal(controller.state.busy, true)
  await controller.cancel(); await pause()
  assert.equal(controller.state.busy, false); assert.equal(controller.state.task.state, 'cancelled'); assert.equal(controller.state.entries.length, 2)
  await controller.scan(); assert.equal(bridge.calls.filter(([command]) => command === 'inventory.scan').length, 2)
  await controller.dispose()
})

test('failed scan is recoverable and does not turn into success', async () => {
  const bridge = bridgeWith(), controller = createWorkbenchController(bridge)
  await controller.choose({ installation_id: 'install' }); await controller.scan()
  bridge.watchers.find(watch => watch.task_id === 'scan').update({ task_id: 'scan', state: 'failed' })
  await pause(); assert.equal(controller.state.task.state, 'failed'); assert.equal(controller.state.busy, false)
  await controller.scan(); assert.equal(controller.state.busy, true); await controller.dispose()
})

test('multi-selection is frontend-only and all detail reads bind generation', async () => {
  const bridge = bridgeWith(), controller = createWorkbenchController(bridge)
  await controller.choose({ installation_id: 'install' }); await controller.select('entry-1'); await controller.select('entry-2', { ctrlKey: true })
  assert.deepEqual(controller.state.selectedIds, ['entry-1', 'entry-2'])
  assert.equal(controller.state.detail.entry_id, 'entry-2')
  assert.ok(bridge.calls.filter(([command]) => command === 'inventory.detail').every(([,payload]) => payload.generation === 7))
  assert.ok(bridge.calls.every(([command]) => !command.startsWith('deployment.')))
  await controller.dispose()
})

test('session changes invalidate a slow diagnosis before showing the new scope', async () => {
  let resolveOld
  const bridge = bridgeWith({ 'diagnostics.explain': payload => payload.session_id === 'old' ? new Promise(resolve => { resolveOld = resolve }) : { diagnostics: [{ diagnostic_id: payload.session_id || 'all' }], snapshot_id: 'new', next_offset: null, summary: {} } })
  const controller = createWorkbenchController(bridge); await controller.choose({ installation_id: 'install' })
  const pending = controller.setSession('old'); await pause(); await controller.setSession('new')
  resolveOld({ diagnostics: [{ diagnostic_id: 'wrong-old' }], snapshot_id: 'old', next_offset: null, summary: {} }); await pending
  assert.equal(controller.state.sessionId, 'new'); assert.equal(controller.state.diagnostics[0].diagnostic_id, 'new')
  await controller.dispose()
})

test('slow previous detail never overwrites newest selection', async () => {
  let resolveOld
  const bridge = bridgeWith({ 'inventory.detail': payload => payload.entry_id === 'entry-1' ? new Promise(resolve => { resolveOld = resolve }) : { entry_id: payload.entry_id, analysis: {} } })
  const controller = createWorkbenchController(bridge); await controller.choose({ installation_id: 'install' })
  const pending = controller.select('entry-1'); await controller.select('entry-2')
  resolveOld({ entry_id: 'entry-1', analysis: {} }); await pending
  assert.equal(controller.state.detail.entry_id, 'entry-2'); await controller.dispose()
})

test('diagnostic pagination carries immutable snapshot and evidence windows are byte offsets', async () => {
  const bridge = bridgeWith({ 'diagnostics.explain': payload => ({ diagnostics: [{ diagnostic_id: `d${payload.offset}` }], snapshot_id: 'snapshot-hash', next_offset: payload.offset === 0 ? 20 : null, summary: {} }),
    'evidence.read': payload => ({ evidence_id: payload.evidence_id, text: 'text', raw_base64: 'dGV4dA==', next_offset: payload.offset === 0 ? 4 : null, window_start: payload.offset, window_end: payload.offset + 4 }) })
  const controller = createWorkbenchController(bridge); await controller.choose({ installation_id: 'install' }); await controller.diagnostics(true)
  assert.equal(bridge.calls.filter(([command]) => command === 'diagnostics.explain')[1][1].snapshot_id, 'snapshot-hash')
  await controller.readEvidence('log-id'); await controller.readEvidence('log-id', true)
  assert.equal(bridge.calls.filter(([command]) => command === 'evidence.read')[1][1].offset, 4)
  assert.equal(controller.state.evidenceParts.length, 2); await controller.dispose()
})
