#!/usr/bin/env bash
# Install or update Flyover on a Raspberry Pi running Raspberry Pi OS (Bookworm or later).
#
#   sudo deploy/install.sh [--no-matrix] [--volatile-logs] [--no-watchdog]
#
# Safe to run again: every step checks before it changes anything. Run it from the
# checkout you want to install from; services point at that directory.
#
#   --no-matrix      skip the LED matrix driver and the boot settings it needs
#                    (use this on a Pi with no panel; the display runs in preview mode)
#   --volatile-logs  keep the system journal in RAM to spare the SD card
#   --no-watchdog    do not enable the hardware watchdog
#
# Environment:
#   RGB_MATRIX_REF   git ref of rpi-rgb-led-matrix to build (default: master)

set -euo pipefail

RGB_MATRIX_URL="https://github.com/hzeller/rpi-rgb-led-matrix"
RGB_MATRIX_REF="${RGB_MATRIX_REF:-master}"
RGB_MATRIX_DIR="/opt/rpi-rgb-led-matrix"
# One CPU core is reserved for the panel's refresh thread (the driver's own recommendation).
CMDLINE_FLAGS="isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0,1,2"

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# -- file edits (kept as functions so they can be tested against temp files) ---------------

# Turn onboard audio off. The matrix driver refuses to start while it is loaded.
set_audio_off() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    if grep -qE '^[[:space:]]*dtparam=audio=off' "$file"; then
        return 0
    elif grep -qE '^[[:space:]]*dtparam=audio=on' "$file"; then
        sed -i -E 's/^[[:space:]]*dtparam=audio=on/dtparam=audio=off/' "$file"
    else
        printf '\ndtparam=audio=off\n' >>"$file"
    fi
}

# Append kernel arguments to the single line of cmdline.txt (never add a newline).
append_cmdline_flags() {
    local file="$1" flags="$2"
    [[ -f "$file" ]] || { warn "$file not found; add '$flags' to the kernel command line yourself"; return 0; }
    grep -q 'isolcpus' "$file" && return 0
    sed -i -e '1s/[[:space:]]*$//' -e "1s/\$/ ${flags}/" "$file"
}

write_audio_blacklist() {
    printf '# Flyover: the LED matrix driver needs the timer that onboard audio uses.\nblacklist snd_bcm2835\n' >"$1"
}

# Copy a unit file, filling in where this checkout lives.
render_unit() {
    local src="$1" dst="$2" install_dir="$3"
    sed "s|@INSTALL_DIR@|${install_dir}|g" "$src" >"$dst"
}

# Print a random lowercase+digit string.
random_password() {
    python3 -c 'import secrets, string; print("".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)))'
}

boot_dir() {
    if [[ -d /boot/firmware ]]; then echo /boot/firmware; else echo /boot; fi
}

# -- the installation ---------------------------------------------------------------------------

