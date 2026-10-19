<script>
import { terminalStates } from '../../app/bridge/reducer.js'
import { createRequestId } from '../../app/requestId.js'

// All plan facts and approvals come from the same backend commands used by AI proposals.
export function createPlanPreviewStore(bridge, state, newId = createRequestId) {
  let generation = 0, disposed = false, watcher = null, pending = null
  const empty = () => ({ busy: false, task: null, preview: null, approval: null, conflicts: [], errorDetails: null, offset: 0, offsets: [0], error: '', notice: '', installationId: null, activePlanId: null, deployment: null, statusUnknown: false, executing: false, operation: null, cancelRequested: false, intent: 'deployment', sourceTransactionId: null })
  Object.assign(state, empty())
  const valid = token => !disposed && token === generation
  const failed = snapshot => Object.assign(new Error(snapshot.data?.message || (snapshot.state === 'cancelled' ? '操作已取消。' : '计划操作未完成。')), { code: snapshot.data?.error_code, conflicts: snapshot.data?.conflicts, details: snapshot.data?.details, taskState: snapshot.state })
  async function call(command, payload) {
    const token = generation, operation = state.operation
    let result = await bridge.request(command, payload)
    if (!valid(token)) {
      if (operation !== 'recover' && result?.task_id && !terminalStates.has(result.state)) await bridge.request('task.cancel', { task_id: result.task_id }).catch(() => {})
      return null
    }
    if (!result?.task_id) throw new Error('计划操作未返回后台任务。')
    if (state.executing && state.cancelRequested && !terminalStates.has(result.state)) {
      result = await bridge.request('task.cancel', { task_id: result.task_id })
      if (!valid(token)) return null
    }
    state.task = result
    if (terminalStates.has(result.state)) {
      if (result.state !== 'succeeded' || result.data?.error_code) throw failed(result)
      return result.data
    }
    return new Promise((resolve, reject) => {
      pending = resolve
      watcher = bridge.watchTask(result.task_id, snapshot => {
        if (!valid(token)) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        pending = null
        if (snapshot.state === 'succeeded' && !snapshot.data?.error_code) resolve(snapshot.data)
        else reject(failed(snapshot))
      }, reject)
    })
  }
  async function run(action) {
    if (disposed || state.busy) return
    const token = generation
    state.busy = true; state.error = ''; state.notice = ''; state.task = null; state.conflicts = []; state.errorDetails = null
    try { return await action(() => valid(token)) }
    catch (error) {
      if (valid(token)) {
        state.preview = null; state.approval = null
        state.conflicts = Array.isArray(error.conflicts) ? error.conflicts.slice(0, 20) : []
        state.errorDetails = error.details || null
        state.error = `${error.code ? `${error.code}：` : ''}${error.message || '计划操作未完成，请重新预览。'}`
      }
    } finally {
      if (valid(token)) {
        const previous = watcher; watcher = null; pending = null
        await previous?.dispose()
        if (valid(token)) state.busy = false
      }
    }
  }
  function accept(result, offset, previous = null) {
    if (!result?.plan?.plan_id || !/^[a-f0-9]{64}$/.test(result.plan.plan_hash || '') || !Array.isArray(result.files) || !Number.isSafeInteger(result.total) || result.total < 0 || typeof result.can_approve !== 'boolean' || typeof result.no_op !== 'boolean' || result.execution_available !== false) throw new Error('计划预览响应不完整，请重新预览。')
    if (['backup_count', 'precondition_count', 'verification_check_count'].some(key => !Number.isSafeInteger(result.plan[key]) || result.plan[key] < 0) || !Array.isArray(result.plan.backup_scope) || result.configuration_policy !== 'unsupported' || !Array.isArray(result.configuration_changes) || result.configuration_changes.length) throw new Error('计划影响响应不完整，请重新预览。')
    if (previous && (previous.plan_id !== result.plan.plan_id || previous.plan_hash !== result.plan.plan_hash)) throw new Error('计划内容已变化，请重新预览。')
    if (state.intent === 'rollback' && (result.intent !== 'rollback' || result.source_transaction_id !== state.sourceTransactionId)) throw new Error('回退计划与所选历史不匹配，请重新预览。')
    if (result.next_offset !== null && (!Number.isSafeInteger(result.next_offset) || result.next_offset <= offset || result.next_offset >= result.total)) throw new Error('计划分页响应无效，请重新预览。')
    if (result.files.length > 20 || offset + result.files.length > result.total || result.next_offset !== null && result.next_offset !== offset + result.files.length) throw new Error('计划分页响应无效，请重新预览。')
    if (result.next_offset === null && offset + result.files.length !== result.total) throw new Error('计划分页响应无效，请重新预览。')
    state.preview = result; state.offset = offset
    state.activePlanId = result.plan.plan_id
    if (!state.offsets.includes(offset)) state.offsets.push(offset)
    return result
  }
  async function readStatus(planId = state.activePlanId) {
    if (!state.installationId) return null
    const token = generation
    try {
      const result = await bridge.request('deployment.status', { installation_id: state.installationId, ...(planId ? { plan_id: planId } : {}) })
      if (!valid(token)) return null
      const states = ['planned', 'approved', 'staging', 'ready', 'applying', 'committed', 'rolling_back', 'rolled_back', 'failed', 'cancelled', 'recovery_required']
      if (!result || result.installation_id !== state.installationId || typeof result.recovery_required !== 'boolean' || typeof result.execution_available !== 'boolean' || result.verification_status !== 'not_run' || !Array.isArray(result.blockers) || !(result.current_generation === null || typeof result.current_generation === 'string') || !(result.transaction === null || result.transaction && states.includes(result.transaction.state) && typeof result.transaction.transaction_id === 'string' && typeof result.transaction.plan_id === 'string') || !['clear', 'recovered', 'blocked'].includes(result.startup_recovery?.status) || ['recovered_count', 'pending_count'].some(key => !Number.isSafeInteger(result.startup_recovery[key]) || result.startup_recovery[key] < 0)) throw new Error('部署状态响应不完整，无法确认磁盘结果。')
      state.deployment = result; state.statusUnknown = false
      return result
    } catch (error) {
      if (valid(token)) { state.deployment = null; state.statusUnknown = true }
      throw error
    }
  }
  async function cancel() {
    if (!state.busy) return
    if (state.operation === 'recover') { state.notice = '正在恢复未完成事务，完成后可关闭。'; return }
    if (state.executing) {
      if (state.cancelRequested) return
      state.cancelRequested = true; state.notice = '正在取消并恢复部署前状态，等待事务结果。'
    }
    const task = state.task
    if (!task || terminalStates.has(task.state)) return
    try { if (watcher) await watcher.cancel(); else state.task = await bridge.request('task.cancel', { task_id: task.task_id }) }
    catch { if (!disposed) { state.cancelRequested = false; state.error = '取消请求未完成，请重试。' } }
  }
  async function clear(force = false) {
    if (state.executing && !force) { await cancel(); return false }
    generation++
    const previous = watcher, cancelTask = state.operation !== 'recover'; watcher = null
    pending?.(null); pending = null
    Object.assign(state, empty())
    if (cancelTask) await previous?.cancel().catch(() => {})
    await previous?.dispose()
    return true
  }
  const canApply = () => !disposed && !state.busy && !state.statusUnknown && !!state.preview?.can_approve && !state.preview.no_op && !!state.approval && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(state.approval.approval_id || '') && state.approval.plan_id === state.preview.plan.plan_id && state.approval.plan_hash === state.preview.plan.plan_hash && state.deployment?.execution_available === true && !state.deployment.recovery_required && !state.deployment.blockers.length && !(state.deployment.transaction?.plan_id === state.preview.plan.plan_id && ['committed', 'rolled_back'].includes(state.deployment.transaction.state))
  const execute = (operation, command, payload) => run(async current => {
    state.executing = true; state.operation = operation; state.cancelRequested = false
    let result, failure
    try { result = await call(command, payload) }
    catch (error) { failure = error }
    try {
      if (!current()) return
      // Task success/cancellation alone never proves a filesystem outcome.
      const status = await readStatus()
      if (!current()) return
      if (status?.transaction?.state === 'committed') state.notice = state.intent === 'rollback' ? '回退已提交，运行验证未进行。' : '部署已提交，运行验证未进行。'
      else if (status?.transaction?.state === 'rolled_back') state.notice = '已恢复部署前状态，运行验证未进行。'
      else if (status?.recovery_required) state.notice = '事务需要恢复，已阻断新的部署。'
      else if (operation === 'recover') state.notice = '未完成事务已处理，运行验证未进行。'
      else if (result?.no_op) state.notice = '没有变化，无需部署。'
      if (failure && !(failure.taskState === 'cancelled' && ['rolled_back', 'cancelled'].includes(status?.transaction?.state))) throw failure
      return status
    } finally {
      if (current()) { state.executing = false; state.operation = null; state.cancelRequested = false }
    }
  })
  return {
    async openRollback(installationId, transactionId, expectedGeneration) {
      if (!await clear()) return
      if (disposed) return
      state.installationId = installationId; state.intent = 'rollback'; state.sourceTransactionId = transactionId
      return run(async current => {
        const status = await readStatus(null)
        if (!current() || status?.recovery_required) return
        if (!expectedGeneration || status.current_generation !== expectedGeneration) throw Object.assign(new Error('当前部署已变化，请刷新历史后重新选择。'), { code: 'ROLLBACK_STALE' })
        try {
          const result = await call('deployment.rollback.create', { installation_id: installationId, transaction_id: transactionId, expected_generation: expectedGeneration, idempotency_key: newId() })
          if (current()) return accept(result, 0)
        } catch (error) {
          if (current()) await readStatus(null).catch(() => {})
          throw error
        }
      })
    },
    async open(profile, installationId = profile?.installation_id || null) {
      if (!await clear()) return
      if (disposed) return
      state.installationId = installationId
      if (!profile || profile.dirty || !Number.isSafeInteger(profile.profile_revision) || profile.profile_revision < 0) { state.error = '请先保存当前方案，再预览部署。'; return }
      return run(async current => {
        if (state.installationId) {
          const status = await readStatus(null)
          if (!current() || status?.recovery_required) return
        }
        const result = await call('plan.create', { profile_id: profile.profile_id, expected_revision: profile.profile_revision, idempotency_key: newId() })
        if (current()) return accept(result, 0)
      })
    },
    page(offset) {
      if (!state.preview || !Number.isSafeInteger(offset) || offset < 0 || offset >= state.preview.total) return
      const plan = state.preview.plan
      return run(async current => {
        const result = await call('plan.inspect', { plan_id: plan.plan_id, plan_hash: plan.plan_hash, offset, limit: 20 })
        if (current()) return accept(result, offset, plan)
      })
    },
    approve() {
      if (!state.preview?.can_approve || state.preview.no_op || state.approval) return
      const plan = state.preview.plan
      return run(async current => {
        const result = await call('plan.approve', { plan_id: plan.plan_id, plan_hash: plan.plan_hash })
        if (!current()) return
        if (typeof result?.approved !== 'boolean' || result.execution_available !== false) throw new Error('批准响应不完整，请重新预览。')
        if (!result.approved) { state.notice = '未批准此计划。'; return result }
        if (!result.approval || result.approval.plan_id !== plan.plan_id || result.approval.plan_hash !== plan.plan_hash) throw new Error('批准与当前计划不匹配，请重新预览。')
        state.approval = result.approval; state.notice = '计划已批准，尚未应用到游戏。'
        return result
      })
    },
    apply() {
      if (!canApply()) return
      return execute('apply', state.intent === 'rollback' ? 'deployment.rollback' : 'plan.apply', { plan_id: state.preview.plan.plan_id, plan_hash: state.preview.plan.plan_hash, approval_id: state.approval.approval_id })
    },
    recover() {
      if (!state.installationId || !state.deployment?.recovery_required || state.busy) return
      return execute('recover', 'deployment.recover', { installation_id: state.installationId })
    },
    refreshStatus() { if (state.installationId) return run(() => readStatus()) },
    canApply,
    cancel,
    clear,
    async dispose() { disposed = true; await clear(true) },
  }
}
</script>
<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
const props = defineProps({ bridge: { type: Object, required: true }, profile: { type: Object, default: null }, rollback: { type: Object, default: null }, installation: { type: Object, required: true } })
const emit = defineEmits(['close'])
const dialog = ref(null), state = reactive({}), store = createPlanPreviewStore(props.bridge, state)
const plan = computed(() => state.preview?.plan)
const previousOffset = computed(() => state.offsets[state.offsets.indexOf(state.offset) - 1] ?? 0)
const canApply = computed(() => store.canApply())
const stages = { planned: '计划已登记', approved: '已批准', staging: '正在暂存文件', ready: '准备执行', applying: '正在部署', committed: '部署已提交', rolling_back: '正在恢复部署前状态', rolled_back: '已恢复部署前状态', failed: '部署失败', cancelled: '部署已取消', recovery_required: '需要恢复' }
const progress = computed(() => state.operation === 'recover' ? '正在恢复未完成事务…' : state.executing ? (state.cancelRequested || state.task?.state === 'cancelling' ? '正在取消并恢复部署前状态，等待事务结果…' : `${stages[state.task?.stage] || '正在执行部署'}…`) : state.task?.state === 'cancelling' ? '正在取消…' : '正在读取和校验计划…')
const changes = { add: '新增', replace: '替换', remove: '移除', keep: '保留', conflict: '冲突' }
const roots = { mods: 'Mods', plugins: 'Plugins', user_libs: 'UserLibs', supported_config: '配置' }
const bytes = value => Number.isFinite(value) ? `${value.toLocaleString('zh-CN')} 字节` : '未知'
const hash = value => value === 'absent' ? '不存在' : value
function close() { if (state.executing) { void store.cancel(); return }; void store.clear(); dialog.value?.close(); emit('close') }
function open() { return props.rollback ? store.openRollback(props.installation.installation_id, props.rollback.transactionId, props.rollback.expectedGeneration) : store.open(props.profile, props.installation.installation_id) }
watch(() => [props.profile?.profile_id, props.profile?.revision, props.profile?.profile_revision, props.profile?.dirty, props.rollback?.transactionId, props.rollback?.expectedGeneration, props.installation.installation_id], close)
onMounted(() => { dialog.value.showModal(); void open() })
onUnmounted(() => { void store.dispose() })
</script>
<template>
  <dialog ref="dialog" class="plan-preview" data-testid="plan-preview" :data-dismiss="state.executing ? 'keep' : undefined" aria-labelledby="plan-title" @cancel.prevent.stop="close">
    <header><div><h2 id="plan-title">{{ rollback ? '回退计划预览' : '部署计划预览' }}</h2><p>{{ rollback ? '核对所选部署的回退差异，批准后执行' : `${profile.name} · 已保存修订 ${profile.profile_revision}` }}</p></div><button data-testid="plan-close" aria-label="关闭计划预览" @click="close">×</button></header>
    <div class="body">
      <p v-if="state.busy" role="status" data-testid="plan-progress">{{ progress }}<span v-if="state.executing && state.task?.total"> {{ state.task.completed }} / {{ state.task.total }}</span></p>
      <p v-if="state.error" role="alert" class="error" data-testid="plan-error">{{ state.error }}</p>
      <p v-if="rollback" class="boundary">只回退这次部署，当前方案不随回退修改。外部变更会保全并阻断自动回退；请先手动恢复文件，再到恢复页复查。</p>
      <section v-if="state.conflicts.length" aria-labelledby="conflicts-title"><h3 id="conflicts-title">目标冲突</h3><ul class="files"><li v-for="file in state.conflicts" :key="`${file.target_root_kind}:${file.relative_path}`" data-testid="plan-conflict" data-change="conflict"><div class="file-heading"><strong>{{ roots[file.target_root_kind] || file.target_root_kind }}/{{ file.relative_path }}</strong><span>冲突</span></div><details><summary>查看前后哈希</summary><dl><dt>当前文件</dt><dd>{{ hash(file.actual ?? file.before) }}</dd><dt>{{ rollback ? '回退预期' : '方案需要' }}</dt><dd>{{ hash(file.expected ?? file.after) }}</dd></dl></details></li></ul></section>
      <p v-if="state.notice" role="status" data-testid="plan-notice">{{ state.notice }}</p>
      <p v-if="state.statusUnknown" role="alert" class="error" data-testid="deployment-unknown">无法确认当前部署结果，请刷新部署状态。</p>
      <section v-if="state.deployment" class="deployment-status" aria-labelledby="deployment-title"><h3 id="deployment-title">部署状态</h3><p v-if="state.deployment.startup_recovery.status === 'recovered'" data-testid="deployment-startup-recovery">启动时已恢复 {{ state.deployment.startup_recovery.recovered_count }} 个未完成事务。</p><p v-if="state.deployment.recovery_required" class="error" data-testid="deployment-recovery-blocked">事务需要恢复，新的部署已阻断。</p><p v-if="state.executing" data-testid="deployment-pending">当前事务尚在执行，完成后核对持久状态。</p><p v-else-if="state.deployment.transaction" data-testid="deployment-state" :data-state="state.deployment.transaction.state" :data-plan-id="state.deployment.transaction.plan_id">{{ stages[state.deployment.transaction.state] }}</p><dl><dt>最近已提交的部署代次</dt><dd data-testid="deployment-generation">{{ state.deployment.current_generation || '尚无部署代次' }}</dd><dt>运行验证</dt><dd data-testid="deployment-verification">未进行</dd></dl><ul v-if="state.deployment.blockers.length"><li v-for="(blocker, index) in state.deployment.blockers" :key="index">{{ blocker.code }}：{{ blocker.message }}</li></ul><details v-if="state.deployment.transaction && !state.executing"><summary>事务记录</summary><dl><dt>事务</dt><dd>{{ state.deployment.transaction.transaction_id }}</dd><dt>持久记录序号</dt><dd>{{ state.deployment.transaction.last_sequence }}</dd></dl></details></section>
      <template v-if="state.preview">
        <p class="summary" data-testid="plan-summary">{{ state.preview.no_op ? '没有变化，无需部署或批准。' : `共有 ${state.preview.total} 个文件记录，请核对完整预览后批准。` }}</p>
        <dl class="facts"><div><dt>游戏重启</dt><dd>{{ plan.restart_required ? '需要重启游戏' : '无需重启' }}</dd></div><div><dt>配置影响</dt><dd>当前版本不支持修改游戏配置；此计划没有配置变更。</dd></div><div><dt>备份范围</dt><dd>共 {{ plan.backup_count }} 个文件；{{ plan.backup_scope?.length ? `当前页：${plan.backup_scope.join('、')}` : '当前页无需备份' }}。全部范围可逐页查看各文件的备份标记。</dd></div><div><dt>计划期限</dt><dd>{{ plan.expires_at }}</dd></div></dl>
        <details v-if="plan.approval_scope?.length"><summary>批准范围</summary><ul><li v-for="scope in plan.approval_scope" :key="scope">{{ scope }}</li></ul></details>
        <section v-if="Object.keys(state.preview.space || {}).length" aria-labelledby="space-title"><h3 id="space-title">磁盘空间预检</h3><div class="table-scroll"><table data-testid="plan-space"><thead><tr><th>卷</th><th>包库</th><th>暂存</th><th>备份</th><th>目标增长</th><th>预留</th><th>总需求</th><th>可用</th></tr></thead><tbody><tr v-for="(space, volume) in state.preview.space" :key="volume"><th>{{ volume }}</th><td>{{ bytes(space.library) }}</td><td>{{ bytes(space.staging) }}</td><td>{{ bytes(space.backup) }}</td><td>{{ bytes(space.target_growth) }}</td><td>{{ bytes(space.overhead) }}</td><td>{{ bytes(space.required) }}</td><td>{{ bytes(space.available) }}</td></tr></tbody></table></div></section>
        <section aria-labelledby="files-title"><h3 id="files-title">文件差异 <span>{{ state.preview.total }} 项</span></h3><ul class="files" data-testid="plan-files"><li v-for="file in state.preview.files" :key="`${file.target_root_kind}:${file.relative_path}`" data-testid="plan-file" :data-change="file.change"><div class="file-heading"><strong>{{ roots[file.target_root_kind] || file.target_root_kind }}/{{ file.relative_path }}</strong><span>{{ changes[file.change] || file.change }}</span></div><p>{{ bytes(file.size_before) }} → {{ bytes(file.size_after) }} · {{ file.backup_required ? '需要备份' : '无需备份' }}</p><p v-if="file.owner_ids?.length > 1">共享文件，由 {{ file.owner_ids.length }} 个版本共同拥有</p><p v-else-if="!file.owner_ids?.length && !file.before_owner_ids?.length">未托管文件，保持原状</p><details><summary data-testid="plan-file-facts">查看完整哈希与归属</summary><dl><dt>当前文件</dt><dd data-testid="plan-before-hash">{{ hash(file.before) }}</dd><dt>计划文件</dt><dd data-testid="plan-after-hash">{{ hash(file.after) }}</dd><dt>原归属</dt><dd>{{ file.before_owner_ids?.join('、') || '未托管' }}</dd><dt>计划归属</dt><dd>{{ file.owner_ids?.join('、') || '无' }}</dd></dl></details></li></ul><nav v-if="state.preview.total" aria-label="计划文件分页"><button data-testid="plan-previous" :disabled="state.busy || !state.offset" @click="store.page(previousOffset)">上一页</button><span>{{ state.offset + 1 }}–{{ Math.min(state.offset + state.preview.files.length, state.preview.total) }} / {{ state.preview.total }}</span><button data-testid="plan-next" :disabled="state.busy || state.preview.next_offset === null" @click="store.page(state.preview.next_offset)">下一页</button></nav></section>
        <details><summary data-testid="plan-binding-toggle">计划绑定与校验依据</summary><dl><dt>计划哈希</dt><dd data-testid="plan-hash">{{ plan.plan_hash }}</dd><dt>部署基线</dt><dd>{{ plan.base_generation || '首次部署：完整基线快照' }}</dd><dt>基线哈希</dt><dd>{{ plan.baseline_manifest_hash }}</dd><dt>运行时指纹</dt><dd>{{ plan.fingerprint_id }}</dd><dt>前提检查</dt><dd>{{ plan.precondition_count }} 项</dd><dt>部署后验证</dt><dd>{{ plan.verification_check_count }} 项</dd></dl></details>
        <div v-if="state.approval" data-testid="plan-approval" role="status"><strong>已批准当前计划</strong><p>批准期限：{{ state.approval.expires_at }}</p><p>批准范围：{{ state.approval.scope?.join('、') }}</p></div>
      </template>
      <p class="boundary">预览和批准只登记计划；{{ rollback ? '“执行回退”' : '“应用到游戏”' }}执行已批准的文件变更。提交后仍需独立运行验证。</p>
    </div>
    <footer><button v-if="state.busy && state.task && state.operation !== 'recover'" data-testid="plan-cancel" :disabled="state.cancelRequested || state.task.state === 'cancelling'" @click="store.cancel">{{ state.executing ? '取消并恢复旧部署' : '取消操作' }}</button><button v-if="state.installationId" data-testid="deployment-refresh" :disabled="state.busy" @click="store.refreshStatus">刷新部署状态</button><button v-if="state.deployment?.recovery_required" data-testid="deployment-recover" :disabled="state.busy" @click="store.recover">恢复未完成事务</button><button data-testid="plan-recreate" :disabled="state.busy || profile?.dirty" @click="open">重新预览</button><button class="primary" data-testid="plan-approve" :disabled="state.busy || !state.preview?.can_approve || state.preview?.no_op || !!state.approval" @click="store.approve">批准当前计划</button><button class="primary" data-testid="plan-apply" :disabled="!canApply" @click="store.apply">{{ rollback ? '执行回退' : '应用到游戏' }}</button><button @click="close">关闭</button></footer>
  </dialog>
