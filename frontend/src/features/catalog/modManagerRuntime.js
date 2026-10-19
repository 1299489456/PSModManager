import { createRequestId } from '../../app/requestId.js'
import { terminalStates } from '../../app/bridge/reducer.js'

const states = new Set(['not_installed', 'enabled', 'disabled', 'drifted', 'unknown'])
const expectedState = { install: 'enabled', enable: 'enabled', disable: 'disabled', uninstall: 'not_installed' }
const actionLabels = { install: '安装', enable: '启用', disable: '停用', uninstall: '卸载' }

export function modStateText(value) {
  return { not_installed: '未安装', enabled: '已启用', disabled: '已停用', drifted: '文件已改变', unknown: '状态待确认' }[value] || '状态待确认'
}

export function modActions(value) {
  return value === 'not_installed' ? ['install'] : value === 'enabled' ? ['disable', 'uninstall'] : value === 'disabled' ? ['enable', 'uninstall'] : []
}

export const modActionText = value => actionLabels[value] || value

function taskError(snapshot) {
  const error = new Error(snapshot.data?.message || (snapshot.state === 'cancelled' ? '操作已取消，已重新核对文件状态。' : '操作未完成，请刷新后重试。'))
  error.code = snapshot.data?.error_code || (snapshot.state === 'cancelled' ? 'CANCELLED' : 'TASK_FAILED')
  return error
}

function readPage(page, offset) {
  if (!page || !Array.isArray(page.items) || page.items.length > 20 || page.offset !== offset
    || !Number.isSafeInteger(page.total) || page.total < page.items.length
    || !(page.next_offset === null || Number.isSafeInteger(page.next_offset) && page.next_offset > offset)
    || page.items.some(item => !item || typeof item.release_id !== 'string' || !item.release_id
      || typeof item.manifest_hash !== 'string' || !/^[a-f0-9]{64}$/.test(item.manifest_hash)
      || typeof item.display_name !== 'string' || !states.has(item.state)
      || !(item.version_raw === null || typeof item.version_raw === 'string')
      || !Number.isSafeInteger(item.file_count) || item.file_count < 0 || typeof item.message !== 'string')) {
    throw new Error('Mod 列表返回不完整，请刷新后重试。')
  }
  return page
}

