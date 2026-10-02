import { useEffect, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CLASS_ORDER, STATUS_LABEL, TEMPO, api, classStatus, dayLabel, isoDay, num, worstStatus } from './util.js'

const C = { stock: '#3987e5', noPlan: '#898781', withPlan: '#199e70', grid: '#2c2c2a', axis: '#383835', muted: '#898781' }

export default function PostPanel({ state, scenario, post, cls, onCls, onPost, classes }) {
  const [series, setSeries] = useState(null)
  useEffect(() => {
    if (!post) return
    let live = true
    api('series', { post_id: post, scenario }).then((s) => live && setSeries(s))
    return () => { live = false }
  }, [post, scenario, state])

  if (!post) return <Overview state={state} onPost={onPost} />
  const p = state.posts.find((x) => x.id === post)
  const st = worstStatus(p)
  return (
    <>
      <div className="post-head">
        <h3><span className={`sev-dot`} style={{ background: `var(--${st === 'good' ? 'good' : st})` }} />{p.name}
          <button className="back" onClick={() => onPost(null)}>All posts</button></h3>
        <div className="meta">
          <span>Alt <b>{num(p.alt_m)} m</b></span><span>Troops <b>{p.troops}</b></span>
          <span>Tempo <b>{TEMPO[p.tempo]}</b></span><span>Access <b>{p.access === 'track' ? 'road + mule track' : 'road'}</b></span>
          <span>Sector <b>{p.sector}</b></span>
        </div>
      </div>
      <div className="scroll">
        <div style={{ padding: '8px 0' }}>
          {CLASS_ORDER.map((k) => <StockRow key={k} c={p.classes[k]} label={classes[k].label} sel={k === cls} onClick={() => onCls(k)} />)}
        </div>
        {series && series.post === post && <Charts series={series} state={state} post={p} cls={cls} unit={classes[cls].unit} label={classes[cls].label} />}
      </div>
    </>
  )
}

function StockRow({ c, label, sel, onClick }) {
  const st = classStatus(c)
  const need = c.target ? c.stock / c.target : 1
  return (
    <div className={`stock-row ${sel ? 'sel' : ''}`} onClick={onClick} title={STATUS_LABEL[st]}>
      <div className="name">{label}</div>
      <div className="bar"><i className={st} style={{ width: `${Math.min(100, need * 100)}%` }} /><s style={{ left: 'calc(100% - 1px)' }} /></div>
      <div className="days">{c.days_of_stock >= 180 ? '180+' : c.days_of_stock} d
        <small>{num(c.stock)} {c.unit}</small></div>
    </div>
  )
}

