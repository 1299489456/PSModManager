import test from 'node:test'
import assert from 'node:assert/strict'
import { themeMenuIndex } from './themeMenu.js'
test('theme menu arrow keys wrap and Home End reach first last choice', () => {
  assert.equal(themeMenuIndex(0,'ArrowUp',3),2)
  assert.equal(themeMenuIndex(2,'ArrowDown',3),0)
  assert.equal(themeMenuIndex(1,'ArrowDown',3),2)
  assert.equal(themeMenuIndex(1,'ArrowUp',3),0)
  assert.equal(themeMenuIndex(2,'Home',3),0)
  assert.equal(themeMenuIndex(0,'End',3),2)
})
test('other keys preserve focus; zero choices and stale indices remain bounded', () => {
  assert.equal(themeMenuIndex(1,'Escape',3),1)
  assert.equal(themeMenuIndex(20,'Tab',3),2)
  assert.equal(themeMenuIndex(-5,'Enter',3),0)
  assert.equal(themeMenuIndex(0,'ArrowDown',0),0)
})
