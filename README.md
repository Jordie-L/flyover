# Flyover

A small LED display that shows the aircraft passing overhead: flight number, origin → destination, aircraft type, altitude and speed. When nothing is overhead it shows the time, date and temperature.

It runs on a Raspberry Pi Zero 2 W driving a 64x64 RGB LED matrix. It sets up its own Wi-Fi: when it has no connection it opens a hotspot with a setup page, so it can be handed to someone without knowing their network details in advance.

> **This README is the build spec.** The code is being written against it, phase by phase. The [status table](#status) says what exists in the repo today. Hardware values that can only be confirmed with the panel attached are marked **verify on panel**.

## Contents

1. [Status](#status)
2. [Hardware](#hardware)
3. [Raspberry Pi setup](#raspberry-pi-setup)
4. [Install](#install)
5. [What to change for your install](#what-to-change-for-your-install)
6. [Configuration reference](#configuration-reference)
7. [Behaviour](#behaviour)
8. [Architecture](#architecture)
9. [Data sources](#data-sources)
10. [Development](#development)
11. [Operations](#operations)
12. [Troubleshooting](#troubleshooting)
13. [Adapting to other hardware](#adapting-to-other-hardware)
14. [Roadmap](#roadmap)
15. [Credits and licence](#credits-and-licence)

---

## Status

| Area | State |
|---|---|
| Design and spec (this README) | Done |
| Config loading, `preview` and `png` display backends, fonts | Planned (phase 1) |
| Idle screen: clock, date, weather | Planned (phase 1) |
| Flight data, overhead selection, flight screen | Planned (phase 2) |
| Web UI: live preview, settings, Wi-Fi page | Planned (phase 3) |
| `install.sh`, systemd services, hotspot and captive portal on a real Pi | Planned (phase 4) |
| LED matrix backend and panel tuning | Planned (phase 5, needs the panel) |
| Remote access, watchdog, extras | Planned (phase 6) |

Nothing below is runnable until its phase lands. Commands and file paths describe the target layout.

---

## Hardware

### Bill of materials

| Part | Notes |
|---|---|
| Raspberry Pi Zero 2 W, with header pins | The original Zero W is not supported; see [Adapting to other hardware](#adapting-to-other-hardware). |
| Adafruit RGB Matrix Bonnet for Raspberry Pi (product 3211) | Level-shifts the Pi's 3.3 V signals to the panel's 5 V and powers both the panel and the Pi from one supply. |
| 64x64 RGB LED matrix panel, 2.5 mm pitch, HUB75 connector with address line E | 160 × 160 mm. Sold as "P2.5 64x64". Comes with a 16-pin ribbon cable and a power cable. |
| 5 V DC power supply, 4 A or more, 2.1 mm barrel plug | Plugs into the bonnet. |
| microSD card, 8 GB or more | |
| Smoked (tinted) acrylic sheet, 160 × 160 mm, about 3 mm thick | Optional but recommended: it raises contrast and hides the glow around lit pixels. |
| BH1750 light sensor | Optional. Automatic brightness. |
| DS3231 clock module | Optional. Keeps the time through a power cut with no internet. |

### Power

- The bonnet's barrel jack powers the panel and back-powers the Pi through the header. Do not connect a second supply to the Pi's USB power port.
- Never power the panel from the Pi's 5 V pins.
- Panels of this size are typically rated around 4 A at 5 V with every pixel white at full brightness. Check your panel's listing. **Verify on panel.**
- Flyover's screens are mostly black and brightness is capped in config, so real draw should be well below the rating. Measure before choosing a smaller supply.

### Bonnet modifications

Two solder jobs on the bonnet. The first is required for this panel.

1. **Address E jumper (required for 64x64).**
   - A 64x64 panel needs a fifth address line, called E. The bonnet leaves it unconnected by default.
   - The bonnet has three pads labelled `8`, `E` and `16`. Bridge `E` to `8` with a blob of solder.
   - `8` is the usual position for this connector. If the lower half of the picture is garbled or mirrors the top half, remove the blob and bridge `E` to `16`. **Verify on panel.**
2. **PWM jumper (optional, reduces flicker).**
   - Solder a short wire between the pads for GPIO4 and GPIO18.
   - Then set `hardware_mapping = "adafruit-hat-pwm"` in the config. Without the wire, use `"adafruit-hat"`.
   - This uses the Pi's hardware timer, which is shared with onboard audio. Audio is disabled by the installer in either case.

### Assembly

1. Do the solder jobs above before fitting the bonnet.
2. Push the bonnet onto the Pi's 40-pin header.
3. Connect the ribbon cable from the bonnet to the panel's **input** connector. Arrows printed on the back of the panel point from input to output.
4. Screw the panel's power cable into the bonnet's terminals: red to `+`, black to `-`.
5. Mount the acrylic a few millimetres in front of the LEDs.
6. Plug the 5 V supply into the bonnet last.

### Picture quality

Two different effects make lit pixels appear to spill into their neighbours.

| Effect | What it looks like | Fix |
|---|---|---|
| Electrical ghosting | Faint copies of bright pixels on other rows or columns | Raise `gpio_slowdown`, adjust `pwm_lsb_nanoseconds`, fit the PWM jumper |
| Optical bleed | A soft glow around each lit pixel | Smoked acrylic in front; optionally a printed grid between the LEDs and the acrylic |

The screens also use bitmap fonts with no smoothing and avoid pure white, which reduces both effects.

---

## Raspberry Pi setup

### Flash the SD card

Use Raspberry Pi Imager.

1. Device: **Raspberry Pi Zero 2 W**.
2. Operating system: **Raspberry Pi OS Lite (64-bit)**. The Lite image has no desktop, which is what you want.
3. In the customisation settings:
   - **Hostname:** `flyover`. The device is then reachable as `flyover.local`.
   - **Username and password:** your choice.
   - **Wi-Fi:** a network you can reach for first setup, such as your own or a phone hotspot. Set the **Wi-Fi country** correctly: the setup hotspot will not start without it.
   - **Locale:** your keyboard layout. The time zone is set automatically later from the configured location.
   - **SSH:** enabled, with your public key.
4. Write the card, insert it, and power the Pi.

### Check access

```bash
ssh <user>@flyover.local
```

### Limits to know about

- **The Zero 2 W's radio is 2.4 GHz only.** It cannot see or join 5 GHz networks. Most home routers broadcast both.
- **It has no battery-backed clock.** After a power cut it does not know the time until it reaches the internet. Flyover shows `--:--` until then.

---

## Install

On the Pi:

```bash
sudo apt-get update && sudo apt-get install -y git
```

```bash
sudo git clone https://github.com/jordie-l/flyover.git /opt/flyover
```

```bash
sudo mkdir -p /etc/flyover && sudo cp /opt/flyover/config.example.toml /etc/flyover/config.toml
```

Edit the config (see [What to change for your install](#what-to-change-for-your-install)):

```bash
sudo nano /etc/flyover/config.toml
```

Run the installer, then reboot:

```bash
sudo /opt/flyover/deploy/install.sh
```

```bash
sudo reboot
```

After the reboot, open `http://flyover.local/` from a device on the same network. The page shows a live picture of what the panel is displaying, whether or not a panel is attached.

### What the installer does

The script is safe to run again after an update. Each step can also be done by hand.

| Step | Action |
|---|---|
| 1 | Installs system packages: Python virtual-environment support, Python headers, Cython and a C++ compiler (needed to build the matrix library). |
| 2 | Creates a Python virtual environment at `/opt/flyover/.venv` and installs Flyover into it. |
| 3 | Builds and installs the [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) Python binding into the same environment. Skipped with `--no-matrix`. |
| 4 | Creates `/var/lib/flyover` (route cache, owned by the `daemon` user) and a rule that creates `/run/flyover` at every boot. |
| 5 | Turns off onboard audio: adds `dtparam=audio=off` to `/boot/firmware/config.txt` and blacklists the `snd_bcm2835` module. The matrix library refuses to start while that module is loaded. |
| 6 | Reserves one CPU core for the panel refresh thread by appending `isolcpus=3` to the single line in `/boot/firmware/cmdline.txt`. |
| 7 | Adds a DNS rule for the setup hotspot at `/etc/NetworkManager/dnsmasq-shared.d/flyover.conf`, so that every name resolves to the Pi while a phone is connected to the hotspot. |
| 8 | Installs and enables the two services, `flyover-display` and `flyover-control`. |
| 9 | With `--volatile-logs`: keeps system logs in memory to reduce SD-card wear. |

Steps 5 and 6 need the reboot to take effect.

---

## What to change for your install

Everything specific to one installation lives in `/etc/flyover/config.toml`. Nothing in the code needs editing.

| Setting | Key | Change it? |
|---|---|---|
| Where the display is | `[location] latitude`, `longitude` | **Always.** Can also be set by clicking a map on the settings page. |
| Hotspot name and password | `[hotspot] ssid`, `password` | **Always** change the password (8–63 characters). |
| How far out to look | `[flights] radius_km`, `max_altitude_ft` | Tune after watching a few passes. |
| Units and clock style | `[display] temperature_unit`, `distance_unit`, `clock_24h` | To taste. |
| Brightness and night hours | `[display] brightness`, `night_*`, `quiet_*` | To taste. |
| Matrix wiring | `[matrix] hardware_mapping` | Only if you fitted the PWM jumper. |
| Matrix timing | `[matrix] gpio_slowdown` and the other timing keys | Only if the picture flickers or ghosts. |

The home Wi-Fi name and password are **not** in this file. They are entered on the setup page and stored by the operating system's network manager.

---

## Configuration reference

The full file with defaults. Most of these can also be changed on the settings page, which rewrites this file.

```toml
[location]
latitude  = 0.0            # decimal degrees, north positive
longitude = 0.0            # decimal degrees, east positive
# timezone = "America/Chicago"   # optional; by default it is looked up from the coordinates

[flights]
provider         = "airplanes_live"   # or "adsb_lol"
radius_km        = 4.0     # a plane inside this circle counts as overhead
max_altitude_ft  = 15000   # ignore anything higher
hold_seconds     = 10      # keep showing a plane this long after it leaves the circle
poll_idle_s      = 10      # seconds between position requests when nothing is near
poll_active_s    = 5       # seconds between requests when a plane is near
route_cache_days = 7       # how long a looked-up route is remembered

[display]
backend          = "auto"  # "auto", "matrix" or "preview"; auto uses the matrix when its library is installed
brightness       = 60      # percent, daytime
night_brightness = 15      # percent
night_start      = "22:00" # local time; dim and switch to amber
night_end        = "07:00"
quiet_start      = ""      # optional; panel fully off between these times
quiet_end        = ""
clock_24h        = false
temperature_unit = "F"     # "F" or "C"
distance_unit    = "mi"    # "mi" or "km"; altitude is always feet and speed always knots

[matrix]
rows                = 64
cols                = 64
hardware_mapping    = "adafruit-hat"  # "adafruit-hat-pwm" with the PWM jumper fitted
gpio_slowdown       = 2    # raise if the picture is unstable or ghosts
pwm_bits            = 11   # lower gives a faster refresh and fewer colour steps
pwm_lsb_nanoseconds = 130  # raise to reduce ghosting at the cost of refresh rate
limit_refresh_hz    = 0    # 0 = no limit; a fixed value can remove a faint flicker
panel_type          = ""   # "FM6126A" if the panel stays dark
row_addr_type       = 0
rgb_sequence        = "RGB" # change if colours are swapped

[hotspot]
ssid          = "Flyover-Setup"
password      = "change-me-now"  # 8-63 characters
retry_minutes = 5          # how often to retry saved networks while the hotspot is idle

[network]
boot_timeout_s           = 60   # how long to wait for a saved network at boot
check_interval_s         = 30   # how often to test the connection
failures_before_degraded = 3    # consecutive failed tests before reacting
degraded_timeout_s       = 180  # how long to wait for a reconnect before opening the hotspot

[web]
port = 80
```

The `[matrix]` defaults are starting values. **Verify on panel.**

---

## Behaviour

### Screens

The panel is 64 × 64 pixels. Mock-ups below are indicative, not pixel-exact.

**Flight** (a plane is overhead)

```
┌────────────────┐
│▌UA1234         │  flight number, with a bar in the airline's colour
│                │
│  SFO>ORD       │  origin and destination, large
│                │
│ B738   8.5k ↓  │  aircraft type, altitude in thousands of feet, climbing or descending
│ 142kt  0.8mi ↗ │  ground speed, distance from you, direction of travel
│             +2 │  number of other aircraft also in range
└────────────────┘
```

**Idle** (nothing overhead)

```
┌────────────────┐
│                │
│     7:42       │  large clock
│                │
│   SAT NOV 9    │
│   ☀  54°F      │  weather icon and temperature
│                │
└────────────────┘
```

**Wi-Fi setup** (hotspot is open)

```
┌────────────────┐
│  WIFI SETUP    │
│   ▄▄▄▄▄▄▄▄     │
│   █ QR   █     │  scan with a phone camera to join the hotspot
│   ▀▀▀▀▀▀▀▀     │
│ Flyover-Setup  │
│ <password>     │
└────────────────┘
```

**Other states**

| State | What is shown |
|---|---|
| Connected to Wi-Fi but no internet | The idle screen with a small red Wi-Fi mark in the corner. |
| Time not yet known | `--:--` in place of the clock. |
| Night hours | Reduced brightness, amber only. |
| Quiet hours | Panel off. Flight requests are paused. |

### Which plane is shown

1. Ask the flight data service for aircraft in a circle around the configured location.
2. Discard aircraft on the ground and aircraft above `max_altitude_ft`.
3. Between requests, move each aircraft forward along its heading at its reported speed. A jet covers about a kilometre between requests, so this keeps the timing and the distance readout accurate.
4. An aircraft is "overhead" while it is inside `radius_km`.
5. If several are overhead, show the closest and a `+N` count.
6. Keep showing an aircraft for `hold_seconds` after it leaves, so the screen does not flick back and forth at the edge of the circle.

What is shown for each aircraft:

- **Flight number.** The form printed on a ticket (`UA1234`) when the lookup returns it. Otherwise the radio callsign (`UAL1234`).
- **Route.** Airport codes for origin and destination.
- **Route check.** Route databases key on the flight number, and airlines reuse numbers. If the aircraft's position is far from the line between the two airports, the route is shown dimmed with a `?`.
- **No route found** (private, military, some cargo): the registration and aircraft type instead.

### Network states

```
BOOT      Try saved networks for boot_timeout_s.
          Connected -> ONLINE.  Not connected -> scan, then HOTSPOT.

ONLINE    Test the connection every check_interval_s.
          failures_before_degraded misses in a row -> DEGRADED.

DEGRADED  Leave the network manager to reconnect for degraded_timeout_s.
          Reconnected -> ONLINE.  Still down -> scan, then HOTSPOT.

HOTSPOT   Open the setup hotspot and serve the setup page.
          Credentials submitted -> try to join.
              Joined -> ONLINE.  Failed -> HOTSPOT again, with the reason shown on the page.
          Every retry_minutes, if no device is connected to the hotspot:
              close it briefly, rescan, and retry saved networks.
```

- The last rule covers a router that was simply rebooting: the display rejoins without anyone touching it.
- The hotspot is never closed while a device is connected to it, so setup is not interrupted.

### Setting up Wi-Fi

1. The panel shows the Wi-Fi setup screen. With no panel attached, the hotspot name and password are the ones in `config.toml`.
2. On a phone, scan the QR code, or join the hotspot by name and password.
3. The phone opens the setup page by itself (the "sign in to network" prompt). If it does not, browse to `http://10.42.0.1/`.
4. Pick the home network from the dropdown, enter its password, and submit.
5. The hotspot closes while the Pi tries to join. On success the panel returns to the idle screen. On failure the hotspot reopens and the page shows why.

Details:

- The Pi has one radio and cannot scan while it is acting as a hotspot. Networks are scanned just before the hotspot opens. The **Rescan** button closes the hotspot for about 10 seconds to scan again; the phone has to rejoin afterwards.
- The hotspot is password-protected (WPA2), so the home Wi-Fi password is not sent over an open network.
- A hidden network can be typed in by name.
- Only 2.4 GHz networks appear in the list.

---

## Architecture

### Two services

```
flyover-display     starts as root to claim the panel, then runs as the `daemon` user
    fetches flights, routes and weather
    picks the overhead aircraft
    draws the current screen and sends it to the panel
    reads   /etc/flyover/config.toml
            /run/flyover/net.json
    writes  /run/flyover/frame.png      the current screen, for the web preview
            /run/flyover/status.json    what it is showing and whether its data sources are healthy
            /var/lib/flyover/           route cache and the last known time zone

flyover-control     runs as root
    web UI on port 80
        /            live preview and status
        /settings    location, radius, brightness, units
        /wifi        network dropdown and password
    network state machine
    reads   /run/flyover/frame.png, /run/flyover/status.json
    writes  /etc/flyover/config.toml    replaced atomically
            /run/flyover/net.json       current network state and hotspot details
```

Why two and not one:

- The matrix library gives up root as soon as the panel is initialised. Joining networks and opening a hotspot need root. They cannot share a process.
- A crash in the web UI must not blank the panel, and the reverse.

Why not three: the Wi-Fi page and the network state machine both issue network commands. Keeping them in one process means only one thing is ever changing the network.

The services exchange plain files and nothing else. The display notices a config change by watching the file's modification time.

### Display backends

Every screen is drawn as a 64 × 64 image in memory. A backend then delivers it.

| Backend | Delivers to | Used for |
|---|---|---|
| `matrix` | The LED panel | The finished device |
| `preview` | `/run/flyover/frame.png`, shown on the web UI | A Pi with no panel, a laptop, and remote checks |
| `png` | Files on disk | Automated tests |

The `matrix` backend also writes the preview file, so the web UI always shows the real panel contents.

### Source layout

```
flyover/
  config.py            load and validate config.toml
  state.py             read and write the files in /run/flyover
  geo.py               distance, bearing, dead reckoning, distance from a route line
  selector.py          choose the overhead aircraft
  providers/
    flights.py         aircraft positions
    routes.py          flight number, route, aircraft type
    weather.py         temperature, conditions, time zone
    cache.py           SQLite cache
  display/
    app.py             main loop
    fonts.py           bitmap font loading
    scenes/            idle, flight, wifi_setup, boot
    backends/          matrix, preview, png
  control/
    app.py             starts the web server and the network state machine
    netwatch.py        the state machine
    nm.py              the only module that runs network commands
    web/               pages and templates
fonts/                 bitmap fonts (X11 "misc-fixed", public domain)
deploy/
  install.sh
  systemd/             flyover-display.service, flyover-control.service
  nm/                  hotspot DNS rule
tests/
  fixtures/            sample API responses
config.example.toml
pyproject.toml
```

### Extending

| To add | Do this |
|---|---|
| A screen | Add a module in `display/scenes/` that draws onto the image it is given, and register when it should be active in `display/app.py`. |
| A flight data source | Add a class in `providers/flights.py` that returns the common aircraft record, and name it in `[flights] provider`. |
| A display type | Add a module in `display/backends/` that accepts a 64 × 64 image. |

### Network commands used

`control/nm.py` wraps the `nmcli` tool. Running these by hand is the quickest way to debug.

| Purpose | Command |
|---|---|
| List networks | `nmcli -t -f SSID,SIGNAL,SECURITY device wifi list --rescan yes` |
| Join a network | `nmcli device wifi connect "<ssid>" password "<password>" ifname wlan0` |
| Open the hotspot | `nmcli device wifi hotspot ifname wlan0 ssid "<ssid>" password "<password>"` |
| Close the hotspot | `nmcli connection down Hotspot` |
| Connection state | `nmcli -t -f STATE,CONNECTIVITY general` |
| Saved networks | `nmcli -t -f NAME,TYPE connection show` |

The hotspot uses the network manager's "shared" mode: the Pi takes the address `10.42.0.1` and hands out addresses to devices that join. The DNS rule from the installer makes every hostname resolve to the Pi, and the web UI redirects any unknown request to `/wifi`. Together these trigger the phone's sign-in prompt.

### Security

- The web UI has no login. Anyone on the same network can change the settings. This suits a home network; do not expose port 80 to the internet.
- Home Wi-Fi credentials are stored by the network manager in files only root can read. They are never written to `config.toml`, the logs or the web pages.
- Keep your edited `config.toml` out of version control: it holds your location and hotspot password.

---

## Data sources

All three are free and need no account.

| Data | Service | Request |
|---|---|---|
| Aircraft positions | [airplanes.live](https://airplanes.live) | `GET https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}` |
| Positions, alternative | [adsb.lol](https://adsb.lol) | `GET https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}` |
| Route, flight number, aircraft | [adsbdb](https://www.adsbdb.com) | `GET https://api.adsbdb.com/v0/aircraft/{hex}?callsign={callsign}` |
| Weather and time zone | [Open-Meteo](https://open-meteo.com) | `GET https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code,is_day&timezone=auto` |

Notes:

- **Positions.** Both position services return the same format. Radius is in nautical miles. Fields used: `hex` (the aircraft's unique radio address), `flight` (callsign), `r` (registration), `t` (type), `lat`, `lon`, `alt_baro`, `gs` (ground speed), `track`, `baro_rate`. The airplanes.live endpoint and its limit of one request per second are to be confirmed on the first live run.
- **adsb.lol** has announced that it will require an API key in future, issued to people who contribute receiver data. It is offered as an alternative, not as an automatic fallback.
- **Routes.** One request per newly seen aircraft returns the aircraft details and the route together. An unknown callsign returns HTTP 404, or just the aircraft details in the combined form. Results are cached for `route_cache_days`; "not found" is cached for a shorter time.
- **Weather.** Refreshed every 15 minutes. The response also names the time zone for the coordinates, which Flyover uses for the clock and stores for the next offline start.

### Request volume

These are community services, so Flyover keeps its footprint small.

- One position request every 10 seconds when idle, every 5 seconds while an aircraft is near. About 9,000 requests a day at most.
- No requests during quiet hours.
- After an error or a "slow down" response, the interval doubles on each failure up to 5 minutes, then recovers.
- A descriptive `User-Agent` header identifies the project.

### When a source is down

| Source down | Effect |
|---|---|
| Positions | Idle screen stays up. No flights are shown. |
| Routes | Flights are shown with callsign, type, altitude and speed, without origin and destination. |
| Weather | The temperature line is hidden after the last reading is an hour old. |

---

## Development

Flyover runs on any computer with Python; no Pi or panel is needed for most work.

```bash
python3 -m venv .venv && . .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

Run the display with the preview backend:

```bash
python -m flyover.display --config config.example.toml --backend preview --run-dir ./run
```

Run the web UI against a simulated network manager:

```bash
python -m flyover.control --config config.example.toml --run-dir ./run --fake-network --port 8080
```

Then open `http://localhost:8080/`.

Run the tests:

```bash
pytest
```

What the tests cover:

- Aircraft selection: entering and leaving the circle, several candidates, the altitude ceiling, hold time.
- Route check: on-route and off-route positions.
- The network state machine, driven by a fake network manager.
- One image snapshot per screen, compared against a stored reference.

`tests/fixtures/` holds sample responses in each service's format, so tests never touch the network.

---

## Operations

### Services

```bash
systemctl status flyover-display flyover-control
```

```bash
sudo systemctl restart flyover-display flyover-control
```

```bash
journalctl -u flyover-control -f
```

Both services restart automatically if they exit.

### Updating

```bash
cd /opt/flyover && sudo git pull && sudo deploy/install.sh
```

The installer leaves an existing `/etc/flyover/config.toml` untouched.

### Remote access

Once the display is in someone else's home you are no longer on its network. A mesh VPN such as [Tailscale](https://tailscale.com) on the Pi gives you SSH and the web UI from anywhere without opening ports on their router. Install it before handing the device over.

### Unattended reliability

- **Watchdog.** The Pi's hardware watchdog reboots it if the system hangs. Enabled by setting `RuntimeWatchdogSec=15s` in `/etc/systemd/system.conf`.
- **SD-card wear.** Run the installer with `--volatile-logs`. The route cache writes only when a new aircraft is first seen.
- **Power cuts.** The device needs no clean shutdown. After power returns it rejoins Wi-Fi, syncs the time, and carries on.

---

## Troubleshooting

### Panel

| Symptom | Likely cause | Fix |
|---|---|---|
| Panel stays dark, service is running | Panel uses a driver chip that needs an init sequence | Set `panel_type = "FM6126A"` |
| Panel stays dark, service fails to start | Onboard audio still enabled | Re-run the installer and reboot; check `lsmod` shows no `snd_bcm2835` |
| Lower half garbled, or a copy of the top half | Address E jumper missing or on the wrong pad | Bridge `E` to `8`; if already done, move it to `16` |
| Only the top half lights | `rows` set to 32 | Set `rows = 64` |
| Random sparkles, unstable picture | Signals too fast for the panel | Raise `gpio_slowdown` to 3 or 4 |
| Faint copies of bright pixels | Electrical ghosting | Raise `pwm_lsb_nanoseconds` (try 200–300); fit the PWM jumper |
| Visible flicker, worse on camera | Refresh rate uneven | Fit the PWM jumper; set `limit_refresh_hz = 120`; lower `pwm_bits` to 9 |
| Red and blue swapped | Panel colour order differs | Set `rgb_sequence = "BGR"` (or the order that matches) |
| Panel dims or resets on bright screens | Power supply too small or cable too thin | Use a 4 A supply; shorten the power cable |

### Wi-Fi

| Symptom | Likely cause | Fix |
|---|---|---|
| Home network missing from the dropdown | It is 5 GHz only | Enable the router's 2.4 GHz band |
| Home network missing, router is 2.4 GHz | The scan is from before the hotspot opened | Press **Rescan**, then rejoin the hotspot |
| Hotspot never appears | Wi-Fi country not set | `sudo raspi-config nonint do_wifi_country <code>`, for example `US` |
| Phone joins the hotspot but no page opens | Phone skipped the sign-in check | Browse to `http://10.42.0.1/` |
| "Wrong password" after submitting | Typo, or the router uses WPA3 only | Re-enter; set the router to WPA2 or mixed mode |
| `flyover.local` does not resolve | The client does not support `.local` names | Use the IP address shown on the router's device list |
| Clock shows `--:--` while online | Time not synced yet | Wait a minute; check `timedatectl` |

### Flights

| Symptom | Likely cause | Fix |
|---|---|---|
| No flights ever shown | Location not set, or radius too small | Check `[location]`; raise `radius_km` |
| Planes shown too early or too late | Radius does not match what you can see | Tune `radius_km` and `max_altitude_ft` |
| High-altitude cruise traffic shown | Ceiling too high | Lower `max_altitude_ft` |
| Route shown with `?` | Flight number reused for a different route | Expected; the position did not match the stored route |
| Flights shown without a route | Not in the route database | Expected for private and military aircraft |

---

## Adapting to other hardware

| Change | What to adjust |
|---|---|
| Fitted the PWM jumper | `hardware_mapping = "adafruit-hat-pwm"` |
| A different adapter board | `hardware_mapping` to that board's name in the matrix library |
| Raspberry Pi 3 or 4 | Works. A Pi 4 usually needs `gpio_slowdown = 4`. |
| Raspberry Pi 5 | Not supported by this setup; its GPIO hardware differs. |
| Original Pi Zero W | Not supported: one slow core gives a flickering panel, and current Python packages are not built for its processor. |
| A different panel size | `rows` and `cols`. The screens are laid out for 64 × 64 and would need new layouts. |

---

## Roadmap

| Phase | Contents |
|---|---|
| 0 | This README |
| 1 | Config, preview and test backends, fonts, idle screen with weather |
| 2 | Flight data, selection, flight screen |
| 3 | Web UI: preview, settings, Wi-Fi page against a simulated network manager |
| 4 | Installer, services, real hotspot and captive portal on a Pi without a panel |
| 5 | LED matrix backend, timing, brightness, 48-hour soak test |
| 6 | Remote access, watchdog, daily flight count, alerts for notable aircraft, update button, optional local receiver |

---

## Credits and licence

- LED matrix driver: [rpi-rgb-led-matrix](https://github.com/hzeller/rpi-rgb-led-matrix) by Henner Zeller.
- Aircraft positions: [airplanes.live](https://airplanes.live) and [adsb.lol](https://adsb.lol), both built on data from volunteer receivers.
- Routes and aircraft details: [adsbdb](https://www.adsbdb.com).
- Weather: [Open-Meteo](https://open-meteo.com), licensed CC BY 4.0.
- Fonts: X11 "misc-fixed" bitmap fonts, public domain.

No licence has been chosen for this repository yet.
