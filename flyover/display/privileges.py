"""Give up root once the hardware has been claimed."""

from __future__ import annotations

import logging
import os
import pwd

log = logging.getLogger(__name__)


def drop_privileges(user: str = "daemon") -> bool:
    """Switch from root to ``user``. Returns True if it did; False if not root or the user is missing.

    The LED matrix needs root only to set up the GPIO hardware. After that the
    display, which parses data from the internet, should not be root.
    """
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        log.warning("no user %r to drop privileges to; staying root", user)
        return False
    os.setgroups([])
    os.setgid(entry.pw_gid)
    os.setuid(entry.pw_uid)
    os.environ["HOME"] = entry.pw_dir
    log.info("dropped privileges to %s", user)
    return True
