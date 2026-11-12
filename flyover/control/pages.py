"""HTML for the control service. Plain strings, no template engine.

Every dynamic value goes through ``esc`` so a hostile network name cannot
inject markup into the setup page.
"""

from __future__ import annotations

import html
from typing import Any

from ..config import Config
from .nm import WifiNetwork

CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1c1e22;--muted:#6a707a;--line:#d9dce1;--card:#f4f5f7;--accent:#0a6cff;--bad:#c62828;--good:#1b7f3b}
@media (prefers-color-scheme:dark){:root{--bg:#121316;--fg:#e8e9ec;--muted:#9096a0;--line:#2c2f36;--card:#1b1d22;--accent:#5b9dff;--bad:#ff6b6b;--good:#5ad17f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
main{max-width:34rem;margin:0 auto;padding:1rem 16px 3rem}h1{font-size:1.4rem;margin:.5rem 0 1rem}h2{font-size:1.05rem;margin:1.6rem 0 .5rem}
nav{display:flex;gap:1rem;margin-bottom:.5rem}nav a{color:var(--accent);text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem;margin:.75rem 0}
label{display:block;font-weight:600;margin:.8rem 0 .2rem}small,.hint{color:var(--muted);font-weight:400}
input,select{width:100%;padding:.6rem;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
input[type=checkbox]{width:auto;margin-right:.5rem}.row{display:flex;gap:.75rem}.row>div{flex:1}
button{margin-top:1rem;padding:.7rem 1.2rem;border:0;border-radius:8px;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button.secondary{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.msg{padding:.7rem 1rem;border-radius:8px;margin:.75rem 0}.ok{border:1px solid var(--good);color:var(--good)}.err{border:1px solid var(--bad);color:var(--bad)}
table{width:100%;border-collapse:collapse}td{padding:.3rem 0;border-bottom:1px solid var(--line)}td:last-child{text-align:right}
img.preview{display:block;width:100%;max-width:320px;aspect-ratio:1;margin:0 auto;image-rendering:pixelated;background:#000;border-radius:6px}
"""

POLL_JS = """
async function tick(){try{const r=await fetch('/api/status');const s=await r.json();
for(const [k,v] of Object.entries(s.flat)){const e=document.getElementById('s-'+k);if(e)e.textContent=v}
document.getElementById('frame').src='/frame.png?'+Date.now()}catch(e){}}
setInterval(tick,2000);
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, *, nav: bool = True, script: str = "") -> str:
    links = ('<nav><a href="/">Status</a><a href="/settings">Settings</a>'
             '<a href="/wifi">Wi-Fi</a></nav>') if nav else ""
    return (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{CSS}</style><main>{links}<h1>{esc(title)}</h1>{body}"
        f"</main>{f'<script>{script}</script>' if script else ''}</html>"
    )


def message(text: str | None, error: bool = False) -> str:
    return f'<p class="msg {"err" if error else "ok"}">{esc(text)}</p>' if text else ""


# -- status ----------------------------------------------------------------------------------


def flat_status(status: dict | None, net: dict | None) -> dict[str, str]:
    """The values shown on the status page, as display strings."""
    status, net = status or {}, net or {}
    weather, flights = status.get("weather") or {}, status.get("flights") or {}
    return {
        "scene": status.get("scene", "not running"),
        "mode": status.get("mode", "-"),
        "backend": status.get("backend", "-"),
        "time": "synchronised" if status.get("time_trusted") else "not synchronised",
        "weather": "ok" if weather.get("ok") else (weather.get("error") or "waiting"),
        "flights": "ok" if flights.get("ok") else (flights.get("error") or "waiting"),
        "overhead": str(flights.get("overhead_today", 0)),
        "network": net.get("state", "unknown"),
        "ssid": net.get("ssid") or "-",
    }


def dashboard(status: dict | None, net: dict | None) -> str:
    rows = [("Showing", "scene"), ("Brightness mode", "mode"), ("Display", "backend"),
            ("Clock", "time"), ("Weather data", "weather"), ("Flight data", "flights"),
            ("Aircraft overhead today", "overhead"), ("Network", "network"), ("Wi-Fi", "ssid")]
    flat = flat_status(status, net)
    table = "".join(f'<tr><td>{label}</td><td id="s-{key}">{esc(flat[key])}</td></tr>' for label, key in rows)
    body = f'<div class=card><img id=frame class=preview src="/frame.png" alt="The display"></div><table>{table}</table>'
    return layout("Flyover", body, script=POLL_JS)


# -- settings ------------------------------------------------------------------------------------


def _field(label: str, name: str, value: Any, hint: str = "", kind: str = "text", extra: str = "") -> str:
    hint_html = f' <small>{esc(hint)}</small>' if hint else ""
    return (f'<label for={name}>{esc(label)}{hint_html}</label>'
            f'<input id={name} name={name} type={kind} value="{esc(value)}" {extra}>')


def _select(label: str, name: str, value: str, options: tuple[tuple[str, str], ...]) -> str:
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == value else ""}>{esc(t)}</option>'
                   for v, t in options)
    return f"<label for={name}>{esc(label)}</label><select id={name} name={name}>{opts}</select>"


def settings(cfg: Config, notice: str | None = None, error: str | None = None) -> str:
    loc, fl, d, hs = cfg.location, cfg.flights, cfg.display, cfg.hotspot
    body = (
        message(notice) + message(error, error=True) +
        '<form method=post action=/settings>'
        '<div class=card><h2>Location</h2>'
        + _field("Paste coordinates", "paste", "", "e.g. 41.9742, -87.9073 from a map app")
        + '<div class=row><div>' + _field("Latitude", "latitude", loc.latitude, kind="number",
                                          extra="step=any min=-90 max=90")
        + '</div><div>' + _field("Longitude", "longitude", loc.longitude, kind="number",
                                 extra="step=any min=-180 max=180") + '</div></div>'
        + _field("Time zone", "timezone", loc.timezone, "empty = taken from the location")
        + '</div><div class=card><h2>Which aircraft</h2><div class=row><div>'
        + _field("Radius (km)", "radius_km", fl.radius_km, kind="number", extra="step=0.5 min=0.5 max=50")
        + '</div><div>' + _field("Ceiling (ft)", "max_altitude_ft", fl.max_altitude_ft, kind="number",
                                 extra="step=500 min=0 max=60000") + '</div></div></div>'
        '<div class=card><h2>Display</h2><div class=row><div>'
        + _field("Brightness %", "brightness", d.brightness, kind="number", extra="min=1 max=100")
        + '</div><div>' + _field("Night brightness %", "night_brightness", d.night_brightness,
                                 kind="number", extra="min=0 max=100") + '</div></div>'
        f'<label><input type=checkbox name=auto_brightness value=1{" checked" if d.auto_brightness else ""}>'
        'Dim with the sun by day (Brightness is the maximum)</label>'
        + _field("Dimmest by day %", "auto_brightness_min", d.auto_brightness_min, kind="number",
                 extra="min=1 max=100")
        + '<div class=row><div>' + _field("Night starts", "night_start", d.night_start, kind="time")
        + '</div><div>' + _field("Night ends", "night_end", d.night_end, kind="time") + '</div></div>'
        '<div class=row><div>' + _field("Off from", "quiet_start", d.quiet_start, "optional", "time")
        + '</div><div>' + _field("Off until", "quiet_end", d.quiet_end, "optional", "time") + '</div></div>'
        '<div class=row><div>'
        + _select("Temperature", "temperature_unit", d.temperature_unit, (("F", "Fahrenheit"), ("C", "Celsius")))
        + '</div><div>' + _select("Distance", "distance_unit", d.distance_unit,
                                  (("mi", "Miles"), ("km", "Kilometres"))) + '</div></div>'
        f'<label><input type=checkbox name=clock_24h value=1{" checked" if d.clock_24h else ""}>24-hour clock</label>'
        '</div><div class=card><h2>Setup hotspot</h2>'
        + _field("Name", "hotspot_ssid", hs.ssid) + _field("Password", "hotspot_password", hs.password,
                                                          "8-63 characters")
        + '</div><button type=submit>Save</button></form>'
    )
    return layout("Settings", body)


# -- Wi-Fi ---------------------------------------------------------------------------------------


def wifi(snapshot: dict, notice: str | None = None, error: str | None = None,
         connecting: str | None = None) -> str:
    state, networks = snapshot.get("state", "BOOT"), snapshot.get("networks") or []
    status = {
        "ONLINE": f"Connected to {snapshot.get('ssid') or 'Wi-Fi'}.",
        "DEGRADED": "Wi-Fi is connected but the internet is not reachable.",
        "HOTSPOT": "Not connected. Pick your home network below.",
        "CONNECTING": f"Connecting to {snapshot.get('ssid') or 'the network'}...",
        "BOOT": "Starting up...",
    }.get(state, state)
    options = "".join(
        f'<option value="{esc(n.ssid)}">{esc(n.ssid)} ({n.signal}%{"" if n.secured else ", open"})</option>'
        for n in networks if isinstance(n, WifiNetwork)
    )
    wait = ""
    if connecting:
        wait = (f'<p class="msg ok">Connecting to {esc(connecting)}. If this page stops responding, '
                "rejoin the setup hotspot: it comes back by itself if the connection fails.</p>")
    elif state == "CONNECTING":
        wait = '<p class="msg ok">Connecting...</p>'
    body = (
        message(notice) + message(error or snapshot.get("error"), error=True) + wait +
        f'<p class=hint>{esc(status)}</p>'
        '<form method=post action=/wifi/connect><div class=card>'
        '<label for=ssid>Network</label>'
        f'<select id=ssid name=ssid>{options}<option value="">Other (type the name)...</option></select>'
        + _field("Network name", "ssid_manual", "", "only if it is not in the list")
        + '<label for=password>Password</label><input id=password name=password type=password '
          'autocomplete=off><label><input type=checkbox onclick="password.type=this.checked?\'text\':\'password\'">'
          'Show password</label>'
        '<label><input type=checkbox name=hidden value=1>Hidden network</label>'
        '<button type=submit>Connect</button></div></form>'
        '<form method=post action=/wifi/rescan><button class=secondary type=submit>Scan again</button> '
        '<small class=hint>Briefly closes the setup hotspot (about 15 seconds).</small></form>'
        '<p class=hint>This device can only use 2.4 GHz Wi-Fi.</p>'
    )
    return layout("Wi-Fi setup", body, nav=state != "HOTSPOT")
