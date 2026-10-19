import { terminalStates } from '../../app/bridge/reducer.js'
import { createRequestId } from '../../app/requestId.js'

export function createImportController(bridge, state, { getInstallContext = () => ({}) } = {}) {
  let watcher = null, disposed = false, operation = 0, finishWait = null, contextEpoch = 0, automatic = null, previewInstallationId = null
  Object.assign(state, { busy: false, task: null, preview: null, result: null, error: '', errorCode: '', notice: '', displayName: '', offset: 0,
    versionRaw: '', packageId: '', updatePreview: null, applied: null, sourceDisplayName: '', installStage: '' })
  async function perform(command, payload, waitForCompletion = false) {
    const token = ++operation
    const epoch = contextEpoch
    let resolveCompletion
    const completion = new Promise(resolve => { resolveCompletion = resolve })
    const finish = value => { resolveCompletion(value); if (finishWait === finish) finishWait = null }
    finishWait = finish
    state.busy = true; state.task = null; state.error = ''; state.errorCode = ''; state.notice = ''
    try {
      await watcher?.dispose(); watcher = null
      if (waitForCompletion && command !== 'package.import_preview' && !automatic?.canContinue()) { finish(false); return false }
      if (command === 'package.update_apply' && (previewInstallationId !== payload.installation_id || getInstallContext().workspaceDirty)) {
        state.busy = Boolean(automatic); state.notice = '游戏或模组列表已改变，请重新确认安装目标。'; finish(false); return false
      }
      const initial = await bridge.request(command, payload)
      if (disposed || token !== operation) {
        if (initial?.task_id && !terminalStates.has(initial.state)) await bridge.request('task.cancel', { task_id: initial.task_id }).catch(() => {})
        finish(false)
        return
      }
      let settled = false
      const update = snapshot => {
        if (disposed || token !== operation || settled) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        settled = true; state.busy = Boolean(automatic)
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
          if (epoch === contextEpoch) { state.updatePreview = snapshot.data; previewInstallationId = payload.installation_id }
          else { state.updatePreview = null; state.notice = '游戏或模组列表已改变，请重新确认安装目标。' }
        } else if (command === 'package.update_apply') {
          state.applied = snapshot.data; state.updatePreview = null
          state.notice = snapshot.data?.completed ? '已安装并启用此版本；旧版本保留在包库。' : '安装尚未完成，请刷新核对。'
        } else {
          state.result = snapshot.data; state.preview = null; state.notice = '已加入本地包库，尚未启用或部署。'
        }
        finish(snapshot.state === 'succeeded' && !snapshot.data?.error_code)
      }
      update(initial)
      if (!settled) watcher = bridge.watchTask(initial.task_id, update, () => {
        if (!disposed && token === operation) { settled = true; state.busy = Boolean(automatic); state.error = '导入进度连接中断，请重新选择文件核对结果。'; state.preview = null; finish(false) }
      })
      if (automatic?.cancelled && !settled) await watcher?.cancel()
    } catch {
      if (!disposed && token === operation) { state.busy = Boolean(automatic); state.error = '无法完成导入，请重新选择文件。' }
      finish(false)
    }
    if (waitForCompletion) return completion
  }
  function resetSource(source) {
    state.preview = null; state.result = null; state.applied = null; state.updatePreview = null; previewInstallationId = null
    state.sourceDisplayName = typeof source.display_name === 'string' ? source.display_name.replace(/\.(zip|dll)$/i, '').slice(0, 200) : ''
  }
  function commitRequest() {
    const payload = { preview_id: state.preview.preview_id, preview_hash: state.preview.preview_hash, display_name: state.displayName.trim() }
    if (state.preview.version?.identity || state.versionRaw || state.packageId) {
      payload.version_raw = state.versionRaw.trim() || null
      if (state.packageId) payload.package_id = state.packageId
      return ['package.import_version', payload]
    }
    return ['package.import_commit', payload]
  }
  function updateRequest(installationId) {
    const preview = state.updatePreview
    return { installation_id: installationId, release_id: preview.release_id,
      manifest_hash: preview.manifest_hash, snapshot_id: preview.snapshot_id,
      replaced_ids: preview.replacements.map(row => row.id), idempotency_key: createRequestId() }
  }
  const controller = {
    async openSource(source) {
      if (disposed || state.busy || !source?.source_handle) return
      resetSource(source)
      return perform('package.import_preview', { source_handle: source.source_handle })
    },
    async installSource(source) {
      if (disposed || state.busy || !source?.source_handle) return false
      const context = { ...getInstallContext() }, epoch = contextEpoch
      const run = automatic = { cancelled: false }
      const canContinue = () => {
        if (disposed) return false
        if (run.cancelled) { state.notice = '已取消自动安装，可在此核对文件。'; return false }
        const current = getInstallContext()
        if (!context.installationId || !current.installationId) { state.notice = '请先选择游戏，再确认安装。'; return false }
        if (context.workspaceDirty || current.workspaceDirty) { state.notice = '模组列表有未保存的更改，请先保存或撤销后再安装。'; return false }
        if (epoch !== contextEpoch || current.installationId !== context.installationId) { state.notice = '游戏或模组列表已改变，请重新确认安装目标。'; return false }
        return true
      }
      run.canContinue = canContinue
      resetSource(source)
      try {
        state.installStage = '正在检查 Mod 文件…'
        if (!await perform('package.import_preview', { source_handle: source.source_handle }, true) || !canContinue()) return false
        const preview = state.preview, version = preview?.version, candidates = preview?.version_candidates || []
        if (!preview?.can_commit || !state.displayName.trim()) return false
        const review = preview.install_review_reasons ?? preview.warnings ?? []
        if (review.length || !version?.identity || version.identity_source !== 'embedded_metadata'
          || !version.version_raw || version.version_source !== 'embedded_metadata'
          || candidates.some(row => row.match !== 'embedded_metadata' || row.comparison === 'unknown')
          || new Set(candidates.map(row => row.package_id)).size > 1) {
          state.notice = '文件或版本信息需要核对，请确认后继续安装。'; return false
        }
        const downgrade = candidates.some(row => row.comparison === 'downgrade')
        state.installStage = '正在加入本地包库…'
        if (!await perform(...commitRequest(), true) || !state.result?.release_id || !canContinue()) return false
        state.installStage = '正在检查安装目标…'
        if (!await perform('package.update_preview', { installation_id: context.installationId,
          release_id: state.result.release_id, manifest_hash: state.result.manifest_hash }, true) || !canContinue()) return false
        if (!state.updatePreview || state.updatePreview.replacements?.length || downgrade || state.updatePreview.warnings?.length) {
          state.notice = '请确认版本及替换范围后安装。'; return false
        }
        state.installStage = '正在安装并启用…'
        return Boolean(await perform('package.update_apply', updateRequest(context.installationId), true) && state.applied?.completed)
      } finally {
        if (automatic === run) { automatic = null; state.busy = false; state.installStage = '' }
      }
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
      return perform(...commitRequest())
    },
    previewUpdate(installationId) {
      if (disposed || state.busy || !installationId || !state.result?.release_id) return
      return perform('package.update_preview', { installation_id: installationId, release_id: state.result.release_id, manifest_hash: state.result.manifest_hash })
    },
    applyUpdate(installationId) {
      if (disposed || state.busy || !installationId || !state.updatePreview || getInstallContext().workspaceDirty) return
      if (previewInstallationId !== installationId) { state.updatePreview = null; state.notice = '游戏已改变，请重新预览安装。'; return }
      return perform('package.update_apply', updateRequest(installationId))
    },
    contextChanged() { contextEpoch++; state.updatePreview = null; previewInstallationId = null },
    async cancel() { if (automatic) automatic.cancelled = true; try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求未完成，请重试。' } },
    async dispose() { disposed = true; operation++; finishWait?.(false); if (state.task && !terminalStates.has(state.task.state)) await watcher?.cancel().catch(() => {}); await watcher?.dispose() },
  }
  return controller
}
