import { computed, defineComponent, h } from 'vue'
import { isBlock, parseBbcode, plainText, safeImage, safeLink, youtubeLink } from './bbcode.js'

const SIZE = { 1: '0.8em', 2: '0.9em', 3: '1em', 4: '1.15em', 5: '1.35em', 6: '1.55em', 7: '1.75em' }
const BARE_URL = /(https?:\/\/[^\s<>"'\][]+)/g

// Renders a parsed description with Vue nodes only; links are handed to `open`.
export default defineComponent({
  name: 'BbcodeView',
  props: { source: { type: String, default: '' }, open: { type: Function, required: true } },
  setup(props) {
    const tree = computed(() => parseBbcode(props.source))
    const link = (url, label, key) => h('a', { key, href: url, class: 'bb-link', title: url, rel: 'noreferrer',
      onClick: event => { event.preventDefault(); props.open(url) } }, label)
    function textNodes(value, key, inList) {
      if (inList && !value.trim()) return []
      const out = []
      value.split('\n').forEach((line, index) => {
        if (index) out.push(h('br', { key: `${key}-br${index}` }))
        line.split(BARE_URL).forEach((part, piece) => {
          if (!part) return
          const url = piece % 2 ? safeLink(part) : null
          out.push(url ? link(url, part, `${key}-u${index}-${piece}`) : part)
        })
      })
      return out
    }
    function children(node, key, inList = false) {
      const list = node.c || []
      return list.flatMap((child, index) => {
        if (child.t !== 'text') return [render(child, `${key}.${index}`)]
        // Line breaks right next to blocks are layout noise in Nexus markup.
        let value = child.v
        if (isBlock(list[index - 1])) value = value.replace(/^\n/, '')
        if (isBlock(list[index + 1])) value = value.replace(/\n$/, '')
        return textNodes(value, `${key}.${index}`, inList)
      })
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
    return () => h('div', { class: 'bb-content' }, children(tree.value, 'r'))
  },
})
