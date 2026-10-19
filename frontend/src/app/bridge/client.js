import { createRequestId } from '../requestId.js'
import { reduceTask, terminalStates } from './reducer.js'

const payloadFields = {
  'app.info': [], 'app.check': [], 'task.snapshot': ['task_id'], 'task.cancel': ['task_id'],
  'app.theme.get': [], 'app.theme.set': ['theme_id'],
  'app.storage.status': [], 'app.storage.choose': [], 'app.storage.accept': [],
  'app.storage.start': ['target'], 'app.storage.restart': [],
  'window.control': ['op'],
  'nexus.settings.get': [], 'nexus.settings.save': ['api_key', 'remember'],
  'nexus.settings.clear': [], 'nexus.account': [], 'nexus.browse': ['view'],
  'nexus.files': ['mod_id'], 'nexus.mod': ['mod_id'], 'nexus.translate': ['mod_id'],
  'nexus.download': ['mod_id', 'file_id'], 'nexus.open': ['page'],
  'nexus.protocol.status': [], 'nexus.protocol.register': ['replace'], 'nexus.protocol.unregister': [],
  'nexus.protocol.choose_default': [], 'nexus.inbox.list': [], 'nexus.inbox.download': ['id'], 'nexus.inbox.discard': ['id'],
  'ai.settings.get': [], 'ai.settings.save': ['provider', 'base_url', 'model', 'remember'],
  'ai.credentials.clear': [], 'ai.test': [], 'ai.chat.send': ['question'],
  'ai.chat.get': ['session_id'], 'ai.chat.clear': ['session_id'], 'ai.chat.export': ['session_id'],
  'ai.logs.list': ['installation_id'],
  'ai.diagnostics.sources': [],
  'ai.diagnostics.read': ['source_id'],
  'ai.diagnostics.preview': ['source_ids'],
  'ai.diagnostics.send': ['snapshot_id', 'question'],
  'ai.suggestion.resolve': ['session_id', 'suggestion_id'],
  'game.launch': ['installation_id'], 'game.status': ['installation_id'],
  'loader.status': ['installation_id'], 'loader.install': ['installation_id'],
  'loader.repair': ['installation_id'],
  'loader.choose': ['installation_id'], 'loader.install_local': ['installation_id', 'source_handle'],
  'mod.list': ['installation_id'],
  'mod.workspace': ['installation_id'],
  'mod.workspace.detail': ['installation_id', 'id'],
  'mod.workspace.save': ['installation_id', 'snapshot_id', 'enabled_ids', 'idempotency_key'],
  'mod.action': ['installation_id', 'release_id', 'manifest_hash', 'action', 'idempotency_key'],
  'config.read': ['installation_id', 'adapter_id'],
  'config.save': ['installation_id', 'adapter_id', 'expected_revision', 'changes'],
  'task.subscribe': ['task_id', 'after_sequence'], 'task.events': ['subscription_id', 'after_sequence'],
  'task.unsubscribe': ['subscription_id'],
  'installation.choose': ['kind'], 'installation.discover': ['source_handles'], 'installation.auto_discover': [],
  'installation.select': ['candidate_id'], 'installation.use': ['installation_id'], 'installation.list': [],
  'inventory.scan': ['installation_id'], 'inventory.list': ['installation_id'],
  'inventory.detail': ['installation_id', 'entry_id'], 'inventory.contents': ['installation_id', 'entry_id'],
  'inventory.graph': ['installation_id'], 'diagnostics.explain': ['installation_id'],
  'logs.watch': ['installation_id'], 'logs.sources': ['installation_id'],
  'session.list': ['installation_id'], 'evidence.list': ['installation_id'],
  'evidence.read': ['installation_id', 'evidence_id'],
  'package.choose': ['kind'], 'package.import_preview': ['source_handle'],
  'package.list': [],
  'package.detail': ['release_id', 'manifest_hash'],
  'package.adopt_preview': ['installation_id'], 'package.adopt_files': ['snapshot_id'],
  'package.adopt': ['snapshot_id', 'manifest_hash', 'paths', 'display_name'],
  'profile.list': ['installation_id'], 'profile.create': ['installation_id', 'name'],
  'profile.capture': ['installation_id', 'name'],
  'profile.share_export': ['profile_id', 'kind'], 'profile.share_choose': [],
  'profile.share_preview': ['source_handle', 'installation_id'],
  'profile.share_import': ['source_handle', 'preview_hash', 'installation_id', 'name'],
  'profile.apply_saved': ['profile_id', 'expected_revision', 'snapshot_id', 'idempotency_key'],
  'package.import_version': ['preview_id', 'preview_hash', 'display_name'],
  'package.update_preview': ['installation_id', 'release_id', 'manifest_hash'],
  'package.update_apply': ['installation_id', 'release_id', 'manifest_hash', 'snapshot_id', 'replaced_ids', 'idempotency_key'],
  'profile.get': ['profile_id'], 'profile.select': ['profile_id'],
  'profile.edit': ['profile_id', 'expected_revision', 'action'],
  'profile.clone': ['profile_id', 'expected_revision', 'name'],
  'profile.delete': ['profile_id', 'expected_revision'],
  'profile.commit': ['profile_id', 'expected_revision'],
  'compatibility.check': ['profile_id', 'expected_revision'],
  'plan.create': ['profile_id', 'expected_revision', 'idempotency_key'],
  'plan.inspect': ['plan_id', 'plan_hash'], 'plan.approve': ['plan_id', 'plan_hash'],
  'assistant.propose_plan': ['profile_id', 'expected_revision', 'idempotency_key'],
  'assistant.inspect_plan': ['plan_id', 'plan_hash'],
  'plan.apply': ['plan_id', 'plan_hash', 'approval_id'], 'assistant.apply_plan': ['plan_id', 'plan_hash', 'approval_id'],
  'deployment.status': ['installation_id'], 'deployment.recover': ['installation_id'],
  'deployment.history': ['installation_id'], 'deployment.conflicts.recheck': ['installation_id'],
  'deployment.rollback.create': ['installation_id', 'transaction_id', 'expected_generation', 'idempotency_key'],
  'deployment.rollback': ['plan_id', 'plan_hash', 'approval_id'],
  'profile.export': ['profile_id'], 'profile.save_export': ['profile_id'],
  'profile.choose': [], 'profile.import_preview': ['source_handle', 'installation_id'],
  'profile.import_commit': ['source_handle', 'preview_hash', 'installation_id', 'name'],
  'package.import_files': ['preview_id'],
  'package.import_commit': ['preview_id', 'preview_hash', 'display_name'],
}
const optionalFields = {
  'nexus.download': ['nxm_url'], 'nexus.open': ['mod_id', 'file_id', 'url'], 'nexus.browse': ['offset', 'query'],
  'profile.capture': ['profile_id', 'expected_revision'],
  'package.import_version': ['version_raw', 'package_id'],
  'ai.settings.save': ['api_key'],
  'ai.chat.send': ['selected_text', 'selected_label', 'session_id', 'installation_id', 'selected_evidence_id'],
  'ai.logs.list': ['after', 'limit'],
  'ai.diagnostics.sources': ['installation_id'],
  'ai.diagnostics.read': ['installation_id', 'before'],
  'ai.diagnostics.preview': ['installation_id', 'include_context'],
  'ai.diagnostics.send': ['session_id'],
  'mod.list': ['offset', 'limit'],
  'mod.workspace.detail': ['offset', 'limit'],
  'config.save': ['backup'],
  'inventory.scan': ['force'], 'inventory.list': ['generation', 'offset', 'limit'],
  'inventory.detail': ['generation'], 'inventory.contents': ['generation', 'offset', 'limit'],
  'inventory.graph': ['generation', 'offset', 'limit'],
  'diagnostics.explain': ['generation', 'session_id', 'offset', 'limit', 'snapshot_id'],
  'logs.watch': ['user_logs'], 'session.list': ['after', 'limit'],
  'evidence.list': ['after', 'limit', 'session_id', 'group_id'], 'evidence.read': ['offset', 'limit'],
  'package.import_files': ['offset', 'limit'], 'package.import_commit': ['version_raw'],
  'package.list': ['offset', 'limit'],
  'package.detail': ['offset', 'limit'], 'package.adopt_files': ['offset', 'limit'],
  'package.adopt': ['version_raw', 'package_id'],
  'compatibility.check': ['profile_kind', 'enable_release_ids', 'disable_release_ids'],
  'plan.inspect': ['offset', 'limit'],
  'assistant.inspect_plan': ['offset', 'limit'],
  'deployment.status': ['plan_id'],
  'deployment.history': ['offset', 'limit'],
}
const windowOperations = ['state', 'minimize', 'toggle_maximize', 'close', 'drag', 'resize_n', 'resize_s', 'resize_e', 'resize_w', 'resize_ne', 'resize_nw', 'resize_se', 'resize_sw']
export class BridgeError extends Error {
  constructor(code, message) { super(typeof message === 'string' && message.length > 0 && message.length <= 300 ? message : '本地操作未能完成，请重试或查看本地诊断。'); this.code = code }
}
export function createBridge({ transport, pollInterval = 200 } = {}) {
  const watchers = new Set()
  let closed = false
  async function request(command, payload = {}) {
    if (closed) throw new BridgeError('BRIDGE_CLOSED')
    return send(command, payload)
  }
  async function send(command, payload) {
    const fields = Object.hasOwn(payloadFields, command) ? payloadFields[command] : null
    const allowed = [...(fields || []), ...(optionalFields[command] || [])]
    if (!fields || !payload || Array.isArray(payload) || typeof payload !== 'object' || Object.keys(payload).some(key => !allowed.includes(key)) || fields.some(key => !Object.hasOwn(payload, key))) throw new BridgeError('INVALID_REQUEST')
    if (command === 'ai.chat.send' && Object.hasOwn(payload, 'selected_evidence_id') && !Object.hasOwn(payload, 'installation_id')) throw new BridgeError('INVALID_REQUEST')
    for (const key of Object.keys(payload)) {
      if (command === 'app.storage.start' && key === 'target') {
        if (typeof payload[key] !== 'string' || !payload[key].trim() || payload[key].length > 4096) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (command.startsWith('nexus.')) {
        const value = payload[key]
        const valid = key === 'api_key' ? typeof value === 'string' && value.trim().length > 0 && value.length <= 4096
          : ['remember', 'replace'].includes(key) ? typeof value === 'boolean'
          : key === 'id' ? typeof value === 'string' && /^[a-f0-9]{32}$/.test(value)
          : key === 'view' ? ['latest_updated', 'latest_added', 'downloads', 'endorsements'].includes(value)
          : ['mod_id', 'file_id'].includes(key) ? Number.isSafeInteger(value) && value > 0
          : key === 'offset' ? Number.isSafeInteger(value) && value >= 0 && value <= 10000
          : key === 'query' ? typeof value === 'string' && value.length <= 100
          : key === 'nxm_url' ? typeof value === 'string' && value.length <= 4096
          : key === 'url' ? payload.page === 'link' && typeof value === 'string' && value.length <= 2048 && /^https?:\/\/[^\s/@]+(?:[/?#]\S*)?$/i.test(value)
          : key === 'page' && ['game', 'keys', 'mod', 'files', 'link'].includes(value)
        if (!valid) throw new BridgeError('INVALID_REQUEST')
        if (command === 'nexus.open' && (payload.page === 'link') !== Object.hasOwn(payload, 'url')) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (command.startsWith('ai.')) {
        const value = payload[key]
        let valid = false
        if (key === 'provider') valid = value === 'openai_compatible'
        else if (key === 'remember') valid = typeof value === 'boolean'
        else if (key === 'include_context') valid = typeof value === 'boolean'
        else if (key === 'source_ids') valid = Array.isArray(value) && value.length >= 1 && value.length <= 3 && new Set(value).size === value.length && value.every(id => typeof id === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id))
        else if (key === 'api_key') valid = value === null || typeof value === 'string' && value.length <= 4096
        else if (key === 'model') valid = typeof value === 'string' && value.trim().length > 0 && value.length <= 200
        else if (key === 'question') valid = typeof value === 'string' && value.trim().length > 0 && value.length <= 2000
        else if (key === 'selected_text') valid = typeof value === 'string' && value.length <= 4000
        else if (key === 'selected_label') valid = typeof value === 'string' && value.length <= 120
        else if (['session_id', 'suggestion_id', 'installation_id', 'selected_evidence_id', 'snapshot_id', 'source_id'].includes(key)) valid = key === 'session_id' && command === 'ai.chat.send' && value === null || typeof value === 'string' && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(value)
        else if (command === 'ai.diagnostics.read' && key === 'before') valid = Number.isSafeInteger(value) && value >= 0
        else if (command === 'ai.logs.list' && ['after', 'limit'].includes(key)) valid = Number.isSafeInteger(value) && value >= (key === 'limit' ? 1 : 0) && value <= (key === 'limit' ? 20 : Number.MAX_SAFE_INTEGER)
        else if (key === 'base_url' && typeof value === 'string' && value.length <= 2048 && !/[?#]/.test(value)) {
          try {
            const url = new URL(value)
            valid = Boolean(url.hostname) && !url.username && !url.password && (url.protocol === 'https:' || url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))
          } catch { valid = false }
        }
        if (!valid) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (command.startsWith('config.')) {
        if (key === 'adapter_id') { if (payload[key] !== 'pswallpaper') throw new BridgeError('INVALID_REQUEST'); continue }
        if (key === 'backup') { if (typeof payload[key] !== 'boolean') throw new BridgeError('INVALID_REQUEST'); continue }
        if (key === 'expected_revision') { if (typeof payload[key] !== 'string' || !/^[a-f0-9]{64}$/.test(payload[key])) throw new BridgeError('INVALID_REQUEST'); continue }
        if (key === 'changes') {
          const changes = payload[key]
          if (!changes || typeof changes !== 'object' || Array.isArray(changes) || Object.keys(changes).length < 1 || Object.keys(changes).length > 3 || Object.entries(changes).some(([field, value]) => field === 'WallpaperFit' ? !['cover', 'fit', 'stretch'].includes(value) : field === 'WallpaperBrightness' ? !Number.isSafeInteger(value) || value < 0 || value > 100 : field === 'LogToFile' ? typeof value !== 'boolean' : true)) throw new BridgeError('INVALID_REQUEST')
          continue
        }
      }
      if (command === 'window.control' && key === 'op') {
        if (!windowOperations.includes(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'theme_id') {
        if (!['obsidian-cyan', 'graphite-ash', 'win11-mica'].includes(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'profile_kind') {
        if (!['draft', 'committed'].includes(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'enable_release_ids' || key === 'disable_release_ids') {
        if (!Array.isArray(payload[key]) || payload[key].length > 200 || payload[key].some(id => typeof id !== 'string' || !id)) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'enabled_ids' || key === 'replaced_ids') {
        if (!Array.isArray(payload[key]) || payload[key].length > 200 || new Set(payload[key]).size !== payload[key].length || payload[key].some(id => typeof id !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(id))) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'expected_revision') {
        if (!Number.isSafeInteger(payload[key]) || payload[key] < 0) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'action') {
        if (command === 'mod.action') {
          if (!['install', 'enable', 'disable', 'uninstall'].includes(payload[key])) throw new BridgeError('INVALID_REQUEST')
          continue
        }
        if (!payload[key] || Array.isArray(payload[key]) || typeof payload[key] !== 'object') throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'version_raw' && payload[key] === null) continue
      if (command === 'package.choose' && key === 'kind' && !['dll', 'directory', 'zip'].includes(payload[key])) throw new BridgeError('INVALID_REQUEST')
      if (key === 'preview_hash' || key === 'manifest_hash' || key === 'plan_hash') {
        if (typeof payload[key] !== 'string' || !/^[a-f0-9]{64}$/.test(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'paths') {
        if (!Array.isArray(payload[key]) || !payload[key].length || payload[key].length > 128 || payload[key].some(value => typeof value !== 'string' || !value || value.length > 240)) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'snapshot_id' && command.startsWith('package.adopt')) {
        if (typeof payload[key] !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'idempotency_key' || key === 'plan_id' || key === 'approval_id' || key === 'expected_generation' || key === 'transaction_id') {
        if (typeof payload[key] !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(payload[key])) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (['generation', 'session_id', 'group_id', 'snapshot_id'].includes(key) && payload[key] === null) continue
      if (['after', 'offset', 'generation', 'limit'].includes(key)) {
        const maximum = key === 'limit' ? (command === 'evidence.read' ? 8192 : command === 'mod.workspace.detail' ? 200 : command === 'inventory.list' ? 50 : command === 'deployment.history' ? 10 : 20) : Number.MAX_SAFE_INTEGER
        if (!Number.isSafeInteger(payload[key]) || payload[key] < (['limit', 'generation'].includes(key) ? 1 : 0) || payload[key] > maximum) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'force') { if (typeof payload[key] !== 'boolean') throw new BridgeError('INVALID_REQUEST'); continue }
      if (key === 'user_logs') {
        if (!Array.isArray(payload[key]) || payload[key].length > 8 || payload[key].some(value => typeof value !== 'string' || !value || value.length > 256)) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'snapshot_id') { if (typeof payload[key] !== 'string' || !/^[a-f0-9]{64}$/.test(payload[key])) throw new BridgeError('INVALID_REQUEST'); continue }
      if (key === 'source_handles') {
        if (!Array.isArray(payload[key]) || payload[key].length < 1 || payload[key].length > 8 || payload[key].some(value => typeof value !== 'string' || !value)) throw new BridgeError('INVALID_REQUEST')
        continue
      }
      if (key === 'after_sequence' ? !Number.isSafeInteger(payload[key]) || payload[key] < 0 : typeof payload[key] !== 'string' || !payload[key]) throw new BridgeError('INVALID_REQUEST')
    }
    const request_id = createRequestId()
    let response
    try { response = await transport({ schema_version: 1, request_id, command, payload }) }
    catch { throw new BridgeError('TRANSPORT_UNAVAILABLE') }
    if (!response || response.request_id !== request_id || typeof response.ok !== 'boolean') throw new BridgeError('INVALID_RESPONSE')
    if (!response.ok) throw new BridgeError(typeof response.error?.code === 'string' ? response.error.code : 'INVALID_RESPONSE', ['app.storage.', 'config.', 'game.', 'loader.', 'mod.', 'ai.', 'nexus.', 'profile.', 'package.'].some(prefix => command.startsWith(prefix)) ? response.error?.message : undefined)
    return response.data
  }
  function watchTask(taskId, onUpdate, onError = () => {}) {
    if (closed) throw new BridgeError('BRIDGE_CLOSED')
    let snapshot = null, subscription = null, timer = null, disposed = false
    const apply = event => {
      const next = reduceTask(snapshot, event, taskId)
      if (next !== snapshot) { snapshot = next; onUpdate(next) }
    }
    const unsubscribe = id => id ? send('task.unsubscribe', { subscription_id: id }).catch(() => {}) : Promise.resolve()
    async function poll() {
      if (disposed) return
      try {
        const data = subscription
          ? await request('task.events', { subscription_id: subscription, after_sequence: snapshot?.sequence ?? 0 })
          : await request('task.subscribe', { task_id: taskId, after_sequence: snapshot?.sequence ?? 0 })
        if (disposed) { await unsubscribe(data.subscription_id); return }
        subscription = data.subscription_id
        for (const event of data.events ?? []) apply(event)
        apply(data.snapshot)
        if (terminalStates.has(snapshot?.state)) { await dispose(); return }
      } catch (error) {
        if (disposed) return
        if (error.code === 'SUBSCRIPTION_EXPIRED') subscription = null
        else { onError(error); await dispose(); return }
      }
      if (!disposed) timer = setTimeout(poll, pollInterval)
    }
    async function dispose() {
      if (disposed) return
      disposed = true
      clearTimeout(timer)
      watchers.delete(handle)
      const id = subscription; subscription = null
      await unsubscribe(id)
    }
    const handle = {
      get snapshot() { return snapshot },
      async cancel() { if (!disposed) { const next = await request('task.cancel', { task_id: taskId }); if (!disposed) apply(next) } },
      dispose,
    }
    watchers.add(handle)
    void poll()
    return handle
  }
  return { request, watchTask, async dispose() { closed = true; await Promise.all([...watchers].map(w => w.dispose())) } }
}
