import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Icons from 'lucide-vue-next'

const { descriptor } = parse(readFileSync(new URL('./StorageSettings.vue', import.meta.url), 'utf8'))
const compiled = compileScript(descriptor, { id: 'storage-settings-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } }).content
const executable = compiled
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"](vue|lucide-vue-next)['"]/g, (_, bindings, source) =>
    'const {' + bindings.split(',').map(binding => binding.trim().replace(/\s+as\s+/, ': ')).join(',') + '} = ' + (source === 'vue' ? 'Vue' : 'Icons'))
  .replace(/import (\w+) from ['"][^'"]+\.png\?inline['"]/g, 'const $1 = "data:image/png;base64,"')
  .replace('export default', 'return')
const StorageSettings = new Function('Vue', 'Icons', executable)(Vue, Icons)
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}

function element(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    getRootNode() { return {} }, focus() { this.focused = true },
    addEventListener(name, handler) { this.listeners[name] = handler },
  }
}
const renderer = Vue.createRenderer({
  createElement: element, createText: text => element('#text', text), createComment: () => element('#comment'),
  setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text; node.children = [] },
  patchProp(node, key, previous, value) { node.props[key] = value; if (key === 'value') node.value = value },
  insert(node, parent, anchor = null) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    node.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
  },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1) },
  parentNode: node => node.parent,
  nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  setScopeId() {},
})
const all = node => [node, ...node.children.flatMap(all)]
const textOf = node => node.text + node.children.map(textOf).join('')
const tick = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
const initial = () => ({ root: 'C:\\Users\\Player\\AppData\\Local\\PSModManager', first_run: false, phase: 'idle',
  target: null, completed: 0, total: 0, message: '', previous_root: null,
  locations: [{ label: '模组包库', path: 'C:\\PSMM\\library' }, { label: '导入暂存', path: 'C:\\PSMM\\imports' }] })

async function fixture({ props = {}, value = initial(), handlers = {} } = {}) {
  const calls = [], events = [], root = element('root'), options = Vue.reactive({ ...props })
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (handlers[command]) return handlers[command](payload)
    if (command === 'app.storage.status') return value
    if (command === 'app.storage.accept') return { ...value, first_run: false }
    if (command === 'app.storage.choose') return { target: null }
    if (command === 'app.storage.restart') return {}
    throw new Error(command)
  } }
  const app = renderer.createApp({ render: () => Vue.h(StorageSettings, {
    ...options, bridge, onReady: () => events.push({ name: 'ready' }), onState: status => events.push({ name: 'state', status }),
  }) })
  app.mount(root); await tick()
  const find = id => all(root).find(node => node.props['data-testid'] === id)
  async function click(id) {
    const node = find(id); assert.ok(node, id); assert.notEqual(node.props.disabled, true, id + ' must be enabled')
    await node.props.onClick(); await tick()
  }
  async function input(value) {
    const node = find('storage-target'); assert.ok(node)
    node.value = value; node.listeners.input({ target: node }); await tick()
  }
  async function review(path = 'D:\\PSModManager') {
    await click('storage-change'); await input(path)
    const button = find('storage-review'); assert.notEqual(button.props.disabled, true)
    await button.parent.parent.props.onSubmit({ preventDefault() {} }); await tick()
  }
  return { app, root, find, click, input, review, calls, events, options, setStatus(status) { value = status } }
}

test('first use offers default storage and emits ready only after the choice is saved', async () => {
  const f = await fixture({ props: { firstRun: true }, value: { ...initial(), first_run: true } })
  try {
    assert.ok(f.find('storage-first-run'))
    assert.equal(f.find('storage-locations'), undefined)
    assert.match(textOf(f.find('storage-root')), /AppData/)
    assert.equal(f.events.some(event => event.name === 'ready'), false)
    await f.click('storage-default')
    assert.deepEqual(f.calls.at(-1), { command: 'app.storage.accept', payload: {} })
    assert.equal(f.events.at(-1).name, 'ready')
    assert.equal(f.events.filter(event => event.name === 'state').at(-1).status.first_run, false)
  } finally { f.app.unmount() }
})

test('settings shows actual locations and cancelled folder selection preserves a manual path', async () => {
  const f = await fixture()
  try {
    assert.ok(f.find('storage-settings'))
    assert.match(textOf(f.find('storage-locations')), /导入暂存C:\\PSMM\\imports/)
    await f.click('storage-change')
    assert.equal(f.find('storage-target').focused, true)
    await f.input('E:\\Mods Data')
    await f.click('storage-choose')
    assert.equal(f.find('storage-target').value, 'E:\\Mods Data')
    await f.click('storage-cancel')
    assert.equal(f.find('storage-target'), undefined)
    assert.equal(f.calls.some(call => call.command === 'app.storage.start'), false)
  } finally { f.app.unmount() }
})

