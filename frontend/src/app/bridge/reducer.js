export const terminalStates = new Set(['succeeded', 'failed', 'cancelled'])
const states = new Set(['queued', 'running', 'cancelling', ...terminalStates])

// State belongs to exactly one task; a terminal result is immutable.
export function reduceTask(current, event, taskId) {
  if (!event || event.task_id !== taskId || typeof event.request_id !== 'string' || !event.request_id || !Number.isSafeInteger(event.sequence) || event.sequence < 1 || !states.has(event.state)) return current
  if (current && (current.task_id !== taskId || terminalStates.has(current.state) || event.sequence <= current.sequence || event.request_id !== current.request_id)) return current
  return { ...event }
}
