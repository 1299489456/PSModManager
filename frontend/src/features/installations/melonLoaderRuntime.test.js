import test from 'node:test'
import assert from 'node:assert/strict'
import { createMelonLoaderRuntime, melonLoaderStatusText, melonLoaderCheckText, melonLoaderGenerationNotice, melonLoaderProgress } from './melonLoaderRuntime.js'

const tick = () => new Promise(resolve => setTimeout(resolve, 0))
const status = (extra = {}) => ({ status: 'not_installed', version_raw: null, recommended_version: '0.7.3',
  ready: false, message: '需要 MelonLoader 才能加载 Mod。', can_install: true, install_label: '安装 MelonLoader',
  checks: [{ id: 'loader', label: 'MelonLoader', status: 'missing', detail: '未找到加载器。' }], ...extra })
const installed = () => status({ status: 'installed', version_raw: '0.7.3', ready: true, message: '运行环境已就绪。',
  checks: [{ id: 'loader', label: 'MelonLoader', status: 'ok', detail: '已安装 0.7.3。' }] })
const result = () => ({ installed: true, files_written: 18, status: installed() })

function fixture(overrides = {}) {
  const calls = [], watchers = []
  let serial = 0
  const bridge = {
    async request(command, payload) {
      calls.push([command, structuredClone(payload)])
      if (overrides[command]) return overrides[command](payload)
      if (command === 'loader.status') return status()
      if (command === 'loader.choose') return { source_handle: 'authorized-zip' }
      if (command === 'loader.install' || command === 'loader.install_local') return { task_id: `install-${++serial}`, state: 'queued' }
      throw new Error(command)
    },
    watchTask(task_id, update, error) {
      const handle = { task_id, update, error, disposed: false, cancelled: false,
        async dispose() { this.disposed = true },
        async cancel() { this.cancelled = true; update({ task_id, state: 'cancelled', data: {} }) },
      }
      watchers.push(handle)
      return handle
    },
  }
  const state = {}, runtime = createMelonLoaderRuntime(bridge, state)
  return { calls, watchers, bridge, state, runtime }
}

test('installation selection only detects locally and does not start a download', async () => {
  const { calls, state, runtime } = fixture()
  assert.equal(await runtime.setInstallation('installation'), true)
  assert.deepEqual(calls, [['loader.status', { installation_id: 'installation' }]])
  assert.equal(melonLoaderStatusText(state), '未安装 MelonLoader')
  assert.equal(runtime.canInstall(), true)
  runtime.dispose()
})

test('offline repair uses only selected installation and displays the returned dependency state', async () => {
  const needsRepair = status({ status: 'installed', generation_dependencies: { can_repair: true, ready: false } })
  const { calls, state, runtime } = fixture({ 'loader.status': () => needsRepair,
    'loader.repair': () => ({ repaired: true, files_written: 3, status: installedWithDependencies() }) })
  function installedWithDependencies() { return { ...installed(), generation_dependencies: { can_repair: false, ready: true } } }
  await runtime.setInstallation('installation')
  assert.equal(runtime.canRepair(), true)
  assert.equal(await runtime.repair(), true)
  assert.deepEqual(calls[1], ['loader.repair', { installation_id: 'installation' }])
  assert.equal(runtime.canRepair(), false)
  assert.match(state.notice, /无需联网/)
  assert.equal(state.busy, false)
  runtime.dispose()
})

test('unsupported dependency versions never enable offline repair', async () => {
  const { calls, runtime } = fixture({ 'loader.status': () => status({ generation_dependencies: { can_repair: false, ready: false } }) })
  await runtime.setInstallation('installation')
  assert.equal(await runtime.repair(), false)
  assert.equal(calls.length, 1)
  runtime.dispose()
})

