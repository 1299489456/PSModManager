import { terminalStates } from '../../app/bridge/reducer.js'

export function issueText(issue) {
  const raw = typeof issue === 'string' ? issue : String(issue?.code || issue?.message || '')
  const known = [
    ['installation_directory_missing', '游戏目录不存在，请重新选择。'],
    ['data_root_conflict', '游戏目录与管理器数据目录重叠，请选择独立的游戏安装。'],
    ['loader_missing', '未找到 MelonLoader，请检查安装是否完整。'],
    ['reparse', '安装包含重定向目录或链接，无法安全校验。'],
    ['hardlinked', '安装包含硬链接或非常规文件，无法安全校验。'],
    ['read_size', '文件大小超出本次校验限制。'],
    ['read_time', '读取文件超时，请检查磁盘状态后重试。'],
    ['file_drift', '文件在校验期间发生变化，请重新发现安装。'],
    ['root_identity_drift', '安装目录在校验期间发生变化，请重新发现安装。'],
    ['invalid_pe', '游戏程序或加载器文件格式无效，可能已损坏。'],
    ['invalid_metadata', '游戏元数据格式无效，可能已损坏。'],
    ['empty_binary', '安装包含空的程序文件，请检查安装完整性。'],
    ['global-metadata', '游戏元数据文件缺失或无法读取。'],
    ['GameAssembly', '游戏程序集缺失或无法读取。'],
    ['Probably Stolen.exe', '游戏主程序缺失或无法读取。'],
    ['Probably Stolen_Data', '游戏数据目录缺失或无法读取。'],
    ['MelonLoader', '加载器文件无法读取，请检查安装完整性。'],
    ['manifest_', 'Steam 安装清单无效，请检查所选安装。'],
  ]
  return known.find(([key]) => raw.includes(key))?.[1] || '无法读取游戏目录，请检查路径和目录权限。'
}

export function canSelect(candidate) {
  return candidate?.valid === true
}

export function createInstallationController(bridge, state, onSelected = () => {}) {
  let watcher = null, disposed = false, operation = 0, notifiedInstallationId = null
  Object.assign(state, { installations: [], selectedId: null, candidates: [], busy: false, task: null, error: '', notice: '' })
  async function refresh() {
    const data = await bridge.request('installation.list')
    if (!disposed) {
      state.installations = data.installations; state.selectedId = data.selected_installation_id
      const selected = data.installations.find(item => item.installation_id === state.selectedId)
      if (selected && selected.installation_id !== notifiedInstallationId) { notifiedInstallationId = selected.installation_id; onSelected(selected) }
    }
  }
  async function perform(command, payload) {
    if (state.busy || disposed) return
    const token = ++operation
    state.busy = true; state.error = ''; state.notice = ''; state.task = null
    await watcher?.dispose(); watcher = null
    try {
      const initial = await bridge.request(command, payload)
      if (disposed || token !== operation) return
      let settled = false
      const update = snapshot => {
        if (disposed || token !== operation || settled) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        settled = true; state.busy = false
        if (snapshot.state === 'succeeded') {
          if (command === 'installation.discover' || command === 'installation.auto_discover') {
            state.candidates = snapshot.data?.candidates ?? []
            state.notice = state.candidates.length ? '' : '未发现游戏安装，可以手动选择游戏目录。'
          }
          if (snapshot.data?.error_code === 'INSTALLATION_CHANGED') {
            state.error = '所选游戏目录已移动或无法读取，请重新选择目录。'
          } else if (snapshot.data?.installation) {
            state.selectedId = snapshot.data.installation.installation_id
            notifiedInstallationId = state.selectedId
            onSelected(snapshot.data.installation)
            void refresh().catch(() => { if (!disposed) state.error = '安装已选定，但列表刷新失败。请重新打开安装选择器。' })
          } else if (command !== 'installation.discover' && command !== 'installation.auto_discover') state.error = '无法读取所选安装。'
        } else state.error = snapshot.state === 'cancelled' ? '操作已取消。' : '无法读取游戏目录，请检查路径和目录权限。'
      }
      update(initial)
      if (!settled) watcher = bridge.watchTask(initial.task_id, update, () => {
        if (!disposed && token === operation) { state.busy = false; state.error = '进度连接中断，请重试。' }
      })
    } catch {
      if (!disposed && token === operation) { state.busy = false; state.error = '无法完成安装操作，请重试。' }
    }
  }
  return {
    async initialize() {
      try { await refresh() } catch { if (!disposed) state.error = '无法读取已保存的安装。' }
      if (!disposed && !state.selectedId) await perform('installation.auto_discover', {})
    },
    async refresh() { try { await refresh() } catch { if (!disposed) state.error = '无法读取已保存的安装。' } },
    discoverAutomatic() { return perform('installation.auto_discover', {}) },
    async choose(kind) {
      if (state.busy || disposed) return
      state.busy = true; state.error = ''; state.notice = ''
      try {
        const result = await bridge.request('installation.choose', { kind })
        if (disposed) return
        state.busy = false
        if (result.source_handle) await perform('installation.discover', { source_handles: [result.source_handle] })
        else state.notice = '已取消目录选择。'
      } catch { if (!disposed) { state.busy = false; state.error = '无法打开目录选择器。' } }
    },
    select(candidate) { if (canSelect(candidate)) return perform('installation.select', { candidate_id: candidate.candidate_id }) },
    use(id) { return perform('installation.use', { installation_id: id }) },
    async cancel() { try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求失败，请重试。' } },
    async dispose() { disposed = true; operation++; await watcher?.dispose() },
  }
}
