<script>
import { terminalStates } from '../../app/bridge/reducer.js'

const uuid = value => typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)
const states = ['planned', 'approved', 'staging', 'ready', 'applying', 'committed', 'rolling_back', 'rolled_back', 'failed', 'cancelled', 'recovery_required']
export function createDeploymentHistoryStore(bridge, state) {
  let generation = 0, disposed = false, watcher = null, pending = null
  const empty = () => ({ installationId: null, history: null, busy: false, task: null, offset: 0, offsets: [0], error: '', notice: '', statusUnknown: false, operation: null })
  Object.assign(state, empty())
  const valid = token => !disposed && token === generation
  const failure = snapshot => Object.assign(new Error(snapshot.data?.message || '部署历史操作未完成。'), { code: snapshot.data?.error_code })
  async function call(command, payload) {
    const token = generation
    const previous = watcher; watcher = null
    await previous?.dispose()
    if (!valid(token)) return null
    const result = await bridge.request(command, payload)
    if (!valid(token)) return null
    if (!result?.task_id) throw new Error('部署历史操作未返回后台任务。')
    const accepted = snapshot => snapshot.state === 'succeeded' && (!snapshot.data?.error_code || command === 'deployment.history' && Array.isArray(snapshot.data.records))
    state.task = result
    if (terminalStates.has(result.state)) {
      if (!accepted(result)) throw failure(result)
      return result.data
    }
    return new Promise((resolve, reject) => {
      pending = resolve
      watcher = bridge.watchTask(result.task_id, snapshot => {
        if (!valid(token)) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        pending = null
        if (accepted(snapshot)) resolve(snapshot.data)
        else reject(failure(snapshot))
      }, reject)
    })
  }
  function accept(result, offset) {
    if (!result || result.installation_id !== state.installationId || !(result.current_generation === null || uuid(result.current_generation)) || typeof result.recovery_required !== 'boolean' || result.verification_status !== 'not_run' || result.cleanup_available !== false || !Array.isArray(result.records) || !Array.isArray(result.issues) || !Number.isSafeInteger(result.total) || result.total < 0) throw new Error('部署历史响应不完整，无法确认回退条件。')
    if (result.records.length > 10 || offset + result.records.length > result.total || !(result.next_offset === null || Number.isSafeInteger(result.next_offset) && result.next_offset === offset + result.records.length && result.next_offset > offset && result.next_offset < result.total) || result.next_offset === null && offset + result.records.length !== result.total) throw new Error('部署历史分页响应无效，请刷新。')
    for (const record of result.records) {
      if (!uuid(record.transaction_id) || !(record.generation_id === null || uuid(record.generation_id)) || !(record.sequence === null || Number.isSafeInteger(record.sequence) && record.sequence > 0) || !states.includes(record.state) || !['deployment', 'rollback', 'unknown'].includes(record.kind) || typeof record.rollback_allowed !== 'boolean' || !Array.isArray(record.rollback_blockers) || record.rollback_blockers.some(code => typeof code !== 'string') || record.retention?.protected !== true || record.retention.cleanup_available !== false || !Array.isArray(record.retention.reasons) || record.retention.reasons.some(reason => typeof reason !== 'string')) throw new Error('事务或保留条件响应不完整，无法确认回退条件。')
      if (record.rollback_allowed && (record.state !== 'committed' || !record.generation_id || !record.sequence || record.kind === 'unknown' || record.rollback_blockers.length)) throw new Error('未完成或未知事务不能回退，请刷新历史。')
    }
    for (const issue of result.issues) {
      if (typeof issue.issue_id !== 'string' || typeof issue.code !== 'string' || !['recovery_required', 'resolved'].includes(issue.state) || !Array.isArray(issue.files) || issue.files.some(file => typeof file.target_root_kind !== 'string' || typeof file.relative_path !== 'string' || typeof file.preserved !== 'boolean')) throw new Error('恢复记录响应不完整，无法确认文件保全状态。')
    }
    if (result.error_code && (typeof result.error_code !== 'string' || result.recovery_required !== true || result.records.some(record => record.kind !== 'unknown' || record.rollback_allowed))) throw new Error('历史来源错误响应未关闭回退操作，无法确认安全状态。')
    state.history = result; state.offset = offset; state.statusUnknown = Boolean(result.error_code)
    if (result.error_code) state.error = `${result.error_code}：历史来源无法校验，以下仅显示现存事务记录，所有回退已停用。`
    if (!state.offsets.includes(offset)) state.offsets.push(offset)
    return result
  }
  async function read(offset = 0) {
    const result = await call('deployment.history', { installation_id: state.installationId, offset, limit: 10 })
    if (result) return accept(result, offset)
  }
  async function run(operation, action) {
    if (disposed || state.busy || !state.installationId) return
    const token = generation
    state.busy = true; state.error = ''; state.notice = ''; state.task = null; state.operation = operation
    try { return await action(() => valid(token)) }
    catch (error) {
      if (valid(token)) {
        state.statusUnknown = true
        state.error = `${error.code ? `${error.code}：` : ''}${error.message || '部署历史操作未完成。'}`
      }
    } finally {
      if (valid(token)) {
        const previous = watcher; watcher = null; pending = null
        await previous?.dispose()
        if (valid(token)) { state.busy = false; state.operation = null }
      }
    }
  }
  async function clear() {
    generation++
    const previous = watcher; watcher = null
    pending?.(null); pending = null
    Object.assign(state, empty())
    // Recovery and conflict rechecks remain backend-owned when this view closes.
    await previous?.dispose()
  }
  const canRollback = record => !disposed && !state.busy && !state.statusUnknown && !state.history?.recovery_required && !!state.history?.current_generation && record?.state === 'committed' && !!record.generation_id && record.kind !== 'unknown' && record.rollback_allowed === true && !record.rollback_blockers.length && state.history.records.includes(record)
  return {
    async open(installationId) {
      await clear()
      if (disposed || !installationId) return
      state.installationId = installationId
      return run('history', () => read())
    },
    refresh() { return run('history', () => read(0)) },
    page(offset) {
      if (!Number.isSafeInteger(offset) || offset < 0 || offset >= (state.history?.total || 0)) return
      return run('history', () => read(offset))
    },
    recheck() {
      return run('recheck', async current => {
        const result = await call('deployment.conflicts.recheck', { installation_id: state.installationId })
        if (!current()) return
        if (!result || result.verification_status !== 'not_run' || typeof result.recovery_required !== 'boolean' || !Array.isArray(result.issues) || ['resolved_count', 'remaining_count'].some(key => !Number.isSafeInteger(result[key]) || result[key] < 0)) throw new Error('复查响应不完整，尚未确认文件已恢复。')
        const history = await read(0)
        if (!current()) return
        state.notice = history.recovery_required ? '仍有文件或事务需要恢复，请核对保全记录。' : `复查完成：${result.resolved_count} 项已解除，运行验证未进行。`
        return result
      })
    },
    recover() {
      if (!state.history?.recovery_required) return
      return run('recover', async current => {
        await call('deployment.recover', { installation_id: state.installationId })
        if (!current()) return
        const history = await read(0)
        if (current()) state.notice = history.recovery_required ? '仍有恢复阻断，请核对保全记录。' : '未完成事务已处理，运行验证未进行。'
      })
    },
    canRollback,
    clear,
    async dispose() { disposed = true; await clear() },
  }
}
</script>
<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import PlanPreview from '../profiles/PlanPreview.vue'
const props = defineProps({ bridge: { type: Object, required: true }, installation: { type: Object, default: null } })
const state = reactive({}), store = createDeploymentHistoryStore(props.bridge, state), rollback = ref(null)
const previousOffset = computed(() => state.offsets[state.offsets.indexOf(state.offset) - 1] ?? 0)
const pendingRecords = computed(() => state.history?.records.some(record => ['staging', 'ready', 'applying', 'rolling_back', 'recovery_required'].includes(record.state)))
const labels = { planned: '计划已登记', approved: '已批准', staging: '正在暂存', ready: '准备执行', applying: '正在部署', committed: '已提交', rolling_back: '正在恢复', rolled_back: '已恢复部署前状态', failed: '失败', cancelled: '已取消', recovery_required: '需要恢复' }
const roots = { mods: 'Mods', plugins: 'Plugins', user_libs: 'UserLibs', supported_config: '配置' }
const reasons = { current_generation: '当前部署引用', journal_reference: '持久事务记录引用', rollback_source: '回退来源引用', unresolved_issue: '未解决恢复记录引用', backup_required: '恢复所需备份', history_reference: '历史记录引用', signed_history: '已认证历史记录', plan_baseline: '计划基线与备份引用', owner_references: '文件所有权引用', history_chain: '历史代次链引用' }
const blockers = { ROLLBACK_STALE: '当前部署已经变化，请刷新后重新预览。', ROLLBACK_BACKUP_MISSING: '恢复所需备份缺失。', ROLLBACK_BACKUP_CORRUPT: '备份校验失败。', ROLLBACK_NOT_COMMITTED: '此事务未提交，不能回退。', ROLLBACK_DRIFT: '发现外部变更，已保全并阻断自动回退。', GUARD_RECOVERY_REQUIRED: '存在未解决恢复记录。', HISTORY_SOURCE_INVALID: '历史来源无法校验，不能回退。', ROLLBACK_SUPERSEDED: '相关文件已被后续部署改变，不能直接回退此记录。', HISTORY_SOURCE_UNCOMMITTED: '此事务尚未提交，不能作为回退来源。', ROLLBACK_PREVIEW_UNAVAILABLE: '回退预检未通过，请核对文件和备份。', PLAN_GENERATION_INVALID: '部署代次来源校验失败，不能回退。' }
const hash = value => value === 'absent' ? '不存在' : typeof value === 'string' ? value : JSON.stringify(value)
function choose(record) { if (store.canRollback(record)) rollback.value = { transactionId: record.transaction_id, expectedGeneration: state.history.current_generation } }
function closed() { rollback.value = null; void store.refresh() }
watch(() => props.installation?.installation_id, value => { rollback.value = null; void store.open(value) }, { immediate: true })
onUnmounted(() => { void store.dispose() })
</script>
<template>
  <section class="deployment-history" data-testid="deployment-history" aria-labelledby="history-title">
    <header><div><h2 id="history-title">部署历史与恢复</h2><p>回退所选部署的文件变更，当前编辑方案保持不变。</p></div><button data-testid="history-refresh" :disabled="!installation || state.busy || !!rollback" @click="store.refresh">刷新历史</button></header>
    <p v-if="!installation" data-testid="history-no-installation">请先选择安装目录，再查看部署与恢复记录。</p>
    <p v-if="state.busy" role="status" data-testid="history-progress">{{ state.operation === 'recheck' ? '正在复查已手动恢复的文件…' : state.operation === 'recover' ? '正在恢复未完成事务…' : '正在校验部署历史…' }}</p>
    <p v-if="state.error" role="alert" class="error" data-testid="history-error">{{ state.error }}</p>
    <p v-if="state.statusUnknown" class="error">历史状态尚未确认，回退操作已停用。请刷新后重试。</p>
    <p v-if="state.notice" role="status" data-testid="history-notice">{{ state.notice }}</p>
    <template v-if="state.history">
      <div class="current"><p data-testid="history-current-generation">当前部署代次：{{ state.history.current_generation || '尚无已提交部署' }}</p><p data-testid="history-verification">文件提交与运行验收分别记录。运行验证：未进行。</p></div>
      <section v-if="state.history.recovery_required" class="recovery-alert" data-testid="history-recovery-required"><h3>需要恢复</h3><p>新的部署和回退已阻断。外部变更会保全；请核对文件并手动恢复后复查。</p><button v-if="pendingRecords" data-testid="history-recover" :disabled="state.busy || !!rollback" @click="store.recover">恢复未完成事务</button></section>
      <section v-if="state.history.issues.length" aria-labelledby="issues-title"><h3 id="issues-title">恢复记录</h3><article v-for="issue in state.history.issues" :key="issue.issue_id" class="issue" data-testid="history-issue" :data-issue-state="issue.state"><div class="row-heading"><strong>{{ issue.state === 'resolved' ? '已复查解除' : '需要手动恢复' }}</strong><span>{{ issue.code }}</span></div><p>{{ issue.state === 'resolved' ? '文件已按记录复查，恢复阻断已解除；保全副本与历史继续保留。' : blockers[issue.code] || '请核对记录中的文件与预期状态；无法确认时保持原件并停止写入。' }}</p><ul class="issue-files"><li v-for="file in issue.files" :key="`${file.target_root_kind}:${file.relative_path}`" data-testid="history-conflict-file"><strong>{{ roots[file.target_root_kind] || file.target_root_kind }}/{{ file.relative_path }}</strong><p data-testid="history-preservation">{{ issue.state === 'resolved' ? file.preserved ? '原件的手动恢复已通过复查；保全副本继续保留。' : '原件的手动恢复已通过复查；副本保全未确认。' : file.preserved ? '外部变更已保全；原件保持原状。' : '原件保持原状；副本保全尚未确认。' }}</p><details><summary>核对文件哈希</summary><dl><dt>回退预期</dt><dd>{{ hash(file.expected) }}</dd><dt>发现的文件</dt><dd>{{ hash(file.actual) }}</dd><template v-if="file.copy_sha256"><dt>保全副本</dt><dd>{{ file.copy_sha256 }}</dd></template></dl></details></li></ul></article><button data-testid="history-recheck" :disabled="state.busy || !!rollback" @click="store.recheck">复查已手动恢复的文件</button></section>
      <h3>事务与部署代次 <span>{{ state.history.total }} 项</span></h3>
      <p v-if="!state.history.total" data-testid="history-empty">尚无部署事务。</p>
      <ol class="records" data-testid="history-records"><li v-for="record in state.history.records" :key="record.transaction_id" data-testid="history-record" :data-transaction-id="record.transaction_id" :data-generation-id="record.generation_id || ''" :data-kind="record.kind" :data-state="record.state"><div class="row-heading"><div><h4>{{ record.kind === 'rollback' ? '回退提交' : record.kind === 'deployment' ? '部署' : '待校验事务' }}{{ record.sequence ? ` · 代次 ${record.sequence}` : '' }}</h4><p>{{ record.profile_name || '未命名记录' }} · {{ labels[record.state] }}</p></div><span v-if="record.generation_id && record.generation_id === state.history.current_generation" class="current-label">当前部署</span></div><p v-if="record.committed_at">提交时间：{{ record.committed_at }}</p><p v-if="record.reverts_transaction" data-testid="history-reverts">回退了事务 {{ record.reverts_transaction }}</p><ul v-if="record.rollback_blockers.length" class="blockers"><li v-for="code in record.rollback_blockers" :key="code">{{ code }}：{{ blockers[code] || '回退条件未满足，请核对来源、文件和备份。' }}</li></ul><details><summary>事务与保留依据</summary><dl><dt>事务</dt><dd>{{ record.transaction_id }}</dd><dt>部署代次</dt><dd>{{ record.generation_id || '未提交' }}</dd><dt>保留依据</dt><dd>{{ record.retention.reasons.map(reason => reasons[reason] || reason).join('、') || '恢复历史引用' }}</dd></dl><p>历史与备份受引用保护，当前版本不提供清理。不要手工删除正在使用或未完成事务的数据。</p></details><button data-testid="history-rollback" :disabled="!store.canRollback(record) || !!rollback" @click="choose(record)">回退这次部署</button></li></ol>
      <nav v-if="state.history.total" aria-label="部署历史分页"><button data-testid="history-previous" :disabled="state.busy || !state.offset || !!rollback" @click="store.page(previousOffset)">上一页</button><span>{{ state.offset + 1 }}–{{ state.offset + state.history.records.length }} / {{ state.history.total }}</span><button data-testid="history-next" :disabled="state.busy || state.history.next_offset === null || !!rollback" @click="store.page(state.history.next_offset)">下一页</button></nav>
      <p class="boundary">未知文件、配置和可变数据保留原状。此页不会启动游戏；回退提交后仍需独立运行验证。</p>
    </template>
    <PlanPreview v-if="rollback && installation" :bridge="bridge" :installation="installation" :rollback="rollback" @close="closed"/>
  </section>
