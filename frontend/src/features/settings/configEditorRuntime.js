const adapterId = 'pswallpaper'
const definitions = {
  WallpaperFit: { label: '壁纸显示方式', kind: 'choice', options: ['cover', 'fit', 'stretch'] },
  WallpaperBrightness: { label: '壁纸亮度', kind: 'integer', min: 0, max: 100 },
  LogToFile: { label: '写入日志文件', kind: 'boolean' },
}

function validValue(key, value) {
  const field = definitions[key]
  if (field.kind === 'choice') return field.options.includes(value)
  if (field.kind === 'integer') return Number.isInteger(value) && value >= field.min && value <= field.max
  return typeof value === 'boolean'
}

function readDocument(result) {
  if (!result || result.adapter_id !== adapterId || typeof result.editable !== 'boolean'
    || !/^[a-f0-9]{64}$/.test(result.revision) || !Array.isArray(result.fields)) throw new Error('Invalid configuration response')
  const fields = [], values = {}
  for (const field of result.fields) {
    const definition = Object.hasOwn(definitions, field.key) ? definitions[field.key] : null
    if (!definition || Object.hasOwn(values, field.key) || field.kind !== definition.kind || !validValue(field.key, field.value)) throw new Error('Invalid configuration field')
    fields.push({ key: field.key, ...definition, label: field.label || definition.label })
    values[field.key] = field.value
  }
  if (result.editable && !fields.length) throw new Error('Missing configuration field')
  return { ...result, fields, values }
}

function errorMessage(error, saving) {
  const code = String(error?.code || '')
  if (code === 'CONFIG_CHANGED') return '配置文件已被其他程序修改。请重新读取后再保存。'
  if (code && typeof error.message === 'string' && error.message) return error.message
  return saving ? '未能保存配置，编辑内容已保留。请检查文件是否被占用或是否有写入权限。' : '未能读取配置。请检查文件是否存在、被占用或有读取权限。'
}

export function createConfigEditorRuntime(bridge, state = {}) {
  let installationId = null, sequence = 0, disposed = false
  function reset() {
    Object.assign(state, { open: false, loading: false, saving: false, document: null, values: {}, original: {}, backup: true, needsRefresh: false, error: '', notice: '' })
  }
  reset()
  const current = (token, id) => !disposed && state.open && token === sequence && id === installationId
  function setInstallation(id) {
    if (installationId === id) return
    installationId = id || null
    sequence += 1
    reset()
  }
  function close() {
    sequence += 1
    reset()
  }
  function adopt(document) {
    state.document = document
    state.original = { ...document.values }
    state.values = { ...document.values }
  }
  async function read() {
    if (disposed || !installationId || !state.open || state.saving) return false
    const token = ++sequence, id = installationId
    state.loading = true
    state.error = ''
    state.notice = ''
    state.needsRefresh = false
    // Old values cannot be saved while a fresh read is in progress or fails.
    state.document = null
    state.values = {}
    state.original = {}
    try {
      const result = readDocument(await bridge.request('config.read', { installation_id: id, adapter_id: adapterId }))
      if (!current(token, id)) return false
      adopt(result)
      return true
    } catch (error) {
      if (current(token, id)) state.error = errorMessage(error, false)
      return false
    } finally {
      if (current(token, id)) state.loading = false
    }
  }
  async function open() {
    if (disposed || !installationId || state.open) return false
    state.open = true
    return read()
  }
  function changes() {
    const result = {}
    for (const key of Object.keys(definitions)) {
      if (Object.hasOwn(state.original, key) && state.values[key] !== state.original[key]) result[key] = state.values[key]
    }
    return result
  }
  function canSave() {
    return state.open && !state.loading && !state.saving && !state.needsRefresh && Boolean(state.document?.editable)
      && Object.keys(changes()).length > 0 && state.document.fields.every(field => validValue(field.key, state.values[field.key]))
  }
  async function save() {
    if (!canSave() || disposed || !installationId) return false
    const token = ++sequence, id = installationId, submitted = changes()
    state.saving = true
    state.error = ''
    state.notice = ''
    try {
      const result = readDocument(await bridge.request('config.save', {
        installation_id: id, adapter_id: adapterId, expected_revision: state.document.revision,
        changes: submitted, backup: state.backup,
      }))
      if (!current(token, id)) return false
      if (result.saved !== true || typeof result.changed !== 'boolean' || typeof result.backup_created !== 'boolean'
        || Object.keys(submitted).some(key => result.values[key] !== submitted[key])) throw new Error('Unconfirmed configuration save')
      adopt(result)
      state.notice = result.changed ? result.backup_created ? '已保存，并备份了原文件。' : '已保存。' : '文件无需修改。'
      return true
    } catch (error) {
      if (current(token, id)) {
        state.error = errorMessage(error, true)
        state.needsRefresh = error?.code === 'CONFIG_CHANGED'
      }
      return false
    } finally {
      if (current(token, id)) state.saving = false
    }
  }
  function dispose() { disposed = true; close() }
  return { setInstallation, open, close, read, changes, canSave, save, dispose }
}
