import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import { MODES, STATUS_WORD, TEMPO, daysText, riskStatus, worstStatus } from './util.js'

const ROUTE = { good: '#9aa1ab', warning: '#e8a33d', critical: '#c42b1c' } // low-risk roads stay quiet
const SIDE = { CHARLIE: 'left', DELTA: 'left', ECHO: 'left', GOLF: 'left', KESTREL: 'below', ONYX: 'below', SAPPHIRE: 'below' }
const TYPE = { base: 'base depot', depot: 'depot', airhead: 'airhead' }
const TOWNS = [['Srinagar', 34.07, 74.79], ['Sonamarg', 34.30, 75.29], ['Drass', 34.43, 75.76], ['Kargil', 34.56, 76.13],
  ['Lamayuru', 34.28, 76.77], ['Leh', 34.16, 77.58], ['Diskit', 34.55, 77.55], ['Tangtse', 34.03, 78.17]]

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

export default function MapView({ net, state, post, onPost, trips, focus = null }) {
  const bounds = useMemo(() => L.latLngBounds(net.nodes.map((n) => [n.lat, n.lon])).pad(0.06), [net])
  const posts = Object.fromEntries(state.posts.map((p) => [p.id, p]))
  const showTrips = focus ? trips.filter((t) => t.vehicle === focus) : trips
  return (
    <>
      <MapContainer bounds={bounds} zoomSnap={0.25} style={{ height: '100%', width: '100%' }}>
        <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}"
          attribution="Terrain: Esri · Roads: © OpenStreetMap contributors" maxZoom={13} />
        <TileLayer url="https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"
          opacity={0.25} maxZoom={13} />
        <Fit bounds={bounds} />
        {TOWNS.map(([name, lat, lon]) => (
          <Marker key={name} position={[lat, lon]} interactive={false} icon={L.divIcon({ className: 'town', html: name, iconSize: [0, 0] })} />
        ))}

        {state.segments.map((s) => {
          const st = riskStatus(s.risk)
          return (
            <Polyline key={s.id} positions={s.geom}
              pathOptions={{ color: ROUTE[st], weight: st === 'good' ? 4 : 6, opacity: showTrips.length ? 0.55 : 0.9, lineCap: 'round' }}>
              <Tooltip className="tip" sticky>
                <b>{st === 'good' ? 'Low' : st === 'warning' ? 'Elevated' : 'High'} route risk</b> · {s.km} km, up to {s.max_alt_m.toLocaleString()} m
                <div className="row"><span>Pass closing</span><span className="num">{Math.round(s.pass_risk * 100)}%</span></div>
                <div className="row"><span>Snow, next 7 days</span><span className="num">{Math.round(s.snow_risk * 100)}%</span></div>
                <div className="row"><span>Terrain</span><span className="num">{Math.round(s.terrain_risk * 100)}%</span></div>
              </Tooltip>
            </Polyline>
          )
        })}

        {net.nodes.filter((n) => n.access === 'track').map((n) => (
          <Polyline key={`mule-${n.id}`} positions={[[n.road_lat, n.road_lon], [n.lat, n.lon]]}
            pathOptions={{ color: '#6a717d', weight: 2, dashArray: '2 6', opacity: 0.9 }}>
            <Tooltip className="tip" sticky>Mule / porter track · {n.mule_km} km, {n.mule_climb_m} m climb</Tooltip>
          </Polyline>
        ))}

        {showTrips.map((t) => (
          <Polyline key={t.vehicle} positions={t.geom} pathOptions={{ color: MODES[t.mode].hex, weight: focus ? 5 : 3.5, opacity: 1, className: 'trip-line' }}>
            <Tooltip className="tip" sticky><b>{t.vehicle}</b> · {MODES[t.mode].label} · {t.stops.map((s) => s.post).join(' → ')}</Tooltip>
          </Polyline>
        ))}

        {state.passes.map((p) => {
          const txt = p.status === 'closed' ? 'closed' : p.id === 'ZOJILA'
            ? (p.close_day === null ? 'open' : `shuts in ${p.close_day} d`)
            : p.closed_days.length ? `brief closures` : 'open'
          return (
            <Marker key={p.id} position={[p.lat, p.lon]} icon={icon(`<div class="pass ${p.status}"></div><div class="plabel">${p.name}<span class="${p.status}">${txt}</span></div>`)}>
              <Tooltip className="tip">{p.name} · {p.alt_m.toLocaleString()} m{p.closed_days?.length ? ` · snow may shut it on day ${p.closed_days.join(', ')}` : ''}</Tooltip>
            </Marker>
          )
        })}

        {net.nodes.filter((n) => n.type !== 'post').map((n) => (
          <Marker key={n.id} position={[n.lat, n.lon]} icon={icon(`<div class="depot ${n.type}"></div><div class="nlabel ${SIDE[n.id] || ''}">${n.name.split(' ').pop()}<span class="sub">${TYPE[n.type]}</span></div>`, 13)}>
            <Tooltip className="tip">{n.name} · {n.alt_m.toLocaleString()} m{n.type === 'base' ? ' · trucks and airdrop airfield' : n.type === 'airhead' ? ' · helicopters' : ''}</Tooltip>
          </Marker>
        ))}

        {net.nodes.filter((n) => n.type === 'post').map((n) => {
          const p = posts[n.id]
          const st = worstStatus(p)
          const tag = st === 'good' ? '' : `<span class="tag ${st}">${daysText(p.worst_days)}</span>`
          return (
            <Marker key={n.id} position={[n.lat, n.lon]} zIndexOffset={1000} eventHandlers={{ click: () => onPost(n.id) }}
              icon={icon(`<div class="pdot ${st} ${post === n.id ? 'sel' : ''}"></div><div class="nlabel ${SIDE[n.id] || ''}">${p.name.replace('Post ', '')}${tag}</div>`)}>
              <Tooltip className="tip" direction="top" offset={[0, -10]}>
                <b>{p.name}</b> · {STATUS_WORD[st]}<br />{n.alt_m.toLocaleString()} m · {p.troops} troops · tempo {TEMPO[p.tempo]}<br />
                Lowest stock: {daysText(p.worst_days)} · click for detail
              </Tooltip>
            </Marker>
          )
        })}
      </MapContainer>
      <div className="maplegend">
        <span><i className="dot" style={{ borderColor: 'var(--critical)' }} />Act now</span>
        <span><i className="dot" style={{ borderColor: 'var(--warning-line)' }} />Top up</span>
        <span><i className="dot" style={{ borderColor: 'var(--good)' }} />On target</span>
        <span><i className="sw" style={{ borderColor: ROUTE.critical }} />High route risk</span>
        <span><i className="sw" style={{ borderColor: ROUTE.warning }} />Elevated</span>
        <span><i className="sw dash" style={{ borderColor: '#6a717d', borderTopWidth: 2 }} />Mule track</span>
        {showTrips.length > 0 && Object.entries(MODES).map(([k, m]) => <span key={k}><i className="sw dash" style={{ borderColor: m.hex }} />{m.short}</span>)}
      </div>
    </>
  )
}