test('status jobs consume task data and release their subscription', async () => {
  const { state, runtime, watchers } = fixture({ 'loader.status': () => ({ task_id: 'status', state: 'running' }) })
  const detecting = runtime.setInstallation('installation')
  await tick()
  assert.equal(state.refreshing, true)
  assert.equal(runtime.canInstall(), false)
  assert.equal(melonLoaderStatusText(state), '正在检测 MelonLoader…')
  watchers[0].update({ task_id: 'status', state: 'succeeded', data: installed() })
  assert.equal(await detecting, true)
  assert.equal(melonLoaderStatusText(state), 'MelonLoader 0.7.3 · 环境已就绪')
  assert.equal(watchers[0].disposed, true)
  runtime.dispose()
})

test('online installation starts only on the explicit action and reports download progress', async () => {
  const { calls, state, runtime, watchers } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.install()
  await tick()
  assert.deepEqual(calls[1], ['loader.install', { installation_id: 'installation' }])
  assert.equal(state.busy, true)
  assert.equal(runtime.canInstall(), false)
  assert.equal(await runtime.install(), false)
  watchers[0].update({ task_id: 'install-1', state: 'running', stage: 'download', completed: 0, total: null,
    data: { bytes_received: 1024000, bytes_total: 2048000 } })
  assert.deepEqual(melonLoaderProgress(state.task), { label: '1.0 MB / 2.0 MB', value: 1024000, max: 2048000 })
  watchers[0].update({ task_id: 'install-1', state: 'succeeded', data: result() })
  assert.equal(await installing, true)
  assert.equal(state.snapshot.version_raw, '0.7.3')
  assert.equal(state.notice, 'MelonLoader 已安装。')
  assert.equal(watchers[0].disposed, true)
  assert.equal(state.busy, false)
  runtime.dispose()
})

test('local installation forwards only the chosen source handle', async () => {
  const { calls, runtime, watchers } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.installLocal()
  await tick()
  assert.deepEqual(calls.slice(1), [['loader.choose', { installation_id: 'installation' }],
    ['loader.install_local', { installation_id: 'installation', source_handle: 'authorized-zip' }]])
  watchers[0].update({ task_id: 'install-1', state: 'succeeded', data: result() })
  assert.equal(await installing, true)
  runtime.dispose()
})

test('cancelling the native ZIP chooser does not start installation or show an error', async () => {
  const { calls, state, runtime } = fixture({ 'loader.choose': () => ({ source_handle: null }) })
  await runtime.setInstallation('installation')
  assert.equal(await runtime.installLocal(), false)
  assert.equal(calls.some(([command]) => command === 'loader.install_local'), false)
  assert.equal(state.error, '')
  assert.equal(state.busy, false)
  runtime.dispose()
})

test('explicit cancellation stays cancelled and releases progress polling', async () => {
  const { state, runtime, watchers } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.install()
  await tick()
  assert.equal(await runtime.cancel(), true)
  assert.equal(await installing, false)
  assert.equal(watchers[0].cancelled, true)
  assert.equal(watchers[0].disposed, true)
  assert.match(state.error, /已取消/)
  assert.equal(state.notice, '')
  runtime.dispose()
})

test('installation changes detach work without cancelling it or adopting late results', async () => {
  const { state, runtime, watchers } = fixture()
  await runtime.setInstallation('first')
  const installing = runtime.install()
  await tick()
  const old = watchers[0]
  await runtime.setInstallation('second')
  assert.equal(await installing, false)
  assert.equal(old.disposed, true)
  assert.equal(old.cancelled, false)
  old.update({ task_id: 'install-1', state: 'succeeded', data: result() })
  assert.equal(state.installationId, 'second')
  assert.equal(state.snapshot.status, 'not_installed')
  assert.equal(state.notice, '')
  runtime.dispose()
})

test('a late native chooser result cannot install into the next selected game', async () => {
  let finishChooser
  const { calls, runtime } = fixture({ 'loader.choose': () => new Promise(resolve => { finishChooser = resolve }) })
  await runtime.setInstallation('first')
  const choosing = runtime.installLocal()
  await tick()
  await runtime.setInstallation('second')
  finishChooser({ source_handle: 'old-source' })
  assert.equal(await choosing, false)
  assert.equal(calls.some(([command]) => command === 'loader.install_local'), false)
  runtime.dispose()
})

