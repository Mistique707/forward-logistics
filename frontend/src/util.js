export const CLASS_ORDER = ['kerosene', 'rations', 'diesel', 'ammunition', 'medical']
export const MODES = {
  truck: { label: 'Truck convoy', short: 'Truck', color: 'var(--m-truck)', hex: '#2a78d6' },
  heli: { label: 'Helicopter', short: 'Helicopter', color: 'var(--m-heli)', hex: '#1baf7a' },
  airdrop: { label: 'Airdrop', short: 'Airdrop', color: 'var(--m-airdrop)', hex: '#eb6834' },
}
export const TEMPO = { 1: 'Quiet', 2: 'Elevated', 3: 'High' }
export const PLACE = { SAPPHIRE: 'Sapphire', ONYX: 'Onyx', KESTREL: 'Kestrel' }
export const PASS = { ZOJILA: 'Zoji La', KHARDUNG: 'Khardung La', CHANG: 'Chang La' }

// Write access key (only needed when the server sets FL_API_TOKEN): open the dashboard once with ?key=<token>
const KEY = (() => {
  try {
    const k = new URLSearchParams(location.search).get('key')
    if (k) { localStorage.setItem('fl.key', k); history.replaceState(null, '', location.pathname) }
    return localStorage.getItem('fl.key')
  } catch { return null }
})()

export async function api(path, body, method) {
  const auth = KEY ? { authorization: `Bearer ${KEY}` } : {}
  const r = await fetch(`/api/${path}`, body === undefined && !method ? {} : {
    method: method || 'POST', headers: { 'content-type': 'application/json', ...auth }, body: body === undefined ? undefined : JSON.stringify(body),
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
  date(demo, day).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
export const hourLabel = (demo, h) => `${dayLabel(demo, Math.floor(h / 24))}, ${String(h % 24).padStart(2, '0')}:00`
export const isoDay = (demo, day) => {
  const d = date(demo, day)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const num = (x, d = 0) => (x ?? 0).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d })
export const lakh = (x) => `₹${num(x, 1)} lakh`
export const tonnes = (kg) => (kg >= 1000 ? `${num(kg / 1000, 1)} t` : `${num(kg)} kg`)

// Stock status for one class: runs out inside the two-week window, below winter target, or fine
export function classStatus(c) {
  if (c.runout_day !== null && c.runout_day <= 14) return 'critical'
  if (c.shortfall > 0 && c.shortfall >= 0.03 * c.target) return 'warning'
  return 'good'
}
const RANK = { critical: 0, warning: 1, good: 2 }
export const worstStatus = (post) =>
  Object.values(post.classes).map(classStatus).sort((a, b) => RANK[a] - RANK[b])[0]
export const STATUS_WORD = { critical: 'Act now', warning: 'Top up', good: 'On target' }
export const riskStatus = (r) => (r >= 0.55 ? 'critical' : r >= 0.3 ? 'warning' : 'good')
export const daysText = (d) => (d >= 180 ? '180+ d' : `${d} d`)
export const ago = (s) => (s === null || s === undefined ? 'never' : s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`)
