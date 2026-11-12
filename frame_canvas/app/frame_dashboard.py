#!/usr/bin/env python3
"""
Frame Dashboard – viser klokke, dato, vær, kalender og kamerabilder i Art Mode
på Samsung The Frame, med styringsside (også for Home Assistant).

  1. En lokal webserver lager dashboard-siden og styringssiden (/control).
  2. Headless Chrome tar et 4K-skjermbilde av dashboardet.
  3. Bildet lastes opp til TV-ens Art Mode og vises; forrige bilde slettes.
  4. TV-ens egen bevegelsessensor skrur skjermen av/på (bare i Art Mode).

Kommandoer:
  python frame_dashboard.py run            # kjør tjenesten (standard)
  python frame_dashboard.py render         # lag bare dashboard.png lokalt
  python frame_dashboard.py status         # sjekk TV-tilkobling og Art Mode
  python frame_dashboard.py cleanup        # slett alle dashboard-bilder fra TV-en

HTTP-API (port fra config, standard 8765):
  GET  /control             styringsside
  GET  /preview.jpg         siste bilde sendt til TV-en
  GET  /api/status          innstillinger + TV-status
  POST /api/settings        {"theme": "lys", "show": {"cameras": false}, ...}
  POST /api/tv              {"art_mode": true, "brightness": 6, "motion_timer": "15", ...}
  POST /api/refresh         oppdater TV-en nå
"""
from __future__ import annotations

import base64
import datetime as dt
import hmac
import ipaddress
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("FRAME_DATA") or ("/data" if Path("/data/options.json").exists() else HERE))
CONFIG_PATH = DATA / "config.json"
OPTIONS_PATH = DATA / "options.json"                     # skrives av Home Assistant
SETTINGS_PATH = DATA / "settings.json"
STATE_PATH = DATA / ".frame_state.json"
TOKEN_PATH = DATA / ".tv_token"
PREVIEW_PATH = DATA / "dashboard.jpg"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")

log = logging.getLogger("frame")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

DAYS_NO = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS_NO = ["januar", "februar", "mars", "april", "mai", "juni", "juli",
             "august", "september", "oktober", "november", "desember"]
DAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]


def lang_of(cfg: dict, settings: dict) -> str:
    """Resolve UI/dashboard language: explicit setting, else Home Assistant's language, else Norwegian."""
    lang = settings.get("language", "auto")
    if lang in ("no", "en"):
        return lang
    ha = str(cfg.get("ha_language") or "")
    if not ha:
        return "no"
    return "no" if ha.split("-")[0] in ("nb", "nn", "no") else "en"

CHOICES = {
    "theme": ["mork", "lys", "papir", "natt", "skog"],
    "layout": ["klassisk", "sentrert", "kamera", "minimal"],
    "font": ["sans", "serif"],
    "font_weight": ["tynn", "normal", "fet"],
    "clock_size": ["stor", "medium", "liten"],
    "ticker_position": ["bottom", "top"],
    "language": ["auto", "no", "en"],
}
DEFAULT_SETTINGS = {
    "enabled": True,          # send dashboard til TV-en
    "language": "auto",       # auto (følger Home Assistant) | no | en
    "theme": "mork",
    "layout": "klassisk",
    "font": "sans",
    "font_weight": "tynn",
    "accent": "",             # tom = temaets egen farge
    "clock_size": "stor",
    "ticker_position": "bottom",
    "news_source": 0,         # indeks i news_feeds
    "news_count": 4,
    "show": {"clock": True, "date": True, "week": True, "weather": True,
             "calendar": True, "calendar_legend": True, "cameras": True, "news": True},
    "interval_seconds": 60,
    "location": None,         # {"name", "lat", "lon"} – overstyrer config.json
    "calendars": None,        # [{"id","name","color","ics_url","visible"}] – None = bruk config.json
    "calendar_days": 7,       # hvor langt frem det hentes avtaler
    "calendar_list_days": 5,  # maks antall dager i lista
    "camera_ids": None,       # valgte kameraer (camera.xxx fra HA eller cfg:Navn) – None = alle fra config
    "tv": None,               # valgt TV {"ip","mac","name","model"} – overstyrer tv_ip i oppsettet
    # Bakgrunnsbilde: none | kunst | natur | bing
    "background": {"source": "none", "interval_minutes": 30, "dim": 65, "offset": 0, "panels": "auto"},
    # Tilstedeværelse: slå TV-en på i Art Mode når noen er i rommet, av når rommet er tomt
    "presence": {"enabled": False, "entities": [], "off_minutes": 15, "turn_on": True, "turn_off": True},
}

DEFAULT_NEWS_FEEDS = [
    {"name": "NRK", "url": "https://www.nrk.no/toppsaker.rss"},
    {"name": "NRK Norge", "url": "https://www.nrk.no/norge/toppsaker.rss"},
    {"name": "NRK Urix", "url": "https://www.nrk.no/urix/toppsaker.rss"},
    {"name": "NRK Sport", "url": "https://www.nrk.no/sport/toppsaker.rss"},
]

DEFAULT_RADIO = [
    {"id": "p1", "name": "NRK P1", "url": "https://lyd.nrk.no/nrk_radio_p1_ostlandssendingen_mp3_h"},
    {"id": "p2", "name": "NRK P2", "url": "https://lyd.nrk.no/nrk_radio_p2_mp3_h"},
    {"id": "p3", "name": "NRK P3", "url": "https://lyd.nrk.no/nrk_radio_p3_mp3_h"},
    {"id": "mp3", "name": "NRK mP3", "url": "https://lyd.nrk.no/nrk_radio_mp3_mp3_h"},
    {"id": "nyheter", "name": "NRK Nyheter", "url": "https://lyd.nrk.no/nrk_radio_alltid_nyheter_mp3_h"},
    {"id": "klassisk", "name": "NRK Klassisk", "url": "https://lyd.nrk.no/nrk_radio_klassisk_mp3_h"},
    {"id": "p4", "name": "P4", "url": "https://p4.p4groupaudio.com/P04_MM"},
    {"id": "norge", "name": "Radio Norge", "url": "https://live-bauerno.sharp-stream.com/radionorge_no_mp3"},
]


