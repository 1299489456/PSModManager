import test from 'node:test'
import assert from 'node:assert/strict'
import { evidenceText, sessionLabel, classifications, scanLabel, historicalReference, ruleTitles } from './presentation.js'
test('raw byte windows reconstruct Chinese split across a UTF8 boundary', () => {
  const bytes = Buffer.from('原始中文证据\n')
  assert.equal(evidenceText([{ raw_base64: bytes.subarray(0, 4).toString('base64') }, { raw_base64: bytes.subarray(4).toString('base64') }]), '原始中文证据\n')
})
test('unknown session dates stay unknown and explicit dates remain their own dates', () => {
  assert.match(sessionLabel({}), /^时间未知/)
  assert.match(sessionLabel({ started_at: '2026-09-28T11:00:00Z', pid: 3 }), /2026-09-28/)
  assert.doesNotMatch(sessionLabel({}), /09-24/)
  assert.equal(classifications.confirmed_failure, '日志确认失败')
})
test('job success distinguishes complete partial failed and superseded scan outcomes', () => {
  for (const [status, expected] of Object.entries({ complete: '扫描完成', partial: '扫描不完整', failed: '扫描失败', superseded: '已有更新的扫描结果' })) assert.equal(scanLabel({ state: 'succeeded', data: { status } }), expected)
  assert.equal(scanLabel({ state: 'cancelled', data: { status: 'partial' } }), '扫描已取消')
  assert.equal(scanLabel({ state: 'succeeded' }), '扫描已结束 · 结果待核实')
})
const matchedReference = { date:'2026-09-24', sha256:'1eb5a7730f09c33342d18521dd7c50b09ca80c56fe3cd50e4a36cb7d3ec53f1a', basis:'known_reference_hash' }
test('shadow rule explains higher version preference as a prediction', () => {
  assert.equal(ruleTitles['ML073-SHADOW'], '预计优先加载同名的更高版本')
  assert.doesNotMatch(ruleTitles['ML073-SHADOW'], /删除|已加载|遮蔽/)
})
test('exact backend reference annotation labels history without inventing a started_at timestamp', () => {
  const session = { started_at:null, historical_reference:matchedReference }
  const before = JSON.stringify(session)
  assert.match(sessionLabel(session), /^2026-09-24 历史日志/)
  assert.equal(historicalReference(session), matchedReference)
  assert.equal(JSON.stringify(session), before)
  assert.equal(session.started_at, null)
})
test('invalid reference annotations never manufacture the September 24 history label', () => {
  for (const reference of [null, {}, {...matchedReference,basis:'filename'}, {...matchedReference,sha256:'not-a-hash'},
    {...matchedReference,date:'2026-02-30'}, {...matchedReference,date:'2026-09-24T00:00:00Z'}, {...matchedReference,date:20260924}]) {
    const session = {historical_reference:reference}
    assert.equal(historicalReference(session), null)
    assert.match(sessionLabel(session), /^时间未知/)
    assert.doesNotMatch(sessionLabel(session), /2026-09-24/)
  }
})
test('reference date never replaces an explicit original session timestamp', () => {
  const session = {started_at:'2026-09-24T14:16:46+08:00',historical_reference:matchedReference}
  assert.match(sessionLabel(session), /2026-09-24 历史日志/)
  assert.ok(sessionLabel(session).includes(session.started_at))
  assert.equal(session.started_at,'2026-09-24T14:16:46+08:00')
})