test('download failure exposes the useful backend error and leaves installation unconfirmed', async () => {
  const { state, runtime, watchers } = fixture()
  await runtime.setInstallation('installation')
  const installing = runtime.install()
  await tick()
  watchers[0].update({ task_id: 'install-1', state: 'succeeded', data: { error_code: 'LOADER_DOWNLOAD_FAILED', message: '下载失败，可改用本地 ZIP。' } })
  assert.equal(await installing, false)
  assert.equal(state.error, '下载失败，可改用本地 ZIP。')
  assert.equal(state.notice, '')
  assert.equal(state.snapshot.status, 'not_installed')
  runtime.dispose()
})

test('unconfirmed writes and malformed statuses are not presented as installation success', async () => {
  for (const response of [{ installed: true, files_written: 0, status: installed() },
    { installed: true, files_written: 18, status: { ...installed(), checks: [{ id: 'bad' }] } }]) {
    const { state, runtime } = fixture({ 'loader.install': () => response })
    await runtime.setInstallation('installation')
    assert.equal(await runtime.install(), false)
    assert.equal(state.notice, '')
    assert.notEqual(state.error, '')
    runtime.dispose()
  }
})

test('missing runtime and disabled loader remain distinct visible environmental facts', async () => {
  const { state, runtime } = fixture({ 'loader.status': () => status({ ...installed(), ready: false,
    checks: [{ id: 'dotnet', label: '.NET Desktop Runtime', status: 'missing', detail: '未检测到 .NET Desktop Runtime。', help_url: 'https://dotnet.microsoft.com/download/dotnet/6.0' },
      { id: 'enabled', label: 'Mod 加载', status: 'disabled', detail: '配置关闭了 Mod 加载。' }] }) })
  await runtime.setInstallation('installation')
  assert.equal(melonLoaderStatusText(state), 'MelonLoader 0.7.3 · 环境待处理')
  assert.equal(melonLoaderCheckText('missing'), '缺少')
  assert.equal(melonLoaderCheckText('disabled'), '已关闭')
  assert.equal(state.snapshot.checks[0].help_url, 'https://dotnet.microsoft.com/download/dotnet/6.0')
  runtime.dispose()
})

test('failed status checks can be retried and blocked installations cannot begin a write', async () => {
  let fail = true
  const { state, runtime, calls } = fixture({ 'loader.status': () => {
    if (fail) throw new Error('目录无法读取。')
    return status({ status: 'unreadable', can_install: false })
  } })
  assert.equal(await runtime.setInstallation('installation'), false)
  assert.equal(state.error, '目录无法读取。')
  assert.equal(runtime.canInstall(), false)
  fail = false
  assert.equal(await runtime.refresh(), true)
  assert.equal(state.error, '')
  assert.equal(await runtime.install(), false)
  assert.equal(calls.length, 2)
  runtime.dispose()
})

test('synchronous task completion during watcher setup releases the handle', async () => {
  const { state, runtime, bridge } = fixture()
  const handle = { disposed: false, async dispose() { this.disposed = true } }
  bridge.watchTask = (id, update) => { update({ task_id: id, state: 'succeeded', data: result() }); return handle }
  await runtime.setInstallation('installation')
  assert.equal(await runtime.install(), true)
  assert.equal(handle.disposed, true)
  assert.equal(state.notice, 'MelonLoader 已安装。')
  runtime.dispose()
})

test('dispose releases a running status subscription and ignores its late completion', async () => {
  const { state, runtime, watchers } = fixture({ 'loader.status': () => ({ task_id: 'status', state: 'running' }) })
  const detecting = runtime.setInstallation('installation')
  await tick()
  runtime.dispose()
  assert.equal(await detecting, false)
  watchers[0].update({ task_id: 'status', state: 'succeeded', data: installed() })
  assert.equal(watchers[0].disposed, true)
  assert.equal(state.snapshot, null)
})

