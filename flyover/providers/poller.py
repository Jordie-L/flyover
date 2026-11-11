"""Run a fetch function on a timer in a background thread.

The main loop only ever reads ``latest``, so it can never block on the
network. After a failure the wait before the next try doubles (up to
``max_backoff_s``) and returns to normal after one success. Flights reuse this
in a later phase.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)


class Poller(Generic[T]):
    def __init__(
        self,
        name: str,
        fetch: Callable[[], T],
        interval_s: float,
        *,
        max_backoff_s: float = 300.0,
        on_result: Callable[[T], None] | None = None,
    ) -> None:
        self.name = name
        self._fetch = fetch
        self._interval = interval_s
        self._max_backoff = max_backoff_s
        self._on_result = on_result
        self._lock = threading.Lock()
        self._value: T | None = None
        self._error: str | None = None
        self._failures = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- reading the result (any thread) ---------------------------------

    @property
    def latest(self) -> T | None:
        with self._lock:
            return self._value

    @property
    def error(self) -> str | None:
        """The last failure's message, or None if the last attempt worked."""
        with self._lock:
            return self._error

    # -- one attempt (also used directly by tests and ``--once``) ---------

    def step(self) -> float:
        """Fetch once and return how many seconds to wait before the next try."""
        try:
            value = self._fetch()
        except Exception as exc:  # a provider may fail in any way; never kill the thread
            with self._lock:
                self._failures += 1
                self._error = str(exc) or exc.__class__.__name__
                failures = self._failures
            delay = min(self._max_backoff, self._interval * 2**failures)
            log.warning("%s: %s (retrying in %.0f s)", self.name, self._error, delay)
            return delay
        with self._lock:
            recovered = self._failures > 0
            self._value, self._error, self._failures = value, None, 0
        if recovered:
            log.info("%s: recovered", self.name)
        if self._on_result is not None:
            try:
                self._on_result(value)
            except Exception:
                log.exception("%s: result handler failed", self.name)
        return self._interval

    # -- thread control ---------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"poll-{self.name}", daemon=True)
        self._thread.start()

    def poke(self) -> None:
        """Fetch again as soon as possible (for example after the location changed)."""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            delay = self.step()
            self._wake.wait(delay)
            self._wake.clear()
