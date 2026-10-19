import test from 'node:test'
import assert from 'node:assert/strict'
import { createImportController } from './controller.js'
import { createBridge } from '../../app/bridge/client.js'

const preview = { preview_id: 'preview', preview_hash: 'a'.repeat(64), display_name: '中文 100% 文件', files: [{ path: '中文 100%.dll', ownership: 'unknown' }], total: 21, next_offset: 20, can_commit: true, warnings: ['未知归属'] }
function fixture(choose = { source_handle: 'source' }) {
  const calls = []; let update, onError, disposals = 0, cancellations = 0
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload })
      if (command === 'package.choose') return choose
      if (command === 'package.import_files') return { ...preview, files: [{ path: 'last.dll' }], next_offset: null }
      return { task_id: 'task', state: 'running' }
    },
    watchTask(id, callback, error) { update = callback; onError = error; return { async dispose() { disposals++ }, async cancel() { cancellations++ } } },
  }
  const state = {}, controller = createImportController(bridge, state)
  return { state, controller, calls, finish(data = preview, status = 'succeeded') { update({ task_id: 'task', state: status, data }) }, fail() { onError() }, get disposals() { return disposals }, get cancellations() { return cancellations } }
}
test('native selection cancellation preserves preview without reporting failure', async () => {
  const f = fixture({ source_handle: null }); f.state.preview = preview
  await f.controller.choose('zip')
  assert.equal(f.state.busy, false); assert.equal(f.state.error, ''); assert.equal(f.state.preview, preview); assert.equal(f.calls.length, 1)
})
test('rejected replacement source clears previous preview and cannot commit old content', async () => {
  const f = fixture({ error_code: 'IMPORT_SOURCE_PROTECTED_LIBRARY', message: '已有包请在包库中选择。' })
  f.state.preview = preview; f.state.displayName = 'old'; f.state.result = { release_id: 'old' }
  await f.controller.choose('zip')
  assert.equal(f.state.preview, null); assert.equal(f.state.result, null)
  assert.equal(f.state.errorCode, 'IMPORT_SOURCE_PROTECTED_LIBRARY')
  assert.equal(f.state.error, '已有包请在包库中选择。')
  await f.controller.commit(); assert.equal(f.calls.length, 1)
})
test('preview uses native handle and explicit commit preserves preview hash without activation', async () => {
  const f = fixture(); await f.controller.choose('dll'); await f.controller.choose('zip')
  assert.equal(f.calls.length, 2); assert.deepEqual(f.calls[1].payload, { source_handle: 'source' })
  f.finish(); assert.equal(f.state.displayName, preview.display_name)
  await f.controller.commit(); await f.controller.commit()
  assert.equal(f.calls.length, 3); assert.deepEqual(f.calls[2].payload, { preview_id: 'preview', preview_hash: 'a'.repeat(64), display_name: preview.display_name })
  f.finish({ release_id: 'release', file_count: 21, enabled: false, deployed: false })
  assert.equal(f.state.result.enabled, false); assert.equal(f.state.preview, null)
})
test('pagination bounded to 20 and unknown metadata retained', async () => {
  const f = fixture(); await f.controller.choose('directory'); f.finish()
  assert.equal(f.state.preview.files[0].ownership, 'unknown')
  await f.controller.page(20)
  assert.deepEqual(f.calls.at(-1).payload, { preview_id: 'preview', offset: 20, limit: 20 }); assert.equal(f.state.offset, 20)
  await f.controller.page(21); assert.equal(f.calls.length, 3)
})
test('cannot commit rejected preview or empty name', async () => {
  const f = fixture(); await f.controller.choose('dll'); f.finish({ ...preview, can_commit: false }); await f.controller.commit()
  f.state.preview = preview; f.state.displayName = ' '; await f.controller.commit(); assert.equal(f.calls.length, 2)
})
test('task errors retain backend message and cancellation is not failure', async () => {
  const f = fixture(); await f.controller.choose('zip'); f.finish({ error_code: 'BAD', message: '文件格式不支持。' }, 'failed')
  assert.equal(f.state.error, '文件格式不支持。')
  assert.equal(f.state.errorCode, 'BAD')
  await f.controller.choose('zip'); await f.controller.cancel(); assert.equal(f.cancellations, 1)
  assert.equal(f.state.errorCode, '')
  f.finish({}, 'cancelled'); assert.equal(f.state.error, ''); assert.equal(f.state.notice, '已取消导入。')
})
test('unmount disposes watcher and ignores late result', async () => {
  const f = fixture(); await f.controller.choose('dll'); await f.controller.dispose(); f.finish()
  assert.equal(f.disposals, 1); assert.equal(f.state.preview, null)
})
test('progress connection failure invalidates commit preview', async () => {
  const f = fixture(); await f.controller.choose('dll'); f.finish(); await f.controller.commit(); f.fail()
  assert.equal(f.state.busy, false); assert.equal(f.state.preview, null); assert.ok(f.state.error)
})
test('real bridge accepts import commands and blocks paths, invalid kinds and pagination', async () => {
  const calls = []; const bridge = createBridge({ transport: async request => { calls.push(request); return { request_id: request.request_id, ok: true, data: {} } } })
  await bridge.request('package.choose', { kind: 'zip' })
  await bridge.request('package.import_preview', { source_handle: 'handle' })
  await bridge.request('package.import_files', { preview_id: 'preview', offset: 20, limit: 20 })
  await bridge.request('package.import_commit', { preview_id: 'preview', preview_hash: 'a'.repeat(64), display_name: '中文 100%', version_raw: null })
  await assert.rejects(bridge.request('package.choose', { kind: 'exe' }))
  await assert.rejects(bridge.request('package.import_preview', { source_handle: 'handle', path: 'C:/game' }))
  await assert.rejects(bridge.request('package.import_files', { preview_id: 'preview', limit: 21 }))
  await assert.rejects(bridge.request('package.import_commit', { preview_id: 'preview', preview_hash: 'bad', display_name: 'name' }))
  assert.equal(calls.length, 4); await bridge.dispose()
})

