import MarkdownIt from 'markdown-it'

// Model text can format an explanation, never inject HTML or fetch remote media.
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true })
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index].content || '图片')
export const diagnosticMarkdown = value => markdown.render(String(value || ''))
