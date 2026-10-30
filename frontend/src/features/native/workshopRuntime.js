export const operationDone = new Set(['completed','partial','failed','uncertain'])
export function downloadLabel(item) {
  if (!item) return { text:'更新状态待核对', tone:'off' }
  const progress=typeof item.progress==='number'?` ${Math.round(Math.max(0,Math.min(1,item.progress))*100)}%`:''
  if(item.downloading===true||item.download_state==='downloading')return {text:'下载中'+progress,tone:'sky'}
  if(item.download_pending===true||item.download_state==='download_pending')return {text:'等待下载',tone:'pend'}
  if(item.needs_update===true||item.update_available===true)return {text:'待更新',tone:'pend'}
  if(item.installed===true&&(item.needs_update===false||item.update_available===false))return {text:'已安装 · 无待更新',tone:'on'}
  return {text:item.installed?'已安装 · 更新未知':item.download_state==='not_installed'?'尚未下载':'更新状态未知',tone:'off'}
}
export const subscriptionLabel = item => item?.subscription_state==='subscribed'?'已订阅':item?.subscription_state==='not_subscribed'?'未订阅':'订阅未确认'
export const updateEligible = item => Boolean(item&&(item.needs_update===true||item.update_available===true)&&!['downloading','download_pending'].includes(item.download_state)&&item.downloading!==true&&item.download_pending!==true)
export const itemOperationLabel = (item,action) => ({queued:'等待处理',subscribing:'正在订阅',unsubscribing:'正在取消订阅',downloading:'正在下载',download_pending:'等待 Steam 下载',completed:action==='subscribe'?'订阅已确认':action==='unsubscribe'?'取消订阅已确认':'下载完成',failed:'失败',uncertain:'结果待核对',already_current:'无需更新'})[item.state]||'状态待核对'
function retainObserved(previous,current){
  const incoming=new Set((current||[]).map(x=>x.workshop_id))
  return [...(current||[]).map(item=>{const old=previous[item.workshop_id];if(!old)return item;const value={...item};if(item.subscription_state==='unknown'||item.subscribed==null){value.subscription_state=old.subscription_state;value.subscribed=old.subscribed}if(item.download_state==='unknown'||item.needs_update==null){for(const key of ['needs_update','update_available','downloading','download_pending','download_state','bytes_downloaded','bytes_total','progress'])if(old[key]!=null)value[key]=old[key]}return value}),...Object.values(previous).filter(x=>!incoming.has(x.workshop_id))]
}
const awaitingProbe=value=>value?.runtime?.state==='stopped'&&value.session?.refreshing===true&&value.session.reason_code==='WORKSHOP_SESSION_CONNECTING'
const observedStatus=value=>Boolean(value&&(value.session?.state==='ready'&&value.session?.observed_at||(value.items||[]).some(item=>[item.subscribed,item.needs_update,item.downloading,item.download_pending].some(x=>typeof x==='boolean'))))
export function workshopStatusView(state={},action='subscribe',local={}){
  const value=state.status,runtime=value?.runtime||{},session=value?.session||{},capability=action==='update'?value?.capabilities?.direct_updates:value?.capabilities?.direct_subscription
  const codes=new Set([capability?.reason_code,session.reason_code,...[...(runtime.blockers||[]),...(capability?.blockers||[])].map(x=>typeof x==='string'?x:x?.code)])
  const view=(kind,title,message,extra={})=>({kind,title,message,retained:Boolean(state.stale&&state.hasObserved),refreshLabel:'刷新状态',...extra})
  if(!state.installationId)return view('installation','先选择游戏安装','使用右上角的游戏安装牌子选择正式版目录。')
  if(!value)return view('connecting','正在读取工坊状态',state.error||'正在核对游戏安装和 Steam 会话。公开模组信息仍可查看。',{refreshLabel:'刷新连接'})
  if(codes.has('GUARD_RECOVERY_REQUIRED')||codes.has('WORKSHOP_RECOVERY_REQUIRED'))return view('recovery','安装恢复检查未通过',capability?.reason_code==='WORKSHOP_RECOVERY_REQUIRED'?capability.reason:'此安装仍有未完成的文件操作，请处理对应恢复记录后再刷新状态。',{action:'logs',actionLabel:'查看日志'})
  if(runtime.state==='running')return view('running','游戏正在运行','退出游戏后才能订阅、取消订阅或下载更新。',{refreshLabel:'退出后刷新'})
  if(runtime.state!=='stopped')return view('process','游戏进程状态未确认',capability?.reason_code==='WORKSHOP_PROCESS_UNKNOWN'?capability.reason:'暂时无法核实游戏已退出，修改操作已暂停。')
  if(['WORKSHOP_INSTALLATION_CHANGED','WORKSHOP_INSTALLATION_UNAVAILABLE','WORKSHOP_GUARD_UNAVAILABLE'].some(code=>codes.has(code)))return view('installation','安装暂不可写',capability?.reason||runtime.message||'请核对右上角的游戏安装位置和目录访问状态。',{action:'logs',actionLabel:'查看日志'})
  if((value.operations||[]).some(op=>op.state==='uncertain')||codes.has('WORKSHOP_OPERATION_BUSY'))return view('uncertain','工坊操作待核对',capability?.reason||session.message||'已有操作结果尚未确认，请刷新核对，不要重复提交。')
  if(state.pending||state.remainingCount||(value.operations||[]).some(op=>!operationDone.has(op.state)))return view('busy','Steam 正在处理操作','请等待下方操作记录中的回调和下载结果。')
  if(awaitingProbe(value)||runtime.busy_owner==='workshop_probe'||capability?.reason_code==='WORKSHOP_SESSION_CONNECTING'||runtime.writable!==false&&session.reason_code==='WORKSHOP_SESSION_CONNECTING')return view('connecting','正在连接本机 Steam','正在后台核对 Steam 状态，完成后更新操作按钮。',{refreshLabel:'刷新连接'})
  if(runtime.writable===false||codes.has('WORKSHOP_INSTALLATION_BUSY')||codes.has('GUARD_BUSY'))return view('busy','安装暂不可写',capability?.reason||runtime.message||'安装目录正由其他操作占用，请等待完成后刷新。')
  if(session.state!=='ready')return view('steam','Steam 会话不可用',capability?.reason||session.message||session.last_error?.message||state.error||'请打开并登录 Steam 后刷新连接。',{refreshLabel:'刷新连接'})
  if(state.stale)return view('connecting','正在核对 Steam 状态',session.refreshing?'后台核对正在进行，确认后才能提交操作。':state.error||'当前状态尚未确认，请刷新连接。',{refreshLabel:'刷新连接'})
  if(capability?.available===false)return view('blocked','工坊操作暂不可用',capability.reason||'当前会话不支持此操作，请刷新核对。')
  if(local.writable===false)return view('busy','本地工作区处理中',local.message||'本地工作区正在处理另一项操作，请等待完成。')
  return view('ready','已连接本机 Steam','使用 Steam 客户端当前账号管理订阅；订阅确认与文件下载分别核对。',{refreshLabel:'刷新连接'})
}
export function createWorkshopRuntime(bridge,state={},options={}){
  Object.assign(state,{installationId:null,status:null,loading:false,pending:false,error:'',notice:'',remainingCount:0,queueNotice:'',stale:false,hasObserved:false})
  let epoch=0,timer=null,disposed=false,requesting=null,queued=[],activeBatch=null
  const delivered=new Set()
  const payload=()=>({installation_id:state.installationId})
  const items=()=>Object.fromEntries((state.status?.items||[]).map(x=>[x.workshop_id,x]))
  const busy=()=>state.pending||queued.length>0||(state.status?.operations||[]).some(x=>!operationDone.has(x.state))
  const canDispatch=action=>Boolean(state.installationId&&!state.stale&&state.status?.session?.state==='ready'&&options.canWrite?.()!==false&&state.status?.runtime?.state==='stopped'&&state.status?.runtime?.writable!==false&&(action==='update'?state.status?.capabilities?.direct_updates:state.status?.capabilities?.direct_subscription)?.available)
  const available=action=>!busy()&&canDispatch(action)
  const statusView=(action='subscribe')=>workshopStatusView(state,action,{writable:options.canWrite?.(),message:options.blockReason?.()})
  function reason(action){const view=statusView(action);return view.kind==='ready'?'':view.message}
  function schedule(){clearTimeout(timer);if(disposed||!state.installationId)return;const active=busy()||state.status?.session?.refreshing||(state.status?.items||[]).some(x=>['downloading','download_pending'].includes(x.download_state));timer=setTimeout(()=>void refresh(),active?2000:15000)}
  function stopQueue(message){if(queued.length)state.queueNotice=`后续 ${queued.length} 个模组尚未提交：${message}`;queued=[];state.remainingCount=0;activeBatch=null}
  async function submit(action,ids,token){state.pending=true;try{const result=await bridge.request('workshop.action',{...payload(),action,ids});if(disposed||token!==epoch)return null;const ops=state.status?.operations||[];state.status={...state.status,operations:[result,...ops.filter(x=>x.operation_id!==result.operation_id)]};return result}finally{if(token===epoch&&!disposed)state.pending=false}}
  async function nextBatch(token){if(!queued.length||disposed||token!==epoch)return;if(!canDispatch('update')){stopQueue(state.status?.capabilities?.direct_updates?.reason||'Steam 会话或游戏状态不允许继续，请检查后重新更新。');return}const batch=queued.splice(0,100);state.remainingCount=queued.length;try{const result=await submit('update',batch,token);if(result)activeBatch=result.operation_id}catch(e){if(token===epoch&&!disposed){queued.unshift(...batch);stopQueue(e.message||'提交失败，请重试。')}}}
  async function refresh(force=false){
    if(!state.installationId||disposed||requesting===epoch)return false
    const token=epoch;requesting=token;state.loading=true
    try{
      const value=await bridge.request('workshop.status',{...payload(),...(force===true?{refresh:true}:{})})
      if(disposed||token!==epoch)return false
      const before=items(),wasProbing=awaitingProbe(state.status),probing=awaitingProbe(value);state.hasObserved=state.hasObserved||observedStatus(value);state.stale=Boolean(value.session?.stale||value.session?.state!=='ready');if(state.stale)value.items=retainObserved(before,value.items);state.status=value;state.error=''
      let changed=false
      for(const op of value.operations||[]){
        const terminalKey=`${op.operation_id}:terminal:${op.state}`
        if(operationDone.has(op.state)&&!delivered.has(terminalKey)){delivered.add(terminalKey);changed=true}
        for(const item of op.items||[]){
          const key=`${op.operation_id}:${item.workshop_id}:${item.state}`
          if(item.state==='completed'&&!delivered.has(key)){delivered.add(key);changed=true}
        }
      }
      for(const item of value.items||[])if(item.download_state==='installed'&&['downloading','download_pending'].includes(before[item.workshop_id]?.download_state))changed=true
      // The probe may hold the native write guard while the terminal inventory is read.
      if(changed||(queued.length&&wasProbing&&!probing&&value.session?.state==='ready'))await options.changed?.()
      if(disposed||token!==epoch)return false
      // Normal post-operation reconnect waits; a lost session or game start stops only unsubmitted items.
      if(queued.length&&value.runtime?.state!=='stopped')stopQueue('游戏未确认停止，请退出游戏后重新更新。')
      else if(queued.length&&value.session?.state!=='ready'&&!probing)stopQueue('Steam 会话已断开，请恢复连接后重新更新。')
      const current=(value.operations||[]).find(x=>x.operation_id===activeBatch)
      if(queued.length&&current&&operationDone.has(current.state)){
        if(current.state==='uncertain')stopQueue('当前批次结果待核对，请刷新确认后重试。')
        else if(probing)state.queueNotice=`正在刷新 Steam 状态，余下 ${queued.length} 个模组等待确认后继续。`
        else{state.queueNotice='';await nextBatch(token)}
      }
      return true
    }catch(e){
      if(token===epoch&&!disposed){state.error=e.message||'Steam 状态暂不可用。';state.stale=true;if(state.status)state.status={...state.status,session:{...state.status.session,state:'unavailable',stale:true,refreshing:false,reason_code:e.code||'WORKSHOP_NETWORK_ERROR',message:state.error},capabilities:{...state.status.capabilities,direct_updates:{available:false,reason:state.error},direct_subscription:{available:false,reason:state.error}}};stopQueue(state.error)}
      return false
    }finally{
      if(requesting===token)requesting=null
      if(token===epoch&&!disposed){state.loading=false;schedule()}
    }
  }
  async function setInstallation(id){++epoch;clearTimeout(timer);delivered.clear();queued=[];activeBatch=null;Object.assign(state,{installationId:id||null,status:null,loading:false,pending:false,error:'',notice:'',remainingCount:0,queueNotice:'',stale:false,hasObserved:false});if(id)await refresh()}
  async function act(action,ids){if(!available(action)){state.error=reason(action);return null}const unique=[...new Set(ids)].filter(x=>/^\d{1,20}$/.test(x));const chosen=action==='update'?unique.filter(id=>updateEligible(items()[id])):unique;if(!chosen.length||(action!=='update'&&chosen.length>100)){state.error=chosen.length>100?'一次最多处理 100 个订阅条目。':'当前没有可执行的工坊条目，请刷新状态。';return null}const token=epoch;state.error='';state.queueNotice='';queued=chosen.slice(100);state.remainingCount=queued.length;try{const result=await submit(action,chosen.slice(0,100),token);if(!result)return null;activeBatch=queued.length?result.operation_id:null;state.notice='操作已提交，等待 Steam 回调核对。订阅确认与文件下载分别显示。';await refresh();return result}catch(e){if(token===epoch&&!disposed){state.error=e.message||'工坊操作未完成。';stopQueue(state.error)}return null}finally{if(token===epoch&&!disposed)schedule()}}
  return{items,busy,available,reason,statusView,refresh,setInstallation,act,dispose(){disposed=true;++epoch;queued=[];clearTimeout(timer)}}
}
