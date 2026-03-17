# FlyOver

Raspberry Pi Zero 2 W plane tracker, clock, and weather display on a 32x32 RGB LED matrix.

**Default mode:** time + temperature + rain chance.
**When a plane flies overhead:** switches to show flight route (e.g., LAX→GRR) and callsign for ~30 seconds.
**First boot:** captive portal (QR code on display) for WiFi setup.
**When connected:** web dashboard at `http://flyover.local` with flight stats.

## Hardware

- Raspberry Pi Zero 2 W
- Adafruit 32x32 RGB LED Matrix
- Adafruit RGB Matrix Bonnet

## Pi System Prep

Do these steps **first**, before any code. They resolve hardware conflicts that will block you later.

### 1. Disable onboard audio (conflicts with matrix GPIO PWM)

```bash
sudo sed -i 's/dtparam=audio=on/dtparam=audio=off/' /boot/firmware/config.txt
echo "blacklist snd_bcm2835" | sudo tee /etc/modprobe.d/blacklist-audio.conf
```

### 2. Install system dependencies

```bash
sudo apt update && sudo apt install -y \
  python3-pip python3-dev python3-venv libpython3-dev \
  cmake gcc g++ git cython3 \
  libjpeg-dev zlib1g-dev libfreetype6-dev liblcms2-dev \
  libopenjp2-7-dev libtiff-dev libwebp-dev \
  python3-pil python3-pil.imagetk \
  libgraphicsmagick++-dev \
  network-manager avahi-daemon \
  fonts-dejavu
```

### 3. Add isolcpus for smoother display rendering

```bash
sudo sed -i 's/$/ isolcpus=3/' /boot/firmware/cmdline.txt
```

### 4. Reboot

```bash
sudo reboot
```

### 5. Verify audio is disabled

```bash
lsmod | grep snd
```

Should return nothing. If `snd_bcm2835` appears, do NOT proceed.

## Build and Install rgbmatrix

Do this **before** creating the project venv.

```bash
cd ~
git clone https://github.com/hzeller/rpi-rgb-led-matrix.git
cd rpi-rgb-led-matrix
```

Verify `Imaging.h` exists:

```bash
ls /usr/include/python3.*/Imaging.h
```

Build and install system-wide:

```bash
sudo pip3 install --break-system-packages cython pillow scikit-build-core --no-binary pillow
sudo pip3 install . --break-system-packages --no-build-isolation \
  --config-settings=cmake.args="-DCMAKE_C_FLAGS=-I/usr/include/python3.13 -DCMAKE_CXX_FLAGS=-I/usr/include/python3.13"
```

> Adjust `python3.13` to match your version (`python3 --version`).

Verify:

```bash
sudo python3 -c "
from rgbmatrix import RGBMatrix, RGBMatrixOptions
options = RGBMatrixOptions()
options.rows = 32
options.cols = 32
options.hardware_mapping = 'adafruit-hat'
options.gpio_slowdown = 4
matrix = RGBMatrix(options=options)
print('Matrix OK')
"
```

## Project Setup

### Create venv and install dependencies

```bash
cd ~/flyover
python3 -m venv .venv

# Build tools first
sudo /home/jordie/flyover/.venv/bin/pip install scikit-build-core cython pillow --no-binary pillow

# rgbmatrix into the venv
cd ~/rpi-rgb-led-matrix
sudo /home/jordie/flyover/.venv/bin/pip install . --no-build-isolation \
  --config-settings=cmake.args="-DCMAKE_C_FLAGS=-I/usr/include/python3.13 -DCMAKE_CXX_FLAGS=-I/usr/include/python3.13"

# All other dependencies
sudo /home/jordie/flyover/.venv/bin/pip install \
  pyyaml flask requests "qrcode[pil]" certifi

cd ~/flyover
```

### Verify imports

```bash
sudo -E /home/jordie/flyover/.venv/bin/python -c "
import rgbmatrix; print('rgbmatrix OK')
import yaml; print('pyyaml OK')
import flask; print('flask OK')
import requests; print('requests OK')
import qrcode.image.pil; print('qrcode OK')
import certifi; print('certifi:', certifi.where())
from PIL import Image; print('Pillow OK')
"
```

### Fix SSL for sudo

```bash
echo "REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt" | sudo tee -a /etc/environment
```

### Create database directory

```bash
sudo mkdir -p /var/lib/flyover
sudo chown root:root /var/lib/flyover
```

## Configuration

Edit `config.yaml` before first run:

- Set your **latitude/longitude** under `location`
- Set your **OpenWeatherMap API key** under `weather.openweathermap_api_key` (free tier works)
- Adjust `flight_tracking` radii if needed (defaults tuned for a location ~5km from an airport)

## Running

```bash
cd ~/flyover
sudo -E /home/jordie/flyover/.venv/bin/python main.py 2>&1
```

`sudo` is required for GPIO access. `-E` preserves environment variables.

## Auto-Start with systemd

```bash
sudo cp flyover.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable flyover
sudo systemctl start flyover
```

Check logs:

```bash
sudo journalctl -u flyover -f
```

## Read-Only Filesystem (Optional)

For resilience against sudden power loss:

1. Mount root filesystem read-only
2. Keep `/var/lib/flyover/` as a separate writable partition for the SQLite database
3. Disable swap: `sudo dphys-swapfile swapoff && sudo systemctl disable dphys-swapfile`
4. Add `fastboot noswap ro` to `/boot/firmware/cmdline.txt`

## Known Gotchas

1. **Venv isolation is total** — system pip packages are invisible. Install everything with `/home/jordie/flyover/.venv/bin/pip`.
2. **rgbmatrix requires root** — always run with `sudo -E` and the full venv python path.
3. **Pillow must build from source** so `Imaging.h` is available for rgbmatrix's CMake build.
4. **Hardware mapping** must be `"adafruit-hat"` — not `"regular"` or `"adafruit-hat-pwm"`.
5. **`gpio_slowdown: 4`** is correct for Pi Zero 2 W.
6. **certifi under sudo** — set `REQUESTS_CA_BUNDLE` in systemd and `/etc/environment`.
7. **`qrcode` needs `[pil]` extra** — `pip install "qrcode[pil]"`.
8. **OpenSky anonymous API** — ~400 req/day limit. Consider a free account for all-day tracking.
