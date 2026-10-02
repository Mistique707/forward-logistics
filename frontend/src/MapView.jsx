import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { MODES, STATUS_LABEL, TEMPO, riskStatus, worstStatus } from './util.js'

const RISK = { good: '#0ca30c', warning: '#fab219', critical: '#d03b3b' }
const ROUTE = { ...RISK, good: '#8a8983' } // low-risk roads stay neutral so plan lines and risk stand out
const MODE_HEX = { truck: '#3987e5', heli: '#199e70', airdrop: '#d95926' }

// label placement so neighbouring posts do not collide
const SIDE = { CHARLIE: 'left', DELTA: 'left', ECHO: 'left', GOLF: 'left', KESTREL: 'below', ONYX: 'below', SAPPHIRE: 'below' }
const TOWNS = [['Srinagar', 34.07, 74.79], ['Sonamarg', 34.30, 75.29], ['Drass', 34.43, 75.76], ['Kargil', 34.56, 76.13],
  ['Lamayuru', 34.28, 76.77], ['Leh', 34.16, 77.58], ['Diskit', 34.55, 77.55], ['Tangtse', 34.03, 78.17]]
const TYPE = { base: 'base depot', depot: 'int. depot', airhead: 'airhead' }

function Fit({ bounds }) {
  const map = useMap()
  useEffect(() => {
    const ro = new ResizeObserver(() => { map.invalidateSize(); map.fitBounds(bounds) })
    ro.observe(map.getContainer())
    return () => ro.disconnect()
  }, [map, bounds])
  return null
}

const icon = (html, size = 16) => L.divIcon({ className: 'node', html, iconSize: [size, size], iconAnchor: [size / 2, size / 2] })

