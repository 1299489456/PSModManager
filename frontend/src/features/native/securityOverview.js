// Pure list helpers for the Mod check page. They only reorganise data the
// security runtime already holds; nothing here reads reports or starts a scan.
import { securityLabel } from './securityRuntime.js'

export const GROUPS = [
  ['all', '全部'],
  ['care', '需关注'],
  ['stale', '内容已变化'],
  ['unscanned', '未检查'],
  ['ok', '未发现高风险'],
]

// care = high risk, needs review, incomplete, unreadable, quarantined or an unfinished disposition.
export function groupOf(item) {
  const { key, tone } = securityLabel(item)
  if (key === 'scanning') return 'scanning'
  if (key === 'stale') return 'stale'
  if (tone === 'bad' || ['attention', 'incomplete', 'unavailable', 'blocked'].includes(key)) return 'care'
  if (key === 'reviewed') return 'ok'
  return 'unscanned'
}

const RANK = { care: 0, stale: 1, unscanned: 2, scanning: 3, ok: 4 }
const nameKey = mod => (mod?.name || mod?.id || '').toLowerCase()

export function riskRank(item) {
  const group = groupOf(item)
  return RANK[group] * 2 + (group === 'care' && securityLabel(item).tone !== 'bad' ? 1 : 0)
}

export function summarize(mods, items) {
  const counts = { all: mods.length, care: 0, stale: 0, unscanned: 0, scanning: 0, ok: 0 }
  for (const mod of mods) counts[groupOf(items[mod.id])]++
  return counts
}

export function visibleChecks(mods, items, { query = '', group = 'all', sort = 'risk', enabledOnly = false, enabledIds = [] } = {}) {
  const needle = query.trim().toLowerCase(), enabled = new Set(enabledIds)
  const list = mods.filter(mod => (!needle || `${mod.name || ''} ${mod.id} ${mod.author || ''} ${mod.workshop_id || ''}`.toLowerCase().includes(needle))
    && (group === 'all' || groupOf(items[mod.id]) === group)
    && (!enabledOnly || enabled.has(mod.id)))
  const byName = (a, b) => nameKey(a).localeCompare(nameKey(b), 'zh')
  const checkedAt = mod => Date.parse(items[mod.id]?.scanned_at || '') || 0
  if (sort === 'name') return list.sort(byName)
  if (sort === 'checked') return list.sort((a, b) => checkedAt(b) - checkedAt(a) || byName(a, b))
  return list.sort((a, b) => riskRank(items[a.id]) - riskRank(items[b.id]) || byName(a, b))
}

// IDs worth a one-click re-check: never inspected, or inspected content has since changed.
export function quickTargets(mods, items) {
  return {
    unscanned: mods.filter(mod => groupOf(items[mod.id]) === 'unscanned').map(mod => mod.id),
    stale: mods.filter(mod => groupOf(items[mod.id]) === 'stale').map(mod => mod.id),
  }
}

export const SEVERITY = {
  critical: ['严重', 'bad', 0], high: ['高风险', 'bad', 0],
  medium: ['需复核', 'pend', 1], warning: ['需复核', 'pend', 1],
  low: ['提示', 'sky', 2], info: ['能力提示', 'sky', 2],
}
export const severityOf = value => {
  const [label, tone, rank] = SEVERITY[value] || [value || '未分级', 'off', 3]
  return { label, tone, rank, level: rank === 0 ? 'high' : rank === 1 ? 'review' : 'hint' }
}

export function findingCounts(findings = []) {
  const counts = { all: findings.length, high: 0, review: 0, hint: 0 }
  for (const finding of findings) { const { level } = severityOf(finding.severity); if (level in counts) counts[level]++ }
  return counts
}

export function visibleFindings(findings = [], { level = 'all', query = '' } = {}) {
  const needle = query.trim().toLowerCase()
  return findings
    .filter(finding => level === 'all' || severityOf(finding.severity).level === level)
    .filter(finding => !needle || [finding.title, finding.rule, finding.id, ...(finding.evidence || []).flatMap(e => [e.path, e.method, e.detail])]
      .some(value => typeof value === 'string' && value.toLowerCase().includes(needle)))
    .sort((a, b) => severityOf(a.severity).rank - severityOf(b.severity).rank)
}

export function filterFiles(files = [], query = '') {
  const needle = query.trim().toLowerCase()
  return needle ? files.filter(file => (typeof file === 'string' ? file : file.path || '').toLowerCase().includes(needle)) : files
}
