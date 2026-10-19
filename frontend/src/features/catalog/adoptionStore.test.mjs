import test from 'node:test'
import assert from 'node:assert/strict'
import { createAdoptionStore } from './adoptionStore.js'

const entry = (path, lifecycle = 'immutable') => ({ relative_path: path, lifecycle, sha256: 'b'.repeat(64), size: 9,
  owners: { matching: ['owner-a', 'owner-b'], conflicting: ['owner-c'] } })
const preview = { snapshot_id: 'snapshot', manifest_hash: 'a'.repeat(64), coverage: 'complete', total: 21,
  files: [entry('Mods/中文 %.dll'), entry('Mods/settings.cfg', 'shared_config')], next_offset: 20 }
// Match DesktopApi._package_adopt's bounded terminal payload, not the richer
// PackageService.adopt return value used internally by that API.
const committed = { release: { release_id: 'release', manifest_hash: 'c'.repeat(64) }, snapshot_id: 'snapshot', deployment_generation: null }
const tick = () => new Promise(resolve => setImmediate(resolve))
async function fixture() {
  const state = {}, calls = []
  let update, connectionError, cancellations = 0, disposals = 0, mode = 'success'
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload })
      if (mode === 'request-error') throw new Error('untrusted backend path must not display')
      if (command === 'package.adopt_files') return { ...preview, files: [entry('Mods/last.png')], next_offset: null }
      if (mode === 'running') return { task_id: 'task', state: 'running' }
      return { task_id: 'task', state: 'succeeded', data: command === 'package.adopt' ? committed : structuredClone(preview) }
    },
    watchTask(id, callback, error) {
      update = callback; connectionError = error
      return { async cancel() { cancellations++ }, async dispose() { disposals++ } }
    },
  }
  const store = createAdoptionStore(bridge, state)
  await store.setInstallation('installation')
  return { state, calls, bridge, store, set mode(value) { mode = value },
    finish(status, data) { update({ task_id: 'task', state: status, data }) }, fail() { connectionError(new Error('connection')) },
    get cancellations() { return cancellations }, get disposals() { return disposals } }
}

test('case027 adoption requires explicit immutable choices and exact snapshot hash', async () => {
  const f = await fixture()
  await f.store.preview()
  assert.deepEqual(f.calls[0], { command: 'package.adopt_preview', payload: { installation_id: 'installation' } })
  assert.deepEqual(f.state.selectedPaths, [])
  f.store.toggle('Mods/settings.cfg', true); f.store.toggle('Mods/not-scanned.dll', true)
  assert.deepEqual(f.state.selectedPaths, [])
  f.state.displayName = '本地包'; f.state.version = '0.46D'
  await f.store.commit(); assert.equal(f.calls.length, 1)
  f.store.toggle('Mods/中文 %.dll', true)
  await f.store.commit()
  assert.deepEqual(f.calls[1], { command: 'package.adopt', payload: { snapshot_id: 'snapshot', manifest_hash: preview.manifest_hash,
    paths: ['Mods/中文 %.dll'], display_name: '本地包', version_raw: '0.46D' } })
  assert.equal(f.state.result.deployment_generation, null)
  assert.equal(f.state.confirmedFileCount, 1)
  assert.equal(f.state.preview, null)
  assert.match(f.state.notice, /原文件保持原样/)
  assert.equal(f.calls.some(call => call.command.startsWith('profile.') || call.command.includes('deploy')), false)
})

test('case027 pagination retains explicit selections and every shared owner', async () => {
  const f = await fixture(); await f.store.preview()
  assert.deepEqual(f.state.preview.files[0].owners, { matching: ['owner-a','owner-b'], conflicting:['owner-c'] })
  f.store.toggle('Mods/中文 %.dll', true)
  await f.store.page(20)
  assert.deepEqual(f.calls.at(-1), { command: 'package.adopt_files', payload: { snapshot_id: 'snapshot', offset: 20, limit: 20 } })
  f.store.toggle('Mods/last.png', true)
  assert.deepEqual(f.state.selectedPaths, ['Mods/last.png', 'Mods/中文 %.dll'])
  await f.store.page(21); assert.equal(f.calls.length, 2)
})

test('case027 a mismatched or expired page invalidates confirmation', async () => {
  const f = await fixture(); await f.store.preview()
  f.store.toggle('Mods/中文 %.dll', true); f.state.displayName = 'Name'
  f.bridge.request = async () => ({ ...preview, snapshot_id: 'different', next_offset: null })
  await f.store.page(20)
  assert.equal(f.state.preview, null); assert.deepEqual(f.state.selectedPaths, [])
  assert.match(f.state.error, /重新读取预览/)
  await f.store.commit(); assert.equal(f.calls.length, 1)
})

test('case027 failed request does not misreport a missing scan or expose backend exception', async () => {
  const f = await fixture(); f.mode = 'request-error'
  await f.store.preview()
  assert.match(f.state.error, /错误原因/)
  assert.equal(f.state.error.includes('尚未扫描'), false)
  assert.equal(f.state.error.includes('untrusted'), false)
})

