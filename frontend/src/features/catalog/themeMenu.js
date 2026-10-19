export function themeMenuIndex(current, key, count) {
  if (!Number.isInteger(count) || count <= 0) return 0
  const index = Math.max(0, Math.min(count - 1, current))
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  if (key === 'ArrowDown') return (index + 1) % count
  if (key === 'ArrowUp') return (index + count - 1) % count
  return index
}
