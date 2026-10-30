import test from 'node:test'
import assert from 'node:assert/strict'
import { createGameLaunchRuntime, gameLaunchStatusText } from './gameLaunchRuntime.js'

function snapshot(state = 'not_started', extra = {}) {
  return { installation_id: 'one', state, pid: state === 'running' ? 123 : null, exit_code: null, started_at: state === 'running' ? '2026-10-12T08:00:00Z' : null, message: '', ...extra }
}
function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function setup(respond = command => snapshot(command === 'game.launch' ? 'running' : 'not_started')) {
  const state = {}, calls = [], timers = new Map(), cancelled = []
  let nextTimer = 0
  const runtime = createGameLaunchRuntime({
    async request(command, payload) { calls.push([command, payload]); return respond(command, payload) },
  }, state, {
    schedule(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id },
    cancel(id) { cancelled.push(id); timers.delete(id) },
  })
  return { runtime, state, calls, timers, cancelled, async tick() {
    const entry = timers.entries().next().value
    assert.ok(entry, 'Expected a pending polling timer')
    timers.delete(entry[0])
    await entry[1].callback()
  } }
}

test('launch labels distinguish process states without claiming gameplay verification', () => {
  assert.equal(gameLaunchStatusText({ snapshot: null }), '未启动')
  assert.equal(gameLaunchStatusText({ busy: true }), '正在启动')
  for (const [result, text] of [
    [snapshot(), '未启动'], [snapshot('running'), '已启动'],
    [snapshot('running', { already_running: true }), '已在运行'],
    [snapshot('exited'), '已退出'], [snapshot('exited', { exit_code: 7 }), '已退出（退出码 7）'],
    [snapshot('failed'), '未启动'], [snapshot('unknown'), '暂无法确认'],
  ]) {
    assert.equal(gameLaunchStatusText({ snapshot: result }), text)
    assert.doesNotMatch(text, /运行正常|玩法/)
  }
})

test('no installation cannot launch or refresh; selection reads actual status by ID only', async () => {
  const app = setup()
  assert.equal(app.runtime.canLaunch(), false)
  assert.equal(await app.runtime.launch(), false)
  assert.equal(await app.runtime.refresh(), false)
  assert.deepEqual(app.calls, [])
  assert.equal(await app.runtime.setInstallation('one'), true)
  assert.deepEqual(app.calls, [['game.status', { installation_id: 'one' }]])
  assert.equal(app.runtime.canLaunch(), true)
  assert.equal(await app.runtime.setInstallation('one'), false)
  assert.equal(app.calls.length, 1)
})

test('duplicate clicks and refresh while launching do not send more requests', async () => {
  const pending = deferred(), app = setup(command => command === 'game.launch' ? pending.promise : snapshot())
  await app.runtime.setInstallation('one')
  const launch = app.runtime.launch()
  assert.equal(app.state.busy, true)
  assert.equal(gameLaunchStatusText(app.state), '正在启动')
  assert.equal(await app.runtime.launch(), false)
  assert.equal(await app.runtime.refresh(), false)
  assert.equal(app.calls.length, 2)
  pending.resolve(snapshot('running'))
  assert.equal(await launch, true)
  assert.equal(app.state.busy, false)
  assert.equal(app.runtime.canLaunch(), false)
  assert.equal(await app.runtime.launch(), false)
  assert.equal(app.calls.length, 2)
})

test('a running process keeps polling after exit so external launches can be observed', async () => {
  let statusCalls = 0
  const app = setup(command => command === 'game.launch' ? snapshot('running') : ++statusCalls === 1 ? snapshot() : statusCalls === 2 ? snapshot('running') : snapshot('exited', { exit_code: 0 }))
  await app.runtime.setInstallation('one')
  await app.runtime.launch()
  assert.equal(app.timers.size, 1)
  assert.equal([...app.timers.values()][0].delay, 2000)
  await app.tick()
  assert.equal(app.state.snapshot.state, 'running')
  assert.equal(app.timers.size, 1)
  await app.tick()
  assert.equal(gameLaunchStatusText(app.state), '已退出（退出码 0）')
  assert.equal(app.timers.size, 1)
  assert.equal([...app.timers.values()][0].delay, 5000)
  assert.equal(app.runtime.canLaunch(), true)
})

