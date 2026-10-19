// Playwright browser_run_code input. Serve a copy of
// tests/desktop/fixtures/mod-workspace-preview.html from the frontend Vite root.
async (page) => {
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.reload()
  const library = page.locator('[data-testid="mod-library-list"]')
  const enabled = page.locator('[data-testid="mod-enabled-list"]')
  await library.locator('article').first().waitFor()
  async function drag(source, target) {
    await source.scrollIntoViewIfNeeded()
    const a = await source.boundingBox(), b = await target.boundingBox()
    await page.mouse.move(a.x + 80, a.y + a.height / 2)
    await page.mouse.down()
    await page.mouse.move(b.x + b.width / 2, b.y + 35, { steps: 15 })
    await page.mouse.up()
  }
  async function count(expected, label) {
    if (await enabled.locator('article').count() !== expected) throw Error(label)
  }
  await drag(library.locator('[data-mod-id="mod-8"]'), enabled)
  await count(9, 'Drag to enable failed')
  if (await page.evaluate(() => fixtureCalls.some(x => x.command === 'mod.workspace.save'))) throw Error('Unexpected write before Save')
  await drag(enabled.locator('[data-mod-id="mod-8"]'), library)
  await count(8, 'Drag back failed')
  await enabled.locator('[data-mod-id="mod-0"]').click()
  await enabled.locator('[data-mod-id="mod-1"]').click({ modifiers: ['Control'] })
  if (await enabled.locator('[aria-selected="true"]').count() !== 2) throw Error('Multi-selection failed')
  await drag(enabled.locator('[data-mod-id="mod-0"]'), library)
  await count(6, 'Multi-drag failed')
  await page.getByRole('button', { name: '撤销', exact: true }).click()
  await count(8, 'Undo failed')
  await enabled.locator('[data-mod-id="mod-0"]').focus()
  await page.keyboard.press('Alt+ArrowDown')
  const order = await enabled.locator('article').evaluateAll(rows => rows.map(row => row.dataset.modId))
  if (order[1] !== 'mod-0') throw Error('Keyboard reorder failed')
  await page.keyboard.press('Control+s')
  if (await page.locator('[data-testid="workspace-save"]').isEnabled()) throw Error('Save did not settle')
  return { status: 'PASS', order, calls: await page.evaluate(() => fixtureCalls.filter(row => row.command === 'mod.workspace.save')) }
}
