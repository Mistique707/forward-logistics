"""Road network from OpenStreetMap.

build() turns the cached OSM ways into data/network.json: snapped depot/post locations,
shortest road paths between every pair of endpoints (distance, passes crossed, geometry) and
the route segments drawn on the map (with DEM elevation profile). Runtime only reads the JSON.
"""
import functools
import gzip
import json
import math

import networkx as nx
import numpy as np

from .config import DATA, NODES, PASSES

NETWORK_JSON = DATA / "network.json"
PASS_RADIUS_KM = 2.0


def haversine(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def _decimate(coords, min_km=0.4):
    out = [coords[0]]
    for c in coords[1:-1]:
        if haversine(out[-1], c) >= min_km:
            out.append(c)
    out.append(coords[-1])
    return [[round(a, 5), round(b, 5)] for a, b in out]


def _passes_on(coords):
    """{pass_id: km from start of coords to the pass} for passes the polyline goes through."""
    hits, run = {}, 0.0
    for i, c in enumerate(coords):
        if i:
            run += haversine(coords[i - 1], c)
        for pid, p in PASSES.items():
            if pid not in hits and haversine(c, (p["lat"], p["lon"])) < PASS_RADIUS_KM:
                hits[pid] = round(run, 1)
    return hits


def build():
    from .fetch_data import RAW_OSM, elevation, weather_points

    with gzip.open(RAW_OSM, "rt", encoding="utf-8") as f:
        ways = json.load(f)
    G, pos = nx.Graph(), {}
    for w in ways:
        for a, b, pa, pb in zip(w["nodes"], w["nodes"][1:], w["geom"], w["geom"][1:]):
            pos[a], pos[b] = tuple(pa), tuple(pb)
            G.add_edge(a, b, km=haversine(pa, pb))
    G = G.subgraph(max(nx.connected_components(G), key=len)).copy()
    ids = np.array(list(G.nodes))
    xy = np.array([pos[i] for i in ids])

    def snap(lat, lon):
        d = (xy[:, 0] - lat) ** 2 + ((xy[:, 1] - lon) * math.cos(math.radians(lat))) ** 2
        return int(ids[d.argmin()])

    nodes = []
    for nid, name, typ, lat, lon, access, sector, troops in NODES:
        rn = snap(lat, lon)
        rl = pos[rn]
        if access == "road":  # sit the node exactly on the road
            lat, lon = rl
        nodes.append({"id": nid, "name": name, "type": typ, "lat": lat, "lon": lon, "access": access,
                      "sector": sector, "troops": troops, "road_node": rn, "road_lat": rl[0], "road_lon": rl[1]})

    # all-pairs shortest road paths between endpoints
    pairs = {}
    for i, a in enumerate(nodes):
        for b in nodes[i + 1:]:
            km, path = nx.bidirectional_dijkstra(G, a["road_node"], b["road_node"], weight="km")
            coords = [pos[n] for n in path]
            pairs[f"{a['id']}|{b['id']}"] = {"km": round(km, 1), "passes": _passes_on(coords),
                                              "geom": _decimate(coords), "path": path}
        print(f"paths from {a['id']}")

    # map segments: the shortest-path tree from the base, split at junctions
    base = nodes[0]["id"]
    T = nx.Graph()
    for n in nodes[1:]:
        p = pairs[f"{base}|{n['id']}"]["path"]
        nx.add_path(T, p)
    ends = {n["road_node"] for n in nodes}
    stops = {n for n in T if T.degree(n) != 2 or n in ends}
    segments, used = [], set()
    for s in stops:
        for nb in T[s]:
            if frozenset((s, nb)) in used:
                continue
            seg = [s, nb]
            while seg[-1] not in stops:
                seg.append(next(x for x in T[seg[-1]] if x != seg[-2]))
            used.update(frozenset(e) for e in zip(seg, seg[1:]))
            coords = [pos[n] for n in seg]
            segments.append({"geom": _decimate(coords, 1.0),
                             "km": round(sum(haversine(x, y) for x, y in zip(coords, coords[1:])), 1),
                             "passes": list(_passes_on(coords))})
    for p in pairs.values():
        del p["path"]

    # elevations: nodes, roadheads, passes, segment profiles
    pts = [(n["lat"], n["lon"]) for n in nodes] + [(n["road_lat"], n["road_lon"]) for n in nodes]
    seg_slices = []
    for s in segments:
        prof = s["geom"][::2] + [s["geom"][-1]]
        seg_slices.append((len(pts), len(pts) + len(prof)))
        pts += [tuple(c) for c in prof]
    el = elevation(pts)
    for i, n in enumerate(nodes):
        n["alt_m"], n["road_alt_m"] = round(el[i]), round(el[len(nodes) + i])
        if n["access"] == "track":
            n["mule_km"] = round(haversine((n["lat"], n["lon"]), (n["road_lat"], n["road_lon"])) * 1.6, 1)
            n["mule_climb_m"] = max(0, n["alt_m"] - n["road_alt_m"])
    wpts = weather_points()
    for k, (s, (a, b)) in enumerate(zip(segments, seg_slices)):
        prof = el[a:b]
        s["id"] = f"S{k:02d}"
        s["max_alt_m"] = round(max(prof))
        s["climb_m"] = round(sum(max(0, y - x) for x, y in zip(prof, prof[1:])))
        mid = s["geom"][len(s["geom"]) // 2]
        s["weather_node"] = s["passes"][0] if s["passes"] else min(
            wpts, key=lambda w: haversine(mid, (w[1], w[2])))[0]

    NETWORK_JSON.write_text(json.dumps({"nodes": nodes, "passes": PASSES, "pairs": pairs, "segments": segments}))
    print(f"network: {len(nodes)} nodes, {len(pairs)} paths, {len(segments)} segments -> {NETWORK_JSON}")


@functools.cache
def load():
    net = json.loads(NETWORK_JSON.read_text())
    net["by_id"] = {n["id"]: n for n in net["nodes"]}
    return net


def pair(a, b):
    """Road path a->b: km, {pass: km from a}, geometry oriented from a."""
    net = load()
    if a == b:
        return {"km": 0.0, "passes": {}, "geom": []}
    p = net["pairs"].get(f"{a}|{b}")
    if p:
        return p
    p = net["pairs"][f"{b}|{a}"]
    return {"km": p["km"], "passes": {k: round(p["km"] - v, 1) for k, v in p["passes"].items()},
            "geom": p["geom"][::-1]}


def air_km(a, b):
    na, nb = load()["by_id"][a], load()["by_id"][b]
    return haversine((na["lat"], na["lon"]), (nb["lat"], nb["lon"]))
