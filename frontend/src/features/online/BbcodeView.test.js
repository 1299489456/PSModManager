import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createSSRApp, computed, defineComponent, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import * as bbcode from './bbcode.js'
import { createSafeMarkdown } from '../../shared/lib/safeMarkdown.js'

// The component uses render functions; only its CSS import needs a browser.
const source = readFileSync(new URL('./BbcodeView.js', import.meta.url), 'utf8')
  .replace(/^import .*$/gm, '').replace('export default', 'return')
const scope = { computed, defineComponent, h, ...bbcode, createSafeMarkdown }
const View = new Function(...Object.keys(scope), source)(...Object.values(scope))
const render = text => renderToString(createSSRApp({ render: () => h(View, { source: text, open() {} }) }))

test('renders pure Markdown blocks and literal BBCode in fenced and inline code', async () => {
  const html = await render('# 说明\n\n- **启用**\n\n|版本|用途|\n|---|---|\n|1|稳定|\n\n```text\n[b]示例[/b]\n```\n`[i]原样[/i]`')
  assert.match(html, /<h1>说明<\/h1>/)
  assert.match(html, /<ul>/)
  assert.match(html, /<table>/)
  assert.match(html, /<code class="language-text">\[b\]示例\[\/b\]/)
  assert.match(html, /<code>\[i\]原样\[\/i\]<\/code>/)
})

test('renders Markdown within BBCode without trusting HTML or external image hosts', async () => {
  const html = await render('[b]名称[/b]\n## 功能\n- **快速安装**\n[quote]`安全代码`[/quote]\n<script>alert(1)</script>\n![外部图片](https://example.invalid/a.png)')
  assert.match(html, /<strong[^>]*>.*名称/)
  assert.match(html, /<h2>功能<\/h2>/)
  assert.match(html, /<strong>快速安装<\/strong>/)
  assert.match(html, /<blockquote/)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<script|<img/)
})

test('mixed BBCode stays within Markdown list items, paragraphs and table cells', async () => {
  const html = (await render('- 自动整理\n- [b]保留配置[/b]\n\n选择 [i]指定版本[/i] 安装。\n\n|版本|说明|\n|---|---|\n|1|[b]稳定[/b]|')).replace(/<!--[\s\S]*?-->/g, '')
  assert.match(html, /<li><strong[^>]*>保留配置<\/strong><\/li>/)
  assert.match(html, /<p>选择 <em[^>]*>指定版本<\/em> 安装。<\/p>/)
  assert.match(html, /<td><strong[^>]*>稳定<\/strong><\/td>/)
  assert.doesNotMatch(html, /\uE000|\uE001/)
})