function Charts({ series, state, post, cls, unit, label }) {
  const s = series.classes[cls]
  const c = post.classes[cls]
  const demo = state.demo_date
  const zo = state.passes[0]
  const horizon = Math.min(series.dates.length, (zo.reopen_day ?? 150) + 12)
  const hist = s.history.dates.slice(-60).map((d, i, a) => ({ d, stock: s.history.stock[s.history.stock.length - a.length + i] }))
  const fut = series.dates.slice(0, horizon).map((d, i) => ({ d, noPlan: s.projected[i], withPlan: s.projected_with_plan[i] }))
  if (hist.length) fut[0].stock = hist[hist.length - 1].stock
  const data = [...hist, ...fut]
  const cons = [
    ...s.history.dates.slice(-45).map((d, i, a) => ({ d, actual: s.history.consumed[s.history.consumed.length - a.length + i] })),
    ...series.dates.slice(0, 45).map((d, i) => ({ d, forecast: s.forecast[i] })),
  ]
  const tick = (d) => new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
  const maxDrv = Math.max(...s.drivers.map(([, v]) => Math.abs(v)), 1e-9)
  const today = isoDay(demo, 0)
  return (
    <>
      <div className="section">
        <h4>{label} stock · projection<span className="r ink2 mono">target {num(c.target)} {unit}</span></h4>
        <div className="chart-legend">
          <span><i className="sw-line" style={{ borderColor: C.stock }} />Recorded</span>
          <span><i className="sw-line dash" style={{ borderColor: C.noPlan }} />No action</span>
          <span><i className="sw-line" style={{ borderColor: C.withPlan }} />With dispatch plan</span>
        </div>
        <ResponsiveContainer width="100%" height={190}>
          <ComposedChart data={data} margin={{ top: 14, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="d" tickFormatter={tick} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} minTickGap={36} />
            <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} width={46} tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v)} />
            <Tooltip content={<StockTip unit={unit} />} />
            <Area dataKey="stock" stroke={C.stock} strokeWidth={2} fill={C.stock} fillOpacity={0.12} dot={false} isAnimationActive={false} />
            <Line dataKey="noPlan" stroke={C.noPlan} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
            <Line dataKey="withPlan" stroke={C.withPlan} strokeWidth={2} dot={false} isAnimationActive={false} />
            <ReferenceLine x={today} stroke="#c3c2b7" label={{ value: 'TODAY', fill: '#c3c2b7', fontSize: 9, position: 'insideTopRight' }} />
            {zo.close_day !== null && zo.close_day > 0 && <ReferenceLine x={isoDay(demo, zo.close_day)} stroke="#fab219" strokeDasharray="3 3"
              label={{ value: 'ZOJI LA CLOSES', fill: '#fab219', fontSize: 9, position: 'insideTopLeft' }} />}
            {zo.reopen_day !== null && zo.reopen_day < horizon && <ReferenceLine x={isoDay(demo, zo.reopen_day)} stroke="#0ca30c" strokeDasharray="3 3"
              label={{ value: 'REOPENS', fill: '#0ca30c', fontSize: 9, position: 'insideTopRight' }} />}
            {c.runout_day !== null && c.runout_day < horizon && <ReferenceLine x={isoDay(demo, c.runout_day)} stroke="#d03b3b"
              label={{ value: 'RUNOUT', fill: '#d03b3b', fontSize: 9, position: 'insideBottomRight' }} />}
          </ComposedChart>
        </ResponsiveContainer>
        <div className="note">
          {c.runout_day === null ? 'Stock lasts beyond the 180-day forecast horizon.'
            : <>Without action, {label.toLowerCase()} runs out <b className="ink2">{dayLabel(demo, c.runout_day)}</b> (day {c.runout_day}).</>}
          {' '}Forecast weather: days 1–16 from the weather feed, then climatology.
        </div>
      </div>

      <div className="section">
        <h4>Daily consumption<span className="r mono ink2">next 7 d avg {num(c.daily, 1)} {unit}/day</span></h4>
        <div className="chart-legend">
          <span><i className="sw-line" style={{ borderColor: C.stock }} />Actual</span>
          <span><i className="sw-line dash" style={{ borderColor: C.stock }} />LightGBM forecast</span>
        </div>
        <ResponsiveContainer width="100%" height={110}>
          <ComposedChart data={cons} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="d" tickFormatter={tick} stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} minTickGap={36} />
            <YAxis stroke={C.axis} tick={{ fill: C.muted, fontSize: 10 }} width={46} />
            <Tooltip content={<ConsTip unit={unit} />} />
            <Line dataKey="actual" stroke={C.stock} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="forecast" stroke={C.stock} strokeWidth={2} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
            <ReferenceLine x={today} stroke="#c3c2b7" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="section">
        <h4>Why this forecast<span className="r muted">SHAP, next 14 d, {unit}/day</span></h4>
        {s.drivers.map(([k, v]) => (
          <div className="driver" key={k}>
            <span>{k}</span>
            <span className="dbar"><i style={v >= 0 ? { left: '50%', width: `${(v / maxDrv) * 50}%` } : { right: '50%', width: `${(-v / maxDrv) * 50}%`, background: '#898781' }} /></span>
            <span className="v">{v >= 0 ? '+' : '−'}{num(Math.abs(v), 1)}</span>
          </div>
        ))}
        <div className="note">Contribution of each driver to the daily forecast, relative to the model's average post-day.</div>
      </div>

      <div className="section">
        <h4>Inbound under this plan</h4>
        {s.deliveries.length === 0 && <div className="muted" style={{ fontSize: 12 }}>Nothing planned for {label.toLowerCase()}.</div>}
        {s.deliveries.map((d) => (
          <div className="inbound" key={d.day}><span>{dayLabel(demo, d.day)} (day {d.day})</span><span className="mono">+{num(d.qty)} {unit}</span></div>
        ))}
      </div>
    </>
  )
}

function StockTip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="leaflet-tooltip tip" style={{ position: 'static' }}>
      <b>{new Date(label).toLocaleDateString('en-GB', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })}</b>
      {row.stock !== undefined && <div className="row"><span>Recorded</span><span className="mono">{num(row.stock)} {unit}</span></div>}
      {row.noPlan !== undefined && <div className="row"><span>No action</span><span className="mono">{num(row.noPlan)} {unit}</span></div>}
      {row.withPlan !== undefined && <div className="row"><span>With plan</span><span className="mono">{num(row.withPlan)} {unit}</span></div>}
    </div>
  )
}

function ConsTip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="leaflet-tooltip tip" style={{ position: 'static' }}>
      <b>{new Date(label).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}</b>
      <div className="row"><span>{row.actual !== undefined ? 'Actual' : 'Forecast'}</span>
        <span className="mono">{num(row.actual ?? row.forecast, 1)} {unit}</span></div>
    </div>
  )
}

function Overview({ state, onPost }) {
  const posts = [...state.posts].sort((a, b) => a.worst_days - b.worst_days)
  return (
    <>
      <div className="panel-head"><h2>Forward posts</h2><span className="count">{posts.length} posts · click for detail</span></div>
      <div className="scroll">
        {posts.map((p) => {
          const st = worstStatus(p)
          const worst = Object.entries(p.classes).sort((a, b) => a[1].days_of_stock - b[1].days_of_stock)[0]
          return (
            <div className="overview-row" key={p.id} onClick={() => onPost(p.id)}>
              <span className={`post-dot ${st}`} style={{ width: 14, height: 14 }} />
              <div><div className="n">{p.name}</div><div className="s">{p.sector} · {num(p.alt_m)} m · {p.troops} troops</div></div>
              <div className="s" style={{ textAlign: 'right' }}>lowest<br /><span className="ink2">{worst[0]}</span></div>
              <div className="mono" style={{ textAlign: 'right', color: `var(--${st === 'good' ? 'ink-2' : st})` }}>
                {p.worst_days >= 180 ? '180+' : p.worst_days} d</div>
            </div>
          )
        })}
      </div>
    </>
  )
}
