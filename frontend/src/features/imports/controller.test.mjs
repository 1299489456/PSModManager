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

const safePreview = { ...preview, warnings: [], version: { identity: 'melon:demo', identity_source: 'embedded_metadata',
  version_raw: '2.0', version_source: 'embedded_metadata' }, version_candidates: [] }
function installFixture({ incoming = safePreview, replacements = [], onRequest = () => {}, pendingCommand = null, failure = null } = {}) {
  const calls = [], state = {}, context = { installationId: 'game', workspaceDirty: false }
  let complete, fail, cancels = 0
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload }); await onRequest(command, context)
      if (command === pendingCommand) return { task_id: command, state: 'running' }
      if (command === failure) return { task_id: command, state: 'failed', data: { error_code: 'TEST_BLOCKED', message: '文件被占用。' } }
      const data = command === 'package.import_preview' ? incoming
        : command === 'package.import_version' ? { release_id: 'new', manifest_hash: 'lock', file_count: 1 }
          : command === 'package.update_preview' ? { installation_id: payload.installation_id, release_id: 'new', manifest_hash: 'lock',
            snapshot_id: 'fixed', incoming: { display_name: 'Demo', version_raw: '2.0' }, replacements }
            : { completed: true }
      return { task_id: command, state: 'succeeded', data }
    },
    watchTask(id, callback, error) { complete = data => callback({ task_id: id, state: 'succeeded', data }); fail = error; return { async dispose() {}, async cancel() { cancels++ } } },
  }
  const controller = createImportController(bridge, state, { getInstallContext: () => context })
  return { controller, calls, state, context, finish: data => complete(data), fail: () => fail(), get cancels() { return cancels } }
}
const source = { source_handle: 'nexus-source', kind: 'zip', display_name: 'Demo.zip' }
const flush = () => new Promise(resolve => setImmediate(resolve))

test('Nexus install completes safe import, version preview and apply without extra confirmation', async () => {
  const f = installFixture()
  assert.equal(await f.controller.installSource(source), true)
  assert.deepEqual(f.calls.map(row => row.command), ['package.import_preview', 'package.import_version', 'package.update_preview', 'package.update_apply'])
  assert.equal(f.calls[1].payload.display_name, 'Demo')
  assert.equal(f.calls.at(-1).payload.installation_id, 'game')
  assert.equal(f.calls.at(-1).payload.snapshot_id, 'fixed')
  assert.deepEqual(f.calls.at(-1).payload.replaced_ids, [])
  assert.equal(f.state.applied.completed, true); assert.equal(f.state.busy, false); assert.equal(f.state.installStage, '')
})

test('automatic install waits for task completion and blocks competing import controls', async () => {
  const f = installFixture({ pendingCommand: 'package.import_preview' })
  const installing = f.controller.installSource(source); await flush()
  assert.equal(f.state.busy, true); assert.match(f.state.installStage, /检查 Mod/)
  await f.controller.openSource({ source_handle: 'competing' }); await f.controller.choose('zip'); await f.controller.commit()
  assert.equal(f.calls.length, 1)
  f.finish(safePreview)
  assert.equal(await installing, true)
  assert.equal(f.calls.length, 4)
})

test('replacement and downgrade stop at the existing explicit install confirmation', async () => {
  for (const comparison of ['upgrade', 'downgrade']) {
    const f = installFixture({ replacements: [{ id: 'old', comparison }] })
    assert.equal(await f.controller.installSource(source), false)
    assert.equal(f.calls.at(-1).command, 'package.update_preview')
    assert.ok(f.state.updatePreview); assert.equal(f.state.applied, null)
    await f.controller.applyUpdate('game')
    assert.deepEqual(f.calls.at(-1).payload.replaced_ids, ['old'])
    assert.equal(f.state.applied.completed, true)
  }
})

