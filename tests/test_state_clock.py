from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flyover import clock
from flyover.state import RunPaths, internet_down, read_json, write_json_atomic


def test_json_round_trip_and_no_temp_files_left(tmp_path):
    path = tmp_path / "x.json"
    write_json_atomic(path, {"a": 1})
    assert read_json(path) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_read_json_tolerates_missing_and_broken_files(tmp_path):
    assert read_json(tmp_path / "none.json") is None
    assert read_json(tmp_path / "none.json", default={}) == {}
    broken = tmp_path / "broken.json"
    broken.write_text('{"half": ')
    assert read_json(broken, default="fallback") == "fallback"


def test_run_paths(tmp_path):
    paths = RunPaths(tmp_path)
    assert paths.frame == tmp_path / "frame.png"
    assert paths.status.name == "status.json" and paths.net.name == "net.json"


def test_internet_down_only_when_explicit():
    assert internet_down({"internet": False})
    assert not internet_down({"internet": True})
    assert not internet_down({})
    assert not internet_down(None)
    assert not internet_down("garbage")


def test_time_trusted_follows_the_timesync_marker(tmp_path):
    systemd, marker = tmp_path / "system", tmp_path / "synchronized"
    assert clock.time_is_trusted(systemd_dir=systemd, marker=marker)  # no systemd: assume fine
    systemd.mkdir()
    assert not clock.time_is_trusted(systemd_dir=systemd, marker=marker)
    marker.touch()
    assert clock.time_is_trusted(systemd_dir=systemd, marker=marker)


def test_timezone_priority_is_config_then_saved_then_system():
    assert clock.resolve_timezone("Europe/Paris", "Asia/Tokyo") == ZoneInfo("Europe/Paris")
    assert clock.resolve_timezone("", "Asia/Tokyo") == ZoneInfo("Asia/Tokyo")
    assert clock.resolve_timezone("", "") is None
    assert clock.resolve_timezone("Not/AZone", "Asia/Tokyo") == ZoneInfo("Asia/Tokyo")


def test_now_in_uses_the_zone():
    tokyo = clock.now_in(ZoneInfo("Asia/Tokyo"))
    utc = datetime.now(timezone.utc)
    assert abs((tokyo - utc).total_seconds()) < 5
    assert tokyo.utcoffset().total_seconds() == 9 * 3600
    assert clock.now_in(None).tzinfo is None


def test_saved_timezone_round_trip(tmp_path):
    assert clock.load_saved_timezone(tmp_path) == ""
    clock.save_timezone(tmp_path, "America/Chicago")
    assert clock.load_saved_timezone(tmp_path) == "America/Chicago"
    clock.save_timezone(tmp_path, "garbage")  # ignored
    assert clock.load_saved_timezone(tmp_path) == "America/Chicago"
