const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const validId = value => typeof value === 'string' && idPattern.test(value)
const errorText = (error, fallback) => error?.code && typeof error.message === 'string' && error.message.length <= 300 ? error.message : fallback

// Pages read for the continuous reader are kept per source until the logs are
// refreshed, so switching tabs does not re-read what is already on screen.
export const MAX_CACHED_PAGES = 64

export function createLogDiagnosticsRuntime(bridge, ai, aiState, state = {}) {
  let disposed = false, epoch = 0, readEpoch = 0
  const cache = new Map()
  Object.assign(state, { installationId: null, sources: [], selectedIds: [], includeContext: true, preview: null,
    loading: false, reading: false, error: '', activeSourceId: '', warnings: [],
    pageReading: false, pages: [], pageIndex: 0, viewMode: 'original', reconciliation: null })
  const available = () => !disposed && !aiState.busy && !state.loading && !state.reading
  const payload = () => state.installationId ? { installation_id: state.installationId } : {}

  async function refreshPreview() {
    if (!available()) return false
    const token = ++epoch
    state.error = ''
    state.preview = null
    cache.clear()
    await ai.setDiagnostic(null)
    if (disposed || token !== epoch) return false
    if (!state.selectedIds.length) return false
    state.reading = true
    try {
      const result = await bridge.request('ai.diagnostics.preview', { ...payload(), source_ids: [...state.selectedIds], include_context: state.includeContext })
      if (disposed || token !== epoch) return false
      if (!result || !validId(result.snapshot_id) || !Array.isArray(result.logs) || result.logs.length > 3
        || result.logs.some(row => !validId(row.source_id) || typeof row.text !== 'string' || typeof row.label !== 'string' || typeof row.truncated !== 'boolean')
        || !Array.isArray(result.warnings) || !result.context || typeof result.context !== 'object') throw new Error('Invalid diagnostic preview')
      state.preview = result
      if (!result.logs.some(row => row.source_id === state.activeSourceId)) {
        const selected = source => result.logs.some(row => row.source_id === source.source_id)
        const latest = state.installationId && state.sources.find(row => row.status === 'available'
          && row.kind === 'game' && row.relative_path === 'Player.log' && selected(row))
        const manager = state.sources.find(row => row.status === 'available' && row.kind === 'manager' && selected(row))
        state.activeSourceId = latest?.source_id || manager?.source_id || result.logs[0]?.source_id || ''
      }
      await ai.setAllowQueries(Boolean(state.includeContext && state.installationId))
      if (disposed || token !== epoch) return false
      await ai.setDiagnostic(result.snapshot_id)
      if (disposed || token !== epoch) return false
      await viewSource(state.activeSourceId)
      return true
    } catch (error) {
      if (!disposed && token === epoch) state.error = errorText(error, '无法读取所选日志，请刷新或更换日志来源。')
      return false
    } finally { if (!disposed && token === epoch) state.reading = false }
  }

  async function load(installation = null) {
    const token = ++epoch
    const installationId = validId(installation?.installation_id) ? installation.installation_id : null
    const changed = state.installationId !== installationId
    ++readEpoch
    cache.clear()
    Object.assign(state, { installationId, loading: true, reading: false, error: '', preview: null, pages: [], pageIndex: 0, pageReading: false, reconciliation: null })
    await ai.setInstallation(installation)
    if (disposed || token !== epoch) return false
    await ai.setDiagnostic(null)
    if (disposed || token !== epoch) return false
    try {
      const result = await bridge.request('ai.diagnostics.sources', payload())
      if (disposed || token !== epoch) return false
      if (!result || !Array.isArray(result.sources) || result.sources.some(row => !validId(row.source_id) || typeof row.label !== 'string' || !['available', 'missing', 'unreadable'].includes(row.status))) throw new Error('Invalid log sources')
      state.sources = result.sources
      state.reconciliation = result.runtime_reconciliation || null
      state.warnings = result.warnings || []
      const retained = changed ? [] : state.selectedIds.filter(id => result.sources.some(row => row.source_id === id && row.status === 'available'))
      state.selectedIds = retained.length ? retained : result.sources.filter(row => row.default && row.status === 'available').slice(0, 3).map(row => row.source_id)
    } catch (error) {
      if (!disposed && token === epoch) { state.sources = []; state.selectedIds = []; state.error = errorText(error, '日志来源暂时不可读，请刷新后重试。') }
      return false
    } finally { if (!disposed && token === epoch) state.loading = false }
    if (disposed || token !== epoch) return false
    return refreshPreview()
  }

  async function toggleSource(sourceId, enabled) {
    if (!available() || !state.sources.some(row => row.source_id === sourceId && row.status === 'available')) return false
    const ids = state.selectedIds.filter(id => id !== sourceId)
    if (enabled) ids.push(sourceId)
    if (ids.length > 3) { state.error = '每次最多选择 3 份日志；请先取消一个来源。'; return false }
    state.selectedIds = ids
    return refreshPreview()
  }

  async function setContext(enabled) {
    if (!available()) return false
    state.includeContext = Boolean(enabled)
    await ai.setAllowQueries(Boolean(state.includeContext && state.installationId))
    return refreshPreview()
  }

  async function analyze() {
    if (!available()) return false
    if (!aiState.settings?.configured) { aiState.error = '先配置 AI 服务和模型，即可分析当前日志。'; return false }
    if (!state.preview && !await refreshPreview()) return false
    if (!state.preview.logs.some(row => row.text.trim())) { state.error = '所选日志暂时没有内容，先运行游戏生成日志或选择其他来源。'; return false }
    aiState.question = aiState.question.trim() || '请分析这些日志，找出游戏、Mod 或管理器的问题，引用相关日志行，说明最可能原因和下一步排查方法。'
    return ai.send()
  }

  async function chatOnly() {
    if (!available()) return false
    await ai.setDiagnostic(null)
    return true
  }

  async function readPage(before = null, older = false) {
    if (disposed || !state.activeSourceId || state.pageReading) return false
    const token = ++readEpoch, sourceId = state.activeSourceId
    state.pageReading = true
    state.error = ''
    try {
      const args = { ...payload(), source_id: sourceId }
      if (before !== null) args.before = before
      const result = await bridge.request('ai.diagnostics.read', args)
      if (disposed || token !== readEpoch) return false
      if (!result || result.source_id !== sourceId || typeof result.text !== 'string' || !Number.isSafeInteger(result.byte_start)
        || !Number.isSafeInteger(result.byte_end) || result.byte_end < result.byte_start
        || result.previous_before !== null && !Number.isSafeInteger(result.previous_before)) throw new Error('Invalid log page')
      if (older) { state.pages.push(result); state.pageIndex = state.pages.length - 1 }
      else { state.pages = [result]; state.pageIndex = 0 }
      return true
    } catch (error) {
      if (!disposed && token === readEpoch) state.error = errorText(error, '这段日志未能读取，请刷新重试。')
      return false
    } finally { if (!disposed && token === readEpoch) state.pageReading = false }
  }

  async function viewSource(sourceId) {
    ++readEpoch
    if (state.activeSourceId && state.pages.length) cache.set(state.activeSourceId, state.pages)
    const cached = cache.get(sourceId)
    Object.assign(state, { activeSourceId: sourceId, pages: cached || [], pageIndex: 0, pageReading: false })
    return cached ? true : readPage()
  }

  // Continuous reading: fetch the page before the oldest one already loaded.
  async function loadOlder() {
    if (disposed || state.pageReading || !state.pages.length || state.pages.length >= MAX_CACHED_PAGES) return false
    const before = state.pages.at(-1).previous_before
    return before == null ? false : readPage(before, true)
  }

  async function reloadSource() {
    if (disposed || !state.activeSourceId) return false
    cache.delete(state.activeSourceId)
    ++readEpoch
    Object.assign(state, { pages: [], pageIndex: 0, pageReading: false })
    return readPage()
  }

  async function olderPage() {
    if (disposed || state.pageReading) return false
    if (state.pageIndex < state.pages.length - 1) { ++state.pageIndex; return true }
    const before = state.pages[state.pageIndex]?.previous_before
    return before == null ? false : readPage(before, true)
  }

  function newerPage() { if (disposed || state.pageReading || state.pageIndex <= 0) return false; --state.pageIndex; return true }

  function dispose() { disposed = true; ++epoch; ++readEpoch }
  return { load, refreshPreview, toggleSource, setContext, analyze, chatOnly, viewSource, readPage, olderPage, newerPage, loadOlder, reloadSource, dispose }
}
