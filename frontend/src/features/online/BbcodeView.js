import { computed, defineComponent, h } from 'vue'
import { isBlock, parseBbcode, plainText, safeImage, safeLink, youtubeLink } from './bbcode.js'
import { createSafeMarkdown } from '../../shared/lib/safeMarkdown.js'
import '../../shared/lib/richText.css'

const SIZE = { 1: '0.8em', 2: '0.9em', 3: '1em', 4: '1.15em', 5: '1.35em', 6: '1.55em', 7: '1.75em' }
const markdown = createSafeMarkdown({ imageUrl: safeImage })
function slotAt(state, position) {
  const prefix = state.env.prefix
  if (!prefix || !state.src.startsWith(prefix, position)) return null
  const end = state.src.indexOf('\uE001', position + prefix.length)
  const index = Number(state.src.slice(position + prefix.length, end))
  return end >= 0 && Number.isInteger(index) && state.env.slots[index] ? { index, end: end + 2 } : null
}
markdown.inline.ruler.before('text', 'bb_slot', (state, silent) => {
  const slot = slotAt(state, state.pos)
  if (!slot) return false
  if (!silent) state.push('bb_slot', '', 0).meta = slot.index
  state.pos = slot.end
  return true
})
markdown.block.ruler.before('paragraph', 'bb_block', (state, start, end, silent) => {
  const position = state.bMarks[start] + state.tShift[start], slot = slotAt(state, position)
  if (!slot || !isBlock(state.env.slots[slot.index]) || state.src.slice(slot.end, state.eMarks[start]).trim()) return false
  if (!silent) { const token = state.push('bb_slot', '', 0); token.meta = slot.index; token.map = [start, start + 1] }
  state.line = start + 1
  return true
}, { alt: ['paragraph', 'blockquote', 'list'] })

// Renders a parsed description with Vue nodes only; links are handed to `open`.
export default defineComponent({
  name: 'BbcodeView',
  props: { source: { type: String, default: '' }, open: { type: Function, required: true } },
  setup(props) {
    const tree = computed(() => parseBbcode(props.source))
    const link = (url, label, key) => h('a', { key, href: url, class: 'bb-link', title: url, rel: 'noreferrer',
      onClick: event => { event.preventDefault(); event.stopPropagation(); props.open(url) } }, label)
    function markdownNodes(tokens, env, key) {
      const result = [], stack = [result]
      tokens.forEach((token, index) => {
        const output = stack.at(-1), id = `${key}.m${index}`
        if (token.type === 'bb_slot') output.push(render(env.slots[token.meta], id))
        else if (token.type === 'inline') output.push(...markdownNodes(token.children, env, id))
        else if (token.type === 'text') output.push(token.content)
        else if (token.type === 'softbreak' || token.type === 'hardbreak') output.push(h('br', { key: id }))
        else if (token.type === 'code_inline') output.push(h('code', { key: id }, token.content))
        else if (token.type === 'fence' || token.type === 'code_block') output.push(h('pre', { key: id }, h('code', { class: token.info ? `language-${token.info.split(/\s/)[0]}` : null }, token.content)))
        else if (token.type === 'image') {
          const source = safeImage(token.attrGet('src'))
          output.push(source ? h('img', { key: id, src: source, alt: token.content, loading: 'lazy', referrerpolicy: 'no-referrer' }) : token.content)
        } else if (token.nesting === -1) stack.pop()
        else if (token.nesting === 1) {
          const kids = [], attrs = Object.fromEntries(token.attrs || [])
          output.push(token.hidden ? kids : token.tag === 'a' ? link(attrs.href, kids, id) : h(token.tag, { ...attrs, key: id }, kids))
          stack.push(kids)
        } else if (token.tag === 'hr') output.push(h('hr', { key: id }))
      })
      return result
    }
    function children(node, key, inList = false) {
      const list = node.c || []
      if (inList) return list.filter(child => child.t !== 'text' || child.v.trim()).map((child, index) => child.t === 'text' ? child.v : render(child, `${key}.${index}`))
      let prefix = '[\uE000BB'
      while (props.source.includes(prefix)) prefix += 'B'
      const slots = [], env = { prefix, slots }
      const source = list.map(child => {
        if (child.t === 'text') return child.v
        const marker = `${prefix}${slots.push(child) - 1}\uE001]`
        return isBlock(child) ? `\n${marker}\n` : marker
      }).join('')
      const inline = !['root', 'quote', 'center', 'left', 'right', 'justify', 'spoiler', '*', 'td', 'th'].includes(node.t)
      return markdownNodes(inline ? markdown.parseInline(source, env) : markdown.parse(source, env), env, key)
    }
    function render(node, key) {
      const kids = (inList = false) => children(node, key, inList)
      switch (node.t) {
        case 'b': return h('strong', { key }, kids())
        case 'i': return h('em', { key }, kids())
        case 'u': return h('span', { key, class: 'bb-u' }, kids())
        case 's': case 'strike': return h('del', { key }, kids())
        case 'sup': return h('sup', { key }, kids())
        case 'sub': return h('sub', { key }, kids())
        case 'size': return h('span', { key, style: { fontSize: SIZE[Number.parseInt(node.a, 10)] || null } }, kids())
        case 'color': case 'font': case 'left': case 'justify': case 'email': return h('span', { key }, kids())
        case 'center': return h('div', { key, class: 'bb-center' }, kids())
        case 'right': return h('div', { key, class: 'bb-right' }, kids())
        case 'heading': case 'h1': case 'h2': return h('h3', { key, class: 'bb-heading' }, kids())
        case 'h3': return h('h4', { key, class: 'bb-heading' }, kids())
        case 'quote': return h('blockquote', { key, class: 'bb-quote' }, kids())
        case 'code': return h('pre', { key, class: 'bb-code' }, h('code', plainText(node)))
        case 'spoiler': return h('details', { key, class: 'bb-spoiler' }, [h('summary', node.a ? String(node.a).slice(0, 80) : '展开隐藏内容'), h('div', kids())])
        case 'line': case 'hr': return h('hr', { key, class: 'bb-line' })
        case 'list': return h(node.a ? 'ol' : 'ul', { key, class: 'bb-list' }, kids(true))
        case '*': return h('li', { key }, kids())
        case 'table': return h('div', { key, class: 'bb-table' }, h('table', h('tbody', kids(true))))
        case 'tr': return h('tr', { key }, kids(true))
        case 'td': return h('td', { key }, kids())
        case 'th': return h('th', { key }, kids())
        case 'url': {
          const url = safeLink(node.a ?? plainText(node))
          const label = node.a ? kids() : plainText(node)
          return url ? link(url, label, key) : h('span', { key }, kids())
        }
        case 'img': {
          const source = plainText(node).trim(), image = safeImage(source), url = safeLink(source)
          if (image) return h('img', { key, class: 'bb-img', src: image, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' })
          return url ? link(url, `图片：${new URL(url).hostname}`, key) : null
        }
        case 'youtube': {
          const url = youtubeLink(plainText(node))
          return url ? link(url, 'YouTube 视频', key) : null
        }
        default: return h('span', { key }, kids())
      }
    }
    return () => h('div', { class: 'bb-content rich-text' }, children(tree.value, 'r'))
  },
})
