import test from 'node:test'
import assert from 'node:assert/strict'
import { createSafeMarkdown, safeWebLink } from './safeMarkdown.js'
import { safeImage } from '../../features/online/bbcode.js'

test('renders headings, lists, code, tables, links and streamed fragments', () => {
  const md = createSafeMarkdown()
  const html = md.render('# 标题\n\n- **启用** `Mod.dll`\n- 停用\n\n|版本|说明|\n|---|---|\n|1.2|修复|\n\n```json\n{"enabled":true}\n```\n[帮助](https://example.com/help)')
  for (const tag of ['h1', 'ul', 'li', 'strong', 'code', 'table', 'th', 'td', 'pre', 'a']) assert.match(html, new RegExp(`<${tag}[ >]`))
  assert.match(html, /rel="noreferrer noopener"/)
  assert.match(md.render('**尚未完成'), /尚未完成/)
})

test('never executes raw HTML, dangerous links or automatic remote media in AI replies', () => {
  const md = createSafeMarkdown()
  const html = md.render('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[x](javascript:alert(1))\n[x](file:///C:/secret)\n![追踪](https://example.com/private.png)\n[x](https://user:password@example.com)')
  assert.doesNotMatch(html, /<script|<img|<a /)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /追踪/)
  assert.equal(safeWebLink('file:///C:/secret'), null)
})

test('Nexus Markdown images retain the existing Nexus-only policy', () => {
  const md = createSafeMarkdown({ imageUrl: safeImage })
  const html = md.render('![截图](https://staticdelivery.nexusmods.com/mods/1/a.png)\n![外站](https://other.example/a.png)')
  assert.equal((html.match(/<img /g) || []).length, 1)
  assert.match(html, /referrerpolicy="no-referrer"/)
  assert.match(html, /外站/)
})
