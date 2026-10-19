import test from 'node:test'
import assert from 'node:assert/strict'
import { createNexusRuntime, joinPieces } from './nexusRuntime.js'

function bridge(replies, calls = []) {
  return { calls, request: async (command, payload) => { calls.push([command, payload]); const reply = replies[command]; return typeof reply === 'function' ? reply(payload) : reply } }
}
const done = data => ({ task_id: 'owned-job', request_id: 'r', sequence: 2, state: 'succeeded', data: { ok: true, ...data } })
const mods = (from, count) => Array.from({ length: count }, (_, index) => ({ mod_id: from + index, name: `Mod ${from + index}` }))

test('opening Nexus loads only local settings, never browses or contacts an account', async () => {
  const b = bridge({ 'nexus.settings.get': { configured: false } }), state = {}
  await createNexusRuntime(b, state).open()
  assert.deepEqual(b.calls.map(call => call[0]), ['nexus.settings.get'])
})

test('saving credentials clears input and does not automatically connect', async () => {
  const b = bridge({ 'nexus.settings.save': { configured: true, notice: 'saved' } }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.apiKey = 'owned-secret'
  await runtime.save()
  assert.deepEqual(b.calls.map(call => call[0]), ['nexus.settings.save'])
  assert.equal(state.apiKey, '')
})

test('the list pages through the whole catalogue until the total is reached', async () => {
  const b = bridge({ 'nexus.browse': payload => done({ mods: mods(payload.offset + 1, payload.offset < 40 ? 20 : 3), total: 43, page_size: 20 }) }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.settings = { configured: true }
  await runtime.browse()
  assert.equal(state.mods.length, 20); assert.equal(state.total, 43)
  await runtime.more(); await runtime.more(); await runtime.more()
  assert.equal(state.mods.length, 43)
  assert.deepEqual(b.calls.map(call => call[1].offset), [0, 20, 40])
})

test('a new search replaces the list and ignores late answers from the old one', async () => {
  let release
  const b = bridge({ 'nexus.browse': payload => payload.query === 'old' ? new Promise(resolve => { release = () => resolve(done({ mods: mods(1, 20), total: 99 })) })
    : done({ mods: mods(500, 2), total: 2, page_size: 20 }) }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.settings = { configured: true }
  state.query = 'old'; const first = runtime.browse()
  state.query = ' new '; await runtime.browse()
  release(); await first
  assert.deepEqual(state.mods.map(mod => mod.mod_id), [500, 501])
  assert.equal(state.appliedQuery, 'new')
  assert.equal(b.calls.at(-1)[1].query, 'new')
})

test('translation pieces arrive in order and join back with their separators', () => {
  assert.equal(joinPieces([{ sep: '', text: 'a' }, { sep: '\n', text: 'b' }, { sep: '', text: 'c' }]), 'a\nbc')
})

test('Nexus download hands a granted handle into the existing import preview', async () => {
  const imported = [], state = {}
  const runtime = createNexusRuntime(bridge({ 'nexus.download': done({ source_handle: 'owned-handle', kind: 'zip' }) }), state, value => imported.push(value))
  state.detail = { mod_id: 81, name: 'Owned' }; state.nxmUrl = 'owned-authorization'
  await runtime.download({ file_id: 11 })
  assert.equal(imported[0].source_handle, 'owned-handle')
  assert.equal(imported[0].display_name, 'Owned')
  assert.equal(state.nxmUrl, '')
})

test('download authorization error is visible and does not import', async () => {
  const imported = [], state = {}
  const runtime = createNexusRuntime(bridge({ 'nexus.download': { task_id: 'owned-job', request_id: 'r', sequence: 2, state: 'succeeded', data: { ok: false, message: '需要官网授权' } } }), state, value => imported.push(value))
  state.detail = { mod_id: 81 }
  await runtime.download({ file_id: 11 })
  assert.equal(state.error, '需要官网授权'); assert.equal(imported.length, 0)
})

test('description links go through the fixed link command', async () => {
  const b = bridge({ 'nexus.open': { opened: true } }), state = {}
  await createNexusRuntime(b, state).openLink('https://github.com/owner/repo')
  assert.deepEqual(b.calls[0], ['nexus.open', { page: 'link', url: 'https://github.com/owner/repo' }])
})

test('translation progress fills the reactive state and finishes', async () => {
  const { reactive } = await import('vue').catch(() => ({ reactive: value => new Proxy(value, {}) }))
  const state = reactive({})
  const b = { request: async (command, payload) => command === 'nexus.translate'
    ? { task_id: 't', request_id: 'r', sequence: 1, state: 'running' } : null,
    watchTask: (id, update) => {
      queueMicrotask(() => {
        update({ task_id: 't', request_id: 'r', sequence: 2, type: 'progress', state: 'running', completed: 1, total: 2, data: { part: 'summary', index: 0, sep: '', text: '整理战利品' } })
        update({ task_id: 't', request_id: 'r', sequence: 3, type: 'progress', state: 'running', completed: 2, total: 2, data: { part: 'description', index: 0, sep: '', text: '译文' } })
        update({ task_id: 't', request_id: 'r', sequence: 4, type: 'terminal', state: 'succeeded', data: { completed: true } })
      })
      return { dispose: async () => {}, cancel: async () => {} }
    } }
  const runtime = createNexusRuntime(b, state)
  state.detail = { mod_id: 81 }
  await runtime.translate()
  assert.equal(state.translation.status, 'done')
  assert.equal(runtime.translated('summary'), '整理战利品')
  assert.equal(runtime.translated('description'), '译文')
})
