import { createModManagerRuntime, modActionText } from '../catalog/modManagerRuntime.js'
import { createModWorkspaceRuntime } from '../catalog/modWorkspaceRuntime.js'

const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const validId = value => typeof value === 'string' && idPattern.test(value)
const fields = ['suggestion_id', 'installation_id', 'release_id', 'manifest_hash', 'display_name', 'version_raw', 'action', 'reason']
const workspaceFields = ['kind', 'suggestion_id', 'installation_id', 'workspace_id', 'snapshot_id', 'display_name', 'version_raw', 'action', 'reason']
const actions = new Set(['install', 'enable', 'disable'])

export function readSuggestions(value = [], installationId) {
  if (!Array.isArray(value) || value.length > 4 || value.some(item => !item || typeof item !== 'object'
    || Object.keys(item).some(key => !(item.kind === 'workspace' ? workspaceFields : fields).includes(key)) || (item.kind === 'workspace' ? workspaceFields : fields).some(key => !Object.hasOwn(item, key))
    || !validId(item.suggestion_id) || !validId(item.installation_id)
    || installationId !== undefined && item.installation_id !== installationId
    || (item.kind === 'workspace' ? !validId(item.workspace_id) || typeof item.snapshot_id !== 'string' || !/^[a-f0-9]{64}$/.test(item.snapshot_id) || !['enable', 'disable'].includes(item.action)
      : !validId(item.release_id) || typeof item.manifest_hash !== 'string' || !/^[a-f0-9]{64}$/.test(item.manifest_hash))
    || typeof item.display_name !== 'string' || item.display_name.length > 200
    || !(item.version_raw === null || typeof item.version_raw === 'string' && item.version_raw.length <= 200)
    || !actions.has(item.action) || typeof item.reason !== 'string' || item.reason.length > 300)
    || new Set(value.map(item => item.suggestion_id)).size !== value.length) throw new Error('Invalid AI suggestions response')
  return value.map(item => Object.fromEntries((item.kind === 'workspace' ? workspaceFields : fields).map(field => [field, item[field]])))
}

function readResolution(result, card) {
  const request = result?.request
  if (card.kind === 'workspace') {
    const keys = ['installation_id', 'snapshot_id', 'enabled_ids', 'idempotency_key']
    if (result.kind !== 'workspace' || !request || Object.keys(request).length !== keys.length || keys.some(key => !Object.hasOwn(request, key))
      || request.installation_id !== card.installation_id || request.snapshot_id !== card.snapshot_id || !validId(request.idempotency_key)
      || !Array.isArray(request.enabled_ids) || request.enabled_ids.length > 200 || request.enabled_ids.some(id => !validId(id))
      || new Set(request.enabled_ids).size !== request.enabled_ids.length || request.enabled_ids.includes(card.workspace_id) !== (card.action === 'enable')) throw new Error('建议返回不完整，请重新提问后再试。')
    return { request }
  }
  const keys = ['installation_id', 'release_id', 'manifest_hash', 'action', 'idempotency_key']
  if (!request || Object.keys(request).length !== keys.length || keys.some(key => !Object.hasOwn(request, key))
    || request.installation_id !== card.installation_id || request.release_id !== card.release_id
    || request.manifest_hash !== card.manifest_hash || request.action !== card.action
    || !validId(request.idempotency_key) || !Number.isSafeInteger(result.offset) || result.offset < 0) {
    throw new Error('建议返回不完整，请重新提问后再试。')
  }
  return { request, offset: result.offset }
}

function errorText(error, fallback) {
  return error?.code && typeof error.message === 'string' && error.message.length <= 300 ? error.message : fallback
}

