// Reuse the fixed RimCrow theme token data without its settings store or services.
import themes from '../../app/styles/builtinThemes.json'
import { reactive } from 'vue'
import { createBridge } from '../../app/bridge/client.js'
import { createThemePreferences } from './themePreferences.js'
export const themeChoices = themes.filter(theme => ['obsidian-cyan', 'graphite-ash', 'win11-mica'].includes(theme.id)).map(theme =>
  theme.id === 'win11-mica' ? { ...theme, tokens: { ...theme.tokens,
    // These accents are also used as small status text on the light surface.
    accent: { ...theme.tokens.accent, primary: '#005fa3', success: '#256b16', warn: '#674000' },
  } } : theme)
export function applyTheme(id = 'obsidian-cyan') {
  const theme = themeChoices.find(item => item.id === id) || themeChoices[0]
  for (const [group, values] of Object.entries(theme.tokens)) for (const [name, value] of Object.entries(values)) {
    document.documentElement.style.setProperty(`--${group}-${name}`, value)
    if (/^#[\da-f]{6}$/i.test(value)) document.documentElement.style.setProperty(`--rgb-${group}-${name}`, [1, 3, 5].map(index => parseInt(value.slice(index, index + 2), 16)).join(' '))
  }
  document.documentElement.dataset.theme = theme.id
  document.documentElement.style.colorScheme = theme.id === 'win11-mica' ? 'light' : 'dark'
  return theme.id
}

export const themeState = reactive({ current: themeChoices[0].id, loading: false, saving: false, error: '' })
const bridge = createBridge({ transport: request => window.pywebview.api.dispatch(request) })
const preferences = createThemePreferences({
  request: (command, payload) => bridge.request(command, payload), apply: applyTheme,
  choices: themeChoices.map(theme => theme.id), state: themeState,
})
export const selectTheme = id => preferences.select(id)
export function initializeTheme() {
  applyTheme(themeState.current)
  const load = () => { void preferences.load() }
  window.addEventListener('pywebviewready', load)
  window.addEventListener('beforeunload', () => {
    window.removeEventListener('pywebviewready', load)
    void bridge.dispose()
  }, { once: true })
  if (window.pywebview?.api?.dispatch) load()
}