</template>
<style scoped>
.deployment-history{margin:18px 24px 30px;padding:20px 22px;border:1px solid rgb(var(--rgb-border-base)/.18);border-radius:12px;background:var(--bg-surface);color:var(--text-main);font-size:15px;line-height:1.6;overflow:auto;min-height:0}header,.row-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}h2{margin:0;font-size:21px}h3{margin:22px 0 10px;font-size:17px}h3 span{font-size:14px;font-weight:400;color:var(--text-dim)}h4{margin:0;font-size:16px}p{margin:6px 0}header p,.records p,.boundary{font-size:14px;color:var(--text-dim)}.current{margin:20px 0;padding:12px 16px;background:var(--bg-deep);border-radius:8px;overflow-wrap:anywhere}.current-label{font-size:14px;color:var(--accent-primary)}.recovery-alert,.issue{border:1px solid var(--accent-danger);border-radius:8px;padding:12px 16px;margin:12px 0}.recovery-alert h3{margin:0 0 8px}.records{list-style:none;margin:0;padding:0}.records>li{padding:14px 16px;margin:10px 0;border:1px solid rgb(var(--rgb-border-base)/.2);border-radius:8px;background:var(--bg-deep)}details{margin:12px 0}summary{cursor:pointer;font-size:14px}dt{font-size:14px;color:var(--text-dim)}dd{margin:0 0 8px;overflow-wrap:anywhere}.issue-files{padding-left:20px}.issue-files strong{overflow-wrap:anywhere}.blockers{font-size:14px;color:var(--accent-danger);padding-left:20px}.error{color:var(--accent-danger)}nav{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin:14px 0}button{font:inherit;min-height:36px;padding:6px 12px;border:1px solid rgb(var(--rgb-border-base)/.25);border-radius:7px;background:var(--bg-surface);color:inherit;cursor:pointer}button:hover:enabled{background:rgb(var(--rgb-accent-primary)/.13);border-color:var(--accent-primary)}button:disabled{opacity:.45;cursor:default}button:focus-visible,summary:focus-visible{outline:2px solid var(--accent-primary);outline-offset:2px}.boundary{border-top:1px solid rgb(var(--rgb-border-base)/.16);padding-top:12px;margin-top:20px}@media(max-width:650px){.deployment-history{margin:12px;padding:14px}header,.row-heading{align-items:flex-start;flex-direction:column;gap:8px}nav{justify-content:flex-start}}
</style>
