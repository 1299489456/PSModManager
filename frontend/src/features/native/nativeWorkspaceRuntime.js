import { changes, equalOrder, dependencies, reverseDependencies, draftIssues, moveSelection, dropSelection, stableSort, usable } from './nativeModel.js'

const message = error => error?.message || '操作未完成，请刷新后重试。'
export function createNativeWorkspaceRuntime(bridge, state = {}) {
  let epoch = 0, disposed = false
  Object.assign(state, { installationId: null, snapshot: null, order: [], baseline: [], past: [], future: [], rules: [], busy: false, loading: false, error: '', notice: '', externalChanged: false })
  const dirty = () => changes(state.order, state.baseline).dirty
  const payload = () => ({ installation_id: state.installationId })
  function adopt(snapshot) {
    if (!snapshot || !Array.isArray(snapshot.mods) || !Array.isArray(snapshot.enabled_ids) || typeof snapshot.revision !== 'string') throw new Error('原生模组状态返回不完整。')
    state.snapshot = snapshot; state.order = [...snapshot.enabled_ids]; state.baseline = [...snapshot.enabled_ids]
    state.rules = (snapshot.rules || []).map(rule => ({ ...rule }))
    state.past = []; state.future = []; state.externalChanged = false
  }
  async function refresh(discard = false) {
    if (!state.installationId || state.busy || disposed) return false
    const token = ++epoch; state.loading = true; state.error = ''
    try {
      const snapshot = await bridge.request('native.snapshot', payload())
      if (disposed || token !== epoch) return false
      if (!discard && dirty()) {
        state.externalChanged = snapshot.revision !== state.snapshot?.revision
        if (state.externalChanged) state.notice = '游戏状态或模组文件已经改变，草稿保留。重新读取后再保存。'
        state.snapshot.runtime = snapshot.runtime
      } else adopt(snapshot)
      return true
    } catch (error) { if (token === epoch && !disposed) state.error = message(error); return false }
    finally { if (token === epoch && !disposed) state.loading = false }
  }
  async function setInstallation(id) {
    ++epoch; state.installationId = id || null; state.snapshot = null; state.order = []; state.baseline = []; state.past = []; state.future = []; state.rules = []; state.busy = false; state.loading = false; state.error = ''; state.notice = ''; state.externalChanged = false
    if (id) return refresh(true)
    return false
  }
  async function refreshInventory() {
    if (!state.installationId || state.busy || disposed) return false
    const token=++epoch;state.loading=true
    try {
      const latest=await bridge.request('native.snapshot',payload())
      if(token!==epoch||disposed)return false
      if(!dirty())adopt(latest)
      else if(equalOrder(latest.enabled_ids,state.baseline)&&JSON.stringify(latest.rules||[])===JSON.stringify(state.rules)) {
        state.snapshot=latest;state.externalChanged=false
        state.notice='工坊文件状态已更新；启用草稿和撤销记录已保留，请检查依赖后保存。'
      } else {
        state.externalChanged=true;state.snapshot.runtime=latest.runtime
        state.notice='游戏启用列表或排序规则也发生了变化，草稿已保留，请先核对。'
      }
      return true
    } catch(error){if(token===epoch&&!disposed)state.error=message(error);return false}
    finally{if(token===epoch&&!disposed)state.loading=false}
  }
  function change(next, label) {
    if (state.busy || equalOrder(next, state.order)) return false
    state.past.push({ order: [...state.order], label }); if (state.past.length > 100) state.past.shift()
    state.future = []; state.order = [...next]; state.notice = label + '；保存后写入游戏。'; return true
  }
  function enable(ids, at = state.order.length) {
    const valid = ids.filter(id => usable(state.snapshot?.mods.find(m => m.id === id)))
    if (valid.length !== ids.length) state.error = '无法启用缺少原生包结构或尚未安装的模组。'
    const closure = dependencies(valid, state.order, state.snapshot?.mods || [])
    const next = [...state.order]; next.splice(Math.min(at, next.length), 0, ...closure.add)
    change(next, `启用 ${closure.add.length} 个模组${closure.missing.length ? `，还缺少 ${closure.missing.join('、')}` : ''}`)
    return closure
  }
  function disable(ids, force = false) {
    const affected = reverseDependencies(ids, state.order, state.snapshot?.mods || [])
    if (affected.length && !force) return { confirmation: true, affected }
    change(state.order.filter(id => !ids.includes(id) && !(force && affected.includes(id))), `停用 ${ids.length + (force ? affected.length : 0)} 个模组`)
    return { confirmation: false, affected }
  }
  function undo() { if(state.busy)return false; const prev = state.past.pop(); if (!prev) return false; state.future.push({ order: [...state.order], label: prev.label }); state.order = prev.order; state.notice = `已撤销：${prev.label}`; return true }
  function redo() { if(state.busy)return false; const next = state.future.pop(); if (!next) return false; state.past.push({ order: [...state.order], label: next.label }); state.order = next.order; state.notice = `已重做：${next.label}`; return true }
  const issues = () => [...draftIssues(state.order, state.snapshot?.mods || [], state.rules), ...(state.snapshot?.issues || []).filter(x => x.severity === 'error').map(x=>({...x,level:'block',message:x.message || x.code}))]
  const canWrite = () => state.snapshot?.runtime?.state === 'stopped' && state.snapshot?.runtime?.writable !== false && !state.busy && !state.externalChanged
  async function save() {
    if (!canWrite() || issues().some(i => i.level === 'block')) { state.error = state.externalChanged ? '外部状态有变化，请先重新读取。' : '请先处理阻断问题，并确认游戏已经退出。'; return null }
    if (!dirty()) return state.snapshot
    const token = epoch; state.busy = true; state.error = ''
    try {
      const validation = await bridge.request('native.validate', { ...payload(), enabled_ids: [...state.order], rules: [...state.rules] })
      if (token !== epoch || disposed) return null
      if (validation.blocking) throw new Error('原生加载条件未满足，请处理依赖或包检查问题。')
      const result = await bridge.request('native.save', { ...payload(), expected_revision: state.snapshot.revision, enabled_ids: [...state.order] })
      if (token !== epoch || disposed) return null
      if (result.saved !== true) throw new Error('写入结果尚未确认，请刷新核对；草稿已保留。')
      adopt(result); state.notice = '目标状态已写入并读回一致；运行结果等待新的游戏日志确认。'; return result
    } catch (error) { if (token === epoch && !disposed) { state.error = message(error); if (error?.code === 'NATIVE_STATE_CHANGED') state.externalChanged = true } return null }
    finally { if (token === epoch && !disposed) state.busy = false }
  }
  return { setInstallation, refresh, refreshInventory, adopt, enable, disable, undo, redo, change, dirty, issues, canWrite, save,
    move: (ids, mode) => change(moveSelection(state.order, ids, mode), '调整保存顺序'),
    drop: (ids, at) => change(dropSelection(state.order, ids, at), '拖动调整保存顺序'),
    sorted: () => stableSort(state.order, state.snapshot?.mods || [], state.rules),
    dispose() { disposed = true; ++epoch },
  }
}
