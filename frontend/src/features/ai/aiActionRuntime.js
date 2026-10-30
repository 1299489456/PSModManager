const idPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const validId = value => typeof value === 'string' && idPattern.test(value)
const fields = ['suggestion_id', 'installation_id', 'release_id', 'manifest_hash', 'display_name', 'version_raw', 'action', 'reason']
const workspaceFields = ['kind', 'suggestion_id', 'installation_id', 'workspace_id', 'snapshot_id', 'display_name', 'version_raw', 'action', 'reason']
const nativeFields = ['kind', 'suggestion_id', 'installation_id', 'mod_id', 'revision', 'display_name', 'version_raw', 'action', 'reason']
const actions = new Set(['install', 'enable', 'disable'])
const fieldsFor = item => item.kind === 'native_mod' ? nativeFields : item.kind === 'workspace' ? workspaceFields : fields

export function readSuggestions(value = [], installationId) {
  if (!Array.isArray(value) || value.length > 4 || value.some(item => !item || typeof item !== 'object'
    || Object.keys(item).some(key => !fieldsFor(item).includes(key)) || fieldsFor(item).some(key => !Object.hasOwn(item, key))
    || !validId(item.suggestion_id) || !validId(item.installation_id)
    || installationId !== undefined && item.installation_id !== installationId
    || (item.kind === 'native_mod' ? typeof item.mod_id !== 'string' || !/^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}$/.test(item.mod_id) || typeof item.revision !== 'string' || !item.revision || item.revision.length > 256 || !['enable','disable'].includes(item.action) : item.kind === 'workspace' ? !validId(item.workspace_id) || typeof item.snapshot_id !== 'string' || !/^[a-f0-9]{64}$/.test(item.snapshot_id) || !['enable', 'disable'].includes(item.action)
      : !validId(item.release_id) || typeof item.manifest_hash !== 'string' || !/^[a-f0-9]{64}$/.test(item.manifest_hash))
    || typeof item.display_name !== 'string' || item.display_name.length > 200
    || !(item.version_raw === null || typeof item.version_raw === 'string' && item.version_raw.length <= 200)
    || !actions.has(item.action) || typeof item.reason !== 'string' || item.reason.length > 300)
    || new Set(value.map(item => item.suggestion_id)).size !== value.length) throw new Error('Invalid AI suggestions response')
  return value.map(item => Object.fromEntries(fieldsFor(item).map(field => [field, item[field]])))
}


// Suggestions navigate only; native changes require the ordinary draft/save path.
export function createAiActionRuntime(bridge, state = {}, onChanged = () => {}) {
  let disposed = false, epoch = 0
  Object.assign(state, { cards: [], sessionId: null, installationId: null, busy: false, activeId: null, resolving: false, cancelling: false, manager: null })
  function clear() { ++epoch; Object.assign(state, { cards: [], sessionId:null, installationId:null, busy:false, activeId:null, resolving:false, cancelling:false }) }
  function setSuggestions(sessionId, installationId, suggestions) {
    if(disposed || state.busy) return false
    const verified = readSuggestions(suggestions, installationId)
    if(!validId(sessionId) || verified.length && !validId(installationId)) return false
    state.sessionId=sessionId;state.installationId=installationId
    state.cards=verified.map(card=>({...card,status:'ready',result:'',error:''}))
    return true
  }
  async function execute(suggestionId) {
    const card=state.cards.find(c=>c.suggestion_id===suggestionId)
    if(disposed || state.busy || !card) return false
    if(card.kind!=='native_mod'){card.error='这是旧版操作建议。请在原生模组列表中重新核对，不再执行旧文件部署。';return false}
    const token=++epoch;state.busy=true;state.activeId=suggestionId;state.resolving=true;card.error=''
    try {
      const result=await bridge.request('ai.suggestion.resolve',{session_id:state.sessionId,suggestion_id:suggestionId})
      if(disposed || token!==epoch) return false
      if(result.kind!=='native_mod' || result.installation_id!==card.installation_id || result.mod_id!==card.mod_id || result.navigation?.page!=='mods' || result.navigation?.mod_id!==card.mod_id) throw new Error('建议与当前模组不一致，请重新分析。')
      card.result='已打开模组详情。请在模组列表确认启停并保存。';onChanged(card);return true
    } catch(error) {if(token===epoch)card.error=error.message || '无法打开建议，请在模组列表中查看。';return false}
    finally {if(token===epoch){state.busy=false;state.activeId=null;state.resolving=false}}
  }
  async function cancel(){++epoch;state.busy=false;state.activeId=null;state.resolving=false;return true}
  return {setSuggestions,execute,cancel,clear,dispose(){clear();disposed=true}}
}