def load_config() -> dict:
    if OPTIONS_PATH.exists():  # kjører som Home Assistant-tillegg
        opts = json.loads(OPTIONS_PATH.read_text())
        cfg = {k: v for k, v in opts.items() if v not in (None, "", [])}
        cfg.setdefault("server_port", 8765)
        cfg.setdefault("location", {"name": "Oslo", "lat": 59.91, "lon": 10.75})
        if SUPERVISOR_TOKEN:
            cfg["home_assistant"] = {"url": "http://supervisor/core", "token": SUPERVISOR_TOKEN}
            try:  # bruk hjemmeposisjonen fra Home Assistant
                req = urllib.request.Request("http://supervisor/core/api/config",
                                             headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"})
                ha = json.loads(urllib.request.urlopen(req, timeout=10).read())
                cfg["ha_language"] = ha.get("language", "")
                if ha.get("latitude"):
                    cfg["location"] = {"name": ha.get("location_name") or "Hjemme",
                                       "lat": ha["latitude"], "lon": ha["longitude"]}
            except Exception as e:  # noqa: BLE001
                log.warning("Fant ikke hjemmeposisjon i Home Assistant: %s", e)
        return cfg
    path = CONFIG_PATH if CONFIG_PATH.exists() else HERE / "config.example.json"
    return json.loads(path.read_text())


def deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Settings:
    def __init__(self):
        self.lock = threading.Lock()
        saved = json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}
        self.data = deep_merge(DEFAULT_SETTINGS, saved)

    def get(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data))

    def update(self, patch: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        patch = {k: v for k, v in patch.items() if k in allowed}
        for key, valid in CHOICES.items():
            if key in patch and patch[key] not in valid:
                raise ValueError(f"Ugyldig {key}: {patch[key]}")
        if "accent" in patch and patch["accent"] and not str(patch["accent"]).startswith("#"):
            raise ValueError("accent må være en farge som #e8b04b")
        if "news_count" in patch:
            patch["news_count"] = max(1, min(8, int(patch["news_count"])))
        if "interval_seconds" in patch:
            patch["interval_seconds"] = max(30, int(patch["interval_seconds"]))
        if patch.get("location"):
            loc = patch["location"]
            patch["location"] = {"name": str(loc["name"])[:60], "lat": round(float(loc["lat"]), 4),
                                 "lon": round(float(loc["lon"]), 4)}
        for k, lo, hi in (("calendar_days", 1, 31), ("calendar_list_days", 1, 10)):
            if k in patch:
                patch[k] = max(lo, min(hi, int(patch[k])))
        patch.pop("calendars", None)  # endres bare via calendar_action
        patch.pop("tv", None)  # endres bare via select_tv
        if isinstance(patch.get("background"), dict):
            bg = patch["background"]
            if "source" in bg and bg["source"] not in BG_SOURCES:
                raise ValueError(f"Ukjent bildekilde: {bg['source']}")
            if "interval_minutes" in bg:
                bg["interval_minutes"] = max(5, min(1440, int(bg["interval_minutes"])))
            if "dim" in bg:
                bg["dim"] = max(0, min(95, int(bg["dim"])))
            if "offset" in bg:
                bg["offset"] = int(bg["offset"]) % 100000
            if "panels" in bg and bg["panels"] not in ("auto", "on", "off"):
                raise ValueError("panels må være auto, on eller off")
        if isinstance(patch.get("presence"), dict):
            pr = patch["presence"]
            if "entities" in pr:
                pr["entities"] = [str(e)[:120] for e in pr["entities"]
                                  if str(e).startswith(("binary_sensor.", "person.", "device_tracker."))][:6]
            if "off_minutes" in pr:
                pr["off_minutes"] = max(1, min(240, int(pr["off_minutes"])))
        if "camera_ids" in patch:
            ids = patch["camera_ids"]
            if not isinstance(ids, list):
                raise ValueError("camera_ids må være en liste")
            patch["camera_ids"] = [str(i)[:120] for i in ids][:4]
        with self.lock:
            if "camera_ids" in patch:  # replace, don't merge
                self.data["camera_ids"] = patch.pop("camera_ids")
            if isinstance(patch.get("presence"), dict) and "entities" in patch["presence"]:
                self.data.setdefault("presence", {})["entities"] = patch["presence"].pop("entities")
            if "location" in patch:  # replace, don't merge
                self.data["location"] = patch.pop("location")
            self.data = deep_merge(self.data, patch)
            self._save()
            return json.loads(json.dumps(self.data))

    def select_tv(self, tv: dict | None) -> None:
        with self.lock:
            self.data["tv"] = tv
            self._save()

    def _save(self) -> None:
        SETTINGS_PATH.write_text(json.dumps(self.data, indent=2, ensure_ascii=False))

    # ---- kalendere
    def calendars(self, cfg: dict) -> list[dict]:
        with self.lock:
            if self.data.get("calendars") is None:
                seed = [c for c in cfg.get("calendars", [])
                        if c.get("ics_url", "").startswith(("http://", "https://", "webcal://"))]
                self.data["calendars"] = [{"id": f"c{i + 1}", "name": c.get("name", "Kalender"),
                                           "color": c.get("color", "#e8b04b"), "ics_url": c["ics_url"],
                                           "visible": True} for i, c in enumerate(seed)]
                self._save()
            return json.loads(json.dumps(self.data["calendars"]))

    def calendar_action(self, cfg: dict, body: dict) -> list[dict]:
        cals = self.calendars(cfg)
        action = body.get("action")
        if action == "add":
            url = normalize_ics_url(body.get("ics_url", ""))
            color = body.get("color") or "#e8b04b"
            if not color.startswith("#"):
                raise ValueError("Ugyldig farge")
            new_id = "c" + str(int(time.time() * 1000))[-9:]
            cals.append({"id": new_id, "name": (body.get("name") or "Kalender").strip()[:40],
                         "color": color, "ics_url": url, "visible": True})
        elif action == "update":
            cal = next((c for c in cals if c["id"] == body.get("id")), None)
            if not cal:
                raise ValueError("Fant ikke kalenderen")
            for k in ("name", "color", "visible"):
                if k in body:
                    cal[k] = body[k] if k != "name" else str(body[k]).strip()[:40]
            if body.get("ics_url"):
                cal["ics_url"] = normalize_ics_url(body["ics_url"])
        elif action == "remove":
            cals = [c for c in cals if c["id"] != body.get("id")]
        elif action == "move":
            i = next((n for n, c in enumerate(cals) if c["id"] == body.get("id")), None)
            j = (i or 0) + (1 if body.get("dir") == "down" else -1)
            if i is not None and 0 <= j < len(cals):
                cals[i], cals[j] = cals[j], cals[i]
        else:
            raise ValueError("Ukjent handling")
        with self.lock:
            self.data["calendars"] = cals
            self._save()
        return cals


# ---------------------------------------------------------------- data sources

def http_get(url: str, timeout: int = 10, headers: dict | None = None) -> bytes:
    """GET with support for user:pass@host in the URL (basic auth)."""
    parts = urllib.parse.urlsplit(url)
    hdrs = {"User-Agent": "frame-dashboard/1.0"}
    hdrs.update(headers or {})
    if parts.username:
        cred = f"{urllib.parse.unquote(parts.username)}:{urllib.parse.unquote(parts.password or '')}"
        hdrs["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
        netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
        url = urllib.parse.urlunsplit(parts._replace(netloc=netloc))
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
        return r.read()


def geocode(q: str) -> list[dict]:
    url = ("https://geocoding-api.open-meteo.com/v1/search?count=8&language=no&name="
           + urllib.parse.quote(q))
    res = json.loads(http_get(url)).get("results") or []
    res.sort(key=lambda r: r.get("country_code") != "NO")  # norske steder først
    return [{"name": r["name"], "detail": ", ".join(x for x in (r.get("admin2"), r.get("admin1"), r.get("country")) if x),
             "lat": r["latitude"], "lon": r["longitude"]} for r in res]


def location_of(cfg: dict, settings: dict) -> dict | None:
    return settings.get("location") or cfg.get("location")


def get_weather(loc: dict | None) -> dict | None:
    if not loc:
        return None
    try:
        url = f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={loc['lat']}&lon={loc['lon']}"
        data = json.loads(http_get(url))
        series = data["properties"]["timeseries"]

        def point(p):
            d = p["data"]
            sym = (d.get("next_1_hours") or d.get("next_6_hours") or {}).get("summary", {}).get("symbol_code")
            t = dt.datetime.fromisoformat(p["time"].replace("Z", "+00:00")).astimezone()
            return {"temp": d["instant"]["details"]["air_temperature"], "symbol": sym, "hour": t.strftime("%H")}

        return {"now": point(series[0]), "next": [point(p) for p in series[3:16:3]]}
    except Exception as e:  # noqa: BLE001
        log.warning("Vær feilet: %s", e)
        return None


def normalize_ics_url(url: str) -> str:
    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    if not url.startswith(("http://", "https://")):
        raise ValueError("Lenken må starte med https:// eller webcal://")
    return url


_ics_cache: dict[str, tuple[float, object]] = {}


def fetch_calendar(url: str, max_age: int = 300):
    import icalendar
    url = normalize_ics_url(url)
    hit = _ics_cache.get(url)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    cal = icalendar.Calendar.from_ical(http_get(url, timeout=20))
    _ics_cache[url] = (time.time(), cal)
    return cal


def test_calendar(url: str) -> dict:
    """Check that an iCal link works; returns suggested name and upcoming event count."""
    import recurring_ical_events
    cal = fetch_calendar(url, max_age=0)
    today = dt.date.today()
    n = len(list(recurring_ical_events.of(cal).between(today, today + dt.timedelta(days=30))))
    name = str(cal.get("X-WR-CALNAME", "") or "").strip()
    return {"ok": True, "name": name, "upcoming": n}


def mask_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.netloc} …{url[-6:]}" if p.netloc else "…"


def get_events(calendars: list[dict], days_ahead: int = 7, list_days: int = 5, lang: str = "no") -> list[dict]:
    try:
        import recurring_ical_events
    except ImportError:
        log.warning("Mangler icalendar/recurring-ical-events – hopper over kalender")
        return []

    today = dt.date.today()
    now = dt.datetime.now()
    end = today + dt.timedelta(days=days_ahead)
    items = []
    for c in calendars:
        if not c.get("visible", True):
            continue
        try:
            cal = fetch_calendar(c["ics_url"])
            for ev in recurring_ical_events.of(cal).between(today, end):
                start = ev.get("DTSTART").dt
                all_day = not isinstance(start, dt.datetime)
                if not all_day:
                    start = start.astimezone().replace(tzinfo=None) if start.tzinfo else start
                    endv = ev.get("DTEND")
                    if endv and isinstance(endv.dt, dt.datetime):
                        e = endv.dt.astimezone().replace(tzinfo=None) if endv.dt.tzinfo else endv.dt
                        if e < now:
                            continue  # allerede ferdig
                items.append({
                    "date": start if all_day else start.date(),
                    "sort": "00:00" if all_day else start.strftime("%H:%M"),
                    "time": ("Hele dagen" if lang == "no" else "All day") if all_day else start.strftime("%H:%M"),
                    "title": str(ev.get("SUMMARY", "")),
                    "color": c.get("color", "#e8b04b"),
                    "who": c.get("name", ""),
                })
        except Exception as e:  # noqa: BLE001
            log.warning("Kalender %s feilet: %s", c.get("name"), e)

    items.sort(key=lambda x: (x["date"], x["sort"]))
    days: dict[dt.date, list] = {}
    for it in items:
        days.setdefault(it["date"], []).append(it)

    out = []
    for d, evs in list(days.items())[:list_days]:
        if d == today:
            label = "I dag" if lang == "no" else "Today"
        elif d == today + dt.timedelta(days=1):
            label = "I morgen" if lang == "no" else "Tomorrow"
        elif lang == "no":
            label = f"{DAYS_NO[d.weekday()]} {d.day}. {MONTHS_NO[d.month - 1]}"
        else:
            label = f"{DAYS_EN[d.weekday()]}, {MONTHS_EN[d.month - 1]} {d.day}"
        out.append({"label": label, "events": [{k: e[k] for k in ("time", "title", "color", "who")} for e in evs[:6]]})
    return out


_cam_backoff: dict[str, float] = {}


def camera_b64(cam: dict) -> str | None:
    url = cam.get("snapshot_url") or cam.get("rtsp_url") or cam.get("ha_entity") or ""
    if "PASSORD" in url or "BRUKER" in url:
        return None  # ikke konfigurert ennå – ikke bruk opp innloggingsforsøk
    if time.time() < _cam_backoff.get(url, 0):
        return None
    try:
        return base64.b64encode(get_camera(cam)).decode()
    except Exception as e:  # noqa: BLE001
        # Vent 10 min før nytt forsøk, så kameraet ikke låser kontoen ved feil passord
        _cam_backoff[url] = time.time() + 600
        log.warning("Kamera %s feilet (prøver igjen om 10 min): %s", cam.get("name"), e)
        return None


def ha_request(cfg: dict, path: str, timeout: int = 10) -> bytes:
    ha = cfg.get("home_assistant") or {}
    if not ha.get("url") or not ha.get("token"):
        raise RuntimeError("Ingen tilgang til Home Assistant")
    req = urllib.request.Request(f"{ha['url'].rstrip('/')}{path}",
                                 headers={"Authorization": f"Bearer {ha['token']}"})
    return urllib.request.urlopen(req, timeout=timeout).read()


_ha_cams: tuple[float, list[dict]] = (0.0, [])


def camera_options(cfg: dict) -> list[dict]:
    """All selectable cameras: from config plus camera.* entities in Home Assistant."""
    global _ha_cams
    opts = [{"id": f"cfg:{c.get('name')}", "name": c.get("name", "Kamera"), "source": "Oppsett"}
            for c in cfg.get("cameras", [])]
    if (cfg.get("home_assistant") or {}).get("token"):
        if time.time() - _ha_cams[0] > 300:
            try:
                states = json.loads(ha_request(cfg, "/api/states"))
                found = [{"id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"]),
                          "source": "Home Assistant"}
                         for s in states if s["entity_id"].startswith("camera.") and s["state"] != "unavailable"]
                found.sort(key=lambda c: c["name"].lower())
                _ha_cams = (time.time(), found)
            except Exception as e:  # noqa: BLE001
                log.warning("Kunne ikke hente kameraer fra Home Assistant: %s", e)
        opts += _ha_cams[1]
    return opts


