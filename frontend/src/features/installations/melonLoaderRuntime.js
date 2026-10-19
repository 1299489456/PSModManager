import { terminalStates } from '../../app/bridge/reducer.js'

const loaderStates = new Set(['not_installed', 'incomplete', 'installed', 'unreadable'])
const checkStates = new Set(['ok', 'missing', 'unknown', 'disabled'])

function readStatus(value) {
  if (!value || !loaderStates.has(value.status) || typeof value.ready !== 'boolean'
    || !(value.version_raw === null || typeof value.version_raw === 'string')
    || typeof value.recommended_version !== 'string' || typeof value.message !== 'string'
    || typeof value.can_install !== 'boolean' || typeof value.install_label !== 'string'
    || !Array.isArray(value.checks) || value.checks.some(check => !check || typeof check.id !== 'string'
      || typeof check.label !== 'string' || !checkStates.has(check.status) || typeof check.detail !== 'string')) {
    throw new Error('未能读到完整的 MelonLoader 状态，请重新检测。')
  }
  return value
}

function taskError(snapshot) {
  return Object.assign(new Error(snapshot.data?.message || (snapshot.state === 'cancelled'
    ? '已取消，请重新检测当前安装状态。' : 'MelonLoader 操作未完成，请重试。')),
  { code: snapshot.data?.error_code || (snapshot.state === 'cancelled' ? 'CANCELLED' : 'TASK_FAILED') })
}

export function melonLoaderStatusText(state) {
  if (state.refreshing) return '正在检测 MelonLoader…'
  if (!state.snapshot) return '尚未确认 MelonLoader 状态'
  const value = state.snapshot
  const version = value.version_raw ? ` ${value.version_raw.split('+')[0]}` : ''
  if (value.status === 'installed') return value.ready ? `MelonLoader${version} · 环境已就绪` : `MelonLoader${version} · 环境待处理`
  return { not_installed: '未安装 MelonLoader', incomplete: `MelonLoader${version} · 安装不完整`, unreadable: '无法读取 MelonLoader' }[value.status]
}

export const melonLoaderCheckText = value => ({ ok: '正常', missing: '缺少', unknown: '待确认', disabled: '已关闭' })[value] || '待确认'

export function melonLoaderProgress(task) {
  const downloading = task?.stage === 'download' || task?.stage === 'downloading'
  if (downloading) {
    const received = task.data?.bytes_received, total = task.data?.bytes_total
    const hasReceived = Number.isSafeInteger(received) && received >= 0
    const hasTotal = Number.isSafeInteger(total) && total > 0
    const mb = bytes => `${(bytes / 1_000_000).toFixed(1)} MB`
    return {
      label: hasReceived ? hasTotal ? `${mb(received)} / ${mb(total)}` : mb(received) : '',
      value: hasReceived && hasTotal ? received : undefined,
      max: hasTotal ? total : 1,
    }
  }
  const hasCount = Number.isSafeInteger(task?.completed) && Number.isSafeInteger(task?.total) && task.total > 0
  return { label: hasCount ? `${task.completed} / ${task.total} 个文件` : '', value: hasCount ? task.completed : undefined, max: hasCount ? task.total : 1 }
}