test('migration requires an explicit second confirmation and polls until it is ready', async () => {
  const copying = { ...initial(), phase: 'copying', target: 'D:\\PSModManager', completed: 1, total: 4 }
  const f = await fixture({ handlers: { 'app.storage.start': () => copying } })
  try {
    await f.review('  D:\\PSModManager  ')
    assert.ok(f.find('storage-confirm'))
    assert.match(textOf(f.find('storage-confirm')), /历史备份.*WebView.*旧目录/s)
    assert.match(textOf(f.find('storage-confirm')), /保留 \.storage-redirect\.json.*旧快捷方式和下载链接/)
    assert.equal(f.calls.some(call => call.command === 'app.storage.start'), false)
    await f.click('storage-start')
    assert.deepEqual(f.calls.at(-1), { command: 'app.storage.start', payload: { target: 'D:\\PSModManager' } })
    assert.equal(f.events.at(-1).status.phase, 'copying')
    assert.ok(f.find('storage-copying'))
    assert.match(textOf(f.root), /请勿关闭窗口/)
    assert.match(textOf(f.find('storage-progress')), /25%/)
    const warning = '部分 Windows 凭据未能迁移，请在新目录重新填写 AI / Nexus 密钥'
    f.setStatus({ ...copying, phase: 'ready', completed: 4, message: warning })
    await new Promise(resolve => setTimeout(resolve, 660)); await tick()
    assert.ok(f.find('storage-complete'))
    assert.equal(f.events.at(-1).status.phase, 'ready')
    assert.equal(textOf(f.find('storage-result-message')), warning)
    await f.click('storage-restart')
    assert.equal(f.calls.at(-1).command, 'app.storage.restart')
    assert.equal(f.find('storage-restart').props.disabled, true)
  } finally { f.app.unmount() }
})

test('ready status preserves startup redirect warnings and tells users to retain its locator file', async () => {
  const message = '启动位置记录未能更新，请使用新数据目录启动。'
  const f = await fixture({ value: { ...initial(), phase: 'ready', target: 'D:\\PSModManager', message } })
  try {
    assert.equal(textOf(f.find('storage-result-message')), message)
    assert.match(textOf(f.find('storage-complete')), /保留 \.storage-redirect\.json.*旧快捷方式和下载链接/)
    assert.ok(f.find('storage-restart'))
  } finally { f.app.unmount() }
})

test('blocked edits cannot start a migration, including when blocked after reviewing', async () => {
  const f = await fixture({ props: { blocked: true } })
  try {
    await f.click('storage-change'); await f.input('E:\\PSModManager')
    assert.equal(f.find('storage-review').props.disabled, true)
    assert.match(textOf(f.find('storage-blocked')), /保存或撤销.*等待/)
    f.options.blocked = false; await tick()
    await f.find('storage-review').parent.parent.props.onSubmit({ preventDefault() {} }); await tick()
    assert.ok(f.find('storage-confirm'))
    f.options.blocked = true; await tick()
    assert.equal(f.find('storage-start').props.disabled, true)
    await f.find('storage-start').props.onClick(); await tick()
    assert.equal(f.calls.some(call => call.command === 'app.storage.start'), false)
  } finally { f.app.unmount() }
})

test('chooser and start failures retain their Chinese service error without accepting storage', async () => {
  const f = await fixture({ handlers: {
    'app.storage.choose': () => { throw new Error('无法打开目录选择窗口') },
    'app.storage.start': () => { throw new Error('目标目录不是空目录，请重新选择') },
  } })
  try {
    await f.click('storage-change'); await f.click('storage-choose')
    assert.match(textOf(f.find('storage-error')), /无法打开目录选择窗口/)
    await f.input('D:\\PSModManager')
    await f.find('storage-review').parent.parent.props.onSubmit({ preventDefault() {} }); await tick()
    await f.click('storage-start')
    assert.match(textOf(f.find('storage-error')), /目标目录不是空目录/)
    assert.ok(f.find('storage-confirm'))
    assert.equal(f.events.some(event => event.name === 'ready'), false)
  } finally { f.app.unmount() }
})

test('failed migration keeps the original location and offers restart into it', async () => {
  const f = await fixture({ value: { ...initial(), phase: 'failed', message: '磁盘空间不足，原目录保持不变。' } })
  try {
    assert.ok(f.find('storage-failed'))
    assert.match(textOf(f.root), /磁盘空间不足，原目录保持不变/)
    assert.match(textOf(f.find('storage-restart')), /返回原目录/)
    assert.equal(f.find('storage-change'), undefined)
    await f.click('storage-restart')
    assert.equal(f.calls.at(-1).command, 'app.storage.restart')
  } finally { f.app.unmount() }
})

test('initial status failure can be retried and unmount stops copying polling', async () => {
  let fail = true
  const copying = { ...initial(), phase: 'copying' }
  const f = await fixture({ handlers: { 'app.storage.status': () => { if (fail) throw new Error('本地服务暂不可用'); return copying } } })
  try {
    assert.match(textOf(f.find('storage-error')), /本地服务暂不可用/)
    fail = false; await f.click('storage-retry')
    assert.ok(f.find('storage-copying'))
    f.app.unmount()
    const count = f.calls.length
    await new Promise(resolve => setTimeout(resolve, 650))
    assert.equal(f.calls.length, count)
  } finally { if (f.app._container) f.app.unmount() }
})