</template>
<style scoped>
.plan-preview{width:min(1100px,calc(100vw - 32px));max-height:calc(100dvh - 32px);padding:0;border:1px solid rgb(var(--rgb-border-base)/.24);border-radius:12px;background:var(--bg-surface);color:var(--text-main);font-size:15px;line-height:1.6;box-shadow:0 24px 70px #0005}.plan-preview[open]{display:flex;flex-direction:column}.plan-preview::backdrop{background:#0005}header,footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 22px;flex-shrink:0}header{border-bottom:1px solid rgb(var(--rgb-border-base)/.16)}header p{color:var(--text-dim);font-size:14px;margin:4px 0 0}header button{font-size:22px;width:36px;padding:0}h2{font-size:21px;margin:0}h3{font-size:17px;margin:20px 0 8px}h3 span{font-size:14px;font-weight:400;color:var(--text-dim)}h4{font-size:15px;margin:12px 0 6px}.body{padding:18px 22px;overflow:auto;min-height:0}.summary{font-weight:600}.facts{display:flex;flex-wrap:wrap;gap:12px 28px}.facts>div{flex:1;min-width:200px}dt{font-size:14px;color:var(--text-dim)}dd{margin:0 0 8px;overflow-wrap:anywhere}details{margin:12px 0}summary{cursor:pointer;font-size:14px}details dl{margin:8px 0}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid rgb(var(--rgb-border-base)/.16);white-space:nowrap}.files{list-style:none;margin:0;padding:0}.files>li{border:1px solid rgb(var(--rgb-border-base)/.18);border-radius:8px;padding:12px 14px;margin:8px 0;background:var(--bg-deep)}.file-heading{display:flex;justify-content:space-between;gap:16px}.file-heading strong{min-width:0;overflow-wrap:anywhere}.file-heading span{flex-shrink:0;font-size:14px}.files p{margin:4px 0;font-size:14px;color:var(--text-dim)}[data-change=conflict]{border-color:var(--accent-danger)}nav{display:flex;gap:12px;align-items:center;justify-content:flex-end;margin:12px 0}button{font:inherit;min-height:36px;padding:6px 12px;border:1px solid rgb(var(--rgb-border-base)/.24);border-radius:7px;background:var(--bg-surface);color:inherit;cursor:pointer}button:hover:enabled{background:rgb(var(--rgb-accent-primary)/.13);border-color:var(--accent-primary)}button:disabled{opacity:.45;cursor:default}button:focus-visible,summary:focus-visible{outline:2px solid var(--accent-primary);outline-offset:2px}.primary{background:rgb(var(--rgb-accent-primary)/.17);border-color:var(--accent-primary);font-weight:600}footer{justify-content:flex-end;flex-wrap:wrap;border-top:1px solid rgb(var(--rgb-border-base)/.16)}.error{color:var(--accent-danger)}.boundary{color:var(--text-dim);font-size:14px;border-top:1px solid rgb(var(--rgb-border-base)/.16);padding-top:12px;margin-top:20px}@media(max-width:650px){header,footer,.body{padding:12px 14px}.file-heading{flex-direction:column;gap:4px}.plan-preview{width:calc(100vw - 20px)}}
</style>
