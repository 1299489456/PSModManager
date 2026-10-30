const states = {
  unscanned: ['未检查', 'off', '可按需检查本地包内容；普通启用、保存和启动不需要先检查。'],
  scanning: ['检查中', 'sky', '正在后台读取与分析内容，可以继续浏览和整理草稿。'],
  reviewed: ['未发现高风险项', 'on', '当前检查规则未发现高风险项，请结合覆盖范围理解结果。'],
  attention: ['需要复核', 'pend', '查看具体证据，确认这些行为是否符合模组用途。'],
  high_risk: ['高风险告警', 'bad', '请复核具体证据，并决定是否停用或隔离；报告本身不阻止普通操作。'],
  blocked: ['需要复核', 'bad', '查看报告证据，自行决定是否停用或隔离。检查结果不自动处置。'],
  incomplete: ['检查未完成', 'pend', '查看覆盖缺口，解决后重新检查；未完成不表示通过。'],
  unavailable: ['报告不可用', 'pend', '报告暂时无法读取，可查看错误或重新检查；不影响普通模组操作。'],
  stale: ['内容已变化', 'pend', '旧报告仅作历史参考，请重新检查当前内容。'],
}
export function securityLabel(item) {
  const key = item?.state || 'unscanned', [label, tone, next] = states[key] || states.incomplete
  if(['prepared','failed'].includes(item?.disposition?.status))return {key,label:'处置待核对',tone:'bad',next:'处置尚未确认完成；查看记录与恢复选项，不据此认为已经移走文件。'}
  if (item?.disposition?.mode === 'quarantine' && item.disposition.status !== 'restored') return { key, label:'已隔离', tone:'bad', next:'管理器启动阻断仍然有效；恢复不会自动启用或批准。' }
  if(!['stale','scanning'].includes(key)&&(item?.verdict==='high_risk'||item?.review_reasons?.includes('SECURITY_HIGH_RISK')||item?.blocking_reasons?.includes('SECURITY_HIGH_RISK')))return {key,label:'高风险告警',tone:'bad',next:states.high_risk[2]}
  if(!['stale','scanning'].includes(key)) {
    const nativeReason=(item?.review_reasons||item?.blocking_reasons)?.find(code=>['SECURITY_GAME_SCAN_INCOMPLETE','SECURITY_GAME_SCAN_REFUSED','SECURITY_UNDECLARED_CAPABILITIES'].includes(code))
    if(nativeReason)return {key,label:({SECURITY_GAME_SCAN_INCOMPLETE:'原生检查未完成',SECURITY_GAME_SCAN_REFUSED:'原生检查拒绝',SECURITY_UNDECLARED_CAPABILITIES:'权限声明不完整'})[nativeReason],tone:'bad',next:securityReason(nativeReason)}
  }
  return { key, label, tone, next }
}
const reasons={same_method_three_signals_no_dataflow_or_execution_proof:'同一方法包含凭据特征、文件读取和网络发送；尚未证明运行路径或实际数据流。',SECURITY_SCAN_REQUIRED:'当前内容尚未检查。',SECURITY_CONTENT_CHANGED:'内容或检查规则已变化，需要重新检查。',SECURITY_HIGH_RISK:'报告发现高风险组合，请结合用途复核；不会自动停用或阻止启动。',SECURITY_QUARANTINED:'存在隔离或未确认的处置记录。',SECURITY_RECORD_INVALID:'检查记录损坏或无法读取，请保留记录并查看日志。',SECURITY_INCOMPLETE:'检查未完成，请查看覆盖缺口。',SECURITY_BUSY:'本安装正在检查或处置，请等待完成。',SECURITY_IO:'报告或处置记录未能保存，请检查数据目录。',script_or_nested_archive_not_analyzed:'脚本或嵌套归档未分析',native_behavior_not_analyzed:'未分析原生代码行为',partial_static_analysis:'静态分析仅部分完成',parser_unavailable:'静态解析器不可用',analysis_failed:'静态分析失败',content_time_limit:'完整内容读取超过时间限制',content_size_limit:'内容超过检查大小限制',static_evidence:'静态证据，不代表已经执行。',call_instruction_not_runtime_execution:'发现调用指令，不代表运行时执行过。',same_method_static_combination:'同一方法内出现相关静态证据，需要结合用途复核。',string_only:'仅字符串特征，不构成已执行行为的证据。',manifest_declaration:'来自清单声明，不代表实际行为。',antivirus_detection:'来自本机防病毒引擎的检测结果。'}
Object.assign(reasons, {
  SECURITY_GAME_SCAN_INCOMPLETE:'报告中的游戏原生检查未完成，请查看错误或重新手动检查。',
  SECURITY_GAME_SCAN_REFUSED:'报告中的游戏原生检查发现拒绝项，请查看证据并决定如何处理。',
  SECURITY_UNDECLARED_CAPABILITIES:'报告检测到清单未声明的权限，请结合模组用途复核。',
  source_resolution_unproven:'当前包未满足游戏的 IMod 初筛，无法确认实际加载来源；请移出无效本地覆盖或切换来源后重新检查。',
  source_ambiguous:'同一 ID 存在多个同优先级来源，无法确认游戏会加载哪一个；请先整理重复包。',
  rule_evidence_limited:'调用或字符串风险证据已达到上限，检查覆盖不完整。',
  embedded_payload_evidence_limited:'内嵌载荷证据已达到上限，检查覆盖不完整。',
  static_instruction_presence_not_execution:'检查静态指令，不代表这些行为已经执行。',
  no_control_flow_or_dataflow:'未证明实际运行路径或完整数据流。',
  no_resource_deserialization_or_unpacking:'不反序列化资源或解包内嵌载荷。',
  no_deobfuscation:'未进行反混淆。',
  no_runtime_generated_code:'无法检查运行时生成的代码。',
  WORKER_PROTOCOL_INVALID:'静态解析器协议不匹配；请重启管理器后重新检查。',
})
export function nativeScanLabel(scan) {
  if(!scan)return {label:'尚未检查',tone:'off'}
  if(scan.status!=='scanned'||scan.error||typeof scan.refused!=='boolean')return {label:'检查未完成',tone:'pend'}
  if(scan.refused)return {label:'原生检查拒绝',tone:'bad'}
  if(scan.undeclared?.length)return {label:'权限声明不完整',tone:'bad'}
  return {label:'原生检查通过',tone:'on'}
}
export function securityReason(value) { const raw=typeof value==='string'?value:value?.message||value?.detail||value?.reason||value?.code;return `${value?.path?value.path+'：':''}${reasons[raw]||raw||'原因未提供'}` }
export function securityEffect(effect){return ({local_package_move:'停用受影响模组，创建恢复点，再把本地包移出游戏扫描范围。',logical_quarantine_files_remain:'停用受影响模组，并阻止此来源通过管理器启动；文件仍在 Steam 工坊，不取消订阅。',disable_only:'停用当前模组及依赖它的启用项；保留包文件，不取消订阅。'})[effect]||effect}
export function securityRestoreAllowed(runtime,{dirty=false,busy=false,scanning=false,externalChanged=false}={}) {
  return runtime?.state==='stopped' && (runtime.writable===true || runtime.blocking_reason==='NATIVE_RECOVERY_PENDING') && !dirty && !busy && !scanning && !externalChanged
}
export function workshopSecurityItem(mods, items, workshopId) {
  const mod = mods.find(m => m.source === 'workshop' && m.workshop_id === workshopId)
  const item=mod&&items[mod.id],matches=item?.source==='workshop'&&item.workshop_id===workshopId&&(!mod.package_revision||!item.package_revision||mod.package_revision===item.package_revision)
  return mod ? { mod, item:matches?item:undefined } : null
}
const itemKey = item => JSON.stringify([item?.source, item?.source_id,item?.package_revision,item?.workshop_id, item?.content_hash, item?.report_id, item?.state, item?.disposition])
const inventoryKey = mod => JSON.stringify([mod.source, mod.workshop_id, mod.folder_path, mod.package_revision, mod.version])
const matchesInventory=(item,mod)=>!mod||(!mod.source||item.source===mod.source)&&(!mod.workshop_id||item.workshop_id===mod.workshop_id)&&(!mod.source_id||!item.source_id||mod.source_id===item.source_id)&&(!mod.package_revision||!item.package_revision||mod.package_revision===item.package_revision)

