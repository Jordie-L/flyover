# Pi integration guide

For whoever brings this up on the real Raspberry Pi (a Pi Zero 2 W, Raspberry Pi OS Lite 64-bit, later with a 64x64 HUB75 panel on an Adafruit RGB Matrix Bonnet).

## What you are inheriting

All the software exists and has automated tests (`pytest`, 310 tests, about 5 seconds, run in CI). It was written in a cloud sandbox that had **no Pi, no panel, no access to the live data services, and no NetworkManager**. Everything that touches those was written from documentation and checked only against stand-ins. This document is the list of what that means.

Read [`README.md`](../README.md) first for the design. This file covers only what needs verifying and how.

## Verified here vs not

| Area | How it was checked | Left to check on the device |
|---|---|---|
| Config, state files, clock, fonts, icons, themes | Unit tests | Nothing |
| Screens | Pixel snapshots; viewed enlarged | **Legibility on the real panel** (see "Panel" below) |
| Flight, route, weather parsing | Hand-built sample replies in `tests/fixtures/` that follow the documented formats | **Live replies** (see "Live data") |
| Selector, hold time, dead reckoning, route check | Unit tests | Tune `radius_km` and `max_altitude_ft` against real traffic |
| Network state machine | Tests against `FakeBackend`, with a fake clock | Behaviour of real NetworkManager (see "Wi-Fi") |
| `nmcli` wrapper | Command lines and parsing, against a fake subprocess | **Real `nmcli` output and flags** |
| Web UI | A real HTTP server on a local port, real requests | Phones, captive-portal pop-ups |
| `install.sh` | Its file-editing functions, against temp files; `bash -n` | **The script itself, on Raspberry Pi OS** |
| systemd units | Text checked | `systemctl start`, permissions after the privilege drop |
| LED matrix backend | Against a stand-in `rgbmatrix` module | **The real driver**: option names, build, panel behaviour |

## Order of work

Do these in order. Each step can be done without the later ones.

### 1. Install without the panel

```bash
git clone https://github.com/Jordie-L/flyover.git /opt/flyover && cd /opt/flyover
sudo deploy/install.sh --no-matrix
```

Check:

- The script completes. If it fails, which step? (See "Where things are likely wrong" for the guesses I made.)
- `systemctl status flyover-display flyover-control` are both `active (running)`.
- `ls -ld /run/flyover` is `drwxrwxr-x root daemon`, and `ls /run/flyover` shows `frame.png`, `status.json` and `net.json`.
- `ps -o user,cmd -C python` shows the **display as `daemon`** (it starts as root and drops) and the control service as `root`. If the display is still root, `drop_privileges()` did nothing: read `journalctl -u flyover-display`.
- `http://flyover.local/` loads and the preview image updates. `curl -s localhost/api/status` returns JSON.
- Memory is sane: `free -m` (both services together should be well under 150 MB).

### 2. Live data

Set the location in the web UI (Settings, paste "lat, lon"). Then watch `journalctl -u flyover-display -f` for warnings from `flyover.providers.*`.

Confirm each reply format by hand (substitute your coordinates), and compare against the parsers:

```bash
curl -s "https://api.airplanes.live/v2/point/41.9742/-87.9073/10" | head -c 1500
curl -s "https://api.adsbdb.com/v0/aircraft/a1b2c3?callsign=UAL1" | head -c 1500
curl -s "https://api.adsbdb.com/v0/callsign/UAL1" | head -c 1500
curl -s "https://api.open-meteo.com/v1/forecast?latitude=41.97&longitude=-87.9&current=temperature_2m,weather_code,is_day&timezone=auto"
```

| Unverified assumption | Where it lives |
|---|---|
| airplanes.live serves `/v2/point/{lat}/{lon}/{radius_nm}` with an `ac` list, and tolerates one request every 5 to 10 s | `providers/flights.py` (`URLS`, `parse_reply`) |
| adsbdb aircraft objects use `icao_type`, `type`, `manufacturer`, `registration` (only the *route* fields were confirmed from its docs) | `providers/routes.py` (`parse_response`) |
| adsbdb returns HTTP 404 for an unknown aircraft or callsign, and 200 with only the aircraft for a known aircraft with an unknown callsign | `RouteResolver._query` |
| Open-Meteo returns `current.temperature_2m`, `weather_code`, `is_day` and a top-level `timezone` | `providers/weather.py` |

