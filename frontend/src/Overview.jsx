import { useEffect, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import MapView from './MapView.jsx'
import { CLASS_ORDER, STATUS_WORD, TEMPO, api, classStatus, dayLabel, daysText, isoDay, num, worstStatus } from './util.js'

const SEV = { critical: 'Act now', high: 'Act soon', warning: 'Top up' }

export default function Overview({ state, net, scenario, post, cls, onPost, onCls, busy, scenarioName }) {
  return (
    <div className="overview">
      <div className="side card">
        {post ? <PostDetail state={state} scenario={scenario} post={post} cls={cls} onCls={onCls} onBack={() => onPost(null)}
          classes={net.classes} />
          : <ActionList state={state} onPost={onPost} />}
      </div>
      <div className="map-card card">
        <MapView net={net} state={state} post={post} onPost={onPost} trips={[]} />
        {scenarioName && <div className="banner">Showing what-if: <b>{scenarioName}</b></div>}
        {busy && <div className="busy"><div>Re-forecasting and re-planning…</div></div>}
      </div>
    </div>
  )
}

function ActionList({ state, onPost }) {
  const urgent = state.alerts.filter((a) => a.cls)
  const topups = state.alerts.filter((a) => !a.cls)
  const posts = [...state.posts].sort((a, b) => a.worst_days - b.worst_days)
  return (
    <>
      <div className="card-h"><h2>What needs action</h2><span className="r">{urgent.length + topups.length} items</span></div>
      <div className="scroll card-b">
        {urgent.length === 0 && topups.length === 0 && <div className="empty">Every post is stocked to its winter target.</div>}
        {urgent.map((a, i) => <Action key={i} a={a} onPost={onPost} />)}
        {topups.map((a, i) => <Action key={`t${i}`} a={a} onPost={onPost} />)}
        <div className="section">
          <h4>All posts<span className="r">days of stock, lowest item</span></h4>
          <div className="posts">
            {posts.map((p) => {
              const st = worstStatus(p)
              const low = CLASS_ORDER.map((c) => [c, p.classes[c]]).sort((a, b) => a[1].days_of_stock - b[1].days_of_stock)[0]
              return (
                <button className="postrow" key={p.id} onClick={() => onPost(p.id)}>
                  <span><b>{p.name}</b><span className="sub">{p.sector} · {num(p.alt_m)} m · {p.troops} troops</span></span>
                  <span className={`pill ${st}`}>{STATUS_WORD[st]}</span>
                  <span className="num" title={`lowest: ${low[0]}`}>{daysText(p.worst_days)}</span>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </>
  )
}

function Action({ a, onPost }) {
  return (
    <button className={`action ${a.severity}`} onClick={() => onPost(a.post, a.cls)}>
      <div className="row1"><span className={`pill ${a.severity}`}>{SEV[a.severity]}</span>{a.cls && <span className="days">{a.days} d left</span>}</div>
      <h3>{a.title}</h3>
      <div className="what">{a.context.replace(/^./, (c) => c.toUpperCase())}.</div>
      <div className="do">→ {a.action.replace(/^./, (c) => c.toUpperCase())}</div>
    </button>
  )
}

function PostDetail({ state, scenario, post, cls, onCls, onBack, classes }) {
  const [series, setSeries] = useState(null)
  useEffect(() => {
    let live = true
    api('series', { post_id: post, scenario }).then((s) => live && setSeries(s))
    return () => { live = false }
  }, [post, scenario, state])
  const p = state.posts.find((x) => x.id === post)
  const st = worstStatus(p)
  const c = p.classes[cls]
  const unit = classes[cls].unit
  const label = classes[cls].label.toLowerCase()
  const s = series?.post === post ? series.classes[cls] : null
  const demo = state.demo_date
  const arriving = s?.deliveries || []
  const summary = c.runout_day === null
    ? `${classes[cls].label} lasts beyond the six-month forecast.`
    : `Without action, ${label} runs out on ${dayLabel(demo, c.runout_day)} (in ${c.runout_day} days).`
  return (
    <>
      <div className="card-h" style={{ display: 'block' }}>
        <button className="back" onClick={onBack}>← All posts</button>
        <div className="post-title"><h2>{p.name}</h2><span className={`pill ${st}`}>{STATUS_WORD[st]}</span></div>
        <div className="facts"><span><b>{num(p.alt_m)} m</b> altitude</span><span><b>{p.troops}</b> troops</span>
          <span>tempo <b>{TEMPO[p.tempo]}</b></span><span>{p.access === 'track' ? 'road, then mule track' : 'road access'}</span></div>
      </div>
      <div className="scroll card-b">
        <div className="summary">{summary} {arriving.length > 0 && <>This plan lands <b>{num(arriving.reduce((x, d) => x + d.qty, 0))} {unit}</b> by {dayLabel(demo, arriving[0].day)}.</>}</div>
        <h4 className="section" style={{ margin: '0 0 6px', fontSize: 12, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.06em', display: 'flex' }}>Days of stock<span className="r" style={{ marginLeft: 'auto', textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>bar: stock vs winter target</span></h4>
        {CLASS_ORDER.map((k) => {
          const x = p.classes[k]
          const status = classStatus(x)
          return (
            <button key={k} className={`stock ${k === cls ? 'sel' : ''}`} onClick={() => onCls(k)} aria-pressed={k === cls}>
              <span className="name">{classes[k].label}</span>
              <span className="bar"><i className={status} style={{ width: `${Math.min(100, (x.stock / Math.max(1, x.target)) * 100)}%` }} /><s style={{ left: 'calc(100% - 1px)' }} /></span>
              <span className="days">{daysText(x.days_of_stock)}<small>{num(x.stock)} {x.unit}</small></span>
            </button>
          )
        })}
        {s && <StockChart s={s} c={c} state={state} dates={series.dates} unit={unit} label={label} />}
        {s && (
          <div className="section">
            <h4>Why the forecast says this<span className="r">next two weeks</span></h4>
            <div className="why">
              {s.drivers.map(([k, v]) => (
                <span key={k} style={{ display: 'contents' }}>
                  <span>{k}</span>
                  <span className={`v ${v > 0 ? 'up' : 'down'}`}>{v > 0 ? '+' : '−'}{num(Math.abs(v))}%</span>
                </span>
              ))}
            </div>
            <div className="note">How much each factor pushes daily {label} use above or below an average post-day.
              {c.local_factor !== 1 && <> This post's own records adjust the shared model by ×{num(c.local_factor, 2)}.</>}</div>
          </div>
        )}
      </div>
    </>
  )
}

function StockChart({ s, c, state, dates, unit, label }) {
  const demo = state.demo_date
  const zo = state.passes[0]
  const horizon = Math.min(dates.length, (zo.reopen_day ?? 60) + 14)
  const hist = s.history.dates.slice(-45).map((d, i, a) => ({ d, stock: s.history.stock[s.history.stock.length - a.length + i] }))
  const fut = dates.slice(0, horizon).map((d, i) => ({ d, noPlan: s.projected[i], withPlan: s.projected_with_plan[i] }))
  if (hist.length) fut[0].stock = hist[hist.length - 1].stock
  const data = [...hist, ...fut]
  const tick = (d) => new Date(d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
  return (
    <div className="section">
      <h4>{label} stock, past and projected</h4>
      <div className="legend-row">
        <span><i className="sw" style={{ borderColor: '#2a78d6' }} />Recorded</span>
        <span><i className="sw dash" style={{ borderColor: '#c42b1c' }} />If nothing is sent</span>
        <span><i className="sw" style={{ borderColor: '#1a7f37' }} />With this plan</span>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#edf0f3" vertical={false} />
          <XAxis dataKey="d" tickFormatter={tick} stroke="#cfd4db" tick={{ fill: '#6a717d', fontSize: 11 }} minTickGap={40} />
          <YAxis stroke="#cfd4db" tick={{ fill: '#6a717d', fontSize: 11 }} width={44} tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v)} />
          <Tooltip content={<Tip unit={unit} />} />
          <Area dataKey="stock" stroke="#2a78d6" strokeWidth={2} fill="#2a78d6" fillOpacity={0.1} dot={false} isAnimationActive={false} />
          <Line dataKey="noPlan" stroke="#c42b1c" strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
          <Line dataKey="withPlan" stroke="#1a7f37" strokeWidth={2} dot={false} isAnimationActive={false} />
          <ReferenceLine x={isoDay(demo, 0)} stroke="#6a717d" label={{ value: 'today', fill: '#6a717d', fontSize: 10, position: 'insideTopRight' }} />
          {zo.close_day > 0 && <ReferenceLine x={isoDay(demo, zo.close_day)} stroke="#d98a0b" strokeDasharray="3 3"
            label={{ value: 'Zoji La shuts', fill: '#9a5b00', fontSize: 10, position: 'insideTopLeft' }} />}
          {zo.reopen_day !== null && zo.reopen_day < horizon && <ReferenceLine x={isoDay(demo, zo.reopen_day)} stroke="#1a7f37" strokeDasharray="3 3"
            label={{ value: 'reopens', fill: '#1a7f37', fontSize: 10, position: 'insideTopRight' }} />}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

function Tip({ active, payload, label, unit }) {
  if (!active || !payload?.length) return null
  const r = payload[0].payload
  return (
    <div className="leaflet-tooltip tip" style={{ position: 'static' }}>
      <b>{new Date(label).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}</b>
      {r.stock !== undefined && <div className="row"><span>Recorded</span><span className="num">{num(r.stock)} {unit}</span></div>}
      {r.noPlan !== undefined && <div className="row"><span>If nothing is sent</span><span className="num">{num(r.noPlan)} {unit}</span></div>}
      {r.withPlan !== undefined && <div className="row"><span>With this plan</span><span className="num">{num(r.withPlan)} {unit}</span></div>}
    </div>
  )
}
