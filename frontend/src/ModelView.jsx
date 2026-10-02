import { useState } from 'react'
import { CLASS_ORDER, num } from './util.js'

const MODE = {
  archived: ['Archived weather', 'Future weather replayed from the archive: a perfect weather forecast (upper bound).'],
  climatology: ['Climatology only', 'No weather forecast at all: day-of-year averages from earlier years (lower bound).'],
}

export default function ModelView({ model, classes }) {
  const [mode, setMode] = useState('archived')
  const [cls, setCls] = useState('kerosene')
  if (!model) return null
  const m = model[mode]
  const imp = Object.entries(model.importance[cls])
  return (
    <div className="model">
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <h4 style={{ margin: 0, fontSize: 10, letterSpacing: '.12em', color: 'var(--muted)', textTransform: 'uppercase' }}>
            Holdout validation · LightGBM vs naive baseline</h4>
          <div className="seg" style={{ marginLeft: 'auto' }}>
            {Object.entries(MODE).map(([k, [l]]) => <button key={k} className={mode === k ? 'on' : ''} onClick={() => setMode(k)}>{l}</button>)}
          </div>
        </div>
        <table className="grid">
          <thead><tr><th>Supply class</th><th className="r">Model MAE</th><th className="r">Naive MAE</th><th className="r">MAE gain</th>
            <th className="r">Model MAPE</th><th className="r">Naive MAPE</th></tr></thead>
          <tbody>
            {CLASS_ORDER.map((k) => {
              const r = m[k]
              const u = classes[k].unit
              return (
                <tr key={k} className={`click ${cls === k ? 'sel' : ''}`} onClick={() => setCls(k)}>
                  <td>{classes[k].label}</td>
                  <td className="r mono">{num(r.model_mae, 2)} {u}</td>
                  <td className="r mono muted">{num(r.naive_mae, 2)} {u}</td>
                  <td className="r mono gain">{r.mae_gain_pct > 0 ? '−' : '+'}{num(Math.abs(r.mae_gain_pct), 0)}%</td>
                  <td className="r mono" style={{ color: r.model_mape <= r.naive_mape ? 'var(--ink)' : 'var(--serious)' }}>{num(r.model_mape, 1)}%</td>
                  <td className="r mono muted">{num(r.naive_mape, 1)}%</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="note">
          {MODE[mode][1]} Holdout {model.holdout[0]} → {model.holdout[1]} (a full winter), horizons {model.horizons[0]}–{model.horizons[1]} days,
          troops and tempo frozen at the forecast origin. Naive = trailing 7-day mean at the origin. MAE is per post-day.
          Trained on {num(model.train_rows)} post-day-class rows of synthetic history.
        </div>
      </div>
      <div>
        <h4 style={{ margin: '0 0 8px', fontSize: 10, letterSpacing: '.12em', color: 'var(--muted)', textTransform: 'uppercase' }}>
          What drives {classes[cls].label.toLowerCase()} demand · gain importance</h4>
        {imp.map(([k, v]) => (
          <div className="imp" key={k}><span>{k}</span><span className="b"><i style={{ width: `${(v / imp[0][1]) * 100}%` }} /></span>
            <span className="mono">{num(v * 100, 0)}%</span></div>
        ))}
        <div className="note">The model predicts per-capita daily use; troop strength multiplies it, so a surge extrapolates correctly.
          Click a class on the left to see its drivers.</div>
      </div>
    </div>
  )
}
