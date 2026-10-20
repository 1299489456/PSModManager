import { createSafeMarkdown } from '../shared/lib/safeMarkdown.js'

// The same versioned document is bundled for offline reading and shown in Settings.
export function releaseNotes(markdown) {
  const headings = [...markdown.matchAll(/^# (\d+\.\d+\.\d+)([^\r\n]*)\r?$/gm)]
  const renderer = createSafeMarkdown()
  return headings.map((heading, index) => {
    const content = markdown.slice(heading.index + heading[0].length, headings[index + 1]?.index).trim()
    // Package-relative documentation links cannot navigate in the desktop webview.
    const text = content.replace(/\[([^\]]+)\]\((?!https?:)[^)]+\)/g, '$1')
    return { version: heading[1], label: heading[2].trim().replace(/^[·—–]\s*/, ''), html: renderer.render(text) }
  })
}