test('initial already-running result disables launch and starts polling', async () => {
  const app = setup(() => snapshot('running', { already_running: true }))
  await app.runtime.setInstallation('one')
  assert.equal(gameLaunchStatusText(app.state), '已在运行')
  assert.equal(app.runtime.canLaunch(), false)
  assert.equal(app.timers.size, 1)
})

test('switching installations ignores a late launch reply and resets the selected status', async () => {
  const pending = deferred()
  const app = setup((command, payload) => command === 'game.launch' ? pending.promise : snapshot('not_started', { installation_id: payload.installation_id }))
  await app.runtime.setInstallation('one')
  const launch = app.runtime.launch()
  await app.runtime.setInstallation('two')
  pending.resolve(snapshot('running'))
  assert.equal(await launch, false)
  assert.equal(app.state.installationId, 'two')
  assert.equal(app.state.snapshot.installation_id, 'two')
  assert.equal(app.state.busy, false)
  assert.equal(app.timers.size, 1)
  assert.equal(app.runtime.canLaunch(), true)
})

test('late status reply cannot replace status for another installation', async () => {
  const pending = deferred()
  const app = setup((command, payload) => payload.installation_id === 'one' ? pending.promise : snapshot('exited', { installation_id: 'two', exit_code: 9 }))
  const oldRead = app.runtime.setInstallation('one')
  assert.equal(app.runtime.canLaunch(), false)
  assert.equal(await app.runtime.refresh(), false)
  await app.runtime.setInstallation('two')
  pending.resolve(snapshot('running'))
  await oldRead
  assert.equal(gameLaunchStatusText(app.state), '已退出（退出码 9）')
  assert.equal(app.state.snapshot.installation_id, 'two')
  assert.equal(app.state.refreshing, false)
  assert.equal(app.timers.size, 1)
})

test('clearing installation removes running status and cancels its timer', async () => {
  const app = setup(() => snapshot('running'))
  await app.runtime.setInstallation('one')
  await app.runtime.setInstallation(null)
  assert.equal(app.state.installationId, null)
  assert.equal(gameLaunchStatusText(app.state), '未启动')
  assert.equal(app.runtime.canLaunch(), false)
  assert.equal(app.timers.size, 0)
  assert.equal(app.cancelled.length, 1)
})

test('launch errors keep backend message and permit explicit retry', async () => {
  let launches = 0
  const app = setup(command => {
    if (command === 'game.status') return snapshot()
    if (++launches === 1) throw Object.assign(new Error('游戏程序无法执行，请检查权限。'), { code: 'LAUNCH_FAILED' })
    return snapshot('running')
  })
  await app.runtime.setInstallation('one')
  assert.equal(await app.runtime.launch(), false)
  assert.equal(gameLaunchStatusText(app.state), '启动失败')
  assert.equal(app.state.error, '游戏程序无法执行，请检查权限。')
  assert.equal(app.timers.size, 1)
  assert.equal(app.runtime.canLaunch(), true)
  assert.equal(await app.runtime.launch(), true)
  assert.equal(app.state.error, '')
  assert.equal(gameLaunchStatusText(app.state), '已启动')
})

test('manual refresh can clear a failed launch without automatically relaunching', async () => {
  const app = setup(command => { if (command === 'game.launch') throw new Error('private stack'); return snapshot() })
  await app.runtime.setInstallation('one')
  await app.runtime.launch()
  assert.match(app.state.error, /未能启动/)
  assert.doesNotMatch(app.state.error, /private/)
  await app.runtime.refresh()
  assert.equal(gameLaunchStatusText(app.state), '未启动')
  assert.equal(app.state.error, '')
  assert.deepEqual(app.calls.map(([command]) => command), ['game.status', 'game.launch', 'game.status'])
})

