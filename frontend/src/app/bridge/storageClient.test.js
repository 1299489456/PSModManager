import test from 'node:test'
import assert from 'node:assert/strict'
import { createBridge } from './client.js'

test('only storage selection accepts a manual destination, with bounded payload and readable errors', async () => {
  const calls = []
  const bridge = createBridge({ transport: async request => {
    calls.push(request)
    return { request_id: request.request_id, ok: true, data: { phase: 'idle' } }
  } })
  for (const command of ['status', 'choose', 'accept', 'restart']) await bridge.request(`app.storage.${command}`)
  await bridge.request('app.storage.start', { target: 'D:\\PSModManager' })
  assert.equal(calls.length, 5)
  for (const payload of [{}, { target: '' }, { target: 12 }, { target: 'x'.repeat(4097) }, { target: 'D:\\PSMM', force: true }]) {
    await assert.rejects(bridge.request('app.storage.start', payload))
  }
  for (const command of ['app.info', 'app.storage.status', 'app.storage.choose', 'profile.delete']) {
    await assert.rejects(bridge.request(command, { target: 'D:\\PSMM' }))
  }
  const failing = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false,
    error: { code: 'STORAGE_TARGET_NOT_EMPTY', message: '目标目录不是空目录。' } }) })
  await assert.rejects(failing.request('app.storage.start', { target: 'D:\\PSMM' }), /目标目录不是空目录/)
  await bridge.dispose(); await failing.dispose()
})

test('profile deletion carries exactly the displayed revision', async () => {
  let received
  const bridge = createBridge({ transport: async request => {
    received = request.payload
    return { request_id: request.request_id, ok: true, data: { selected_profile_id: null } }
  } })
  await bridge.request('profile.delete', { profile_id: 'scheme', expected_revision: 5 })
  assert.deepEqual(received, { profile_id: 'scheme', expected_revision: 5 })
  for (const revision of [-1, '5', 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(bridge.request('profile.delete', { profile_id: 'scheme', expected_revision: revision }))
  }
  await bridge.dispose()
})
