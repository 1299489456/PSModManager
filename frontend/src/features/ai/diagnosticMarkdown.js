import { createSafeMarkdown } from '../../shared/lib/safeMarkdown.js'

// Model text can format an explanation, never inject HTML or fetch remote media.
const markdown = createSafeMarkdown()
export const diagnosticMarkdown = value => markdown.render(String(value || ''))
