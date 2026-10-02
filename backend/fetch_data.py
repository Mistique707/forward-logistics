"""Fetch open data once and cache it in data/ so the demo runs offline.

    python -m backend.fetch_data

Sources (no keys, no signup): OpenStreetMap via Overpass, Open-Meteo archive (weather),
Open-Meteo elevation (Copernicus 90 m DEM). Everything lands in data/ and is committed:
weather.csv, a gzipped road extract (raw_osm_roads.json.gz) and the derived network.json.
Existing files are reused; delete one to refetch it.
"""
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from .config import BBOX, DATA, NODES, PASSES, WEATHER_END, WEATHER_START

RAW_OSM = DATA / "raw_osm_roads.json.gz"
WEATHER_CSV = DATA / "weather.csv"
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
UA = {"User-Agent": "forward-logistics/0.1 (SIH prototype)"}


def _get(url, data=None, timeout=180):
    for attempt in range(6):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or attempt == 5:
                raise
            time.sleep(65)  # Open-Meteo's free tier limits requests per minute


def fetch_osm():
    s, w, n, e = BBOX
    q = (f'[out:json][timeout:170];(way["highway"~"^(trunk|primary|secondary|tertiary)$"]({s},{w},{n},{e}););'
         "out body geom qt;")
    for url in OVERPASS:
        try:
            body = _get(url, urllib.parse.urlencode({"data": q}).encode())
            ways = [{"nodes": el["nodes"], "geom": [[p["lat"], p["lon"]] for p in el["geometry"]],
                     "hw": el["tags"]["highway"]}
                    for el in json.loads(body)["elements"] if el["type"] == "way"]
            with gzip.open(RAW_OSM, "wt", encoding="utf-8") as f:
                json.dump(ways, f)
            print(f"osm: {len(ways)} ways from {url}")
            return
        except Exception as ex:  # busy mirrors return HTML or time out; try the next
            print(f"osm: {url} failed ({ex})")
    raise SystemExit("all Overpass mirrors failed; retry later")


def weather_points():
    pts = [(n[0], n[3], n[4]) for n in NODES]
    pts += [(k, p["lat"], p["lon"]) for k, p in PASSES.items()]
    return pts


def fetch_weather():
    frames = []
    for pid, lat, lon in weather_points():
        url = ("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode({
            "latitude": lat, "longitude": lon, "start_date": WEATHER_START, "end_date": WEATHER_END,
            "daily": "temperature_2m_mean,temperature_2m_min,snowfall_sum,wind_gusts_10m_max,cloud_cover_mean",
            "timezone": "Asia/Kolkata"}))
        d = json.loads(_get(url))["daily"]
        frames.append(pd.DataFrame({
            "node_id": pid, "date": d["time"], "t_mean": d["temperature_2m_mean"], "t_min": d["temperature_2m_min"],
            "snow_cm": d["snowfall_sum"], "gust_kmh": d["wind_gusts_10m_max"],
            "cloud_pct": d["cloud_cover_mean"]}).ffill().bfill())  # rare gaps
        print(f"weather: {pid}")
        time.sleep(1)  # be polite to the free API
    pd.concat(frames).round(2).to_csv(WEATHER_CSV, index=False)


def elevation(points):
    """Elevations (m) for [(lat, lon), ...], 100 per request."""
    out = []
    for i in range(0, len(points), 100):
        chunk = points[i:i + 100]
        url = ("https://api.open-meteo.com/v1/elevation?latitude=" + ",".join(f"{a:.5f}" for a, _ in chunk)
               + "&longitude=" + ",".join(f"{b:.5f}" for _, b in chunk))
        out += json.loads(_get(url))["elevation"]
        time.sleep(0.5)
    return out


if __name__ == "__main__":
    from . import network
    if not RAW_OSM.exists():
        fetch_osm()
    if not WEATHER_CSV.exists():
        fetch_weather()
    network.build()