Then check the behaviour:

- The status page shows flights `ok`, and "in range" matches a flight-tracking site for the same area.
- A real overhead pass switches to the flight screen (use `tests/fixtures/` style data to eyeball `docs/screens/` if no aircraft come by).
- Compare route and flight number with a tracking site for several flights. Note any wrongly marked `?`.
- Tune `radius_km` so an aircraft appears when it is about as close as you can see it.

If a parser is wrong, fix it **and update the fixture in `tests/fixtures/`** with a real (trimmed) reply, so the test then guards the real format.

### 3. Wi-Fi and the setup hotspot

This is the least-verified part, because none of it ran against NetworkManager.

Before testing, check the primitives by hand:

```bash
nmcli -t -f DEVICE,STATE,CONNECTION device
nmcli -t -f SSID,SIGNAL,SECURITY device wifi list ifname wlan0 --rescan yes
nmcli -t -f ACTIVE,SSID device wifi list ifname wlan0 --rescan no
sudo nmcli device wifi hotspot ifname wlan0 con-name Flyover-Hotspot ssid Test password testpass12 band bg
sudo nmcli connection modify Flyover-Hotspot connection.autoconnect no
iw dev wlan0 station dump
sudo nmcli connection down id Flyover-Hotspot
nmcli device connect wlan0
```

Scenarios to run (a phone is needed; watch `journalctl -u flyover-control -f` and `cat /run/flyover/net.json`):

| Scenario | Expect |
|---|---|
| Boot with a working saved network | `ONLINE` within about 10 s |
| Remove the saved network (`nmcli connection delete id preconfigured`) and reboot | Hotspot after about 60 s; panel or `net.json` shows its name and password; the QR joins the hotspot |
| Phone joins the hotspot | A sign-in pop-up opens the setup page. Test **iPhone and Android**. If not, browse `http://10.42.0.1/` |
| Pick the home network, right password | Hotspot closes, state `ONLINE`, preview returns to idle |
| Wrong password | Hotspot returns within seconds, page shows "Wrong password", `net.json` has `error` |
| Router off for 5 minutes, then on | `ONLINE` → `DEGRADED` → `HOTSPOT`; after the router returns the Pi rejoins by itself within `retry_minutes` (with no phone connected) |
| Phone stays on the hotspot past `retry_minutes` | The hotspot is **not** closed while it is connected |

Likely failures:

- **Hotspot never starts:** Wi-Fi country not set, or rfkill. `nmcli` prints the reason; it is shown on the page and in the log.
- **NetworkManager starts the hotspot by itself at boot:** the `connection.autoconnect no` step did not take; check `nmcli connection show Flyover-Hotspot | grep autoconnect`.
- **No sign-in pop-up:** the wildcard DNS rule (`/etc/NetworkManager/dnsmasq-shared.d/flyover.conf`, content `address=/#/10.42.0.1`) may not be read by NetworkManager's dnsmasq. Check with `nslookup example.com 10.42.0.1` from the phone side or the Pi while the hotspot is up. Restart NetworkManager after installing the rule.
- **`error: "Wrong password"` for a right password:** `friendly_connect_error` in `control/nm.py` matches on the text of nmcli's message; read the real message and adjust.
- **Hotspot client count always 0:** `iw` output differs, or it is missing. The retry rule would then close the hotspot while someone is using it. Fix `NmcliBackend.hotspot_clients`.
- **Joined network shown as `preconfigured`:** the `ACTIVE,SSID` query returned nothing; the code falls back to the profile name.

### 4. The panel

When the panel and bonnet are fitted (see README, "Bonnet modifications": the **E jumper for 64x64**):

1. Smoke-test the driver on its own, away from Flyover:

   ```bash
   cd /opt/rpi-rgb-led-matrix/examples-api-use
   make
   sudo ./demo -D0 --led-rows=64 --led-cols=64 --led-gpio-mapping=adafruit-hat --led-slowdown-gpio=2
   ```

   Use `adafruit-hat-pwm` if the GPIO4–GPIO18 jumper is fitted. This settles wiring and the `[matrix]` values before Flyover is involved.
