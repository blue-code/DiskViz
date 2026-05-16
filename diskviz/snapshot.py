"""Persist DiskViz scan trees + tags to a portable JSON snapshot file.

The format is intentionally plain JSON so the file is easy to inspect,
hand-edit, and version-control. Files end in ``.diskviz.json``. Loading is
the inverse: rebuild a :class:`DiskNode` tree and the tag map from the file
without touching the filesystem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .model import DiskNode

SNAPSHOT_MAGIC = "diskviz-snapshot"
SNAPSHOT_VERSION = 1


@dataclass
class Snapshot:
    """Parsed snapshot — root tree, tag map, plus metadata."""

    root: DiskNode
    tags: Dict[str, str] = field(default_factory=dict)
    source_path: str = ""
    created_iso: str = ""


def _node_to_dict(node: DiskNode) -> Dict[str, Any]:
    return {
        "path": str(node.path),
        "size": int(node.size),
        "is_dir": bool(node.is_dir),
        "modified_ns": int(node.modified_ns),
        "children": [_node_to_dict(child) for child in node.children],
    }


def _node_from_dict(data: Dict[str, Any]) -> DiskNode:
    return DiskNode(
        path=Path(data["path"]),
        size=int(data["size"]),
        is_dir=bool(data["is_dir"]),
        modified_ns=int(data["modified_ns"]),
        children=[_node_from_dict(child) for child in data.get("children", [])],
    )


def save_snapshot(
    path: Path,
    root: DiskNode,
    tags: Dict[str, str],
    *,
    created_iso: str = "",
) -> None:
    """Write ``root`` (and ``tags``) to ``path`` as JSON."""
    payload = {
        "magic": SNAPSHOT_MAGIC,
        "version": SNAPSHOT_VERSION,
        "created": created_iso,
        "source": str(root.path),
        "tags": tags,
        "tree": _node_to_dict(root),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_snapshot(path: Path) -> Snapshot:
    """Parse a JSON snapshot at ``path``. Raises ValueError on bad input."""
    with Path(path).open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or payload.get("magic") != SNAPSHOT_MAGIC:
        raise ValueError(f"Not a DiskViz snapshot: {path}")
    if int(payload.get("version", 0)) > SNAPSHOT_VERSION:
        raise ValueError(
            f"Snapshot version {payload['version']} is newer than supported "
            f"({SNAPSHOT_VERSION})"
        )
    tree = _node_from_dict(payload["tree"])
    tags = dict(payload.get("tags", {}))
    return Snapshot(
        root=tree,
        tags=tags,
        source_path=str(payload.get("source", "")),
        created_iso=str(payload.get("created", "")),
    )
