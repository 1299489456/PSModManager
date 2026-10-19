import test from 'node:test'
import assert from 'node:assert/strict'
import { createBridge } from '../../app/bridge/client.js'
import { createInstallationController } from './controller.js'

test('cold startup crosses the actual closed bridge, including a saved install on reopen', async () => {
  for (const saved of [false, true]) {
    const installation = { installation_id: 'saved-game', canonical_root: 'C:/synthetic-game' }
    const calls = [], state = {}, selected = []
    let active = false
    const bridge = createBridge({ transport: async request => {
      calls.push(request.command)
      let data
      if (request.command === 'installation.list') data = { installations: saved || active ? [installation] : [], selected_installation_id: active ? installation.installation_id : null }
      else if (request.command === 'installation.auto_discover') {
        active = true
        data = { state: 'succeeded', data: { candidates: [], installation } }
      } else assert.fail(`Unexpected ${request.command}`)
      return { request_id: request.request_id, ok: true, data }
    } })
    const controller = createInstallationController(bridge, state, value => selected.push(value))
    await controller.initialize()
    assert.equal(state.error, '')
    assert.equal(state.selectedId, installation.installation_id)
    assert.deepEqual(selected, [installation])
    assert.deepEqual(calls.slice(0, 2), ['installation.list', 'installation.auto_discover'])
    await controller.dispose()
    await bridge.dispose()
  }
})

test('automatic discovery accepts no caller-supplied paths or flags', async () => {
  let calls = 0
  const bridge = createBridge({ transport: async request => { calls++; return { request_id: request.request_id, ok: true, data: {} } } })
  await bridge.request('installation.auto_discover')
  await assert.rejects(bridge.request('installation.auto_discover', { root: 'C:/game' }))
  await assert.rejects(bridge.request('installation.auto_discover', { force: true }))
  assert.equal(calls, 1)
  await bridge.dispose()
})
