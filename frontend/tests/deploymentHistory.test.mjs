import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

async function controller(relative, name) {
  const url = new URL(relative, import.meta.url), source = await readFile(url, 'utf8'), { descriptor } = parse(source)
  const module = descriptor.script.content.replace(/from '([^']+)'/g, (_, path) => `from '${new URL(path, url).href}'`)
  return { source, descriptor, create: (await import(`data:text/javascript;base64,${Buffer.from(module).toString('base64')}`))[name] }
}
const historyComponent = await controller('../src/features/deployments/DeploymentHistory.vue', 'createDeploymentHistoryStore')
const planComponent = await controller('../src/features/profiles/PlanPreview.vue', 'createPlanPreviewStore')
const installation = '00000000-0000-4000-8000-000000000001', transaction = '00000000-0000-4000-8000-000000000002', generation = '00000000-0000-4000-8000-000000000003', plan = '00000000-0000-4000-8000-000000000004', approval = '00000000-0000-4000-8000-000000000005', hash = 'a'.repeat(64)
const record = (extra = {}) => ({ transaction_id: transaction, generation_id: generation, sequence: 1, state: 'committed', kind: 'deployment', reverts_transaction: null, rollback_allowed: true, rollback_blockers: [], retention: { protected: true, reasons: ['current_generation'], cleanup_available: false }, ...extra })
const history = (extra = {}) => ({ installation_id: installation, current_generation: generation, records: [record()], total: 1, next_offset: null, issues: [], recovery_required: false, verification_status: 'not_run', cleanup_available: false, ...extra })
const issue = (state = 'recovery_required') => ({ issue_id: transaction, code: 'ROLLBACK_DRIFT', state, files: [{ target_root_kind: 'mods', relative_path: 'asset.png', expected: hash, actual: 'b'.repeat(64), preserved: true, copy_sha256: 'b'.repeat(64) }] })
const status = (extra = {}) => ({ installation_id: installation, current_generation: generation, transaction: null, recovery_required: false, blockers: [], verification_status: 'not_run', execution_available: true, startup_recovery: { status: 'clear', recovered_count: 0, pending_count: 0 }, ...extra })
const preview = (extra = {}) => ({ plan: { plan_id: plan, plan_hash: hash, backup_scope: [], backup_count: 0, precondition_count: 1, verification_check_count: 1 }, files: [{ change: 'remove', relative_path: 'asset.png', target_root_kind: 'mods' }], total: 1, next_offset: null, can_approve: true, no_op: false, configuration_policy: 'unsupported', configuration_changes: [], execution_available: false, intent: 'rollback', source_transaction_id: transaction, ...extra })
const success = data => ({ task_id: plan, state: 'succeeded', data })
const tick = () => new Promise(resolve => setImmediate(resolve))
function fixture(create, handler = () => undefined) {
  const state = {}, calls = []; let update, disposed = 0
  const bridge = { async request(command, payload) { calls.push({ command, payload }); const result = handler(command, payload); if (result !== undefined) return result; if (command === 'deployment.history') return success(history()); if (command === 'deployment.status') return status(); if (command === 'deployment.rollback.create') return success(preview()); if (command === 'plan.approve') return success({ approved: true, execution_available: false, approval: { approval_id: approval, plan_id: plan, plan_hash: hash } }); return { task_id: plan, state: 'running' } }, watchTask(id, callback) { assert.equal(id, plan); update = callback; return { async dispose() { disposed++ }, async cancel() {} } } }
  return { state, calls, store: create(bridge, state, () => approval), finish(taskState, data) { update({ task_id: plan, state: taskState, data }) }, get disposed() { return disposed } }
}

