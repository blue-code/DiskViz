"""Directory scanning utilities for DiskViz."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Tuple

from .model import DiskNode


IGNORED_NAMES = {"$Recycle.Bin", "System Volume Information", "proc", "sys", "dev"}


def _safe_stat(path: Path) -> Tuple[int, int]:
    """Safely obtain file size and modification timestamp."""

    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except (FileNotFoundError, PermissionError, OSError):
        return 0, int(time.time_ns())


def scan_directory(root: Path, max_depth: int = 4, follow_symlinks: bool = False) -> DiskNode:
    """Build a DiskNode tree representing disk usage starting at *root*."""

    root = root.expanduser().resolve()
    return _scan_node(root, 0, max_depth, follow_symlinks)


def _scan_node(path: Path, depth: int, max_depth: int, follow_symlinks: bool) -> DiskNode:
    if not follow_symlinks and path.is_symlink():
        target = path.resolve()
        size, mtime = _safe_stat(target)
        return DiskNode(path, size, False, mtime, [])

    if path.is_dir():
        size = 0
        mtime = 0
        children = []
        if depth < max_depth:
            try:
                with os.scandir(path) as it:
                    entries = sorted(it, key=lambda e: (e.is_file(), e.name.lower()))
            except (PermissionError, FileNotFoundError, OSError):
                entries = []
            for entry in entries:
                if entry.name in IGNORED_NAMES:
                    continue
                child_path = Path(entry.path)
                child_node = _scan_node(child_path, depth + 1, max_depth, follow_symlinks)
                size += child_node.size
                mtime = max(mtime, child_node.modified_ns)
                children.append(child_node)
        else:
            children = []
            # Directory summary when max depth reached
            try:
                size, mtime = _safe_stat(path)
            except Exception:
                size, mtime = 0, int(time.time_ns())
        dir_size, dir_mtime = _safe_stat(path)
        size = max(size, dir_size)
        mtime = max(mtime, dir_mtime)
        children.sort(key=lambda node: node.size, reverse=True)
        return DiskNode(path, size, True, mtime, children)

    size, mtime = _safe_stat(path)
    return DiskNode(path, size, False, mtime, [])


def flatten_snapshot(node: DiskNode) -> Iterable[Tuple[Path, int, int]]:
    """Produce a flat snapshot of path metadata for change detection."""

    for item in node.iter_all():
        yield (item.path, item.size, item.modified_ns)
