import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

// Import the controller used by the real dialog; transport fixtures cover its
// lifecycle, while the separate desktop harness proves the filesystem consumer.
const componentUrl = new URL('../src/features/profiles/PlanPreview.vue', import.meta.url)
const source = await readFile(componentUrl, 'utf8')
const { descriptor } = parse(source)
const moduleSource = descriptor.script.content.replace(/from '([^']+)'/g, (_, path) => `from '${new URL(path, componentUrl).href}'`)
const { createPlanPreviewStore } = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString('base64')}`)
const id = '00000000-0000-4000-8000-000000000001', installation = '00000000-0000-4000-8000-000000000002'
const approvalId = '00000000-0000-4000-8000-000000000003', transactionId = '00000000-0000-4000-8000-000000000004', hash = 'a'.repeat(64)
const profile = { profile_id: id, profile_revision: 0, revision: 1, dirty: false }
const preview = (extra = {}) => ({ plan: { plan_id: id, plan_hash: hash, backup_scope: [], backup_count: 0, precondition_count: 1, verification_check_count: 1 }, files: [{ target_root_kind: 'mods', relative_path: 'asset.png', change: 'add', before: 'absent', after: hash }], total: 1, next_offset: null, can_approve: true, no_op: false, configuration_policy: 'unsupported', configuration_changes: [], execution_available: false, ...extra })
const status = (extra = {}) => ({ installation_id: installation, transaction: null, current_generation: null, recovery_required: false, blockers: [], verification_status: 'not_run', execution_available: true, startup_recovery: { status: 'clear', recovered_count: 0, pending_count: 0 }, ...extra })
const transaction = state => ({ transaction_id: transactionId, plan_id: id, state, target_generation: transactionId, last_sequence: 7 })
const success = data => ({ task_id: id, state: 'succeeded', data })
const approve = () => success({ approved: true, approval: { approval_id: approvalId, plan_id: id, plan_hash: hash }, execution_available: false })
const tick = () => new Promise(resolve => setImmediate(resolve))
function fixture(handler = () => undefined) {
  const calls = [], state = {}; let update, cancelled = 0, disposed = 0, current = status()
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload })
      const custom = handler(command, payload)
      if (custom !== undefined) return custom
      if (command === 'deployment.status') return current
      if (command === 'plan.create') return success(preview())
      if (command === 'plan.approve') return approve()
      return { task_id: id, state: 'running', stage: 'applying' }
    },
    watchTask(taskId, callback) {
      assert.equal(taskId, id); update = callback
      return { async cancel() { cancelled++; callback({ task_id: id, state: 'cancelling', stage: 'rolling_back', data: {} }) }, async dispose() { disposed++ } }
    },
  }
  const store = createPlanPreviewStore(bridge, state, () => approvalId)
  return { state, store, calls, setStatus(value) { current = value }, finish(taskState = 'succeeded', data = {}) { update({ task_id: id, state: taskState, data }) }, get cancelled() { return cancelled }, get disposed() { return disposed } }
}
async function ready(f) { await f.store.open(profile, installation); await f.store.approve() }

test('case030 actual deployment consumer compiles execution and recovery controls', () => {
  const script = compileScript(descriptor, { id: 'deployment-execution-test' })
  const template = compileTemplate({ source: descriptor.template.content, filename: 'PlanPreview.vue', id: 'deployment-execution-test', compilerOptions: { bindingMetadata: script.bindings } })
  assert.deepEqual(template.errors, [])
  for (const testid of ['plan-apply', 'deployment-state', 'deployment-pending', 'deployment-generation', 'deployment-verification', 'deployment-recover']) assert.ok(descriptor.template.content.includes(`data-testid="${testid}"`))
})

test('case030 preview reads installation status before generating a plan and cannot execute without approval', async () => {
  const f = fixture(); await f.store.open(profile, installation); await f.store.apply()
  assert.deepEqual(f.calls.map(call => call.command), ['deployment.status', 'plan.create'])
  assert.deepEqual(f.calls[0].payload, { installation_id: installation })
  assert.equal(f.store.canApply(), false)
})

test('case030 apply binds approved plan hash and approval id then reads persisted result', async () => {
  const f = fixture(); await ready(f)
  const applying = f.store.apply(); await tick()
  assert.deepEqual(f.calls.at(-1), { command: 'plan.apply', payload: { plan_id: id, plan_hash: hash, approval_id: approvalId } })
  assert.equal(f.state.executing, true); assert.equal(f.state.busy, true)
  f.setStatus(status({ transaction: transaction('committed'), current_generation: transactionId }))
  f.finish(); await applying
  assert.deepEqual(f.calls.at(-1), { command: 'deployment.status', payload: { installation_id: installation, plan_id: id } })
  assert.equal(f.state.deployment.transaction.state, 'committed'); assert.equal(f.state.deployment.verification_status, 'not_run')
  assert.match(f.state.notice, /已提交.*未进行/); assert.equal(f.store.canApply(), false); assert.equal(f.state.executing, false)
})

test('case030 repeated apply clicks cannot start concurrent tasks', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick()
  await f.store.apply(); await f.store.approve(); await f.store.refreshStatus()
  assert.equal(f.calls.filter(call => call.command === 'plan.apply').length, 1)
  f.setStatus(status({ transaction: transaction('committed'), current_generation: transactionId })); f.finish(); await applying
  assert.equal(f.disposed, 1)
})

test('case030 no-op plans never request approval or execution', async () => {
  const f = fixture(command => command === 'plan.create' ? success(preview({ no_op: true, can_approve: false, files: [], total: 0 })) : undefined)
  await ready(f); await f.store.apply()
  assert.deepEqual(f.calls.map(call => call.command), ['deployment.status', 'plan.create']); assert.equal(f.store.canApply(), false)
})

test('case030 guarded or unavailable installations cannot execute an approved plan', async () => {
  for (const change of [{ execution_available: false }, { blockers: [{ code: 'GUARD_PROCESS_ACTIVE', message: 'Game running' }] }]) {
    const f = fixture(); f.setStatus(status(change)); await ready(f); await f.store.apply()
    assert.equal(f.store.canApply(), false); assert.equal(f.calls.some(call => call.command === 'plan.apply'), false)
  }
})

test('case030 approval without a valid backend approval id cannot enable apply', async () => {
  const f = fixture(command => command === 'plan.approve' ? success({ approved: true, approval: { plan_id: id, plan_hash: hash }, execution_available: false }) : undefined)
  await ready(f); await f.store.apply(); assert.equal(f.store.canApply(), false)
  assert.equal(f.calls.some(call => call.command === 'plan.apply'), false)
})

test('case030 authenticated approval UUID is accepted without inventing a version or variant constraint', async () => {
  const receipt = '6004965e-117f-a27a-538c-70093af2e479'
  const f = fixture(command => command === 'plan.approve' ? success({ approved: true, approval: { approval_id: receipt, plan_id: id, plan_hash: hash }, execution_available: false }) : undefined)
  await ready(f); assert.equal(f.store.canApply(), true)
  const applying = f.store.apply(); await tick()
  assert.equal(f.calls.at(-1).payload.approval_id, receipt)
  f.setStatus(status({ transaction: transaction('committed'), current_generation: transactionId })); f.finish(); await applying
})

test('case030 task success alone does not create a committed generation', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick()
  f.setStatus(status({ transaction: transaction('applying'), execution_available: false })); f.finish(); await applying
  assert.equal(f.state.deployment.current_generation, null); assert.equal(f.state.deployment.transaction.state, 'applying')
  assert.doesNotMatch(f.state.notice, /已提交/); assert.equal(f.store.canApply(), false)
})

test('case032 cancelling application keeps consumer alive until actual rollback status arrives', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick()
  assert.equal(await f.store.clear(), false); await f.store.cancel(); await f.store.clear()
  assert.equal(f.cancelled, 1); assert.equal(f.state.busy, true); assert.equal(f.state.executing, true); assert.equal(f.state.cancelRequested, true)
  assert.equal(f.state.deployment.transaction, null); assert.equal(f.disposed, 0)
  f.setStatus(status({ transaction: transaction('rolled_back') })); f.finish('cancelled', { message: 'cancelled after rollback' }); await applying
  assert.equal(f.state.deployment.transaction.state, 'rolled_back'); assert.match(f.state.notice, /已恢复/)
  assert.equal(f.state.error, ''); assert.equal(f.state.executing, false); assert.equal(f.disposed, 1); assert.equal(f.store.canApply(), false)
})

test('case032 cancellation before apply response cancels the late task once and reads rollback', async () => {
  let resolveApply
  const f = fixture(command => command === 'plan.apply' ? new Promise(resolve => { resolveApply = resolve }) : command === 'task.cancel' ? { task_id: id, state: 'cancelled', data: {} } : undefined)
  await ready(f); const applying = f.store.apply(); await tick(); await f.store.clear(); await f.store.cancel()
  f.setStatus(status({ transaction: transaction('rolled_back') })); resolveApply({ task_id: id, state: 'running' }); await applying
  assert.equal(f.calls.filter(call => call.command === 'task.cancel').length, 1)
  assert.equal(f.state.deployment.transaction.state, 'rolled_back'); assert.equal(f.state.preview.plan.plan_id, id)
})

test('case032 cancelled task with recovery conflict exposes durable block instead of claiming rollback', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick(); await f.store.cancel()
  f.setStatus(status({ transaction: transaction('recovery_required'), recovery_required: true, execution_available: false, blockers: [{ code: 'RECOVERY_CONFLICT', message: 'External file changed' }] }))
  f.finish('cancelled', { error_code: 'RECOVERY_CONFLICT', message: 'Recovery blocked' }); await applying
  assert.equal(f.state.deployment.recovery_required, true); assert.equal(f.state.preview, null); assert.equal(f.state.approval, null)
  assert.match(f.state.notice, /需要恢复/); assert.doesNotMatch(f.state.notice, /已恢复/); assert.match(f.state.error, /RECOVERY_CONFLICT/)
})

test('case030 failed apply rereads authoritative deployment state and retains actual error code', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick()
  f.setStatus(status({ transaction: transaction('rolled_back') })); f.finish('failed', { error_code: 'DEPLOYMENT_IO_ERROR', message: 'Disk write failed' }); await applying
  assert.equal(f.calls.at(-1).command, 'deployment.status'); assert.equal(f.state.deployment.transaction.state, 'rolled_back')
  assert.match(f.state.error, /DEPLOYMENT_IO_ERROR/); assert.equal(f.state.preview, null)
})

test('case030 succeeded envelope with error code still rereads disk and is never treated as success', async () => {
  const f = fixture(); await ready(f); const applying = f.store.apply(); await tick()
  f.setStatus(status({ transaction: transaction('failed'), execution_available: false })); f.finish('succeeded', { error_code: 'PLAN_STALE', message: 'Changed baseline' }); await applying
  assert.equal(f.calls.at(-1).command, 'deployment.status'); assert.match(f.state.error, /PLAN_STALE/); assert.equal(f.state.approval, null)
})

test('case030 failed post-task status is unknown and cannot fabricate a disk outcome', async () => {
  let reads = 0
  const f = fixture(command => command === 'deployment.status' && ++reads > 1 ? Promise.reject(new Error('Status unavailable')) : undefined)
  await ready(f); const applying = f.store.apply(); await tick(); f.finish(); await applying
  assert.equal(f.state.statusUnknown, true); assert.equal(f.state.deployment, null); assert.equal(f.state.approval, null)
  assert.equal(f.store.canApply(), false); assert.match(f.state.error, /Status unavailable/)
})

test('case030 malformed or foreign status fails closed before any plan creation', async () => {
  for (const value of [status({ installation_id: id }), status({ verification_status: 'passed' }), status({ blockers: null }), status({ startup_recovery: { status: 'clear', recovered_count: -1, pending_count: 0 } })]) {
    const f = fixture(command => command === 'deployment.status' ? value : undefined); await f.store.open(profile, installation)
    assert.equal(f.state.statusUnknown, true); assert.equal(f.state.preview, null); assert.equal(f.calls.length, 1)
  }
})

test('case032 recovery-required startup status blocks new plan creation and approval', async () => {
  const f = fixture(); f.setStatus(status({ recovery_required: true, execution_available: false, transaction: transaction('recovery_required'), startup_recovery: { status: 'blocked', recovered_count: 0, pending_count: 1 } }))
  await f.store.open(profile, installation); await f.store.approve(); await f.store.apply()
  assert.deepEqual(f.calls.map(call => call.command), ['deployment.status']); assert.equal(f.state.deployment.recovery_required, true)
})

test('case032 recovery uses installation-bound backend task and rereads terminal state without cancellation', async () => {
  const f = fixture(); f.setStatus(status({ recovery_required: true, execution_available: false, transaction: transaction('recovery_required') }))
  await f.store.open(profile, installation); const recovering = f.store.recover(); await tick()
  assert.deepEqual(f.calls.at(-1), { command: 'deployment.recover', payload: { installation_id: installation } })
  assert.equal(await f.store.clear(), false); await f.store.cancel(); await f.store.recover()
  assert.equal(f.cancelled, 0); assert.equal(f.calls.filter(call => call.command === 'deployment.recover').length, 1)
  f.setStatus(status({ transaction: transaction('rolled_back'), startup_recovery: { status: 'recovered', recovered_count: 1, pending_count: 0 } })); f.finish(); await recovering
  assert.equal(f.calls.at(-1).command, 'deployment.status'); assert.equal(f.state.deployment.recovery_required, false)
  assert.equal(f.state.deployment.verification_status, 'not_run'); assert.equal(f.state.approval, null)
})

test('case032 failed recovery keeps actual durable block and does not claim completion', async () => {
  const blocked = status({ recovery_required: true, execution_available: false, transaction: transaction('recovery_required') })
  const f = fixture(); f.setStatus(blocked); await f.store.open(profile, installation)
  const recovering = f.store.recover(); await tick(); f.finish('failed', { error_code: 'RECOVERY_CONFLICT', message: 'Manual repair required' }); await recovering
  assert.deepEqual(f.state.deployment, blocked); assert.match(f.state.error, /RECOVERY_CONFLICT/)
  assert.doesNotMatch(f.state.notice, /已处理/)
})

test('case032 disposal unsubscribes recovery without cancelling durable recovery task', async () => {
  const f = fixture(); f.setStatus(status({ recovery_required: true, execution_available: false }))
  await f.store.open(profile, installation); const recovering = f.store.recover(); await tick(); await f.store.dispose(); await recovering
  assert.equal(f.cancelled, 0); assert.equal(f.disposed, 1); assert.equal(f.state.deployment, null)
})

test('case030 delayed installation status after disposal cannot restore stale state or create a plan', async () => {
  let resolveStatus
  const f = fixture(command => command === 'deployment.status' ? new Promise(resolve => { resolveStatus = resolve }) : undefined)
  const opening = f.store.open(profile, installation); await tick(); await f.store.dispose(); resolveStatus(status()); await opening
  assert.equal(f.state.deployment, null); assert.equal(f.state.preview, null); assert.equal(f.calls.length, 1)
})
