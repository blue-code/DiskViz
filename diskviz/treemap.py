"""Treemap layout routines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .model import DiskNode


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float

    def inset(self, padding: float) -> "Rect":
        return Rect(
            self.x + padding,
            self.y + padding,
            max(0.0, self.width - 2 * padding),
            max(0.0, self.height - 2 * padding),
        )


@dataclass
class NodeRect:
    node: DiskNode
    rect: Rect
    depth: int
    parent: Optional[DiskNode]


def slice_and_dice(
    node: DiskNode, bounds: Rect, depth: int = 0, parent: Optional[DiskNode] = None
) -> List[NodeRect]:
    """Compute a treemap layout for *node* using the slice-and-dice algorithm."""

    layouts: List[NodeRect] = [NodeRect(node=node, rect=bounds, depth=depth, parent=parent)]
    if not node.children or node.size <= 0:
        return layouts

    horizontal = depth % 2 == 0
    total = sum(child.size for child in node.children) or 1
    offset = 0.0
    for child in node.children:
        ratio = child.size / total
        if horizontal:
            child_rect = Rect(
                bounds.x + offset,
                bounds.y,
                bounds.width * ratio,
                bounds.height,
            )
            offset += child_rect.width
        else:
            child_rect = Rect(
                bounds.x,
                bounds.y + offset,
                bounds.width,
                bounds.height * ratio,
            )
            offset += child_rect.height
        layouts.extend(slice_and_dice(child, child_rect, depth + 1, node))
    return layouts


def filter_layout(layouts: Sequence[NodeRect], query: str) -> Iterable[NodeRect]:
    """Yield layout entries that match the *query* or have matching descendants."""

    if not query:
        yield from layouts
        return

    normalized = query.lower()
    parent_map = {layout.node: layout.parent for layout in layouts}
    matching_nodes = {layout.node for layout in layouts if normalized in str(layout.node.path).lower()}

    # Include ancestors of matches.
    for node in list(matching_nodes):
        parent = parent_map.get(node)
        while parent:
            if parent in matching_nodes:
                break
            matching_nodes.add(parent)
            parent = parent_map.get(parent)

    # Include descendants of matching directories for context.
    added = True
    while added:
        added = False
        for layout in layouts:
            parent = parent_map.get(layout.node)
            if parent and parent in matching_nodes and layout.node not in matching_nodes:
                matching_nodes.add(layout.node)
                added = True
    for layout in layouts:
        if layout.node in matching_nodes:
            yield layout
