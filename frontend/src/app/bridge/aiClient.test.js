import test from 'node:test'
import assert from 'node:assert/strict'
import { createBridge, BridgeError } from './client.js'

const id = '11111111-1111-4111-8111-111111111111'
const settings = { provider: 'openai_compatible', base_url: 'https://api.openai.com/v1', model: 'user-chosen-model', remember: false }
function setup() {
  const calls = []
  const bridge = createBridge({ transport: async request => { calls.push(request); return { request_id: request.request_id, ok: true, data: request.payload } } })
  return { calls, bridge }
}

test('optional AI bridge forwards settings, bounded selected text, and only session identities', async () => {
  const { calls, bridge } = setup()
  for (const command of ['ai.settings.get', 'ai.credentials.clear', 'ai.test']) await bridge.request(command)
  assert.deepEqual(await bridge.request('ai.settings.save', { ...settings, api_key: 'synthetic-key', remember: true }), { ...settings, api_key: 'synthetic-key', remember: true })
  await bridge.request('ai.settings.save', { ...settings, api_key: null })
  await bridge.request('ai.chat.send', { question: 'why?', selected_text: 'chosen lines', selected_label: 'selected log', session_id: id })
  await bridge.request('ai.chat.send', { question: 'new', session_id: null })
  for (const command of ['ai.chat.get', 'ai.chat.clear', 'ai.chat.export']) await bridge.request(command, { session_id: id })
  assert.equal(calls.length, 10)
  await bridge.dispose()
})

test('AI bridge permits HTTPS and loopback HTTP without URL credentials, query or fragments', async () => {
  const { calls, bridge } = setup()
  for (const base_url of ['https://provider.example/v1', 'http://localhost:1234/v1', 'http://127.0.0.1:1234/v1', 'http://[::1]:1234/v1']) await bridge.request('ai.settings.save', { ...settings, base_url })
  for (const base_url of ['http://provider.example/v1', 'file:///C:/secret', 'https://user:secret@provider.example/v1', 'https://provider.example/v1?token=secret', 'https://provider.example/v1#key', 'https://provider.example/v1?', 'https://provider.example/v1#', 'invalid']) await assert.rejects(bridge.request('ai.settings.save', { ...settings, base_url }), BridgeError)
  assert.equal(calls.length, 4)
  await bridge.dispose()
})

test('AI bridge rejects arbitrary files, tools, extra fields and invalid bounded text', async () => {
  const { calls, bridge } = setup()
  for (const extra of [{ provider: 'legacy' }, { remember: 'true' }, { model: '' }, { model: 'x'.repeat(201) }, { api_key: 123 }, { api_key: 'x'.repeat(4097) }, { path: 'C:/key.txt' }, { import_rimcrow: true }]) await assert.rejects(bridge.request('ai.settings.save', { ...settings, ...extra }), BridgeError)
  for (const extra of [{ question: '' }, { question: 'x'.repeat(2001) }, { selected_text: 'x'.repeat(4001) }, { selected_text: null }, { selected_label: 'x'.repeat(121) }, { session_id: 'free/path' }, { session_id: false }, { attachments: [] }, { installation_id: 'arbitrary-game/path' }, { installation_id: null }, { selected_evidence_id: id }, { installation_id: id, selected_evidence_id: 'arbitrary/log/path' }, { logs: ['all'] }, { tool: 'install' }]) await assert.rejects(bridge.request('ai.chat.send', { question: 'test', ...extra }), BridgeError)
  for (const command of ['ai.settings.get', 'ai.credentials.clear', 'ai.test']) await assert.rejects(bridge.request(command, { url: 'https://other.example' }), BridgeError)
  for (const command of ['ai.chat.get', 'ai.chat.clear', 'ai.chat.export']) {
    await assert.rejects(bridge.request(command, { session_id: null }), BridgeError)
    await assert.rejects(bridge.request(command, { session_id: id, path: 'C:/save.txt' }), BridgeError)
  }
  assert.equal(calls.length, 0)
  await bridge.dispose()
})

test('AI bridge accepts only explicit installation and selected evidence identities for readonly queries', async () => {
  const { calls, bridge } = setup()
  const plain = { question: 'explain' }
  await bridge.request('ai.chat.send', plain)
  await bridge.request('ai.chat.send', { ...plain, installation_id: id })
  await bridge.request('ai.chat.send', { ...plain, installation_id: id, selected_evidence_id: id })
  assert.deepEqual(calls.map(call => call.payload), [plain, { ...plain, installation_id: id }, { ...plain, installation_id: id, selected_evidence_id: id }])
  await bridge.request('ai.logs.list', { installation_id: id })
  await bridge.request('ai.logs.list', { installation_id: id, after: 0, limit: 20 })
  await bridge.request('ai.logs.list', { installation_id: id, after: Number.MAX_SAFE_INTEGER, limit: 1 })
  for (const payload of [{}, { installation_id: null }, { installation_id: 'free/path' }, { installation_id: id, path: 'any/log' }, { installation_id: id, after: -1 }, { installation_id: id, after: 0.5 }, { installation_id: id, after: Number.MAX_SAFE_INTEGER + 1 }, { installation_id: id, limit: 0 }, { installation_id: id, limit: 21 }, { installation_id: id, limit: '20' }, { installation_id: id, session_id: id }]) await assert.rejects(bridge.request('ai.logs.list', payload), BridgeError)
  assert.equal(calls.length, 6)
  await bridge.dispose()
})

test('AI bridge shows bounded backend user errors and hides transport exception details', async () => {
  const bridge = createBridge({ transport: async request => ({ request_id: request.request_id, ok: false, error: { code: 'AI_OFFLINE', message: '无法连接 AI 服务。' } }) })
  await assert.rejects(bridge.request('ai.test'), error => error.code === 'AI_OFFLINE' && error.message === '无法连接 AI 服务。')
  const broken = createBridge({ transport: async () => { throw new Error('secret api key') } })
  await assert.rejects(broken.request('ai.test'), error => !error.message.includes('secret'))
  await bridge.dispose()
  await broken.dispose()
})

test('suggestion resolution accepts only session and suggestion identities and exposes no write or approval fields', async () => {
  const { calls, bridge } = setup()
  const payload = { session_id: id, suggestion_id: id }
  await bridge.request('ai.suggestion.resolve', payload)
  for (const invalid of [{}, { session_id: id }, { ...payload, suggestion_id: 'free/path' }, { ...payload, path: 'C:/game' }, { ...payload, action: 'install' }, { ...payload, approval_id: id }, { ...payload, installation_id: id }]) await assert.rejects(bridge.request('ai.suggestion.resolve', invalid), BridgeError)
  assert.deepEqual(calls.map(call => call.payload), [payload])
  await bridge.dispose()
})
