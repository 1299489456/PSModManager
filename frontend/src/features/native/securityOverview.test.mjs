import test from 'node:test'
import assert from 'node:assert/strict'
import { groupOf, summarize, visibleChecks, quickTargets, severityOf, findingCounts, visibleFindings, filterFiles } from './securityOverview.js'

const mods = [
  { id: 'a', name: 'Alpha' }, { id: 'b', name: 'Beta', author: 'Kim' }, { id: 'c', name: 'Gamma' },
  { id: 'd', name: 'Delta' }, { id: 'e', name: 'Echo', workshop_id: '123' }, { id: 'f', name: 'Foxtrot' },
]
const items = {
  a: { state: 'reviewed', scanned_at: '2026-10-29T00:00:00Z' },
  b: { state: 'high_risk', verdict: 'high_risk', scanned_at: '2026-10-30T00:00:00Z' },
  c: { state: 'stale' },
  d: { state: 'attention', scanned_at: '2026-10-28T00:00:00Z' },
  e: { state: 'scanning' },
}

test('groups follow the visible security label, including quarantine and native refusals', () => {
  assert.equal(groupOf(items.a), 'ok')
  assert.equal(groupOf(items.b), 'care')
  assert.equal(groupOf(items.c), 'stale')
  assert.equal(groupOf(items.d), 'care')
  assert.equal(groupOf(items.e), 'scanning')
  assert.equal(groupOf(undefined), 'unscanned')
  assert.equal(groupOf({ state: 'reviewed', disposition: { mode: 'quarantine', status: 'applied' } }), 'care')
  assert.equal(groupOf({ state: 'reviewed', review_reasons: ['SECURITY_UNDECLARED_CAPABILITIES'] }), 'care')
  assert.equal(groupOf({ state: 'incomplete' }), 'care')
})

test('summary counts every mod once', () => {
  assert.deepEqual(summarize(mods, items), { all: 6, care: 2, stale: 1, unscanned: 1, scanning: 1, ok: 1 })
})

test('risk sort puts red before amber, then changed, unchecked, running and clean', () => {
  assert.deepEqual(visibleChecks(mods, items).map(m => m.id), ['b', 'd', 'c', 'f', 'e', 'a'])
  assert.deepEqual(visibleChecks(mods, items, { sort: 'name' }).map(m => m.id), ['a', 'b', 'd', 'e', 'f', 'c'])
  assert.deepEqual(visibleChecks(mods, items, { sort: 'checked' }).map(m => m.id).slice(0, 3), ['b', 'a', 'd'])
})

test('filters combine query, group and enabled-only without mutating the input', () => {
  const copy = mods.map(m => m.id)
  assert.deepEqual(visibleChecks(mods, items, { group: 'care' }).map(m => m.id), ['b', 'd'])
  assert.deepEqual(visibleChecks(mods, items, { query: 'kim' }).map(m => m.id), ['b'])
  assert.deepEqual(visibleChecks(mods, items, { query: '123' }).map(m => m.id), ['e'])
  assert.deepEqual(visibleChecks(mods, items, { enabledOnly: true, enabledIds: ['a', 'c'] }).map(m => m.id), ['c', 'a'])
  assert.deepEqual(mods.map(m => m.id), copy)
})

test('quick targets list unchecked and changed mods for one-click checking', () => {
  assert.deepEqual(quickTargets(mods, items), { unscanned: ['f'], stale: ['c'] })
})

test('findings are counted, filtered and searched by severity and evidence', () => {
  const findings = [
    { id: 'F1', severity: 'info', title: 'Networking', rule: 'capability_call', evidence: [{ path: 'Main.dll', method: 'Net::Get', detail: 'HttpClient' }] },
    { id: 'F2', severity: 'high', title: 'Combination', rule: 'combo', evidence: [{ path: 'Lib.dll', method: 'Steal::Run' }] },
    { id: 'F3', severity: 'warning', title: 'Reflection', rule: 'reflection' },
  ]
  assert.deepEqual(findingCounts(findings), { all: 3, high: 1, review: 1, hint: 1 })
  assert.deepEqual(visibleFindings(findings).map(f => f.id), ['F2', 'F3', 'F1'])
  assert.deepEqual(visibleFindings(findings, { level: 'review' }).map(f => f.id), ['F3'])
  assert.deepEqual(visibleFindings(findings, { query: 'steal' }).map(f => f.id), ['F2'])
  assert.equal(severityOf('unexpected').label, 'unexpected')
})

test('file filtering accepts string and object rows', () => {
  assert.deepEqual(filterFiles(['a/Main.dll', { path: 'Textures/x.png' }], 'tex'), [{ path: 'Textures/x.png' }])
  assert.equal(filterFiles([1, 2], '').length, 2)
})
