import { useState } from 'react'
import { TEMPO, lakh, num } from './util.js'

const ROWS = [
  ['posts_at_risk', 'Posts at risk', (x) => x, 'down'],
  ['earliest_runout', 'Earliest runout (days)', (x) => x ?? '—', 'up'],
  ['tonnes_planned', 'Planned lift (t)', (x) => num(x, 1), null],
  ['trucks', 'Truck lifts', (x) => x, null],
  ['heli_sorties', 'Helicopter sorties', (x) => x, null],
  ['airdrops', 'Airdrop sorties', (x) => x, null],
  ['cost_lakh', 'Plan cost', lakh, 'down'],
  ['air_liability_t', 'Winter air liability (t)', (x) => num(x, 1), 'down'],
  ['air_liability_lakh', 'Air liability cost', lakh, 'down'],
  ['unmet_t', 'Unmet before runout (t)', (x) => num(x, 1), 'down'],
  ['dispatch_by', 'First truck must leave by', (x) => x ?? '—', null],
]
const MODE = { truck: 'Truck', heli: 'Helicopter', airdrop: 'Airdrop', unmet: 'UNMET', 'air maintenance': 'Air maint.' }

export default function WhatIf({ presets, state, base, busy, onRun, net }) {
  const [f, setF] = useState({ shift: 0, surgePost: '', surgePct: 50, grounded: 0, cold: 0, coldDays: 10, sector: '', level: 3 })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'range' ? Number(e.target.value) : e.target.value })
  const posts = net.nodes.filter((n) => n.type === 'post')
  const sectors = [...new Set(posts.map((p) => p.sector))]
  const zo = base.passes[0]

  function custom() {
    const o = {}
    if (f.shift) o.pass_shift_days = f.shift
    if (f.surgePost && f.surgePct) o.troop_surge = { post: f.surgePost, pct: f.surgePct }
    if (f.grounded) o.heli_grounded_days = f.grounded
    if (f.cold) { o.temp_offset_c = f.cold; o.temp_days = f.coldDays }
    if (f.sector) o.tempo = { sector: f.sector, level: Number(f.level) }
    onRun(o)
  }

  const d = state.diff
  return (
    <div className="whatif">
      <div>
        <h4>Scenarios</h4>
        {presets.map((p) => (
          <button key={p.id} className={`preset ${state.preset === p.id ? 'on' : ''}`} onClick={() => onRun(p.overrides)} disabled={busy}>
            <b>{p.name}</b><span>{p.desc}</span>
          </button>
        ))}
      </div>
      <div>
        <h4>Build a scenario</h4>
        <div className="ctl">
          <label>Zoji La closure <span className="mono">{f.shift === 0 ? 'as forecast' : `${Math.abs(f.shift)} d ${f.shift < 0 ? 'early' : 'late'}`}
            {' '}(day {Math.max(0, (zo.close_day ?? 0) + f.shift)})</span></label>
          <input type="range" min={-10} max={5} value={f.shift} onChange={set('shift')} />
        </div>
        <div className="ctl">
          <label>Troop surge <span className="mono">{f.surgePost ? `+${f.surgePct}%` : 'none'}</span></label>
          <div className="row">
            <select value={f.surgePost} onChange={set('surgePost')}>
              <option value="">No surge</option>
              {posts.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          {f.surgePost && <input type="range" min={10} max={150} step={10} value={f.surgePct} onChange={set('surgePct')} />}
        </div>
        <div className="ctl">
          <label>Helicopters grounded <span className="mono">{f.grounded} d</span></label>
          <input type="range" min={0} max={7} value={f.grounded} onChange={set('grounded')} />
        </div>
        <div className="ctl">
          <label>Cold snap <span className="mono">{f.cold} °C for {f.coldDays} d</span></label>
          <input type="range" min={-15} max={0} value={f.cold} onChange={set('cold')} />
        </div>
        <div className="ctl">
          <label>Operational tempo</label>
          <div className="row">
            <select value={f.sector} onChange={set('sector')}>
              <option value="">Unchanged</option>
              {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={f.level} onChange={set('level')} disabled={!f.sector}>
              {[1, 2, 3].map((l) => <option key={l} value={l}>{TEMPO[l]}</option>)}
            </select>
          </div>
        </div>
        <button className="btn" onClick={custom} disabled={busy}>{busy ? 'Re-planning…' : 'Run scenario'}</button>
        <button className="btn ghost" onClick={() => onRun({})} disabled={busy}>Reset to baseline</button>
      </div>
      <div>
        {!d ? (
          <div className="empty">Pick a scenario. The forecast, runout dates and the OR-Tools dispatch plan
            recompute, and the map, alerts and plan switch to the scenario. The baseline vs scenario comparison appears here.</div>
        ) : (
          <>
            <h4>Baseline vs scenario</h4>
            <table className="cmp">
              <thead><tr><th>Measure</th><th>Baseline</th><th>Scenario</th></tr></thead>
              <tbody>
                {ROWS.map(([k, label, fmt, better]) => {
                  const { before, after } = d.kpis[k]
                  const changed = before !== after
                  const worse = changed && better && typeof after === 'number' && (better === 'down' ? after > before : after < before)
                  return (
                    <tr key={k}><td>{label}</td><td>{fmt(before)}</td>
                      <td className={changed && better ? (worse ? 'worse' : 'better') : ''}>{fmt(after)}</td></tr>
                  )
                })}
              </tbody>
            </table>
            <div className="changes">
              <div>
                <h4>Transport changes</h4>
                {d.mode_changes.length === 0 && <div className="muted">Same modes as baseline.</div>}
                {d.mode_changes.slice(0, 10).map((c, i) => (
                  <div className="change" key={i}><b>{c.post}</b> {c.cls}: {c.before.map((m) => MODE[m]).join(' + ') || 'nothing'}
                    <span className="arrow">→</span><b>{c.after.map((m) => MODE[m]).join(' + ') || 'nothing'}</b></div>
                ))}
                {d.mode_changes.length > 10 && <div className="muted">+{d.mode_changes.length - 10} more</div>}
              </div>
              <div>
                <h4>Days of stock changes</h4>
                {d.runout_changes.length === 0 && <div className="muted">No change in runout dates.</div>}
                {[...d.runout_changes].sort((a, b) => a.after - b.after).slice(0, 10).map((c, i) => (
                  <div className="change" key={i}><b>{c.post}</b> {c.cls}: {c.before >= 180 ? '180+' : c.before} d
                    <span className="arrow">→</span><b>{c.after >= 180 ? '180+' : c.after} d</b></div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
