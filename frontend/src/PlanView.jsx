import { useState } from 'react'
import MapView from './MapView.jsx'
import { CLASS_ORDER, MODES, PASS, PLACE, hourLabel, lakh, num, tonnes } from './util.js'

export default function PlanView({ state, net, onPost }) {
  const [focus, setFocus] = useState(null)
  const { trips, unmet, deferred, totals } = state.plan
  const demo = state.demo_date
  const classes = net.classes
  const byPost = deferred.reduce((a, d) => ({ ...a, [d.post]: (a[d.post] || 0) + d.kg }), {})
  return (
    <div className="plan">
      <div className="card side">
        <div className="card-h"><h2>What to send, and by when</h2>
          <span className="r">{trips.length} lift{trips.length === 1 ? '' : 's'} · {tonnes(trips.reduce((a, t) => a + t.load_kg, 0))} · {lakh(totals.cost / 1e5)}</span></div>
        <div className="scroll card-b">
          {unmet.length > 0 && <div className="callout crit"><b>Cannot reach in time:</b> {unmet.map((u) => `${u.post} ${u.cls} (${tonnes(u.kg)})`).join(', ')}.
            No transport can land it before it runs out. Escalate for extra airlift.</div>}
          {deferred.length > 0 && <div className="callout warn"><b>{tonnes(totals.deferred_kg)} must be flown in later</b> (about {lakh(totals.deferred_cost / 1e5)} by helicopter):
            winter stock that can no longer go by road. {Object.entries(byPost).map(([p, kg]) => `${p} ${tonnes(kg)}`).join(', ')}.</div>}
          {trips.length === 0 && <div className="empty">Nothing needs to move. Every post is stocked to its target.</div>}
          {trips.map((t) => (
            <button key={t.vehicle} className={`lift ${focus === t.vehicle ? 'sel' : ''}`} onClick={() => setFocus(focus === t.vehicle ? null : t.vehicle)}>
              <div className="h">
                <span className="mode"><i style={{ background: MODES[t.mode].color }} />{MODES[t.mode].short}</span>
                <b>{t.vehicle}</b>
                <span className="cost">{lakh(t.cost / 1e5)}</span>
              </div>
              <div className="route">
                {PLACE[t.origin]}{t.passes.map((p) => <span key={p}> → {PASS[p]}</span>)}
                {t.stops.map((s) => <span key={s.post}> → <b>{s.post[0] + s.post.slice(1).toLowerCase()}</b>{s.mule && ' (then mule)'}</span>)}
              </div>
              <div className="grid">
                <div><div className="k">Must leave by</div><div className="deadline">{hourLabel(demo, t.depart_by_h)}</div></div>
                <div><div className="k">Arrives</div><div>{t.stops.map((s) => hourLabel(demo, s.arrive_h)).join(', ')}</div></div>
                <div style={{ gridColumn: '1 / -1' }}><div className="k">Carries</div>
                  {t.stops.map((s) => (
                    <div key={s.post}>{t.stops.length > 1 && <span className="muted">{s.post[0] + s.post.slice(1).toLowerCase()}: </span>}
                      {CLASS_ORDER.filter((k) => s.items[k]).map((k) => `${num(s.items[k])} ${classes[k].unit} ${classes[k].label.toLowerCase()}`).join(', ')}</div>
                  ))}
                </div>
                <div style={{ gridColumn: '1 / -1' }}><div className="k">Load {num(t.load_kg)} of {num(t.capacity_kg)} kg · {num(t.km)} km</div>
                  <div className="loadbar"><i style={{ width: `${(t.load_kg / t.capacity_kg) * 100}%` }} /></div></div>
              </div>
            </button>
          ))}
          {trips.length > 0 && <div className="note">Planned with Google OR-Tools across trucks (with mule legs), helicopters and airdrops,
            respecting payloads, pass closures and flying weather ({state.plan.nodes} decision points).
            Click a lift to see its route. Click a post on the map to open it.</div>}
        </div>
      </div>
      <div className="map-card card">
        <MapView net={net} state={state} post={null} onPost={onPost} trips={trips} focus={focus} />
      </div>
    </div>
  )
}
