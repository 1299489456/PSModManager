import test from 'node:test'
import assert from 'node:assert/strict'
import { createProfileStore } from './profileStore.js'

const original = { profile_id: 'first', installation_id: 'game', revision: 3, name: '当前方案', selections: [], dirty: true }
const fallback = { ...original, profile_id: 'fallback', name: '保留方案', dirty: false }
async function fixture(replies = {}) {
  const calls = [], state = {}
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (typeof replies[command] === 'function') return replies[command](payload)
    return replies[command] || {}
  } }
  const store = createProfileStore(bridge, state)
  await store.setInstallation('game')
  state.current = original; state.profiles = [original, fallback]
  state.exportText = 'old content'; state.applyPreview = { snapshot_id: 'old' }; state.packageDetail = { release_id: 'detail' }
  return { store, state, calls }
}

test('deletion uses the displayed revision and selects the server fallback', async () => {
  const f = await fixture({ 'profile.delete': { deleted_profile_id: 'first', selected_profile_id: 'fallback' }, 'profile.get': fallback })
  await f.store.deleteCurrent()
  assert.deepEqual(f.calls, [
    { command: 'profile.delete', payload: { profile_id: 'first', expected_revision: 3 } },
    { command: 'profile.get', payload: { profile_id: 'fallback' } },
  ])
  assert.deepEqual(f.state.profiles, [fallback]); assert.equal(f.state.current.profile_id, 'fallback')
  assert.equal(f.state.exportText, ''); assert.equal(f.state.applyPreview, null); assert.equal(f.state.packageDetail, null)
  assert.match(f.state.notice, /已删除「当前方案」/); assert.equal(f.state.error, ''); assert.equal(f.state.busy, false)
})

test('deleting the last scheme returns to the empty state without creating another', async () => {
  const f = await fixture({ 'profile.delete': { deleted_profile_id: 'first', selected_profile_id: null } })
  f.state.profiles = [original]
  await f.store.deleteCurrent()
  assert.equal(f.state.current, null); assert.deepEqual(f.state.profiles, []); assert.equal(f.calls.length, 1)
})

test('revision conflict leaves scheme, selection, preview and notice intact', async () => {
  const f = await fixture({ 'profile.delete': () => { throw Object.assign(new Error('conflict'), { code: 'PROFILE_REVISION_CONFLICT' }) } })
  const result = await f.store.deleteCurrent()
  assert.equal(result, undefined); assert.equal(f.state.current, original)
  assert.deepEqual(f.state.profiles, [original, fallback]); assert.equal(f.state.exportText, 'old content')
  assert.equal(f.state.notice, ''); assert.match(f.state.error, /其他窗口修改/)
})

test('a failed fallback read reports successful deletion and asks for refresh', async () => {
  const f = await fixture({ 'profile.delete': { deleted_profile_id: 'first', selected_profile_id: 'fallback' },
    'profile.get': () => { throw new Error('offline') } })
  assert.equal((await f.store.deleteCurrent()).deleted_profile_id, 'first')
  assert.equal(f.state.current, null); assert.deepEqual(f.state.profiles, [fallback])
  assert.match(f.state.notice, /已删除/); assert.match(f.state.error, /请刷新列表/)
})

test('switching installation while deletion is pending cannot replace the new page', async () => {
  let finish
  const f = await fixture({ 'profile.delete': () => new Promise(resolve => { finish = resolve }) })
  const deleting = f.store.deleteCurrent()
  await f.store.setInstallation('different-game')
  finish({ deleted_profile_id: 'first', selected_profile_id: 'fallback' })
  await deleting
  assert.equal(f.state.current, null); assert.deepEqual(f.state.profiles, []); assert.equal(f.state.notice, '')
  assert.equal(f.calls.length, 1)
})