export function createMelonLoaderRuntime(bridge, state = {}) {
  let generation = 0, disposed = false, active = null
  const empty = () => ({ snapshot: null, busy: false, refreshing: false, choosing: false, task: null, error: '', notice: '', operation: '' })
  Object.assign(state, { installationId: null, ...empty() })
  const current = (token, id) => !disposed && token === generation && id === state.installationId
  const working = () => state.busy || state.refreshing || state.choosing

  function detach() {
    active?.invalidate()
    active = null
  }

  async function call(command, payload, token, id) {
    const initial = await bridge.request(command, payload)
    if (!current(token, id)) return null
    if (!initial?.task_id) {
      if (initial?.error_code) throw taskError({ state: 'failed', data: initial })
      return initial
    }
    let settled = false, watcher = null
    return new Promise((resolve, reject) => {
      const job = {
        cancelRequested: false,
        async cancel() {
          job.cancelRequested = true
          await watcher?.cancel()
        },
        invalidate() { settle(null) },
      }
      function settle(result, error) {
        if (settled) return
        settled = true
        if (active === job) active = null
        void watcher?.dispose()
        if (error) reject(error)
        else resolve(result)
      }
      const update = snapshot => {
        if (settled) return
        if (!current(token, id)) { settle(null); return }
        if (snapshot?.task_id !== initial.task_id) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        if (snapshot.state === 'succeeded' && !snapshot.data?.error_code) settle(snapshot.data)
        else settle(null, taskError(snapshot))
      }
      active = job
      update(initial)
      if (!settled) {
        try {
          watcher = bridge.watchTask(initial.task_id, update, error => settle(null, error))
          if (settled) void watcher.dispose()
          else if (job.cancelRequested) void watcher.cancel().catch(error => settle(null, error))
        } catch (error) { settle(null, error) }
      }
    })
  }

  async function refresh() {
    if (disposed || !state.installationId || working()) return false
    const token = ++generation, id = state.installationId
    Object.assign(state, { refreshing: true, task: null, error: '', notice: '', operation: 'status' })
    try {
      const result = await call('loader.status', { installation_id: id }, token, id)
      if (!current(token, id)) return false
      state.snapshot = readStatus(result)
      return true
    } catch (error) {
      if (current(token, id)) { state.snapshot = null; state.error = error.message || '未能检测 MelonLoader，请重试。' }
      return false
    } finally {
      if (current(token, id)) { state.refreshing = false; state.operation = '' }
    }
  }

  function canInstall() {
    return !disposed && Boolean(state.installationId) && !working() && state.snapshot?.can_install === true
  }

  function canRepair() {
    return !disposed && Boolean(state.installationId) && !working() && state.snapshot?.generation_dependencies?.can_repair === true
  }

  async function repair() {
    if (!canRepair()) return false
    const token = ++generation, id = state.installationId
    Object.assign(state, { busy: true, task: null, error: '', notice: '', operation: 'repair' })
    try {
      const result = await call('loader.repair', { installation_id: id }, token, id)
      if (!current(token, id)) return false
      if (!result || typeof result.repaired !== 'boolean' || !Number.isSafeInteger(result.files_written)) throw new Error('修复结果尚未确认，请重新检测。')
      state.snapshot = readStatus(result.status)
      state.notice = result.warning || (state.snapshot.generation_dependencies?.ready
        ? '生成依赖已就绪，修复过程无需联网。' : state.snapshot.generation_dependencies?.message || '请检查生成依赖状态。')
      return state.snapshot.generation_dependencies?.ready === true
    } catch (error) {
      if (current(token, id)) state.error = error.message || '离线修复未完成，请重试。'
      return false
    } finally {
      if (current(token, id)) { state.busy = false; state.operation = '' }
    }
  }

  async function install(local = false) {
    if (!canInstall()) return false
    const token = ++generation, id = state.installationId
    Object.assign(state, { busy: true, task: null, error: '', notice: '', operation: local ? 'local' : 'online' })
    try {
      let sourceHandle
      if (local) {
        state.choosing = true
        const result = await bridge.request('loader.choose', { installation_id: id })
        if (!current(token, id)) return false
        state.choosing = false
        if (result?.source_handle === null) return false
        if (typeof result?.source_handle !== 'string' || !result.source_handle) throw new Error('未能选择 MelonLoader ZIP，请重试。')
        sourceHandle = result.source_handle
      }
      const result = await call(local ? 'loader.install_local' : 'loader.install', {
        installation_id: id, ...(local ? { source_handle: sourceHandle } : {}),
      }, token, id)
      if (!current(token, id)) return false
      if (result?.installed !== true || !Number.isSafeInteger(result.files_written) || result.files_written < 1) throw new Error('安装结果尚未确认，请重新检测。')
      state.snapshot = readStatus(result.status)
      state.notice = `MelonLoader 已安装。${typeof result.warning === 'string' && result.warning ? ` ${result.warning}` : ''}`
      return true
    } catch (error) {
      if (current(token, id)) state.error = error.message || 'MelonLoader 安装未完成，请重试。'
      return false
    } finally {
      if (current(token, id)) { state.busy = false; state.choosing = false; state.operation = '' }
    }
  }

  async function cancel() {
    if (!working() || !active || terminalStates.has(state.task?.state)) return false
    try { await active.cancel(); return true }
    catch { if (!disposed) state.error = '取消请求未完成，请等待当前操作结束。'; return false }
  }

  function setInstallation(id) {
    const next = id || null
    if (disposed || state.installationId === next) return false
    generation += 1
    detach()
    Object.assign(state, { installationId: next, ...empty() })
    return refresh()
  }

  function dispose() {
    if (disposed) return
    disposed = true
    generation += 1
    detach()
  }

  return { setInstallation, refresh, canInstall, canRepair, repair, install: () => install(false), installLocal: () => install(true), cancel, dispose }
}
