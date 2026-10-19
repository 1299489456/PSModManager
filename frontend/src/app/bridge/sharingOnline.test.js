import test from 'node:test'
import assert from 'node:assert/strict'
import { createBridge, BridgeError } from './client.js'

const id = '11111111-1111-4111-8111-111111111111'
const transport = async request => ({ request_id: request.request_id, ok: true, data: request.payload })

test('NXM bridge accepts bounded receipts, explicit registration and selected file website links', async () => {
  const bridge = createBridge({ transport })
  for (const command of ['nexus.protocol.status', 'nexus.protocol.unregister', 'nexus.protocol.choose_default', 'nexus.inbox.list']) await bridge.request(command)
  await bridge.request('nexus.protocol.register', { replace: false })
  await bridge.request('nexus.protocol.register', { replace: true })
  for (const command of ['nexus.inbox.download', 'nexus.inbox.discard']) {
    assert.deepEqual(await bridge.request(command, { id: 'a'.repeat(32) }), { id: 'a'.repeat(32) })
    await assert.rejects(bridge.request(command, { id: 'nxm://secret' }), BridgeError)
    await assert.rejects(bridge.request(command, { id: 'a'.repeat(32), nxm_url: 'secret' }), BridgeError)
  }
  await assert.rejects(bridge.request('nexus.protocol.register', { replace: 'yes' }), BridgeError)
  await assert.rejects(bridge.request('nexus.protocol.register', { replace: false, command: 'arbitrary.exe' }), BridgeError)
  await bridge.request('nexus.open', { page: 'files', mod_id: 81, file_id: 11 })
  await bridge.dispose()
})

test('sharing, capture and updates expose bound identities but never filesystem authority', async () => {
  const bridge = createBridge({ transport })
  await bridge.request('profile.capture', { installation_id: id, name: '保存列表', profile_id: id, expected_revision: 2 })
  await bridge.request('profile.share_export', { profile_id: id, kind: 'bundle' })
  await bridge.request('profile.share_preview', { source_handle: id, installation_id: id })
  const update = { installation_id: id, release_id: id, manifest_hash: 'a'.repeat(64), snapshot_id: 'b'.repeat(64), replaced_ids: [id], idempotency_key: id }
  assert.deepEqual(await bridge.request('package.update_apply', update), update)
  for (const change of [{ path: 'C:/game' }, { replaced_ids: [id,id] }, { snapshot_id: id }]) {
    await assert.rejects(bridge.request('package.update_apply', { ...update, ...change }), BridgeError)
  }
  await bridge.dispose()
})

test('Nexus commands accept fixed operation values and refuse arbitrary URLs and paths', async () => {
  const bridge = createBridge({ transport })
  await bridge.request('nexus.browse', { view: 'latest_updated' })
  await bridge.request('nexus.download', { mod_id: 1, file_id: 2, nxm_url: '' })
  for (const payload of [{ mod_id: -1, file_id: 2 }, { mod_id: 1, file_id: 2, url: 'https://private' }, { mod_id: 1, file_id: 2, destination: 'C:/game' }]) {
    await assert.rejects(bridge.request('nexus.download', payload), BridgeError)
  }
  await assert.rejects(bridge.request('nexus.browse', { view: 'all' }), BridgeError)
  await bridge.dispose()
})

test('Nexus browsing pages and searches; links open only as http(s) through the link page', async () => {
  const bridge = createBridge({ transport })
  await bridge.request('nexus.browse', { view: 'downloads', offset: 40, query: 'storage' })
  await bridge.request('nexus.mod', { mod_id: 81 })
  await bridge.request('nexus.translate', { mod_id: 81 })
  await bridge.request('nexus.open', { page: 'link', url: 'https://github.com/owner/repo' })
  for (const [command, payload] of [
    ['nexus.browse', { view: 'downloads', offset: -1 }], ['nexus.browse', { view: 'downloads', offset: 10001 }],
    ['nexus.browse', { view: 'downloads', query: 'x'.repeat(101) }], ['nexus.browse', { view: 'trending' }],
    ['nexus.translate', { mod_id: 81, text: 'any text' }], ['nexus.mod', { mod_id: 0 }],
    ['nexus.open', { page: 'link', url: 'javascript:alert(1)' }], ['nexus.open', { page: 'link', url: 'file:///C:/Windows' }],
    ['nexus.open', { page: 'link', url: 'https://user@host.test/' }], ['nexus.open', { page: 'link' }],
    ['nexus.open', { page: 'mod', mod_id: 1, url: 'https://github.com/' }],
  ]) await assert.rejects(bridge.request(command, payload), BridgeError, `${command} ${JSON.stringify(payload)}`)
  await bridge.dispose()
})