2. Re-run `sudo deploy/install.sh` **without** `--no-matrix`, then reboot. This builds the Python binding and applies the boot settings.
3. Confirm `/opt/flyover/.venv/bin/python -c "import rgbmatrix"` works, then `journalctl -u flyover-display` shows `"backend": "matrix"` in `/run/flyover/status.json`.
4. Put the working values in `/etc/flyover/config.toml` under `[matrix]`.

Check:

- **Option names.** `display/backends/matrix.py` sets `gpio_slowdown`, `limit_refresh_rate_hz`, `row_address_type`, `led_rgb_sequence`, `panel_type`, `drop_privileges`, `pwm_bits`, `pwm_lsb_nanoseconds`. These were written from the driver's documentation, not run. A wrong name raises `AttributeError` at startup (it surfaces in the journal); check against `bindings/python/rgbmatrix/core.pyx` in the driver checkout.
- **Brightness.** The code assigns `matrix.brightness` (1 to 100) and uses a black frame at brightness 1 for "off". Confirm night mode really dims.
- **Privilege drop with the real driver.** The backend sets `drop_privileges = False`, then `display/privileges.py` drops to `daemon` after startup. Confirm the panel keeps updating after the drop. If the driver needs root later (it should not), use `--keep-root` in the unit.
- **Boot settings.** After reboot: `cat /proc/cmdline` shows the `isolcpus=...` flags on one line, `lsmod | grep snd_bcm2835` is empty.
- **Flicker and ghosting:** README, "Picture quality" and Troubleshooting. Tune `gpio_slowdown`, `pwm_lsb_nanoseconds`, `limit_refresh_hz`, `pwm_bits`.
- **Legibility at 2 to 4 m**, in daylight and at night, **behind the smoked acrylic**. The layouts in `display/scenes/` use fixed pixel positions; adjust them if something is too small, and regenerate snapshots (`UPDATE_SNAPSHOTS=1 pytest tests/test_scenes_app.py`). The 4 px font used for the hotspot name and password is the smallest; check it is readable.
- **QR code scans** from a phone at normal distance. If not, raise brightness or enlarge the code (fewer error-correction bits are already used; a shorter hotspot name and password give a smaller code).
- **Colours.** Day colours and the amber night palette are in `display/theme.py`; airline bar colours in `display/scenes/flight.py`.

### 5. Soak test

Leave it running for 48 hours with the panel on. Then:

- `journalctl -u flyover-display -u flyover-control --since "2 days ago" -p warning` has nothing recurring.
- `free -m` and `ps -o rss,cmd -C python` have not grown (the route cache dictionary is bounded; look anyway).
- `ls -la /var/lib/flyover` (the SQLite file is small; rows older than 30 days are pruned at start).
- Pull the power mid-run, restore it: the display comes back by itself, with `--:--` until the time syncs.

## Where things are likely wrong

My guesses that could not be checked, in the order I would suspect them:

| # | Guess | If wrong | Fix in |
|---|---|---|---|
| 1 | `install.sh` package names and the driver build (`make build-python PYTHON=...`, `make install-python PYTHON=...`) work on current Raspberry Pi OS | Script stops at that step | `deploy/install.sh` |
| 2 | `/boot/firmware/{config,cmdline}.txt` are the right files (Bookworm onward; older images use `/boot`) | Boot settings not applied; `boot_dir` falls back to `/boot` if `/boot/firmware` is absent | `boot_dir` in `deploy/install.sh` |
| 3 | `raspi-config nonint get_wifi_country` prints the country code (empty if unset) | A false "no country" warning, or a missed one | end of `deploy/install.sh` |
| 4 | `ProtectSystem=full` and `ProtectHome=yes` on the display unit do not stop the matrix driver (`/dev/mem`) | The panel stays dark with a permission error | `deploy/systemd/flyover-display.service` |
| 5 | After the drop to `daemon`, `/run/flyover` (mode 0775, group `daemon`) and `/var/lib/flyover` are writable | The status page says "not running"; the journal shows a `PermissionError` (reported once, then quiet) | `deploy/tmpfiles.d/flyover.conf`, `install.sh` |
| 6 | `/run/systemd/timesync/synchronized` exists once `systemd-timesyncd` has synced | The clock stays `--:--` forever | `clock.py` (`_TIMESYNC_MARKER`) |
| 7 | The wildcard DNS file is read by NetworkManager's dnsmasq in shared mode | No sign-in pop-up (the manual URL still works) | `deploy/nm/flyover-captive.conf` |
| 8 | `nmcli device connect wlan0` rejoins a saved network | The "router came back" retry never succeeds | `NmcliBackend.reconnect_saved` |
| 9 | The driver's Python option names (see step 4) | `AttributeError` at startup | `display/backends/matrix.py` |
| 10 | `isolcpus=domain,managed_irq,3 ...` is accepted by the installed kernel | Kernel ignores it, or boot problems (remove the flags from `cmdline.txt`) | `CMDLINE_FLAGS` in `deploy/install.sh` |
| 11 | The hardening on the control unit (`NoNewPrivileges`, `ProtectHome`, `ProtectKernel*`, ...) does not stop `nmcli`, `iw` or the config edit | Control service fails to start, or Wi-Fi/settings changes fail with a permission error | `deploy/systemd/flyover-control.service` |
| 12 | `rpi-rgb-led-matrix` at the pinned commit builds with the `make` targets in guess 1 | Driver step fails; try a newer reviewed commit via `RGB_MATRIX_REF` | `RGB_MATRIX_REF` in `deploy/install.sh` |

