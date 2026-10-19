// Nexus Mod descriptions are BBCode. This parser builds a plain tree of known
// tags; anything else stays literal text, so nothing from a description is ever
// injected as HTML. Rendering lives in BbcodeView.js.

const BLOCK = new Set(['center', 'left', 'right', 'justify', 'list', 'quote', 'code', 'spoiler', 'line', 'hr', 'table', 'tr', 'td', 'th', 'heading', 'h1', 'h2', 'h3', 'img', 'youtube', '*'])
const KNOWN = new Set([...BLOCK, 'b', 'i', 'u', 's', 'strike', 'size', 'color', 'font', 'url', 'email', 'sup', 'sub'])
const VOID = new Set(['line', 'hr'])
const TAG = /\[(\/?)(\*|[a-z][a-z0-9]{0,9})(?:=([^\]\n]{0,500}))?\]/gi
const MAX_NODES = 8000, MAX_DEPTH = 40

export const IMAGE_HOSTS = ['staticdelivery.nexusmods.com', 'images.nexusmods.com', 'avatars.nexusmods.com']

export function isBlock(node) { return node?.t !== 'text' && BLOCK.has(node?.t) }

export function safeLink(value) {
  const text = String(value ?? '').trim().replace(/^["']|["']$/g, '')
  if (!text || text.length > 2048 || /\s/.test(text)) return null
  try {
    const url = new URL(/^www\./i.test(text) ? `https://${text}` : text)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}

export function safeImage(value) {
  const url = safeLink(value)
  if (!url) return null
  const parsed = new URL(url)
  return parsed.protocol === 'https:' && IMAGE_HOSTS.includes(parsed.hostname) ? url : null
}

export function youtubeLink(value) {
  const text = String(value ?? '').trim()
  const id = /^[A-Za-z0-9_-]{6,20}$/.test(text) ? text : text.match(/(?:v=|youtu\.be\/|embed\/)([A-Za-z0-9_-]{6,20})/)?.[1]
  return id ? `https://www.youtube.com/watch?v=${id}` : null
}

export function parseBbcode(source) {
  const root = { t: 'root', c: [] }, stack = [root]
  const input = String(source ?? '')
  let count = 0, last = 0, match
  const top = () => stack.at(-1)
  const text = value => { if (value && count++ < MAX_NODES) { const c = top().c, previous = c.at(-1); if (previous?.t === 'text') previous.v += value; else c.push({ t: 'text', v: value }) } }
  const close = name => {
    const index = stack.findLastIndex(node => node.t === name)
    if (index < 1) return false
    stack.length = index
    return true
  }
  TAG.lastIndex = 0
  while ((match = TAG.exec(input))) {
    const [raw, slash, rawName, arg] = match
    const name = rawName.toLowerCase()
    text(input.slice(last, match.index))
    last = TAG.lastIndex
    if (!KNOWN.has(name)) { text(raw); continue }
    if (slash) {
      // [/*] closes the item; any other unmatched closer is shown as written.
      if (!close(name) && name !== '*') text(raw)
      continue
    }
    if (count++ >= MAX_NODES) break
    if (name === 'code') {
      // Code is literal up to its closer.
      const end = input.toLowerCase().indexOf('[/code]', last)
      const body = input.slice(last, end < 0 ? input.length : end)
      top().c.push({ t: 'code', c: [{ t: 'text', v: body.replace(/^\n|\n$/g, '') }] })
      last = TAG.lastIndex = end < 0 ? input.length : end + 7
      continue
    }
    if (name === '*') {
      // A new item ends the previous one inside the nearest list.
      const list = stack.findLastIndex(node => node.t === 'list')
      if (list < 1) { text(raw); continue }
      stack.length = list + 1
    }
    const node = { t: name, a: arg ?? null, c: [] }
    top().c.push(node)
    if (!VOID.has(name) && stack.length < MAX_DEPTH) stack.push(node)
  }
  text(input.slice(last))
  return root
}

// Plain text of a node, used for link labels and image/video sources.
export function plainText(node) {
  if (!node) return ''
  if (node.t === 'text') return node.v
  return (node.c || []).map(plainText).join('')
}
