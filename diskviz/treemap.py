"""Treemap layout routines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

from .model import DiskNode


@dataclass
class Rect:
    """Rectangle bounds for treemap layout.

    Attributes:
        x: Left edge coordinate
        y: Top edge coordinate
        width: Rectangle width
        height: Rectangle height
    """
    x: float
    y: float
    width: float
    height: float

    def inset(self, padding: float) -> "Rect":
        """Create a new rectangle inset by padding on all sides.

        Args:
            padding: Amount to inset on each side

        Returns:
            New Rect with inset bounds
        """
        return Rect(
            self.x + padding,
            self.y + padding,
            max(0.0, self.width - 2 * padding),
            max(0.0, self.height - 2 * padding),
        )


@dataclass
class NodeRect:
    """Represents a DiskNode with its treemap layout rectangle.

    Attributes:
        node: The filesystem node
        rect: Layout rectangle for this node
        depth: Tree depth of this node
        parent: Parent DiskNode, if any
    """
    node: DiskNode
    rect: Rect
    depth: int
    parent: Optional[DiskNode]


def slice_and_dice(
    node: DiskNode,
    bounds: Rect,
    depth: int = 0,
    parent: Optional[DiskNode] = None,
    max_depth: Optional[int] = None,
) -> List[NodeRect]:
    """Compute a treemap layout using the slice-and-dice algorithm.

    This algorithm alternates between horizontal and vertical slicing
    at each depth level, creating rectangular regions proportional
    to file/directory sizes.

    Args:
        node: Root node to layout
        bounds: Available rectangle bounds
        depth: Current tree depth (controls slice direction)
        parent: Parent node, if any
        max_depth: Maximum depth to recurse (None for entire tree)

    Returns:
        List of NodeRect entries for the entire tree
    """
    layouts: List[NodeRect] = [NodeRect(node=node, rect=bounds, depth=depth, parent=parent)]
    if not node.children or node.size <= 0 or (max_depth is not None and depth >= max_depth):
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
        layouts.extend(slice_and_dice(child, child_rect, depth + 1, node, max_depth))
    return layouts


def filter_layout(layouts: Sequence[NodeRect], query: str) -> Iterable[NodeRect]:
    """Yield layout entries matching the query or having matching descendants.

    The filter includes:
    - Nodes whose path contains the query string
    - All ancestors of matching nodes (for context)
    - All descendants of matching directories (for context)

    Args:
        layouts: Complete treemap layout to filter
        query: Search query (case-insensitive)

    Yields:
        NodeRect entries that match or provide context
    """
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
