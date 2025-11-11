"""Data structures representing directory scans for DiskViz."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


@dataclass
class DiskNode:
    """Representation of a file system entry in the treemap."""

    path: Path
    size: int
    is_dir: bool
    modified_ns: int
    children: List["DiskNode"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    def iter_all(self) -> Iterable["DiskNode"]:
        """Yield this node and all of its descendants."""

        yield self
        for child in self.children:
            yield from child.iter_all()

    def find_by_path(self, target: Path) -> Optional["DiskNode"]:
        """Find a node by path."""

        if self.path == target:
            return self
        for child in self.children:
            match = child.find_by_path(target)
            if match:
                return match
        return None
