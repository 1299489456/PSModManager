import { terminalStates } from '../../app/bridge/reducer.js'
import { createRequestId } from '../../app/requestId.js'

export function createImportController(bridge, state) {
  let watcher = null, disposed = false, operation = 0
  Object.assign(state, { busy: false, task: null, preview: null, result: null, error: '', errorCode: '', notice: '', displayName: '', offset: 0,
    versionRaw: '', packageId: '', updatePreview: null, applied: null, sourceDisplayName: '' })
  async function perform(command, payload) {
    const token = ++operation
    state.busy = true; state.task = null; state.error = ''; state.errorCode = ''; state.notice = ''
    try {
      await watcher?.dispose(); watcher = null
      const initial = await bridge.request(command, payload)
      if (disposed || token !== operation) {
        if (initial?.task_id && !terminalStates.has(initial.state)) await bridge.request('task.cancel', { task_id: initial.task_id }).catch(() => {})
        return
      }
      let settled = false
      const update = snapshot => {
        if (disposed || token !== operation || settled) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        settled = true; state.busy = false
        if (snapshot.data?.error_code) { state.preview = null; if (command.startsWith('package.update_')) state.updatePreview = null; state.errorCode = snapshot.data.error_code; state.error = snapshot.data.message || '导入未完成，请重新选择文件。' }
        else if (snapshot.state === 'cancelled') { state.preview = null; state.notice = '已取消导入。' }
        else if (snapshot.state !== 'succeeded') { state.preview = null; state.error = '导入未完成，请检查文件后重试。' }
        else if (command === 'package.import_preview') {
          state.preview = snapshot.data; state.displayName = state.sourceDisplayName || snapshot.data.display_name; state.offset = 0
          state.versionRaw = snapshot.data.version?.version_raw || ''
          const candidates = snapshot.data.version_candidates || []
          const packages = [...new Set(candidates.filter(row => row.match === 'embedded_metadata').map(row => row.package_id))]
          state.packageId = packages.length === 1 ? packages[0] : ''
        } else if (command === 'package.update_preview') {
          state.updatePreview = snapshot.data
        } else if (command === 'package.update_apply') {
          state.applied = snapshot.data; state.updatePreview = null
          state.notice = snapshot.data?.completed ? '已安装并启用此版本；旧版本保留在包库。' : '安装尚未完成，请刷新核对。'
        } else {
          state.result = snapshot.data; state.preview = null; state.notice = '已加入本地包库，尚未启用或部署。'
        }
      }
      update(initial)
      if (!settled) watcher = bridge.watchTask(initial.task_id, update, () => {
        if (!disposed && token === operation) { state.busy = false; state.error = '导入进度连接中断，请重新选择文件核对结果。'; state.preview = null }
      })
    } catch {
      if (!disposed && token === operation) { state.busy = false; state.error = '无法完成导入，请重新选择文件。' }
    }
  }
  return {
    async openSource(source) {
      if (disposed || state.busy || !source?.source_handle) return
      state.preview = null; state.result = null; state.applied = null; state.updatePreview = null
      state.sourceDisplayName = typeof source.display_name === 'string' ? source.display_name.replace(/\.(zip|dll)$/i, '').slice(0, 200) : ''
      return perform('package.import_preview', { source_handle: source.source_handle })
    },
    async choose(kind) {
      if (disposed || state.busy) return
      state.busy = true; state.error = ''; state.errorCode = ''; state.notice = ''; state.task = null
      state.sourceDisplayName = ''
      try {
        const selected = await bridge.request('package.choose', { kind })
        if (disposed) return
        if (selected.error_code) { state.busy = false; state.preview = null; state.result = null; state.errorCode = selected.error_code; state.error = selected.message || '无法导入此来源。'; return }
        if (!selected.source_handle) { state.busy = false; state.notice = '已取消文件选择。'; return }
        state.preview = null; state.result = null
        state.applied = null; state.updatePreview = null
        await perform('package.import_preview', { source_handle: selected.source_handle })
      } catch { if (!disposed) { state.busy = false; state.error = '无法打开文件选择器，请重试。' } }
    },
    async page(offset) {
      if (disposed || state.busy || !state.preview || !Number.isSafeInteger(offset) || offset < 0 || offset >= state.preview.total) return
      state.busy = true; state.error = ''; state.errorCode = ''; state.task = null
      try {
        const page = await bridge.request('package.import_files', { preview_id: state.preview.preview_id, offset, limit: 20 })
        if (!disposed) { state.preview = page; state.offset = offset }
      } catch { if (!disposed) state.error = '无法读取预览，请重新选择文件。' }
      finally { if (!disposed) state.busy = false }
    },
    commit() {
      if (disposed || state.busy || !state.preview?.can_commit || !state.displayName.trim()) return
      const payload = { preview_id: state.preview.preview_id, preview_hash: state.preview.preview_hash, display_name: state.displayName.trim() }
      if (state.preview.version?.identity || state.versionRaw || state.packageId) {
        payload.version_raw = state.versionRaw.trim() || null
        if (state.packageId) payload.package_id = state.packageId
        return perform('package.import_version', payload)
      }
      return perform('package.import_commit', payload)
    },
    previewUpdate(installationId) {
      if (disposed || state.busy || !installationId || !state.result?.release_id) return
      return perform('package.update_preview', { installation_id: installationId, release_id: state.result.release_id, manifest_hash: state.result.manifest_hash })
    },
    applyUpdate(installationId) {
      if (disposed || state.busy || !installationId || !state.updatePreview) return
      const preview = state.updatePreview
      return perform('package.update_apply', { installation_id: installationId, release_id: preview.release_id,
        manifest_hash: preview.manifest_hash, snapshot_id: preview.snapshot_id,
        replaced_ids: preview.replacements.map(row => row.id), idempotency_key: createRequestId() })
    },
    async cancel() { try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求未完成，请重试。' } },
    async dispose() { disposed = true; operation++; if (state.task && !terminalStates.has(state.task.state)) await watcher?.cancel().catch(() => {}); await watcher?.dispose() },
  }
}
