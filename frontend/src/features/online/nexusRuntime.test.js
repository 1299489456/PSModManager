import test from 'node:test'
import assert from 'node:assert/strict'
import { canInstallFile, createNexusRuntime, joinPieces, latestMainFile } from './nexusRuntime.js'

function bridge(replies, calls = []) {
  return { calls, request: async (command, payload) => { calls.push([command, payload]); const reply = replies[command]; return typeof reply === 'function' ? reply(payload) : reply } }
}
const done = data => ({ task_id: 'owned-job', request_id: 'r', sequence: 2, state: 'succeeded', data: { ok: true, ...data } })
const mods = (from, count) => Array.from({ length: count }, (_, index) => ({ mod_id: from + index, name: `Mod ${from + index}` }))
const flush = () => new Promise(resolve => setImmediate(resolve))

test('manual download import uses selected local identity without requiring a Nexus account or protocol', async () => {
  const source = { source_handle: 'chosen-file', kind: 'zip', display_name: 'Actual local Mod' }
  const b = bridge({ 'package.choose': source }), state = {}, imported = []
  const runtime = createNexusRuntime(b, state, value => imported.push(value))
  state.pendingWebsite = { mod_id: 81, file_id: 11, name: 'Unrelated website title' }
  state.nxmUrl = 'unused-secret'
  await runtime.importLocal('zip')
  assert.deepEqual(b.calls, [['package.choose', { kind: 'zip' }]])
  assert.deepEqual(imported, [source]); assert.equal(state.pendingWebsite, null); assert.equal(state.nxmUrl, '')
})

test('cancelled or rejected local selection does not import or lose the website file', async () => {
  for (const reply of [{}, { error_code: 'IMPORT_INVALID', message: '无法导入此来源。' }]) {
    const state = {}, imported = [], b = bridge({ 'package.choose': reply })
    const runtime = createNexusRuntime(b, state, value => imported.push(value))
    state.pendingWebsite = { mod_id: 81, file_id: 11 }
    await runtime.importLocal('dll')
    assert.equal(imported.length, 0); assert.equal(state.pendingWebsite.file_id, 11)
    if (reply.error_code) assert.equal(state.error, reply.message)
  }
})

test('local file chooser respects active installation and rejects unsupported kinds', async () => {
  const b = bridge({}), state = {}, runtime = createNexusRuntime(b, state, () => {}, { canInstall: () => false })
  await runtime.importLocal('zip'); assert.equal(b.calls.length, 0)
  const other = createNexusRuntime(b, {})
  await other.importLocal('exe'); assert.equal(b.calls.length, 0)
})

test('free account opens the selected website file and matching receipt resumes exactly once', async () => {
  const imported = [], state = {}, b = bridge({
    'nexus.download': done({ requires_website: true, mod_id: 81, file_id: 11 }),
    'nexus.open': { opened: true }, 'nexus.inbox.download': done({ source_handle: 'download', kind: 'zip' }),
  })
  const runtime = createNexusRuntime(b, state, result => imported.push(result))
  state.settings = { configured: true }
  await runtime.download({ file_id: 11, name: 'Old version' }, { mod_id: 81, name: 'Owned Mod' })
  assert.deepEqual(b.calls[1], ['nexus.open', { page: 'files', mod_id: 81, file_id: 11 }])
  assert.equal(state.pendingWebsite.fileName, 'Old version')
  const item = { id: 'receipt', mod_id: 81, file_id: 11 }
  runtime.incoming([item]); runtime.incoming([item]); await flush(); runtime.incoming([item])
  assert.equal(b.calls.filter(([name]) => name === 'nexus.inbox.download').length, 1)
  assert.equal(imported[0].display_name, 'Owned Mod'); assert.equal(state.pendingWebsite, null)
  assert.deepEqual(state.incoming, [])
})

