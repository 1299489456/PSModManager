import { terminalStates } from '../../app/bridge/reducer.js'

const empty = () => ({ busy: false, task: null, preview: null, selectedPaths: [], offset: 0,
  displayName: '', version: '', result: null, confirmedFileCount: 0, error: '', notice: '', operation: '', issues: [] })
const messages = {
  ADOPTION_SCAN_REQUIRED: '尚未扫描此安装。点击“扫描并读取清单”开始。',
  ADOPTION_SCAN_RUNNING: '文件扫描正在进行，请等待扫描完成后再读取清单。',
  ADOPTION_SCAN_INCOMPLETE: '文件扫描未完成，暂时不能接管。请检查下方原因，处理后重新扫描。',
  ADOPTION_READ_FAILED: '无法完整读取游戏文件，文件可能已变化、被占用或超出读取限制。请重新扫描。',
}
const issueLabels = {
  WORKER_PROTOCOL_INVALID: '程序集读取程序没有返回有效结果',
  WORKER_INITIALIZATION_FAILED: '程序集读取程序未能启动',
  SANDBOX_UNAVAILABLE: '程序集隔离环境不可用',
  RUNTIME_UNAVAILABLE: '未找到程序集读取运行时',
  HELPER_UNAVAILABLE: '程序集读取程序缺失',
  WORKER_BUSY: '程序集读取程序正忙',
  CANCELLED: '扫描已取消',
  FILE_UNREADABLE: '文件无法读取',
  FILE_DRIFT: '文件在扫描期间变化',
  REPARSE_POINT: '目录中存在链接文件',
  READ_TIME_LIMIT: '读取超时，请重试或检查文件占用',
  READ_SIZE_LIMIT: '文件超过读取大小限制，请分批添加',
  DIRECTORY_UNREADABLE: '目录无法读取，请检查权限和占用',
  FILE_LIMIT: '目录文件过多，请分批添加',
  DIRECTORY_LIMIT: '子目录过多或层级过深，请分批添加',
}
const failure = (phase, error) => messages[error?.code] || (phase === 'preview'
  ? '未能读取接管清单。请重试；若仍失败，请查看扫描结果中的错误原因。'
  : phase === 'commit'
    ? '接管未完成，来源文件或包库可能已变化或不可用。请重新扫描并读取预览，核对后再次确认。'
    : '接管预览已不可用。请重新读取预览并核对文件。')
const taskError = data => Object.assign(new Error('failed'), { code: data?.error_code, issues: data?.issues })

