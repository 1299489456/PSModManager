import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Icons from 'lucide-vue-next'
import * as Runtime from './modWorkspaceRuntime.js'

const { descriptor } = parse(readFileSync(new URL('./ModWorkspace.vue', import.meta.url), 'utf8'))
const compiled = compileScript(descriptor, { id: 'mod-workspace-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } }).content
const executable = compiled
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"](vue|lucide-vue-next|\.\/modWorkspaceRuntime\.js)['"]/g, (_, bindings, source) =>
    'const {' + bindings.split(',').map(binding => binding.trim().replace(/\s+as\s+/, ': ')).join(',') + '} = ' + ({ vue: 'Vue', 'lucide-vue-next': 'Icons', './modWorkspaceRuntime.js': 'Runtime' })[source])
  .replace(/import ModFileTree from ['"]\.\/ModFileTree\.vue['"]/, 'const ModFileTree = { render: () => null }')
  .replace(/import SelectMenu from ['"][^'"]+SelectMenu\.vue['"]/, 'const SelectMenu = { render: () => null }')
  .replace(/import\s+\{[^}]+\}\s+from\s+['"]\.\/gardenMotion\.js['"]/, 'const flip = (_, change) => change(), flyLeaf = () => {}')
  .replace('export default', 'return')
const ModWorkspace = new Function('Vue', 'Icons', 'Runtime', executable)(Vue, Icons, Runtime)
const keys = new Set()
globalThis.window = { addEventListener(name, handler) { if (name === 'keydown') keys.add(handler) }, removeEventListener(name, handler) { keys.delete(handler) } }
globalThis.document = { activeElement: null, querySelector: () => null }
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}
const all = node => [node, ...node.children.flatMap(all)]
const hasClass = (node, name) => (node.props.class || '').split(' ').includes(name)
function element(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    get options() { return this.children.filter(node => node.tag === 'option') },
    getRootNode() { return {} },
    addEventListener(name, handler) { this.listeners[name] = handler },
    querySelector(selector) { return all(this).find(node => selector.startsWith('.') && hasClass(node, selector.slice(1))) },
    closest(selector) { return selector.split(',').includes(this.tag) ? this : this.parent?.closest(selector) },
    getBoundingClientRect: () => ({ top: 100 }), focus() {},
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
const tick = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
async function fixture() {
  const calls = [], root = element('root')
  const items = ['Alpha', 'Beta'].map((name, index) => ({ id: String(index), display_name: name, state: index ? 'disabled' : 'enabled', file_count: 1 }))
  let enabled = ['0']
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (command === 'mod.workspace') return { items, enabled_ids: enabled, snapshot_id: 'snapshot' }
    if (command === 'mod.workspace.detail') return { files: [], total: 0, next_offset: null }
    if (command === 'mod.workspace.save') { enabled = payload.enabled_ids; return { completed: true, workspace: { items, enabled_ids: enabled, snapshot_id: 'saved' } } }
    throw new Error(command)
  } }
  const app = renderer.createApp(ModWorkspace, { bridge, installation: { installation_id: 'game' } })
  app.mount(root); await tick()
  const find = id => all(root).find(node => node.props['data-testid'] === id)
  async function click(node, clientY = 20) {
    assert.ok(node)
    assert.notEqual(node.props.disabled, true)
    let stopped = false
    const event = { target: node, clientY, stopPropagation() { stopped = true } }
    for (let parent = node; parent && !stopped; parent = parent.parent) {
      event.currentTarget = parent; parent.props.onClick?.(event)
    }
    await tick()
  }
  return { app, root, find, click, calls }
}

test('expanded cards close from upper blank space and Escape, not from content or switches', async () => {
  const f = await fixture()
  try {
    let card = f.find('workspace-mod-row')
    await f.click(card); assert.ok(f.find('mod-detail'))
    await f.click(card.querySelector('.plant-name')); assert.ok(f.find('mod-detail'))
    await f.click(f.find('mod-detail')); assert.ok(f.find('mod-detail'))
    await f.click(card.querySelector('.leaf-switch')); assert.ok(f.find('mod-detail'))
    await f.click(card.querySelector('.plant-top')); assert.equal(f.find('mod-detail'), undefined)
    await f.click(card); assert.ok(f.find('mod-detail'))
    await f.click(card, 120); assert.ok(f.find('mod-detail'), 'bottom padding is not a close target')
    await f.click(card, 20); assert.equal(f.find('mod-detail'), undefined)
    await f.click(card)
    for (const handler of keys) handler({ key: 'Escape', preventDefault() {} })
    await tick(); assert.equal(f.find('mod-detail'), undefined)
  } finally { f.app.unmount() }
})

test('bulk buttons stay bound to the full list while search hides rows and require save', async () => {
  const f = await fixture()
  try {
    const search = f.find('mod-search')
    search.value = 'Alpha'; search.listeners.input({ target: search }); await tick()
    assert.equal(all(f.root).filter(node => node.props['data-testid'] === 'workspace-mod-row').length, 1)
    await f.click(f.find('workspace-enable-all'))
    assert.equal(f.find('workspace-enable-all').props.disabled, true)
    assert.equal(f.calls.some(call => call.command === 'mod.workspace.save'), false)
    await f.click(f.find('workspace-save'))
    assert.deepEqual(f.calls.find(call => call.command === 'mod.workspace.save').payload.enabled_ids, ['0', '1'])
    await f.click(f.find('workspace-disable-all'))
    assert.equal(f.find('workspace-disable-all').props.disabled, true)
    await f.click(f.find('workspace-save'))
    assert.deepEqual(f.calls.filter(call => call.command === 'mod.workspace.save').at(-1).payload.enabled_ids, [])
  } finally { f.app.unmount() }
})
