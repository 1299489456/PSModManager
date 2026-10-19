// Pressing the dimmed area around an open <dialog> behaves exactly like Esc:
// a cancel event is dispatched first, so each dialog's own handler still decides
// whether it may close (busy dialogs keep refusing). A dialog marked
// data-dismiss="keep" (running deployment, unsaved edits) ignores the backdrop.
export function outsideBox(rect, x, y) {
  return x < rect.left || x > rect.right || y < rect.top || y > rect.bottom
}

export function installDialogDismiss(root = document) {
  let armed = null
  // A click on the backdrop targets the dialog element itself, outside its box.
  const backdropOf = event => {
    const dialog = event.target
    if (dialog?.tagName !== 'DIALOG' || !dialog.open || dialog.dataset?.dismiss === 'keep') return null
    return outsideBox(dialog.getBoundingClientRect(), event.clientX, event.clientY) ? dialog : null
  }
  // Both press and release must land on the backdrop, so a text selection or
  // drag that starts inside the sheet and ends outside never closes it.
  const press = event => { armed = event.button === 0 ? backdropOf(event) : null }
  const release = event => {
    const dialog = backdropOf(event), pressed = armed
    armed = null
    if (!dialog || dialog !== pressed) return
    if (dialog.dispatchEvent(new Event('cancel', { cancelable: true }))) dialog.close()
  }
  root.addEventListener('pointerdown', press, true)
  root.addEventListener('click', release, true)
  return () => {
    root.removeEventListener('pointerdown', press, true)
    root.removeEventListener('click', release, true)
  }
}
