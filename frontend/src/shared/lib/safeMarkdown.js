import MarkdownIt from 'markdown-it'

export function safeWebLink(value) {
  try {
    const url = new URL(String(value))
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null
  } catch { return null }
}

// Descriptions and model replies are text, not trusted HTML. Media is opt-in.
export function createSafeMarkdown({ imageUrl = () => null } = {}) {
  const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
  md.validateLink = value => Boolean(safeWebLink(value))
  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index], source = imageUrl(token.attrGet('src'))
    const alt = md.utils.escapeHtml(token.content || '图片')
    return source ? `<img src="${md.utils.escapeHtml(source)}" alt="${alt}" loading="lazy" referrerpolicy="no-referrer">` : alt
  }
  const openLink = md.renderer.rules.link_open || ((tokens, index, options, env, renderer) => renderer.renderToken(tokens, index, options))
  md.renderer.rules.link_open = (tokens, index, options, env, renderer) => {
    tokens[index].attrSet('rel', 'noreferrer noopener')
    return openLink(tokens, index, options, env, renderer)
  }
  return md
}
