import test from 'node:test'
import assert from 'node:assert/strict'
import { createThemePreferences } from './themePreferences.js'
import { createBridge } from '../../app/bridge/client.js'

const choices = ['obsidian-cyan', 'graphite-ash', 'win11-mica']
function setup(request) {
  const state = { current: choices[0], loading: false, saving: false, error: '' }
  const applied = []
  const preferences = createThemePreferences({ request, choices, state, apply: id => { applied.push(id); return id } })
  return { state, applied, preferences }
}

test('loads native persisted theme without browser storage', async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('opaque origin') } })
  try {
    const { state, preferences } = setup(async command => {
      assert.equal(command, 'app.theme.get'); return { theme_id: 'win11-mica' }
    })
    assert.equal(await preferences.load(), true)
    assert.equal(state.current, 'win11-mica')
  } finally { delete globalThis.localStorage }
})

test('selection only changes after persistent save is acknowledged', async () => {
  let complete
  const { state, applied, preferences } = setup((command, payload) => {
    assert.equal(command, 'app.theme.set'); assert.deepEqual(payload, { theme_id: 'graphite-ash' })
    return new Promise(resolve => { complete = resolve })
  })
  const saved = preferences.select('graphite-ash')
  assert.equal(state.saving, true); assert.equal(state.current, choices[0]); assert.deepEqual(applied, [])
  complete({ theme_id: 'graphite-ash' })
  assert.equal(await saved, true); assert.equal(state.current, 'graphite-ash'); assert.equal(state.saving, false)
})

test('write failure is visible, preserves old theme and permits retry', async () => {
  let attempts = 0
  const { state, preferences } = setup(async () => { if (++attempts === 1) throw new Error('disk'); return { theme_id: 'win11-mica' } })
  assert.equal(await preferences.select('win11-mica'), false)
  assert.equal(state.current, choices[0]); assert.match(state.error, /未能保存/)
  assert.equal(await preferences.select('win11-mica'), true)
  assert.equal(state.current, 'win11-mica'); assert.equal(state.error, '')
})

test('late startup read cannot overwrite a newly saved selection', async () => {
  let finishRead
  const { state, preferences } = setup(async (command, payload) => command === 'app.theme.get'
    ? new Promise(resolve => { finishRead = resolve }) : payload)
  const loaded = preferences.load()
  await preferences.select('win11-mica')
  finishRead({ theme_id: choices[0] }); await loaded
  assert.equal(state.current, 'win11-mica')
})

test('unknown values and concurrent selections are not submitted', async () => {
  let finish, calls = 0
  const { preferences } = setup(() => { calls += 1; return new Promise(resolve => { finish = resolve }) })
  assert.equal(await preferences.select('external'), false)
  const first = preferences.select('win11-mica')
  assert.equal(await preferences.select('graphite-ash'), false); assert.equal(calls, 1)
  finish({ theme_id: 'win11-mica' }); await first
})

test('invalid read or save response does not claim a persisted change', async () => {
  const { state, preferences } = setup(async () => ({ theme_id: 'external' }))
  assert.equal(await preferences.load(), false); assert.match(state.error, /未能读取/)
  assert.equal(await preferences.select('win11-mica'), false); assert.match(state.error, /未能保存/)
  assert.equal(state.current, choices[0])
})

test('production bridge permits only closed theme commands and fields', async () => {
  const calls = []
  const bridge = createBridge({ transport: async request => {
    calls.push(request); return { request_id: request.request_id, ok: true, data: { theme_id: 'win11-mica' } }
  } })
  await bridge.request('app.theme.get')
  await bridge.request('app.theme.set', { theme_id: 'win11-mica' })
  await assert.rejects(bridge.request('app.theme.set', { theme_id: 'unknown' }))
  await assert.rejects(bridge.request('app.theme.get', { path: 'outside' }))
  assert.equal(calls.length, 2)
})
