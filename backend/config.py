"""Static theatre definition and model constants. All names are fictional."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "logistics.db"
MODELS = ROOT / "models"

SEED = 26251
SIM_START = "2021-01-01"
WEATHER_START, WEATHER_END = "2020-10-01", "2026-04-30"
BBOX = (33.6, 74.6, 35.2, 78.6)  # south, west, north, east

# id, display name, type, lat, lon, access, sector, base troops
# Track posts sit off-road; their roadhead is the nearest node on the OSM road graph.
NODES = [
    ("SAPPHIRE", "Base Depot Sapphire", "base", 34.1000, 74.8700, "road", None, 0),
    ("KESTREL", "Int. Depot Kestrel", "depot", 34.5500, 76.1300, "road", "Kargil", 0),
    ("ONYX", "Airhead Onyx", "airhead", 34.1500, 77.5700, "road", "Leh", 0),
    ("ALPHA", "Post Alpha", "post", 34.4760, 75.7880, "track", "Drass", 45),
    ("BRAVO", "Post Bravo", "post", 34.6171, 76.3047, "road", "Kargil", 120),
    ("CHARLIE", "Post Charlie", "post", 34.6300, 76.2200, "track", "Kargil", 35),
    ("DELTA", "Post Delta", "post", 34.4092, 75.6516, "road", "Drass", 90),
    ("ECHO", "Post Echo", "post", 34.7080, 77.1551, "road", "Nubra", 110),
    ("FOXTROT", "Post Foxtrot", "post", 34.7760, 77.2320, "track", "Nubra", 40),
    ("GOLF", "Post Golf", "post", 34.0368, 78.2660, "road", "Changthang", 100),
    ("HOTEL", "Post Hotel", "post", 34.0600, 78.3240, "track", "Changthang", 30),
]
POSTS = [n[0] for n in NODES if n[2] == "post"]
SECTORS = sorted({n[6] for n in NODES if n[2] == "post"})

# Real passes (OSM). Zojila closes for the winter; the other two close only briefly after heavy snow.
PASSES = {
    "ZOJILA": {"name": "Zoji La", "lat": 34.2791, "lon": 75.4706, "alt_m": 3528, "seasonal": True},
    "KHARDUNG": {"name": "Khardung La", "lat": 34.2787, "lon": 77.6047, "alt_m": 5359, "seasonal": False},
    "CHANG": {"name": "Chang La", "lat": 34.0472, "lon": 77.9304, "alt_m": 5383, "seasonal": False},
}
ZOJILA_CLOSE_SNOW_3D_CM = 15.0   # closes on first day after 1 Nov when 3-day snowfall >= this
ZOJILA_LATEST_CLOSE = (12, 31)   # ...or on 31 Dec at the latest
ZOJILA_REOPEN_TEMP_C = 0.0       # reopens first day after 15 Mar when 14-day mean temp > this
TRANSIENT_CLOSE_SNOW_CM = 10.0   # Khardung / Chang close that day + 1 (+2 if >= 20 cm)

# Supply classes: unit, kg per unit, criticality weight (used for alert ranking and drop penalties)
CLASSES = {
    "rations": {"label": "Rations", "unit": "kg", "kg": 1.0, "crit": 1.0},
    "kerosene": {"label": "Kerosene", "unit": "L", "kg": 0.8, "crit": 1.0},
    "diesel": {"label": "Diesel", "unit": "L", "kg": 0.84, "crit": 0.8},
    "ammunition": {"label": "Ammunition", "unit": "kg", "kg": 1.0, "crit": 0.9},
    "medical": {"label": "Medical", "unit": "kg", "kg": 1.0, "crit": 0.9},
}

# Illustrative transport parameters (not real specifications). Costs in rupees.
MODES = {
    "truck": {"label": "Truck convoy", "payload_kg": 4000, "speed_kmh": 10.5, "fixed": 20000, "per_km": 60,
              "fleet": 12, "base": "SAPPHIRE"},  # speed includes night halts (25 km/h driving, 10 h/day)
    "mule": {"label": "Mule/porter train", "payload_kg": 3000, "speed_kmh": 3.0, "climb_m_per_h": 400,
             "max_snow_cm": 25, "per_kg": 4},
    "heli": {"label": "Helicopter", "payload_kg": 1500, "speed_kmh": 180, "fixed": 50000, "per_km": 1500,
             "airframes": 2, "sorties_per_day": 2, "base": "ONYX", "max_gust": 45, "max_snow_cm": 1,
             "max_cloud": 85, "range_km": 250},
    "airdrop": {"label": "Airdrop", "payload_kg": 5000, "speed_kmh": 400, "fixed": 500000, "per_km": 2000,
                "sorties_per_day": 1, "base": "SAPPHIRE", "max_gust": 35, "max_cloud": 70, "loss": 0.10},
}

PLAN_DAYS = 14
FORECAST_DAYS = 180
WEATHER_FORECAST_DAYS = 16  # beyond this the forecast uses climatology
SAFETY_DAYS = 7


def heli_derate(alt_m):
    """Helicopter payload fraction at a landing site altitude (~40% at 5,000 m)."""
    return max(0.35, min(1.0, 1 - (alt_m - 2000) / 5000))