test('download progress reads bytes from task data instead of the file counter', () => {
  assert.deepEqual(melonLoaderProgress({ stage: 'download', completed: 0, total: null,
    data: { bytes_received: 32500000, bytes_total: 65000000 } }), { label: '32.5 MB / 65.0 MB', value: 32500000, max: 65000000 })
})

test('download without a content length shows received MB and an indeterminate bar', () => {
  assert.deepEqual(melonLoaderProgress({ stage: 'downloading', completed: 0, total: null,
    data: { bytes_received: 4500000, bytes_total: null } }), { label: '4.5 MB', value: undefined, max: 1 })
})

test('installation progress uses actual file counts even if stale byte data is present', () => {
  assert.deepEqual(melonLoaderProgress({ stage: 'installing', completed: 82, total: 164,
    data: { bytes_received: 65000000, bytes_total: 65000000 } }), { label: '82 / 164 个文件', value: 82, max: 164 })
})

test('queued and status-only tasks do not invent progress counts', () => {
  assert.deepEqual(melonLoaderProgress(null), { label: '', value: undefined, max: 1 })
  assert.deepEqual(melonLoaderProgress({ stage: 'download', completed: 0, total: null, data: null }), { label: '', value: undefined, max: 1 })
})

test('visible loader version omits build metadata while keeping the API version intact', async () => {
  const raw = '0.7.3+982ed9981ac5b0619648a2e79bcc02fd8861db33'
  const { state, runtime } = fixture({ 'loader.status': () => ({ ...installed(), version_raw: raw }) })
  await runtime.setInstallation('installation')
  assert.equal(melonLoaderStatusText(state), 'MelonLoader 0.7.3 · 环境已就绪')
  assert.equal(state.snapshot.version_raw, raw)
  runtime.dispose()
})

test('successful installation keeps its useful cleanup warning visible', async () => {
  const warning = '旧文件正被占用，临时备份暂未清理。'
  const { state, runtime } = fixture({ 'loader.install': () => ({ ...result(), warning }) })
  await runtime.setInstallation('installation')
  assert.equal(await runtime.install(), true)
  assert.equal(state.snapshot.status, 'installed')
  assert.equal(state.notice, `MelonLoader 已安装。 ${warning}`)
  assert.equal(state.error, '')
  runtime.dispose()
})

test('pending generation stays visible despite ready dependencies and does not enable repair', async () => {
  const message = '游戏更新后待重新生成程序集；下次启动由 MelonLoader 重建，可能需要较长时间。'
  const pending = { ...installed(), assembly_generation: { status: 'pending', message },
    generation_dependencies: { ready: true, can_repair: false },
    checks: [{ id: 'assembly_generation', label: '程序集生成状态', status: 'pending', detail: message }] }
  const { state, runtime } = fixture({ 'loader.status': () => pending })
  assert.equal(await runtime.setInstallation('installation'), true)
  assert.equal(state.snapshot.ready, true)
  assert.match(melonLoaderStatusText(state), /待重新生成程序集/)
  assert.equal(melonLoaderGenerationNotice(state.snapshot), message)
  assert.equal(melonLoaderCheckText('pending'), '待重新生成')
  assert.equal(runtime.canRepair(), false)
  runtime.dispose()
})

test('first generation, matched and unknown states remain distinct', () => {
  for (const [status, label, summary] of [['not_generated', '尚未生成', '首次启动待生成程序集'],
    ['matched', '已匹配', '环境已就绪'], ['unknown', '待确认', '生成状态待确认']]) {
    const snapshot = { ...installed(), assembly_generation: { status, message: '生成状态说明' } }
    assert.match(melonLoaderStatusText({ snapshot }), new RegExp(summary))
    assert.equal(melonLoaderCheckText(status), label)
    assert.equal(melonLoaderGenerationNotice(snapshot), status === 'matched' ? '' : '生成状态说明')
  }
})
