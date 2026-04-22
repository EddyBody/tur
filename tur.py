"""
weather_vessel_app.py
=====================
Streamlit weather + vessel tracking app.

Features
--------
  - Wind arrows (direction + strength) on map
  - Temperature rings (optional)
  - 3 tracked vessels: MS Oddrun With, With Junior, With Frohavet
  - Forecast selector: Now / Day 1–7
  - API key loaded from .env file (no typing needed)

Requirements
------------
    pip install requests folium streamlit streamlit-folium websockets python-dotenv

Run
---
    streamlit run weather_vessel_app.py

API key setup
-------------
    Create a file called .env in the same folder as this script:
        AISSTREAM_API_KEY=your_key_here

    Never commit .env to git — add it to .gitignore!
"""

import asyncio
import json
import os
import requests
import folium
import streamlit as st
from streamlit_folium import st_folium
from datetime import date, timedelta

# API key — loaded later inside the app via a cached function (see load_api_key())

try:
    import websockets
    WEBSOCKETS_OK = True
except ImportError:
    WEBSOCKETS_OK = False


# ── Vessel registry ───────────────────────────────────────────────────────────

VESSELS = {
    "MS Oddrun With":  {"mmsi": "259000790", "color": "#1A56DB", "type": "General cargo"},
    "With Junior":     {"mmsi": "259120000", "color": "#E8603C", "type": "Pallet carrier"},
    "With Frohavet":   {"mmsi": "257038590", "color": "#6B21A8", "type": "Reefer"},
}

ALL_MMSI = [v["mmsi"] for v in VESSELS.values()]


# ── Cities ────────────────────────────────────────────────────────────────────

ALL_CITIES = {
    "Stavanger":    (58.9700,  5.7331),
    "Bergen":       (60.3913,  5.3221),
    "Florø":        (61.5997,  5.0327),
    "Måløy":        (61.9351,  5.1145),
    "Ålesund":      (62.4722,  6.1495),
    "Kristiansund": (63.1107,  7.7278),
    "Trondheim":    (63.4305, 10.3951),
    "Brønnøysund":  (65.4747, 12.2131),
    "Bodø":         (67.2827, 14.3751),
    "Svolvær":      (68.2346, 14.5680),
    "Harstad":      (68.7982, 16.5412),
    "Tromsø":       (69.6492, 18.9553),
    "Hammerfest":   (70.6634, 23.6821),
    "Honningsvåg":  (70.9822, 25.9706),
    "Oslo":         (59.9139, 10.7522),
    "Copenhagen":   (55.6761, 12.5683),
    "Stockholm":    (59.3293, 18.0686),
    "London":       (51.5074, -0.1278),
    "Amsterdam":    (52.3676,  4.9041),
    "Hamburg":      (53.5753,  9.9947),
}

DEFAULT_CITIES = [
    "Stavanger", "Bergen", "Florø", "Måløy", "Ålesund",
    "Kristiansund", "Trondheim", "Brønnøysund", "Bodø",
    "Svolvær", "Harstad", "Tromsø", "Hammerfest", "Honningsvåg",
    "Oslo", "Hamburg", "Amsterdam",
]

NAV_STATUS = {
    0: "Underveis (maskin)", 1: "For anker", 2: "Ikke under kommando",
    3: "Begrenset manøvreringsevne", 5: "Fortøyd", 8: "Seiler", 15: "Ukjent",
}

MAP_TILES = {
    "Light (CartoDB)": "CartoDB positron",
    "Dark":            "CartoDB dark_matter",
    "Street (OSM)":    "OpenStreetMap",
}

# Forecast day options — Open-Meteo supports up to 16 days
FORECAST_OPTIONS = {"Nå (gjeldende)": 0} | {f"Dag +{i} ({(date.today()+timedelta(days=i)).strftime('%A %d. %b')})": i
                                            for i in range(1, 8)}


