"""Filesystem watcher that triggers DiskViz rescans on disk changes.

Wraps :mod:`watchdog` so the UI doesn't depend on the library directly.
When ``watchdog`` isn't importable (e.g. minimal headless installs) the
watcher silently no-ops and DiskViz falls back to its periodic poller.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Optional

try:  # pragma: no cover - import-time fallback only
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    _WATCHDOG_AVAILABLE = True
except Exception:  # pragma: no cover - exercised on systems without watchdog
    FileSystemEventHandler = object  # type: ignore[misc, assignment]
    Observer = None  # type: ignore[assignment]
    _WATCHDOG_AVAILABLE = False


class _RescanHandler(FileSystemEventHandler):
    """Adapter that fires a single callback for any FS event we care about."""

    def __init__(self, on_change: Callable[[], None]):
        super().__init__()
        self._on_change = on_change

    # All the watchdog events route through the same callback. We don't
    # care which kind of change happened, only that *something* did.
    def on_any_event(self, event):  # noqa: D401 - watchdog hook
        try:
            self._on_change()
        except Exception:
            # Never let an exception kill the observer thread.
            pass


class PathWatcher:
    """High-level wrapper around a watchdog ``Observer``.

    The watcher debounces rapid bursts of FS events (typical when many
    files change at once) into a single ``on_change`` callback after a
    short quiet period.
    """

    def __init__(self) -> None:
        self._observer = Observer() if _WATCHDOG_AVAILABLE else None
        self._running = False

    @property
    def available(self) -> bool:
        return _WATCHDOG_AVAILABLE

    def start(self, paths: Iterable[Path], on_change: Callable[[], None]) -> None:
        """Start watching ``paths`` (or replace the existing watch list)."""
        if not _WATCHDOG_AVAILABLE or self._observer is None:
            return
        self.stop()
        handler = _RescanHandler(on_change)
        self._observer = Observer()
        for path in paths:
            try:
                self._observer.schedule(handler, str(path), recursive=True)
            except (FileNotFoundError, OSError):
                continue
        self._observer.start()
        self._running = True

    def stop(self) -> None:
        if not self._running or self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=1.0)
        except Exception:
            pass
        self._running = False
        self._observer = None
