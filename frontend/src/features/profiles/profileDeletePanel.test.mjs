import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import * as Icons from 'lucide-vue-next'
import * as Store from './profileStore.js'
import * as Selection from './profileSelection.js'

const { descriptor } = parse(readFileSync(new URL('./ProfilePanel.vue', import.meta.url), 'utf8'))
const compiled = compileScript(descriptor, { id: 'profile-delete-test', inlineTemplate: true,
  templateOptions: { compilerOptions: { hoistStatic: false } } }).content
const modules = { vue: 'Vue', 'lucide-vue-next': 'Icons', './profileStore.js': 'Store', './profileSelection.js': 'Selection' }
const executable = compiled.replace(/import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/g, (_, bindings, source) =>
  'const {' + bindings.split(',').map(binding => binding.trim().replace(/\s+as\s+/, ': ')).join(',') + '} = ' + modules[source])
  .replace(/import (PackageDetails|PlanPreview) from ['"][^'"]+['"]/g, (_, name) => `const ${name} = { render: () => null }`)
  .replace('export default', 'return')
const ProfilePanel = new Function('Vue', 'Icons', 'Store', 'Selection', executable)(Vue, Icons, Store, Selection)
globalThis.document = { activeElement: null, addEventListener() {}, removeEventListener() {} }
globalThis.window = { innerWidth: 1200, innerHeight: 800 }
globalThis.Document ??= class Document {}
globalThis.ShadowRoot ??= class ShadowRoot {}
const all = node => [node, ...node.children.flatMap(all)]
const text = node => [node.text, ...node.children.map(text)].join(' ')
function element(tag, content = '') {
  return { tag, tagName: tag.toUpperCase(), text: content, children: [], props: {}, parent: null, open: false,
    showModal() { this.open = true }, close() { this.open = false }, focus() {},
    addEventListener() {}, removeEventListener() {}, getRootNode() { return {} },
    querySelector(selector) {
      return all(this).find(node => selector === 'button:not(:disabled)' ? node.tag === 'button' && !node.props.disabled
        : selector.startsWith('[data-testid=') ? node.props['data-testid'] === selector.slice(13, -1) : node.tag === selector)
    },
  }
}
const renderer = Vue.createRenderer({
  createElement: element, createText: value => element('#text', value), createComment: () => element('#comment'),
  setText(node, value) { node.text = value }, setElementText(node, value) { node.text = value; node.children = [] },
  patchProp(node, key, old, value) { node.props[key] = value; if (key === 'value') node.value = value },
  insert(node, parent, anchor = null) {
    if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
    node.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
  },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1) },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  setScopeId() {},
})
const tick = async () => { await new Promise(resolve => setImmediate(resolve)); await Vue.nextTick() }
async function fixture({ failure = false } = {}) {
  const root = element('root'), calls = []
  const first = { profile_id: 'first', name: '待删除方案', revision: 4, dirty: true, selections: [], groups: [] }
  const fallback = { ...first, profile_id: 'next', name: '保留的方案', dirty: false }
  let deleted = false
  const bridge = { async request(command, payload) {
    calls.push({ command, payload })
    if (command === 'profile.list') return { profiles: deleted ? [fallback] : [first, fallback], selected_profile_id: deleted ? 'next' : 'first' }
    if (command === 'profile.get') return payload.profile_id === 'first' ? first : fallback
    if (command === 'package.list') return { releases: [] }
    if (command === 'profile.delete') {
      if (failure) throw Object.assign(new Error('changed'), { code: 'PROFILE_REVISION_CONFLICT' })
      deleted = true
      return { deleted_profile_id: 'first', selected_profile_id: 'next' }
    }
    throw new Error(`Unexpected command ${command}`)
  } }
  const app = renderer.createApp(ProfilePanel, { bridge, installation: { installation_id: 'game' } })
  app.mount(root); await tick(); await tick()
  const find = id => all(root).find(node => node.props['data-testid'] === id)
  const click = async id => { const node = find(id); assert.ok(node, id); await node.props.onClick(); await tick() }
  return { root, calls, find, click, app }
}

test('delete action opens a named confirmation and cancel never sends deletion', async () => {
  const f = await fixture()
  try {
    await f.click('profile-more'); await f.click('profile-delete')
    assert.equal(f.find('profile-delete-dialog').open, true)
    assert.match(text(f.find('profile-delete-dialog')), /待删除方案/)
    assert.match(text(f.find('profile-delete-dialog')), /及未保存的修改/)
    await f.click('profile-delete-cancel')
    assert.equal(f.find('profile-delete-dialog').open, false)
    assert.equal(f.calls.filter(call => call.command === 'profile.delete').length, 0)
  } finally { f.app.unmount() }
})

test('confirm deletes the displayed revision and renders fallback and success notice', async () => {
  const f = await fixture()
  try {
    await f.click('profile-more'); await f.click('profile-delete'); await f.click('profile-delete-confirm')
    assert.deepEqual(f.calls.find(call => call.command === 'profile.delete').payload, { profile_id: 'first', expected_revision: 4 })
    assert.equal(f.find('profile-delete-dialog').open, false)
    assert.match(text(f.find('profile-current-name')), /保留的方案/)
    assert.match(text(f.find('profile-notice')), /已删除「待删除方案」/)
  } finally { f.app.unmount() }
})

test('revision conflict stays in confirmation and does not pretend deletion succeeded', async () => {
  const f = await fixture({ failure: true })
  try {
    await f.click('profile-more'); await f.click('profile-delete'); await f.click('profile-delete-confirm')
    assert.equal(f.find('profile-delete-dialog').open, true)
    assert.match(text(f.find('profile-current-name')), /待删除方案/)
    assert.match(text(f.find('profile-delete-dialog')), /其他窗口修改/)
    assert.equal(f.find('profile-notice'), undefined)
  } finally { f.app.unmount() }
})
