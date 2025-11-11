"""Directory scanning utilities for DiskViz."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Tuple

from .model import DiskNode


# System directories and files to skip during scanning
IGNORED_NAMES = {"$Recycle.Bin", "System Volume Information", "proc", "sys", "dev"}


@dataclass
class ScanStats:
    """Statistics collected during directory scanning.

    Attributes:
        files_scanned: Number of files successfully scanned
        dirs_scanned: Number of directories successfully scanned
        permission_denied: List of paths where access was denied
        errors: List of paths where other errors occurred
    """
    files_scanned: int = 0
    dirs_scanned: int = 0
    permission_denied: List[Path] = field(default_factory=list)
    errors: List[Path] = field(default_factory=list)


def _safe_stat(path: Path) -> Tuple[int, int]:
    """Safely obtain file size and modification timestamp.

    Args:
        path: Path to file or directory to stat

    Returns:
        Tuple of (size_in_bytes, modification_time_in_nanoseconds)
        Returns (0, current_time) if stat fails
    """
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except (FileNotFoundError, PermissionError, OSError):
        return 0, int(time.time_ns())


def scan_directory(
    root: Path, max_depth: int = 4, follow_symlinks: bool = False
) -> Tuple[DiskNode, ScanStats]:
    """Build a DiskNode tree representing disk usage starting at root.

    Args:
        root: Root directory to scan
        max_depth: Maximum depth to recurse into subdirectories
        follow_symlinks: Whether to follow symbolic links

    Returns:
        Tuple of (DiskNode tree, ScanStats with collection statistics)
    """
    root = root.expanduser().resolve()
    stats = ScanStats()
    node = _scan_node(root, 0, max_depth, follow_symlinks, stats)
    return node, stats


def _scan_node(
    path: Path, depth: int, max_depth: int, follow_symlinks: bool, stats: ScanStats
) -> DiskNode:
    """Recursively scan a single filesystem node.

    Args:
        path: Path to scan
        depth: Current recursion depth
        max_depth: Maximum depth to recurse
        follow_symlinks: Whether to follow symbolic links
        stats: Statistics collector

    Returns:
        DiskNode representing this path and its children
    """
    if not follow_symlinks and path.is_symlink():
        target = path.resolve()
        size, mtime = _safe_stat(target)
        stats.files_scanned += 1
        return DiskNode(path, size, False, mtime, [])

    if path.is_dir():
        size = 0
        mtime = 0
        children = []
        if depth < max_depth:
            try:
                with os.scandir(path) as it:
                    entries = sorted(it, key=lambda e: (e.is_file(), e.name.lower()))
            except PermissionError:
                stats.permission_denied.append(path)
                entries = []
            except (FileNotFoundError, OSError) as e:
                stats.errors.append(path)
                entries = []

            for entry in entries:
                if entry.name in IGNORED_NAMES:
                    continue
                child_path = Path(entry.path)
                child_node = _scan_node(child_path, depth + 1, max_depth, follow_symlinks, stats)
                size += child_node.size
                mtime = max(mtime, child_node.modified_ns)
                children.append(child_node)
        else:
            children = []
            # Directory summary when max depth reached
            size, mtime = _safe_stat(path)

        dir_size, dir_mtime = _safe_stat(path)
        size = max(size, dir_size)
        mtime = max(mtime, dir_mtime)
        children.sort(key=lambda node: node.size, reverse=True)
        stats.dirs_scanned += 1
        return DiskNode(path, size, True, mtime, children)

    size, mtime = _safe_stat(path)
    stats.files_scanned += 1
    return DiskNode(path, size, False, mtime, [])


def flatten_snapshot(node: DiskNode) -> Iterable[Tuple[Path, int, int]]:
    """Produce a flat snapshot of path metadata for change detection.

    Args:
        node: Root node to flatten

    Yields:
        Tuples of (path, size, modification_time) for all nodes in the tree
    """
    for item in node.iter_all():
        yield (item.path, item.size, item.modified_ns)