test('case034 actual history component compiles and exposes real bound consumers with no cleanup or force action', () => {
  for (const { descriptor } of [historyComponent, planComponent]) {
    const script = compileScript(descriptor, { id: 'history-test' })
    const result = compileTemplate({ source: descriptor.template.content, filename: 'History.vue', id: 'history-test', compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(result.errors, [])
  }
  for (const id of ['history-current-generation', 'history-rollback', 'history-recheck', 'history-issue', 'history-preservation', 'history-next']) assert.ok(historyComponent.source.includes(`data-testid="${id}"`))
  assert.match(historyComponent.source, /当前版本不提供清理/); assert.doesNotMatch(historyComponent.source, /deployment\.force|deployment\.cleanup/)
})
test('case034 history requests selected installation and bounded pagination before enabling rollback', async () => {
  const f = fixture(historyComponent.create); await f.store.open(installation)
  assert.deepEqual(f.calls, [{ command: 'deployment.history', payload: { installation_id: installation, offset: 0, limit: 10 } }])
  assert.equal(f.store.canRollback(f.state.history.records[0]), true); assert.equal(f.store.canRollback(record()), false)
  assert.equal(f.state.history.verification_status, 'not_run')
})
test('case034 incomplete, untrusted, failed and blocked records never enable rollback', async () => {
  for (const item of [record({ state: 'recovery_required', generation_id: null, sequence: null, rollback_allowed: false }), record({ kind: 'unknown', rollback_allowed: false }), record({ state: 'failed', rollback_allowed: false }), record({ rollback_allowed: false, rollback_blockers: ['ROLLBACK_BACKUP_CORRUPT'] })]) {
    const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(history({ records: [item] })) : undefined); await f.store.open(installation)
    assert.equal(f.state.error, ''); assert.equal(f.store.canRollback(f.state.history.records[0]), false)
  }
})
test('case034 malformed, foreign, verification-pass and unsafe rollback responses fail closed', async () => {
  for (const value of [history({ installation_id: transaction }), history({ verification_status: 'passed' }), history({ cleanup_available: true }), history({ records: [record({ state: 'applying' })] }), history({ records: [record({ kind: 'unknown' })] }), history({ records: [record({ retention: { protected: false } })] }), history({ next_offset: 0 })]) {
    const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(value) : undefined); await f.store.open(installation)
    assert.equal(f.state.statusUnknown, true); assert.equal(f.state.history, null); assert.match(f.state.error, /响应|事务/)
  }
})
test('case034 recovery-required installation blocks all rollback records', async () => {
  const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(history({ recovery_required: true, issues: [issue()] })) : undefined)
  await f.store.open(installation); assert.equal(f.store.canRollback(f.state.history.records[0]), false)
})
test('case034 invalid-source partial history only displays unknown closed records and retains its warning', async () => {
  const value = history({ current_generation: null, error_code: 'HISTORY_SOURCE_INVALID', recovery_required: true, records: [record({ generation_id: null, sequence: null, kind: 'unknown', rollback_allowed: false, rollback_blockers: ['HISTORY_SOURCE_INVALID'] })] })
  const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(value) : undefined)
  await f.store.open(installation)
  assert.equal(f.state.history.records[0].kind, 'unknown'); assert.equal(f.state.statusUnknown, true); assert.match(f.state.error, /HISTORY_SOURCE_INVALID/)
  assert.equal(f.store.canRollback(f.state.history.records[0]), false)
})
test('case034 partial history cannot enable a known or supposedly allowed record', async () => {
  for (const rows of [[record()], [record({ kind: 'unknown' })], [record({ rollback_allowed: false })]]) {
    const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(history({ records: rows, error_code: 'HISTORY_SOURCE_INVALID', recovery_required: true })) : undefined)
    await f.store.open(installation); assert.equal(f.state.history, null); assert.equal(f.state.statusUnknown, true)
  }
})
test('case034 pagination only accepts backend next offset and validates its records', async () => {
  const rows = Array.from({ length: 10 }, (_, index) => record({ transaction_id: `00000000-0000-4000-8000-${String(index + 10).padStart(12, '0')}` }))
  const f = fixture(historyComponent.create, (command, payload) => command === 'deployment.history' ? success(payload.offset ? history({ total: 11 }) : history({ records: rows, total: 11, next_offset: 10 })) : undefined)
  await f.store.open(installation); await f.store.page(10)
  assert.deepEqual(f.calls.at(-1).payload, { installation_id: installation, offset: 10, limit: 10 }); assert.deepEqual(f.state.offsets, [0, 10])
  await f.store.page(-1); await f.store.page(11); assert.equal(f.calls.length, 2)
})
test('case034 recheck uses backend task then rereads history before claiming resolution', async () => {
  let reads = 0
  const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(++reads === 1 ? history({ recovery_required: true, issues: [issue()] }) : history({ issues: [issue('resolved')] })) : command === 'deployment.conflicts.recheck' ? success({ resolved_count: 1, remaining_count: 0, recovery_required: false, issues: [issue('resolved')], verification_status: 'not_run' }) : undefined)
  await f.store.open(installation); await f.store.recheck()
  assert.deepEqual(f.calls.map(call => call.command), ['deployment.history', 'deployment.conflicts.recheck', 'deployment.history'])
  assert.equal(f.state.history.recovery_required, false); assert.match(f.state.notice, /1 项已解除.*未进行/)
})
test('case034 unresolved or failed recheck cannot display recovered state', async () => {
  for (const fail of [false, true]) {
    const f = fixture(historyComponent.create, command => command === 'deployment.history' ? success(history({ recovery_required: true, issues: [issue()] })) : command === 'deployment.conflicts.recheck' ? fail ? { task_id: plan, state: 'failed', data: { error_code: 'ROLLBACK_DRIFT', message: 'File still changed' } } : success({ resolved_count: 0, remaining_count: 1, recovery_required: true, issues: [issue()], verification_status: 'not_run' }) : undefined)
    await f.store.open(installation); await f.store.recheck()
    assert.equal(f.state.history.recovery_required, true); assert.doesNotMatch(f.state.notice, /已解除|已恢复/)
    if (fail) { assert.equal(f.state.statusUnknown, true); assert.match(f.state.error, /ROLLBACK_DRIFT/) }
  }
})
test('case034 stale asynchronous view responses cannot replace selected installation', async () => {
  let resolve
  const f = fixture(historyComponent.create, command => command === 'deployment.history' ? new Promise(done => { resolve = done }) : undefined)
  const opening = f.store.open(installation); await tick(); await f.store.clear(); resolve(success(history())); await opening
  assert.equal(f.state.installationId, null); assert.equal(f.state.history, null)
})
test('case034 disposal releases history task subscription without cancelling backend recovery', async () => {
  const f = fixture(historyComponent.create, command => command === 'deployment.history' ? { task_id: plan, state: 'running' } : undefined)
  const opening = f.store.open(installation); await tick(); await f.store.dispose(); await opening
  assert.equal(f.disposed, 1); assert.equal(f.calls.some(call => call.command === 'task.cancel'), false)
})
test('case034 rollback preview binds current generation and source transaction without approving', async () => {
  const f = fixture(planComponent.create); await f.store.openRollback(installation, transaction, generation)
  assert.deepEqual(f.calls, [{ command: 'deployment.status', payload: { installation_id: installation } }, { command: 'deployment.rollback.create', payload: { installation_id: installation, transaction_id: transaction, expected_generation: generation, idempotency_key: approval } }])
  assert.equal(f.store.canApply(), false); assert.equal(f.state.intent, 'rollback'); assert.equal(f.state.preview.source_transaction_id, transaction)
})
test('case034 stale generation or recovery block stops rollback plan creation', async () => {
  for (const value of [status({ current_generation: transaction }), status({ recovery_required: true, execution_available: false })]) {
    const f = fixture(planComponent.create, command => command === 'deployment.status' ? value : undefined); await f.store.openRollback(installation, transaction, generation)
    assert.equal(f.calls.length, 1); assert.equal(f.state.preview, null); assert.equal(f.store.canApply(), false)
  }
})
test('case034 preview rejects a different source transaction or rollback intent', async () => {
  for (const extra of [{ source_transaction_id: generation }, { intent: 'deployment' }]) {
    const f = fixture(planComponent.create, command => command === 'deployment.rollback.create' ? success(preview(extra)) : undefined); await f.store.openRollback(installation, transaction, generation)
    assert.equal(f.state.preview, null); assert.match(f.state.error, /不匹配/); assert.equal(f.store.canApply(), false)
  }
})
test('case034 rollback reuses native approval then invokes approved rollback executor and reads actual committed state', async () => {
  let reads = 0
  const f = fixture(planComponent.create, command => command === 'deployment.status' ? ++reads === 1 ? status() : status({ current_generation: plan, transaction: { transaction_id: approval, plan_id: plan, state: 'committed' } }) : undefined)
  await f.store.openRollback(installation, transaction, generation); await f.store.approve(); const applying = f.store.apply(); await tick()
  assert.deepEqual(f.calls.at(-1), { command: 'deployment.rollback', payload: { plan_id: plan, plan_hash: hash, approval_id: approval } })
  f.finish('succeeded', {}); await applying
  assert.equal(f.calls.at(-1).command, 'deployment.status'); assert.match(f.state.notice, /回退已提交.*未进行/); assert.equal(f.state.deployment.current_generation, plan)
})
test('case022 drift during plan creation rereads recovery status and preserves error without enabling execution', async () => {
  let reads = 0
  const f = fixture(planComponent.create, command => command === 'deployment.status' ? ++reads === 1 ? status() : status({ recovery_required: true, execution_available: false }) : command === 'deployment.rollback.create' ? { task_id: plan, state: 'failed', data: { error_code: 'ROLLBACK_DRIFT', message: 'Preserved external file' } } : undefined)
  await f.store.openRollback(installation, transaction, generation)
  assert.equal(f.state.deployment.recovery_required, true); assert.match(f.state.error, /ROLLBACK_DRIFT/); assert.equal(f.state.preview, null); assert.equal(f.store.canApply(), false)
})