// Suggestion generation is passive. This runtime starts a normal Mod action only from execute().
export function createAiActionRuntime(bridge, state = {}, onChanged = () => {}) {
  let disposed = false, epoch = 0, manager = null, managerState = null, acceptedTask = null
  Object.assign(state, { cards: [], sessionId: null, installationId: null, busy: false, activeId: null, resolving: false, cancelling: false, manager: null })
  const current = token => !disposed && token === epoch

  function stopManager() {
    const previous = manager
    manager = null
    if (previous) { void previous.cancel(); previous.dispose() }
    managerState = null
    acceptedTask = null
    state.manager = null
  }

  function clear() {
    ++epoch
    stopManager()
    Object.assign(state, { cards: [], sessionId: null, installationId: null, busy: false, activeId: null, resolving: false, cancelling: false })
  }

  function setSuggestions(sessionId, installationId, suggestions) {
    if (disposed || state.busy) return false
    const verified = readSuggestions(suggestions, installationId)
    if (!validId(sessionId) || verified.length && !validId(installationId)) return false
    // A second get for the same reply must retain confirmed results and stable retry keys.
    if (state.sessionId === sessionId && state.installationId === installationId) {
      const previous = new Map(state.cards.map(card => [card.suggestion_id, card]))
      state.cards = verified.map(card => previous.has(card.suggestion_id) && Object.keys(card).every(key => card[key] === previous.get(card.suggestion_id)[key])
        ? { ...card, ...previous.get(card.suggestion_id) } : { ...card, status: 'ready', result: '', error: '', idempotencyKey: null })
    } else {
      clear()
      state.sessionId = sessionId
      state.installationId = installationId
      state.cards = verified.map(card => ({ ...card, status: 'ready', result: '', error: '', idempotencyKey: null }))
    }
    return true
  }

  async function execute(suggestionId) {
    const card = state.cards.find(item => item.suggestion_id === suggestionId)
    if (disposed || state.busy || !card || card.status === 'completed' || !state.sessionId || card.installation_id !== state.installationId) return false
    const token = ++epoch, sessionId = state.sessionId
    card.error = ''; card.result = ''; card.status = 'running'
    Object.assign(state, { busy: true, activeId: suggestionId, resolving: true, cancelling: false })
    try {
      const resolution = readResolution(await bridge.request('ai.suggestion.resolve', { session_id: sessionId, suggestion_id: suggestionId }), card)
      if (!current(token)) return false
      if (card.idempotencyKey && card.idempotencyKey !== resolution.request.idempotency_key) throw new Error('建议已改变，请重新提问后再试。')
      card.idempotencyKey = resolution.request.idempotency_key
      state.resolving = false
      managerState = {}
      const guardedBridge = {
        async request(command, payload) {
          if (command === 'mod.workspace.save') {
            if (card.kind !== 'workspace' || payload.snapshot_id !== resolution.request.snapshot_id
              || JSON.stringify(payload.enabled_ids) !== JSON.stringify(resolution.request.enabled_ids)) throw new Error('Mod 列表已改变，请重新分析。')
            payload = { ...resolution.request }
          }
          const result = await bridge.request(command, payload)
          if (['mod.action', 'mod.workspace.save'].includes(command) && result?.task_id) {
            if (!current(token)) { try { await bridge.request('task.cancel', { task_id: result.task_id }) } catch {} }
            else acceptedTask = result.task_id
          }
          return result
        },
        watchTask(taskId, update, onError) {
          const cancelAccepted = () => { if (taskId === acceptedTask) void bridge.request('task.cancel', { task_id: taskId }).catch(() => {}) }
          try { return bridge.watchTask(taskId, update, error => { cancelAccepted(); onError(Object.assign(new Error(errorText(error, 'Mod 操作连接中断，请在模组页核对当前状态。')), { code: error?.code })) }) }
          catch (error) { cancelAccepted(); throw error }
        },
      }
      manager = card.kind === 'workspace' ? createModWorkspaceRuntime(guardedBridge, managerState) : createModManagerRuntime(guardedBridge, managerState)
      state.manager = managerState
      if (!await manager.setInstallation(card.installation_id)) throw new Error(managerState.error || '无法核对 Mod 状态，请刷新后重试。')
      if (!current(token)) return false
      if (card.kind === 'workspace') {
        if (managerState.snapshotId !== card.snapshot_id) throw new Error('Mod 列表已改变，请重新分析后执行建议。')
        const item = managerState.items.find(row => row.id === card.workspace_id)
        if (!item || item.manageable === false || !['enabled', 'disabled', 'not_installed'].includes(item.state)) throw new Error('此 Mod 当前无法操作，请刷新列表。')
        if (!manager.move([card.workspace_id], card.action === 'enable') || !await manager.save()) throw new Error(managerState.error || 'Mod 状态尚未确认，请刷新列表。')
        if (!current(token)) return false
        const enabled = managerState.enabledIds.includes(card.workspace_id)
        if (enabled !== (card.action === 'enable')) throw new Error('操作后状态不匹配，请在模组页核对。')
        card.status = 'completed'
        card.result = `${modActionText(card.action)}完成。已核对当前 Mod 文件状态${card.action === 'disable' ? '，恢复副本已保留' : ''}。`
        onChanged(card)
        return true
      }
      if (resolution.offset !== 0 && !await manager.refresh(resolution.offset)) throw new Error(managerState.error || '无法读取此 Mod，请刷新后重试。')
      if (!current(token)) return false
      const item = managerState.items.find(row => row.release_id === card.release_id && row.manifest_hash === card.manifest_hash)
      if (!item || !manager.canAct(item, card.action)) throw new Error(item?.message || '此建议与当前 Mod 状态不一致，请重新提问或使用模组页操作。')
      const completed = await manager.perform(item, card.action, card.idempotencyKey)
      if (!current(token)) return false
      if (!completed) throw new Error(managerState.error || '操作结果未确认，请在模组页核对当前状态。')
      card.status = 'completed'
      card.result = `${modActionText(card.action)}完成。已核对当前 Mod 文件状态。`
      onChanged(card)
      return true
    } catch (error) {
      if (current(token)) { card.status = state.cancelling ? 'cancelled' : 'failed'; card.error = errorText(error, error.message?.startsWith('建议') || managerState ? error.message : '无法执行建议，请重试或使用模组页操作。') }
      return false
    } finally {
      if (current(token)) { state.busy = false; state.activeId = null; state.resolving = false; state.cancelling = false; stopManager() }
    }
  }

  async function cancel() {
    if (disposed || !state.busy || state.cancelling) return false
    state.cancelling = true
    if (managerState?.busy && acceptedTask) {
      const token = epoch, activeManager = manager, activeState = managerState
      const cancelled = await activeManager.cancel()
      if (current(token) && !cancelled) {
        state.cancelling = false
        const card = state.cards.find(item => item.suggestion_id === state.activeId)
        if (card) card.error = activeState.error || '取消请求未完成，操作仍在处理中。'
      }
      return cancelled
    }
    const card = state.cards.find(item => item.suggestion_id === state.activeId)
    ++epoch
    stopManager()
    if (card) { card.status = 'cancelled'; card.error = '已取消建议操作。' }
    Object.assign(state, { busy: false, activeId: null, resolving: false, cancelling: false })
    return true
  }

  function dispose() { if (!disposed) { clear(); disposed = true } }
  return { setSuggestions, execute, cancel, clear, dispose }
}
