import { useEffect, useState } from 'react'
import Overview from './Overview.jsx'
import PlanView from './PlanView.jsx'
import WhatIf from './WhatIf.jsx'
import Learning from './Learning.jsx'
import DataView from './DataView.jsx'
import { api, dayLabel, lakh, num } from './util.js'

const VIEWS = [['overview', 'Overview'], ['plan', 'Dispatch plan'], ['whatif', 'What-if'], ['learning', 'Forecast & learning'],
  ['data', 'Data sources']]

export default function App() {
  const [net, setNet] = useState(null)
  const [presets, setPresets] = useState([])
  const [base, setBase] = useState(null)
  const [state, setState] = useState(null)
  const [scenario, setScenario] = useState({})
  const [busy, setBusy] = useState(false)
  const [view, setView] = useState('overview')
  const [post, setPost] = useState(null)
  const [cls, setCls] = useState('kerosene')
  const [toast, setToast] = useState(null)

  useEffect(() => {
    Promise.all([api('network'), api('scenarios'), api('state?preset=baseline')]).then(([n, p, s]) => {
      setNet(n); setPresets(p); setBase(s); setState(s)
    })
  }, [])

  // inventory checks (field app today, sensors later) refresh the picture as they arrive
  useEffect(() => {
    if (!base) return
    let last = null
    const poll = async () => {
      const r = await api('inventory?limit=1').catch(() => null)
      if (!r) return
      const top = r[0]?.id ?? 0
      if (last !== null && top !== last) {
        const b = await api('state?preset=baseline')
        setBase(b)
        setState(Object.keys(scenario).length ? await api('state', scenario) : b)
        const e = r[0]
        setToast(`Inventory check received: ${e.site_id} ${e.cls} ${e.kind} ${num(e.quantity)}. The picture is updated.`)
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
      setScenario(overrides); setState(s)
    } finally { setBusy(false) }
  }

  if (!state || !net) return <div className="empty" style={{ paddingTop: '40vh' }}>Building the supply picture…</div>
  const scenarioName = state.preset === 'baseline' ? null : presets.find((p) => p.id === state.preset)?.name || 'Custom scenario'
  const openPost = (id, c) => { setPost(id); if (c) setCls(c); setView('overview') }

  return (
    <div className="shell">
      <header className="top">
        <div className="brand">
          <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#1f5fbf" />
            <path d="M5 24 13 9l5 8 3-4 6 11z" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinejoin="round" /><circle cx="21" cy="12" r="2.4" fill="#fbd38d" /></svg>
          <div><h1>Forward Logistics</h1><p>Ladakh sector · winter supply picture</p></div>
        </div>
        {scenarioName && <span className="chip">What-if: {scenarioName}<button onClick={() => run({})}>Back to today</button></span>}
        <div className="date"><b>{dayLabel(state.demo_date, 0)} 2025</b><span>simulated date · synthetic post data on real terrain and weather</span></div>
        <a className="linkbtn" href="/field/" target="_blank" rel="noreferrer">Open inventory check ↗</a>
      </header>
      <nav className="nav" aria-label="Views">
        {VIEWS.map(([id, label]) => <button key={id} className={view === id ? 'on' : ''} onClick={() => setView(id)}>{label}</button>)}
      </nav>
      <Kpis state={state} base={scenarioName ? base : null} />
      <main className="view">
        {view === 'overview' && <Overview state={state} net={net} scenario={scenario} post={post} cls={cls} onPost={setPost} onCls={setCls}
          busy={busy} scenarioName={scenarioName} />}
        {view === 'plan' && <PlanView state={state} net={net} onPost={openPost} />}
        {view === 'whatif' && <WhatIf presets={presets} state={state} base={base} busy={busy} onRun={run} net={net} />}
        {view === 'learning' && <Learning onChange={() => run(scenario)} />}
        {view === 'data' && <DataView />}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}

function Delta({ v, b, better = 'down', fmt = (x) => x }) {
  if (b === null || b === undefined || v === b || typeof v !== 'number') return null
  const tone = better === 'none' ? '' : (better === 'down' ? v > b : v < b) ? 'worse' : 'better'
  return <span className={`delta ${tone}`}>{v > b ? '▲' : '▼'} {fmt(Math.abs(v - b))}</span>
}

function Kpis({ state, base }) {
  const k = state.kpis
  const b = base?.kpis || {}
  const zo = state.passes[0]
  const demo = state.demo_date
  const lifts = [k.trucks && `${k.trucks} truck${k.trucks > 1 ? 's' : ''}`, k.heli_sorties && `${k.heli_sorties} helicopter sortie${k.heli_sorties > 1 ? 's' : ''}`,
    k.airdrops && `${k.airdrops} airdrop${k.airdrops > 1 ? 's' : ''}`].filter(Boolean).join(' · ') || 'nothing to send'
  return (
    <section className="kpis" aria-label="Summary">
      <div className={`kpi ${k.posts_at_risk ? 'bad' : ''}`}>
        <div className="k">Posts that need action</div>
        <div className="v">{k.posts_at_risk}<small>of 8</small><Delta v={k.posts_at_risk} b={b.posts_at_risk} /></div>
        <div className="s">{k.earliest_runout !== null ? `first runout in ${k.earliest_runout} days without action` : 'no runout in sight'}</div>
      </div>
      <div className={`kpi ${zo.close_day !== null && zo.close_day < 14 ? 'warn' : ''}`}>
        <div className="k">Zoji La (the only road in)</div>
        <div className="v">{zo.close_day === 0 ? 'Closed' : zo.close_day === null ? 'Open' : `closes in ${zo.close_day} d`}</div>
        <div className="s">{zo.close_day ? `${dayLabel(demo, zo.close_day)} · ` : ''}{zo.reopen_day !== null ? `reopens about ${dayLabel(demo, zo.reopen_day)}` : 'no closure forecast'}</div>
      </div>
      <div className="kpi">
        <div className="k">To send now</div>
        <div className="v">{num(k.tonnes_planned, 1)}<small>t</small><Delta v={k.tonnes_planned} b={b.tonnes_planned} better="none" fmt={(x) => num(x, 1)} /></div>
        <div className="s">{lifts}{k.dispatch_by ? ` · first leaves by ${k.dispatch_by}` : ''}</div>
      </div>
      <div className={`kpi ${k.unmet_t ? 'bad' : ''}`}>
        <div className="k">Cost of this plan</div>
        <div className="v">{lakh(k.cost_lakh)}<Delta v={k.cost_lakh} b={b.cost_lakh} fmt={(x) => num(x, 1)} /></div>
        <div className="s">{k.unmet_t ? `${num(k.unmet_t, 1)} t cannot reach in time` : k.air_liability_t ? `+ ${lakh(k.air_liability_lakh)} to fly in ${num(k.air_liability_t, 1)} t later` : 'nothing left for winter airlift'}</div>
      </div>
    </section>
  )
}
