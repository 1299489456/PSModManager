// Small motion helpers for the greenhouse Mod garden. Every effect is skipped
// when the system asks for reduced motion; none of them changes state.
import { nextTick } from 'vue'

const EASE = 'cubic-bezier(.22,1,.36,1)'
export const reducedMotion = () => typeof window !== 'undefined' && Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

// FLIP: remember where each card was, let Vue re-render, then glide cards from the old spot.
export async function flip(container, mutate) {
  if (!container || reducedMotion() || typeof Element === 'undefined' || !Element.prototype.animate) { mutate(); return }
  const before = new Map([...container.querySelectorAll('[data-mod-id]')].map(element => [element.dataset.modId, element.getBoundingClientRect()]))
  mutate()
  await nextTick()
  for (const element of container.querySelectorAll('[data-mod-id]')) {
    const first = before.get(element.dataset.modId), last = element.getBoundingClientRect()
    if (!first) { element.animate([{ opacity: 0, transform: 'scale(.92)' }, { opacity: 1, transform: 'none' }], { duration: 380, easing: EASE }); continue }
    const dx = first.left - last.left, dy = first.top - last.top
    if (Math.abs(dx) + Math.abs(dy) > 1) element.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 460, easing: EASE })
  }
}

const LEAF = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z" fill="#bbf7d0"/><path d="M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12"/></svg>'

// A leaf flies along an arc from the toggled switch into the change basket.
export function flyLeaf(from, to, done) {
  if (!from || !to || reducedMotion() || !Element.prototype.animate) { done?.(); return }
  const a = from.getBoundingClientRect(), b = to.getBoundingClientRect()
  if (!b.width) { done?.(); return }
  const leaf = document.createElement('div')
  leaf.className = 'garden-flyleaf'; leaf.setAttribute('aria-hidden', 'true'); leaf.innerHTML = LEAF
  leaf.style.left = `${a.left + a.width / 2 - 11}px`; leaf.style.top = `${a.top + a.height / 2 - 11}px`
  document.body.appendChild(leaf)
  const dx = b.left + b.width / 2 - (a.left + a.width / 2), dy = b.top + b.height / 2 - (a.top + a.height / 2)
  const flight = leaf.animate([
    { transform: 'translate(0,0) rotate(0) scale(1)', opacity: 1 },
    { transform: `translate(${dx * .5}px, ${dy * .5 - 80}px) rotate(200deg) scale(1.2)`, opacity: 1 },
    { transform: `translate(${dx}px, ${dy}px) rotate(380deg) scale(.5)`, opacity: .3 },
  ], { duration: 700, easing: 'cubic-bezier(.45,.05,.4,1)' })
  flight.onfinish = flight.oncancel = () => { leaf.remove(); done?.() }
}