# ── Weather fetch ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def fetch_weather(lat: float, lon: float, forecast_day: int) -> dict:
    """
    If forecast_day == 0: return current conditions.
    If forecast_day >= 1: fetch daily forecast and return noon values for that day.
    """
    if forecast_day == 0:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":        lat,
                "longitude":       lon,
                "current":         ("temperature_2m,apparent_temperature,weathercode,"
                                    "windspeed_10m,winddirection_10m,windgusts_10m,"
                                    "relative_humidity_2m"),
                "wind_speed_unit": "ms",
                "timezone":        "auto",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()["current"]
    else:
        # Use hourly data, pick the 12:00 hour of the target day
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":        lat,
                "longitude":       lon,
                "hourly":          ("temperature_2m,apparent_temperature,weathercode,"
                                    "windspeed_10m,winddirection_10m,windgusts_10m,"
                                    "relative_humidity_2m"),
                "wind_speed_unit": "ms",
                "timezone":        "auto",
                "forecast_days":   forecast_day + 1,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()["hourly"]

        # Find the index for noon (12:00) on the target day
        target_date = (date.today() + timedelta(days=forecast_day)).isoformat()
        target_time = f"{target_date}T12:00"
        try:
            idx = data["time"].index(target_time)
        except ValueError:
            idx = forecast_day * 24 + 12   # fallback

        return {
            "temperature_2m":       data["temperature_2m"][idx],
            "apparent_temperature": data["apparent_temperature"][idx],
            "weathercode":          data["weathercode"][idx],
            "windspeed_10m":        data["windspeed_10m"][idx],
            "winddirection_10m":    data["winddirection_10m"][idx],
            "windgusts_10m":        data["windgusts_10m"][idx],
            "relative_humidity_2m": data["relative_humidity_2m"][idx],
            "is_forecast":          True,
            "forecast_date":        target_date,
        }


def wmo_description(code: int) -> tuple[str, str]:
    table = {
        0:  ("Clear sky",      "☀️"), 1:  ("Mainly clear",  "🌤️"),
        2:  ("Partly cloudy",  "⛅"),  3:  ("Overcast",      "☁️"),
        45: ("Foggy",          "🌫️"), 51: ("Light drizzle", "🌦️"),
        61: ("Slight rain",    "🌧️"), 63: ("Rain",          "🌧️"),
        65: ("Heavy rain",     "🌧️"), 71: ("Slight snow",   "🌨️"),
        73: ("Snow",           "❄️"),  75: ("Heavy snow",    "❄️"),
        80: ("Showers",        "🌦️"), 95: ("Thunderstorm",  "⛈️"),
    }
    return table.get(code, ("Ukjent", "🌡️"))


def beaufort_label(s: float) -> str:
    if s < 0.5:  return "Stille"
    if s < 3.4:  return "Svak"
    if s < 5.5:  return "Lett"
    if s < 8.0:  return "Frisk bris"
    if s < 10.8: return "Laber bris"
    if s < 13.9: return "Stiv bris"
    if s < 17.2: return "Sterk kuling"
    if s < 20.8: return "Liten storm"
    return "Storm"


# ── Wind + temp visuals ───────────────────────────────────────────────────────

def wind_color(s: float) -> str:
    if s < 3:  return "#74C69D"
    if s < 8:  return "#F5C842"
    if s < 14: return "#F4845F"
    return "#D62839"