def selected_cameras(cfg: dict, settings: dict) -> list[dict]:
    ids = settings.get("camera_ids")
    by_name = {f"cfg:{c.get('name')}": c for c in cfg.get("cameras", [])}
    if ids is None:
        return list(cfg.get("cameras", []))
    names = {o["id"]: o["name"] for o in camera_options(cfg)}
    out = []
    for i in ids:
        if i in by_name:
            out.append(by_name[i])
        elif i.startswith("camera."):
            out.append({"name": names.get(i, i), "ha_entity": i, "_cfg": cfg})
    return out


def get_camera(cam: dict) -> bytes:
    if cam.get("ha_entity"):
        data = ha_request(cam["_cfg"], f"/api/camera_proxy/{cam['ha_entity']}", timeout=12)
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError("Home Assistant ga ikke et JPEG-bilde")
        return data
    if cam.get("snapshot_url"):
        data = http_get(cam["snapshot_url"], timeout=8)
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError(f"Kameraet svarte ikke med et bilde: {data[:160]!r}")
        return data
    if cam.get("rtsp_url"):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg mangler for RTSP")
        r = subprocess.run([ffmpeg, "-loglevel", "error", "-rtsp_transport", "tcp", "-i", cam["rtsp_url"],
                            "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "-"],
                           capture_output=True, timeout=10)
        if r.returncode or not r.stdout:
            raise RuntimeError(r.stderr.decode()[:200])
        return r.stdout
    raise RuntimeError("Kamera mangler snapshot_url eller rtsp_url")


_news_cache: dict[str, tuple[float, list[str]]] = {}


def get_news(cfg: dict, settings: dict) -> dict | None:
    import xml.etree.ElementTree as ET
    feeds = cfg.get("news_feeds") or DEFAULT_NEWS_FEEDS
    feed = feeds[min(int(settings.get("news_source", 0)), len(feeds) - 1)]
    cached = _news_cache.get(feed["url"])
    if cached and time.time() - cached[0] < 300:
        titles = cached[1]
    else:
        try:
            root = ET.fromstring(http_get(feed["url"]))
            titles = [(i.findtext("title") or "").strip() for i in root.iter("item")]
            titles = [t for t in titles if t][:20]
            _news_cache[feed["url"]] = (time.time(), titles)
        except Exception as e:  # noqa: BLE001
            log.warning("Nyheter feilet: %s", e)
            titles = cached[1] if cached else []
    if not titles:
        return None
    # Rotate a little every update so the band acts like a slow ticker
    n = int(settings.get("news_count", 4))
    start = (int(time.time() // 60) * n) % len(titles)
    items = [titles[(start + i) % len(titles)] for i in range(min(n, len(titles)))]
    return {"source": feed["name"], "items": items}


BG_SOURCES = ("none", "kunst", "natur", "bing")
MIN_RATIO = 1.6  # 16:9 = 1.78 – litt slingringsmonn, bildet beskjæres minimalt
ART_QUERIES = ["landscape", "seascape", "mountains", "garden", "impressionism", "winter", "river", "flowers",
               "harbor", "lake", "valley", "coast", "sunset", "forest", "panorama", "view of"]
_bg_lists: dict[str, tuple[float, list[dict]]] = {}
_bg_current: dict[str, object] = {"key": None, "data": None, "credit": ""}


def _bg_list(source: str) -> list[dict]:
    """List of candidate images [{url, credit}] for a source (cached)."""
    hit = _bg_lists.get(source)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    items: list[dict] = []
    if source == "kunst":
        # Cleveland Museum of Art – åpent API, alle bilder CC0 (fri bruk)
        for q in ART_QUERIES:
            url = ("https://openaccess-api.clevelandart.org/api/artworks/?q=" + urllib.parse.quote(q) +
                   "&has_image=1&cc0=1&type=Painting&limit=100&fields=title,creators,images")
            data = json.loads(http_get(url, timeout=20))
            for art in data.get("data", []):
                imgs = art.get("images") or {}
                im = imgs.get("print") or imgs.get("web") or {}
                w, h = int(im.get("width") or 0), int(im.get("height") or 0)
                if im.get("url") and w >= h * MIN_RATIO:  # bare brede bilder som fyller 16:9-skjermen
                    who = ((art.get("creators") or [{}])[0].get("description") or "").split(" (")[0]
                    items.append({"url": im["url"], "credit": f"{art.get('title', '')} – {who or 'ukjent kunstner'}"})
        seen, uniq = set(), []
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"]); uniq.append(it)
        items = uniq
    elif source == "natur":
        for page in range(1, 11):
            for p in json.loads(http_get(f"https://picsum.photos/v2/list?page={page}&limit=100", timeout=15)):
                if p["width"] >= p["height"] * MIN_RATIO:
                    items.append({"url": f"https://picsum.photos/id/{p['id']}/1920/1080",
                                  "credit": f"Foto: {p['author']} / Unsplash"})
    elif source == "bing":
        data = json.loads(http_get("https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=8&mkt=nb-NO", timeout=15))
        for i in data.get("images", []):
            items.append({"url": "https://www.bing.com" + i["urlbase"] + "_UHD.jpg",
                          "credit": i.get("copyright", "")})
    _bg_lists[source] = (time.time(), items)
    return items


def image_brightness(raw: bytes) -> dict:
    """Average luminance (0–255) of the whole image and of the busiest/brightest region, via ffmpeg."""
    if not shutil.which("ffmpeg"):
        return {"mean": 128, "max": 128}
    try:
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vf", "scale=16:9,format=gray",
                            "-f", "rawvideo", "pipe:1"], input=raw, capture_output=True, timeout=30)
        px = list(r.stdout)
        if len(px) < 144:
            return {"mean": 128, "max": 128}
        # 3x3 blokker – den lyseste blokken avgjør, fordi tekst kan havne der
        blocks = []
        for by in range(3):
            for bx in range(3):
                vals = [px[y * 16 + x] for y in range(by * 3, by * 3 + 3) for x in range(bx * 5, min(16, bx * 5 + 6))]
                blocks.append(sum(vals) / len(vals))
        return {"mean": round(sum(px) / len(px)), "max": round(max(blocks))}
    except Exception:  # noqa: BLE001
        return {"mean": 128, "max": 128}


BG_FALLBACK = {"natur": ["bing", "kunst"], "kunst": ["bing", "natur"], "bing": ["natur", "kunst"]}
BG_RETRY = 600  # sekunder før en kilde/et bilde som feilet prøves igjen
_bg_failed: dict[str, float] = {}     # url eller "list:<kilde>" -> tidspunkt den kan prøves igjen
_bg_state = {"busy": False, "retry_key": None, "retry_at": 0.0}
_bg_lock = threading.Lock()


def _bg_slot(bg: dict) -> tuple:
    """Which image the settings ask for right now (source + time bucket + offset)."""
    bucket = int(time.time() // (60 * max(1, int(bg.get("interval_minutes", 30)))))
    return (bg.get("source", "none"), bucket, int(bg.get("offset", 0)))


def _bg_download(url: str) -> bytes:
    raw = http_get(url, timeout=15)
    if not raw.startswith(b"\xff\xd8"):
        raise RuntimeError("bildekilden ga ikke et JPEG-bilde")
    if len(raw) > 1_500_000 and shutil.which("ffmpeg"):  # skaler store bilder ned til 4K én gang
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vf", "scale='min(3840,iw)':-2",
                            "-q:v", "4", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                           input=raw, capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout.startswith(b"\xff\xd8"):
            raw = r.stdout
    return raw


def _bg_fetch(slot: tuple) -> None:
    """Fetch the image for a slot in the background. Tries the chosen source first, then the others,
    so a site that is down never leaves the TV without a background or delays the clock."""
    source, bucket, offset = slot
    errors = []
    try:
        for src in [source] + BG_FALLBACK.get(source, []):
            if _bg_failed.get("list:" + src, 0) > time.time():
                continue
            try:
                items = _bg_list(src)
            except Exception as e:  # noqa: BLE001
                _bg_failed["list:" + src] = time.time() + BG_RETRY
                errors.append(f"{src}: {e}")
                continue
            if not items:
                continue
            start = (bucket * 7919 + offset) % len(items)  # «tilfeldig» men stabil rekkefølge
            for k in range(3):  # prøv inntil tre bilder fra samme kilde
                it = items[(start + k) % len(items)]
                if _bg_failed.get(it["url"], 0) > time.time():
                    continue
                try:
                    raw = _bg_download(it["url"])
                except Exception as e:  # noqa: BLE001
                    _bg_failed[it["url"]] = time.time() + BG_RETRY
                    errors.append(f"{src}: {e}")
                    if "timed out" in str(e) or "timeout" in str(e).lower():
                        break  # hele kilden henger – gå videre til neste kilde
                    continue
                _bg_current.update(key=slot, data=base64.b64encode(raw).decode(), credit=it["credit"],
                                   brightness=image_brightness(raw))
                if src != source:
                    log.info("Bakgrunn: %s svarer ikke – bruker %s i mellomtiden", source, src)
                    _bg_state.update(retry_key=slot, retry_at=time.time() + BG_RETRY)
                else:
                    _bg_state.update(retry_key=None)
                return
        log.warning("Bakgrunnsbilde feilet (prøver igjen om %s min): %s", BG_RETRY // 60, "; ".join(errors)[:300])
        _bg_state.update(retry_key=slot, retry_at=time.time() + BG_RETRY)
    finally:
        _bg_state["busy"] = False


def get_background(settings: dict) -> dict | None:
    bg = settings.get("background") or {}
    if bg.get("source", "none") == "none":
        return None
    slot = _bg_slot(bg)
    want = _bg_current["key"] != slot or (_bg_state["retry_key"] == slot and time.time() > _bg_state["retry_at"])
    if _bg_state["retry_key"] == slot and time.time() <= _bg_state["retry_at"]:
        want = False  # nylig feilet / bruker reserve – ikke mas hvert minutt
    if want:
        with _bg_lock:
            if not _bg_state["busy"]:
                _bg_state["busy"] = True
                th = threading.Thread(target=_bg_fetch, args=(slot,), daemon=True)
                th.start()
                if not _bg_current["data"]:
                    th.join(20)  # første bilde etter oppstart: vent litt, ellers tegnes uten bakgrunn
    if not _bg_current["data"]:
        return None
    return {"image": _bg_current["data"], "credit": _bg_current["credit"], "dim": int(bg.get("dim", 55)),
            "brightness": _bg_current.get("brightness") or {"mean": 128, "max": 128},
            "panels": bg.get("panels", "auto")}


def public_settings(settings: dict) -> dict:
    s = dict(settings)
    s.pop("calendars", None)  # iCal-lenker er hemmelige
    return s


def public_calendars(cals: list[dict]) -> list[dict]:
    return [{**{k: c[k] for k in ("id", "name", "color", "visible")}, "url_hint": mask_url(c["ics_url"])} for c in cals]


def dashboard_data(cfg: dict, settings: dict, calendars: list[dict]) -> dict:
    show = settings["show"]
    layout = settings.get("layout", "klassisk")
    want_cams = show.get("cameras") and layout != "minimal"
    want_weather = show.get("weather") and layout != "minimal"
    want_cal = show.get("calendar") and layout != "minimal"
    cams = selected_cameras(cfg, settings) if want_cams else []
    if layout == "sentrert":
        cams = cams[:1]
    with ThreadPoolExecutor(max_workers=8) as pool:
        loc = location_of(cfg, settings)
        f_weather = pool.submit(get_weather, loc) if want_weather else None
        lang = lang_of(cfg, settings)
        f_events = pool.submit(get_events, calendars, settings.get("calendar_days", 7),
                               settings.get("calendar_list_days", 5), lang) if want_cal else None
        f_news = pool.submit(get_news, cfg, settings) if show.get("news") else None
        f_cams = [pool.submit(camera_b64, c) for c in cams]
        f_bg = pool.submit(get_background, settings)
        return {
            "lang": lang,
            "background": f_bg.result(),
            "settings": public_settings(settings),
            "location": (loc or {}).get("name"),
            "weather": f_weather.result() if f_weather else None,
            "events": f_events.result() if f_events else [],
            "calendar_legend": [{"name": c["name"], "color": c["color"]} for c in calendars if c.get("visible", True)]
                               if settings.get("show", {}).get("calendar_legend", True) else [],
            "news": f_news.result() if f_news else None,
            "cameras": [{"name": c.get("name", f"Kamera {i + 1}"), "image": f.result()}
                        for i, (c, f) in enumerate(zip(cams, f_cams))],
        } if not cfg.get("demo") else demo_data(cfg, settings, f_bg.result(),
                                                f_weather.result() if f_weather else None, loc)


def demo_data(cfg: dict, settings: dict, background, weather, loc) -> dict:
    """Example content for screenshots (no personal calendars, cameras or news)."""
    demo = cfg["demo"]
    layout = settings.get("layout", "klassisk")
    show = settings["show"]
    cams = []
    if show.get("cameras") and layout != "minimal" and demo.get("camera_image"):
        n = 1 if layout == "sentrert" else int(demo.get("camera_count", 1))
        cams = [{"name": nm, "image": demo["camera_image"]} for nm in (["Garden", "View"] if lang_of(cfg, settings) == "en" else ["Hage", "Utsikt"])[:n]]
    return {
        "lang": lang_of(cfg, settings),
        "background": background,
        "settings": public_settings(settings),
        "location": (loc or {}).get("name"),
        "weather": weather if show.get("weather") and layout != "minimal" else None,
        "events": demo.get("events", []) if show.get("calendar") and layout != "minimal" else [],
        "calendar_legend": demo.get("legend", []),
        "news": {"source": demo.get("news_source", "News"), "items": demo.get("news", [])} if show.get("news") else None,
        "cameras": cams,
    }


# ---------------------------------------------------------------- TV

def probe_tv(ip: str, timeout: float = 1.5) -> dict | None:
    """Ask a host if it is a Samsung TV (REST API on port 8001)."""
    try:
        info = json.loads(http_get(f"http://{ip}:8001/api/v2/", timeout=timeout))
        d = info.get("device") or {}
        if d.get("type") != "Samsung SmartTV" and "Samsung" not in str(info.get("type", "")):
            return None
        import html as _html
        return {"ip": d.get("ip") or ip, "mac": d.get("wifiMac") or "", "name": _html.unescape(d.get("name") or info.get("name") or "Samsung TV"),
                "model": d.get("modelName", ""), "frame": d.get("FrameTVSupport") == "true", "power": d.get("PowerState", "")}
    except Exception:  # noqa: BLE001
        return None


def local_subnets() -> list[str]:
    """All private /24 networks this machine is on (skips loopback and Docker/Supervisor nets)."""
    import re
    nets = []
    for cmd in (["ip", "-4", "-o", "addr"], ["ifconfig"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001
            continue
        for a, b, c in re.findall(r"inet (?:addr:)?(\d+)\.(\d+)\.(\d+)\.\d+", out):
            if a == "127" or (a == "172" and 16 <= int(b) <= 31):
                continue
            if a in ("10", "192") or a == "172":
                net = f"{a}.{b}.{c}"
                if net not in nets:
                    nets.append(net)
        if nets:
            break
    if not nets:
        nets = [local_ip().rsplit(".", 1)[0]]
    return nets[:4]


def discover_tvs() -> list[dict]:
    """Scan the local /24 network for Samsung TVs. Frame TVs first."""
    hosts = [f"{base}.{i}" for base in local_subnets() for i in range(1, 255)]

    def open_port(ip):
        try:
            with socket.create_connection((ip, 8001), timeout=0.6):
                return ip
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=64) as pool:
        candidates = [ip for ip in pool.map(open_port, hosts) if ip]
        found = [t for t in pool.map(probe_tv, candidates) if t]
    found.sort(key=lambda t: (not t["frame"], t["name"]))
    return found


class TV:
    """Serialised access to the TV plus a cached status snapshot."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.status: dict = {"online": False}

    def _conn(self):
        from samsungtvws import SamsungTVWS
        if not self.cfg.get("tv_ip"):
            raise RuntimeError("Ingen TV valgt – velg TV i styringen (fanen «TV og sensor»)")
        return SamsungTVWS(host=self.cfg["tv_ip"], port=8002, token_file=str(TOKEN_PATH),
                           timeout=15, name="FrameDashboard")

    def art(self):
        return self._conn().art()

    def power_state(self) -> str:
        if not self.cfg.get("tv_ip"):
            return "off"
        try:
            info = json.loads(http_get(f"http://{self.cfg['tv_ip']}:8001/api/v2/", timeout=3))
            return info["device"].get("PowerState", "on")
        except Exception:  # noqa: BLE001
            return "off"

    def refresh_status(self) -> dict:
        power = self.power_state()
        st: dict = {"online": power != "off", "power": power, "updated": time.time()}
        if st["online"]:
            try:
                with self.lock:
                    art = self.art()
                    st["art_mode"] = art.get_artmode() == "on"
                    raw = art.get_artmode_settings().get("data", "[]")
                    for item in json.loads(raw) if isinstance(raw, str) else raw:
                        st[item["item"]] = item["value"]
            except Exception as e:  # noqa: BLE001
                st["error"] = str(e)
        self.status = st
        return st

    def apply(self, cmd: dict) -> None:
        if cmd.get("power") is True:
            self.wake()
            return
        with self.lock:
            if cmd.get("power") is False:
                self._conn().hold_key("KEY_POWER", 3)
                return
            art = self.art()
            if "art_mode" in cmd:
                art.set_artmode(bool(cmd["art_mode"]))
            if "brightness" in cmd:
                art.set_brightness(int(cmd["brightness"]))
            if "color_temperature" in cmd:
                art.set_color_temperature(int(cmd["color_temperature"]))
            if "motion_timer" in cmd:
                art.set_motion_timer(str(cmd["motion_timer"]))
            if "motion_sensitivity" in cmd:
                art.set_motion_sensitivity(str(cmd["motion_sensitivity"]))
            if "brightness_sensor_setting" in cmd:
                art.set_brightness_sensor_setting(cmd["brightness_sensor_setting"] in (True, "on"))

    def wake(self) -> None:
        mac = self.cfg.get("tv_mac")
        if mac:
            pkt = bytes.fromhex("ff" * 6 + mac.replace(":", "").replace("-", "") * 16)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                for _ in range(3):
                    s.sendto(pkt, ("255.255.255.255", 9))
        if self.power_state() == "standby":
            with self.lock:
                self._conn().send_key("KEY_POWER")

    def push(self, image: Path, only_when_art_mode: bool, show_at: float | None = None) -> bool:
        """Upload the image, then show it at `show_at` (epoch seconds) so the clock flips on the minute."""
        with self.lock:
            art = self.art()
            if only_when_art_mode and art.get_artmode() != "on":
                return False
            matte = self.cfg.get("matte", "none")
            cid = art.upload(image.read_bytes(), file_type=image.suffix[1:], matte=matte, portrait_matte=matte)
            state = load_state()
            save_state({**state, "uploaded": state["uploaded"] + [cid]})  # husk den, så den ryddes uansett
            self.uploaded_at = time.time()
        if show_at:
            delay = show_at - time.time()
            if 0 < delay < 60:
                time.sleep(delay)
        with self.lock:
            art = self.art()
            art.select_image(cid, show=True)
            state = load_state()
            old = [c for c in state["uploaded"] if c != cid]
            keep, fails = self._delete_each(art, old, state.get("fails", {}))
            save_state({"uploaded": keep + [cid], "fails": fails})
            late = time.time() - show_at if show_at else 0
            log.info("Viser %s%s", cid, f" ({late:+.0f} s)" if show_at and abs(late) >= 3 else "")
            return True

    @staticmethod
    def _delete_each(art, ids: list[str], fails: dict) -> tuple[list[str], dict]:
        """Delete our old dashboard images one at a time, so one bad id can't block the rest.
        Ids the TV keeps refusing (e.g. already gone) are dropped after 3 attempts."""
        keep, new_fails = [], {}
        for c in ids:
            try:
                art.delete(c)
            except Exception as e:  # noqa: BLE001
                n = int(fails.get(c, 0)) + 1
                if n < 3:
                    keep.append(c)
                    new_fails[c] = n
                else:
                    log.info("Gir opp å slette %s etter %s forsøk: %s", c, n, e)
        return keep, new_fails

    def cleanup_uploads(self) -> dict:
        """Delete every dashboard image this add-on has uploaded, except the one showing now."""
        with self.lock:
            art = self.art()
            current = (art.get_current() or {}).get("content_id")
            state = load_state()
            tracked = set(state.get("uploaded", []))
            # bilder i «Mine bilder» som følger tilleggets mønster og er eldre enn det som vises
            ours = [a["content_id"] for a in art.available("MY-C0002") if a.get("content_id") in tracked]
            targets = [c for c in ours if c != current]
            keep, fails = self._delete_each(art, targets, {})
            save_state({"uploaded": [c for c in [current] if c in tracked] + keep, "fails": fails})
            return {"deleted": len(targets) - len(keep), "failed": len(keep)}


class Radio:
    """Plays radio streams on a Home Assistant media player, the TV (DLNA) or this computer."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.stations = cfg.get("radio_stations") or DEFAULT_RADIO
        default = [{"id": "local", "name": "Denne maskinen", "type": "local"}]
        if SUPERVISOR_TOKEN:
            default = [{"id": "tv", "name": "TV-en", "type": "dlna"}]
        self.static_speakers = cfg.get("speakers") or default
        self.local_proc: subprocess.Popen | None = None
        self.playing: dict[str, str] = {}   # speaker id -> station id
        self._ha_speakers: tuple[float, list[dict]] = (0.0, [])
        self.tv: "TV | None" = None

    @property
    def speakers(self) -> list[dict]:
        return self.static_speakers + self.ha_speakers()

    def ha_speakers(self) -> list[dict]:
        """All media_player entities in Home Assistant (cached 5 min)."""
        ha = self.cfg.get("home_assistant") or {}
        if not ha.get("token") or not self.cfg.get("auto_ha_speakers", True):
            return []
        if time.time() - self._ha_speakers[0] < 300:
            return self._ha_speakers[1]
        try:
            req = urllib.request.Request(f"{ha['url'].rstrip('/')}/api/states",
                                         headers={"Authorization": f"Bearer {ha['token']}"})
            states = json.loads(urllib.request.urlopen(req, timeout=10).read())
            # Hopp over TV-ens egne entiteter – «TV-en» styres direkte (Samsung krever egen metadata)
            found = [{"id": s["entity_id"], "type": "ha", "entity_id": s["entity_id"],
                      "name": s["attributes"].get("friendly_name", s["entity_id"])}
                     for s in states if s["entity_id"].startswith("media_player.") and s["state"] != "unavailable"
                     and not any(k in s["entity_id"] for k in ("frame", "ls03f"))]  # skjuler TV-en og Music Frame (Arnis kontor)
            found.sort(key=lambda s: s["name"].lower())
            self._ha_speakers = (time.time(), found)
        except Exception as e:  # noqa: BLE001
            log.warning("Kunne ikke hente høyttalere fra Home Assistant: %s", e)
        return self._ha_speakers[1]

    def public(self) -> dict:
        return {"stations": [{"id": s["id"], "name": s["name"]} for s in self.stations],
                "speakers": [{"id": s["id"], "name": s["name"], "type": s.get("type", "local")} for s in self.speakers],
                "playing": self.playing}

    def _speaker(self, sid: str | None) -> dict:
        for s in self.speakers:
            if s["id"] == sid or sid is None:
                return s
        raise ValueError(f"Ukjent høyttaler: {sid}")

    @staticmethod
    def _resolve(url: str) -> str:
        """Follow redirects (NRK) so simple players get the final stream URL."""
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "frame-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.geturl()
        except Exception:  # noqa: BLE001
            return url

    def play(self, station_id: str, speaker_id: str | None) -> None:
        st = next((s for s in self.stations if s["id"] == station_id), None)
        if not st:
            raise ValueError(f"Ukjent kanal: {station_id}")
        sp = self._speaker(speaker_id)
        kind = sp.get("type", "local")
        if kind in ("ha", "dlna"):
            # TV-er og mange høyttalere takler ikke https/omdirigering – send via vår egen http-proxy
            url = f"http://{local_ip()}:{self.cfg.get('server_port', 8765)}/stream/{st['id']}.mp3"
        else:
            url = self._resolve(st["url"])
        if kind == "local":
            self._stop_local()
            player = shutil.which("ffplay") or shutil.which("mpv")
            if not player:
                raise RuntimeError("Fant verken ffplay eller mpv")
            args = [player, "-nodisp", "-loglevel", "quiet", url] if "ffplay" in player else [player, "--no-video", url]
            self.local_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif kind == "ha":
            self._ha("play_media", sp["entity_id"], media_content_id=url, media_content_type="music")
        elif kind == "dlna":
            if self.tv is not None:
                try:  # mediaspilleren svarer best når TV-en står i vanlig TV-modus
                    if self.tv.status.get("art_mode"):
                        self.tv.apply({"art_mode": False})
                        time.sleep(3)
                except Exception as e:  # noqa: BLE001
                    log.warning("Kunne ikke gå ut av Art Mode: %s", e)
            import html as _html
            didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
                    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
                    f'<item id="0" parentID="-1" restricted="1"><dc:title>{_html.escape(st["name"])}</dc:title>'
                    '<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>'
                    '<res protocolInfo="http-get:*:audio/mpeg:DLNA.ORG_PN=MP3;DLNA.ORG_OP=00;DLNA.ORG_CI=0;'
                    f'DLNA.ORG_FLAGS=01700000000000000000000000000000">{_html.escape(url)}</res></item></DIDL-Lite>')
            self._dlna(sp, "SetAVTransportURI",
                       f"<CurrentURI>{_html.escape(url)}</CurrentURI><CurrentURIMetaData>{_html.escape(didl)}</CurrentURIMetaData>")
            self._dlna(sp, "Play", "<Speed>1</Speed>")
        else:
            raise ValueError(f"Ukjent høyttalertype: {kind}")
        self.playing[sp["id"]] = station_id

    def stop(self, speaker_id: str | None) -> None:
        sp = self._speaker(speaker_id)
        kind = sp.get("type", "local")
        if kind == "local":
            self._stop_local()
        elif kind == "ha":
            self._ha("media_stop", sp["entity_id"])
        elif kind == "dlna":
            self._dlna(sp, "Stop", "")
        self.playing.pop(sp["id"], None)

    def volume(self, speaker_id: str | None, level: float) -> None:
        sp = self._speaker(speaker_id)
        if sp.get("type") == "ha":
            self._ha("volume_set", sp["entity_id"], volume_level=max(0.0, min(1.0, float(level))))
        else:
            raise ValueError("Volum støttes bare for Home Assistant-høyttalere")

    def _stop_local(self) -> None:
        if self.local_proc and self.local_proc.poll() is None:
            self.local_proc.terminate()
        self.local_proc = None

    def _ha(self, service: str, entity_id: str, **data) -> None:
        ha = self.cfg.get("home_assistant") or {}
        if not ha.get("url") or not ha.get("token"):
            raise RuntimeError("home_assistant.url og token mangler i config.json")
        body = json.dumps({"entity_id": entity_id, **data}).encode()
        req = urllib.request.Request(f"{ha['url'].rstrip('/')}/api/services/media_player/{service}", data=body,
                                     headers={"Authorization": f"Bearer {ha['token']}",
                                              "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()

    def _dlna(self, sp: dict, action: str, args: str) -> None:
        url = sp.get("control_url") or f"http://{self.cfg['tv_ip']}:9197/upnp/control/AVTransport1"
        svc = "urn:schemas-upnp-org:service:AVTransport:1"
        body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{svc}"><InstanceID>0</InstanceID>{args}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(url, data=body.encode(), headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{svc}#{action}"'})
        try:
            urllib.request.urlopen(req, timeout=8).read()
        except OSError as e:
            raise RuntimeError(f"TV-ens mediaspiller svarer ikke (skjermen må være på): {e}") from e


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"uploaded": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state))


# ---------------------------------------------------------------- web server

class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.settings = Settings()
        chosen = self.settings.get().get("tv")
        if chosen and chosen.get("ip"):
            cfg["tv_ip"] = chosen["ip"]
            if chosen.get("mac"):
                cfg["tv_mac"] = chosen["mac"]
        self.tv = TV(cfg)
        self.radio = Radio(cfg)
        self.radio.tv = self.tv
        self.wakeup = threading.Event()
        self.last_push: float | None = None
        self.last_error: str | None = None


MAX_BODY = 1_000_000
TRUSTED_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "172.30.32.0/23")]
_own_ips: set[str] = set()


def is_trusted_client(ip: str) -> bool:
    """Loopback, the Supervisor/ingress network, or this machine itself."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if any(addr in n for n in TRUSTED_NETS if n.version == addr.version):
        return True
    if not _own_ips:
        _own_ips.add(local_ip())
    return str(addr) in _own_ips


def make_handler(app: App):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_bytes(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, obj, code: int = 200):
            self.send_bytes(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

        def read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("Forespørselen er for stor")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def allowed(self, path: str) -> bool:
            """Only Home Assistant itself (ingress panel, automations on 127.0.0.1) may use the API.
            The radio stream is open so the TV can fetch it. Optional token for LAN access."""
            if path.startswith("/stream/"):
                return True
            if is_trusted_client(self.client_address[0]):
                return True
            token = str(app.cfg.get("api_token") or "")
            if token:
                auth = self.headers.get("Authorization", "")
                given = auth[7:] if auth.startswith("Bearer ") else self.headers.get("X-Frame-Token", "")
                if hmac.compare_digest(given, token):
                    return True
            if not SUPERVISOR_TOKEN and app.cfg.get("allow_lan"):
                return True  # utviklingsmodus utenfor Home Assistant
            log.warning("Avviste %s %s fra %s (ikke Home Assistant)", self.command, path, self.client_address[0])
            self.send_bytes(b"Forbidden - use the Frame Canvas panel in Home Assistant", "text/plain", 403)
            return False

        def do_OPTIONS(self):
            self.send_bytes(b"", "text/plain", 405)

        def stream_radio(self, sid: str):
            st = next((s for s in app.radio.stations if s["id"] == sid), None)
            if not st:
                return self.send_bytes(b"ukjent kanal", "text/plain", 404)
            try:
                up = urllib.request.urlopen(urllib.request.Request(
                    st["url"], headers={"User-Agent": "frame-dashboard/1.0"}), timeout=15)
            except Exception as e:  # noqa: BLE001
                log.warning("Radio %s feilet: %s", sid, e)
                return self.send_bytes(b"", "text/plain", 502)
            log.info("Radio %s %s fra %s", sid, self.command, self.client_address[0])
            # Samsung-TV-er avviser direktestrømmer uten lengde – oppgi en stor «falsk» lengde
            self.protocol_version = "HTTP/1.1"
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", "2000000000")
            self.send_header("Accept-Ranges", "none")
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("contentFeatures.dlna.org",
                             "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000")
            self.end_headers()
            if self.command == "HEAD":
                up.close()
                return
            try:
                while True:
                    chunk = up.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # lytteren stoppet
            finally:
                up.close()

        def do_HEAD(self):
            path = urllib.parse.urlsplit(self.path).path
            if not self.allowed(path):
                return
            if path.startswith("/stream/"):
                return self.stream_radio(path.rsplit("/", 1)[1].removesuffix(".mp3"))
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if not self.allowed(path):
                return
            if path.startswith("/stream/"):
                return self.stream_radio(path.rsplit("/", 1)[1].removesuffix(".mp3"))
            if path in ("/", "/index.html", "/dashboard"):
                return self.send_bytes((HERE / "dashboard.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/control":
                return self.send_bytes((HERE / "control.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/preview.jpg":
                if PREVIEW_PATH.exists():
                    return self.send_bytes(PREVIEW_PATH.read_bytes(), "image/jpeg")
                return self.send_bytes(b"", "text/plain", 404)
            if path == "/api/tvs/discover":
                return self.send_json(discover_tvs())
            if path == "/api/geocode":
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("q", [""])[0].strip()
                try:
                    return self.send_json(geocode(q) if len(q) >= 2 else [])
                except Exception as e:  # noqa: BLE001
                    return self.send_json({"error": str(e)}, 502)
            if path == "/api/data":
                return self.send_json(dashboard_data(app.cfg, app.settings.get(), app.settings.calendars(app.cfg)))
            if path == "/api/status":
                return self.send_json({
                    "settings": public_settings(app.settings.get()),
                    "calendar_list": public_calendars(app.settings.calendars(app.cfg)),
                    "choices": CHOICES,
                    "lang": lang_of(app.cfg, app.settings.get()),
                    "tv_info": {"ip": app.cfg.get("tv_ip"), "mac": app.cfg.get("tv_mac"),
                                **(app.settings.get().get("tv") or {})},
                    "location": location_of(app.cfg, app.settings.get()),
                    "news_feeds": [f["name"] for f in (app.cfg.get("news_feeds") or DEFAULT_NEWS_FEEDS)],
                    "radio": app.radio.public(),
                    "tv": app.tv.status,
                    "last_push": app.last_push,
                    "last_error": app.last_error,
                    "camera_options": camera_options(app.cfg),
                    "presence_options": presence_options(app.cfg),
                    "presence_state": getattr(app, "presence_state", None),
                    "camera_selected": [f"cfg:{c['name']}" if not c.get("ha_entity") else c["ha_entity"]
                                        for c in selected_cameras(app.cfg, app.settings.get())],
                })
            return self.send_bytes(b"not found", "text/plain", 404)

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if not self.allowed(path):
                return
            try:
                body = self.read_json()
                if path == "/api/settings":
                    s = app.settings.update(body)
                    app.wakeup.set()
                    return self.send_json({"ok": True, "settings": public_settings(s)})
                if path == "/api/calendars/test":
                    try:
                        return self.send_json(test_calendar(body.get("ics_url", "")))
                    except Exception as e:  # noqa: BLE001
                        return self.send_json({"ok": False, "error": f"Kunne ikke lese kalenderen: {e}"}, 400)
                if path == "/api/calendars":
                    cals = app.settings.calendar_action(app.cfg, body)
                    app.wakeup.set()
                    return self.send_json({"ok": True, "calendar_list": public_calendars(cals)})
                if path == "/api/tv":
                    log.info("TV-kommando fra %s: %s", self.client_address[0], body)
                    app.tv.apply(body)
                    threading.Thread(target=lambda: (time.sleep(2), app.tv.refresh_status()), daemon=True).start()
                    if body.get("art_mode") is True:
                        app.wakeup.set()
                    return self.send_json({"ok": True})
                if path == "/api/tv/select":
                    ip = str(body.get("ip", "")).strip()
                    tv = probe_tv(ip, timeout=4) if ip else None
                    if not tv:
                        raise ValueError(f"Fant ingen Samsung-TV på {ip}")
                    if ip != app.cfg.get("tv_ip") and TOKEN_PATH.exists():
                        TOKEN_PATH.unlink()  # ny TV – ny tilgang
                    app.cfg["tv_ip"] = tv["ip"]
                    if tv.get("mac"):
                        app.cfg["tv_mac"] = tv["mac"]
                    app.settings.select_tv({k: tv[k] for k in ("ip", "mac", "name", "model")})
                    save_state({"uploaded": []})
                    log.info("Valgte TV: %s (%s, %s)", tv["name"], tv["model"], tv["ip"])
                    threading.Thread(target=app.tv.refresh_status, daemon=True).start()
                    app.wakeup.set()
                    return self.send_json({"ok": True, "tv": tv})
                if path == "/api/tv/cleanup":
                    res = app.tv.cleanup_uploads()
                    log.info("Opprydding: slettet %s gamle dashboard-bilder (%s feilet)", res["deleted"], res["failed"])
                    return self.send_json({"ok": True, **res})
                if path == "/api/radio":
                    sp = body.get("speaker")
                    if body.get("stop"):
                        app.radio.stop(sp)
                    elif "volume" in body:
                        app.radio.volume(sp, body["volume"])
                    else:
                        app.radio.play(body["station"], sp)
                    return self.send_json({"ok": True, "radio": app.radio.public()})
                if path == "/api/refresh":
                    app.wakeup.set()
                    return self.send_json({"ok": True})
            except Exception as e:  # noqa: BLE001
                log.warning("API-feil: %s", e)
                return self.send_json({"ok": False, "error": str(e)}, 400)
            return self.send_json({"ok": False, "error": "ukjent"}, 404)
    return Handler


def start_server(app: App, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, app.cfg.get("server_port", 8765)), make_handler(app))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------- rendering

def find_chrome() -> str:
    for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
              shutil.which("google-chrome") or ""]:
        if p and Path(p).exists():
            return p
    raise RuntimeError("Fant ikke Chrome/Chromium")


class PersistentBrowser:
    """One long-lived headless Chromium driven over the DevTools protocol.

    Starting Chromium every minute is the most CPU-hungry thing the add-on does; keeping one
    instance alive and just reloading the page + capturing a JPEG costs a fraction of that.
    """

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.port = 9333
        self.tmp = tempfile.mkdtemp(prefix="frame-chrome-")
        self.lock = threading.Lock()

    def _start(self) -> None:
        extra = ["--no-sandbox", "--disable-dev-shm-usage"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        self.proc = subprocess.Popen(
            [find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio", *extra,
             "--no-first-run", "--disable-extensions", "--disable-background-networking",
             "--disable-component-update", "--disable-sync", "--renderer-process-limit=1",
             f"--user-data-dir={self.tmp}", "--force-device-scale-factor=1", "--window-size=3840,2160",
             "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={self.port}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                http_get(f"http://127.0.0.1:{self.port}/json/version", timeout=1)
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        raise RuntimeError("Chromium startet ikke")

    def _page_ws(self) -> str:
        targets = json.loads(http_get(f"http://127.0.0.1:{self.port}/json/list", timeout=3))
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")
            pages = [json.loads(urllib.request.urlopen(req, timeout=3).read())]
        return pages[0]["webSocketDebuggerUrl"]

    def capture(self, url: str, timeout: float = 30) -> bytes:
        import websocket
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            ws = websocket.create_connection(self._page_ws(), timeout=timeout, suppress_origin=True)
            try:
                seq = [0]

                def call(method, **params):
                    seq[0] += 1
                    ws.send(json.dumps({"id": seq[0], "method": method, "params": params}))
                    while True:
                        msg = json.loads(ws.recv())
                        if msg.get("id") == seq[0]:
                            if "error" in msg:
                                raise RuntimeError(f"{method}: {msg['error']}")
                            return msg.get("result", {})

                call("Emulation.setDeviceMetricsOverride", width=3840, height=2160, deviceScaleFactor=1, mobile=False)
                call("Page.navigate", url=url)
                deadline = time.time() + timeout
                while time.time() < deadline:  # vent til dashboardet har lastet data og bilder
                    r = call("Runtime.evaluate", expression="document.body && document.body.dataset.ready", returnByValue=True)
                    if r.get("result", {}).get("value") == "1":
                        break
                    time.sleep(0.2)
                shot = call("Page.captureScreenshot", format="jpeg", quality=92, captureBeyondViewport=False)
                call("Page.navigate", url="about:blank")  # slipp minnet fra den store siden
                return base64.b64decode(shot["data"])
            finally:
                ws.close()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


_browser: PersistentBrowser | None = None


def render(cfg: dict, out: Path, at: float | None = None) -> Path:
    """Render the dashboard to a 4K JPEG – via the persistent browser, falling back to a one-off Chromium.
    `at` (epoch seconds) is the moment the image will be shown; the clock on the page uses it."""
    global _browser
    url = f"http://127.0.0.1:{cfg.get('server_port', 8765)}/" + (f"?t={int(at * 1000)}" if at else "")
    if out.suffix.lower() in (".jpg", ".jpeg") and not cfg.get("oneshot_browser"):
        try:
            if _browser is None:
                _browser = PersistentBrowser()
            data = _browser.capture(url)
            if data.startswith(b"\xff\xd8"):
                tmp_out = out.with_name(out.stem + ".tmp" + out.suffix)
                tmp_out.write_bytes(data)
                tmp_out.replace(out)
                return out
        except Exception as e:  # noqa: BLE001
            log.warning("Vedvarende nettleser feilet (%s) – bruker engangs-Chromium", e)
            if _browser:
                _browser.stop()
            _browser = None
    return render_oneshot(cfg, out, url)


def render_oneshot(cfg: dict, out: Path, url: str) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "shot.png"
        extra = ["--no-sandbox", "--disable-dev-shm-usage"] if hasattr(os, "geteuid") and os.geteuid() == 0 else []
        subprocess.run([find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", *extra,
                        f"--user-data-dir={tmp}/profile", "--force-device-scale-factor=1",
                        "--window-size=3840,2160", "--virtual-time-budget=20000",
                        f"--screenshot={png}", url],
                       check=True, capture_output=True, timeout=90)
        if out.suffix.lower() in (".jpg", ".jpeg") and shutil.which("ffmpeg"):
            tmp_out = out.with_name(out.stem + ".tmp" + out.suffix)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(png), "-q:v", "2", str(tmp_out)], check=True)
            tmp_out.replace(out)
        else:
            out = out.with_suffix(".png")
            shutil.copy(png, out)
    return out


# ---------------------------------------------------------------- main loop

PRESENCE_CLASSES = ("occupancy", "presence", "motion")
_presence_opts: tuple[float, list[dict]] = (0.0, [])


def presence_options(cfg: dict) -> list[dict]:
    """Binary sensors in Home Assistant that can tell if someone is in the room."""
    global _presence_opts
    if not (cfg.get("home_assistant") or {}).get("token"):
        return []
    if time.time() - _presence_opts[0] > 60:
        try:
            states = json.loads(ha_request(cfg, "/api/states"))
            found = [{"id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"]),
                      "state": s["state"], "kind": s["attributes"].get("device_class")}
                     for s in states if s["entity_id"].startswith("binary_sensor.")
                     and s["attributes"].get("device_class") in PRESENCE_CLASSES and s["state"] != "unavailable"]
            people = [{"id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"]),
                       "state": s["state"], "kind": "person" if s["entity_id"].startswith("person.") else "telefon"}
                      for s in states if s["entity_id"].startswith(("person.", "device_tracker."))
                      and (s["entity_id"].startswith("person.") or s["attributes"].get("source_type") == "gps")]
            found.sort(key=lambda o: o["name"].lower())
            people.sort(key=lambda o: o["name"].lower())
            found = people + found
            _presence_opts = (time.time(), found)
        except Exception as e:  # noqa: BLE001
            log.warning("Kunne ikke hente sensorer fra Home Assistant: %s", e)
    return _presence_opts[1]


def presence_loop(app: App) -> None:
    """Turn the TV on (Art Mode) when someone arrives, off once the room/home has been empty a while.

    Edge-triggered: the TV is only turned on at the moment presence goes from absent to present, and
    only turned off once per empty period – so it never fights someone using the remote.
    """
    last_seen = time.time()
    was_present = None
    turned_off = False
    while True:
        time.sleep(5)
        try:
            pr = app.settings.get().get("presence") or {}
            ents = pr.get("entities") or []
            if not pr.get("enabled") or not ents:
                was_present = None
                continue
            present = False
            for e in ents:
                st = json.loads(ha_request(app.cfg, f"/api/states/{e}", timeout=5))
                if st.get("state") in ("on", "home"):
                    present = True
            now = time.time()
            if present:
                last_seen = now
                turned_off = False
            app.presence_state = {"present": present, "last_seen": last_seen}
            arrived = present and was_present is False
            if present != was_present:
                log.info("Tilstedeværelse: %s", "noen er her" if present else "tomt")
                was_present = present
            if arrived and pr.get("turn_on", True) and app.tv.power_state() != "on":
                log.info("Tilstedeværelse: noen kom – slår på TV-en (Art Mode)")
                app.tv.wake()
                app.wakeup.set()
            elif (not present and not turned_off and pr.get("turn_off", True)
                  and now - last_seen >= 60 * int(pr.get("off_minutes", 15))):
                turned_off = True  # bare ett forsøk per tomme periode
                if app.tv.power_state() == "on" and app.tv.status.get("art_mode"):
                    log.info("Tilstedeværelse: tomt i %s min – slår av TV-en", pr.get("off_minutes"))
                    with app.tv.lock:
                        app.tv._conn().send_key("KEY_POWER")
        except Exception as e:  # noqa: BLE001
            log.warning("Tilstedeværelse feilet: %s", e)
            time.sleep(25)


def status_loop(app: App) -> None:
    while True:
        try:
            app.tv.refresh_status()
        except Exception as e:  # noqa: BLE001
            log.warning("Status feilet: %s", e)
        time.sleep(30)


def cmd_run(cfg: dict) -> None:
    app = App(cfg)
    start_server(app)
    threading.Thread(target=status_loop, args=(app,), daemon=True).start()
    threading.Thread(target=presence_loop, args=(app,), daemon=True).start()
    port = cfg.get("server_port", 8765)
    log.info("Styringsside: http://%s:%s/control", local_ip(), port)
    lead = 20.0  # sekunder før minuttskiftet vi starter; justeres etter hvor lang tid en runde faktisk tar
    while True:
        s = app.settings.get()
        interval = int(s.get("interval_seconds", 60))
        aligned = interval % 60 == 0
        if s.get("enabled", True):
            t0 = time.time()
            # Bildet skal vise minuttet det står på skjermen i: lages litt før, vises på minuttet
            show_at = (t0 // 60 + (1 if 60 - t0 % 60 <= lead + 5 else 0)) * 60 if aligned else None
            try:
                img = render(cfg, PREVIEW_PATH, show_at)
                # Bare når TV-en er helt på – et bildebytte i standby vekker TV-en, som så slår seg av igjen
                power = app.tv.power_state()
                if power == "on":
                    if app.tv.push(img, cfg.get("only_when_art_mode", True), show_at):
                        app.last_push = time.time()
                        if show_at and getattr(app.tv, "uploaded_at", 0) > t0:
                            # tid fra start til bildet lå klart på TV-en, + litt margin
                            lead = min(45.0, max(10.0, app.tv.uploaded_at - t0 + 5))
                if power != getattr(app, "_last_power", None):
                    log.info("TV-ens strømstatus: %s%s", power, " – sender ikke bilder" if power != "on" else "")
                    app._last_power = power
                app.last_error = None
            except Exception as e:  # noqa: BLE001
                app.last_error = str(e)
                log.warning("Feil: %s", e)
        if aligned:
            nxt = (time.time() // 60 + interval // 60) * 60  # neste visningsminutt
            if nxt - time.time() < lead:
                nxt += 60
            wait = nxt - lead - time.time()
        else:
            wait = interval
        app.wakeup.wait(max(5, wait))
        app.wakeup.clear()


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        cmd_run(cfg)
    elif cmd == "render":
        start_server(App(cfg), host="127.0.0.1")
        print(render(cfg, HERE / "dashboard.png"))
    elif cmd == "status":
        print(json.dumps(TV(cfg).refresh_status(), indent=2))
    elif cmd == "cleanup":
        state = load_state()
        if state["uploaded"]:
            TV(cfg).art().delete_list(state["uploaded"])
        save_state({"uploaded": []})
        print("Slettet", len(state["uploaded"]), "bilder")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
