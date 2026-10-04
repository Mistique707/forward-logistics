import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, num } from './util.js'

const fmt = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })

export default function DataView() {
  const [d, setD] = useState(null)
  useEffect(() => { api('data').then(setD) }, [])
  if (!d) return <div className="empty">Loading…</div>
  const closeMae = d.closures.reduce((a, r) => a + Math.abs(r.close_err_days), 0) / d.closures.length
  const openMae = d.closures.reduce((a, r) => a + Math.abs(r.reopen_err_days), 0) / d.closures.length
  const pc = [...d.per_capita].sort((a, b) => b.kg_per_person.all - a.kg_per_person.all)
  const india = pc.find((x) => x.state === 'All India Total')
  const ladakh = pc.find((x) => x.state === 'Ladakh')
  return (
    <div className="page">
      <div className="stack">
        <div className="card">
          <div className="card-h"><h2>Zoji La: what really happened, and what our rule predicts</h2>
            <span className="r">real dates from BRO reports in the press</span></div>
          <div className="card-b">
            <div className="two">
              <div>
                <table className="t">
                  <thead><tr><th>Winter</th><th>Really closed</th><th>Rule</th><th>Really reopened</th><th>Rule</th></tr></thead>
                  <tbody>{d.closures.map((r) => (
                    <tr key={r.winter}><td><a href={r.source} target="_blank" rel="noreferrer">{r.winter}</a></td>
                      <td>{fmt(r.real_close)}</td><td className="num small">{r.close_err_days > 0 ? '+' : ''}{r.close_err_days} d</td>
                      <td>{fmt(r.real_reopen)}</td><td className="num small">{r.reopen_err_days > 0 ? '+' : ''}{r.reopen_err_days} d</td></tr>))}</tbody>
                </table>
              </div>
              <div>
                <div style={{ display: 'flex', gap: 24 }}>
                  <div><div className="big">{num(closeMae, 1)} days</div><div className="muted">average closing-date error</div></div>
                  <div><div className="big">{num(openMae, 1)} days</div><div className="muted">average reopening-date error</div></div>
                </div>
                <p>Since 2020 the Border Roads Organisation has kept Zoji La open through December. The pass now shuts at the first
                  heavy snow from 31 December onwards. Our rule, fitted to these five winters on real ERA5 weather, says it shuts when three-day
                  snowfall at the pass reaches 20 cm, and reopens once the 14-day mean temperature climbs past −7 °C, after at least three weeks.</p>
                <p className="muted">The demo date, 20 Feb 2025, is six days before the closure the rule forecasts for the western disturbance of
                  late February 2025. In reality the pass shut on 28 Feb and reopened on 1 Apr. The "What really happened" scenario replays that.
                  In 2025-26 the BRO held the pass open through heavier January snow than the rule allows, so the threshold is a policy setting worth revisiting each year.</p>
              </div>
            </div>
          </div>
        </div>

        <div className="two">
          <div className="card">
            <div className="card-h"><h2>Ladakh fuel sales, real</h2><span className="r">PPAC, thousand tonnes a year</span></div>
            <div className="card-b">
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={d.ladakh_trend} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="#edf0f3" vertical={false} />
                  <XAxis dataKey="fiscal_year" stroke="#cfd4db" tick={{ fill: '#6a717d', fontSize: 11 }} />
                  <YAxis stroke="#cfd4db" tick={{ fill: '#6a717d', fontSize: 11 }} width={40} />
                  <Tooltip formatter={(v, k) => [`${num(v, 1)} kt`, { all: 'All products', diesel: 'Diesel', petrol: 'Petrol' }[k]]} />
                  <Line dataKey="all" stroke="#2a78d6" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                  <Line dataKey="diesel" stroke="#eb6834" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                  <Line dataKey="petrol" stroke="#1baf7a" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
              <div className="legend-row"><span><i className="sw" style={{ borderColor: '#2a78d6' }} />All products</span>
                <span><i className="sw" style={{ borderColor: '#eb6834' }} />Diesel</span><span><i className="sw" style={{ borderColor: '#1baf7a' }} />Petrol</span></div>
              <div className="source">Source: <a href="https://ppac.gov.in/consumption/state-wise" target="_blank" rel="noreferrer">Petroleum Planning & Analysis Cell</a>,
                state/UT-wise sales, Ministry of Petroleum & Natural Gas. Ladakh is reported separately from 2019-20.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h2>Fuel per person, by region</h2><span className="r">{d.ppac_year}, kg per person (Census 2011 population)</span></div>
            <div className="card-b">
              {pc.map((x) => (
                <div className="hbar" key={x.state}><span>{x.state.replace(' Total', '')}</span>
                  <span className="b"><i style={{ width: `${(x.kg_per_person.all / pc[0].kg_per_person.all) * 100}%`, background: x.state === 'Ladakh' ? 'var(--critical)' : undefined }} /></span>
                  <span className="num">{num(x.kg_per_person.all)}</span></div>
              ))}
              <p>Ladakh burns about <b>{num(ladakh.kg_per_person.all / india.kg_per_person.all, 1)}×</b> the all-India petroleum per person:
                cold, altitude and remoteness. That regional signal is why the model is built on heating demand and altitude, and why each site gets its own learned correction.</p>
            </div>
          </div>
        </div>

        <div className="two">
          <div className="card">
            <div className="card-h"><h2>Real inputs</h2></div>
            <div className="card-b">
              <table className="t"><tbody>
                <tr><td><b>Weather</b></td><td>Daily temperature, snowfall, gusts and cloud at every post and pass, Oct 2020 to Apr 2026 (Open-Meteo, ERA5 reanalysis)</td></tr>
                <tr><td><b>Roads and passes</b></td><td>About 4,000 OpenStreetMap road segments, routed with real distances and climbs (Copernicus 90 m elevation)</td></tr>
                <tr><td><b>Pass closures</b></td><td>Five winters of real Zoji La closures and reopenings (table above)</td></tr>
                <tr><td><b>Rations</b></td><td>Authorised high-altitude ration: {num(d.ration.kcal)} kcal per man per day, about {d.ration.kg_per_day} kg ({d.ration.source})</td></tr>
                <tr><td><b>Regional fuel</b></td><td>PPAC fuel sales for Ladakh and neighbouring states (charts above)</td></tr>
              </tbody></table>
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h2>Still simulated, and how that changes</h2></div>
            <div className="card-b">
              <p style={{ marginTop: 0 }}>No public data exists on how much a post consumes each day, so post-level usage, troop strength, tempo and stock levels
                are simulated from the real inputs on the left. Each post also has hidden local habits (old heaters, generator wear) that only its own records reveal.
                The avalanche at Post Alpha and Foxtrot's diesel shortfall are scripted for the demo.</p>
              <p>Post and depot names are fictional, and their locations are generic high-altitude terrain points. Nothing represents a real deployment.</p>
              <p className="muted">Every inventory check recorded in the field app is real usage data: it adjusts that site's forecast immediately and retrains the shared model,
                so the simulated history is replaced as real records arrive.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