test('warnings, unclear identity, uncertain version and target overlap stay in import review', async () => {
  const variants = [
    { ...safePreview, warnings: ['需核对文件'] },
    { ...safePreview, version: { ...safePreview.version, identity_source: 'unconfirmed' } },
    { ...safePreview, version: { ...safePreview.version, version_source: 'filename_hint' } },
    { ...safePreview, version: { ...safePreview.version, version_raw: null } },
    { ...safePreview, version_candidates: [{ package_id: 'p', match: 'target_overlap', comparison: 'upgrade' }] },
    { ...safePreview, version_candidates: [{ package_id: 'p', match: 'embedded_metadata', comparison: 'unknown' }] },
    { ...safePreview, can_commit: false },
  ]
  for (const incoming of variants) {
    const f = installFixture({ incoming })
    assert.equal(await f.controller.installSource(source), false)
    assert.equal(f.calls.length, 1); assert.ok(f.state.preview); assert.equal(f.state.busy, false)
  }
})

test('no game or unsaved workspace prevents automatic library commit and install', async () => {
  for (const change of [{ installationId: null }, { workspaceDirty: true }]) {
    const f = installFixture(); Object.assign(f.context, change)
    assert.equal(await f.controller.installSource(source), false)
    assert.equal(f.calls.length, 1); assert.ok(f.state.preview); assert.ok(f.state.notice)
  }
})

test('game or draft changes during asynchronous import stop before deployment', async () => {
  for (const change of [{ installationId: 'other-game' }, { workspaceDirty: true }]) {
    const f = installFixture({ onRequest(command, context) { if (command === 'package.import_version') Object.assign(context, change) } })
    assert.equal(await f.controller.installSource(source), false)
    assert.equal(f.calls.at(-1).command, 'package.import_version'); assert.ok(f.state.result); assert.equal(f.state.applied, null)
  }
})

test('switching game away and back invalidates an in-flight install preview and manual confirmation', async () => {
  const f = installFixture({ pendingCommand: 'package.update_preview' })
  const installing = f.controller.installSource(source); await flush()
  f.context.installationId = 'other'; f.controller.contextChanged()
  f.context.installationId = 'game'; f.controller.contextChanged()
  f.finish({ release_id: 'new', manifest_hash: 'lock', snapshot_id: 'fixed', replacements: [] })
  assert.equal(await installing, false); assert.equal(f.state.updatePreview, null)
  await f.controller.applyUpdate('game')
  assert.equal(f.calls.at(-1).command, 'package.update_preview')
})

test('automatic cancellation, task failure and lost progress stop the chain visibly', async () => {
  const cancelled = installFixture({ pendingCommand: 'package.import_preview' })
  const installing = cancelled.controller.installSource(source); await flush(); await cancelled.controller.cancel()
  cancelled.finish(safePreview)
  assert.equal(await installing, false); assert.equal(cancelled.calls.length, 1); assert.equal(cancelled.cancels, 1)
  const failed = installFixture({ failure: 'package.update_apply' })
  assert.equal(await failed.controller.installSource(source), false)
  assert.equal(failed.state.errorCode, 'TEST_BLOCKED'); assert.equal(failed.state.error, '文件被占用。'); assert.equal(failed.state.busy, false)
  const lost = installFixture({ pendingCommand: 'package.import_preview' })
  const waiting = lost.controller.installSource(source); await flush(); lost.fail()
  assert.equal(await waiting, false); assert.match(lost.state.error, /连接中断/); assert.equal(lost.calls.length, 1)
})

test('normal source opening still stops at preview and never auto installs', async () => {
  const f = installFixture()
  await f.controller.openSource(source)
  assert.equal(f.calls.length, 1); assert.ok(f.state.preview); assert.equal(f.state.result, null)
})

test('explicit full-inventory review reasons distinguish handling guidance from installation blockers', async () => {
  const safe = installFixture({ incoming: { ...safePreview, warnings: ['一般文件处理说明'], install_review_reasons: [] } })
  assert.equal(await safe.controller.installSource(source), true)
  for (const reason of ['UNSUPPORTED_TARGET', 'NON_IMMUTABLE_FILE', 'UNRECOGNIZED_DLL', 'METADATA_DIAGNOSTICS']) {
    const blocked = installFixture({ incoming: { ...safePreview, install_review_reasons: [reason] } })
    assert.equal(await blocked.controller.installSource(source), false)
    assert.equal(blocked.calls.length, 1); assert.ok(blocked.state.preview)
  }
})
