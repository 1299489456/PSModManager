import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { createAdoptionStore } from './adoptionStore.js'

// Compile the real SFC and mount it with Vue's renderer. This checks actual
// component controls and reactive bindings without opening a desktop window.
const source = readFileSync(new URL('./AdoptionPanel.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'adoption-panel-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } }).content
const executable = compiled
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"]vue['"]/g, (_, bindings) =>
    'const {' + bindings.split(',').map(binding => binding.trim().replace(/\s+as\s+/, ': ')).join(',') + '} = Vue')
  .replace(/import\s+\{\s*createAdoptionStore\s*\}\s+from\s+['"]\.\/adoptionStore\.js['"]/, '')
  .replace('export default', 'return')
const AdoptionPanel = new Function('Vue', 'createAdoptionStore', executable)(Vue, createAdoptionStore)
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}

function element(tag, text = '') {
  return { tag, tagName: tag.toUpperCase(), text, props: {}, children: [], parent: null, listeners: {},
    addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback) },
    getRootNode() { return {} }, showModal() { this.open = true }, close() { this.open = false }, focus() {},
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
const file = (path, lifecycle = 'immutable') => ({ relative_path: path, lifecycle, sha256: 'b'.repeat(64), size: 20,
  owners: { matching: [], conflicting: [] } })

async function fixture(files = [file('Mods/测试.dll'), file('Mods/图片.png'), file('Mods/settings.cfg', 'shared_config')]) {
  const calls = [], root = element('root')
  const preview = { snapshot_id: 'snapshot', manifest_hash: 'a'.repeat(64), coverage: 'complete',
    files, total: files.length, next_offset: null }
  const bridge = {
    async request(command, payload) {
      calls.push({ command, payload })
      const data = command === 'inventory.scan' ? { status: 'complete' }
        : command === 'package.adopt' ? { release: { release_id: 'release' }, snapshot_id: 'snapshot', deployment_generation: null }
          : preview
      return { task_id: command, state: 'succeeded', data }
    },
  }
  const app = renderer.createApp(AdoptionPanel, { bridge, installation: { installation_id: 'installation' } })
  app.mount(root)
  await tick()
  const find = id => all(root).find(node => node.props['data-testid'] === id)
  const findAll = id => all(root).filter(node => node.props['data-testid'] === id)
  async function click(id) {
    const node = find(id)
    assert.ok(node, id)
    assert.notEqual(node.props.disabled, true, id + ' must be enabled')
    await node.props.onClick()
    await tick()
  }
  async function name(value) {
    const node = find('adoption-name')
    node.value = value
    for (const callback of node.listeners.input || []) callback({ target: node })
    await tick()
  }
  await click('adoption-scan-button')
  return { app, root, find, findAll, click, name, calls }
}

test('adoption panel exposes explicit selection, required name, count and disabled reason', async () => {
  const f = await fixture()
  try {
    assert.match(textOf(f.find('adoption-selected-count')), /已选 0 项/)
    assert.ok(Object.hasOwn(f.find('adoption-name').props, 'required'))
    assert.equal(f.find('adoption-name').props['aria-required'], 'true')
    assert.match(textOf(f.find('adoption-confirmation-reason')), /勾选.*填写包名称/)
    assert.equal(f.find('adoption-commit').props.disabled, true)
    const choices = f.findAll('adoption-file-checkbox')
    assert.equal(choices.length, 3)
    assert.equal(choices[0].parent.tag, 'label')
    assert.match(textOf(choices[0].parent), /Mods\/测试.dll.*选择/)
    assert.equal(choices[2].props.disabled, true)
    await choices[0].props.onChange({ target: { checked: true } }); await tick()
    assert.match(textOf(f.find('adoption-selected-count')), /已选 1 项/)
    assert.match(textOf(f.find('adoption-confirmation-reason')), /已选择文件，请填写包名称/)
    await f.name('我的 Mod')
    assert.equal(f.find('adoption-commit').props.disabled, false)
    assert.match(textOf(f.find('adoption-confirmation-reason')), /可以确认入库/)
    assert.equal(f.calls.some(call => call.command === 'package.adopt'), false)
  } finally { f.app.unmount() }
})

test('adoption full-page selection only includes allowed files and still requires explicit confirmation', async () => {
  const f = await fixture()
  try {
    await f.click('adoption-select-page')
    assert.match(textOf(f.find('adoption-selected-count')), /已选 2 项/)
    assert.equal(f.find('adoption-select-page').props.disabled, true)
    assert.equal(f.find('adoption-commit').props.disabled, true)
    assert.equal(f.calls.some(call => call.command === 'package.adopt'), false)
    await f.name('   ')
    assert.equal(f.find('adoption-commit').props.disabled, true)
    await f.name('合成包')
    await f.click('adoption-commit')
    const commit = f.calls.find(call => call.command === 'package.adopt')
    assert.deepEqual(commit.payload.paths, ['Mods/图片.png', 'Mods/测试.dll'])
    assert.equal(commit.payload.display_name, '合成包')
    assert.equal(f.find('adoption-result').props['data-release-id'], 'release')
  } finally { f.app.unmount() }
})

test('clearing selection updates the count and explains why confirmation is disabled', async () => {
  const f = await fixture()
  try {
    await f.name('本地包')
    await f.click('adoption-select-page')
    await f.click('adoption-clear-selection')
    assert.match(textOf(f.find('adoption-selected-count')), /已选 0 项/)
    assert.equal(f.find('adoption-commit').props.disabled, true)
    assert.match(textOf(f.find('adoption-confirmation-reason')), /全选本页/)
    assert.equal(f.find('adoption-clear-selection').props.disabled, true)
  } finally { f.app.unmount() }
})

test('a page without adoptable files has no misleading select-all action', async () => {
  const f = await fixture([file('Mods/settings.cfg', 'shared_config')])
  try {
    assert.equal(f.find('adoption-select-page').props.disabled, true)
    assert.equal(f.find('adoption-file-checkbox').props.disabled, true)
    assert.match(textOf(f.find('adoption-file-checkbox').parent), /不支持接管.*不可选/)
    assert.match(textOf(f.find('adoption-confirmation-reason')), /这些文件均不支持接管/)
  } finally { f.app.unmount() }
})
