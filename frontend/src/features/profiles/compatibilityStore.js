import { terminalStates } from '../../app/bridge/reducer.js'
export function reasonCount(chain) {
  if (Array.isArray(chain)) return chain.length
  return Array.isArray(chain?.relation_ids) ? chain.relation_ids.length : 0
}
export function releaseLabel(labels, id) {
  const value = labels?.[id]
  return typeof value === 'string' ? value : value?.display_name || value?.name || '未知发布版本'
}
export function candidateSummary(preview) {
  const closure = preview.complete ? '已确认硬依赖闭包集合完整' : '仍有缺失或未知依赖'
  return `${closure}；${preview.candidate_blocking === true ? '候选组合仍有阻断' : preview.candidate_blocking === false ? '本次未发现候选阻断' : '候选阻断状态未确认'}，不代表运行兼容。`
}
export function createCompatibilityStore(bridge, state) {
  let profile = null, generation = 0, disposed = false, watcher = null
  Object.assign(state, { status: 'unchecked', report: null, task: null, busy: false, error: '', mode: 'check' })
  function setProfile(value) {
    const changed = profile?.profile_id !== value?.profile_id || profile?.revision !== value?.revision
    profile = value ? { profile_id: value.profile_id, revision: value.revision } : null
    if (!changed) return
    generation++
    const previous = watcher; watcher = null
    void previous?.cancel().catch(() => {}); void previous?.dispose()
    state.status = state.report || state.busy ? 'stale' : 'unchecked'
    state.report = null; state.task = null; state.busy = false; state.error = ''
  }
  async function check(mode = 'check', ids = []) {
    if (disposed || state.busy || !profile || !['check', 'enable', 'disable'].includes(mode) || (mode !== 'check' && !ids.length)) return
    const token = ++generation
    state.busy = true; state.status = 'checking'; state.report = null; state.error = ''; state.task = null; state.mode = mode
    const payload = { profile_id: profile.profile_id, expected_revision: profile.revision, profile_kind: 'draft' }
    if (mode !== 'check') payload[mode === 'enable' ? 'enable_release_ids' : 'disable_release_ids'] = [...new Set(ids)]
    let settled = false
    function failure(message) { state.error = message; state.status = 'error'; state.busy = false }
    const update = snapshot => {
      if (disposed || token !== generation || settled) return
      state.task = snapshot
      if (!terminalStates.has(snapshot.state)) return
      settled = true; state.busy = false
      if (snapshot.state === 'cancelled') { state.status = 'cancelled'; return }
      if (snapshot.state !== 'succeeded' || snapshot.data?.error_code) { failure(snapshot.data?.message || '检查未完成，请重新检查。'); return }
      if (!snapshot.data || !Array.isArray(snapshot.data.findings)) { failure('检查结果不完整，请重新检查。'); return }
      state.report = snapshot.data; state.status = 'ready'
    }
    try {
      await watcher?.dispose(); watcher = null
      const initial = await bridge.request('compatibility.check', payload)
      if (disposed || token !== generation) {
        if (initial?.task_id && !terminalStates.has(initial.state)) {
          await bridge.request('task.cancel', { task_id: initial.task_id }).catch(() => {})
        }
        return
      }
      update(initial)
      if (!settled) watcher = bridge.watchTask(initial.task_id, update, () => {
        if (!disposed && token === generation) {
          settled = true
          const failedWatcher = watcher; watcher = null
          void failedWatcher?.dispose()
          failure('检查连接中断，请重新检查。')
        }
      })
    } catch (error) {
      if (!disposed && token === generation) {
        const messages = {
          COMPATIBILITY_STALE: '方案或包库已改变，请刷新后重新检查。',
          COMPATIBILITY_UNRESOLVED: '方案中有缺失或校验失败的 Mod，请导入对应版本后重试。',
          COMPATIBILITY_PROFILE_NOT_FOUND: '找不到此方案，请刷新并重新选择。',
          COMPATIBILITY_LIMIT: '此方案超过检查范围，请减少所选 Mod 后重试。',
        }
        failure(messages[error?.code] || error?.message || '无法开始检查，请刷新方案后重试。')
      }
    }
  }
  return {
    setProfile, check,
    async cancel() { try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求未完成，请重试。' } },
    async dispose() { disposed = true; generation++; await watcher?.cancel().catch(() => {}); await watcher?.dispose() },
  }
}