test('foreign file and cold-start receipts wait for explicit confirmation, never arbitrary auto-install', async () => {
  const b = bridge({ 'nexus.inbox.download': done({ source_handle: 'download' }) }), state = {}, imported = []
  const runtime = createNexusRuntime(b, state, result => imported.push(result))
  state.settings = { configured: true }; state.pendingWebsite = { mod_id: 81, file_id: 11 }
  runtime.incoming([{ id: 'foreign', mod_id: 82, file_id: 11 }, { id: 'other-version', mod_id: 81, file_id: 12 }])
  await flush(); assert.equal(b.calls.length, 0)
  state.pendingWebsite = null
  runtime.incoming([{ id: 'cold', mod_id: 81, file_id: 11 }]); await flush()
  assert.equal(b.calls.length, 0)
  await runtime.receive(state.incoming[0]); assert.equal(imported.length, 1)
})

test('cancelling website wait prevents a late authorization from auto-installing', async () => {
  const b = bridge({}), state = {}, runtime = createNexusRuntime(b, state)
  state.settings = { configured: true }; state.pendingWebsite = { mod_id: 81, file_id: 11 }
  runtime.cancelWebsite(); runtime.incoming([{ id: 'late', mod_id: 81, file_id: 11 }]); await flush()
  assert.equal(b.calls.length, 0); assert.equal(state.incoming.length, 1)
})

