"""Module entry point and CLI driver for DiskViz.

Run without arguments to open the interactive GUI. The CLI flags below
turn DiskViz into a batch tool that can scan, filter and export without
ever showing a window — useful for scripts and CI.

Examples
--------

Interactive (default)::

    python -m diskviz

Scan and open the GUI on a specific folder::

    python -m diskviz --scan /Volumes/SSD

Headless scan + save snapshot::

    python -m diskviz --scan /Volumes/SSD --save out.diskviz.json --autoclose

Headless scan + export CSV with a filter applied::

    python -m diskviz --scan /Volumes/SSD --filter "*.jpg;>1mb" \
        --export-csv jpegs.csv --autoclose

Load a previously-saved snapshot::

    python -m diskviz --load out.diskviz.json
"""

from __future__ import annotations

import argparse
import csv
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from diskviz.app import run_app
except ModuleNotFoundError:  # pragma: no cover - fallback for direct script exec
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from diskviz.app import run_app  # type: ignore


def _log_launch_exception(exc: BaseException) -> None:
    try:
        log_path = Path.home() / "Library" / "Logs" / "DiskViz-launch.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
            fh.writelines(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskviz",
        description="DiskViz — treemap-based disk usage explorer.",
    )
    parser.add_argument(
        "--scan", action="append", default=[], metavar="PATH",
        help="Scan PATH on launch (may be passed multiple times to scan more roots).",
    )
    parser.add_argument(
        "--load", metavar="FILE",
        help="Load a .diskviz.json snapshot instead of scanning.",
    )
    parser.add_argument(
        "--filter", metavar="EXPR",
        help="Apply this filter expression after scan/load.",
    )
    parser.add_argument(
        "--save", metavar="DEST",
        help="Save the resulting tree to a snapshot at DEST.",
    )
    parser.add_argument(
        "--export-txt", metavar="DEST",
        help="Export the (filtered) view as a TXT report at DEST.",
    )
    parser.add_argument(
        "--export-csv", metavar="DEST",
        help="Export the (filtered) view as CSV at DEST.",
    )
    parser.add_argument(
        "--depth", type=int, default=4,
        help="Maximum scan depth (default: 4).",
    )
    parser.add_argument(
        "--show-hidden", action="store_true",
        help="Include dotfiles / hidden entries in the scan.",
    )
    parser.add_argument(
        "--follow-symlinks", action="store_true",
        help="Follow symbolic links (off by default to avoid cycles).",
    )
    parser.add_argument(
        "--include-zero", action="store_true",
        help="Include zero-byte files (hidden by default).",
    )
    parser.add_argument(
        "--autoclose", action="store_true",
        help="Run headlessly and exit after save/export — no GUI window.",
    )
    return parser


def _headless_run(args: argparse.Namespace) -> int:
    """Execute the batch operations implied by ``args`` without a GUI."""
    from diskviz.colors import classify_path, load_user_classes, make_classifier
    from diskviz.filters import build_predicate
    from diskviz.scanner import scan_many
    from diskviz.snapshot import load_snapshot, save_snapshot
    from diskviz.app import _format_mtime, format_size

    # Load tree from snapshot OR a fresh scan.
    tags: dict = {}
    if args.load:
        snap = load_snapshot(Path(args.load))
        root = snap.root
        tags = dict(snap.tags)
        print(f"loaded snapshot: {snap.source_path} ({format_size(root.size)})")
    else:
        paths = [Path(p) for p in args.scan]
        if not paths:
            print("error: --scan PATH (or --load FILE) is required for headless mode")
            return 2
        root, stats = scan_many(
            paths,
            max_depth=args.depth,
            follow_symlinks=args.follow_symlinks,
            show_hidden=args.show_hidden,
            skip_zero_size=not args.include_zero,
        )
        print(
            f"scanned: {stats.files_scanned} files, {stats.dirs_scanned} dirs, "
            f"{len(stats.permission_denied)} denied → {format_size(root.size)}"
        )

    # Compile the optional filter so save/export only includes matching nodes.
    _user_colors, ext_map = load_user_classes()
    classifier = make_classifier(ext_map) if ext_map else classify_path
    predicate = None
    if args.filter:
        predicate = build_predicate(
            args.filter, tags,
            file_classifier=classifier,
            on_warning=lambda msg: print(f"warning: {msg}", file=sys.stderr),
        )

    # Save snapshot first so it captures the whole tree pre-filter — we want
    # snapshots to be re-filterable later in the GUI.
    if args.save:
        save_snapshot(
            Path(args.save), root, tags,
            created_iso=_format_mtime(__import__("time").time_ns()),
        )
        print(f"saved snapshot → {args.save}")

    if args.export_txt or args.export_csv:
        rows = []
        for entry in root.iter_all():
            if predicate is not None and not predicate(entry):
                continue
            rows.append(entry)
        rows.sort(key=lambda n: n.size, reverse=True)

        if args.export_txt:
            with open(args.export_txt, "w", encoding="utf-8") as fh:
                fh.write(f"DiskViz report — {root.path}\n")
                fh.write(f"Generated: {_format_mtime(__import__('time').time_ns())}\n")
                if args.filter:
                    fh.write(f"Filter: {args.filter}\n")
                fh.write(f"Entries: {len(rows)}    Total: {format_size(root.size)}\n")
                fh.write("-" * 78 + "\n")
                for r in rows:
                    fh.write(f"{format_size(r.size):>12}  {r.path}\n")
            print(f"exported text → {args.export_txt}")

        if args.export_csv:
            with open(args.export_csv, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["path", "size_bytes", "size_human", "type",
                                  "tag", "modified_iso"])
                for r in rows:
                    writer.writerow([
                        str(r.path), r.size, format_size(r.size),
                        "dir" if r.is_dir else (r.path.suffix.lower() or "file"),
                        tags.get(str(r.path), ""),
                        _format_mtime(r.modified_ns),
                    ])
            print(f"exported csv → {args.export_csv}")

    return 0


def _interactive_run(args: argparse.Namespace) -> int:
    """Launch the Tk GUI, optionally preseeded with a scan/snapshot path."""
    import tkinter as tk
    from diskviz.app import DiskVizApp
    from diskviz.snapshot import load_snapshot

    root = tk.Tk()
    app = DiskVizApp(root)
    if args.load:
        try:
            snap = load_snapshot(Path(args.load))
            app.apply_snapshot(snap, Path(args.load))
        except Exception as exc:
            print(f"warning: could not load snapshot {args.load}: {exc}",
                  file=sys.stderr)
    elif args.scan:
        # Schedule the scan via the existing flow so the same UI plumbing runs.
        app.path_var.set(args.scan[0])
        for extra in args.scan[1:]:
            app.extra_paths.append(Path(extra))
        if args.depth:
            app.depth_var.set(args.depth)
        app.show_hidden.set(args.show_hidden)
        app.follow_symlinks.set(args.follow_symlinks)
        app.skip_zero_size.set(not args.include_zero)
        if args.filter:
            app.search_var.set(args.filter)
        root.after(50, app.schedule_scan)
    root.mainloop()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    has_batch_intent = bool(
        args.save or args.export_txt or args.export_csv or args.autoclose
    )
    try:
        if has_batch_intent and not args.autoclose:
            # Bare save/export without autoclose still implies a GUI run, but
            # save/export should still fire on first scan-complete. Today we
            # only short-circuit to headless when --autoclose is also set.
            return _interactive_run(args)
        if args.autoclose:
            return _headless_run(args)
        return _interactive_run(args)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - launch failure path
        _log_launch_exception(exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
