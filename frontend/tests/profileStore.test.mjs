import test from 'node:test'
import assert from 'node:assert/strict'
import { createProfileStore } from '../src/features/profiles/profileStore.js'
import { filterRows } from '../src/features/profiles/profileSelection.js'

const profile = (extra = {}) => ({ profile_id: 'p1', installation_id: 'i1', name: '配置甲', revision: 7, committed_revision: 3, selections: [], groups: [], can_undo: true, can_redo: false, dirty: true, ...extra })
function fixture(handler = () => profile()) {
  const state = {}, calls = []; let update, cancelled = 0, disposed = 0
  const bridge = {
    async request(command, payload) { calls.push({ command, payload }); return handler(command, payload) },
    watchTask(id, callback) { update = callback; return { async cancel() { cancelled++ }, async dispose() { disposed++ } } },
  }
  const store = createProfileStore(bridge, state)
  return { state, store, calls, finish(data, status = 'succeeded') { update({ state: status, data }) }, get cancelled() { return cancelled }, get disposed() { return disposed } }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

test('profile reopen restores persisted selected profile and drafts without a local storage copy', async () => {
  const f = fixture(command => command === 'profile.list' ? { profiles: [profile(), profile({ profile_id: 'p2', name: '草稿乙' })], selected_profile_id: 'p2' } : profile({ profile_id: 'p2', name: '草稿乙' }))
  await f.store.setInstallation('i1'); await f.store.refresh()
  assert.equal(f.state.current.profile_id, 'p2'); assert.equal(f.state.current.revision, 7); assert.equal(f.state.current.dirty, true)
  assert.deepEqual(f.calls[0], { command: 'profile.list', payload: { installation_id: 'i1' } })
})

test('profile single and batch enable form one server edit with optimistic revision', async () => {
  const f = fixture(); f.state.current = profile()
  await f.store.setEnabled(['r1', 'r2', 'r1'], false)
  assert.deepEqual(f.calls[0].payload, { profile_id: 'p1', expected_revision: 7, action: { op: 'set_enabled', release_ids: ['r1', 'r2'], selected: false } })
  await f.store.setEnabled(['r1'], true); assert.equal(f.calls.length, 2)
  assert.equal(f.calls[1].payload.action.selected, true)
})

test('profile hash locked releases use server integrity and preserve unknown version', async () => {
  const f = fixture(() => profile({ selections: [{ release_id: 'r', manifest_hash: 'hash', version_raw: null, selected: true, resolved: false }] })); f.state.current = profile()
  await f.store.add({ release_id: 'bad', integrity: 'corrupt' }); assert.equal(f.calls.length, 0)
  await f.store.add({ release_id: 'r', manifest_hash: 'hash', integrity: 'verified' })
  assert.deepEqual(f.calls[0].payload.action, { op: 'set_selection', selection: { release_id: 'r', manifest_hash: 'hash', selected: true } })
  assert.equal(f.state.current.selections[0].version_raw, null); assert.equal(f.state.current.selections[0].resolved, false)
  assert.match(f.state.notice, /缺少匹配文件/)
})

test('profile add returns server row outside the current package page and enables non-Mod-role packages too', async () => {
  const release = { release_id: 'r25', manifest_hash: 'hash', display_name: '安装目录 25', integrity: 'verified', roles: ['dependency'] }
  const added = { release_id: 'r25', manifest_hash: 'hash', display_name: '安装目录 25', selected: true, resolved: true, version_raw: null }
  const f = fixture(() => profile({ selections: [added] })); f.state.current = profile()
  f.state.packages = [{ release_id: 'unrelated', display_name: '第一页内容' }]; f.state.packageOffset = 20
  const result = await f.store.add(release)
  assert.equal(result, f.state.current); assert.deepEqual(result.selections, [added])
  assert.match(f.state.notice, /安装目录 25/); assert.match(f.state.notice, /配置甲/)
  assert.equal(filterRows(result.selections, true, '旧搜索条件', item => item.display_name).length, 0)
  assert.deepEqual(filterRows(result.selections, true, '', item => item.display_name), [added])
  assert.deepEqual(f.state.packages, [{ release_id: 'unrelated', display_name: '第一页内容' }])
})

test('profile add failure never returns a successful relocation target', async () => {
  const release = { release_id: 'r', manifest_hash: 'hash', integrity: 'verified' }
  const missing = fixture(() => profile()); missing.state.current = profile()
  assert.equal(await missing.store.add(release), undefined)
  assert.match(missing.state.error, /没有找到/); assert.equal(missing.state.notice, '')
  const refused = fixture(() => { throw Object.assign(new Error('stale'), { code: 'PROFILE_REVISION_CONFLICT' }) }); refused.state.current = profile()
  assert.equal(await refused.store.add(release), undefined)
  assert.match(refused.state.error, /其他窗口/); assert.equal(refused.state.notice, '')
})

test('profile move binds the current revision and one server action for ordered batch selection', async () => {
  const result = profile({ revision: 8, selections: [{ release_id: 'r2' }, { release_id: 'r1' }] })
  const f = fixture(() => result); f.state.current = profile()
  assert.equal(await f.store.move(['r2', 'r1', 'r2'], false, 'anchor'), result)
  assert.deepEqual(f.calls[0].payload, { profile_id: 'p1', expected_revision: 7, action: { op: 'move_selections', release_ids: ['r2', 'r1'], selected: false, before_release_id: 'anchor' } })
  await f.store.move(['r2'], true)
  assert.equal(f.calls[1].payload.expected_revision, 8); assert.equal(f.calls[1].payload.action.before_release_id, null)
})

test('profile group, undo and redo use persisted history without deployment writes', async () => {
  const f = fixture(() => profile({ can_redo: true })); f.state.current = profile()
  await f.store.setGroup(['r1','r2'], ' 中文 分组 '); await f.store.undo(); await f.store.redo()
  assert.deepEqual(f.calls.map(x => x.payload.action.op), ['set_group', 'undo', 'redo'])
  assert.equal(f.calls[0].payload.action.group, '中文 分组')
  assert.ok(f.calls.every(x => x.command === 'profile.edit'))
})

test('profile empty group removes membership with null instead of invalid empty name', async () => {
  const f = fixture(); f.state.current = profile()
  await f.store.setGroup(['r1'], '   ')
  assert.deepEqual(f.calls[0].payload.action, { op: 'set_group', release_ids: ['r1'], group: null })
})

test('profile commit task waits for server and reports saved pending application only', async () => {
  const f = fixture(command => command === 'profile.commit' ? { task_id: 't', state: 'running' } : profile({ dirty: false, committed_revision: 7 })); f.state.current = profile()
  const saving = f.store.save(); await tick(); await f.store.save(); assert.equal(f.calls.length, 1); assert.equal(f.state.busy, true)
  f.finish(profile({ dirty: false, committed_revision: 7 })); await saving
  assert.equal(f.state.current.dirty, false); assert.equal(f.state.current.committed_revision, 7); assert.match(f.state.notice, /待应用/)
  assert.equal(f.state.deployment, undefined); assert.equal(f.disposed, 1)
})

test('profile stale revision errors never replace local view with invented success', async () => {
  const f = fixture(() => { throw Object.assign(new Error('rejected'), { code: 'PROFILE_REVISION_CONFLICT' }) }); f.state.current = profile()
  await f.store.rename('新名称'); assert.equal(f.state.current.name, '配置甲'); assert.match(f.state.error, /其他窗口/); assert.equal(f.state.busy, false)
})

test('profile selection is persisted by profile.select; clone binds revision', async () => {
  const f = fixture(); await f.store.select('p1'); await f.store.clone('副本')
  assert.equal(f.calls[0].command, 'profile.select'); assert.deepEqual(f.calls[1].payload, { profile_id: 'p1', expected_revision: 7, name: '副本' })
})

test('profile manifest preview and commit use source handle, hash and installation', async () => {
  const f = fixture(command => command === 'profile.share_choose' ? { source_handle: 'h' } : command === 'profile.share_preview' ? { preview_hash: 'hash', selections: [] } : profile())
  await f.store.setInstallation('i1'); await f.store.chooseImport(); await f.store.importCommit('导入配置')
  assert.deepEqual(f.calls[2].payload, { source_handle: 'h', preview_hash: 'hash', installation_id: 'i1', name: '导入配置' }); assert.equal(f.state.importPreview, null)
})

test('profile manifest chooser returns a named preview and reports missing versions before explicit confirmation', async () => {
  const selections = [{ release_id: 'present', resolved: true, display_name: '本地依赖库' }, { release_id: 'missing', resolved: false }]
  const preview = { preview_hash: 'hash', name: '刚刚导出的方案', selections }
  const imported = profile({ name: preview.name, selections })
  const f = fixture(command => command === 'profile.share_choose' ? { source_handle: 'source' } : command === 'profile.share_preview' ? preview : imported)
  await f.store.setInstallation('i1')
  assert.equal(await f.store.chooseImport(), preview)
  assert.equal(f.state.current, null); assert.equal(f.calls.length, 2)
  assert.match(f.state.notice, /刚刚导出的方案/); assert.match(f.state.notice, /2 个 Mod 版本/)
  assert.match(f.state.notice, /1 个缺少匹配文件/); assert.match(f.state.notice, /确认导入/)
  assert.equal(await f.store.importCommit(preview.name), imported)
  assert.equal(f.state.current.selections.length, 2); assert.match(f.state.notice, /已导入并切换/)
  assert.match(f.state.notice, /尚未应用到游戏/); assert.match(f.state.notice, /1 个缺少匹配文件/)
})

test('profile picker cancellation is a non-error and export displays only backend manifest', async () => {
  const f = fixture(command => command === 'profile.share_choose' ? { source_handle: null } : command === 'profile.export' ? { manifest: { schema_version: 2, selections: [] } } : { saved: false, cancelled: true })
  await f.store.setInstallation('i1'); await f.store.chooseImport(); assert.equal(f.state.error, ''); assert.match(f.state.notice, /取消/)
  f.state.current = profile(); await f.store.exportPreview(); assert.deepEqual(JSON.parse(f.state.exportText), { schema_version: 2, selections: [] })
  await f.store.exportFile(); assert.equal(f.calls.at(-1).command, 'profile.save_export'); assert.match(f.state.notice, /取消/)
})

test('profile package listing consumes real asynchronous page and supports cancellation', async () => {
  const f = fixture(() => ({ task_id: 't', state: 'running' })); const listing = f.store.packages(20); await tick()
  await f.store.cancel(); assert.equal(f.cancelled, 1)
  f.finish({ releases: [{ release_id: 'r', integrity: 'verified' }] }); await listing
  assert.equal(f.state.packages[0].release_id, 'r'); assert.equal(f.state.packageOffset, 20); assert.deepEqual(f.calls[0].payload, { offset: 20, limit: 20 })
})

test('profile installation change discards delayed old responses', async () => {
  let resolve
  const f = fixture(() => new Promise(done => { resolve = done }))
  await f.store.setInstallation('old'); const refreshing = f.store.refresh(); await tick(); await f.store.setInstallation('new')
  resolve({ profiles: [profile()], selected_profile_id: 'p1' }); await refreshing
  assert.equal(f.state.installationId, 'new'); assert.equal(f.state.current, null)
})

test('profile dispose releases subscription and resolves outstanding task without applying it', async () => {
  const f = fixture(() => ({ task_id: 't', state: 'running' })); const listing = f.store.packages(); await tick(); await f.store.dispose(); await listing
  assert.equal(f.disposed, 1); assert.deepEqual(f.state.packages, [])
})

test('profile case027 delayed package task is cancelled when installation changes before initial response', async () => {
  let resolve
  const f=fixture(command=>command==='package.list' ? new Promise(done=>{resolve=done}) : {state:'cancelled'})
  const pending=f.store.packages(); await tick(); await f.store.setInstallation('new')
  resolve({task_id:'old',state:'running'}); await pending
  assert.deepEqual(f.calls[1],{command:'task.cancel',payload:{task_id:'old'}}); assert.deepEqual(f.state.packages,[])
})
test('profile case027 delayed task is cancelled on unmount and active subscription cancels before release', async () => {
  let resolve
  const f=fixture(command=>command==='package.list' ? new Promise(done=>{resolve=done}) : {state:'cancelled'})
  const pending=f.store.packages(); await tick(); await f.store.dispose(); resolve({task_id:'old',state:'running'}); await pending
  assert.equal(f.calls[1].command,'task.cancel')
  const active=fixture(()=>({task_id:'task',state:'running'})); const listing=active.store.packages(); await tick(); await active.store.dispose(); await listing
  assert.equal(active.cancelled,1); assert.equal(active.disposed,1)
})
test('profile case027 package detail binds version hash and resets drifting paged ownership', async () => {
  const f=fixture((command,payload)=>({release_id:'r',manifest_hash:'m',detail_hash:payload.offset?'new':'stable',offset:payload.offset,files:[]}))
  await f.store.details({release_id:'r',manifest_hash:'m'})
  await f.store.details({release_id:'r',manifest_hash:'m'},20)
  assert.equal(f.calls.length,3); assert.deepEqual(f.calls[2].payload,{release_id:'r',manifest_hash:'m',offset:0,limit:20})
  assert.equal(f.state.packageDetail.offset,0); assert.match(f.state.notice,/第一页/)
})
test('profile case027 cancelling a replacement manifest picker invalidates the previous preview', async () => {
  const f=fixture(()=>({source_handle:null})); await f.store.setInstallation('i1')
  f.state.sourceHandle='old'; f.state.importPreview={preview_hash:'old'}
  await f.store.chooseImport(); await f.store.importCommit('stale')
  assert.equal(f.state.sourceHandle,null); assert.equal(f.state.importPreview,null); assert.equal(f.calls.length,1)
})
test('profile case027 failed package detail never leaves an old valid-looking detail visible', async () => {
  const f=fixture(()=>{throw new Error('unavailable')}); f.state.packageDetail={release_id:'old',detail_hash:'old'}
  await f.store.details({release_id:'new',manifest_hash:'hash'})
  assert.equal(f.state.packageDetail,null); assert.ok(f.state.error)
})

test('profile case027 changed manifest gives a concrete reselect action without creating another draft', async () => {
  const f = fixture(() => { throw Object.assign(new Error('bridge failed'), { code: 'PROFILE_SOURCE_CHANGED' }) })
  await f.store.setInstallation('i1')
  f.state.sourceHandle = 'old'; f.state.importPreview = { preview_hash: 'old' }
  await f.store.importCommit('Imported')
  assert.match(f.state.error, /重新选择/)
  assert.equal(f.state.current, null)
  assert.equal(f.calls.length, 1)
})