export function createSecurityRuntime(bridge, state = {}, { schedule=(fn,ms)=>setTimeout(fn,ms), cancel=id=>clearTimeout(id), pollInterval=1200, active=true } = {}) {
  Object.assign(state, { installationId:null, status:null, items:{}, details:{}, selectedId:null, loading:false, detailLoading:false, starting:false, mutating:false, error:'', actionError:'', preview:null })
  let epoch=0, readSequence=0, detailSequence=0, timer=null, disposed=false, ownsScan=false, inventory=new Map(), inventoryMods=new Map()
  const current=(token,id)=>!disposed&&token===epoch&&id===state.installationId
  function stop(){if(timer!==null)cancel(timer);timer=null}
  function poll(){stop();if(state.status?.scanning&&!disposed&&(active||ownsScan)){const token=epoch,id=state.installationId;timer=schedule(()=>{timer=null;if(current(token,id))return activity()},pollInterval)}}
  async function activity(){
    const token=epoch,id=state.installationId
    try{
      const value=await bridge.request('security.activity',{installation_id:id})
      if(!current(token,id))return
      if(value?.installation_id!==id||typeof value.scanning!=='boolean')throw new Error('检查进度返回不完整。')
      state.status={...state.status,scanning:value.scanning,operation:value.operation};state.error=''
      if(!value.scanning){ownsScan=false;state.details={};if(active)await refresh()}
    }catch(error){if(current(token,id))state.error=error.message||'检查进度暂时不可用。'}
    finally{if(current(token,id))poll()}
  }
  function adopt(value){
    if(!value||value.installation_id!==state.installationId||!Array.isArray(value.items))throw new Error('本地安全状态返回不完整。')
    const old=state.items;state.status=value;state.items=Object.fromEntries(value.items.map(item=>[item.id,matchesInventory(item,inventoryMods.get(item.id))?item:{...item,state:'stale',report_id:null,blocking:false,blocking_reasons:['SECURITY_CONTENT_CHANGED']}]))
    for(const id of Object.keys(state.details))if(itemKey(old[id])!==itemKey(state.items[id]))delete state.details[id]
    state.error=value.error?securityReason(value.error):'';poll()
  }
  async function refresh(){
    if(!active||!state.installationId||disposed)return null
    const token=epoch,id=state.installationId,seq=++readSequence;stop();state.loading=true
    try{const value=await bridge.request('security.status',{installation_id:id});if(!current(token,id)||seq!==readSequence)return null;adopt(value);if(state.selectedId&&!state.details[state.selectedId])void select(state.selectedId);return value}
    catch(error){if(current(token,id)&&seq===readSequence)state.error=error.message||'无法读取本地检查状态。';return null}
    finally{if(current(token,id)&&seq===readSequence){state.loading=false;poll()}}
  }
  async function select(id,force=false){
    state.selectedId=id||null;state.detailLoading=false;const seq=++detailSequence
    if(!active||!id||!state.installationId||disposed)return null
    if(!force&&state.details[id])return state.details[id]
    const token=epoch,installationId=state.installationId,key=itemKey(state.items[id]);state.detailLoading=true
    try{const value=await bridge.request('security.detail',{installation_id:installationId,id});if(!current(token,installationId)||seq!==detailSequence||key!==itemKey(state.items[id]))return null
      if(value?.item?.id!==id)throw new Error('模组安全报告与当前选择不一致。')
      if(!matchesInventory(value.item,inventoryMods.get(id)))throw new Error('模组来源或内容已变化，请刷新模组列表后重新查看检查报告。')
      state.details[id]=value;state.items[id]=value.item;return value
    }catch(error){if(current(token,installationId)&&seq===detailSequence)state.error=error.message||'无法读取检查报告。';return null}
    finally{if(current(token,installationId)&&seq===detailSequence)state.detailLoading=false}
  }
  async function scan(ids,antivirus=false){
    if(!active||!state.installationId||state.starting||state.status?.scanning||disposed)return null
    // A page-entry read may still describe the state before this explicit scan.
    // It must not replace accepted work or cancel the progress polling owner.
    ++readSequence;++detailSequence;stop();state.loading=false;state.detailLoading=false
    const token=epoch,id=state.installationId;state.starting=true;state.error=''
    try{const value=await bridge.request('security.scan',{installation_id:id,...(ids?{ids:[...new Set(ids)]}:{}),antivirus});if(!current(token,id))return null;ownsScan=Boolean(value.scanning);adopt(value);if(state.selectedId)void select(state.selectedId,true);return value}
    catch(error){if(current(token,id))state.error=error.message||'未能开始检查。';return null}
    finally{if(current(token,id))state.starting=false}
  }
  async function action(command,payload){
    if(!active||!state.installationId||state.mutating||disposed)return null
    const token=epoch,id=state.installationId;state.mutating=true;state.actionError=''
    try{const value=await bridge.request(command,{installation_id:id,...payload});if(!current(token,id))return null;if(value.status)adopt(value.status);return value}
    catch(error){if(current(token,id)){state.actionError=error.message||'处置未完成，请刷新核对。';if(command==='security.dispose')state.preview=null;if(command!=='security.dispose.preview')void refresh()}return null}
    finally{if(current(token,id))state.mutating=false}
  }
  return {
    refresh,select,scan,
    async setInstallation(id){if(state.installationId===(id||null))return;epoch++;readSequence++;detailSequence++;stop();ownsScan=false;inventory=new Map();inventoryMods=new Map();Object.assign(state,{installationId:id||null,status:null,items:{},details:{},selectedId:null,loading:false,detailLoading:false,starting:false,mutating:false,error:'',actionError:'',preview:null});if(id&&active)return refresh()},
    async setActive(value){if(active===Boolean(value))return;active=Boolean(value);if(active)return refresh();readSequence++;detailSequence++;state.loading=false;state.detailLoading=false;poll()},
    inventory(mods){const next=new Map(mods.map(m=>[m.id,inventoryKey(m)]));inventoryMods=new Map(mods.map(m=>[m.id,m]));let changed=false;for(const [id,key] of next)if(inventory.get(id)!==key){delete state.details[id];delete state.items[id];changed=true}for(const id of inventory.keys())if(!next.has(id)){delete state.details[id];delete state.items[id];changed=true}inventory=next;if(changed){detailSequence++;state.preview=null;void refresh()}},
    async preview(id,mode){state.preview=null;const value=await action('security.dispose.preview',{id,mode});if(value)state.preview=value;return value},
    async disposePreview(){if(!state.preview)return null;const value=await action('security.dispose',{preview_id:state.preview.preview_id});if(value){state.preview=null;state.details={};if(state.selectedId)void select(state.selectedId,true)}return value},
    async restore(recordId){const value=await action('security.restore',{record_id:recordId});if(value){state.details={};if(state.selectedId)void select(state.selectedId,true)}return value},
    dispose(){disposed=true;epoch++;detailSequence++;stop()},
  }
}
