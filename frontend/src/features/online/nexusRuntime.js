import { terminalStates } from '../../app/bridge/reducer.js'

export const VIEWS = [
  { value: 'latest_updated', label: '最近更新' }, { value: 'latest_added', label: '最新发布' },
  { value: 'downloads', label: '下载最多' }, { value: 'endorsements', label: '最受好评' },
]

// Pieces published by `nexus.translate`, joined back in order per part.
export function joinPieces(pieces = []) {
  return pieces.map((piece, index) => (index ? piece?.sep ?? '\n' : '') + (piece?.text ?? '')).join('')
}

export const canInstallFile = file => Boolean(file.supported) && !(file.size_bytes > 67108864)
// Never silently fall back to an older release because the latest is too large or unsupported.
export function latestMainFile(files = []) {
  const main = files.filter(file => file.category === 'MAIN')
    .sort((a, b) => (Number(b.uploaded) || 0) - (Number(a.uploaded) || 0) || b.file_id - a.file_id)
  if (!main.length) return null
  const latest = main[0]
  const variants = main.filter(file => file.version === latest.version)
  if (variants.length === 1) return latest
  const primary = variants.filter(file => file.primary)
  return primary.length === 1 ? primary[0] : null
}

export function createNexusRuntime(bridge, state, downloaded = () => {}, { canInstall = () => true } = {}) {
  Object.assign(state, { busy: false, task: null, settings: null, apiKey: '', remember: true,
    view: 'latest_updated', query: '', appliedQuery: '', mods: [], total: 0, loaded: false, listLoading: false, listError: '',
    detail: null, detailLoading: false, detailError: '', translation: null, showTranslation: true,
    versions: null, versionsOpen: false, installingModId: null,
    nxmUrl: '', error: '', notice: '', protocol: null, pendingWebsite: null, incoming: [], readyDownload: null })
  const watchers = new Set()
  const handledReceipts = new Set()
  const attemptedReceipts = new Set()
  let disposed = false, listToken = 0, detailToken = 0, installToken = 0, rejectPending, runTask = null, runCancelled = false, translationTask = null

  // Runs one command; long ones are followed to the end. Progress events go to onProgress.
  async function follow(command, payload, onProgress, onStart) {
    const initial = await bridge.request(command, payload)
    if (!initial?.task_id) {
      if (initial?.ok === false) throw new Error(initial.message || 'Nexus 操作未完成。')
      return initial
    }
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
    runCancelled = false
    try {
      const result = await new Promise((resolve, reject) => {
        rejectPending = reject
        follow(command, payload, snapshot => { state.task = snapshot }, id => {
          runTask = id
          if (runCancelled) void bridge.request('task.cancel', { task_id: id }).catch(() => {})
        }).then(resolve, reject)
      })
      if (runCancelled) { state.error = '已取消。'; return null }
      return result
    } catch (error) {
      if (!disposed) state.error = error.message || 'Nexus 操作未完成。'
      return null
    } finally {
      rejectPending = null; runTask = null
      if (!disposed) { state.busy = false; state.task = null }
    }
  }

  function cancelRun() {
    if (!state.busy) return
    runCancelled = true
    if (runTask) return bridge.request('task.cancel', { task_id: runTask }).catch(() => {})
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

  async function download(file, mod = state.detail) {
    if (!mod || state.busy || disposed || state.readyDownload) return
    // The backend still validates the downloaded type, size and account authorization.
    if (file.supported === false || file.size_bytes > 67108864) {
      state.error = file.size_bytes > 67108864 ? '这个文件超过 64 MB，请到官网下载。' : '这个文件格式需要手动下载。'
      return
    }
    const token = installToken
    const result = await run('nexus.download', { mod_id: mod.mod_id, file_id: file.file_id, nxm_url: state.nxmUrl.trim() })
    if (disposed || token !== installToken) return
    if (result?.source_handle) {
      state.nxmUrl = ''; state.versionsOpen = false
      state.pendingWebsite = null
      deliver({ ...result, display_name: mod.name || result.display_name })
    } else if (result?.requires_website) {
      state.pendingWebsite = { mod_id: mod.mod_id, file_id: file.file_id, name: mod.name, fileName: file.name }
      state.versionsOpen = false
      await openWebsite()
    } else if (state.versions?.mod_id === mod.mod_id && state.error !== '已取消。') state.versionsOpen = true
  }

  function deliver(result) {
    state.readyDownload = result
    if (canInstall()) {
      state.readyDownload = null
      downloaded(result)
    } else state.notice = '下载完成；结束当前导入或保存后将继续安装。'
  }

  async function openWebsite() {
    const pending = state.pendingWebsite
    if (!pending || disposed) return
    state.error = ''
    try {
      const result = await bridge.request('nexus.open', { page: 'files', mod_id: pending.mod_id, file_id: pending.file_id })
      if (!result?.opened) throw new Error('浏览器未能打开，请点击“重新打开官网”重试。')
      state.notice = ''
    } catch (error) { state.error = error.message || '无法打开浏览器，请重试。' }
  }

  async function receive(item) {
    if (disposed || state.busy || state.readyDownload || !canInstall() || !state.settings?.configured || handledReceipts.has(item.id)) return
    attemptedReceipts.add(item.id)
    // Only non-secret receipt IDs are kept in the page. The backend consumes the authorization once.
    if (handledReceipts.size > 64) handledReceipts.delete(handledReceipts.values().next().value)
    const pending = state.pendingWebsite
    const matched = pending?.mod_id === item.mod_id && pending?.file_id === item.file_id
    state.incoming = state.incoming.filter(row => row.id !== item.id)
    const result = await run('nexus.inbox.download', { id: item.id })
    if (disposed) return
    if (result?.source_handle) {
      handledReceipts.add(item.id)
      if (matched) state.pendingWebsite = null
      state.versionsOpen = false
      deliver({ ...result, display_name: matched ? pending.name || result.display_name : result.display_name })
    }
  }

  function incoming(items = []) {
    if (disposed) return
    state.incoming = items.filter(row => !handledReceipts.has(row.id))
    if (state.readyDownload) { deliver(state.readyDownload); return }
    const pending = state.pendingWebsite
    const matched = state.incoming.find(row => !attemptedReceipts.has(row.id) && pending?.mod_id === row.mod_id && pending?.file_id === row.file_id)
    if (matched) void receive(matched)
  }

  async function prepareInstall(mod, choose) {
    if (disposed || state.busy) return
    const token = ++installToken
    state.installingModId = mod.mod_id; state.nxmUrl = ''
    state.versions = { ...mod, files: null }; state.versionsOpen = choose
    try {
      const result = await run('nexus.mod', { mod_id: mod.mod_id })
      if (disposed || token !== installToken) return
      if (!result) { if (state.error !== '已取消。') state.versionsOpen = true; return }
      state.versions = result
      if (choose) return
      const latest = latestMainFile(result.files)
      if (!latest || !canInstallFile(latest)) {
        state.versionsOpen = true
        state.notice = !latest ? '没有唯一的最新主文件，请选择要安装的版本。'
          : latest.size_bytes > 67108864 ? '最新主文件超过 64 MB，请到官网下载，或选择其他版本。'
            : '最新主文件格式暂不支持直接安装，请到官网下载，或选择其他版本。'
        return
      }
      await download(latest, result)
    } finally {
      if (token === installToken) state.installingModId = null
    }
  }

  return {
    async open() {
      state.settings = await run('nexus.settings.get')
      state.protocol = await run('nexus.protocol.status')
    },
    async registerProtocol(replace = false) {
      const result = await run('nexus.protocol.register', { replace })
      if (result) state.protocol = result
    },
    async unregisterProtocol() {
      const result = await run('nexus.protocol.unregister')
      if (result) state.protocol = result
    },
    chooseDefault() { return run('nexus.protocol.choose_default') },
    async refreshProtocol() {
      const result = await run('nexus.protocol.status')
      if (result) state.protocol = result
    },
    openWebsite, incoming, receive,
    async importLocal(kind) {
      if (disposed || state.busy || state.readyDownload || !canInstall() || !['zip', 'dll'].includes(kind)) return
      const source = await run('package.choose', { kind })
      if (source?.error_code) { state.error = source.message || '无法导入所选文件。'; return }
      if (!source?.source_handle || disposed) return
      // The selected file may be a different Mod/version. Trust its contents, not the website selection.
      state.pendingWebsite = null; state.nxmUrl = ''; state.versionsOpen = false
      deliver(source)
    },
    cancelWebsite() { state.pendingWebsite = null; state.nxmUrl = ''; state.notice = '已关闭官网下载提示。' },
    async discard(item) {
      const result = await run('nexus.inbox.discard', { id: item.id })
      if (result) { handledReceipts.add(item.id); state.incoming = state.incoming.filter(row => row.id !== item.id) }
    },
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
    download,
    installLatest(mod) { return prepareInstall(mod, false) },
    chooseVersion(mod) { return prepareInstall(mod, true) },
    closeVersions() {
      installToken++; state.versionsOpen = false; state.installingModId = null; state.nxmUrl = ''
      void cancelRun()
    },
    openPage(page, modId) { return bridge.request('nexus.open', { page, ...(modId ? { mod_id: modId } : {}) }).catch(() => {}) },
    openLink(url) { return bridge.request('nexus.open', { page: 'link', url }).catch(() => { state.error = '这个链接无法打开。' }) },
    cancel: cancelRun,
    async dispose() {
      disposed = true; state.apiKey = ''; state.nxmUrl = ''
      rejectPending?.(new Error('已关闭'))
      await Promise.all([...watchers].map(async watcher => { await watcher.cancel().catch(() => {}); await watcher.dispose() }))
    },
  }
}