test('case027 incomplete scan displays its real file and known failure reason', async () => {
  const f = await fixture()
  f.bridge.request = async () => ({ task_id: 'task', state: 'succeeded', data: {
    error_code: 'ADOPTION_SCAN_INCOMPLETE', issues: [{ path: 'Mods/中文.dll', code: 'WORKER_INITIALIZATION_FAILED' }],
  } })
  await f.store.preview()
  assert.match(f.state.error, /文件扫描未完成/)
  assert.deepEqual(f.state.issues, [{ path: 'Mods/中文.dll', reason: '程序集读取程序未能启动', code: 'WORKER_INITIALIZATION_FAILED' }])
  assert.equal(f.state.preview, null)
})

test('adoption reads files directly without waiting for metadata inventory', async () => {
  const f = await fixture()
  const request = f.bridge.request
  f.bridge.request = async (command, payload) => {
    if (command === 'inventory.scan') {
      f.calls.push({ command, payload })
      return { task_id: 'scan', state: 'succeeded', data: { status: 'complete' } }
    }
    return request(command, payload)
  }
  await f.store.preview(true)
  assert.deepEqual(f.calls.map(call => call.command), ['package.adopt_preview'])
  assert.equal(f.state.preview.snapshot_id, 'snapshot')
  assert.deepEqual(f.state.selectedPaths, [])
  assert.equal(f.state.operation, '')
})

test('adoption read failure displays the affected file and readable reason', async () => {
  const f = await fixture()
  f.bridge.request = async (command, payload) => {
    f.calls.push({ command, payload })
    return { task_id: 'scan', state: 'succeeded', data: { error_code: 'ADOPTION_READ_FAILED', issues: [{ path: 'Mods/unknown.dll', code: 'READ_TIME_LIMIT' }] } }
  }
  await f.store.preview(true)
  assert.equal(f.calls.length, 1)
  assert.equal(f.state.preview, null)
  assert.match(f.state.error, /无法完整读取/)
  assert.equal(f.state.issues[0].path, 'Mods/unknown.dll')
  assert.equal(f.state.issues[0].reason, '读取超时，请重试或检查文件占用')
})

test('case027 source drift or rejected commit needs fresh preview and another explicit confirmation', async () => {
  const f = await fixture(); await f.store.preview()
  f.state.displayName = 'Name'; f.store.toggle('Mods/中文 %.dll', true)
  f.mode = 'running'; const pending = f.store.commit(); await tick()
  f.finish('failed', { error_code: 'JOB_FAILED' }); await pending
  assert.equal(f.state.preview, null); assert.match(f.state.error, /重新扫描/)
  await f.store.commit(); assert.equal(f.calls.length, 2)
  f.mode = 'success'; await f.store.preview()
  assert.equal(f.calls.length, 3); assert.deepEqual(f.state.selectedPaths, [])
  await f.store.commit(); assert.equal(f.calls.length, 3)
})

test('case027 cancellation waits for terminal status and forbids automatic retry', async () => {
  const f = await fixture(); await f.store.preview()
  f.state.displayName = 'Name'; f.store.toggle('Mods/中文 %.dll', true)
  f.mode = 'running'; const pending = f.store.commit(); await tick()
  await f.store.cancel(); assert.equal(f.cancellations, 1); assert.equal(f.state.busy, true)
  f.finish('cancelled', {}); await pending
  assert.equal(f.state.preview, null); assert.equal(f.state.error, ''); assert.match(f.state.notice, /已取消/)
  await f.store.commit(); assert.equal(f.calls.length, 2)
})

test('case027 connection loss invalidates the old commit preview', async () => {
  const f = await fixture(); await f.store.preview()
  f.state.displayName = 'Name'; f.store.toggle('Mods/中文 %.dll', true)
  f.mode = 'running'; const pending = f.store.commit(); await tick(); f.fail(); await pending
  assert.equal(f.state.preview, null); assert.equal(f.state.busy, false); assert.match(f.state.error, /核对后再次确认/)
  await f.store.commit(); assert.equal(f.calls.length, 2)
})

test('case027 changing installation cancels prior task and ignores late snapshot', async () => {
  const f = await fixture(); f.mode = 'running'
  const pending = f.store.preview(); await tick()
  await f.store.setInstallation('other'); f.finish('succeeded', preview); await pending
  assert.equal(f.cancellations, 1); assert.equal(f.disposals, 1)
  assert.equal(f.state.installationId, 'other'); assert.equal(f.state.preview, null)
})

test('case027 late task submission after unmount is explicitly cancelled', async () => {
  const f = await fixture(); let reply
  f.bridge.request = async (command, payload) => {
    f.calls.push({ command, payload })
    return command === 'package.adopt_preview' ? new Promise(resolve => { reply = resolve }) : {}
  }
  const pending = f.store.preview(); await tick(); await f.store.dispose()
  reply({ task_id: 'late', state: 'running' }); await pending
  assert.deepEqual(f.calls.at(-1), { command: 'task.cancel', payload: { task_id: 'late' } })
  assert.equal(f.state.preview, null)
})

test('case027 duplicate confirm while busy cannot submit a second write', async () => {
  const f = await fixture(); await f.store.preview()
  f.state.displayName = 'Name'; f.store.toggle('Mods/中文 %.dll', true)
  f.mode = 'running'; const pending = f.store.commit(); await tick(); await f.store.commit()
  assert.equal(f.calls.length, 2)
  f.finish('succeeded', committed); await pending
  assert.equal(f.state.result.release.release_id, 'release')
})