export function createModManagerRuntime(bridge, state = {}) {
  let disposed = false, generation = 0, mainEpoch = 0, detailEpoch = 0, actionWatcher = null, pageRead = null
  const pending = new Set()
  const empty = () => ({ items: [], total: 0, offset: 0, nextOffset: null, loading: false, busy: false,
    stale: false, task: null, actionReleaseId: null, action: null, error: '', errorCode: '', notice: '',
    detail: null, detailBusy: false, query: '', focusReleaseId: null })
  Object.assign(state, { installationId: null, ...empty() })
  const mainCurrent = (token, id, scope) => !disposed && scope === generation && token === mainEpoch && id === state.installationId
  const detailCurrent = (token, id, scope) => !disposed && scope === generation && token === detailEpoch && id === state.installationId

  function invalidatePending() {
    for (const job of [...pending]) job.invalidate()
    actionWatcher = null
  }

  async function call(command, payload, valid, onSnapshot = () => {}) {
    const initial = await bridge.request(command, payload)
    if (!valid()) return null
    if (!initial?.task_id) {
      if (initial?.error_code) throw taskError({ state: 'failed', data: initial })
      return initial
    }
    let settled = false, watcher = null
    return new Promise((resolve, reject) => {
      const job = {
        valid,
        invalidate() { if (settled) return; settled = true; pending.delete(job); void watcher?.dispose(); resolve(null) },
      }
      pending.add(job)
      const settle = (result, error) => {
        if (settled) return
        settled = true
        pending.delete(job)
        void watcher?.dispose()
        if (actionWatcher === watcher) actionWatcher = null
        if (error) reject(error)
        else resolve(result)
      }
      const update = snapshot => {
        if (settled) return
        if (!valid()) { settle(null); return }
        if (snapshot?.task_id !== initial.task_id) return
        onSnapshot(snapshot)
        if (!terminalStates.has(snapshot.state)) return
        if (snapshot.state === 'succeeded' && !snapshot.data?.error_code) settle(snapshot.data)
        else settle(null, taskError(snapshot))
      }
      update(initial)
      if (!settled) {
        watcher = bridge.watchTask(initial.task_id, update, error => settle(null, error))
        if (command === 'mod.action') actionWatcher = watcher
        // A synchronous watcher can deliver its terminal snapshot during setup.
        if (settled) { if (actionWatcher === watcher) actionWatcher = null; void watcher.dispose() }
      }
    })
  }

  async function loadPage(offset, valid) {
    state.loading = true
    try {
      const reading = call('mod.list', { installation_id: state.installationId, offset, limit: 20 }, valid)
      pageRead = reading
      const page = await reading
      if (!valid()) return false
      const verified = readPage(page, offset)
      Object.assign(state, { items: verified.items, total: verified.total, offset, nextOffset: verified.next_offset, stale: false })
      return true
    } finally { if (valid()) state.loading = false }
  }

  async function refresh(offset = state.offset) {
    if (disposed || !state.installationId || state.busy || state.loading || !Number.isSafeInteger(offset) || offset < 0) return false
    const token = ++mainEpoch, id = state.installationId, scope = generation
    const valid = () => mainCurrent(token, id, scope)
    state.error = ''; state.errorCode = ''; state.notice = ''
    try { return await loadPage(offset, valid) }
    catch (error) {
      if (valid()) { state.stale = true; state.error = error.message || '无法读取 Mod 状态，请刷新后重试。'; state.errorCode = error.code || '' }
      return false
    }
  }

  async function setInstallation(id) {
    const next = id || null
    if (disposed || state.installationId === next) return false
    generation += 1; mainEpoch += 1; detailEpoch += 1
    invalidatePending()
    Object.assign(state, { installationId: next, ...empty() })
    return refresh(0)
  }

  function canAct(item, action) {
    return !disposed && Boolean(state.installationId) && !state.busy && !state.loading && !state.stale
      && state.items.some(row => row.release_id === item?.release_id && row.manifest_hash === item.manifest_hash && row.state === item.state)
      && modActions(item?.state).includes(action)
  }

  async function perform(item, action, idempotencyKey = createRequestId()) {
    if (!canAct(item, action)) return false
    const token = ++mainEpoch, id = state.installationId, scope = generation
    const valid = () => mainCurrent(token, id, scope)
    Object.assign(state, { busy: true, actionReleaseId: item.release_id, action, task: null, error: '', errorCode: '', notice: '' })
    try {
      const result = await call('mod.action', { installation_id: id, release_id: item.release_id,
        manifest_hash: item.manifest_hash, action, idempotency_key: idempotencyKey }, valid, snapshot => { if (valid()) state.task = snapshot })
      if (!valid()) return false
      if (result?.completed !== true || result.state !== expectedState[action]) throw new Error('操作结果尚未确认，请刷新后核对文件状态。')
      await loadPage(state.offset, valid)
      if (!valid()) return false
      const actual = state.items.find(row => row.release_id === item.release_id)
      if (actual?.state !== expectedState[action]) throw new Error('操作后文件状态与结果不一致，请刷新并查看此 Mod 的提示。')
      state.notice = `${actionLabels[action]}完成。`
      return true
    } catch (error) {
      if (!valid()) return false
      state.error = error.message || `${actionLabels[action]}未完成，请刷新后重试。`
      state.errorCode = error.code || ''
      state.notice = ''
      try { await loadPage(state.offset, valid) }
      catch { if (valid()) { state.stale = true; state.error += ' 暂无法刷新文件状态，请稍后重试。' } }
      return false
    } finally { if (valid()) { state.busy = false; state.actionReleaseId = null; state.action = null } }
  }

  async function showDetail(item, offset = 0) {
    if (disposed || !state.installationId || !item || state.busy || state.detailBusy) return false
    const token = ++detailEpoch, id = state.installationId, scope = generation
    const valid = () => detailCurrent(token, id, scope)
    state.detailBusy = true
    try {
      const detail = await call('package.detail', { release_id: item.release_id, manifest_hash: item.manifest_hash, offset, limit: 20 }, valid)
      if (!valid()) return false
      if (!detail || detail.release_id !== item.release_id || detail.manifest_hash !== item.manifest_hash || !Array.isArray(detail.files)) throw new Error('无法读取此版本的文件详情。')
      state.detail = detail
      return true
    } catch (error) { if (valid()) { state.error = error.message || '无法读取 Mod 文件详情。'; state.errorCode = error.code || '' }; return false }
    finally { if (valid()) state.detailBusy = false }
  }

  function closeDetail() {
    detailEpoch += 1; state.detail = null; state.detailBusy = false
    for (const job of [...pending]) if (!job.valid()) job.invalidate()
  }

  async function reveal(releaseId) {
    if (disposed || !state.installationId || state.busy) return false
    const requestedId = state.installationId, requestedScope = generation
    if (state.loading) { try { await pageRead } catch {} }
    if (disposed || state.busy || state.loading || requestedId !== state.installationId || requestedScope !== generation) return false
    state.query = ''; state.focusReleaseId = releaseId || null
    if (!await refresh(0) || !releaseId) return false
    // Imports are bounded; locate a release on later pages without changing the backend sort.
    const id = state.installationId, scope = generation
    while (!disposed && scope === generation && id === state.installationId && !state.items.some(item => item.release_id === releaseId) && state.nextOffset !== null) {
      if (!await refresh(state.nextOffset)) return false
    }
    return state.items.some(item => item.release_id === releaseId)
  }

  async function cancel() {
    if (!state.busy || !actionWatcher) return false
    try { await actionWatcher.cancel(); return true }
    catch { if (!disposed) state.error = '取消请求未完成，操作仍在处理中。'; return false }
  }

  function dispose() { if (disposed) return; disposed = true; generation += 1; invalidatePending() }
  return { refresh, setInstallation, canAct, perform, showDetail, closeDetail, reveal, cancel, dispose }
}