test('security rejection retains its actionable code without claiming the game ran', async () => {
  const app=setup(command=>{if(command==='game.launch')throw Object.assign(new Error('请查看安全证据并停用或隔离。'),{code:'SECURITY_QUARANTINED'});return snapshot()})
  await app.runtime.setInstallation('one');assert.equal(await app.runtime.launch(),false)
  assert.equal(app.state.errorCode,'SECURITY_QUARANTINED');assert.equal(app.state.snapshot.state,'failed');assert.equal(app.state.snapshot.pid,null);assert.equal(app.timers.size,1)
  await app.runtime.refresh();assert.equal(app.state.errorCode,'');assert.equal(app.calls.filter(([c])=>c==='game.launch').length,1)
})

test('poll error shows unknown status and keeps refresh available without claiming a stopped game', async () => {
  let statusCalls = 0
  const app = setup(() => { if (++statusCalls === 1) return snapshot('running'); throw Object.assign(new Error('进程查询暂不可用。'), { code: 'STATUS_UNAVAILABLE' }) })
  await app.runtime.setInstallation('one')
  await app.tick()
  assert.equal(gameLaunchStatusText(app.state), '暂无法确认')
  assert.equal(app.state.error, '进程查询暂不可用。')
  assert.equal(app.state.refreshing, false)
  assert.equal(app.timers.size, 1)
  assert.equal(app.runtime.canLaunch(),false)
})

test('a mismatched or malformed response is not adopted as a running process', async () => {
  for (const result of [null, snapshot('running', { installation_id: 'other' }), snapshot('verified'), snapshot('running', { pid: '123' }), snapshot('running', { started_at: 'yesterday' }), snapshot('running', { exit_code: '0' })]) {
    const app = setup(() => result)
    assert.equal(await app.runtime.setInstallation('one'), false)
    assert.equal(gameLaunchStatusText(app.state), '暂无法确认')
    assert.equal(app.timers.size, 1)
    assert.match(app.state.error, /暂无法确认/)
  }
})

test('dispose cancels polling and stale scheduled callbacks cannot send a request', async () => {
  const app = setup(() => snapshot('running'))
  await app.runtime.setInstallation('one')
  const callback = [...app.timers.values()][0].callback
  app.runtime.dispose()
  assert.equal(app.timers.size, 0)
  assert.equal(app.cancelled.length, 1)
  await callback()
  assert.equal(await app.runtime.refresh(), false)
  assert.equal(await app.runtime.launch(), false)
  assert.equal(app.runtime.setInstallation('two'), false)
  assert.equal(app.calls.length, 1)
})

test('dispose prevents late query errors and replies from mutating state', async () => {
  for (const fail of [false, true]) {
    const pending = deferred(), app = setup(() => pending.promise)
    const read = app.runtime.setInstallation('one')
    app.runtime.dispose()
    const frozen = structuredClone(app.state)
    if (fail) pending.reject(Object.assign(new Error('late'), { code: 'LATE' }))
    else pending.resolve(snapshot('running'))
    assert.equal(await read, false)
    assert.deepEqual(app.state, frozen)
    assert.equal(app.timers.size, 0)
  }
})

test('an old timer retained by the host cannot query the new installation', async () => {
  const app = setup((command, payload) => snapshot('running', { installation_id: payload.installation_id }))
  await app.runtime.setInstallation('one')
  const oldPoll = [...app.timers.values()][0].callback
  await app.runtime.setInstallation('two')
  const count = app.calls.length
  await oldPoll()
  assert.equal(app.calls.length, count)
  assert.equal(app.timers.size, 1)
  app.runtime.dispose()
  assert.equal(app.timers.size, 0)
  assert.equal(app.cancelled.length, 2)
})