## Design decisions and why

Decisions made without the user's input, in case you want to revisit them.

| Decision | Reason | Cost |
|---|---|---|
| Standard-library HTTP server, no Flask | 512 MB board, one user, small pages; fewer dependencies to build on a Zero | Hand-written routing and form handling |
| Two services (display, control) | Root only where needed; a crash in one does not blank the other | A file-based interface between them (`/run/flyover`) |
| One owner for the Wi-Fi radio (`NetWatch`), one lock | The setup page and the watchdog logic both change the network | `request_connect` can block the timer for up to about 80 s |
| Own BDF font renderer | Pixel-exact output, identical everywhere, so snapshot tests are stable; full Unicode coverage of the font | About 100 lines |
| `preview` backend writes `frame.png` | Runs with no panel; the same file powers the web preview and remote checks | A PNG write when a frame changes (about once a minute when idle) |
| Time shown only when `systemd-timesyncd` reports sync | No battery clock: a wrong time is worse than none | `--:--` after a power cut with no internet |
| Weather hidden after 1 hour; positions dead-reckoned up to 60 s | Never show stale data as current | Brief gaps on outages |
| Selector hysteresis (70%) and hold time | Stops the screen flickering between aircraft | A second aircraft briefly hidden behind the first |
| Route sanity check (150 km or 20% of route) | Flight numbers are reused | Rare false `?` on unusual routings |
| Comment-preserving config editor | The config file documents itself; a settings page must not erase that | A small parser to maintain (`control/config_edit.py`) |
| Hotspot password generated at install | A published default password is not a password | It is also in `net.json` (world-readable), because the display draws the QR code |
| QR code black on white, always | Phone cameras need the contrast | Bright block on a dark panel at night |
| No map picker, no web login, no update button, no notable-aircraft alerts | Each needs hosted assets, an auth design or real traffic data; listed under "Not built yet" in the README | Manual entry of coordinates |
| Generic `User-Agent` | Nothing about the owner or project leaves the device | Some services like contact details; if one asks, add them deliberately |
| Daily overhead count in the status file | Cheap, and gives a way to judge radius tuning | Not shown on the panel |

## Working on the code

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest                                   # a few seconds; no network, no hardware
python -m flyover.display.gallery        # re-render docs/screens
```

- Every screen has a pixel snapshot. After changing one on purpose: `UPDATE_SNAPSHOTS=1 pytest tests/test_scenes_app.py`, look at the images (`python -m flyover.display.gallery`), then commit snapshots and `docs/screens` together.
- The config block in the README is checked against `config.example.toml` by a test. Change both together.
- `--fake-network` runs the control service on a laptop with a simulated Wi-Fi (password `correct-horse`), which is the quickest way to try web UI changes.
- CI (`.github/workflows/test.yml`) runs the tests on Python 3.11 and 3.13.
- When a live reply differs from a fixture, replace the fixture with a real trimmed reply and fix the parser until the test passes.
