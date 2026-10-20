import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import * as Vue from 'vue'
import * as Icons from 'lucide-vue-next'
import { releaseNotes } from './releaseNotes.js'

const notes = readFileSync(new URL('../../../docs/user/release-notes.md', import.meta.url), 'utf8')
const { descriptor } = parse(readFileSync(new URL('./AboutUpdates.vue', import.meta.url), 'utf8'))
const compiled = compileScript(descriptor, { id: 'about-updates-test', inlineTemplate: true }).content
const executable = compiled
  .replace(/import\s+\{([^}]+)\}\s+from\s+['"](vue|lucide-vue-next)['"]/g, (_, bindings, source) =>
    'const {' + bindings.split(',').map(binding => binding.trim().replace(/\s+as\s+/, ': ')).join(',') + '} = ' + (source === 'vue' ? 'Vue' : 'Icons'))
  .replace(/import notes from ['"][^'"]+['"]/, '')
  .replace(/import \{ releaseNotes \} from ['"][^'"]+['"]/, '')
  .replace(/import ['"][^'"]+\.css['"]/, '')
  .replace('export default', 'return')
const AboutUpdates = new Function('Vue', 'Icons', 'releaseNotes', 'notes', executable)(Vue, Icons, releaseNotes, notes)

test('versioned notes support Windows newlines and render Markdown without package navigation', () => {
  const releases = releaseNotes('# 0.1.1 · 2026-10-19\r\n\r\n- **修复** `Mod.dll`\r\n- [本地帮助](quick-start.md)\r\n- [官网](https://example.com)\r\n<script>bad()</script>\r\n\r\n# 0.1.0 便携版\r\n历史记录')
  assert.deepEqual(releases.map(release => [release.version, release.label]), [['0.1.1', '2026-10-19'], ['0.1.0', '便携版']])
  assert.match(releases[0].html, /<strong>修复<\/strong> <code>Mod.dll<\/code>/)
  assert.match(releases[0].html, /<li>本地帮助<\/li>/)
  assert.match(releases[0].html, /href="https:\/\/example.com"/)
  assert.doesNotMatch(releases[0].html, /<script>|href="quick-start.md"/)
  assert.match(releases[1].html, /历史记录/)
})

test('the shipped current release is open and older records are collapsed without fetching', async () => {
  const version = releaseNotes(notes)[0].version
  const html = await renderToString(Vue.createSSRApp(AboutUpdates, { version }))
  assert.match(html, new RegExp(`已安装 v${version.replaceAll('.', '\\.')}`))
  assert.match(html, /data-testid="release-current"/)
  assert.match(html, /当前版本/)
  assert.match(html, /首次使用.*分析日志.*本地查询/)
  assert.match(html, /待重新生成程序集/)
  assert.match(html, /<details[^>]*data-version="0.1.0"[^>]*>/)
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/)
  assert.doesNotMatch(html, /<img|<iframe|<script/)
  assert.match(html, /2026-10-16（第三次界面更新）/)
})
