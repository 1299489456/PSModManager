import test from 'node:test'
import assert from 'node:assert/strict'
import { createModWorkspaceRuntime, fileTree } from './modWorkspaceRuntime.js'

function fixture() {
  const calls = [], items = ['a', 'b', 'c'].map(id => ({ id, display_name: id, state: id === 'a' ? 'enabled' : 'not_installed', file_count: 1 }))
  let saved = ['a'], fail = false
  const workspace = () => ({ items: structuredClone(items), enabled_ids: [...saved], snapshot_id: 'snapshot', order_message: 'display only' })
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (command === 'mod.workspace') return workspace()
    if (command === 'mod.workspace.save') {
      if (fail) throw new Error('文件被占用')
      saved = [...payload.enabled_ids]
      return { completed: true, workspace: workspace() }
    }
    if (command === 'mod.workspace.detail') return { files: [{ relative_path: `Mods/${payload.id}.dll` }], total: 1, next_offset: null }
    throw new Error(command)
  } }
  const state = {}, runtime = createModWorkspaceRuntime(bridge, state)
  return { runtime, state, calls, items, setFail: value => { fail = value } }
}

test('drag edits a local draft; one save applies the complete loading list', async () => {
  const { runtime, state, calls } = fixture()
  await runtime.setInstallation('installation')
  runtime.move(['b', 'c'], true, 'a')
  assert.deepEqual(state.enabledIds, ['b', 'c', 'a'])
  assert.equal(calls.filter(row => row.command.includes('save')).length, 0)
  runtime.move(['a'], false)
  assert.equal(await runtime.save(), true)
  assert.deepEqual(calls.at(-1).payload.enabled_ids, ['b', 'c'])
  assert.equal(state.dirty, false)
  assert.match(state.notice, /已保存并应用/)
})

test('undo redo and discard restore enable and display-order changes together', async () => {
  const { runtime, state } = fixture(); await runtime.setInstallation('installation')
  runtime.move(['b'], true); runtime.move(['a'], true)
  assert.deepEqual(state.enabledIds, ['b', 'a'])
  runtime.undo(); assert.deepEqual(state.enabledIds, ['a', 'b'])
  runtime.redo(); assert.deepEqual(state.enabledIds, ['b', 'a'])
  runtime.discard(); assert.deepEqual(state.enabledIds, ['a']); assert.equal(state.dirty, false)
})

test('failed save retains draft and cannot report success or allow launch', async () => {
  const { runtime, state, setFail } = fixture(); await runtime.setInstallation('installation')
  runtime.move(['b'], true); setFail(true)
  assert.equal(await runtime.save(), false)
  assert.deepEqual(state.enabledIds, ['a', 'b']); assert.equal(state.dirty, true)
  assert.equal(state.notice, ''); assert.equal(state.error, '文件被占用'); assert.equal(state.busy, false)
  setFail(false); assert.equal(await runtime.save(), true)
})

test('draft is preserved while switching between games', async () => {
  const { runtime, state } = fixture(); await runtime.setInstallation('first')
  runtime.move(['b'], true); await runtime.setInstallation('second'); await runtime.setInstallation('first')
  assert.deepEqual(state.enabledIds, ['a', 'b']); assert.equal(state.dirty, true)
})

test('newly imported Mod appears without discarding an existing draft', async () => {
  const { runtime, state, items } = fixture(); await runtime.setInstallation('installation')
  runtime.move(['b'], true)
  items.push({ id: 'new', release_id: 'new-release', display_name: 'New', state: 'not_installed' })
  assert.equal(await runtime.reveal('new-release'), 'new')
  assert.deepEqual(state.enabledIds, ['a', 'b']); assert.equal(state.dirty, true)
  assert.equal(state.detail.files[0].relative_path, 'Mods/new.dll')
})

test('changed or unknown files cannot be silently toggled', async () => {
  const { runtime, state } = fixture(); await runtime.setInstallation('installation')
  state.items[1].state = 'drifted'; assert.equal(runtime.move(['b'], true), false)
  state.items[2].manageable = false; assert.equal(runtime.move(['c'], true), false)
  assert.deepEqual(state.enabledIds, ['a'])
})

