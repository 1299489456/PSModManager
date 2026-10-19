import test from 'node:test'
import assert from 'node:assert/strict'
import { createConfigEditorRuntime } from './configEditorRuntime.js'

const firstRevision = 'a'.repeat(64), secondRevision = 'b'.repeat(64)
function document(overrides = {}) {
  return {
    adapter_id: 'pswallpaper', display_name: 'PSWallpaper', relative_path: 'UserData/PSWallpaper.cfg',
    revision: firstRevision, editable: true,
    fields: [
      { key: 'WallpaperFit', label: '壁纸显示方式', kind: 'choice', options: ['cover', 'fit', 'stretch'], value: 'cover' },
      { key: 'WallpaperBrightness', label: '壁纸亮度', kind: 'integer', min: 0, max: 100, value: 100 },
      { key: 'LogToFile', label: '写入日志文件', kind: 'boolean', value: false },
    ], ...overrides,
  }
}
function saved(changes = {}, overrides = {}) {
  const result = document({ revision: secondRevision, saved: true, changed: true, backup_created: true, ...overrides })
  result.fields = result.fields.map(field => Object.hasOwn(changes, field.key) ? { ...field, value: changes[field.key] } : field)
  return result
}
function setup(request = async () => document()) {
  const calls = [], state = {}
  const runtime = createConfigEditorRuntime({ request(command, payload) { calls.push([command, payload]); return request(command, payload) } }, state)
  return { state, runtime, calls }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
function error(code, message) { return Object.assign(new Error(message), { code }) }

test('an installation is required before reading or saving configuration', async () => {
  const { state, runtime, calls } = setup()
  assert.equal(await runtime.open(), false)
  assert.equal(await runtime.save(), false)
  assert.equal(state.open, false)
  assert.deepEqual(calls, [])
})

test('opens the selected adapter and starts with no changes', async () => {
  const { state, runtime, calls } = setup()
  runtime.setInstallation('installation-one')
  assert.equal(await runtime.open(), true)
  assert.deepEqual(calls, [['config.read', { installation_id: 'installation-one', adapter_id: 'pswallpaper' }]])
  assert.equal(state.document.relative_path, 'UserData/PSWallpaper.cfg')
  assert.deepEqual(state.values, { WallpaperFit: 'cover', WallpaperBrightness: 100, LogToFile: false })
  assert.equal(runtime.canSave(), false)
  assert.equal(state.backup, true)
})

test('sends only changed fields with the read revision and ordinary save command', async () => {
  const { state, runtime, calls } = setup(async (command, payload) => command === 'config.read' ? document() : saved(payload.changes))
  runtime.setInstallation('installation-one')
  await runtime.open()
  state.values.WallpaperBrightness = 45
  state.values.UnsupportedSetting = 'not submitted'
  assert.equal(await runtime.save(), true)
  assert.deepEqual(calls[1], ['config.save', {
    installation_id: 'installation-one', adapter_id: 'pswallpaper', expected_revision: firstRevision,
    changes: { WallpaperBrightness: 45 }, backup: true,
  }])
  assert.equal(state.document.revision, secondRevision)
  assert.equal(runtime.canSave(), false)
  assert.match(state.notice, /已保存.*备份/)
  assert.equal(calls.some(([command]) => command.startsWith('plan.')), false)
})

test('optional backup can be disabled without a separate approval action', async () => {
  const { state, runtime, calls } = setup(async (command, payload) => command === 'config.read' ? document() : saved(payload.changes, { backup_created: false }))
  runtime.setInstallation('one')
  await runtime.open()
  state.values.LogToFile = true
  state.backup = false
  assert.equal(await runtime.save(), true)
  assert.equal(calls[1][1].backup, false)
  assert.equal(state.notice, '已保存。')
})

test('a file containing only one supported field saves only that existing field', async () => {
  const oneField = { key: 'WallpaperFit', label: '壁纸显示方式', kind: 'choice', value: 'cover' }
  const { state, runtime, calls } = setup(async (command, payload) => command === 'config.read'
    ? document({ fields: [oneField] })
    : saved({}, { fields: [{ ...oneField, value: payload.changes.WallpaperFit }] }))
  runtime.setInstallation('one')
  assert.equal(await runtime.open(), true)
  assert.deepEqual(state.values, { WallpaperFit: 'cover' })
  state.values.WallpaperBrightness = 20
  state.values.LogToFile = true
  assert.equal(runtime.canSave(), false)
  assert.equal(await runtime.save(), false)
  state.values.WallpaperFit = 'fit'
  assert.equal(runtime.canSave(), true)
  assert.equal(await runtime.save(), true)
  assert.deepEqual(calls[1][1].changes, { WallpaperFit: 'fit' })
  assert.deepEqual(state.values, { WallpaperFit: 'fit' })
})

test('unchanged and invalid values cannot reach the save request', async () => {
  const { state, runtime, calls } = setup()
  runtime.setInstallation('one')
  await runtime.open()
  assert.equal(await runtime.save(), false)
  for (const value of [-1, 101, 2.5, '', '50']) {
    state.values.WallpaperBrightness = value
    assert.equal(runtime.canSave(), false)
    assert.equal(await runtime.save(), false)
  }
  state.values.WallpaperBrightness = 50
  state.values.WallpaperFit = 'unsupported'
  assert.equal(await runtime.save(), false)
  state.values.WallpaperFit = 'fit'
  state.values.LogToFile = 'true'
  assert.equal(await runtime.save(), false)
  assert.equal(calls.length, 1)
})

test('an unsupported file is readable as a message without enabling save', async () => {
  const { state, runtime, calls } = setup(async () => document({ editable: false, fields: [], message: '无法解析此格式。' }))
  runtime.setInstallation('one')
  assert.equal(await runtime.open(), true)
  assert.equal(state.document.message, '无法解析此格式。')
  assert.equal(runtime.canSave(), false)
  assert.equal(await runtime.save(), false)
  assert.equal(calls.length, 1)
})

test('failed save keeps edits and forwards the backend message for retry', async () => {
  let attempts = 0
  const { state, runtime } = setup(async (command, payload) => {
    if (command === 'config.read') return document()
    if (++attempts === 1) throw error('CONFIG_IO', '配置文件正在被使用。')
    return saved(payload.changes)
  })
  runtime.setInstallation('one')
  await runtime.open()
  state.values.WallpaperFit = 'fit'
  assert.equal(await runtime.save(), false)
  assert.equal(state.values.WallpaperFit, 'fit')
  assert.equal(state.original.WallpaperFit, 'cover')
  assert.equal(state.error, '配置文件正在被使用。')
  assert.equal(runtime.canSave(), true)
  assert.equal(await runtime.save(), true)
  assert.equal(state.error, '')
})

test('source change requires a fresh read and preserves the unsuccessful edit', async () => {
  const { state, runtime, calls } = setup(async command => {
    if (command === 'config.read') return document()
    throw error('CONFIG_CHANGED', '配置已变化。')
  })
  runtime.setInstallation('one')
  await runtime.open()
  state.values.WallpaperFit = 'stretch'
  assert.equal(await runtime.save(), false)
  assert.match(state.error, /重新读取/)
  assert.equal(state.values.WallpaperFit, 'stretch')
  assert.equal(runtime.canSave(), false)
  assert.equal(await runtime.save(), false)
  assert.equal(calls.length, 2)
  assert.equal(await runtime.read(), true)
  assert.equal(state.needsRefresh, false)
  assert.equal(state.values.WallpaperFit, 'cover')
})

test('late read after close cannot reopen or overwrite a new session', async () => {
  const pending = deferred()
  let reads = 0
  const { state, runtime } = setup(async () => ++reads === 1 ? pending.promise : document({ revision: secondRevision }))
  runtime.setInstallation('one')
  const first = runtime.open()
  runtime.close()
  assert.equal(await runtime.open(), true)
  pending.resolve(document())
  assert.equal(await first, false)
  assert.equal(state.open, true)
  assert.equal(state.document.revision, secondRevision)
  assert.equal(state.loading, false)
})

test('switching installations closes and clears a pending read', async () => {
  const pending = deferred()
  const { state, runtime } = setup(async () => pending.promise)
  runtime.setInstallation('one')
  const first = runtime.open()
  runtime.setInstallation('two')
  pending.resolve(document())
  assert.equal(await first, false)
  assert.equal(state.open, false)
  assert.equal(state.document, null)
  assert.deepEqual(state.values, {})
})

test('a save completion for the old installation cannot populate the new installation', async () => {
  const pending = deferred()
  const { state, runtime } = setup(async command => command === 'config.read' ? document() : pending.promise)
  runtime.setInstallation('one')
  await runtime.open()
  state.values.LogToFile = true
  const saving = runtime.save()
  runtime.setInstallation('two')
  await runtime.open()
  pending.resolve(saved({ LogToFile: true }))
  assert.equal(await saving, false)
  assert.equal(state.document.revision, firstRevision)
  assert.equal(state.values.LogToFile, false)
  assert.equal(state.notice, '')
})

test('concurrent save and refresh do not submit overlapping work', async () => {
  const pending = deferred()
  const { state, runtime, calls } = setup(async command => command === 'config.read' ? document() : pending.promise)
  runtime.setInstallation('one')
  await runtime.open()
  state.values.LogToFile = true
  const saving = runtime.save()
  assert.equal(await runtime.save(), false)
  assert.equal(await runtime.read(), false)
  assert.equal(calls.length, 2)
  pending.resolve(saved({ LogToFile: true }))
  assert.equal(await saving, true)
})

test('a failed refresh clears the stale document and prevents saving it', async () => {
  let reads = 0
  const { state, runtime } = setup(async () => {
    if (++reads === 1) return document()
    throw error('CONFIG_MISSING', '配置文件不存在。')
  })
  runtime.setInstallation('one')
  await runtime.open()
  state.values.LogToFile = true
  assert.equal(await runtime.read(), false)
  assert.equal(state.document, null)
  assert.equal(runtime.canSave(), false)
  assert.equal(state.error, '配置文件不存在。')
})

test('malformed adapter responses cannot enable an editable form', async () => {
  const { state, runtime } = setup(async () => document({ fields: [{ key: 'WallpaperFit', kind: 'integer', value: 'cover' }] }))
  runtime.setInstallation('one')
  assert.equal(await runtime.open(), false)
  assert.equal(state.document, null)
  assert.equal(runtime.canSave(), false)
  assert.match(state.error, /未能读取/)
})

test('an unconfirmed save response does not discard the edited values', async () => {
  const { state, runtime } = setup(async command => command === 'config.read' ? document() : saved({}, { saved: false }))
  runtime.setInstallation('one')
  await runtime.open()
  state.values.WallpaperBrightness = 20
  assert.equal(await runtime.save(), false)
  assert.equal(state.values.WallpaperBrightness, 20)
  assert.equal(state.original.WallpaperBrightness, 100)
  assert.equal(state.notice, '')
})

test('disposing during a request leaves no session or late error behind', async () => {
  const pending = deferred()
  const { state, runtime } = setup(async () => pending.promise)
  runtime.setInstallation('one')
  const opening = runtime.open()
  runtime.dispose()
  pending.reject(error('CONFIG_IO', '磁盘错误。'))
  assert.equal(await opening, false)
  assert.equal(state.open, false)
  assert.equal(state.error, '')
  assert.equal(await runtime.open(), false)
})
