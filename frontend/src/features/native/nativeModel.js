// Native IDs are case-sensitive. No synthetic package, trust, scan or run data is added here.
export const CAPS = {
  files: { label: '文件读写', desc: '读取或写入此电脑上任意位置的文件', level: 'high', icon: 'hdd' },
  network: { label: '访问互联网', desc: '连接互联网，与外部服务器收发数据', level: 'mid', icon: 'globe' },
  process: { label: '启动程序', desc: '启动其他程序或打开链接', level: 'high', icon: 'terminal' },
  native: { label: '原生系统库', desc: '调用原生系统库，越过 .NET 托管边界', level: 'high', icon: 'cpu' },
  'dynamic-code': { label: '动态代码', desc: '运行时加载或生成代码', level: 'high', icon: 'code' },
  'reflection-external': { label: '反射访问', desc: '通过反射访问系统和引擎的内部实现', level: 'mid', icon: 'eye' },
}
export const cap = key => CAPS[key] || { label: key, desc: '清单声明的能力；当前管理器没有对应说明。', level: 'high', icon: 'shield' }
export const capLevel = caps => !caps?.length ? 'none' : caps.some(key => cap(key).level === 'high') ? 'high' : 'mid'
export const nameOf = mod => mod?.name || mod?.id || '未知模组'
export const sourceText = mod => mod?.installed === false ? '未安装' : mod?.source === 'workshop' ? '创意工坊' : mod?.source === 'local' ? '本地包' : '来源未知'
export const sizeText = n => n == null ? '未知' : n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 ** 2).toFixed(1)} MB`
export const dateText = v => { if (!v) return '未知'; const d = new Date(typeof v === 'number' && v < 1e12 ? v * 1000 : v); return Number.isNaN(d.valueOf()) ? '未知' : d.toLocaleDateString('zh-CN') }
export const shortDate = v => { const d = dateText(v); return d === '未知' ? d : d.split('/').slice(1).join('/') }
export const equalOrder = (a, b) => JSON.stringify(a) === JSON.stringify(b)
export function changes(order, base) {
  const added = order.filter(id => !base.includes(id)), removed = base.filter(id => !order.includes(id))
  const retained = order.filter(id => base.includes(id)), before = base.filter(id => order.includes(id))
  const moved = retained.filter((id, i) => before[i] !== id).length
  return { added, removed, moved, count: added.length + removed.length + Number(moved > 0), dirty: !equalOrder(order, base) }
}
export const modMap = mods => new Map(mods.map(mod => [mod.id, mod]))
export const usable = mod => Boolean(mod && mod.installed !== false && !mod.legacy && !(mod.issues || []).some(x => x.severity === 'error' || x.level === 'block'))
export function dependencies(ids, order, mods) {
  const byId = modMap(mods), seen = new Set(), add = [], missing = []
  function visit(id) {
    if (seen.has(id) || order.includes(id)) return
    seen.add(id)
    const mod = byId.get(id)
    if (!mod || !usable(mod)) { missing.push(id); return }
    ;(mod.prerequisites || []).forEach(visit)
    add.push(id)
  }
  ids.forEach(visit)
  return { add, missing }
}
export function reverseDependencies(ids, order, mods) {
  const set = new Set(ids), byId = modMap(mods)
  let changed = true
  while (changed) { changed = false; for (const id of order) if (!set.has(id) && byId.get(id)?.prerequisites?.some(dep => set.has(dep))) { set.add(id); changed = true } }
  return order.filter(id => set.has(id) && !ids.includes(id))
}
export function stableSort(order, mods, rules = []) {
  const byId = modMap(mods), index = new Map(order.map((id, i) => [id, i]))
  const edges = new Map(order.map(id => [id, new Set()])), degree = new Map(order.map(id => [id, 0]))
  const edge = (a, b) => { if (edges.has(a) && edges.has(b) && !edges.get(a).has(b)) { edges.get(a).add(b); degree.set(b, degree.get(b) + 1) } }
  order.forEach(id => (byId.get(id)?.prerequisites || []).forEach(dep => edge(dep, id)))
  rules.forEach(rule => edge(rule.before, rule.after))
  const ready = order.filter(id => !degree.get(id)), sorted = []
  while (ready.length) { ready.sort((a, b) => index.get(a) - index.get(b)); const id = ready.shift(); sorted.push(id); for (const next of edges.get(id)) { degree.set(next, degree.get(next) - 1); if (!degree.get(next)) ready.push(next) } }
  return sorted.length === order.length ? { order: sorted, cycle: [] } : { order: [...order], cycle: order.filter(id => !sorted.includes(id)) }
}
export function draftIssues(order, mods, rules = []) {
  const byId = modMap(mods), result = []
  order.forEach((id, i) => {
    const mod = byId.get(id)
    if (!mod) { result.push({ level: 'block', id, message: `${id} 尚未安装` }); return }
    for (const issue of mod.issues || []) result.push({ ...issue, id, level: ['error', 'block'].includes(issue.severity || issue.level) ? 'block' : 'warn', message: issue.message || issue.detail || issue.code })
    for (const dep of mod.prerequisites || []) {
      if (!byId.has(dep)) result.push({ level: 'block', id, dep, message: `${nameOf(mod)} 缺少依赖 ${dep}`, action: 'workshop' })
      else if (!order.includes(dep)) result.push({ level: 'block', id, dep, message: `${nameOf(mod)} 依赖的 ${nameOf(byId.get(dep))} 没有启用`, action: 'enable' })
      else if (order.indexOf(dep) > i) result.push({ level: 'warn', id, dep, message: `${nameOf(byId.get(dep))} 应排在 ${nameOf(mod)} 前面`, action: 'sort' })
    }
  })
  const { cycle } = stableSort(order, mods, rules)
  if (cycle.length) result.push({ level: 'block', id: cycle[0], message: `存在循环依赖或排序规则：${cycle.join(' → ')}` })
  return result
}
export function moveSelection(order, ids, mode) {
  const block = order.filter(id => ids.includes(id)), keep = order.filter(id => !ids.includes(id))
  if (!block.length) return [...order]
  const before = order.slice(0, order.indexOf(block[0])).filter(id => !ids.includes(id)).length
  const at = mode === 'top' ? 0 : mode === 'bottom' ? keep.length : mode === 'up' ? Math.max(0, before - 1) : Math.min(keep.length, before + 1)
  keep.splice(at, 0, ...block); return keep
}
export function dropSelection(order, ids, at) {
  const block = order.filter(id => ids.includes(id)), keep = order.filter(id => !ids.includes(id))
  const before = order.slice(0, at).filter(id => !ids.includes(id)).length
  keep.splice(before, 0, ...block); return keep
}
export function missingDependencies(mods) {
  const byId = modMap(mods), missing = new Map()
  for (const mod of mods) for (const dep of mod.prerequisites || []) if (!byId.has(dep)) { if (!missing.has(dep)) missing.set(dep, { id: dep, neededBy: [] }); missing.get(dep).neededBy.push(mod.id) }
  return [...missing.values()]
}
export function runLabel(mod, enabled, baseline) {
  const state = mod?.run?.state || 'unknown', approved = mod?.approval?.state === 'approved'
  if (!enabled && baseline && state === 'running') return { key: 'will-stop', label: '下次不加载', detail: '最近启动记录仍已加载', tone: 'wait' }
  if (state === 'running') return { key: state, label: '本次已加载', detail: '最近 Player.log 的启动证据', tone: 'ok' }
  if (state === 'failed') return { key: state, label: '加载失败', detail: '见运行记录', tone: 'bad' }
  if (state === 'partial') return { key: state, label: '部分加载', detail: '不同入口的结果不一致', tone: 'wait' }
  if (enabled && approved && state === 'not_running') return { key: 'restart', label: '待重启', detail: '已批准，最近启动未加载', tone: 'wait' }
  if (!baseline && enabled) return { key: 'after-save', label: '待启动确认', detail: '保存后启动游戏核对', tone: '' }
  if (state === 'not_running') return { key: state, label: '未加载', detail: '最近启动没有加载此模组', tone: '' }
  return { key: 'unknown', label: '待核对', detail: '没有足够的启动证据', tone: '' }
}
export function approvalLabel(mod) {
  const a = mod?.approval || {}
  if (a.state === 'approved') return { label: a.approval_basis === 'user_enable' ? '已同意启用' : '已批准', detail: a.approval_basis === 'user_enable' ? '用户启用同意，不代表安全检查通过' : dateText(a.approved_at), tone: 'ok' }
  if (a.state === 'changed') return { label: '需重新评估', detail: '包内容与批准时不同', tone: 'wait' }
  if (a.state === 'updated_unverified') return { label: '更新待核对', detail: '保存时记录对当前版本的启用同意', tone: 'wait' }
  if (a.state === 'developer_mode') return { label: '开发者模式', detail: '不等同于批准记录', tone: 'wait' }
  return { label: '待保存批准', detail: '保存时记录本次启用同意', tone: 'wait' }
}
const imageHosts = new Set(['images.steamusercontent.com','steamuserimages-a.akamaihd.net','cdn.akamai.steamstatic.com','cdn.fastly.steamstatic.com','shared.akamai.steamstatic.com','shared.fastly.steamstatic.com','community.akamai.steamstatic.com','community.fastly.steamstatic.com'])
export function safeImage(url) { if(typeof url !== 'string')return null;if(/^data:image\/(?:png|jpeg|webp);base64,[a-z\d+/=]+$/i.test(url))return url;try{const u=new URL(url);return u.protocol==='https:'&&!u.username&&!u.password&&(!u.port||u.port==='443')&&imageHosts.has(u.hostname)?url:null}catch{return null} }
