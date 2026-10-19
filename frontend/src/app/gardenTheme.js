// The single Solarpunk "greenhouse" look. Tokens keep the names the existing
// components already read (--bg-*, --text-*, --accent-*, --rgb-*), so screens
// that are not redrawn still pick up the same light palette. No theme choice.
export const gardenTokens = {
  bg: { deep: '#eaf7ee', surface: '#ffffff', elevated: '#ffffff', highlight: '#ecfdf3', muted: '#f6fdf8', inset: '#f0fdf4', overlay: '#14532d', contrast: '#1f2a24', neutral: '#8aa597' },
  glass: { light: 'rgba(255, 255, 255, 0.62)', medium: 'rgba(255, 255, 255, 0.86)', heavy: 'rgba(255, 255, 255, 0.95)' },
  accent: { danger: '#dc2626', highlight: '#f59e0b', special: '#7c5cd6', cool: '#0284c7', primary: '#16a34a', success: '#15803d', tip: '#4ade80', warn: '#b45309', secondary: '#ea580c', warning: '#d97706' },
  text: { main: '#1f2a24', soft: 'rgba(31, 42, 36, 0.82)', dim: '#5b6f64', subtle: '#8aa597', disabled: 'rgba(91, 111, 100, 0.50)', inverse: '#ffffff' },
  border: { base: '#166534' },
  overlay: { scrim: 'rgba(20, 70, 45, 0.22)' },
}

export function applyGardenTheme(root = document.documentElement) {
  for (const [group, values] of Object.entries(gardenTokens)) for (const [name, value] of Object.entries(values)) {
    root.style.setProperty(`--${group}-${name}`, value)
    root.style.setProperty(`--color-${group}-${name}`, value)
    if (/^#[\da-f]{6}$/i.test(value)) root.style.setProperty(`--rgb-${group}-${name}`, [1, 3, 5].map(index => parseInt(value.slice(index, index + 2), 16)).join(' '))
  }
  root.style.setProperty('--shadow-color', '134 239 172')
  root.dataset.theme = 'garden'
  root.style.colorScheme = 'light'
}