def wind_arrow_icon(direction_deg: float, speed_ms: float) -> folium.DivIcon:
    color  = wind_color(speed_ms)
    size   = int(max(18, min(speed_ms * 2.2 + 14, 44)))
    rotate = (direction_deg + 180) % 360
    svg = f"""
    <div style="width:{size}px;height:{size}px;transform:rotate({rotate}deg);
                display:flex;align-items:center;justify-content:center;">
      <svg viewBox="0 0 24 24" width="{size}" height="{size}" xmlns="http://www.w3.org/2000/svg">
        <line x1="12" y1="21" x2="12" y2="6" stroke="{color}" stroke-width="3" stroke-linecap="round"/>
        <polygon points="12,2 17.5,10 12,7.5 6.5,10" fill="{color}" stroke="white" stroke-width="0.8"/>
      </svg>
    </div>"""
    return folium.DivIcon(html=svg, icon_size=(size, size), icon_anchor=(size//2, size//2))


def _temp_color(t: float) -> str:
    if t < 0:  return "#5B8DD9"
    if t < 8:  return "#7EC8C8"
    if t < 16: return "#A8C97F"
    if t < 24: return "#F5C842"
    return "#E8603C"


# ── AIS fetch — all vessels in one connection ─────────────────────────────────

async def _fetch_all_vessels(api_key: str) -> dict[str, dict]:
    """
    Subscribe to all MMSIs in one WebSocket connection.
    Collect position reports for up to 90 seconds, return whatever we find.
    Returns dict keyed by MMSI string.
    """
    uri = "wss://stream.aisstream.io/v0/stream"
    subscribe = {
        "APIKey":             api_key,
        "BoundingBoxes":      [[[-90, -180], [90, 180]]],
        "FiltersShipMMSI":    ALL_MMSI,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    results: dict[str, dict] = {}
    debug: list[str] = []

    try:
        async with websockets.connect(uri, open_timeout=10) as ws:
            debug.append("✓ WebSocket connected")
            await ws.send(json.dumps(subscribe))
            debug.append(f"✓ Subscribed to {len(ALL_MMSI)} vessels")

            deadline = asyncio.get_event_loop().time() + 90

            while asyncio.get_event_loop().time() < deadline:
                # Stop early if we have a PositionReport for every vessel
                if all(results.get(m, {}).get("msg_type") == "PositionReport"
                       for m in ALL_MMSI):
                    debug.append("✓ All vessels located — done early")
                    break

                try:
                    remaining = deadline - asyncio.get_event_loop().time()
                    raw  = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 10))
                    msg  = json.loads(raw)
                    mtype = msg.get("MessageType", "")
                    meta  = msg.get("MetaData", {})
                    mmsi  = str(meta.get("MMSI_String", ""))

                    if mmsi not in ALL_MMSI:
                        continue

                    debug.append(f"  → {mtype} from MMSI {mmsi}")

                    if mtype == "PositionReport":
                        pos = msg["Message"]["PositionReport"]
                        results[mmsi] = {
                            "found":    True,
                            "mmsi":     mmsi,
                            "name":     meta.get("ShipName", "").strip(),
                            "lat":      meta.get("latitude"),
                            "lon":      meta.get("longitude"),
                            "sog":      pos.get("Sog", 0),
                            "cog":      pos.get("Cog", 0),
                            "heading":  pos.get("TrueHeading", pos.get("Cog", 0)),
                            "nav_stat": NAV_STATUS.get(pos.get("NavigationalStatus", 15), "Ukjent"),
                            "time_utc": meta.get("time_utc", ""),
                            "destination": results.get(mmsi, {}).get("destination", ""),
                            "msg_type": "PositionReport",
                        }

                    elif mtype == "ShipStaticData" and mmsi not in results:
                        static = msg["Message"]["ShipStaticData"]
                        results[mmsi] = {
                            "found":       True,
                            "mmsi":        mmsi,
                            "name":        static.get("Name", "").strip(),
                            "lat":         meta.get("latitude"),
                            "lon":         meta.get("longitude"),
                            "sog":         0,
                            "cog":         0,
                            "heading":     0,
                            "nav_stat":    "Ukjent",
                            "time_utc":    meta.get("time_utc", ""),
                            "destination": static.get("Destination", "").strip(),
                            "msg_type":    "ShipStaticData",
                        }

                except asyncio.TimeoutError:
                    debug.append("  … waiting …")

    except Exception as e:
        debug.append(f"✗ Error: {type(e).__name__}: {e}")

    # Report what we didn't find
    for mmsi in ALL_MMSI:
        if mmsi not in results:
            debug.append(f"  ✗ No data received for MMSI {mmsi}")

    return {"vessels": results, "debug": debug}


VESSEL_CACHE_FILE = "vessel_cache.json"

def load_vessel_cache() -> dict:
    """Load last known positions from disk. Merker alle som from_cache=True."""
    try:
        from pathlib import Path
        p = Path.cwd() / VESSEL_CACHE_FILE
        if p.exists():
            import json as _json
            data = _json.loads(p.read_text())
            # Merk alle lastede posisjoner som cache — ikke live
            for v in data.values():
                v["from_cache"] = True
            return data
    except Exception:
        pass
    return {}

def save_vessel_cache(vessels: dict) -> None:
    """Save positions to disk, merging with existing cache."""
    try:
        from pathlib import Path
        import json as _json
        p = Path.cwd() / VESSEL_CACHE_FILE
        # Load existing cache and merge — only overwrite if new data has a position
        existing = load_vessel_cache()
        for mmsi, data in vessels.items():
            if data.get("lat"):
                existing[mmsi] = data
        p.write_text(_json.dumps(existing, indent=2))
    except Exception as e:
        pass  # cache save failure is non-fatal

def fetch_all_vessels(api_key: str) -> dict:
    return asyncio.run(_fetch_all_vessels(api_key))


# ── Ship icon ─────────────────────────────────────────────────────────────────

def ship_icon(heading: float, color: str) -> folium.DivIcon:
    svg = f"""
    <div style="transform:rotate({heading}deg);width:34px;height:34px;">
      <svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
        <polygon points="16,2 26,28 16,22 6,28"
                 fill="{color}" stroke="white" stroke-width="1.8"/>
      </svg>
    </div>"""
    return folium.DivIcon(html=svg, icon_size=(34, 34), icon_anchor=(17, 17))


# ── Map builder ───────────────────────────────────────────────────────────────

def build_map(
    weather_data: list[dict],
    vessel_data: dict,
    tile_style: str,
    show_temp_ring: bool,
    forecast_day: int,
) -> folium.Map:

    fmap = folium.Map(location=[65.0, 10.0], zoom_start=5, tiles=tile_style)

    forecast_note = "" if forecast_day == 0 else f" · Forecast noon {(date.today()+timedelta(days=forecast_day)).strftime('%A %d %b')}"

    for e in weather_data:
        w     = e["weather"]
        temp  = w["temperature_2m"]
        feels = w["apparent_temperature"]
        hum   = w["relative_humidity_2m"]
        speed = w["windspeed_10m"]
        gust  = w.get("windgusts_10m", 0)
        wdir  = w["winddirection_10m"]
        label, emoji = wmo_description(w["weathercode"])

        popup = f"""
        <div style="font-family:sans-serif;min-width:200px;padding:4px">
          <h3 style="margin:0 0 4px;font-size:14px">{emoji} {e['city']}</h3>
          <p style="margin:0 0 8px;font-size:11px;color:#888">
            {"Nåværende forhold" if forecast_day == 0 else f"Prognose · kl 12 {(date.today()+timedelta(days=forecast_day)).strftime('%a %d %b')}"}</p>
          <table style="font-size:12px;border-collapse:collapse;width:100%">
            <tr><td style="color:#888;padding:2px 8px 2px 0">Condition</td>
                <td><b>{label}</b></td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Temperature</td>
                <td><b>{temp:.1f} °C</b> (feels {feels:.1f})</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Humidity</td>
                <td>{hum} %</td></tr>
            <tr style="border-top:1px solid #eee">
                <td style="color:#888;padding:6px 8px 2px 0">Wind</td>
                <td><b>{speed:.1f} m/s</b> — {beaufort_label(speed)}</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Gusts</td>
                <td>{gust:.1f} m/s</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Direction</td>
                <td>{wdir:.0f}° (from)</td></tr>
          </table>
        </div>"""

        if show_temp_ring:
            folium.CircleMarker(
                location=[e["lat"], e["lon"]],
                radius=10, color=_temp_color(temp),
                weight=3, fill=False, opacity=0.75,
            ).add_to(fmap)

        folium.Marker(
            location=[e["lat"], e["lon"]],
            icon=wind_arrow_icon(wdir, speed),
            popup=folium.Popup(popup, max_width=240),
            tooltip=f"{e['city']} · {speed:.1f} m/s {beaufort_label(speed)} · {temp:.1f}°C",
        ).add_to(fmap)

    # Vessels
    vessels = vessel_data.get("vessels", {})
    for vessel_name, meta in VESSELS.items():
        mmsi  = meta["mmsi"]
        color = meta["color"]
        vtype = meta["type"]
        v     = vessels.get(mmsi)

        if not v or not v.get("lat"):
            continue

        ts       = v["time_utc"][:19].replace("T", " ") if v["time_utc"] else "—"
        dest_row = (f"<tr><td style='color:#888;padding:2px 8px 2px 0'>Destination</td>"
                    f"<td>{v['destination']}</td></tr>") if v.get("destination") else ""

        vessel_popup = f"""
        <div style="font-family:sans-serif;min-width:200px;padding:4px">
          <h3 style="margin:0 0 4px;font-size:15px">
            <span style="color:{color}">▲</span> {v['name'] or vessel_name}
          </h3>
          <p style="margin:0 0 8px;font-size:11px;color:#888">{vtype}</p>
          <table style="font-size:12px;border-collapse:collapse;width:100%">
            <tr><td style="color:#888;padding:2px 8px 2px 0">MMSI</td>
                <td>{mmsi}</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Status</td>
                <td><b>{v['nav_stat']}</b></td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Speed</td>
                <td>{v['sog']:.1f} knots</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Course</td>
                <td>{v['cog']:.1f}°</td></tr>
            {dest_row}
            <tr><td style="color:#888;padding:2px 8px 2px 0">Position</td>
                <td>{v['lat']:.4f}, {v['lon']:.4f}</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Updated</td>
                <td>{ts} UTC</td></tr>
            <tr><td style="color:#888;padding:2px 8px 2px 0">Status</td>
                <td style="color:{'#888' if v.get('from_cache') else '#1D9E75'}">
                {"📦 Lagret posisjon" if v.get("from_cache") else "✓ Live posisjon"}</td></tr>
          </table>
        </div>"""

        folium.Marker(
            location=[v["lat"], v["lon"]],
            icon=ship_icon(v.get("heading") or v.get("cog") or 0, color),
            popup=folium.Popup(vessel_popup, max_width=260),
            tooltip=f"🚢 {v['name'] or vessel_name}  {v['sog']:.1f} kn  ·  {v['nav_stat']}",
        ).add_to(fmap)

    # Legend
    vessel_swatches = "".join(
        "<span style='color:" + m["color"] + ";font-size:16px'>▲</span> " + n + "<br>"
        for n, m in VESSELS.items()
    )
    temp_note = "<br><b style='font-size:11px'>Ring = temperatur</b>" if show_temp_ring else ""
    legend = f"""
    <div style="position:fixed;bottom:28px;left:28px;z-index:1000;background:white;
                padding:12px 16px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.18);
                font-family:sans-serif;font-size:12px;min-width:170px">
      <b>Wind speed</b><br><br>
      <span style="color:#74C69D;font-size:16px">↑</span> &lt;3 m/s &nbsp;Calm<br>
      <span style="color:#F5C842;font-size:16px">↑</span> 3–8 m/s &nbsp;Moderate<br>
      <span style="color:#F4845F;font-size:16px">↑</span> 8–14 m/s &nbsp;Strong<br>
      <span style="color:#D62839;font-size:16px">↑</span> &gt;14 m/s &nbsp;Gale<br>
      <span style="color:#666;font-size:11px">Pil = retning vinden blåser · Størrelse = styrke</span>
      {temp_note}
      <br><br><b>Vessels</b><br><br>
      {vessel_swatches}
      <span style="color:#666;font-size:11px">Click marker for details</span>
    </div>"""
    fmap.get_root().html.add_child(folium.Element(legend))
    return fmap


# ── Streamlit UI ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Wind & Vessel Tracker", page_icon="🚢", layout="wide")
st.title("🚢 Vind, Vær & Skipstracker")

# API key is loaded inside the sidebar block below

# ── Last inn API-nøkkel (kjøres alltid, ikke bare i sidebar) ──────────────────
from pathlib import Path as _Path

# Last inn API-nøkkel:
# 1. Prøv Streamlit secrets først (fungerer på Community Cloud)
# 2. Fall tilbake til .env-fil (fungerer lokalt)
api_key = ""
try:
    api_key = st.secrets["AISSTREAM_API_KEY"]
except Exception:
    pass

if not api_key:
    for _name in (".env", "key.env"):
        _p = _Path.cwd() / _name
        if _p.exists():
            for _line in _p.read_text().splitlines():
                _line = _line.strip()
                if _line.startswith("AISSTREAM_API_KEY"):
                    api_key = _line.split("=", 1)[-1].strip().strip('"').strip("'")
                    break
        if api_key:
            break

with st.sidebar:
    # Logo
    logo_path = _Path.cwd() / "logo.png"
    if logo_path.exists():
        st.image(str(logo_path), use_container_width=True)
    st.markdown("<div style='margin-bottom: 4px'></div>", unsafe_allow_html=True)

    st.divider()

    forecast_label = st.selectbox(
        "Tidshorisont vær",
        options=list(FORECAST_OPTIONS.keys()),
        index=0,
        help="'Nå' viser gjeldende forhold. Dag +1 og fremover viser prognose kl 12 den dagen.",
    )
    forecast_day = FORECAST_OPTIONS[forecast_label]

    st.divider()

    # Økt høyde på byliste via CSS-injeksjon
    st.markdown("""
        <style>
        [data-testid="stMultiSelect"] [data-baseweb="select"] > div:first-child {
            max-height: 220px;
            overflow-y: auto;
        }
        </style>
    """, unsafe_allow_html=True)

    selected_cities = st.multiselect(
        "Steder med værdata", options=list(ALL_CITIES.keys()), default=DEFAULT_CITIES
    )

    tile_label     = st.selectbox("Kartstil", options=list(MAP_TILES.keys()))
    tile_style     = MAP_TILES[tile_label]

    show_temp_ring = st.toggle("Vis temperaturring", value=True)

    st.divider()

if not selected_cities:
    st.warning("Velg minst ett sted.")
    st.stop()

if not WEBSOCKETS_OK:
    st.error("Kjør `pip install websockets` og start på nytt.")
    st.stop()

# ── Fetch weather ─────────────────────────────────────────────────────────────

weather_data = []
prog = st.progress(0, text="Henter værdata...")
for i, city in enumerate(selected_cities):
    lat, lon = ALL_CITIES[city]
    try:
        w = fetch_weather(lat, lon, forecast_day)
        weather_data.append({"city": city, "lat": lat, "lon": lon, "weather": w})
    except Exception as ex:
        st.warning(f"Could not fetch weather for {city}: {ex}")
    prog.progress((i + 1) / len(selected_cities), text=f"Hentet {city}...")
prog.empty()

# ── Skipposisjoner — les fra cache, oppdater kun ved Refresh ─────────────────

_disk_cache = load_vessel_cache()

# Initialiser session state første gang
if "vessel_data" not in st.session_state:
    st.session_state.vessel_data = {"vessels": _disk_cache, "debug": []}

if "ais_trigger" not in st.session_state:
    st.session_state.ais_trigger = False

vessel_data = st.session_state.vessel_data

# ── Status-rad med knapp — én linje, oppdateres i stedet for å dobles ────────

def _status_text(vessels: dict) -> tuple[str, str]:
    """Returnerer (type, tekst) for status-meldingen."""
    v_live  = sum(1 for v in vessels.values() if v.get("lat") and not v.get("from_cache"))
    v_cache = sum(1 for v in vessels.values() if v.get("lat") and v.get("from_cache"))
    parts = []
    if v_live:  parts.append(f"{v_live} live")
    if v_cache: parts.append(f"{v_cache} fra cache")
    if parts:
        return ("info", f"🚢 Skip: {' · '.join(parts)}")
    return ("info", "Ingen skipposisjoner lastet ennå.")

# Én rad: status til venstre, knapp til høyre
_col_status, _col_btn = st.columns([4, 1])
_status_placeholder = _col_status.empty()
_stype, _smsg = _status_text(vessel_data["vessels"])
_status_placeholder.info(_smsg)

with _col_btn:
    if st.button("🔄 Trykk her for å lytte etter ny AIS info ", help="Oppdater skipposisjoner fra AIS (opptil 90 sek)",
                 use_container_width=True):
        if api_key:
            st.session_state["ais_trigger"] = True
        else:
            st.warning("Ingen API-nøkkel funnet.")

# Kjør live AIS-henting KUN når refresh-knappen er trykket
if api_key and st.session_state.ais_trigger:
    st.session_state.ais_trigger = False

    with st.spinner("Henter live AIS-posisjoner (opptil 90 sek)..."):
        live_data = fetch_all_vessels(api_key)

    # Slå sammen: live vinner over cache, manglende bruker gammel cache
    merged = dict(_disk_cache)
    for mmsi, v in live_data["vessels"].items():
        if v.get("lat"):
            v["from_cache"] = False
            merged[mmsi] = v
    for mmsi in ALL_MMSI:
        if mmsi not in live_data["vessels"] or not live_data["vessels"][mmsi].get("lat"):
            if mmsi in _disk_cache:
                cached_v = dict(_disk_cache[mmsi])
                cached_v["from_cache"] = True
                merged[mmsi] = cached_v

    st.session_state.vessel_data = {"vessels": merged, "debug": live_data.get("debug", [])}
    vessel_data = st.session_state.vessel_data
    save_vessel_cache(merged)

    # Oppdater status-feltet i stedet for å legge til ny linje
    _stype2, _smsg2 = _status_text(merged)
    if any(v.get("lat") for v in merged.values()):
        _status_placeholder.success(_smsg2.replace("🚢 Skip:", "🚢 Oppdatert:"))
    else:
        _status_placeholder.warning("Ingen skip funnet — beholder lagrede posisjoner.")

    with st.expander("AIS-logg"):
        for line in live_data.get("debug", []):
            st.text(line)

# ── Metric row ────────────────────────────────────────────────────────────────

speeds = [e["weather"]["windspeed_10m"]  for e in weather_data]
gusts  = [e["weather"]["windgusts_10m"]  for e in weather_data]
temps  = [e["weather"]["temperature_2m"] for e in weather_data]

cols = st.columns(3 + len(VESSELS))
cols[0].metric("Snitt vind",  f"{sum(speeds)/len(speeds):.1f} m/s")
cols[1].metric("Maks vind",  f"{max(speeds):.1f} m/s",
               weather_data[speeds.index(max(speeds))]["city"])
cols[2].metric("Snitt temp",  f"{sum(temps)/len(temps):.1f} °C")

for i, (vname, vmeta) in enumerate(VESSELS.items()):
    v = vessel_data["vessels"].get(vmeta["mmsi"])
    if v and v.get("found"):
        cols[3 + i].metric(
            vname.replace("MS ", ""),
            f"{v['sog']:.1f} kn",
            v["nav_stat"],
        )
    else:
        cols[3 + i].metric(vname.replace("MS ", ""), "—")

# ── Forecast banner ───────────────────────────────────────────────────────────

if forecast_day > 0:
    target = (date.today() + timedelta(days=forecast_day)).strftime("%A %d %b")
    st.info(f"📅 Viser **prognose for {target} kl 12:00** — skipposisjoner er alltid fra siste oppdatering.")

# ── Map ───────────────────────────────────────────────────────────────────────

fmap = build_map(weather_data, vessel_data, tile_style, show_temp_ring, forecast_day)
st_folium(fmap, width=None, height=650, returned_objects=[])

# ── Tables ────────────────────────────────────────────────────────────────────

with st.expander("Værdatatabell"):
    rows = []
    for e in weather_data:
        w = e["weather"]
        label, emoji = wmo_description(w["weathercode"])
        rows.append({
            "City":          e["city"],
            "Forhold":     f"{emoji} {label}",
            "Temp (°C)":     round(w["temperature_2m"], 1),
            "Wind (m/s)":    round(w["windspeed_10m"], 1),
            "Gusts (m/s)":   round(w["windgusts_10m"], 1),
            "Direction (°)": w["winddirection_10m"],
            "Beaufort":      beaufort_label(w["windspeed_10m"]),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

if vessel_data["vessels"]:
    with st.expander("Skipdata"):
        for vname, vmeta in VESSELS.items():
            v = vessel_data["vessels"].get(vmeta["mmsi"])
            if v:
                st.subheader(vname)
                st.json({k: val for k, val in v.items()})