import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { createBridge } from '../src/app/bridge/client.js'

// Exercise the controller exported by the actual consumer, without duplicating it.
const componentUrl = new URL('../src/features/profiles/PlanPreview.vue', import.meta.url)
const source = await readFile(componentUrl, 'utf8')
const { descriptor } = parse(source)
const moduleSource = descriptor.script.content.replace(/from '([^']+)'/g, (_, path) => `from '${new URL(path, componentUrl).href}'`)
const { createPlanPreviewStore } = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}`)
const uuid = '00000000-0000-4000-8000-000000000001', otherId = '00000000-0000-4000-8000-000000000002', hash = 'a'.repeat(64)
const profile = (extra = {}) => ({ profile_id: uuid, name: '已保存方案', revision: 7, committed_revision: 3, profile_revision: 0, dirty: false, ...extra })
const file = index => ({ target_root_kind: 'mods', relative_path: `${index}.dll`, change: 'add', before: 'absent', after: hash, before_owner_ids: [], owner_ids: [uuid], size_before: 0, size_after: 1, backup_required: false })
const preview = (extra = {}) => ({ plan: { plan_id: uuid, plan_hash: hash, backup_scope: [], backup_count: 0, precondition_count: 4, verification_check_count: 22, restart_required: true, approval_scope: ['files:mods'], expires_at: '2026-10-10T12:00:00Z' }, files: Array.from({ length: 20 }, (_, index) => file(index)), total: 21, next_offset: 20, can_approve: true, no_op: false, space: {}, configuration_changes: [], configuration_policy: 'unsupported', execution_available: false, ...extra })
const succeeded = data => ({ task_id: uuid, state: 'succeeded', data })
const approval = (extra = {}) => ({ approval_id: otherId, plan_id: uuid, plan_hash: hash, scope: ['files:mods'], expires_at: '2026-10-10T12:00:00Z', actor: 'native-confirmation', ...extra })
const tick = () => new Promise(resolve => setImmediate(resolve))
function fixture(handler = () => succeeded(preview())) {
  const calls = [], state = {}; let update, cancelled = 0, disposed = 0
  const bridge = {
    async request(command, payload) { calls.push({ command, payload }); return handler(command, payload) },
    watchTask(id, callback) { update = callback; return { async cancel() { cancelled++ }, async dispose() { disposed++ } } },
  }
  const store = createPlanPreviewStore(bridge, state, () => otherId)
  return { state, store, calls, finish(data, status = 'succeeded') { update({ task_id: uuid, state: status, data }) }, get cancelled() { return cancelled }, get disposed() { return disposed } }
}

test('case029 plan consumer compiles both exported controller and setup into one Vue component', () => {
  const script = compileScript(descriptor, { id: 'plan-preview-test' })
  assert.ok(script.content.includes('createPlanPreviewStore'))
  const template = compileTemplate({ source: descriptor.template.content, filename: 'PlanPreview.vue', id: 'plan-preview-test', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
})

test('case029 plan creation binds persisted Profile DTO revision rather than saved draft revision', async () => {
  const f = fixture(); await f.store.open(profile())
  assert.deepEqual(f.calls, [{ command: 'plan.create', payload: { profile_id: uuid, expected_revision: 0, idempotency_key: otherId } }])
  assert.equal(f.state.preview.plan.plan_hash, hash); assert.equal(f.state.approval, null)
})

test('case029 dirty or never-committed profiles cannot request a plan', async () => {
  const f = fixture()
  for (const input of [profile({ dirty: true }), profile({ profile_revision: null }), profile({ profile_revision: undefined }), profile({ profile_revision: -1 })]) {
    await f.store.open(input); assert.match(f.state.error, /先保存/)
  }
  assert.equal(f.calls.length, 0)
})

test('case029 plan inspect pages use the original plan hash and expose every file', async () => {
  const f = fixture((command, payload) => succeeded(command === 'plan.inspect' && payload.offset === 20 ? preview({ files: [file(20)], next_offset: null }) : preview()))
  await f.store.open(profile()); const allFiles = [...f.state.preview.files]
  await f.store.page(20); allFiles.push(...f.state.preview.files)
  assert.equal(allFiles.length, 21); assert.equal(new Set(allFiles.map(item => item.relative_path)).size, 21)
  assert.deepEqual(f.calls[1], { command: 'plan.inspect', payload: { plan_id: uuid, plan_hash: hash, offset: 20, limit: 20 } })
  await f.store.page(0); assert.equal(f.state.offset, 0); assert.equal(f.state.preview.next_offset, 20)
  assert.ok(f.calls.every(call => call.command !== 'plan.approve'))
})

test('case029 byte-bounded small pages retain exact server cursors for previous navigation', async () => {
  const f = fixture((command, payload) => {
    const offset = command === 'plan.create' ? 0 : payload.offset
    const end = offset === 0 ? 5 : offset === 5 ? 18 : 21
    return succeeded(preview({ files: Array.from({ length: end - offset }, (_, index) => file(index + offset)), next_offset: end === 21 ? null : end }))
  })
  await f.store.open(profile()); await f.store.page(f.state.preview.next_offset); await f.store.page(f.state.preview.next_offset)
  assert.deepEqual(f.state.offsets, [0, 5, 18]); assert.equal(f.state.offset, 18)
  await f.store.page(f.state.offsets[f.state.offsets.indexOf(f.state.offset) - 1]); assert.equal(f.state.offset, 5)
  await f.store.page(f.state.offsets[f.state.offsets.indexOf(f.state.offset) - 1]); assert.equal(f.state.offset, 0)
  assert.deepEqual(f.calls.filter(call => call.command === 'plan.inspect').map(call => call.payload.offset), [5, 18, 5, 0])
})

test('case029 no-op previews never send approval or fabricate execution state', async () => {
  const f = fixture(() => succeeded(preview({ files: [], total: 0, next_offset: null, no_op: true, can_approve: false })))
  await f.store.open(profile()); await f.store.approve(); await f.store.page(0)
  assert.equal(f.calls.length, 1); assert.equal(f.state.preview.no_op, true); assert.equal(f.state.preview.execution_available, false); assert.equal(f.state.approval, null)
})

test('case029 approval is requested only by explicit action and binds plan id and hash', async () => {
  const f = fixture(command => succeeded(command === 'plan.approve' ? { approved: true, approval: approval(), no_op: false, execution_available: false } : preview()))
  await f.store.open(profile()); assert.equal(f.calls.length, 1)
  await f.store.approve(); assert.deepEqual(f.calls[1], { command: 'plan.approve', payload: { plan_id: uuid, plan_hash: hash } })
  assert.equal(f.state.approval.plan_hash, hash); assert.match(f.state.notice, /尚未应用/)
  await f.store.approve(); assert.equal(f.calls.length, 2)
})

test('case029 native confirmation cancellation is a non-approval result', async () => {
  const f = fixture(command => succeeded(command === 'plan.approve' ? { approved: false, approval: null, no_op: false, execution_available: false } : preview()))
  await f.store.open(profile()); await f.store.approve()
  assert.equal(f.state.approval, null); assert.equal(f.state.error, ''); assert.match(f.state.notice, /未批准/)
})

test('case029 forged or mismatched approval results clear the plan binding', async () => {
  for (const fake of [approval({ plan_hash: 'b'.repeat(64) }), approval({ plan_id: otherId }), null]) {
    const f = fixture(command => succeeded(command === 'plan.approve' ? { approved: true, approval: fake, execution_available: false } : preview()))
    await f.store.open(profile()); await f.store.approve()
    assert.equal(f.state.approval, null); assert.equal(f.state.preview, null); assert.match(f.state.error, /不匹配/)
  }
})

test('case029 successful task envelope with error_code cannot become a successful preview', async () => {
  const f = fixture(() => succeeded({ error_code: 'PLAN_STALE', message: '计划已过期，请重新预览。' }))
  await f.store.open(profile())
  assert.equal(f.state.preview, null); assert.equal(f.state.busy, false); assert.match(f.state.error, /过期/)
})

test('case029 refused conflict preview retains actual targets and hashes without an approvable plan', async () => {
  const conflicts = [{ ...file(0), change: 'conflict', before: 'b'.repeat(64) }]
  const f = fixture(() => succeeded({ error_code: 'PLAN_TARGET_CONFLICT', message: '目标文件冲突。', conflicts, details: { conflict_count: 1 } }))
  await f.store.open(profile()); await f.store.approve()
  assert.deepEqual(f.state.conflicts, conflicts); assert.deepEqual(f.state.errorDetails, { conflict_count: 1 })
  assert.equal(f.state.preview, null); assert.equal(f.state.approval, null); assert.equal(f.calls.length, 1)
  assert.match(f.state.error, /PLAN_TARGET_CONFLICT/); await f.store.clear(); assert.deepEqual(f.state.conflicts, [])
})

test('case029 failed asynchronous approval invalidates the displayed approval and plan', async () => {
  const f = fixture(command => command === 'plan.approve' ? { task_id: uuid, state: 'running' } : succeeded(preview()))
  await f.store.open(profile()); const applying = f.store.approve(); await tick()
  f.finish({ error_code: 'PLAN_BASELINE_CHANGED', message: '磁盘文件已变化。' }, 'failed'); await applying
  assert.equal(f.state.approval, null); assert.equal(f.state.preview, null); assert.match(f.state.error, /文件已变化/); assert.equal(f.disposed, 1)
})

test('case029 changed plan hash during pagination invalidates the entire preview', async () => {
  const f = fixture(command => succeeded(command === 'plan.inspect' ? preview({ plan: { ...preview().plan, plan_hash: 'b'.repeat(64) } }) : preview()))
  await f.store.open(profile()); await f.store.page(20)
  assert.equal(f.state.preview, null); assert.equal(f.state.approval, null); assert.match(f.state.error, /内容已变化/)
})

test('case029 malformed pagination and executable preview responses are rejected', async () => {
  for (const result of [preview({ next_offset: 0 }), preview({ next_offset: 22 }), preview({ next_offset: null }), preview({ files: [], next_offset: null }), preview({ execution_available: true }), preview({ total: -1 }), preview({ configuration_changes: [{ fake: true }] }), preview({ plan: { ...preview().plan, backup_count: null } })]) {
    const f = fixture(() => succeeded(result)); await f.store.open(profile())
    assert.equal(f.state.preview, null); assert.notEqual(f.state.error, '')
  }
})

test('case029 closing or changing installation cancels the task and ignores delayed completion', async () => {
  const f = fixture(() => ({ task_id: uuid, state: 'running' }))
  const creating = f.store.open(profile()); await tick(); assert.equal(f.state.busy, true)
  await f.store.clear(); await creating; f.finish(preview())
  assert.equal(f.state.preview, null); assert.equal(f.state.approval, null); assert.equal(f.state.busy, false); assert.equal(f.cancelled, 1); assert.equal(f.disposed, 1)
})

test('case029 closing before task response arrives cancels the late task without a subscription', async () => {
  let resolve
  const f = fixture(command => command === 'task.cancel' ? {} : new Promise(done => { resolve = done }))
  const creating = f.store.open(profile()); await tick(); await f.store.clear()
  resolve({ task_id: uuid, state: 'running' }); await creating
  assert.equal(f.state.preview, null); assert.deepEqual(f.calls.at(-1), { command: 'task.cancel', payload: { task_id: uuid } }); assert.equal(f.disposed, 0)
})

test('case029 active task cancellation uses the task subscription and cannot duplicate approval', async () => {
  const f = fixture(command => command === 'plan.approve' ? { task_id: uuid, state: 'running' } : succeeded(preview()))
  await f.store.open(profile()); const approving = f.store.approve(); await tick()
  await f.store.approve(); await f.store.page(20); await f.store.cancel(); assert.equal(f.cancelled, 1)
  f.finish({ message: '操作已取消。' }, 'cancelled'); await approving
  assert.equal(f.calls.length, 2); assert.equal(f.state.approval, null); assert.equal(f.state.preview, null)
})

test('case029 disposed consumer cannot request or restore another plan', async () => {
  const f = fixture(); await f.store.open(profile()); await f.store.dispose(); await f.store.open(profile()); await f.store.approve()
  assert.equal(f.calls.length, 1); assert.equal(f.state.preview, null)
})

test('case029 real frontend bridge allows only backend-generated plan bindings and bounded pages', async () => {
  const calls = [], bridge = createBridge({ transport: async request => { calls.push(request); return { request_id: request.request_id, ok: true, data: succeeded(preview()) } } })
  await bridge.request('plan.create', { profile_id: uuid, expected_revision: 0, idempotency_key: otherId })
  await bridge.request('plan.inspect', { plan_id: uuid, plan_hash: hash, offset: 20, limit: 20 })
  await bridge.request('plan.approve', { plan_id: uuid, plan_hash: hash })
  assert.equal(calls.length, 3)
  for (const payload of [{ plan_id: uuid, plan_hash: hash, approved: true }, { plan_id: uuid, plan_hash: hash, actor: 'AI' }, { plan_id: uuid, plan_hash: hash, scope: ['all'] }, { plan_id: uuid, plan_hash: 'fake' }, { plan_id: 'free/path', plan_hash: hash }]) {
    await assert.rejects(bridge.request('plan.approve', payload), error => error.code === 'INVALID_REQUEST')
  }
  await assert.rejects(bridge.request('plan.inspect', { plan_id: uuid, plan_hash: hash, limit: 21 }), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('plan.create', { profile_id: uuid, expected_revision: 0, idempotency_key: 'free/path' }), error => error.code === 'INVALID_REQUEST')
  assert.equal(calls.length, 3); await bridge.dispose()
})

test('case029 assistant bridge proposals and inspection use the same schema and expose no self-approval route', async () => {
  const calls = [], bridge = createBridge({ transport: async request => { calls.push(request); return { request_id: request.request_id, ok: true, data: succeeded(preview()) } } })
  await bridge.request('assistant.propose_plan', { profile_id: uuid, expected_revision: 0, idempotency_key: otherId })
  await bridge.request('assistant.inspect_plan', { plan_id: uuid, plan_hash: hash, offset: 5, limit: 20 })
  assert.deepEqual(calls.map(call => call.command), ['assistant.propose_plan', 'assistant.inspect_plan'])
  for (const command of ['assistant.approve_plan', 'assistant.approve', 'plan.apply']) {
    await assert.rejects(bridge.request(command, { plan_id: uuid, plan_hash: hash }), error => error.code === 'INVALID_REQUEST')
  }
  await assert.rejects(bridge.request('assistant.propose_plan', { profile_id: uuid, expected_revision: 0, idempotency_key: otherId, approved: true }), error => error.code === 'INVALID_REQUEST')
  await assert.rejects(bridge.request('assistant.inspect_plan', { plan_id: uuid, plan_hash: hash, limit: 21 }), error => error.code === 'INVALID_REQUEST')
  assert.equal(calls.length, 2); await bridge.dispose()
})
