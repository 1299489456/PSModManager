import { createRequestId } from '../../app/requestId.js'
import { terminalStates } from '../../app/bridge/reducer.js'

const same = (a, b) => a.length === b.length && a.every((id, i) => id === b[i])
function taskError(value) {
  const code = value.data?.error_code || value.message_code
  const message = value.data?.message || (value.state === 'cancelled' ? '操作已取消，未保存的选择仍保留。' : '操作未完成，请重试。')
  return new Error(code ? `${message}（${code}）` : message)
}
export const workspaceStateText = value => ({ enabled: '已启用', disabled: '已停用', not_installed: '未安装', drifted: '文件已改变', unknown: '需要检查' })[value] || '已发现'

export function fileTree(files) {
  const root = { children: [] }
  for (const file of files) {
    const parts = file.relative_path.replaceAll('\\', '/').split('/').filter(Boolean)
    let parent = root, path = ''
    parts.forEach((name, index) => {
      path += (path ? '/' : '') + name
      let node = parent.children.find(item => item.name === name)
      if (!node) { node = { name, path, children: [], file: index === parts.length - 1 ? file : null }; parent.children.push(node) }
      parent = node
    })
  }
  const sort = node => { node.children.sort((a, b) => Number(Boolean(a.file)) - Number(Boolean(b.file)) || a.name.localeCompare(b.name)); node.children.forEach(sort) }
  sort(root)
  return root.children
}