export function createAdoptionStore(bridge, state) {
  let generation = 0, disposed = false, watcher = null, finishPending = null, cancelRequested = false
  const knownFiles = new Map()
  Object.assign(state, { installationId: null, ...empty() })
  const invalidate = () => { state.preview = null; state.selectedPaths = []; state.offset = 0; knownFiles.clear() }
  function accept(page, offset, expected = null) {
    if (!page || typeof page.snapshot_id !== 'string' || !/^[a-f0-9]{64}$/.test(page.manifest_hash) || page.coverage !== 'complete'
      || !Array.isArray(page.files) || page.files.length > 20 || !Number.isSafeInteger(page.total) || page.total < 0
      || (page.next_offset != null && (!Number.isSafeInteger(page.next_offset) || page.next_offset <= offset || page.next_offset >= page.total))
      || (expected && (page.snapshot_id !== expected.snapshot_id || page.manifest_hash !== expected.manifest_hash || page.total !== expected.total))) throw new Error('invalid preview')
    for (const file of page.files) {
      if (typeof file.relative_path !== 'string' || !/^[a-f0-9]{64}$/.test(file.sha256)) throw new Error('invalid file')
      knownFiles.set(file.relative_path, file)
    }
    state.preview = page; state.offset = offset
  }
  async function call(command, payload, token) {
    const initial = await bridge.request(command, payload)
    if (disposed || token !== generation) {
      if (initial?.task_id && !terminalStates.has(initial.state)) await bridge.request('task.cancel', { task_id: initial.task_id }).catch(() => {})
      return null
    }
    if (!initial?.task_id) return initial
    state.task = initial
    const terminal = snapshot => {
      if (snapshot.state === 'cancelled') { const error = new Error('cancelled'); error.cancelled = true; throw error }
      if (snapshot.state !== 'succeeded' || snapshot.data?.error_code) throw taskError(snapshot.data)
      return snapshot.data
    }
    if (terminalStates.has(initial.state)) return terminal(initial)
    return new Promise((resolve, reject) => {
      finishPending = resolve
      let settled = false
      const update = snapshot => {
        if (disposed || token !== generation || settled) return
        state.task = snapshot
        if (!terminalStates.has(snapshot.state)) return
        settled = true; finishPending = null
        try { resolve(terminal(snapshot)) } catch (error) { reject(error) }
      }
      watcher = bridge.watchTask(initial.task_id, update, reject)
      if (cancelRequested) void watcher.cancel().catch(reject)
    })
  }
  async function run(phase, action) {
    if (disposed || state.busy) return
    const token = generation
    state.busy = true; state.error = ''; state.notice = ''; state.issues = []; state.task = null; cancelRequested = false
    try { await action(() => !disposed && token === generation, token) }
    catch (error) {
      if (!disposed && token === generation) {
        invalidate()
        if (error.cancelled) state.notice = '已取消接管。需要继续时，请重新读取预览。'
        else {
          state.error = failure(phase, error)
          state.issues = Array.isArray(error.issues) ? error.issues.slice(0, 5).map(issue => ({
            path: typeof issue.path === 'string' ? issue.path.slice(0, 512) : '',
            reason: issueLabels[issue.code] || '文件未能完整读取',
            code: typeof issue.code === 'string' && /^[A-Z0-9_]{1,80}$/.test(issue.code) ? issue.code : '',
          })) : []
        }
      }
    } finally {
      if (!disposed && token === generation) {
        const previous = watcher; watcher = null; finishPending = null
        await previous?.dispose()
        if (!disposed && token === generation) { state.busy = false; state.operation = '' }
      }
    }
  }
  return {
    async setInstallation(id) {
      const token = ++generation, previous = watcher; watcher = null
      finishPending?.(null); finishPending = null
      await previous?.cancel().catch(() => {}); await previous?.dispose()
      if (disposed || token !== generation) return
      invalidate(); Object.assign(state, { installationId: id, ...empty() })
    },
    preview() {
      if (!state.installationId) return
      return run('preview', async (valid, token) => {
        invalidate(); state.result = null
        state.operation = 'preview'
        const result = await call('package.adopt_preview', { installation_id: state.installationId }, token)
        if (valid()) accept(result, 0)
      })
    },
    page(offset) {
      if (!state.preview || !Number.isSafeInteger(offset) || offset < 0 || offset >= state.preview.total) return
      const previous = state.preview
      return run('page', async (valid, token) => {
        const result = await call('package.adopt_files', { snapshot_id: previous.snapshot_id, offset, limit: 20 }, token)
        if (valid()) accept(result, offset, previous)
      })
    },
    toggle(path, selected) {
      if (disposed || state.busy || !state.preview || knownFiles.get(path)?.lifecycle !== 'immutable' || typeof selected !== 'boolean') return
      const paths = new Set(state.selectedPaths)
      if (selected) {
        if (paths.size >= 128 && !paths.has(path)) { state.error = '每次最多选择 128 个文件，请分次接管。'; return }
        paths.add(path)
      } else paths.delete(path)
      state.selectedPaths = [...paths].sort(); state.error = ''
    },
    commit() {
      if (!state.preview || !state.displayName.trim() || !state.selectedPaths.length || state.selectedPaths.length > 128) return
      const payload = { snapshot_id: state.preview.snapshot_id, manifest_hash: state.preview.manifest_hash,
        paths: [...state.selectedPaths], display_name: state.displayName.trim(), version_raw: state.version.trim() || null }
      return run('commit', async (valid, token) => {
        state.result = null; state.confirmedFileCount = 0
        const result = await call('package.adopt', payload, token)
        if (valid()) {
          if (!result?.release?.release_id || result.snapshot_id !== payload.snapshot_id || result.deployment_generation !== null) throw new Error('invalid result')
          state.result = result; state.confirmedFileCount = payload.paths.length
          invalidate(); state.notice = '已加入本地包库，游戏中的原文件保持原样。可在模组页管理。'
        }
      })
    },
    async cancel() {
      if (!state.busy) return
      cancelRequested = true
      try { await watcher?.cancel() } catch { if (!disposed) state.error = '取消请求未完成，请再次点击取消并等待任务结果。' }
    },
    async dispose() {
      disposed = true; generation++; finishPending?.(null); finishPending = null
      const previous = watcher; watcher = null
      await previous?.cancel().catch(() => {}); await previous?.dispose()
    },
  }
}
