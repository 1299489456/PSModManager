export const nativeFields = {
  'native.snapshot': ['installation_id'], 'native.detail': ['installation_id', 'id'],
  'native.identity': ['installation_id'], 'native.rules.read': ['installation_id'], 'native.rules.save': ['installation_id', 'expected_revision', 'rules'],
  'native.validate': ['installation_id', 'enabled_ids'],
  'native.save': ['installation_id', 'expected_revision', 'enabled_ids'],
  'native.config.read': ['installation_id', 'id'], 'native.config.save': ['installation_id', 'id', 'expected_revision', 'entries'],
  'native.import.choose': ['installation_id', 'kind'], 'native.import.preview': ['installation_id', 'source_handle'], 'native.import.commit': ['installation_id', 'preview_id', 'expected_revision'],
  'native.recovery.list': ['installation_id'], 'native.recovery.restore': ['installation_id', 'id', 'expected_revision'], 'native.recovery.create': ['installation_id', 'id'],
  'native.local.remove': ['installation_id', 'id', 'expected_revision'], 'native.source.switch': ['installation_id', 'id', 'expected_revision'],
  'native.profile.list': ['installation_id'], 'native.profile.create': ['installation_id', 'name', 'enabled_ids'],
  'native.profile.update': ['installation_id', 'profile_id', 'name', 'enabled_ids'], 'native.profile.delete': ['installation_id', 'profile_id'],
  'native.profile.preview': ['installation_id', 'profile_id'], 'native.profile.export': ['installation_id', 'profile_id'],
  'native.profile.import.choose': ['installation_id'], 'native.profile.import.commit': ['installation_id', 'source_handle', 'name', 'enabled_ids'],
  'native.open': ['installation_id', 'kind'],
  'workshop.status': ['installation_id'], 'workshop.browse': [], 'workshop.query': ['ids'], 'workshop.detail': ['workshop_id'], 'workshop.open': [],
  'workshop.action': ['installation_id','action','ids'], 'workshop.translate': ['workshop_id'], 'workshop.image':['workshop_id'],
  'app.workshop.settings.get':[], 'app.workshop.settings.set':['auto_update'],
  'ai.translation.cache':['workshop_id'],
  'ai.providers.list':[], 'ai.providers.save':['name','provider','base_url','model'], 'ai.providers.delete':['provider_id'], 'ai.providers.clear_key':['provider_id'], 'ai.providers.bind':['purpose','provider_id'], 'ai.providers.test':['provider_id'],
  'app.open_link': ['url'],
}
export const nativeOptional = {
  'native.validate': ['rules'], 'native.profile.export': ['kind'], 'native.open': ['id'],
  'workshop.status': ['workshop_id','refresh'], 'workshop.browse': ['installation_id', 'query', 'page', 'sort', 'refresh','page_size'],
  'workshop.query': ['installation_id', 'refresh'], 'workshop.detail': ['installation_id', 'refresh'],
  'workshop.open': ['workshop_id', 'intent', 'target'],
  'workshop.translate': ['installation_id','force_refresh'], 'workshop.image':['refresh'],
  'ai.translation.cache':['installation_id'], 'ai.providers.save':['provider_id','api_key','remember'],
}
const uuid = v => typeof v === 'string' && /^(?:[a-f\d]{32}|[a-f\d]{8}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{4}-[a-f\d]{12})$/i.test(v)
const modId = v => typeof v === 'string' && v.length > 0 && v.length <= 64 && !v.startsWith('.') && /^[A-Za-z0-9_.-]+$/.test(v)
const workshopId = v => typeof v === 'string' && /^\d{1,20}$/.test(v)
export function validNativeField(command, key, v) {
  if(key==='provider_id')return typeof v==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(v)
  if(key==='provider')return v==='openai_compatible'
  if(key==='purpose')return ['analysis','translation'].includes(v)
  if(key==='base_url'){try{const u=new URL(v);return typeof v==='string'&&v.length<=2048&&!/[?#]/.test(v)&&Boolean(u.hostname)&&!u.username&&!u.password&&(u.protocol==='https:'||u.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(u.hostname))}catch{return false}}
  if(key==='model')return typeof v==='string'&&v.trim().length>0&&v.length<=200
  if(key==='api_key')return typeof v==='string'&&v.length<=4096
  if(['remember','auto_update','force_refresh'].includes(key))return typeof v==='boolean'
  if(key==='page_size')return [12,24,48].includes(v)
  if (['installation_id', 'profile_id', 'source_handle', 'preview_id'].includes(key)) return uuid(v)
  if (key === 'id') return command === 'native.recovery.restore' ? uuid(v) : modId(v)
  if (key === 'workshop_id') return workshopId(v)
  if (key === 'enabled_ids') return Array.isArray(v) && v.length <= 1000 && v.every(modId) && new Set(v).size === v.length
  if (key === 'ids') return Array.isArray(v) && v.length > 0 && v.length <= (command==='workshop.action'?100:30) && v.every(workshopId) && new Set(v).size===v.length
  if (key === 'action') return ['subscribe','unsubscribe','update'].includes(v)
  if (key === 'expected_revision') return typeof v === 'string' && v.length > 0 && v.length <= 256
  if (key === 'entries') return Array.isArray(v) && v.length <= 1000 && v.every(x => x && typeof x === 'object' && Object.keys(x).every(k => ['key', 'value'].includes(k)) && typeof x.key === 'string' && x.key.length > 0 && x.key.length <= 1024 && typeof x.value === 'string' && x.value.length <= 65536)
  if (key === 'rules') return Array.isArray(v) && v.length <= 1000 && v.every(x => x && Object.keys(x).every(k => ['before','after'].includes(k)) && modId(x.before) && modId(x.after))
  if (key === 'name') return typeof v === 'string' && v.trim().length > 0 && v.length <= (command==='ai.providers.save'?80:100)
  if (key === 'query') return typeof v === 'string' && v.length <= 200
  if (key === 'page') return Number.isSafeInteger(v) && v >= 1 && v <= 1000
  if (key === 'refresh') return typeof v === 'boolean'
  if (key === 'sort') return ['hot','recent','new','mine'].includes(v)
  if (key === 'intent') return ['detail','subscribe','unsubscribe','workshop'].includes(v)
  if (key === 'target') return ['steam','browser'].includes(v)
  if (key === 'kind') return (command === 'native.import.choose' ? ['directory','zip'] : command === 'native.profile.export' ? ['file','text'] : ['mod','config','data','local_mods','persistent']).includes(v)
  if (key === 'url') { try { const url = new URL(v); return v.length <= 2048 && ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password } catch { return false } }
  return false
}