test('one launch awaits preparation before process creation and owns the entire busy interval',async()=>{
  const app=setup(),save=deferred();await app.runtime.setInstallation('one');let preparations=0
  const pending=app.runtime.launch(async()=>{preparations++;await save.promise;return true})
  assert.equal(app.state.phase,'preparing');assert.equal(gameLaunchStatusText(app.state),'正在保存模组');assert.equal(app.timers.size,0)
  assert.equal(await app.runtime.launch(),false);assert.equal(await app.runtime.refresh(),false)
  assert.equal(app.calls.filter(([c])=>c==='game.launch').length,0)
  save.resolve();assert.equal(await pending,true);assert.equal(preparations,1)
  assert.equal(app.calls.filter(([c])=>c==='game.launch').length,1);assert.equal(app.state.busy,false);assert.equal(app.state.phase,null)
})
test('preparation failure keeps the concrete security error and never attempts game launch',async()=>{
  const app=setup();await app.runtime.setInstallation('one')
  assert.equal(await app.runtime.launch(async()=>{throw Object.assign(new Error('该模组已由用户主动隔离。'),{code:'SECURITY_QUARANTINED'})}),false)
  assert.equal(app.state.errorCode,'SECURITY_QUARANTINED');assert.equal(app.state.error,'该模组已由用户主动隔离。')
  assert.equal(app.calls.filter(([c])=>c==='game.launch').length,0)
  app.runtime.clearError();assert.equal(gameLaunchStatusText(app.state),'未启动');assert.equal(app.state.errorCode,'')
})
test('switching installation during preparation cannot launch the newly selected game',async()=>{
  const save=deferred(),app=setup((_,payload)=>snapshot('not_started',{installation_id:payload.installation_id}))
  await app.runtime.setInstallation('one');const pending=app.runtime.launch(async()=>{await save.promise;return true})
  await app.runtime.setInstallation('two');save.resolve();assert.equal(await pending,false)
  assert.equal(app.calls.filter(([c])=>c==='game.launch').length,0);assert.equal(app.state.installationId,'two')
})
test('idle polling detects external Steam launch after a tracked game exited',async()=>{
  let running=false;const app=setup(()=>snapshot(running?'running':'exited',running?{already_running:true}:{}))
  await app.runtime.setInstallation('one');assert.equal(app.timers.size,1);running=true;await app.tick()
  assert.equal(gameLaunchStatusText(app.state),'已在运行');assert.equal(app.runtime.canLaunch(),false)
})
test('background polling does not erase an actionable launch failure before explicit retry or save',async()=>{
  const app=setup(c=>{if(c==='game.launch')throw Object.assign(new Error('安装正在处理其他操作。'),{code:'GUARD_LEASE_BUSY'});return snapshot()})
  await app.runtime.setInstallation('one');await app.runtime.launch();await app.tick()
  assert.equal(app.state.errorCode,'GUARD_LEASE_BUSY');assert.ok(app.state.error)
  app.runtime.clearError();assert.equal(app.state.error,'')
})

test('a background status poll keeps the launch button usable', async () => {
  const gate = deferred()
  let polls = 0
  const app = setup(command => command === 'game.launch' ? snapshot('running') : ++polls > 1 ? gate.promise : snapshot())
  await app.runtime.setInstallation('one')
  assert.equal(app.runtime.canLaunch(), true)
  const poll = app.tick()
  assert.equal(app.state.refreshing, true)
  assert.equal(app.runtime.canLaunch(), true, 'last known stopped state stays launchable while polling')
  assert.equal(await app.runtime.launch(), true)
  gate.resolve(snapshot('exited'))
  await poll
  assert.equal(app.state.snapshot.state, 'running', 'a late poll reply cannot overwrite the launch result')
})