main() {
    local with_matrix=1 volatile_logs=0 watchdog=1
    for arg in "$@"; do
        case "$arg" in
            --no-matrix) with_matrix=0 ;;
            --volatile-logs) volatile_logs=1 ;;
            --no-watchdog) watchdog=0 ;;
            -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
            *) die "unknown option: $arg (see --help)" ;;
        esac
    done

    [[ $EUID -eq 0 ]] || die "run as root: sudo deploy/install.sh"
    local repo venv boot reboot_needed=0
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    venv="$repo/.venv"
    boot="$(boot_dir)"
    systemctl is-active --quiet NetworkManager \
        || die "NetworkManager is not running. Flyover needs it for Wi-Fi (the default on current Raspberry Pi OS)."

    log "Installing system packages"
    export DEBIAN_FRONTEND=noninteractive
    local packages=(python3-venv python3-dev python3-pip git iw dnsmasq-base)
    if [[ $with_matrix -eq 1 ]]; then
        packages+=(cython3 build-essential)  # to build the matrix driver's Python binding
    fi
    apt-get update -qq
    apt-get install -y -qq "${packages[@]}"

    log "Creating the Python environment in $venv"
    python3 -m venv "$venv"
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet -e "$repo"

    if [[ $with_matrix -eq 1 ]]; then
        log "Building the LED matrix driver ($RGB_MATRIX_REF)"
        if [[ ! -d "$RGB_MATRIX_DIR/.git" ]]; then
            git clone --quiet "$RGB_MATRIX_URL" "$RGB_MATRIX_DIR"
        fi
        git -C "$RGB_MATRIX_DIR" fetch --quiet origin "$RGB_MATRIX_REF" 2>/dev/null || true
        git -C "$RGB_MATRIX_DIR" checkout --quiet "$RGB_MATRIX_REF"
        make -C "$RGB_MATRIX_DIR" build-python PYTHON="$venv/bin/python" >/dev/null
        make -C "$RGB_MATRIX_DIR" install-python PYTHON="$venv/bin/python" >/dev/null
        "$venv/bin/python" -c "import rgbmatrix" \
            || warn "the rgbmatrix module did not install; the display will use the preview backend"

        log "Preparing the Pi for the matrix driver"
        set_audio_off "$boot/config.txt"
        write_audio_blacklist /etc/modprobe.d/blacklist-flyover-audio.conf
        if ! grep -q 'isolcpus' "$boot/cmdline.txt" 2>/dev/null; then
            append_cmdline_flags "$boot/cmdline.txt" "$CMDLINE_FLAGS"
            reboot_needed=1
        fi
        lsmod | grep -q '^snd_bcm2835' && reboot_needed=1
    fi

    log "Creating directories and configuration"
    install -d -m 0755 /etc/flyover
    install -d -m 0755 -o daemon -g daemon /var/lib/flyover
    install -m 0644 "$repo/deploy/tmpfiles.d/flyover.conf" /etc/tmpfiles.d/flyover.conf
    systemd-tmpfiles --create /etc/tmpfiles.d/flyover.conf
    local config=/etc/flyover/config.toml
    if [[ ! -f "$config" ]]; then
        install -m 0644 "$repo/config.example.toml" "$config"
    fi
    local hotspot_password=""
    if grep -q 'change-me-now' "$config"; then
        hotspot_password="$(random_password)"
        "$venv/bin/python" -m flyover.control.config_edit "$config" "hotspot.password=$hotspot_password"
    fi

    log "Setting up the captive portal for the setup hotspot"
    install -d /etc/NetworkManager/dnsmasq-shared.d
    install -m 0644 "$repo/deploy/nm/flyover-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/flyover.conf

    if [[ $watchdog -eq 1 ]]; then
        log "Enabling the hardware watchdog"
        install -d /etc/systemd/system.conf.d
        printf '[Manager]\nRuntimeWatchdogSec=15\nRebootWatchdogSec=2min\n' >/etc/systemd/system.conf.d/flyover-watchdog.conf
    fi
    if [[ $volatile_logs -eq 1 ]]; then
        log "Keeping logs in RAM"
        install -d /etc/systemd/journald.conf.d
        printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n' >/etc/systemd/journald.conf.d/flyover.conf
        systemctl restart systemd-journald
    fi

    log "Installing the services"
    for unit in flyover-display flyover-control; do
        render_unit "$repo/deploy/systemd/$unit.service" "/etc/systemd/system/$unit.service" "$repo"
    done
    systemctl daemon-reload
    systemctl enable flyover-display.service flyover-control.service >/dev/null
    systemctl restart flyover-control.service flyover-display.service

    log "Checking Wi-Fi settings"
    if command -v raspi-config >/dev/null; then
        [[ -n "$(raspi-config nonint get_wifi_country 2>/dev/null || true)" ]] \
            || warn "no Wi-Fi country is set, so the setup hotspot cannot start. Run: sudo raspi-config nonint do_wifi_country <two-letter code, e.g. US>"
    fi
    rfkill unblock wifi 2>/dev/null || true

    log "Done"
    echo "Web UI:  http://$(hostname).local/"
    [[ -n "$hotspot_password" ]] && echo "Setup hotspot password (saved in $config): $hotspot_password"
    echo "Set the location in the web UI (Settings) or in $config."
    if [[ $reboot_needed -eq 1 ]]; then
        echo "Reboot now to apply the boot settings:  sudo reboot"
    fi
}

# Run main unless this file is being sourced (the tests source it to exercise the functions).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
