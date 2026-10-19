import test from 'node:test'
import assert from 'node:assert/strict'
import { installDialogDismiss, outsideBox } from './dialogDismiss.js'

function fakeRoot() {
  const handlers = {}
  return { handlers, addEventListener(type, fn) { handlers[type] = fn }, removeEventListener(type) { delete handlers[type] } }
}
function fakeDialog({ refuse = false } = {}) {
  const dialog = { tagName: 'DIALOG', open: true, closed: 0, cancels: 0,
    getBoundingClientRect: () => ({ left: 100, right: 500, top: 200, bottom: 600 }),
    dispatchEvent(event) { dialog.cancels++; if (refuse) event.preventDefault(); return !event.defaultPrevented },
    close() { dialog.closed++; dialog.open = false } }
  return dialog
}
const at = (target, x, y) => ({ target, clientX: x, clientY: y, button: 0 })

test('box test treats the dialog edge as inside', () => {
  const rect = { left: 0, right: 10, top: 0, bottom: 10 }
  assert.equal(outsideBox(rect, 10, 10), false)
  assert.equal(outsideBox(rect, 11, 5), true)
  assert.equal(outsideBox(rect, 5, -1), true)
})

test('pressing and releasing on the backdrop sends cancel and closes', () => {
  const root = fakeRoot(), dialog = fakeDialog()
  installDialogDismiss(root)
  root.handlers.pointerdown(at(dialog, 50, 50)); root.handlers.click(at(dialog, 50, 50))
  assert.equal(dialog.cancels, 1); assert.equal(dialog.closed, 1)
})

test('a busy dialog that prevents cancel stays open', () => {
  const root = fakeRoot(), dialog = fakeDialog({ refuse: true })
  installDialogDismiss(root)
  root.handlers.pointerdown(at(dialog, 50, 50)); root.handlers.click(at(dialog, 50, 50))
  assert.equal(dialog.cancels, 1); assert.equal(dialog.closed, 0)
})

test('clicks inside the sheet, on its contents, or drags out of it are ignored', () => {
  const root = fakeRoot(), dialog = fakeDialog(), child = { tagName: 'BUTTON' }
  const dispose = installDialogDismiss(root)
  root.handlers.pointerdown(at(dialog, 300, 300)); root.handlers.click(at(dialog, 300, 300))
  root.handlers.pointerdown(at(child, 300, 300)); root.handlers.click(at(child, 300, 300))
  root.handlers.pointerdown(at(child, 300, 300)); root.handlers.click(at(dialog, 50, 50))
  root.handlers.pointerdown({ ...at(dialog, 50, 50), button: 2 }); root.handlers.click(at(dialog, 50, 50))
  assert.equal(dialog.cancels, 0)
  dispose()
  assert.deepEqual(Object.keys(root.handlers), [])
})

test('dialogs marked keep ignore the backdrop', () => {
  const root = fakeRoot(), dialog = fakeDialog()
  dialog.dataset = { dismiss: 'keep' }
  installDialogDismiss(root)
  root.handlers.pointerdown(at(dialog, 50, 50)); root.handlers.click(at(dialog, 50, 50))
  assert.equal(dialog.cancels, 0); assert.equal(dialog.open, true)
})
