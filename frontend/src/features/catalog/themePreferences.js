// Desktop settings are persisted by the local application, independently of
// the origin of NavigateToString documents or browser storage availability.
export function createThemePreferences({ request, apply, choices, state }) {
  let revision = 0
  const valid = id => choices.includes(id)
  return {
    async load() {
      const started = revision
      state.loading = true
      state.error = ''
      try {
        const saved = await request('app.theme.get')
        if (!valid(saved?.theme_id)) throw new Error('Invalid theme response')
        if (started === revision) state.current = apply(saved.theme_id)
        return true
      } catch {
        if (started === revision) state.error = '未能读取主题设置，请重新选择主题。'
        return false
      } finally { state.loading = false }
    },
    async select(id) {
      if (state.saving || !valid(id)) return false
      revision += 1
      state.saving = true
      state.error = ''
      try {
        const saved = await request('app.theme.set', { theme_id: id })
        if (saved?.theme_id !== id) throw new Error('Invalid theme response')
        state.current = apply(id)
        return true
      } catch {
        state.error = '主题未能保存，请重试。'
        return false
      } finally { state.saving = false }
    },
  }
}
