import { useEffect, useState } from 'react'
import { CLASS_ORDER, api, num } from './util.js'

const LABEL = { rations: 'Rations', kerosene: 'Kerosene', diesel: 'Diesel', ammunition: 'Ammunition', medical: 'Medical' }

export default function Learning({ onChange }) {
  const [model, setModel] = useState(null)
  const [learn, setLearn] = useState(null)
  const [mode, setMode] = useState('archived')
  const [cls, setCls] = useState('kerosene')
  const [msg, setMsg] = useState(null)

  const load = () => Promise.all([api('model'), api('learning')]).then(([m, l]) => { setModel(m); setLearn(l); return l })
  useEffect(() => { load() }, [])
  useEffect(() => {  // follow a retraining run until it finishes
    if (!learn?.running) return
    const id = setInterval(async () => {
      const l = await load()
      if (!l.running) { clearInterval(id); onChange() }
    }, 2000)
    return () => clearInterval(id)
  }, [learn?.running])

  async function retrain() {
    setMsg(null)
    try {
      await api('learning/retrain', undefined, 'POST')
      setLearn({ ...learn, running: true })
    } catch { setMsg('A retraining run is already in progress.') }
  }

  if (!model || !learn) return <div className="empty">Loading…</div>
  const m = model[mode]
  const ev = learn.evaluation?.wape
  const last = learn.last_run
  const active = learn.versions.find((v) => v.version === learn.active)
  return (
    <div className="page">
      <div className="stack">
        <div className="card">
          <div className="card-h"><h2>How the forecast keeps learning</h2>
            <span className="r">active model v{learn.active}, trained {active?.trained_at.replace('T', ' ')}</span></div>
          <div className="card-b">
            <div className="steps">
              <div className="step"><b>1 · Inventory check</b><span>Each post (or any site) records stock counts, receipts and issues in the field app, even offline.</span></div>
              <div className="step"><b>2 · Actual usage</b><span>Counts are turned into daily usage: last count + receipts − issues − this count, spread over the days in between.</span></div>
              <div className="step"><b>3 · Local correction, instantly</b><span>Each site gets its own factor: its real use against the shared forecast, trusted more as its records pile up (half weight after {learn.prior_days} days).</span></div>
              <div className="step"><b>4 · Shared model retrains</b><span>After {learn.auto_retrain_after} new site-days, a challenger is trained. It replaces the current model only if it forecasts the newest records at least as well.</span></div>
            </div>
            <div className="two" style={{ marginTop: 14 }}>
              <div>
                {ev && (
                  <>
                    <div className="section" style={{ marginTop: 0 }}><h4>What the local layer buys a brand-new site</h4></div>
                    <table className="t">
                      <thead><tr><th>Item</th><th className="r">New site, no records</th><th className="r">After 2 weeks of checks</th></tr></thead>
                      <tbody>{CLASS_ORDER.map((c) => <tr key={c}><td>{LABEL[c]}</td><td className="r num">{num(ev[c].new_site, 1)}%</td>
                        <td className="r num best">{num(ev[c].with_layer, 1)}%</td></tr>)}</tbody>
                    </table>
                    <div className="note">Forecast error (WAPE) when each post is held out of training and treated as a new site. Learned from that post's first 14 days of records.</div>
                  </>
                )}
              </div>
              <div>
                <div className="section" style={{ marginTop: 0 }}><h4>Retraining</h4></div>
                <p style={{ margin: '0 0 8px' }}><b>{learn.new_days}</b> new site-days of records since the active model was trained.
                  {' '}It retrains on its own at {learn.auto_retrain_after}.</p>
                <button className="btn" style={{ width: 'auto', marginTop: 0 }} onClick={retrain} disabled={learn.running}>{learn.running ? 'Retraining…' : 'Retrain now'}</button>
                {msg && <div className="note">{msg}</div>}
                {last?.skipped && <div className="note">Last run: not enough new live data yet: {last.skipped}.</div>}
                {last?.version && <div className="note">Last run: v{last.version} {last.promoted ? 'was promoted.' : 'did not beat the current model, so it was kept on record only.'}</div>}
                {learn.error && <div className="note" style={{ color: 'var(--critical)' }}>Last run failed: {learn.error}</div>}
                <table className="t" style={{ marginTop: 12 }}>
                  <thead><tr><th>Version</th><th>Trained</th><th>Note</th><th className="r">Error, challenger vs current</th><th>Status</th></tr></thead>
                  <tbody>{[...learn.versions].reverse().map((v) => (
                    <tr key={v.version}><td className="num">v{v.version}</td><td className="small">{v.trained_at.replace('T', ' ')}</td><td className="small">{v.note}</td>
                      <td className="r num small">{v.champion_eval ? `${num(avg(v.eval), 1)}% vs ${num(avg(v.champion_eval), 1)}%` : `${num(avg(v.eval), 1)}%`}</td>
                      <td>{v.version === learn.active ? <span className="pill good">active</span> : v.promoted ? 'retired' : 'kept on record'}</td></tr>))}</tbody>
                </table>
              </div>
            </div>
          </div>
        </div>

        <div className="two">
          <div className="card">
            <div className="card-h"><h2>How accurate is the forecast?</h2>
              <div className="seg" style={{ marginLeft: 'auto' }}>
                <button className={mode === 'archived' ? 'on' : ''} onClick={() => setMode('archived')}>With a weather forecast</button>
                <button className={mode === 'climatology' ? 'on' : ''} onClick={() => setMode('climatology')}>Seasonal averages only</button>
              </div></div>
            <div className="card-b">
              <table className="t">
                <thead><tr><th>Item</th><th className="r">Simple average</th><th className="r">Our first model</th><th className="r">Sujal's model</th><th className="r">Combined (in use)</th></tr></thead>
                <tbody>{CLASS_ORDER.map((c) => {
                  const r = m[c]
                  const naive = Math.min(r.naive.wape, r.lag14.wape, r.r28.wape)
                  const vals = { v1: r.v1.wape, sujal: r.sujal.wape, combined: r.combined.wape }
                  const best = Math.min(...Object.values(vals))
                  return (
                    <tr key={c} onClick={() => setCls(c)} style={{ cursor: 'pointer', background: c === cls ? 'var(--accent-soft)' : undefined }}>
                      <td>{LABEL[c]}</td><td className="r num muted">{num(naive, 1)}%</td>
                      {['v1', 'sujal', 'combined'].map((k) => <td key={k} className={`r num ${vals[k] === best ? 'best' : ''}`}>{num(vals[k], 1)}%</td>)}
                    </tr>
                  )
                })}</tbody>
              </table>
              <div className="note">Forecast error (WAPE: total absolute error as a share of actual use; lower is better) over a held-out year
                ({model.holdout[0]} to {model.holdout[1]}), 1 to 14 days ahead. "Simple average" is the best of three naive rules
                (last 7 days, 14 days ago, last 28 days). The combined model keeps our weather drivers and adds Sujal's recent-usage
                lags and P90 safety model. The P90 forecast covered {num(model.archived[cls].p90_coverage, 0)}% of actual {LABEL[cls].toLowerCase()} days.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h2>What drives {LABEL[cls].toLowerCase()} use</h2><span className="r">share of the model's decisions</span></div>
            <div className="card-b">
              {Object.entries(model.importance[cls]).slice(0, 7).map(([k, v], i, all) => (
                <div className="hbar" key={k}><span>{k}</span><span className="b"><i style={{ width: `${(v / all[0][1]) * 100}%` }} /></span><span className="num">{num(v * 100)}%</span></div>
              ))}
              <div className="note">Click a row in the accuracy table to switch item.</div>
            </div>
          </div>
        </div>

        <div className="two">
          <div className="card">
            <div className="card-h"><h2>Each site's correction</h2><span className="r">real use ÷ shared forecast, last {learn.window} days</span></div>
            <div className="card-b">
              <table className="t">
                <thead><tr><th>Site</th>{CLASS_ORDER.map((c) => <th key={c} className="r">{LABEL[c]}</th>)}</tr></thead>
                <tbody>{Object.entries(learn.factors).map(([site, f]) => (
                  <tr key={site}><td>{site.startsWith('LIVE-') ? site.slice(5).replaceAll('-', ' ').toLowerCase() : site[0] + site.slice(1).toLowerCase()}</td>
                    {CLASS_ORDER.map((c) => <td key={c} className="r">{f[c] ? <span className={`factor ${f[c].k > 1.03 ? 'up' : f[c].k < 0.97 ? 'down' : ''}`} title={`${f[c].n_days} days of records`}>×{num(f[c].k, 2)}</span> : '–'}</td>)}</tr>
                ))}</tbody>
              </table>
              <div className="note">Near ×1.00 means the shared model already matches the site. Hover a value to see how many days of records it rests on.</div>
            </div>
          </div>
          <div className="card">
            <div className="card-h"><h2>Live sites collecting real data</h2><span className="r">{learn.sites.length} registered</span></div>
            <div className="card-b">
              {learn.sites.length === 0 ? <div className="muted">No live site yet. Register one (a mess, store or canteen) in the inventory-check app and record counts there. Its real usage feeds this loop.</div> : (
                <table className="t">
                  <thead><tr><th>Site</th><th className="r">People</th><th className="r">Records</th><th className="r">Days of usage</th><th>Last check</th></tr></thead>
                  <tbody>{learn.sites.map((s) => <tr key={s.id}><td>{s.name}</td><td className="r num">{s.headcount}</td><td className="r num">{s.checks}</td>
                    <td className="r num">{s.observed_days}</td><td className="small">{s.last_check?.replace('T', ' ') ?? '–'}</td></tr>)}</tbody>
                </table>
              )}
              <a className="linkbtn" style={{ display: 'inline-block', marginTop: 12 }} href="/field/" target="_blank" rel="noreferrer">Open inventory check ↗</a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

const avg = (o) => { const v = Object.values(o || {}); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0 }
