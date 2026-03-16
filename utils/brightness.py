"""Brightness interpolation from schedule."""

from datetime import datetime


def _time_to_minutes(time_str):
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def get_brightness(schedule, now=None):
    """Interpolate brightness from the schedule based on current time.

    Schedule is a list of dicts with 'time' (HH:MM) and 'brightness' (0-100).
    Returns an integer brightness value.
    """
    if not schedule:
        return 80

    if now is None:
        now = datetime.now()

    current_minutes = now.hour * 60 + now.minute

    entries = sorted(schedule, key=lambda e: _time_to_minutes(e["time"]))

    # Find the two surrounding schedule entries
    before = entries[-1]  # wrap around: last entry is "before" midnight
    after = entries[0]  # wrap around: first entry is "after" midnight

    for i, entry in enumerate(entries):
        entry_min = _time_to_minutes(entry["time"])
        if entry_min <= current_minutes:
            before = entry
            after = entries[(i + 1) % len(entries)]
        else:
            after = entry
            break

    before_min = _time_to_minutes(before["time"])
    after_min = _time_to_minutes(after["time"])

    # Handle wrap-around midnight
    if after_min <= before_min:
        after_min += 1440  # add 24 hours
    if current_minutes < before_min:
        current_minutes += 1440

    span = after_min - before_min
    if span == 0:
        return before["brightness"]

    progress = (current_minutes - before_min) / span
    progress = max(0.0, min(1.0, progress))

    brightness = before["brightness"] + progress * (
        after["brightness"] - before["brightness"]
    )
    return int(round(brightness))
