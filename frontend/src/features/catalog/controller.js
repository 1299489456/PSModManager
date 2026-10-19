import { terminalStates } from '../../app/bridge/reducer.js'

export const rowHeight = 44
export function indexEntries(entries) {
  return entries.map(row => ({ ...row, _search: [row.relative_path, row.assembly_name, row.assembly_version, ...(row.tags || [])].filter(Boolean).join('\n').toLocaleLowerCase() }))
}
export function filterEntries(entries, query = '', mode = 'all') {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/u).filter(Boolean)
  return entries.filter(row => (mode !== 'candidate' || (row.presence !== 'missing' && row.prediction?.status === 'candidate')) && terms.every(term => row._search.includes(term)))
}
export function virtualWindow(rows, scrollTop = 0, height = 600) {
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 4)
  const end = Math.min(rows.length, start + Math.ceil(height / rowHeight) + 8)
  return { rows: rows.slice(start, end), start, offset: start * rowHeight, totalHeight: rows.length * rowHeight }
}

export function createWorkbenchController(bridge, state = {}, onChange = () => {}) {
  Object.assign(state, { installation: null, entries: [], generation: null, inventory: null, busy: false, loading: false,
    task: null, error: '', notice: '', selectedIds: [], focusedId: null, detail: null, contents: [], contentNext: null,
    graph: [], graphNext: null, diagnostics: [], diagnosticNext: null, diagnosticSnapshot: null, diagnosticSummary: null,
    diagnosticCoverage: null, diagnosticBusy: false, sessions: [], sources: [], sessionId: '', evidence: null,
    evidenceParts: [], evidenceNext: null, evidenceBusy: false, tab: 'issues', query: '', logTask: null })
  let disposed = false, scope = 0, inventoryRequest = 0, detailRequest = 0, diagnosticRequest = 0, evidenceRequest = 0, watcher = null, logWatcher = null
  const notify = () => { if (!disposed) onChange(state) }
  const valid = token => !disposed && token === scope
  const id = () => state.installation?.installation_id
  const payload = () => ({ installation_id: id(), ...(state.generation != null ? { generation: state.generation } : {}) })
  const fail = error => { state.error = `操作未完成（${error?.code || '连接中断'}），请重试。之前的扫描结果仍会保留。`; notify() }
  async function loadInventory() {
    if (!id() || disposed) return
    const token = scope, request = ++inventoryRequest; state.loading = true; notify()
    const current = () => valid(token) && request === inventoryRequest
    try {
      let page = await bridge.request('inventory.list', { installation_id: id(), limit: 50, offset: 0 })
      if (!current()) return
      const first = page, rows = [...page.entries]
      while (page.next_offset != null && rows.length < 5000) {
        page = await bridge.request('inventory.list', { installation_id: id(), generation: first.generation, limit: 50, offset: page.next_offset })
        if (!current()) return
        rows.push(...page.entries)
      }
      if (state.generation !== first.generation) { detailRequest++; state.detail = null; state.contents = []; state.graph = []; state.focusedId = null; state.selectedIds = [] }
      state.entries = indexEntries(rows); state.inventory = first; state.generation = first.generation
      state.selectedIds = state.selectedIds.filter(key => rows.some(row => row.entry_id === key))
      state.notice = first.last_attempt && first.last_attempt.status !== 'complete'
        ? '最近一次扫描未完成，仍显示之前保存的结果。' : first.generation ? '显示已保存的扫描结果；游戏运行情况尚未验证。' : '尚无扫描结果，请点击“扫描”。'
      if (page.next_offset != null) state.notice += ' 当前仅加载前 5000 项。'
    } catch (error) { if (current()) fail(error) }
    finally { if (current()) { state.loading = false; notify() } }
  }
  async function watchLogs() {
    const token = scope
    try {
      const initial = await bridge.request('logs.watch', { installation_id: id() })
      if (!valid(token)) return
      state.logTask = initial
      logWatcher = bridge.watchTask(initial.task_id, value => { if (valid(token)) { state.logTask = value; notify() } }, error => { if (valid(token)) fail(error) })
    } catch (error) { if (valid(token)) fail(error) }
  }
  async function refreshSources() {
    if (!id()) return
    const token = scope
    try {
      const [sources, sessions] = await Promise.all([bridge.request('logs.sources', { installation_id: id() }), bridge.request('session.list', { installation_id: id(), after: 0, limit: 20 })])
      if (valid(token)) { state.sources = sources.sources || sources.entries || []; state.sessions = sessions.entries || []; notify() }
    } catch (error) { if (valid(token)) fail(error) }
  }
  async function diagnostics(more = false) {
    if (!id() || state.diagnosticBusy || disposed) return
    const token = scope, request = ++diagnosticRequest
    state.diagnosticBusy = true; notify()
    try {
      const query = { ...payload(), limit: 20, offset: more ? state.diagnosticNext : 0 }
      if (state.sessionId) query.session_id = state.sessionId
      if (more && state.diagnosticSnapshot) query.snapshot_id = state.diagnosticSnapshot
      const result = await bridge.request('diagnostics.explain', query)
      if (!valid(token) || request !== diagnosticRequest) return
      state.diagnostics = more ? [...state.diagnostics, ...result.diagnostics] : result.diagnostics
      state.diagnosticNext = result.next_offset; state.diagnosticSnapshot = result.snapshot_id
      state.diagnosticSummary = result.summary; state.diagnosticCoverage = result.coverage
      if (!more) void refreshSources()
    } catch (error) { if (valid(token)) fail(error) }
    finally { if (valid(token) && request === diagnosticRequest) { state.diagnosticBusy = false; notify() } }
  }
  async function choose(installation) {
    if (disposed || installation?.installation_id === id()) return
    const token = ++scope; detailRequest++; diagnosticRequest++; evidenceRequest++
    await watcher?.dispose(); await logWatcher?.dispose(); watcher = null; logWatcher = null
    if (!valid(token)) return
    Object.assign(state, { installation, entries: [], generation: null, inventory: null, detail: null, contents: [], graph: [], selectedIds: [],
      focusedId: null, task: null, busy: false, diagnostics: [], evidence: null, evidenceParts: [], error: '', sessionId: '',
      diagnosticBusy: false, diagnosticSummary: null, diagnosticSnapshot: null, sessions: [], sources: [] })
    notify(); await loadInventory(); if (!valid(token)) return
    await watchLogs(); if (valid(token)) await diagnostics()
  }
  async function scan(force = false) {
    if (!id() || state.busy || disposed) return
    const token = scope; state.busy = true; state.error = ''; state.task = null; notify()
    await watcher?.dispose()
    try {
      const initial = await bridge.request('inventory.scan', { installation_id: id(), force })
      if (!valid(token)) return
      let settled = false
      const update = value => {
        if (!valid(token) || settled) return
        state.task = value; notify()
        if (!terminalStates.has(value.state)) return
        settled = true; state.busy = false
        if (value.state !== 'succeeded') state.notice = value.state === 'cancelled' ? '扫描已取消，保留此前完整库存。' : '扫描失败，保留此前完整库存。'
        void loadInventory().then(() => valid(token) && diagnostics())
      }
      update(initial)
      if (!settled) watcher = bridge.watchTask(initial.task_id, update, error => { if (valid(token)) { state.busy = false; fail(error) } })
    } catch (error) { if (valid(token)) { state.busy = false; fail(error) } }
  }
  async function select(entryId, modifiers = {}) {
    if (!state.entries.some(row => row.entry_id === entryId)) return
    const filtered = filterEntries(state.entries, state.query)
    if (modifiers.shiftKey && state.focusedId) {
      const start = filtered.findIndex(row => row.entry_id === state.focusedId), end = filtered.findIndex(row => row.entry_id === entryId)
      state.selectedIds = start < 0 || end < 0 ? [entryId] : filtered.slice(Math.min(start, end), Math.max(start, end) + 1).map(row => row.entry_id)
    } else if (modifiers.ctrlKey || modifiers.metaKey) {
      state.selectedIds = state.selectedIds.includes(entryId) ? state.selectedIds.filter(key => key !== entryId) : [...state.selectedIds, entryId]
    } else state.selectedIds = [entryId]
    state.focusedId = entryId; state.detail = null; state.contents = []; state.graph = []; notify()
    const token = scope, request = ++detailRequest
    try {
      const result = await bridge.request('inventory.detail', { ...payload(), entry_id: entryId })
      if (!valid(token) || request !== detailRequest) return
      state.detail = result; state.contents = result.analysis?.content_ids || []; state.contentNext = state.contents.length < (result.analysis?.content_id_count || 0) ? state.contents.length : null
      notify()
      await graph(false, token, request)
    } catch (error) { if (valid(token) && request === detailRequest) fail(error) }
  }
  async function contents() {
    if (state.contentNext == null || !state.detail) return
    const token = scope, request = detailRequest
    try {
      const result = await bridge.request('inventory.contents', { ...payload(), entry_id: state.detail.entry_id, offset: state.contentNext, limit: 20 })
      if (valid(token) && request === detailRequest) { state.contents.push(...result.entries); state.contentNext = result.next_offset; notify() }
    } catch (error) { if (valid(token)) fail(error) }
  }
  async function graph(more = false, token = scope, request = detailRequest) {
    try {
      const result = await bridge.request('inventory.graph', { ...payload(), offset: more ? state.graphNext : 0, limit: 20 })
      if (valid(token) && request === detailRequest) { state.graph = more ? [...state.graph, ...result.relations] : result.relations; state.graphNext = result.next_offset; notify() }
    } catch (error) { if (valid(token)) fail(error) }
  }
  async function readEvidence(evidenceId, more = false) {
    if (!id() || (more && state.evidenceBusy)) return
    const token = scope, request = ++evidenceRequest
    state.evidenceBusy = true; state.tab = 'evidence'; if (!more) { state.evidence = null; state.evidenceParts = [] } notify()
    try {
      const result = await bridge.request('evidence.read', { installation_id: id(), evidence_id: evidenceId, offset: more ? state.evidenceNext : 0, limit: 8192 })
      if (valid(token) && request === evidenceRequest) {
        state.evidence = result; state.evidenceParts.push(result); state.evidenceNext = result.next_offset; notify()
      }
    } catch (error) { if (valid(token) && request === evidenceRequest) fail(error) }
    finally { if (valid(token) && request === evidenceRequest) { state.evidenceBusy = false; notify() } }
  }
  return { state, choose, scan, loadInventory, diagnostics, refreshSources, select, contents, graph, readEvidence,
    async cancel() { try { await watcher?.cancel() } catch (error) { fail(error) } },
    async setSession(value) { diagnosticRequest++; state.diagnosticBusy = false; state.sessionId = value; state.diagnostics = []; state.diagnosticNext = null; state.diagnosticSnapshot = null; await diagnostics() },
    selectAll() { state.selectedIds = filterEntries(state.entries, state.query).map(row => row.entry_id); notify() },
    async dispose() { disposed = true; scope++; await watcher?.dispose(); await logWatcher?.dispose() },
  }
}