export function createModWorkspaceRuntime(bridge, state = {}) {
  const empty = () => ({ items: [], enabledIds: [], baseEnabled: [], snapshotId: null, loaded: false, loading: false, busy: false,
    dirty: false, task: null, error: '', notice: '', focusedId: null, selectedIds: [], detail: null, detailBusy: false,
    query: '', undo: [], redo: [], orderMessage: '' })
  Object.assign(state, { installationId: null, ...empty() })
  let disposed = false, scope = 0, detailSequence = 0
  const jobs = new Set(), drafts = new Map()
  const current = token => !disposed && token === scope

  async function call(command, payload, token, progress = false) {
    const initial = await bridge.request(command, payload)
    if (!current(token)) return null
    if (!initial?.task_id) {
      if (initial?.error_code) throw taskError({ data: initial })
      return initial
    }
    return new Promise((resolve, reject) => {
      let watcher, settled = false
      const job = { stop() { finish(null) }, cancel() { return watcher?.cancel() } }
      function finish(result, error) {
        if (settled) return
        settled = true; jobs.delete(job); void watcher?.dispose()
        if (error) reject(error); else resolve(result)
      }
      const update = value => {
        if (!current(token)) { finish(null); return }
        if (progress) state.task = value
        if (!terminalStates.has(value.state)) return
        if (value.state === 'succeeded' && !value.data?.error_code) finish(value.data)
        else finish(null, taskError(value))
      }
      jobs.add(job); update(initial)
      if (!settled) { watcher = bridge.watchTask(initial.task_id, update, error => finish(null, error)); if (settled) void watcher.dispose() }
    })
  }

  function accept(workspace) {
    if (!workspace || !Array.isArray(workspace.items) || !Array.isArray(workspace.enabled_ids) || typeof workspace.snapshot_id !== 'string'
      || workspace.items.some(item => typeof item.id !== 'string' || typeof item.display_name !== 'string')
      || workspace.enabled_ids.some(id => !workspace.items.some(item => item.id === id))) throw new Error('Mod 列表不完整，请刷新重试。')
    Object.assign(state, { items: workspace.items, enabledIds: [...workspace.enabled_ids], baseEnabled: [...workspace.enabled_ids],
      snapshotId: workspace.snapshot_id, loaded: true, dirty: false, undo: [], redo: [], orderMessage: workspace.order_message || '' })
    state.selectedIds = state.selectedIds.filter(id => state.items.some(item => item.id === id))
    if (!state.items.some(item => item.id === state.focusedId)) { state.focusedId = null; state.detail = null }
  }

  async function refresh() {
    if (disposed || !state.installationId || state.loading || state.busy || state.dirty) return false
    const token = scope
    state.loading = true; state.error = ''
    try {
      const result = await call('mod.workspace', { installation_id: state.installationId }, token)
      if (!current(token)) return false
      accept(result)
      return true
    } catch (error) { if (current(token)) state.error = error.message; return false }
    finally { if (current(token)) state.loading = false }
  }

  async function setInstallation(id) {
    if (disposed || id === state.installationId) return false
    if (state.installationId && state.dirty) drafts.set(state.installationId, { ...state, loading: false, busy: false, detailBusy: false, task: null })
    ++scope; ++detailSequence; for (const job of [...jobs]) job.stop()
    Object.assign(state, { installationId: id || null, ...empty() })
    if (drafts.has(id)) { Object.assign(state, drafts.get(id), { notice: '已恢复此游戏尚未保存的选择。' }); return true }
    return refresh()
  }

  function edit(ids) {
    if (!state.loaded || state.loading || state.busy || same(ids, state.enabledIds)) return false
    state.undo.push([...state.enabledIds]); if (state.undo.length > 100) state.undo.shift()
    state.redo = []; state.enabledIds = ids; state.dirty = !same(ids, state.baseEnabled); state.notice = ''; state.error = ''
    return true
  }

  function move(ids, enabled, beforeId = null) {
    const unique = [...new Set(ids)].filter(id => state.items.some(item => item.id === id && item.manageable !== false && !['unknown', 'drifted'].includes(item.state)))
    if (!unique.length) return false
    const moving = new Set(unique), result = state.enabledIds.filter(id => !moving.has(id))
    if (enabled) {
      const packages = new Set(unique.map(id => state.items.find(item => item.id === id)?.package_id).filter(Boolean))
      for (let i = result.length - 1; i >= 0; i--) {
        const previous = state.items.find(item => item.id === result[i])
        if (previous?.package_id && packages.has(previous.package_id)) result.splice(i, 1)
      }
      const index = beforeId === null ? result.length : result.indexOf(beforeId)
      if (beforeId !== null && index < 0) return false
      result.splice(index, 0, ...unique)
    }
    const changed = edit(result)
    if (changed && enabled && state.baseEnabled.some(id => !result.includes(id) && !moving.has(id)))
      state.notice = '已改选此版本；保存时会停用同一 Mod 的旧版本，旧方案的版本引用保留。'
    return changed
  }

  function history(from, to) {
    if (state.busy || state.loading || !state[from].length) return
    state[to].push([...state.enabledIds]); state.enabledIds = state[from].pop()
    state.dirty = !same(state.enabledIds, state.baseEnabled); state.notice = ''
  }

  async function select(id, modifiers = {}, visibleIds = state.items.map(item => item.id)) {
    if (!state.items.some(item => item.id === id)) return
    if (modifiers.shiftKey && state.focusedId && visibleIds.includes(state.focusedId)) {
      const a = visibleIds.indexOf(state.focusedId), b = visibleIds.indexOf(id)
      state.selectedIds = visibleIds.slice(Math.min(a, b), Math.max(a, b) + 1)
    } else if (modifiers.ctrlKey || modifiers.metaKey) state.selectedIds = state.selectedIds.includes(id) ? state.selectedIds.filter(value => value !== id) : [...state.selectedIds, id]
    else state.selectedIds = [id]
    state.focusedId = id
    const sequence = ++detailSequence, token = scope
    state.detail = null; state.detailBusy = true
    try {
      const payload = { installation_id: state.installationId, id, offset: 0, limit: 200 }
      const first = await call('mod.workspace.detail', payload, token)
      if (!current(token) || sequence !== detailSequence) return
      if (!first || !Array.isArray(first.files)) throw new Error('无法读取 Mod 文件详情。')
      const files = [...first.files]
      let next = first.next_offset
      while (next != null) {
        const page = await call('mod.workspace.detail', { ...payload, offset: next }, token)
        if (!current(token) || sequence !== detailSequence) return
        if (!page || !Array.isArray(page.files) || page.next_offset != null && page.next_offset <= next) throw new Error('文件清单未能完整读取。')
        files.push(...page.files); next = page.next_offset
      }
      state.detail = { ...first, files }
    } catch (error) { if (current(token) && sequence === detailSequence) state.error = error.message }
    finally { if (current(token) && sequence === detailSequence) state.detailBusy = false }
  }

  async function save() {
    if (!state.loaded || state.loading || state.busy) return false
    if (!state.dirty) return true
    const token = scope
    state.busy = true; state.error = ''; state.notice = ''; state.task = null
    try {
      const result = await call('mod.workspace.save', { installation_id: state.installationId, snapshot_id: state.snapshotId,
        enabled_ids: [...state.enabledIds], idempotency_key: createRequestId() }, token, true)
      if (!current(token)) return false
      if (result?.completed !== true) throw new Error('保存尚未完成，未启动游戏。')
      accept(result.workspace); drafts.delete(state.installationId)
      state.notice = '已保存并应用到游戏。'
      return true
    } catch (error) { if (current(token)) state.error = error.message; return false }
    finally { if (current(token)) state.busy = false }
  }

  async function uninstall(item) {
    if (disposed || state.busy || state.loading || state.dirty || !item?.release_id || !['enabled', 'disabled'].includes(item.state)) return false
    const token = scope
    state.busy = true; state.error = ''; state.notice = ''; state.task = null
    try {
      const result = await call('mod.action', { installation_id: state.installationId, release_id: item.release_id,
        manifest_hash: item.manifest_hash, action: 'uninstall', idempotency_key: createRequestId() }, token, true)
      if (!current(token)) return false
      if (result?.completed !== true || result.state !== 'not_installed') throw new Error('卸载结果尚未确认，请刷新核对。')
      const workspace = await call('mod.workspace', { installation_id: state.installationId }, token)
      if (!current(token)) return false
      accept(workspace)
      state.notice = '已卸载，包库副本保留，可重新加入加载列表。'
      return true
    } catch (error) { if (current(token)) state.error = error.message; return false }
    finally { if (current(token)) state.busy = false }
  }

  async function reveal(releaseId) {
    if (!state.installationId || state.busy || state.loading) return null
    if (!state.dirty) await refresh()
    else {
      const token = scope, draft = [...state.enabledIds], baseline = [...state.baseEnabled]
      state.loading = true
      try {
        const workspace = await call('mod.workspace', { installation_id: state.installationId }, token)
        if (!current(token)) return null
        if (!same(workspace.enabled_ids, baseline)) throw new Error('游戏中的 Mod 状态已改变。请先保存或放弃当前草稿，再刷新列表。')
        accept(workspace)
        state.enabledIds = draft; state.dirty = !same(draft, state.baseEnabled); state.undo = [baseline]
      } catch (error) { if (current(token)) state.error = error.message; return null }
      finally { if (current(token)) state.loading = false }
    }
    const row = state.items.find(item => item.release_id === releaseId || item.id === releaseId)
    if (row) { await select(row.id); return row.id }
    return null
  }

  return { refresh, setInstallation, move, select, save, reveal, uninstall,
    undo: () => history('undo', 'redo'), redo: () => history('redo', 'undo'),
    discard() { if (!state.busy) { edit([...state.baseEnabled]); drafts.delete(state.installationId); state.notice = '已撤销未保存的选择。' } },
    async cancel() { for (const job of jobs) await job.cancel() },
    dispose() { disposed = true; ++scope; for (const job of [...jobs]) job.stop() },
  }
}
