const launchStates = new Set(['not_started', 'running', 'exited', 'failed', 'unknown'])

function readStatus(result, installationId) {
  if (!result || result.installation_id !== installationId || !launchStates.has(result.state)
    || !(result.pid === null || Number.isSafeInteger(result.pid) && result.pid > 0)
    || !(result.exit_code === null || Number.isSafeInteger(result.exit_code))
    || !(result.started_at === null || typeof result.started_at === 'string' && Number.isFinite(Date.parse(result.started_at)))
    || typeof result.message !== 'string'
    || result.already_running !== undefined && typeof result.already_running !== 'boolean') {
    throw new Error('Invalid game launch response')
  }
  return { ...result }
}

function errorMessage(error, launching) {
  if (error?.code && typeof error.message === 'string' && error.message) return error.message
  return launching ? '未能启动游戏，请检查游戏文件和执行权限后重试。' : '暂无法确认游戏进程状态，请刷新重试。'
}

export function gameLaunchStatusText(state) {
  if (state.busy) return state.phase === 'preparing' ? '正在保存模组' : '正在启动'
  const snapshot = state.snapshot
  if (!snapshot || snapshot.state === 'not_started') return '未启动'
  if (snapshot.state === 'running') return snapshot.already_running ? '已在运行' : '已启动'
  if (snapshot.state === 'exited') return snapshot.exit_code === null ? '已退出' : `已退出（退出码 ${snapshot.exit_code}）`
  return snapshot.state === 'failed' ? state.error ? '启动失败' : '未启动' : '暂无法确认'
}

export function createGameLaunchRuntime(bridge, state = {}, {
  pollInterval = 2000,
  idlePollInterval = 5000,
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = timer => clearTimeout(timer),
} = {}) {
  let sequence = 0, timer = null, disposed = false
  Object.assign(state, { installationId: null, busy: false, phase:null, refreshing: false, snapshot: null, error: '', errorCode:'' })
  const current = (token, id) => !disposed && token === sequence && id === state.installationId

  function stopPolling() {
    if (timer !== null) cancel(timer)
    timer = null
  }

  function pollStatus() {
    stopPolling()
    if (!disposed && state.installationId && !state.busy) {
      const token = sequence, id = state.installationId
      timer = schedule(() => {
        if (!current(token, id)) return
        timer = null
        return refresh({ clearError:false })
      }, state.snapshot?.state === 'running' ? pollInterval : idlePollInterval)
    }
  }

  function clearError() { state.error=''; state.errorCode='' }
  async function refresh({clearError:clear=true}={}) {
    if (disposed || !state.installationId || state.busy || state.refreshing) return false
    stopPolling()
    const token = ++sequence, id = state.installationId
    state.refreshing = true
    if(clear)clearError()
    try {
      const result = readStatus(await bridge.request('game.status', { installation_id: id }), id)
      if (!current(token, id)) return false
      state.snapshot = result
      if(result.state==='running')clearError()
      return true
    } catch (error) {
      if (current(token, id)) {
        state.snapshot = { installation_id: id, state: 'unknown', pid: null, exit_code: null, started_at: null, message: '' }
        if(!state.error)state.error = errorMessage(error, false)
      }
      return false
    } finally {
      if (current(token, id)) {
        state.refreshing = false
        pollStatus()
      }
    }
  }

  function canLaunch() {
    // A periodic status poll keeps the last known state usable; only the first read gates launching.
    return !disposed && Boolean(state.installationId) && !state.busy && !(state.refreshing && !state.snapshot) && ['not_started','exited','failed'].includes(state.snapshot?.state)
  }

  async function launch(prepare) {
    if (!canLaunch()) return false
    stopPolling()
    const token = ++sequence, id = state.installationId
    // Launching supersedes an in-flight poll; its late reply is ignored by sequence.
    state.refreshing = false
    state.busy = true
    state.phase = prepare ? 'preparing' : 'launching'
    clearError()
    try {
      if(prepare && await prepare() !== true)return false
      if(!current(token,id))return false
      state.phase = 'launching'
      const result = readStatus(await bridge.request('game.launch', { installation_id: id }), id)
      if (!current(token, id)) return false
      state.snapshot = result
      if(result.state==='failed')state.error=result.message||'游戏启动失败，请查看日志。'
      return result.state==='running'
    } catch (error) {
      if (current(token, id)) {
        state.snapshot = { installation_id: id, state: 'failed', pid: null, exit_code: null, started_at: null, message: '' }
        state.error = errorMessage(error, true)
        state.errorCode = error?.code || ''
      }
      return false
    } finally {
      if (current(token, id)) {
        state.busy = false
        state.phase = null
        pollStatus()
      }
    }
  }

  function setInstallation(id) {
    if (disposed || state.installationId === (id || null)) return false
    sequence += 1
    stopPolling()
    Object.assign(state, { installationId: id || null, busy: false, phase:null, refreshing: false, snapshot: null, error: '', errorCode:'' })
    return refresh()
  }

  function dispose() {
    disposed = true
    sequence += 1
    stopPolling()
  }

  return { setInstallation, canLaunch, launch, refresh, clearError, dispose }
}