export default function MapView({ net, state, post, trip, onPost }) {
  const bounds = useMemo(() => L.latLngBounds(net.nodes.map((n) => [n.lat, n.lon])).pad(0.08), [net])
  const posts = Object.fromEntries(state.posts.map((p) => [p.id, p]))
  const trips = state.plan.trips
  const shown = trip === null ? trips : trips.filter((t) => t.vehicle === trip)

  return (
    <>
      <MapContainer bounds={bounds} zoomSnap={0.25} style={{ height: '100%', width: '100%' }} zoomControl attributionControl>
        <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade_Dark/MapServer/tile/{z}/{y}/{x}"
          attribution="Terrain: Esri · Roads: © OpenStreetMap contributors" maxZoom={13} />
        {TOWNS.map(([name, lat, lon]) => (
          <Marker key={name} position={[lat, lon]} interactive={false} icon={L.divIcon({ className: 'town', html: name, iconSize: [0, 0] })} />
        ))}
        <Fit bounds={bounds} />

        {state.segments.map((s) => {
          const st = riskStatus(s.risk)
          return (
            <Polyline key={s.id} positions={s.geom} pathOptions={{ color: ROUTE[st], weight: st === 'good' ? 5 : 7, opacity: trip ? 0.3 : 0.75, lineCap: 'round' }}>
              <Tooltip className="tip" sticky>
                <b>Route risk {Math.round(s.risk * 100)}</b> · {s.km} km · max {s.max_alt_m.toLocaleString()} m
                <div className="row"><span>Pass closure</span><span className="mono">{Math.round(s.pass_risk * 100)}</span></div>
                <div className="row"><span>Snow, next 7 d</span><span className="mono">{Math.round(s.snow_risk * 100)}</span></div>
                <div className="row"><span>Terrain</span><span className="mono">{Math.round(s.terrain_risk * 100)}</span></div>
              </Tooltip>
            </Polyline>
          )
        })}

        {net.nodes.filter((n) => n.access === 'track').map((n) => (
          <Polyline key={`mule-${n.id}`} positions={[[n.road_lat, n.road_lon], [n.lat, n.lon]]}
            pathOptions={{ color: '#c3c2b7', weight: 2, dashArray: '2 6', opacity: 0.8 }}>
            <Tooltip className="tip" sticky>Mule/porter track · {n.mule_km} km · +{n.mule_climb_m} m climb</Tooltip>
          </Polyline>
        ))}

        {shown.map((t) => (
          <Polyline key={t.vehicle} positions={t.geom}
            pathOptions={{ color: MODE_HEX[t.mode], weight: trip ? 4 : 2.5, opacity: 1, className: 'trip-line' }}>
            <Tooltip className="tip" sticky>
              <b>{t.vehicle}</b> · {MODES[t.mode].label}<br />
              {t.stops.map((s) => s.post).join(' → ')} · {Math.round(t.load_kg).toLocaleString()} kg
            </Tooltip>
          </Polyline>
        ))}

        {state.passes.map((p) => {
          const txt = p.status === 'closed' ? 'closed' : p.id === 'ZOJILA'
            ? (p.close_day === null ? 'open' : `closes in ${p.close_day} d`)
            : p.closed_days.length ? `shut day ${p.closed_days.join(', ')}` : 'open'
          return (
            <Marker key={p.id} position={[p.lat, p.lon]} icon={icon(
              `<div class="pass ${p.status}"></div><div class="pass-label">${p.name}<span class="${p.status}">${txt}</span></div>`)}>
              <Tooltip className="tip">{p.name} · {p.alt_m.toLocaleString()} m</Tooltip>
            </Marker>
          )
        })}

        {net.nodes.filter((n) => n.type !== 'post').map((n) => (
          <Marker key={n.id} position={[n.lat, n.lon]} icon={icon(
            `<div class="depot ${n.type}"></div><div class="node-label ${SIDE[n.id] || ''}">${n.id}<span class="sub">${TYPE[n.type]}</span></div>`, 14)}>
            <Tooltip className="tip">{n.name} · {n.alt_m.toLocaleString()} m
              {n.type === 'base' && <><br />Truck fleet · airdrop airfield</>}
              {n.type === 'airhead' && <><br />Helicopters · air-maintenance reserve</>}
            </Tooltip>
          </Marker>
        ))}

        {net.nodes.filter((n) => n.type === 'post').map((n) => {
          const p = posts[n.id]
          const st = worstStatus(p)
          const days = p.worst_days >= 180 ? '180+' : p.worst_days
          return (
            <Marker key={n.id} position={[n.lat, n.lon]} zIndexOffset={1000}
              eventHandlers={{ click: () => onPost(n.id) }}
              icon={icon(`<div class="post-dot ${st} ${post === n.id ? 'sel' : ''}"></div>
                <div class="node-label ${SIDE[n.id] || ''}">${n.id}<span class="mono ${st}">${days} d</span></div>`)}>
              <Tooltip className="tip" direction="top" offset={[0, -10]}>
                <b>{p.name}</b> · {n.alt_m.toLocaleString()} m · {n.access === 'track' ? 'mule track' : 'road'}<br />
                {p.troops} troops · tempo {TEMPO[p.tempo]}<br />
                {STATUS_LABEL[st]} · worst {days} days of stock
              </Tooltip>
            </Marker>
          )
        })}
      </MapContainer>
      <div className="legend">
        <div className="lg"><h4>Route risk</h4>
          {[['good', 'Low'], ['warning', 'Elevated'], ['critical', 'High']].map(([k, l]) => (
            <span className="li" key={k}><span className="sw-line" style={{ borderColor: ROUTE[k] }} />{l}</span>
          ))}</div>
        <div className="lg"><h4>Post stock</h4>
          {[['critical', 'Runs out ≤ 14 d'], ['warning', 'Below winter target'], ['good', 'On target']].map(([k, l]) => (
            <span className="li" key={k}><span className="sw-dot" style={{ borderColor: RISK[k] }} />{l}</span>
          ))}</div>
        <div className="lg"><h4>Dispatch plan</h4>
          {Object.entries(MODES).map(([k, m]) => (
            <span className="li" key={k}><span className="sw-line dash" style={{ borderColor: MODE_HEX[k] }} />{m.label}</span>
          ))}
          <span className="li"><span className="sw-line dash" style={{ borderColor: '#c3c2b7', borderTopWidth: 2 }} />Mule track</span></div>
      </div>
    </>
  )
}