test('busy import defers matching callback and a download result until the importer is free', async () => {
  let blocked = true, release
  const b = bridge({ 'nexus.inbox.download': () => new Promise(resolve => { release = resolve }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, result => imported.push(result), { canInstall: () => !blocked })
  state.settings = { configured: true }; state.pendingWebsite = { mod_id: 81, file_id: 11 }
  const receipt = [{ id: 'waiting', mod_id: 81, file_id: 11 }]
  runtime.incoming(receipt); await flush(); assert.equal(b.calls.length, 0)
  blocked = false; runtime.incoming(receipt); await flush(); blocked = true
  release(done({ source_handle: 'owned' })); await flush()
  assert.equal(imported.length, 0); assert.equal(state.readyDownload.source_handle, 'owned')
  blocked = false; runtime.incoming([])
  assert.equal(imported.length, 1); assert.equal(state.readyDownload, null)
})

test('expired receipt reports reauthorization without retry loops or importing', async () => {
  const b = bridge({ 'nexus.inbox.download': done({ ok: false, message: '管理器下载链接已过期，请在官网重新生成。' }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, result => imported.push(result))
  state.settings = { configured: true }; state.pendingWebsite = { mod_id: 81, file_id: 11 }
  const item = { id: 'expired', mod_id: 81, file_id: 11 }
  runtime.incoming([item]); await flush(); runtime.incoming([item]); await flush()
  assert.match(state.error, /已过期/); assert.equal(imported.length, 0); assert.equal(b.calls.length, 1)
})

test('protocol conflict is not overwritten without an explicit replace action', async () => {
  const b = bridge({ 'nexus.protocol.register': payload => ({ available: true, conflict: !payload.replace, registered: payload.replace }) })
  const state = {}, runtime = createNexusRuntime(b, state)
  await runtime.registerProtocol(); assert.equal(state.protocol.conflict, true)
  assert.deepEqual(b.calls[0][1], { replace: false })
  await runtime.registerProtocol(true); assert.equal(state.protocol.registered, true)
})

test('failed receipt stays available for deliberate retry without automatic retry loops', async () => {
  let attempts = 0
  const b = bridge({ 'nexus.inbox.download': () => ++attempts === 1 ? done({ ok: false, message: '网络中断' }) : done({ source_handle: 'owned' }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, result => imported.push(result))
  const item = { id: 'retry', mod_id: 81, file_id: 11 }
  state.settings = { configured: true }; state.pendingWebsite = { mod_id: 81, file_id: 11 }
  runtime.incoming([item]); await flush(); runtime.incoming([item]); await flush()
  assert.equal(attempts, 1); assert.equal(state.incoming.length, 1); assert.match(state.error, /网络中断/)
  await runtime.receive(item)
  assert.equal(attempts, 2); assert.equal(imported.length, 1); assert.equal(state.pendingWebsite, null)
})

test('browser launch failure keeps the selected file available for retry', async () => {
  const state = {}, b = bridge({ 'nexus.download': done({ requires_website: true }), 'nexus.open': { opened: false } })
  const runtime = createNexusRuntime(b, state)
  await runtime.download({ file_id: 11 }, { mod_id: 81 })
  assert.match(state.error, /浏览器/); assert.equal(state.pendingWebsite.file_id, 11)
})

test('opening Nexus loads only local settings, never browses or contacts an account', async () => {
  const b = bridge({ 'nexus.settings.get': { configured: false } }), state = {}
  await createNexusRuntime(b, state).open()
  assert.deepEqual(b.calls.map(call => call[0]), ['nexus.settings.get', 'nexus.protocol.status'])
})

test('saving credentials clears input and does not automatically connect', async () => {
  const b = bridge({ 'nexus.settings.save': { configured: true, notice: 'saved' } }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.apiKey = 'owned-secret'
  await runtime.save()
  assert.deepEqual(b.calls.map(call => call[0]), ['nexus.settings.save'])
  assert.equal(state.apiKey, '')
})

test('the list pages through the whole catalogue until the total is reached', async () => {
  const b = bridge({ 'nexus.browse': payload => done({ mods: mods(payload.offset + 1, payload.offset < 40 ? 20 : 3), total: 43, page_size: 20 }) }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.settings = { configured: true }
  await runtime.browse()
  assert.equal(state.mods.length, 20); assert.equal(state.total, 43)
  await runtime.more(); await runtime.more(); await runtime.more()
  assert.equal(state.mods.length, 43)
  assert.deepEqual(b.calls.map(call => call[1].offset), [0, 20, 40])
})

test('a new search replaces the list and ignores late answers from the old one', async () => {
  let release
  const b = bridge({ 'nexus.browse': payload => payload.query === 'old' ? new Promise(resolve => { release = () => resolve(done({ mods: mods(1, 20), total: 99 })) })
    : done({ mods: mods(500, 2), total: 2, page_size: 20 }) }), state = {}
  const runtime = createNexusRuntime(b, state)
  state.settings = { configured: true }
  state.query = 'old'; const first = runtime.browse()
  state.query = ' new '; await runtime.browse()
  release(); await first
  assert.deepEqual(state.mods.map(mod => mod.mod_id), [500, 501])
  assert.equal(state.appliedQuery, 'new')
  assert.equal(b.calls.at(-1)[1].query, 'new')
})

test('translation pieces arrive in order and join back with their separators', () => {
  assert.equal(joinPieces([{ sep: '', text: 'a' }, { sep: '\n', text: 'b' }, { sep: '', text: 'c' }]), 'a\nbc')
})

test('Nexus download hands a granted handle into the existing import flow', async () => {
  const imported = [], state = {}
  const runtime = createNexusRuntime(bridge({ 'nexus.download': done({ source_handle: 'owned-handle', kind: 'zip' }) }), state, value => imported.push(value))
  state.detail = { mod_id: 81, name: 'Owned' }; state.nxmUrl = 'owned-authorization'
  await runtime.download({ file_id: 11 })
  assert.equal(imported[0].source_handle, 'owned-handle')
  assert.equal(imported[0].display_name, 'Owned')
  assert.equal(state.nxmUrl, '')
})

test('download authorization error is visible and does not import', async () => {
  const imported = [], state = {}
  const runtime = createNexusRuntime(bridge({ 'nexus.download': { task_id: 'owned-job', request_id: 'r', sequence: 2, state: 'succeeded', data: { ok: false, message: '需要官网授权' } } }), state, value => imported.push(value))
  state.detail = { mod_id: 81 }
  await runtime.download({ file_id: 11 })
  assert.equal(state.error, '需要官网授权'); assert.equal(imported.length, 0)
})

test('description links go through the fixed link command', async () => {
  const b = bridge({ 'nexus.open': { opened: true } }), state = {}
  await createNexusRuntime(b, state).openLink('https://github.com/owner/repo')
  assert.deepEqual(b.calls[0], ['nexus.open', { page: 'link', url: 'https://github.com/owner/repo' }])
})

test('translation progress fills the reactive state and finishes', async () => {
  const { reactive } = await import('vue').catch(() => ({ reactive: value => new Proxy(value, {}) }))
  const state = reactive({})
  const b = { request: async (command, payload) => command === 'nexus.translate'
    ? { task_id: 't', request_id: 'r', sequence: 1, state: 'running' } : null,
    watchTask: (id, update) => {
      queueMicrotask(() => {
        update({ task_id: 't', request_id: 'r', sequence: 2, type: 'progress', state: 'running', completed: 1, total: 2, data: { part: 'summary', index: 0, sep: '', text: '整理战利品' } })
        update({ task_id: 't', request_id: 'r', sequence: 3, type: 'progress', state: 'running', completed: 2, total: 2, data: { part: 'description', index: 0, sep: '', text: '译文' } })
        update({ task_id: 't', request_id: 'r', sequence: 4, type: 'terminal', state: 'succeeded', data: { completed: true } })
      })
      return { dispose: async () => {}, cancel: async () => {} }
    } }
  const runtime = createNexusRuntime(b, state)
  state.detail = { mod_id: 81 }
  await runtime.translate()
  assert.equal(state.translation.status, 'done')
  assert.equal(runtime.translated('summary'), '整理战利品')
  assert.equal(runtime.translated('description'), '译文')
})

const mainFile = (file_id, uploaded, extra = {}) => ({ file_id, uploaded, name: `Release ${file_id}`, version: `${file_id}.0`, category: 'MAIN', supported: true, size_bytes: 100, ...extra })

test('latest main uses release dates, ignoring newer optional files without mutating catalogue', () => {
  const files = [mainFile(11, 10, { primary: true }), mainFile(12, 20), mainFile(13, 30, { category: 'OPTIONAL' })]
  assert.equal(latestMainFile(files).file_id, 12)
  assert.deepEqual(files.map(file => file.file_id), [11, 12, 13])
  assert.equal(latestMainFile([]), null)
  assert.equal(canInstallFile(mainFile(1, 1, { size_bytes: 67108864 })), true)
  assert.equal(canInstallFile(mainFile(1, 1, { size_bytes: 67108865 })), false)
})

test('ambiguous same-version variants require selection unless one is the primary', () => {
  const files = [mainFile(11, 10, { version: '2.0' }), mainFile(12, 20, { version: '2.0' })]
  assert.equal(latestMainFile(files), null)
  files[0].primary = true
  assert.equal(latestMainFile(files).file_id, 11)
})

test('card install fetches latest main and hands it to the install flow without opening details', async () => {
  const files = [mainFile(11, 10), mainFile(12, 20)]
  const b = bridge({ 'nexus.mod': done({ mod_id: 81, name: 'Owned', files }), 'nexus.download': done({ source_handle: 'owned-handle' }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, value => imported.push(value))
  await runtime.installLatest({ mod_id: 81, name: 'Owned' })
  assert.deepEqual(b.calls, [['nexus.mod', { mod_id: 81 }], ['nexus.download', { mod_id: 81, file_id: 12, nxm_url: '' }]])
  assert.equal(imported[0].display_name, 'Owned')
  assert.equal(state.detail, null); assert.equal(state.versionsOpen, false); assert.equal(state.installingModId, null)
})

test('latest unsupported or oversized file never silently installs an older main', async () => {
  for (const extra of [{ supported: false }, { size_bytes: 67108865 }]) {
    const b = bridge({ 'nexus.mod': done({ mod_id: 81, files: [mainFile(11, 10), mainFile(12, 20, extra)] }) })
    const state = {}, runtime = createNexusRuntime(b, state)
    await runtime.installLatest({ mod_id: 81 })
    assert.equal(b.calls.length, 1); assert.equal(state.versionsOpen, true)
    assert.match(state.notice, /最新主文件/)
  }
})

test('version picker lists all files without downloading then installs only explicitly selected version', async () => {
  const files = [mainFile(11, 10, { category: 'OLD_VERSION' }), mainFile(12, 20)]
  const b = bridge({ 'nexus.mod': done({ mod_id: 81, name: 'Owned', files }), 'nexus.download': done({ source_handle: 'handle' }) })
  const state = {}, runtime = createNexusRuntime(b, state)
  await runtime.chooseVersion({ mod_id: 81 })
  assert.equal(state.versionsOpen, true); assert.deepEqual(state.versions.files, files)
  assert.equal(b.calls.length, 1)
  state.nxmUrl = 'owned-authorized-link'
  await runtime.download(state.versions.files[0], state.versions)
  assert.deepEqual(b.calls[1], ['nexus.download', { mod_id: 81, file_id: 11, nxm_url: 'owned-authorized-link' }])
  assert.equal(state.versionsOpen, false); assert.equal(state.nxmUrl, '')
})

test('free account authorization error opens version picker and preserves chosen mod for retry', async () => {
  const b = bridge({ 'nexus.mod': done({ mod_id: 81, name: 'Owned', files: [mainFile(11, 10)] }),
    'nexus.download': done({ ok: false, message: '请使用官网 nxm 授权链接' }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, value => imported.push(value))
  await runtime.installLatest({ mod_id: 81 })
  assert.equal(state.versionsOpen, true); assert.equal(state.versions.mod_id, 81)
  assert.match(state.error, /nxm/); assert.equal(imported.length, 0)
})

test('closing while file lookup is pending does not reopen picker or download late result', async () => {
  let finish
  const b = bridge({ 'nexus.mod': () => new Promise(resolve => { finish = resolve }) })
  const state = {}, runtime = createNexusRuntime(b, state)
  const pending = runtime.chooseVersion({ mod_id: 81 })
  runtime.closeVersions()
  finish(done({ mod_id: 81, files: [mainFile(11, 10)] })); await pending
  assert.equal(state.versionsOpen, false); assert.equal(state.installingModId, null)
  assert.equal(b.calls.some(([command]) => command === 'nexus.download'), false)
  assert.equal(b.calls.some(([command]) => command === 'task.cancel'), true)
})

test('cancelling latest preparation before task id arrives prevents follow-on download', async () => {
  let finish
  const b = bridge({ 'nexus.mod': () => new Promise(resolve => { finish = resolve }) })
  const state = {}, runtime = createNexusRuntime(b, state)
  const pending = runtime.installLatest({ mod_id: 81 })
  runtime.cancel()
  finish(done({ mod_id: 81, files: [mainFile(11, 10)] })); await pending
  assert.equal(state.versionsOpen, false); assert.equal(state.error, '已取消。')
  assert.equal(b.calls.some(([command]) => command === 'nexus.download'), false)
})

test('download retains initiating mod identity if another detail is opened before completion', async () => {
  let finish
  const b = bridge({ 'nexus.download': () => new Promise(resolve => { finish = resolve }) })
  const state = {}, imported = [], runtime = createNexusRuntime(b, state, value => imported.push(value))
  state.detail = { mod_id: 81, name: 'First Mod' }
  const pending = runtime.download(mainFile(11, 10))
  state.detail = { mod_id: 82, name: 'Second Mod' }
  finish(done({ source_handle: 'owned-handle' })); await pending
  assert.equal(imported[0].display_name, 'First Mod')
})
