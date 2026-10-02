import { CLASS_ORDER, MODES, hourLabel, lakh, num } from './util.js'

const PASS = { ZOJILA: 'Zoji La', KHARDUNG: 'Khardung La', CHANG: 'Chang La' }
const NAME = { SAPPHIRE: 'Sapphire', ONYX: 'Onyx' }

export default function PlanView({ state, trip, onTrip, classes }) {
  const { trips, unmet, deferred, totals } = state.plan
  const demo = state.demo_date
  const items = (s) => CLASS_ORDER.filter((k) => s.items[k]).map((k) => (
    <span key={k}><b>{num(s.items[k])}</b> {classes[k].unit === 'kg' ? 'kg' : 'L'} {classes[k].label.toLowerCase()} </span>
  ))
  return (
    <div className="scroll">
      {unmet.length > 0 && (
        <div className="callout crit"><b>Unmet before runout:</b> {unmet.map((u) => `${u.post} ${u.cls} ${num(u.kg)} kg`).join(' · ')}. No transport mode can land it in time: escalate for additional lift.</div>
      )}
      {deferred.length > 0 && (
        <div className="callout warn"><b>Winter air-maintenance liability: {num(totals.deferred_kg / 1000, 1)} t (≈ {lakh(totals.deferred_cost / 1e5)} by helicopter).</b>{' '}
          Stocking that can no longer move by road: {Object.entries(deferred.reduce((a, d) => ({ ...a, [d.post]: (a[d.post] || 0) + d.kg }), {}))
            .map(([p, kg]) => `${p} ${num(kg / 1000, 1)} t`).join(' · ')}.</div>
      )}
      {trips.length === 0 ? <div className="empty">No lifts planned.</div> : (
        <table className="grid">
          <thead><tr>
            <th>Lift</th><th>Route</th><th>Load</th><th>Contents</th><th>Depart</th><th>Latest dispatch</th><th>Arrives</th><th className="r">Cost</th>
          </tr></thead>
          <tbody>
            {trips.map((t) => (
              <tr key={t.vehicle} className={`click ${trip === t.vehicle ? 'sel' : ''}`} onClick={() => onTrip(trip === t.vehicle ? null : t.vehicle)}>
                <td><span className="mode-chip"><i style={{ background: MODES[t.mode].color }} />{MODES[t.mode].short}</span><br /><span className="mono">{t.vehicle}</span></td>
                <td>{NAME[t.origin]}{t.passes.map((p) => <span key={p}> → <span className="ink2">{PASS[p]}</span></span>)}
                  {t.stops.map((s) => <span key={s.post}> → <b>{s.post}</b>{s.mule && <span className="muted"> +mule</span>}</span>)}
                  <div className="muted mono">{num(t.km)} km</div></td>
                <td className="mono"><span className="loadbar"><i style={{ width: `${(t.load_kg / t.capacity_kg) * 100}%` }} /></span>
                  {Math.round((t.load_kg / t.capacity_kg) * 100)}%<div className="muted">{num(t.load_kg)} / {num(t.capacity_kg)} kg</div></td>
                <td className="items">{t.stops.map((s) => <div key={s.post}>{t.stops.length > 1 && <span className="muted">{s.post}: </span>}{items(s)}</div>)}</td>
                <td className="mono">{hourLabel(demo, t.depart_h)}</td>
                <td><span className="deadline">{hourLabel(demo, t.depart_by_h)}</span></td>
                <td className="mono">{t.stops.map((s) => <div key={s.post}>{hourLabel(demo, s.arrive_h)}</div>)}</td>
                <td className="r mono">{lakh(t.cost / 1e5)}</td>
              </tr>
            ))}
            <tr>
              <td colSpan={7} className="muted">{trips.length} lifts · {num(trips.reduce((a, t) => a + t.load_kg, 0) / 1000, 1)} t · solved with OR-Tools ({state.plan.nodes} nodes)</td>
              <td className="r mono"><b>{lakh(totals.cost / 1e5)}</b></td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  )
}
