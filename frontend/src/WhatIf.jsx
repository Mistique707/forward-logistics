import { useState } from 'react'
import { TEMPO, lakh, num } from './util.js'

const ROWS = [
  ['posts_at_risk', 'Posts that need action', (x) => x, 'down'],
  ['earliest_runout', 'First runout without action', (x) => (x === null ? 'none' : `${x} days`), 'up'],
  ['tonnes_planned', 'To send now', (x) => `${num(x, 1)} t`, null],
  ['trucks', 'Truck lifts', (x) => x, null],
  ['heli_sorties', 'Helicopter sorties', (x) => x, null],
  ['airdrops', 'Airdrops', (x) => x, null],
  ['cost_lakh', 'Cost of this plan', lakh, 'down'],
  ['air_liability_lakh', 'Cost to fly stock in later', lakh, 'down'],
  ['unmet_t', 'Cannot reach in time', (x) => `${num(x, 1)} t`, 'down'],
  ['dispatch_by', 'First truck must leave by', (x) => x ?? 'no trucks', null],
]
const MODE = { truck: 'truck', heli: 'helicopter', airdrop: 'airdrop', unmet: 'cannot reach', 'air maintenance': 'fly in later' }

export default function WhatIf({ presets, state, base, busy, onRun, net }) {
  const [f, setF] = useState({ shift: 0, reopen: 0, surgePost: '', surgePct: 50, grounded: 0, cold: 0, coldDays: 10, sector: '', level: 3 })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'range' ? Number(e.target.value) : e.target.value })
  const posts = net.nodes.filter((n) => n.type === 'post')
  const sectors = [...new Set(posts.map((p) => p.sector))]
  const zo = base.passes[0]
  const d = state.diff

  function custom() {
    const o = {}
    if (f.shift) o.pass_shift_days = f.shift
    if (f.reopen) o.reopen_shift_days = f.reopen
    if (f.surgePost && f.surgePct) o.troop_surge = { post: f.surgePost, pct: f.surgePct }
    if (f.grounded) o.heli_grounded_days = f.grounded
    if (f.cold) { o.temp_offset_c = f.cold; o.temp_days = f.coldDays }
    if (f.sector) o.tempo = { sector: f.sector, level: Number(f.level) }
    onRun(o)
  }

  return (
    <div className="whatif">
      <div className="card side">
        <div className="card-h"><h2>Try a scenario</h2></div>
        <div className="scroll card-b">
          {presets.map((p) => (
            <button key={p.id} className={`preset ${state.preset === p.id ? 'on' : ''} ${p.id === 'what_happened' ? 'real' : ''}`}
              onClick={() => onRun(p.overrides)} disabled={busy}>
              <b>{p.id === 'baseline' ? 'Today, as forecast' : p.name}</b><span>{p.desc}</span>
            </button>
          ))}
          <details className="builder">
            <summary>Build your own</summary>
            <div className="ctl"><label>Zoji La shuts <span className="num">{f.shift === 0 ? 'as forecast' : `${Math.abs(f.shift)} d ${f.shift < 0 ? 'earlier' : 'later'}`} (day {Math.max(0, (zo.close_day ?? 0) + f.shift)})</span></label>
              <input type="range" min={-10} max={8} value={f.shift} onChange={set('shift')} aria-label="Closure shift in days" /></div>
            <div className="ctl"><label>Zoji La reopens <span className="num">{f.reopen === 0 ? 'as forecast' : `${Math.abs(f.reopen)} d ${f.reopen < 0 ? 'earlier' : 'later'}`}</span></label>
              <input type="range" min={-10} max={30} value={f.reopen} onChange={set('reopen')} aria-label="Reopening shift in days" /></div>
            <div className="ctl"><label>Troop surge <span className="num">{f.surgePost ? `+${f.surgePct}%` : 'none'}</span></label>
              <div className="row"><select value={f.surgePost} onChange={set('surgePost')} aria-label="Post with troop surge">
                <option value="">No surge</option>{posts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
              {f.surgePost && <input type="range" min={10} max={150} step={10} value={f.surgePct} onChange={set('surgePct')} aria-label="Surge percent" />}</div>
            <div className="ctl"><label>Helicopters grounded <span className="num">{f.grounded} days</span></label>
              <input type="range" min={0} max={7} value={f.grounded} onChange={set('grounded')} aria-label="Days grounded" /></div>
            <div className="ctl"><label>Cold snap <span className="num">{f.cold} °C for {f.coldDays} days</span></label>
              <input type="range" min={-15} max={0} value={f.cold} onChange={set('cold')} aria-label="Temperature change" /></div>
            <div className="ctl"><label>Operational tempo</label>
              <div className="row">
                <select value={f.sector} onChange={set('sector')} aria-label="Sector"><option value="">Unchanged</option>{sectors.map((s) => <option key={s}>{s}</option>)}</select>
                <select value={f.level} onChange={set('level')} disabled={!f.sector} aria-label="Tempo level">{[1, 2, 3].map((l) => <option key={l} value={l}>{TEMPO[l]}</option>)}</select>
              </div></div>
            <button className="btn" onClick={custom} disabled={busy}>{busy ? 'Re-planning…' : 'Run this scenario'}</button>
          </details>
        </div>
      </div>
      <div className="card side">
        <div className="card-h"><h2>{d ? 'How it changes the picture' : 'Pick a scenario'}</h2>
          {d && <span className="r">compared with today's forecast</span>}</div>
        <div className="scroll card-b">
          {!d ? <div className="empty">Each scenario re-runs the demand forecast, the runout dates and the OR-Tools dispatch plan.
            The overview, alerts and plan all switch to the scenario until you go back to today.</div> : (
            <>
              <div className="cmp">
                {ROWS.map(([k, label, fmt, better]) => {
                  const { before, after } = d.kpis[k]
                  const changed = before !== after
                  const worse = changed && better && typeof after === 'number' && (better === 'down' ? after > before : after < before)
                  return (
                    <div key={k} className={`c ${changed && better ? (worse ? 'worse' : 'better') : ''}`}>
                      <div className="k">{label}</div>
                      <div className="v">{fmt(after)}{changed && <s>was {fmt(before)}</s>}</div>
                    </div>
                  )
                })}
              </div>
              <div className="changes">
                <div>
                  <div className="section"><h4>How supplies now travel</h4></div>
                  {d.mode_changes.length === 0 && <div className="muted">Same transport as today's plan.</div>}
                  {d.mode_changes.slice(0, 12).map((c, i) => (
                    <div className="change" key={i}><b>{c.post[0] + c.post.slice(1).toLowerCase()} {c.cls}</b>: {c.before.map((m) => MODE[m]).join(' + ') || 'nothing'} → <b>{c.after.map((m) => MODE[m]).join(' + ') || 'nothing'}</b></div>
                  ))}
                </div>
                <div>
                  <div className="section"><h4>Days of stock that changed</h4></div>
                  {d.runout_changes.length === 0 && <div className="muted">No change in when stocks run out.</div>}
                  {[...d.runout_changes].sort((a, b) => a.after - b.after).slice(0, 12).map((c, i) => (
                    <div className="change" key={i}><b>{c.post[0] + c.post.slice(1).toLowerCase()} {c.cls}</b>: {c.before >= 180 ? '180+' : c.before} → <b>{c.after >= 180 ? '180+' : c.after} days</b></div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
