"""Change values in ``config.toml`` without disturbing the rest of the file.

The settings page and the installer edit single keys. Re-serialising the
whole file would discard the comments that document each option, so this
edits the matching line in place (keeping its trailing comment), or adds the
key to its section if it is missing. The result is validated with
``parse_config`` before it is returned.

    python -m flyover.control.config_edit /etc/flyover/config.toml hotspot.password=hunter22
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from ..config import Config, ConfigError, parse_config

_SECTION = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$")


def format_value(value: Any) -> str:
    """A TOML literal for a bool, int, float or string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        if any(ord(c) < 0x20 for c in escaped):
            raise ConfigError("control characters are not allowed in settings")
        return f'"{escaped}"'
    raise TypeError(f"cannot write {type(value).__name__} to TOML")


def _key_pattern(key: str) -> re.Pattern[str]:
    # indent, optional "# " (a commented-out default), key, "=", value, optional comment.
    # The value is a quoted string or a bare token, so a "#" inside quotes is not a comment.
    return re.compile(
        rf'^(?P<indent>\s*)(?P<hash>#\s*)?{re.escape(key)}(?P<eq>\s*=\s*)'
        r'(?P<value>"(?:[^"\\]|\\.)*"|[^#\s]*)(?P<tail>.*)$'
    )


def update_config_text(text: str, changes: dict[tuple[str, str], Any]) -> str:
    """Return ``text`` with each ``(section, key) -> value`` applied, then validated."""
    lines = text.splitlines()
    for (section, key), value in changes.items():
        literal = format_value(value)
        pattern = _key_pattern(key)
        current = None
        replaced = False
        section_end = None  # index just after the last line of the target section
        for i, line in enumerate(lines):
            m = _SECTION.match(line)
            if m:
                if current == section:
                    break
                current = m.group(1)
                if current == section:
                    section_end = i + 1
                continue
            if current != section:
                continue
            if line.strip():
                section_end = i + 1
            km = pattern.match(line)
            if km and not km.group("hash"):  # a live line; commented-out ones are handled below
                lines[i] = f'{km.group("indent")}{key}{km.group("eq")}{literal}{km.group("tail")}'
                replaced = True
                break
        if not replaced:
            lines = _insert(lines, section, key, literal, section_end, pattern)
    result = "\n".join(lines) + "\n"
    parse_config(result)  # raises ConfigError if the edit produced something invalid
    return result


def _insert(lines, section, key, literal, section_end, pattern) -> list[str]:
    """Uncomment a commented-out default if there is one, else add the key to its section."""
    in_section = False
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if m:
            in_section = m.group(1) == section
            continue
        km = pattern.match(line) if in_section else None
        if km and km.group("hash"):
            lines[i] = f'{km.group("indent")}{key}{km.group("eq")}{literal}{km.group("tail")}'
            return lines
    new = f"{key} = {literal}"
    if section_end is None:  # the section does not exist yet
        return [*lines, *([""] if lines and lines[-1].strip() else []), f"[{section}]", new]
    return [*lines[:section_end], new, *lines[section_end:]]


def write_config(path: Path, text: str) -> None:
    """Atomically replace the config file, keeping its permissions."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            tmp.chmod(path.stat().st_mode & 0o777)
        except FileNotFoundError:
            tmp.chmod(0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def apply_changes(path: Path, changes: dict[tuple[str, str], Any]) -> Config:
    """Edit the file on disk. Nothing is written if the result is invalid."""
    text = update_config_text(path.read_text(encoding="utf-8"), changes)
    write_config(path, text)
    return parse_config(text)


def _parse_assignment(arg: str) -> tuple[tuple[str, str], Any]:
    target, _, raw = arg.partition("=")
    section, _, key = target.partition(".")
    if not section or not key or not _:
        raise SystemExit(f"expected section.key=value, got {arg!r}")
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return (section, key), lowered == "true"
    for cast in (int, float):
        try:
            return (section, key), cast(raw)
        except ValueError:
            pass
    return (section, key), raw


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        apply_changes(Path(args[0]), dict(_parse_assignment(a) for a in args[1:]))
    except (ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
