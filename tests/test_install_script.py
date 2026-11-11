"""The installer itself can't run here (it needs apt, systemd and a Pi), but every file edit it
makes lives in a function that is exercised below against temporary files."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "install.sh"


def bash(snippet: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {snippet}', "bash", *args],
                          capture_output=True, text=True, timeout=30)


def test_script_has_valid_bash_syntax():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_sourcing_the_script_does_not_run_the_installation():
    result = bash("echo sourced")
    assert result.returncode == 0 and result.stdout.strip() == "sourced"


def test_help_and_bad_options():
    ok = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert ok.returncode == 0 and "--no-matrix" in ok.stdout
    bad = subprocess.run(["bash", str(SCRIPT), "--bogus"], capture_output=True, text=True)
    assert bad.returncode == 1 and "unknown option" in bad.stderr


@pytest.mark.parametrize("before, after", [
    ("dtparam=audio=on\n", "dtparam=audio=off\n"),
    ("dtparam=i2c_arm=on\ndtparam=audio=on\ndtoverlay=vc4\n", "dtparam=i2c_arm=on\ndtparam=audio=off\ndtoverlay=vc4\n"),
    ("dtparam=audio=off\n", "dtparam=audio=off\n"),  # already done: untouched
    ("#dtparam=audio=on\n", "#dtparam=audio=on\n\ndtparam=audio=off\n"),  # a comment is not a setting
    ("[all]\n", "[all]\n\ndtparam=audio=off\n"),
])
def test_audio_is_switched_off_once(tmp_path, before, after):
    f = tmp_path / "config.txt"
    f.write_text(before)
    assert bash('set_audio_off "$1"', str(f)).returncode == 0
    assert f.read_text() == after
    bash('set_audio_off "$1"', str(f))  # running again changes nothing
    assert f.read_text() == after


def test_a_missing_config_txt_is_not_an_error(tmp_path):
    assert bash('set_audio_off "$1"', str(tmp_path / "absent")).returncode == 0


@pytest.mark.parametrize("before", [
    "console=serial0,115200 root=PARTUUID=abc rootwait quiet\n",
    "console=serial0,115200 root=PARTUUID=abc rootwait quiet",  # no trailing newline
    "console=tty1 root=/dev/mmcblk0p2   \n",  # trailing spaces
])
def test_kernel_flags_stay_on_one_line_and_are_added_once(tmp_path, before):
    f = tmp_path / "cmdline.txt"
    f.write_text(before)
    flags = "isolcpus=domain,managed_irq,3 nohz_full=3"
    bash('append_cmdline_flags "$1" "$2"', str(f), flags)
    text = f.read_text()
    assert len(text.strip().splitlines()) == 1 and text.strip().endswith(flags)
    assert "  " not in text.strip()
    bash('append_cmdline_flags "$1" "$2"', str(f), flags)
    assert f.read_text() == text  # idempotent


def test_the_flags_the_script_uses_match_the_drivers_documented_set():
    out = bash('echo "$CMDLINE_FLAGS"').stdout.strip()
    assert out == "isolcpus=domain,managed_irq,3 nohz_full=3 rcu_nocbs=3 irqaffinity=0,1,2"


def test_audio_blacklist_file(tmp_path):
    f = tmp_path / "blacklist.conf"
    bash('write_audio_blacklist "$1"', str(f))
    assert "blacklist snd_bcm2835" in f.read_text()


def test_units_are_rendered_for_the_checkout_location(tmp_path):
    for unit in ("flyover-display", "flyover-control"):
        out = tmp_path / f"{unit}.service"
        bash('render_unit "$1" "$2" "$3"', str(ROOT / "deploy" / "systemd" / f"{unit}.service"),
             str(out), "/opt/fly over")
        text = out.read_text()
        assert "@INSTALL_DIR@" not in text
        assert "ExecStart=/opt/fly over/.venv/bin/python -m flyover." in text


def test_generated_hotspot_passwords_are_valid_for_wpa():
    out = bash("random_password").stdout.strip()
    assert len(out) == 10 and out.isalnum() and out == out.lower()
    assert bash("random_password").stdout.strip() != out


@pytest.mark.parametrize("module", ["flyover.display", "flyover.control"])
def test_the_modules_named_in_the_units_run(module):
    r = subprocess.run([sys.executable, "-m", module, "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "--config" in r.stdout


def test_units_start_the_modules_that_exist_and_share_the_run_directory():
    display = (ROOT / "deploy/systemd/flyover-display.service").read_text()
    control = (ROOT / "deploy/systemd/flyover-control.service").read_text()
    assert "-m flyover.display" in display and "-m flyover.control" in control
    assert "StartLimitIntervalSec=0" in display and "Restart=always" in control
    assert "CPUAffinity" not in display  # would stop the driver pinning its thread to the isolated core
    assert "d /run/flyover 0775 root daemon" in (ROOT / "deploy/tmpfiles.d/flyover.conf").read_text()
    assert "address=/#/10.42.0.1" in (ROOT / "deploy/nm/flyover-captive.conf").read_text()
