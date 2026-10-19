import { terminalStates } from '../../app/bridge/reducer.js'
import { createRequestId } from '../../app/requestId.js'

// The server owns draft content, revisions and undo history.
export function createProfileStore(bridge, state) {
  let disposed = false, generation = 0, watcher = null, pendingTask = null
  const empty = () => ({ profiles: [], current: null, packages: [], packageOffset: 0, packageDetail: null, busy: false, task: null, error: '', notice: '', importPreview: null, sourceHandle: null, exportText: '', applyPreview: null })
  Object.assign(state, { installationId: null, ...empty() })
  function accept(profile) {
    state.current = profile
    const index = state.profiles.findIndex(item => item.profile_id === profile.profile_id)
    if (index < 0) state.profiles.push(profile)
    else state.profiles.splice(index, 1, profile)
  }
  async function call(command, payload) {
    const token = generation
    const result = await bridge.request(command, payload)
    if (disposed || token !== generation) {
      if (result?.task_id && !terminalStates.has(result.state)) await bridge.request('task.cancel', { task_id: result.task_id }).catch(() => {})
      return null
    }
    if (!result?.task_id) { if (result?.error_code) throw new Error(result.message || result.error_code); return result }
    state.task = result
    if (terminalStates.has(result.state)) {
      if (result.state !== 'succeeded' || result.data?.error_code) throw new Error(result.data?.message || '操作未完成。')
      return result.data
    }
    return new Promise((resolve, reject) => {
      pendingTask = resolve
      watcher = bridge.watchTask(result.task_id, snapshot => {
        if (disposed || token !== generation) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        pendingTask = null
        if (snapshot.state === 'succeeded' && !snapshot.data?.error_code) resolve(snapshot.data)
        else reject(new Error(snapshot.data?.message || (snapshot.state === 'cancelled' ? '操作已取消。' : '操作未完成。')))
      }, reject)
    })
  }
  async function run(action) {
    if (disposed || state.busy) return
    state.busy = true; state.error = ''; state.notice = ''; state.task = null
    const token = generation
    try { return await action(() => !disposed && token === generation) }
    catch (error) {
      if (!disposed && token === generation) {
        const messages = {
          PROFILE_REVISION_CONFLICT: '配置已在其他窗口修改，请刷新配置后编辑。',
          PROFILE_SOURCE_CHANGED: '清单文件已改变，请重新选择文件并核对预览。',
          PROFILE_SOURCE_EXPIRED: '文件选择已过期，请重新选择清单。',
          PROFILE_PREVIEW_CHANGED: '清单预览已失效，请重新选择文件并核对。',
          PROFILE_UNRESOLVED: '本地版本缺失或校验失败，请导入匹配版本后刷新配置。',
          PROFILE_EXPORT_REFUSED: '无法保存清单，请选择受保护目录之外的新 JSON 文件。',
          PROFILE_MANIFEST_INVALID: '清单格式不受支持，请选择本软件导出的配置清单。',
          PACKAGE_DETAIL_LOCK_CHANGED: '发布版本与锁定哈希不匹配，请刷新包库并重新选择版本。',
          PACKAGE_DETAIL_NOT_FOUND: '本地缺少该版本，请导入匹配版本后刷新。',
          PACKAGE_DETAIL_CORRUPT: '库文件校验失败，请重新导入原始包后刷新。',
          PACKAGE_DETAIL_LIMIT: '文件详情超过本次读取限制，请缩小包内容后重新导入。',
        }
        state.error = messages[error?.code] || error.message || '配置操作未完成，请重试。'
      }
    } finally {
      if (!disposed && token === generation) {
        const finishedWatcher = watcher; watcher = null; pendingTask = null
        await finishedWatcher?.dispose()
        if (!disposed && token === generation) state.busy = false
      }
    }
  }
  const revisionPayload = () => ({ profile_id: state.current.profile_id, expected_revision: state.current.revision })
  const edit = (action, describe = () => '') => state.current && run(async valid => {
    const result = await call('profile.edit', { ...revisionPayload(), action })
    if (valid()) { accept(result); state.notice = describe(result); return result }
  })
  const selectionSummary = selections => {
    const total = selections.length, missing = selections.filter(item => item.resolved === false).length
    return `${total} 个 Mod 版本${missing ? `，其中 ${missing} 个缺少匹配文件，已保留待补齐` : ''}`
  }
  async function readSource(source, valid) {
    state.importPreview = null; state.sourceHandle = source.source_handle
    const preview = await call('profile.share_preview', { source_handle: source.source_handle, installation_id: state.installationId })
    if (valid()) { state.importPreview = preview; return preview }
  }
  async function applyProfile(profile, valid, snapshotId = null) {
    const workspace = snapshotId ? null : await call('mod.workspace', { installation_id: state.installationId })
    if (!valid()) return
    const result = await call('profile.apply_saved', { profile_id: profile.profile_id, expected_revision: profile.revision,
      snapshot_id: snapshotId || workspace.snapshot_id, idempotency_key: createRequestId() })
    if (valid() && result?.completed) { state.applyPreview = null; state.notice = `已将「${profile.name}」应用到游戏。`; return result }
    if (valid()) throw new Error(result?.message || '应用尚未完成，请刷新核对。')
  }
  return {
    async setInstallation(id) {
      const token = ++generation, previous = watcher; watcher = null
      pendingTask?.(null); pendingTask = null
      await previous?.cancel().catch(() => {}); await previous?.dispose()
      if (disposed || token !== generation) return
      Object.assign(state, { installationId: id, ...empty() })
    },
    refresh() { return state.installationId && run(async valid => {
      const result = await call('profile.list', { installation_id: state.installationId })
      if (!valid()) return
      state.profiles = result.profiles
      const id = result.selected_profile_id || state.current?.profile_id
      const selected = result.profiles.find(item => item.profile_id === id) || result.profiles[0]
      if (!selected) { state.current = null; return }
      const profile = await call('profile.get', { profile_id: selected.profile_id })
      if (valid()) accept(profile)
    }) },
    select(id) { return run(async valid => { const result = await call('profile.select', { profile_id: id }); if (valid()) { accept(result); state.exportText = '' } }) },
    create(name) { if (!name.trim() || !state.installationId) return; return run(async valid => { const result = await call('profile.create', { installation_id: state.installationId, name: name.trim() }); if (valid()) accept(result) }) },
    clone(name) { if (!state.current || !name.trim()) return; return run(async valid => { const result = await call('profile.clone', { ...revisionPayload(), name: name.trim() }); if (valid()) accept(result) }) },
    saveAs(name) { if (!state.current || !name.trim()) return; return run(async valid => {
      const clone = await call('profile.clone', { ...revisionPayload(), name: name.trim() })
      if (!valid()) return
      await call('profile.commit', { profile_id: clone.profile_id, expected_revision: clone.revision })
      const result = await call('profile.get', { profile_id: clone.profile_id })
      if (valid()) { accept(result); state.notice = '已另存为新方案，原方案保持不变。'; return result }
    }) },
    capture(name, replace = false) { if (!state.installationId || !name.trim() || replace && !state.current) return; return run(async valid => {
      const payload = { installation_id: state.installationId, name: name.trim() }
      if (replace) Object.assign(payload, revisionPayload())
      const result = await call('profile.capture', payload)
      if (valid()) { accept(result); state.notice = replace ? '已用模组页当前的列表覆盖此方案。' : '已把模组页当前的列表存为新方案。'; return result }
    }) },
    previewApply() { if (!state.current || state.current.dirty) return; return run(async valid => {
      const workspace = await call('mod.workspace', { installation_id: state.installationId })
      if (!valid()) return
      const selected = new Set(state.current.selections.filter(item => item.selected).map(item => item.release_id))
      state.applyPreview = { snapshot_id: workspace.snapshot_id, changes: workspace.items.filter(item =>
        workspace.enabled_ids.includes(item.id) !== selected.has(item.release_id)).map(item => ({ ...item, enable: selected.has(item.release_id) })) }
    }) },
    apply() { if (!state.current || !state.applyPreview) return; return run(valid => applyProfile(state.current, valid, state.applyPreview.snapshot_id)) },
    rename(name) { if (name.trim()) return edit({ op: 'rename', name: name.trim() }) },
    add(release) {
      if (disposed || state.busy) return
      if (!state.current) { state.error = '先创建或选择一个方案，再添加 Mod。'; state.notice = ''; return }
      if (release?.integrity !== 'verified') { state.error = '此版本的文件尚未通过校验，请重新导入原始文件后重试。'; state.notice = ''; return }
      return edit({ op: 'set_selection', selection: { release_id: release.release_id, manifest_hash: release.manifest_hash, selected: true } }, result => {
        const added = result.selections.find(item => item.release_id === release.release_id)
        if (!added?.selected) throw new Error('添加结果中没有找到已启用的所选版本，请刷新方案后重试。')
        const label = added.display_name || release.display_name || '所选 Mod'
        return `已将「${label}」加入「${result.name}」并启用。${added.resolved === false ? '缺少匹配文件，已保留待补齐。' : ''}`
      })
    },
    setEnabled(ids, selected) { if (ids.length) return edit({ op: 'set_enabled', release_ids: [...new Set(ids)], selected }) },
    move(ids, selected, beforeReleaseId = null) { if (ids.length) return edit({ op: 'move_selections', release_ids: [...new Set(ids)], selected, before_release_id: beforeReleaseId }, () => '顺序已更新。') },
    setGroup(ids, group) { if (ids.length) return edit({ op: 'set_group', release_ids: [...new Set(ids)], group: group.trim() || null }) },
    undo() { if (state.current?.can_undo) return edit({ op: 'undo' }) },
    redo() { if (state.current?.can_redo) return edit({ op: 'redo' }) },
    save() { if (!state.current) return; return run(async valid => {
      const summary = await call('profile.commit', revisionPayload())
      if (!valid()) return
      const result = await call('profile.get', { profile_id: summary.profile_id })
      if (valid()) { accept(result); state.notice = '已保存。点击“应用到游戏”后生效。' }
    }) },
    packages(offset = 0) { return run(async valid => { const result = await call('package.list', { offset, limit: 20 }); if (valid()) { state.packages = result.releases; state.packageOffset = offset } }) },
    details(release, offset = 0) { return run(async valid => {
      const previousHash = state.packageDetail?.detail_hash
      state.packageDetail = null
      const result = await call('package.detail', { release_id: release.release_id, manifest_hash: release.manifest_hash, offset, limit: 20 })
      if (!valid()) return
      if (offset && previousHash !== result.detail_hash) {
        const refreshed = await call('package.detail', { release_id: release.release_id, manifest_hash: release.manifest_hash, offset: 0, limit: 20 })
        if (valid()) { state.packageDetail = refreshed; state.notice = '文件归属信息已变化，已重新读取第一页。' }
      } else state.packageDetail = result
    }) },
    chooseImport() { return state.installationId && run(async valid => {
      state.importPreview = null; state.sourceHandle = null
      const selected = await call('profile.share_choose', {})
      if (!valid()) return
      if (!selected.source_handle) return
      const preview = await readSource(selected, valid)
      if (valid()) {
        state.importPreview = preview
        state.notice = `已读取「${preview.name || '方案清单'}」：${selectionSummary(preview.selections || [])}。请核对后确认导入。`
        return preview
      }
    }) },
    openSource(source) { return state.installationId && source?.source_handle && run(valid => readSource(source, valid)) },
    importCommit(name, apply = false) { if (!state.sourceHandle || !state.importPreview || !name.trim()) return; return run(async valid => {
      const result = await call('profile.share_import', { source_handle: state.sourceHandle, preview_hash: state.importPreview.preview_hash, installation_id: state.installationId, name: name.trim() })
      if (valid()) {
        accept(result); state.importPreview = null; state.sourceHandle = null
        state.notice = `已导入并切换到「${result.name}」：${selectionSummary(result.selections || [])}。尚未应用到游戏。`
        if (apply) return await applyProfile(result, valid)
        return result
      }
    }) },
    exportPreview() { if (!state.current) return; return run(async valid => { const result = await call('profile.export', { profile_id: state.current.profile_id }); if (valid()) state.exportText = JSON.stringify(result.manifest, null, 2) }) },
    exportFile() { if (!state.current) return; return run(async valid => { const result = await call('profile.save_export', { profile_id: state.current.profile_id }); if (valid()) state.notice = result.saved ? '清单已导出。' : '已取消导出。' }) },
    exportShare(kind) { if (!state.current) return; return run(async valid => {
      const result = await call('profile.share_export', { profile_id: state.current.profile_id, kind })
      if (valid()) state.notice = result.saved ? (kind === 'bundle' ? '整合包已导出，包含方案与锁定版本的 Mod 文件。' : '清单已导出，接收者需要自行补齐匹配的 Mod。') : '已取消导出。'
    }) },
    async cancel() { try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求未完成，请重试。' } },
    async dispose() { disposed = true; generation++; pendingTask?.(null); pendingTask = null; const previous = watcher; watcher = null; await previous?.cancel().catch(() => {}); await previous?.dispose() },
  }
}
