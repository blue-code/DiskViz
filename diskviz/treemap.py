"""Treemap layout routines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

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

    child_layouts = _squarify_children(node.children, bounds)
    for child, child_rect in child_layouts:
        layouts.extend(slice_and_dice(child, child_rect, depth + 1, node, max_depth))
    return layouts


def _squarify_children(children: Sequence[DiskNode], bounds: Rect) -> List[Tuple[DiskNode, Rect]]:
    """Compute squarified rectangles for a set of children."""
    if not children:
        return []

    sorted_children = sorted(children, key=lambda c: c.size, reverse=True)
    total_size = sum(max(child.size, 1) for child in sorted_children) or 1
    total_area = bounds.width * bounds.height or 1
    areas = [(child, max(child.size, 1) / total_size * total_area) for child in sorted_children]

    result: List[Tuple[DiskNode, Rect]] = []
    _squarify(areas, [], bounds, result, depth_limit=2000)
    return result


def _squarify(
    items: Sequence[Tuple[DiskNode, float]],
    row: List[Tuple[DiskNode, float]],
    rect: Rect,
    acc: List[Tuple[DiskNode, Rect]],
    depth_limit: int,
) -> None:
    if depth_limit <= 0:
        # Fallback to simple slicing to prevent recursion blow-up
        _layout_simple(items, rect, acc)
        return
    if not items:
        if row:
            _layout_row(row, rect, acc)
        return

    first = items[0]
    new_row = row + [first]
    if not row or _worst_ratio(new_row, rect) <= _worst_ratio(row, rect):
        _squarify(items[1:], new_row, rect, acc, depth_limit - 1)
    else:
        new_rect = _layout_row(row, rect, acc)
        _squarify(items, [], new_rect, acc, depth_limit - 1)


def _layout_simple(
    items: Sequence[Tuple[DiskNode, float]],
    rect: Rect,
    acc: List[Tuple[DiskNode, Rect]],
) -> None:
    if not items:
        return
    horizontal = rect.width >= rect.height
    total = sum(area for _, area in items) or 1
    offset = 0.0
    for child, area in items:
        ratio = area / total
        if horizontal:
            width = rect.width * ratio
            acc.append((child, Rect(rect.x + offset, rect.y, width, rect.height)))
            offset += width
        else:
            height = rect.height * ratio
            acc.append((child, Rect(rect.x, rect.y + offset, rect.width, height)))
            offset += height


def _layout_row(
    row: Sequence[Tuple[DiskNode, float]],
    rect: Rect,
    acc: List[Tuple[DiskNode, Rect]],
) -> Rect:
    """Lay out a row either horizontally or vertically."""
    if not row:
        return rect

    row_area = sum(area for _, area in row)
    horizontal = rect.width >= rect.height
    if horizontal:
        row_height = row_area / max(rect.width, 1e-6)
        x = rect.x
        for child, area in row:
            width = area / max(row_height, 1e-6)
            acc.append((child, Rect(x, rect.y, width, row_height)))
            x += width
        return Rect(rect.x, rect.y + row_height, rect.width, max(rect.height - row_height, 0))

    row_width = row_area / max(rect.height, 1e-6)
    y = rect.y
    for child, area in row:
        height = area / max(row_width, 1e-6)
        acc.append((child, Rect(rect.x, y, row_width, height)))
        y += height
    return Rect(rect.x + row_width, rect.y, max(rect.width - row_width, 0), rect.height)


def _worst_ratio(row: Sequence[Tuple[DiskNode, float]], rect: Rect) -> float:
    """Measure how 'square' the row would be in the remaining rectangle."""
    if not row:
        return float("inf")
    short_side = max(min(rect.width, rect.height), 1e-6)
    areas = [max(area, 1e-6) for _, area in row]
    total = sum(areas)
    max_area = max(areas)
    min_area = min(areas)
    return max((short_side ** 2 * max_area) / (total ** 2), (total ** 2) / (short_side ** 2 * min_area))


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
