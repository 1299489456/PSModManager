export function filterRows(rows, selected, query, nameOf) {
  const term = query.trim().toLocaleLowerCase()
  return rows.filter(item => item.selected === selected && (!term || [nameOf(item), item.version_raw, item.group].filter(value => value != null).join(' ').toLocaleLowerCase().includes(term)))
}
export function selectRows(rows, selected, anchor, id, { ctrlKey = false, metaKey = false, shiftKey = false } = {}) {
  const ids = rows.map(row => row.release_id)
  if (!ids.includes(id)) return { selected, anchor }
  if (shiftKey && ids.includes(anchor)) {
    const [start, end] = [ids.indexOf(anchor), ids.indexOf(id)].sort((a, b) => a - b)
    const range = ids.slice(start, end + 1)
    return { selected: ctrlKey || metaKey ? [...new Set([...selected, ...range])] : range, anchor }
  }
  if (ctrlKey || metaKey) return { selected: selected.includes(id) ? selected.filter(value => value !== id) : [...selected, id], anchor: id }
  return { selected: [id], anchor: id }
}

// A press on a selected row must keep the whole selection until we know whether
// it was a click or the beginning of a native drag. A drag never runs the click edit.
export function createRowGesture() {
  let pending = null, dragged = false
  return {
    press(rows, selected, anchor, id, event = {}) {
      pending = null; dragged = false
      if (event.button != null && event.button !== 0) return { selected, anchor }
      if (selected.includes(id) && !event.shiftKey) {
        pending = { rows, selected, anchor, id, modifiers: { ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey } }
        return { selected, anchor }
      }
      const result = selectRows(rows, selected, anchor, id, event)
      pending = { applied: true }
      return result
    },
    drag() { dragged = true },
    click(rows, selected, anchor, id, event = {}) {
      const press = pending; pending = null
      if (dragged) { dragged = false; return { selected, anchor } }
      if (press?.applied) return { selected, anchor }
      if (press) return selectRows(press.rows, press.selected, press.anchor, press.id, press.modifiers)
      return selectRows(rows, selected, anchor, id, event)
    },
    clear() { pending = null; dragged = false },
  }
}

// Dragged IDs are never deserialized from DataTransfer or another view.
export function createProfileDrag() {
  let session = null
  return {
    begin(profile, id, picked) {
      session = null
      const row = profile?.selections.find(item => item.release_id === id)
      if (!row) return []
      const ids = picked.includes(id) ? profile.selections.filter(item => item.selected === row.selected && picked.includes(item.release_id)).map(item => item.release_id) : [id]
      session = { profile_id: profile.profile_id, revision: profile.revision, source: row.selected, ids }
      return ids
    },
    accepts(profile, selected) { return !!session && profile?.profile_id === session.profile_id && profile.revision === session.revision && selected !== session.source },
    take(profile, selected) {
      const valid = this.accepts(profile, selected)
      const old = session; session = null
      return valid ? { release_ids: old.ids, selected } : null
    },
    clear() { session = null },
  }
}

// This interaction stays inside the current editor. WebView/OLE can omit a
// native HTML drop on quick releases, so only a matching pointerup may commit.
export function createPointerProfileDrag(threshold = 6) {
  const drag = createProfileDrag()
  let armed = null, active = false
  function valid(profile) { return armed && profile?.profile_id === armed.profileId && profile?.revision === armed.revision }
  function clear() { armed = null; active = false; drag.clear() }
  function targetAction(profile, target) {
    if (!active || !valid(profile)) return null
    // Existing boolean callers keep their cross-lane-only behavior.
    if (typeof target === 'boolean') return drag.accepts(profile, target) ? { release_ids: [...armed.picked], selected: target } : null
    if (!target || typeof target !== 'object' || Array.isArray(target)
      || Object.keys(target).length !== 2 || !Object.hasOwn(target, 'selected') || !Object.hasOwn(target, 'before_release_id')
      || typeof target.selected !== 'boolean') return null
    const before = target.before_release_id
    if (before !== null) {
      if (typeof before !== 'string' || armed.picked.includes(before)) return null
      const anchor = profile.selections.find(row => row.release_id === before)
      if (!anchor || anchor.selected !== target.selected) return null
    }
    return { release_ids: [...armed.picked], selected: target.selected, before_release_id: before }
  }
  return {
    press(profile, id, picked, event) {
      clear()
      if (!profile || event.button !== 0 || !Number.isInteger(event.pointerId) || !profile.selections.some(row => row.release_id === id)) return false
      armed = { profileId: profile.profile_id, revision: profile.revision, id, picked: [...picked], pointerId: event.pointerId, x: event.clientX, y: event.clientY }
      return true
    },
    move(profile, event) {
      if (!armed || armed.pointerId !== event.pointerId) return null
      if (!valid(profile) || !(event.buttons & 1)) { clear(); return null }
      let started = false
      if (!active && Math.hypot(event.clientX - armed.x, event.clientY - armed.y) >= threshold) {
        const ids = drag.begin(profile, armed.id, armed.picked)
        if (!ids.length) { clear(); return null }
        armed.picked = ids; active = true; started = true
      }
      return active ? { started, release_ids: [...armed.picked] } : null
    },
    accepts(profile, target) { return !!targetAction(profile, target) },
    release(profile, pointerId, target) {
      if (!armed || pointerId !== armed.pointerId) return null
      const result = targetAction(profile, target)
      clear()
      return result
    },
    cancel: clear,
    get active() { return active },
    get pointerId() { return armed?.pointerId ?? null },
  }
}

// Rectangles belong to the visible rows of this editor, not the browser's drag
// payload. Filtering never inserts hidden IDs into the moving set or anchor.
export function insertionTarget(visibleRows, movingIds, selected, clientY, rowRects) {
  if (typeof selected !== 'boolean' || !Number.isFinite(clientY) || !Array.isArray(rowRects)) return null
  const rectangles = new Map(rowRects.map(rect => [rect.release_id, rect]))
  const candidates = visibleRows.filter(row => row.selected === selected && !movingIds.includes(row.release_id))
  for (const row of candidates) {
    const rect = rectangles.get(row.release_id)
    if (!rect || !Number.isFinite(rect.top) || !Number.isFinite(rect.height) || rect.height <= 0) return null
  }
  const before = candidates.find(row => { const rect = rectangles.get(row.release_id); return clientY < rect.top + rect.height / 2 })
  return { selected, before_release_id: before?.release_id ?? null }
}
