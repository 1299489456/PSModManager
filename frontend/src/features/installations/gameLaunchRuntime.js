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
  if (state.busy) return '正在启动'
  const snapshot = state.snapshot
  if (!snapshot || snapshot.state === 'not_started') return '未启动'
  if (snapshot.state === 'running') return snapshot.already_running ? '已在运行' : '已启动'
  if (snapshot.state === 'exited') return snapshot.exit_code === null ? '已退出' : `已退出（退出码 ${snapshot.exit_code}）`
  return snapshot.state === 'failed' ? '启动失败' : '暂无法确认'
}

export function createGameLaunchRuntime(bridge, state = {}, {
  pollInterval = 2000,
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = timer => clearTimeout(timer),
} = {}) {
  let sequence = 0, timer = null, disposed = false
  Object.assign(state, { installationId: null, busy: false, refreshing: false, snapshot: null, error: '' })
  const current = (token, id) => !disposed && token === sequence && id === state.installationId

  function stopPolling() {
    if (timer !== null) cancel(timer)
    timer = null
  }

  function pollIfRunning() {
    stopPolling()
    if (!disposed && state.snapshot?.state === 'running') {
      const token = sequence, id = state.installationId
      timer = schedule(() => {
        if (!current(token, id)) return
        timer = null
        return refresh()
      }, pollInterval)
    }
  }

  async function refresh() {
    if (disposed || !state.installationId || state.busy || state.refreshing) return false
    stopPolling()
    const token = ++sequence, id = state.installationId
    state.refreshing = true
    state.error = ''
    try {
      const result = readStatus(await bridge.request('game.status', { installation_id: id }), id)
      if (!current(token, id)) return false
      state.snapshot = result
      return true
    } catch (error) {
      if (current(token, id)) {
        state.snapshot = { installation_id: id, state: 'unknown', pid: null, exit_code: null, started_at: null, message: '' }
        state.error = errorMessage(error, false)
      }
      return false
    } finally {
      if (current(token, id)) {
        state.refreshing = false
        pollIfRunning()
      }
    }
  }

  function canLaunch() {
    return !disposed && Boolean(state.installationId) && !state.busy && !state.refreshing && state.snapshot?.state !== 'running'
  }

  async function launch() {
    if (!canLaunch()) return false
    stopPolling()
    const token = ++sequence, id = state.installationId
    state.busy = true
    state.error = ''
    try {
      const result = readStatus(await bridge.request('game.launch', { installation_id: id }), id)
      if (!current(token, id)) return false
      state.snapshot = result
      return true
    } catch (error) {
      if (current(token, id)) {
        state.snapshot = { installation_id: id, state: 'failed', pid: null, exit_code: null, started_at: null, message: '' }
        state.error = errorMessage(error, true)
      }
      return false
    } finally {
      if (current(token, id)) {
        state.busy = false
        pollIfRunning()
      }
    }
  }

  function setInstallation(id) {
    if (disposed || state.installationId === (id || null)) return false
    sequence += 1
    stopPolling()
    Object.assign(state, { installationId: id || null, busy: false, refreshing: false, snapshot: null, error: '' })
    return refresh()
  }

  function dispose() {
    disposed = true
    sequence += 1
    stopPolling()
  }

  return { setInstallation, canLaunch, launch, refresh, dispose }
}
