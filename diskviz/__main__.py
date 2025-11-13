"""Module entry point for running DiskViz as a script or module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import traceback

try:
    # Works when the package is discoverable on sys.path (py2app bundle, `python -m`).
    from diskviz.app import run_app
except ModuleNotFoundError:  # pragma: no cover - fallback for direct script execution
    # Allow `python diskviz/__main__.py` by injecting the project root.
    import sys

    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from diskviz.app import run_app  # type: ignore


def _log_launch_exception(exc: BaseException) -> None:
    """Write the exception to ~/Library/Logs/DiskViz-launch.log for debugging."""
    try:
        log_path = Path.home() / "Library" / "Logs" / "DiskViz-launch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
            fh.writelines(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        # If logging fails we do not want to mask the original exception.
        pass


def main() -> None:
    try:
        run_app()
    except Exception as exc:  # pragma: no cover - we only hit this when launch fails
        _log_launch_exception(exc)
        raise


if __name__ == "__main__":
    main()
