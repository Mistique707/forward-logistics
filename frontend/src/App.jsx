import { useEffect, useState } from 'react'
import MapView from './MapView.jsx'
import PostPanel from './PostPanel.jsx'
import PlanView from './PlanView.jsx'
import WhatIf from './WhatIf.jsx'
import ModelView from './ModelView.jsx'
import { api, dayLabel, lakh, num } from './util.js'

export default function App() {
  const [net, setNet] = useState(null)
  const [presets, setPresets] = useState([])
  const [model, setModel] = useState(null)
  const [base, setBase] = useState(null)
  const [state, setState] = useState(null)
  const [scenario, setScenario] = useState({}) // overrides currently applied
  const [busy, setBusy] = useState(false)
  const [post, setPost] = useState(null)
  const [cls, setCls] = useState('kerosene')
  const [trip, setTrip] = useState(null)
  const [tab, setTab] = useState('plan')
  const [reports, setReports] = useState([])
  const [toast, setToast] = useState(null)

  useEffect(() => {
    Promise.all([api('network'), api('scenarios'), api('state?preset=baseline'), api('model')]).then(([n, p, s, m]) => {
      setNet(n); setPresets(p); setBase(s); setState(s); setModel(m)
    })
  }, [])

  // field reports (PWA today, sensors later) land in the stock-ingest API; refresh the picture when one arrives
  useEffect(() => {
    if (!base) return
    let last = null
    const poll = async () => {
      const r = await api('stock-reports?limit=6').catch(() => null)
      if (!r) return
      setReports(r)
      const top = r[0]?.id ?? 0
      if (last !== null && top !== last) {
        const b = await api('state?preset=baseline')
        setBase(b)
        setState(Object.keys(scenario).length ? await api('state', scenario) : b)
        const n = r[0]
        setToast(`Field report: ${n.post_id} ${n.cls} ${Math.round(n.quantity).toLocaleString('en-IN')} on hand · picture updated`)
        setTimeout(() => setToast(null), 6000)
      }
      last = top
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [base === null, scenario])

  async function run(overrides) {
    setBusy(true)
    try {
      const s = Object.keys(overrides).length ? await api('state', overrides) : base
      setScenario(overrides); setState(s); setTrip(null)
    } finally { setBusy(false) }
  }

  if (!state || !net) return <div className="empty" style={{ paddingTop: '40vh' }}>Building the supply picture…</div>

  const scenarioName = state.preset === 'baseline' ? null
    : presets.find((p) => p.id === state.preset)?.name || 'Custom scenario'
  const selectPost = (id, c) => { setPost(id); if (c) setCls(c) }

  return (
    <div className="app">
      <TopBar state={state} base={base} scenarioName={scenarioName} onReset={() => run({})} />
      <Alerts state={state} post={post} onSelect={selectPost} reports={reports} />
      <div className="map panel">
        <MapView net={net} state={state} post={post} trip={trip} onPost={selectPost} />
        {scenarioName && <div className="map-banner"><span className="tag scenario">What-if</span>{scenarioName}</div>}
        {busy && <div className="busy"><div>Re-forecasting · re-planning…</div></div>}
        {toast && <div className="toast">{toast}</div>}
      </div>
      <div className="side panel">
        <PostPanel state={state} scenario={scenario} post={post} cls={cls} onCls={setCls} onPost={setPost} classes={net.classes} />
      </div>
      <div className="bottom panel">
        <div className="tabs">
          {[['plan', 'Dispatch plan', state.plan.trips.length], ['whatif', 'What-if simulator'], ['model', 'Forecast model']].map(([id, label, n]) => (
            <button key={id} className={`tab ${tab === id ? 'on' : ''}`} onClick={() => setTab(id)}>
              {label}{n !== undefined && <span className="badge">{n}</span>}
            </button>
          ))}
          <div className="spacer" />
          <PlanSummary state={state} />
        </div>
        {tab === 'plan' && <PlanView state={state} trip={trip} onTrip={setTrip} classes={net.classes} />}
        {tab === 'whatif' && <WhatIf presets={presets} state={state} base={base} busy={busy} onRun={run} net={net} />}
        {tab === 'model' && <ModelView model={model} classes={net.classes} />}
      </div>
    </div>
  )
}

function Kpi({ k, v, unit, before, better = 'down', tone, fmt = (x) => x }) {
  const delta = before !== undefined && before !== null && v !== before && typeof v === 'number' ? v - before : null
  const worse = delta !== null && (better === 'down' ? delta > 0 : delta < 0)
  return (
    <div className={`kpi ${tone || ''}`}>
      <div className="k">{k}</div>
      <div className="v">{v === null || v === undefined ? '—' : fmt(v)}{unit && <small>{unit}</small>}
        {delta !== null && <span className={`d ${worse ? 'up' : 'down'}`}>{delta > 0 ? '▲' : '▼'}{fmt(Math.abs(delta))}</span>}
      </div>
    </div>
  )
}

function TopBar({ state, base, scenarioName, onReset }) {
  const k = state.kpis
  const b = scenarioName ? base.kpis : {}
  const zo = state.passes[0]
  return (
    <div className="top">
      <div className="brand">
        <div className="brand-mark">
          <svg width="20" height="20" viewBox="0 0 32 32"><path d="M3 26 13 7l5 9 3-4 8 14z" fill="none" stroke="#3987e5" strokeWidth="2.6" strokeLinejoin="round" /><circle cx="21" cy="12" r="2.6" fill="#fab219" /></svg>
        </div>
        <div>
          <h1>FORWARD LOGISTICS</h1>
          <p>Northern Sector · predictive supply picture</p>
        </div>
      </div>
      <div className="kpis">
        <Kpi k="Posts at risk" v={k.posts_at_risk} before={b.posts_at_risk} tone={k.posts_at_risk ? 'alarm' : ''} />
        <Kpi k="Zoji La" v={zo.close_day === 0 ? 'CLOSED' : zo.close_day == null ? 'OPEN' : `closes ${zo.close_day} d`}
          tone={zo.close_day !== null && zo.close_day < 14 ? 'warn' : ''} />
        <Kpi k="Earliest runout" v={k.earliest_runout} unit="d" before={b.earliest_runout} better="up" />
        <Kpi k="Planned lift" v={k.tonnes_planned} unit="t" before={b.tonnes_planned} fmt={(x) => num(x, 1)} />
        <Kpi k="Plan cost" v={k.cost_lakh} before={b.cost_lakh} fmt={lakh} />
        <Kpi k="Air liability" v={k.air_liability_t} unit="t" before={b.air_liability_t} fmt={(x) => num(x, 1)}
          tone={k.air_liability_t ? 'warn' : ''} />
        <Kpi k="Unmet" v={k.unmet_t} unit="t" before={b.unmet_t} fmt={(x) => num(x, 1)} tone={k.unmet_t ? 'alarm' : ''} />
      </div>
      {scenarioName && <span className="tag scenario" onClick={onReset} title="Back to baseline">Scenario <b>{scenarioName}</b> ✕</span>}
      <div className="clock">
        <div className="k">SIMULATED DATE · <span className="tag">synthetic data</span></div>
        <div className="v">{dayLabel(state.demo_date, 0)} 2025 · 06:00</div>
      </div>
    </div>
  )
}

const SEV = { critical: 'Critical', high: 'High', warning: 'Warning' }

function Alerts({ state, post, onSelect, reports }) {
  return (
    <div className="alerts panel">
      <div className="panel-head"><h2>Alerts</h2><span className="count">{state.alerts.length} ranked</span></div>
      <div className="scroll">
        {state.alerts.length === 0 && <div className="empty">All posts on target.</div>}
        {state.alerts.map((a, i) => (
          <button key={i} className={`alert ${a.severity} ${post === a.post ? 'sel' : ''}`} onClick={() => onSelect(a.post, a.cls)}>
            <div className={`sev sev-${a.severity}`}>
              <span className="sev-dot" style={{ background: 'currentColor' }} />{SEV[a.severity]}
              <span className="when">{a.cls ? (a.days === null ? '' : `${a.days} d`) : 'winter stock'}</span>
            </div>
            <div className="title">{a.title}</div>
            <div className="text">{a.text}</div>
          </button>
        ))}
        <div className="events">
          <h3>Field reports</h3>
          {reports.map((r) => (
            <div className="event" key={`r${r.id}`}><span className="mono">{r.observed_at.slice(5, 16).replace('T', ' ')}</span>
              {r.post_id} reports {r.cls} on hand <b className="mono">{Math.round(r.quantity).toLocaleString('en-IN')}</b>
              <span className="muted"> · {r.source}</span></div>
          ))}
          {state.events.map((e, i) => (
            <div className="event" key={i}><span className="mono">{e.date.slice(5)}</span>{e.text}</div>
          ))}
        </div>
      </div>
    </div>
  )
}

function PlanSummary({ state }) {
  const bm = state.plan.totals.by_mode
  return (
    <div className="summary">
      {['truck', 'heli', 'airdrop'].map((m) => bm[m] && (
        <span key={m}>{{ truck: 'Trucks', heli: 'Heli sorties', airdrop: 'Airdrops' }[m]} <b>{bm[m].trips}</b> · <b>{num(bm[m].kg / 1000, 1)} t</b></span>
      ))}
      {state.kpis.dispatch_by && <span>First dispatch by <b className="deadline">{state.kpis.dispatch_by}</b></span>}
    </div>
  )
}