test('case027 late import task after unmount is cancelled without subscribing', async () => {
  let replyTask
  const calls = [], state = {}
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload })
      if (command === 'package.choose') return { source_handle: 'source' }
      if (command === 'package.import_preview') return new Promise(resolve => { replyTask = resolve })
      return {}
    },
    watchTask() { assert.fail('late task must not subscribe') },
  }
  const controller = createImportController(bridge, state)
  const pending = controller.choose('zip')
  while (!replyTask) await new Promise(resolve => setImmediate(resolve))
  await controller.dispose()
  replyTask({ task_id: 'late', state: 'running' })
  await pending
  assert.deepEqual(calls.at(-1), { command: 'task.cancel', payload: { task_id: 'late' } })
  assert.equal(state.preview, null)
})

test('case027 cancelled or rejected commit requires fresh preview before retry', async () => {
  for (const terminal of ['cancelled', 'failed']) {
    const f = fixture(); await f.controller.choose('zip'); f.finish()
    await f.controller.commit(); f.finish({}, terminal)
    const previous = f.calls.length
    assert.equal(f.state.preview, null)
    await f.controller.commit()
    assert.equal(f.calls.length, previous)
  }
})

test('native and Nexus handles use version preview and explicit package association without accepting paths', async () => {
  const f = fixture()
  await f.controller.openSource({ source_handle: 'trusted', kind: 'zip', display_name: 'Nexus 厨房 Mod.zip' })
  assert.deepEqual(f.calls[0], { command: 'package.import_preview', payload: { source_handle: 'trusted' } })
  f.finish({ ...preview, version: { identity: 'melon:example', version_raw: '2.0' },
    version_candidates: [{ package_id: 'same-mod', match: 'embedded_metadata', comparison: 'upgrade' }] })
  assert.equal(f.state.versionRaw, '2.0'); assert.equal(f.state.packageId, 'same-mod')
  assert.equal(f.state.displayName, 'Nexus 厨房 Mod')
  await f.controller.commit()
  assert.equal(f.calls.at(-1).command, 'package.import_version')
  assert.equal(f.calls.at(-1).payload.package_id, 'same-mod')
  assert.equal(f.calls.at(-1).payload.version_raw, '2.0')
})

test('install preview and replacement require a second explicit confirm with fixed snapshot and old IDs', async () => {
  const f = fixture(); f.state.result = { release_id: 'new', manifest_hash: 'lock' }
  await f.controller.previewUpdate('game')
  f.finish({ release_id: 'new', manifest_hash: 'lock', snapshot_id: 'fixed', replacements: [{ id: 'old' }] })
  assert.equal(f.calls.length, 1); assert.equal(f.state.applied, null)
  await f.controller.applyUpdate('game')
  assert.equal(f.calls.at(-1).command, 'package.update_apply')
  assert.deepEqual(f.calls.at(-1).payload.replaced_ids, ['old'])
  assert.equal(f.calls.at(-1).payload.snapshot_id, 'fixed')
  f.finish({ completed: true })
  assert.equal(f.state.applied.completed, true); assert.equal(f.state.updatePreview, null)
})