test('Ctrl and Shift select groups without modifying the loading list', async () => {
  const { runtime, state } = fixture(); await runtime.setInstallation('installation')
  await runtime.select('a'); await runtime.select('c', { shiftKey: true })
  assert.deepEqual(state.selectedIds, ['a', 'b', 'c']); assert.deepEqual(state.enabledIds, ['a'])
  await runtime.select('b', { ctrlKey: true }); assert.deepEqual(state.selectedIds, ['a', 'c'])
})

test('nested resources become a sorted file tree, not additional Mods', () => {
  const tree = fileTree([{ relative_path: 'Mods/Demo.dll' }, { relative_path: 'Mods/Demo/textures/item.png', size: 42 }, { relative_path: 'Plugins/P.dll' }])
  assert.deepEqual(tree.map(node => node.name), ['Mods', 'Plugins'])
  assert.equal(tree[0].children[0].children[0].children[0].file.size, 42)
  assert.equal(tree[0].children[1].name, 'Demo.dll')
})

test('task-style save waits for completion before reporting applied state', async () => {
  let update, disposed = 0
  const state = {}, workspace = { items: [{ id: 'a', display_name: 'A', state: 'not_installed' }], enabled_ids: [], snapshot_id: 'snapshot' }
  const bridge = { request: async command => command === 'mod.workspace' ? workspace : { task_id: 'save-task', state: 'running' }, watchTask(id, callback) { update = callback; return { dispose() { disposed++ } } } }
  const runtime = createModWorkspaceRuntime(bridge, state); await runtime.setInstallation('game'); runtime.move(['a'], true)
  const saving = runtime.save(); await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(state.busy, true); assert.equal(state.dirty, true)
  update({ task_id: 'save-task', state: 'succeeded', data: { completed: true, workspace: { ...workspace, enabled_ids: ['a'] } } })
  assert.equal(await saving, true); assert.equal(state.dirty, false); assert.equal(disposed, 1)
})

test('failed task shows its error code and leaves the desired list intact', async () => {
  const state = {}, workspace = { items: [{ id: 'a', display_name: 'A', state: 'not_installed' }], enabled_ids: [], snapshot_id: 'snapshot' }
  const runtime = createModWorkspaceRuntime({ request: async command => command === 'mod.workspace' ? workspace : { task_id: 'save', state: 'failed', message_code: 'MOD_BUSY', data: { error_code: 'MOD_BUSY', message: '请先退出游戏。' } } }, state)
  await runtime.setInstallation('game'); runtime.move(['a'], true)
  assert.equal(await runtime.save(), false)
  assert.match(state.error, /请先退出游戏.*MOD_BUSY/)
  assert.deepEqual(state.enabledIds, ['a']); assert.equal(state.dirty, true)
})

test('uninstall retains library entry and is disabled while the loading draft is dirty', async () => {
  const calls = [], state = {}, item = { id: 'a', release_id: 'release', manifest_hash: 'manifest', display_name: 'A', state: 'enabled' }
  const workspace = () => ({ items: [{ ...item }], enabled_ids: item.state === 'enabled' ? ['a'] : [], snapshot_id: 'snapshot' })
  const runtime = createModWorkspaceRuntime({ request: async (command, payload) => {
    calls.push({ command, payload })
    if (command === 'mod.workspace') return workspace()
    if (command === 'mod.action') { item.state = 'not_installed'; return { completed: true, state: item.state } }
    throw new Error(command)
  } }, state)
  await runtime.setInstallation('game'); runtime.move(['a'], false)
  assert.equal(await runtime.uninstall(state.items[0]), false)
  runtime.discard(); assert.equal(await runtime.uninstall(state.items[0]), true)
  assert.equal(calls.find(row => row.command === 'mod.action').payload.action, 'uninstall')
  assert.equal(state.items.length, 1); assert.deepEqual(state.enabledIds, [])
})

test('selecting a new version replaces the same Mod only in the unsaved draft and undo restores it', async () => {
  const f = fixture(); f.items[0].package_id = 'same-mod'; f.items[1].package_id = 'same-mod'
  await f.runtime.setInstallation('installation')
  f.runtime.move(['b'], true)
  assert.deepEqual(f.state.enabledIds, ['b']); assert.deepEqual(f.state.baseEnabled, ['a'])
  assert.match(f.state.notice, /旧版本/)
  assert.equal(f.calls.filter(row => row.command === 'mod.workspace.save').length, 0)
  f.runtime.undo(); assert.deepEqual(f.state.enabledIds, ['a'])
})
