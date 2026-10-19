export const classifications = { confirmed_failure: '日志确认失败', delegated: '主动委托', potential_risk: '潜在风险', recovered: '同会话已恢复', info: '信息', needs_verification: '待核实' }
export const ruleTitles = { 'ML073-SHADOW': '预计优先加载同名的更高版本', 'IF-CONTENT': 'ItemForge 内容包', 'CF-CONTENT': 'CharacterForge 内容包', 'PS-SELF-DISABLED': '组件报告本次自停用', 'PS-VERSION-TEXT': '元数据与日志版本文案冲突', 'ML-RECOVERED': '加载器启动过程', 'PS-DELEGATED': '功能交由主模组控制', 'HARMONY-POTENTIAL': '来源报告的兼容性观察', 'PS-NULL-WARNING': '单次空对象警告', 'PS-INIT-FAILURE': '组件初始化观察' }
export function historicalReference(session) {
  const reference = session?.historical_reference
  if (!reference || reference.basis !== 'known_reference_hash' || typeof reference.sha256 !== 'string' ||
      !/^[a-f\d]{64}$/i.test(reference.sha256) || typeof reference.date !== 'string' ||
      !/^\d{4}-\d{2}-\d{2}$/.test(reference.date)) return null
  const parsed = new Date(`${reference.date}T00:00:00Z`)
  if (!Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== reference.date) return null
  return reference
}
export function sessionLabel(session) {
  const reference = historicalReference(session)
  const date = reference ? `${reference.date} 历史日志${session.started_at ? ` · 记录时间 ${session.started_at}` : ''}` : session.started_at || '时间未知'
  return `${date} · ${session.pid ? `声明 PID ${session.pid}` : 'PID 未知'}`
}
export function scanLabel(task) {
  if (task?.state === 'succeeded') return ({ complete: '扫描完成', partial: '扫描不完整', failed: '扫描失败', superseded: '已有更新的扫描结果' })[task.data?.status] || '扫描已结束 · 结果待核实'
  return ({ queued: '等待扫描', running: '正在只读扫描', cancelling: '正在取消', cancelled: '扫描已取消', failed: '扫描失败' })[task?.state] || '尚未开始扫描'
}
export function evidenceText(parts) {
  // Decode consecutive raw byte windows together: a window boundary can split Chinese UTF8.
  if (parts.every(part => typeof part.raw_base64 === 'string')) {
    const bytes = parts.map(part => Uint8Array.from(atob(part.raw_base64), char => char.charCodeAt(0)))
    const joined = new Uint8Array(bytes.reduce((total, item) => total + item.length, 0)); let cursor = 0
    for (const item of bytes) { joined.set(item, cursor); cursor += item.length }
    return new TextDecoder('utf-8').decode(joined)
  }
  return parts.map(part => part.text || '').join('')
}
