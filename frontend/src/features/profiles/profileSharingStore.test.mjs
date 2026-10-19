import test from 'node:test'
import assert from 'node:assert/strict'
import { createProfileStore } from './profileStore.js'

const profile = { profile_id: 'profile', installation_id: 'installation', revision: 2, dirty: false,
  name: '我的方案', selections: [{ release_id: 'new', selected: true, resolved: true }] }

function fixture(replies = {}) {
  const calls = [], state = {}
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (typeof replies[command] === 'function') return replies[command](payload)
    return replies[command] || {}
  } }
  const store = createProfileStore(bridge, state)
  return { store, state, calls }
}

test('capture current saved list can create or explicitly replace a revision', async () => {
  const f = fixture({ 'profile.capture': profile })
  await f.store.setInstallation('installation')
  await f.store.capture('新方案')
  assert.deepEqual(f.calls[0], { command: 'profile.capture', payload: { installation_id: 'installation', name: '新方案' } })
  await f.store.capture('替换方案', true)
  assert.deepEqual(f.calls[1].payload, { installation_id: 'installation', name: '替换方案', profile_id: 'profile', expected_revision: 2 })
})

test('save as commits a new profile without replacing the original identity', async () => {
  const f = fixture({ 'profile.capture': profile, 'profile.clone': { ...profile, profile_id: 'clone', revision: 0 },
    'profile.get': { ...profile, profile_id: 'clone', revision: 0 } })
  await f.store.setInstallation('installation'); await f.store.capture('原方案')
  await f.store.saveAs('新的方案')
  assert.equal(f.state.current.profile_id, 'clone')
  assert.deepEqual(f.calls.find(row => row.command === 'profile.commit').payload, { profile_id: 'clone', expected_revision: 0 })
})

test('native share source opens preview, and import-and-apply uses the refreshed workspace', async () => {
  const f = fixture({ 'profile.share_preview': { name: '分享', preview_hash: 'hash', selections: profile.selections },
    'profile.share_import': profile, 'mod.workspace': { snapshot_id: 'fresh' }, 'profile.apply_saved': { completed: true } })
  await f.store.setInstallation('installation')
  await f.store.openSource({ source_handle: 'trusted-native-handle' })
  assert.equal(f.state.sourceHandle, 'trusted-native-handle')
  const result = await f.store.importCommit('分享方案', true)
  assert.equal(result.completed, true)
  assert.equal(f.calls.at(-1).payload.snapshot_id, 'fresh')
  assert.equal(f.state.importPreview, null)
})

test('apply preview names both activation and deactivation; confirmation uses the fixed snapshot', async () => {
  const f = fixture({ 'profile.capture': profile, 'mod.workspace': { snapshot_id: 'fixed', enabled_ids: ['old'],
    items: [{ id: 'old', release_id: 'old', display_name: '旧版' }, { id: 'new', release_id: 'new', display_name: '新版' }] },
    'profile.apply_saved': { completed: true } })
  await f.store.setInstallation('installation'); await f.store.capture('方案')
  await f.store.previewApply()
  assert.deepEqual(f.state.applyPreview.changes.map(row => [row.id, row.enable]), [['old', false], ['new', true]])
  await f.store.apply()
  assert.equal(f.calls.at(-1).payload.snapshot_id, 'fixed')
})

test('failed apply retains confirmation and reports backend error without success', async () => {
  const f = fixture({ 'profile.capture': profile, 'profile.apply_saved': { error_code: 'STALE', message: '文件已变化' } })
  await f.store.setInstallation('installation'); await f.store.capture('方案')
  f.state.applyPreview = { snapshot_id: 'fixed' }
  const result = await f.store.apply()
  assert.equal(result, undefined); assert.equal(f.state.error, '文件已变化')
  assert.equal(f.state.applyPreview.snapshot_id, 'fixed')
})
