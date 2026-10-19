import { terminalStates } from '../../app/bridge/reducer.js'

export const VIEWS = [
  { value: 'latest_updated', label: '最近更新' }, { value: 'latest_added', label: '最新发布' },
  { value: 'downloads', label: '下载最多' }, { value: 'endorsements', label: '最受好评' },
]

// Pieces published by `nexus.translate`, joined back in order per part.
export function joinPieces(pieces = []) {
  return pieces.map((piece, index) => (index ? piece?.sep ?? '\n' : '') + (piece?.text ?? '')).join('')
}

export function createNexusRuntime(bridge, state, downloaded = () => {}) {
  Object.assign(state, { busy: false, task: null, settings: null, apiKey: '', remember: true,
    view: 'latest_updated', query: '', appliedQuery: '', mods: [], total: 0, loaded: false, listLoading: false, listError: '',
    detail: null, detailLoading: false, detailError: '', translation: null, showTranslation: true,
    nxmUrl: '', error: '', notice: '' })
  const watchers = new Set()
  let disposed = false, listToken = 0, detailToken = 0, rejectPending, runTask = null, translationTask = null

  // Runs one command; long ones are followed to the end. Progress events go to onProgress.
  async function follow(command, payload, onProgress, onStart) {
    const initial = await bridge.request(command, payload)
    if (!initial?.task_id) return initial
    onStart?.(initial.task_id)
    if (disposed) { await bridge.request('task.cancel', { task_id: initial.task_id }).catch(() => {}); return null }
    return await new Promise((resolve, reject) => {
      let watcher = null
      const settle = (done, value) => { if (watcher) { watchers.delete(watcher); void watcher.dispose() }; done(value) }
      const update = snapshot => {
        if (disposed) return
        if (snapshot.type === 'progress') onProgress?.(snapshot)
        if (!terminalStates.has(snapshot.state)) return
        if (snapshot.state === 'cancelled') settle(reject, new Error('已取消。'))
        else if (snapshot.state !== 'succeeded') settle(reject, new Error('在线操作未完成，请重试。'))
        else if (snapshot.data?.ok === false || snapshot.data?.error_code) settle(reject, new Error(snapshot.data.message || 'Nexus 操作未完成。'))
        else settle(resolve, snapshot.data)
      }
      if (terminalStates.has(initial.state)) { update(initial); return }
      watcher = bridge.watchTask(initial.task_id, update, () => settle(reject, new Error('进度连接中断，请重试。')))
      watchers.add(watcher)
    })
  }

  // Account and download steps block each other; browsing and reading details do not.
  async function run(command, payload = {}) {
    if (disposed || state.busy) return null
    state.busy = true; state.error = ''; state.notice = ''; state.task = null
    try {
      return await new Promise((resolve, reject) => {
        rejectPending = reject
        follow(command, payload, snapshot => { state.task = snapshot }, id => { runTask = id }).then(resolve, reject)
      })
    } catch (error) {
      if (!disposed) state.error = error.message || 'Nexus 操作未完成。'
      return null
    } finally {
      rejectPending = null; runTask = null
      if (!disposed) { state.busy = false; state.task = null }
    }
  }

  async function page(reset) {
    if (disposed || !state.settings?.configured || state.listLoading && !reset) return
    if (!reset && state.loaded && state.mods.length >= state.total) return
    const token = ++listToken
    if (reset) { state.appliedQuery = state.query.trim(); state.mods = []; state.total = 0; state.loaded = false }
    state.listLoading = true; state.listError = ''
    try {
      const result = await follow('nexus.browse', { view: state.view, offset: state.mods.length, query: state.appliedQuery })
      if (disposed || token !== listToken || !result) return
      const known = new Set(state.mods.map(mod => mod.mod_id))
      state.mods.push(...result.mods.filter(mod => !known.has(mod.mod_id)))
      // A short page means the end even if the count moved while scrolling.
      state.total = result.mods.length < (result.page_size || 20) ? state.mods.length : result.total
      state.loaded = true
    } catch (error) {
      if (!disposed && token === listToken) state.listError = error.message || '无法读取 Nexus 列表。'
    } finally {
      if (!disposed && token === listToken) state.listLoading = false
    }
  }

  return {
    async open() { state.settings = await run('nexus.settings.get') },
    async save() {
      if (!state.apiKey.trim()) return
      const result = await run('nexus.settings.save', { api_key: state.apiKey.trim(), remember: state.remember })
      state.apiKey = ''
      if (result) { state.settings = result; state.notice = result.notice }
    },
    async clear() { const result = await run('nexus.settings.clear'); if (result) { state.settings = result; state.notice = '已清除 Nexus 密钥。' } },
    async account() { const result = await run('nexus.account'); if (result) state.settings = { ...state.settings, account: result.account } },
    browse() { return page(true) },
    more() { return page(false) },
    setView(view) { if (VIEWS.some(item => item.value === view) && view !== state.view) { state.view = view; return page(true) } },
    async openMod(mod) {
      const token = ++detailToken
      state.detail = { ...mod, description: null, files: null, requirements: [], tags: [] }
      state.detailLoading = true; state.detailError = ''; state.nxmUrl = ''
      if (state.translation?.modId !== mod.mod_id) state.translation = null
      try {
        const result = await follow('nexus.mod', { mod_id: mod.mod_id })
        if (!disposed && token === detailToken && result) state.detail = result
      } catch (error) {
        if (!disposed && token === detailToken) state.detailError = error.message || '无法读取 Mod 详情。'
      } finally {
        if (!disposed && token === detailToken) state.detailLoading = false
      }
    },
    closeMod() { detailToken++; state.detail = null; state.detailLoading = false; state.detailError = ''; state.nxmUrl = '' },
    async translate() {
      const modId = state.detail?.mod_id
      if (!modId || state.translation?.status === 'running') return
      state.translation = { modId, status: 'running', done: 0, total: 0, pieces: { summary: [], description: [] }, error: '', truncated: false }
      // Keep the reactive proxy: comparisons and updates must go through it.
      const translation = state.translation
      state.showTranslation = true
      const current = () => state.translation === translation && !disposed
      try {
        const result = await follow('nexus.translate', { mod_id: modId }, event => {
          if (!current() || !event.data || !['summary', 'description'].includes(event.data.part)) return
          translation.pieces[event.data.part][event.data.index] = { sep: event.data.sep, text: event.data.text }
          translation.done = event.completed; translation.total = event.total
        }, id => { translationTask = id })
        if (current()) { translation.status = 'done'; translation.truncated = Boolean(result?.truncated) }
      } catch (error) {
        if (current()) { translation.status = 'error'; translation.error = error.message || '翻译未完成，请重试。' }
      } finally {
        translationTask = null
      }
    },
    cancelTranslation() { if (translationTask) return bridge.request('task.cancel', { task_id: translationTask }).catch(() => {}) },
    translated(part) { return state.translation?.modId === state.detail?.mod_id ? joinPieces(state.translation.pieces[part]) : '' },
    async download(file) {
      if (!state.detail) return
      const result = await run('nexus.download', { mod_id: state.detail.mod_id, file_id: file.file_id, nxm_url: state.nxmUrl.trim() })
      if (result?.source_handle) { state.nxmUrl = ''; downloaded({ ...result, display_name: state.detail.name || result.display_name }) }
    },
    openPage(page, modId) { return bridge.request('nexus.open', { page, ...(modId ? { mod_id: modId } : {}) }).catch(() => {}) },
    openLink(url) { return bridge.request('nexus.open', { page: 'link', url }).catch(() => { state.error = '这个链接无法打开。' }) },
    cancel() { if (runTask) return bridge.request('task.cancel', { task_id: runTask }).catch(() => {}) },
    async dispose() {
      disposed = true; state.apiKey = ''; state.nxmUrl = ''
      rejectPending?.(new Error('已关闭'))
      await Promise.all([...watchers].map(async watcher => { await watcher.cancel().catch(() => {}); await watcher.dispose() }))
    },
  }
}
