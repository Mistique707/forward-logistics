export const CLASS_ORDER = ['kerosene', 'rations', 'diesel', 'ammunition', 'medical']
export const MODES = {
  truck: { label: 'Truck convoy', short: 'TRUCK', color: 'var(--m-truck)' },
  heli: { label: 'Helicopter', short: 'HELI', color: 'var(--m-heli)' },
  airdrop: { label: 'Airdrop', short: 'AIRDROP', color: 'var(--m-airdrop)' },
}
export const TEMPO = { 1: 'Quiet', 2: 'Elevated', 3: 'High' }

export async function api(path, body) {
  const r = await fetch(`/api/${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path}: ${r.status}`)
  return r.json()
}

const date = (demo, day) => {
  const d = new Date(`${demo}T00:00:00`)
  d.setDate(d.getDate() + day)
  return d
}
export const dayLabel = (demo, day) =>
  date(demo, day).toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short' })
export const hourLabel = (demo, h) =>
  `${dayLabel(demo, Math.floor(h / 24))} ${String(h % 24).padStart(2, '0')}:00`
export const isoDay = (demo, day) => date(demo, day).toISOString().slice(0, 10)

export const num = (x, d = 0) => (x ?? 0).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d })
export const lakh = (x) => `₹${num(x, 1)} L`

// Stock status for one class: inside the plan window, below winter target, or fine
export function classStatus(c) {
  if (c.runout_day !== null && c.runout_day <= 14) return 'critical'
  if (c.shortfall > 0 && c.shortfall >= 0.03 * c.target) return 'warning'
  return 'good'
}
const RANK = { critical: 0, serious: 1, warning: 2, good: 3 }
export const worstStatus = (post) =>
  Object.values(post.classes).map(classStatus).sort((a, b) => RANK[a] - RANK[b])[0]
export const STATUS_LABEL = { critical: 'Runs out in window', warning: 'Below winter target', good: 'On target' }
export const riskStatus = (r) => (r >= 0.55 ? 'critical' : r >= 0.3 ? 'warning' : 'good')
