import test from 'node:test'
import assert from 'node:assert/strict'
import { parseBbcode, plainText, safeImage, safeLink, youtubeLink } from './bbcode.js'

const tags = node => node.t === 'text' ? [] : [node.t, ...(node.c || []).flatMap(tags)]

test('known tags nest; unknown tags and stray closers stay literal text', () => {
  const tree = parseBbcode('[size=5][b]Title[/b][/size]\n[script]alert(1)[/script] [/i] <b>x</b>')
  assert.deepEqual(tags(tree), ['root', 'size', 'b'])
  assert.equal(tree.c[0].a, '5')
  assert.equal(plainText(tree), 'Title\n[script]alert(1)[/script] [/i] <b>x</b>')
})

test('list items close each other and [/*] is optional', () => {
  const tree = parseBbcode('[list]\n[*]one[/*]\n[*]two\n[*]three[/list]after')
  const list = tree.c[0]
  assert.equal(list.t, 'list')
  assert.deepEqual(list.c.filter(node => node.t === '*').map(plainText), ['one', 'two\n', 'three'])
  assert.equal(plainText(tree.c.at(-1)), 'after')
})

test('code is literal and unclosed tags end at the input', () => {
  const tree = parseBbcode('[code][b]not bold[/b][/code][quote]open')
  assert.equal(tree.c[0].t, 'code')
  assert.equal(plainText(tree.c[0]), '[b]not bold[/b]')
  assert.equal(tree.c[1].t, 'quote')
})

test('tables keep rows and cells', () => {
  const tree = parseBbcode('[table][tr][th]按键[/th][th]功能[/th][/tr][tr][td]Ctrl+R[/td][td]整理[/td][/tr][/table]')
  assert.deepEqual(tags(tree), ['root', 'table', 'tr', 'th', 'th', 'tr', 'td', 'td'])
})

test('links allow http(s) only; images only from Nexus hosts', () => {
  assert.equal(safeLink('https://github.com/a/b'), 'https://github.com/a/b')
  assert.equal(safeLink('www.example.com'), 'https://www.example.com/')
  for (const bad of ['javascript:alert(1)', 'file:///C:/x', 'https://u:p@x.test/', 'data:text/html,x', 'https://a b']) assert.equal(safeLink(bad), null)
  assert.ok(safeImage('https://staticdelivery.nexusmods.com/mods/1/a.png'))
  assert.equal(safeImage('https://i.imgur.com/a.png'), null)
  assert.equal(safeImage('http://staticdelivery.nexusmods.com/a.png'), null)
  assert.equal(youtubeLink('https://youtu.be/dQw4w9WgXcQ'), 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
  assert.equal(youtubeLink('javascript:x'), null)
})

test('very deep or large input is bounded', () => {
  const tree = parseBbcode('[b]'.repeat(500) + 'x')
  let depth = 0, node = tree
  while (node.c?.[0] && node.c[0].t !== 'text') { node = node.c[0]; depth++ }
  assert.ok(depth < 45)
  assert.ok(plainText(parseBbcode('[b]a[/b]'.repeat(20000))).length <= 20000)
})
