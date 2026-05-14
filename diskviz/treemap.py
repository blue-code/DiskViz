"""Treemap layout routines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

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


HEADER_HEIGHT_PX = 14       # Strip reserved at top of every non-root rect for the label
HEADER_INSET_PX = 2         # Padding around the inner content
MIN_NEST_DIMENSION_PX = 14  # Stop nesting children when remaining rect is below this


def _inner_rect(bounds: Rect, depth: int) -> Optional[Rect]:
    """Return the rect that should contain children of a node, or None if
    the available space is too small to nest meaningfully.

    The canvas root (depth 0) gets no header — children fill the whole canvas.
    Every level below reserves a strip at the top for its own label so the
    SpaceSniffer-style nested look is preserved.
    """
    if depth == 0:
        return bounds
    inner = Rect(
        bounds.x + HEADER_INSET_PX,
        bounds.y + HEADER_HEIGHT_PX,
        max(0.0, bounds.width - 2 * HEADER_INSET_PX),
        max(0.0, bounds.height - HEADER_HEIGHT_PX - HEADER_INSET_PX),
    )
    if inner.width < MIN_NEST_DIMENSION_PX or inner.height < MIN_NEST_DIMENSION_PX:
        return None
    return inner


def slice_and_dice(
    node: DiskNode,
    bounds: Rect,
    depth: int = 0,
    parent: Optional[DiskNode] = None,
    max_depth: Optional[int] = None,
) -> List[NodeRect]:
    """Compute a SpaceSniffer-style nested squarified treemap.

    Each node's bounds include a small header strip (with its label) above
    its children, which are recursively laid out inside the remaining
    inner rect using the squarified algorithm.

    Args:
        node: Root node to layout
        bounds: Available rectangle bounds
        depth: Current tree depth (controls slice direction and header inset)
        parent: Parent node, if any
        max_depth: Maximum depth to recurse (None for entire tree)

    Returns:
        List of NodeRect entries for the entire tree
    """
    layouts: List[NodeRect] = [NodeRect(node=node, rect=bounds, depth=depth, parent=parent)]
    if not node.children or node.size <= 0 or (max_depth is not None and depth >= max_depth):
        return layouts

    inner = _inner_rect(bounds, depth)
    if inner is None:
        return layouts

    child_layouts = _squarify_children(node.children, inner, depth)
    for child, child_rect in child_layouts:
        layouts.extend(slice_and_dice(child, child_rect, depth + 1, node, max_depth))
    return layouts


def _squarify_children(children: Sequence[DiskNode], bounds: Rect, depth: int) -> List[Tuple[DiskNode, Rect]]:
    """Compute squarified treemap layout (SpaceSniffer style).

    Uses the squarified algorithm to create rectangles with aspect ratios
    close to 1, making items more readable and visually balanced.

    Args:
        children: Child nodes to layout
        bounds: Available rectangle bounds
        depth: Current depth (not used in squarified, but kept for API consistency)

    Returns:
        List of (child, rect) tuples
    """
    if not children:
        return []

    # Sort by size (largest first) - crucial for squarified algorithm
    sorted_children = sorted(children, key=lambda c: c.size, reverse=True)
    total_size = sum(max(child.size, 1) for child in sorted_children) or 1
    total_area = bounds.width * bounds.height or 1

    # Convert to (node, area) tuples for squarify algorithm
    areas = [(child, max(child.size, 1) / total_size * total_area) for child in sorted_children]

    result: List[Tuple[DiskNode, Rect]] = []
    _squarify(areas, [], bounds, result, depth_limit=200)

    return result


def _squarify(
    items: Sequence[Tuple[DiskNode, float]],
    row: List[Tuple[DiskNode, float]],
    rect: Rect,
    acc: List[Tuple[DiskNode, Rect]],
    depth_limit: int,
) -> None:
    """Iterative squarify algorithm to prevent stack overflow."""
    # Use iterative approach with explicit stack instead of recursion
    stack: List[Tuple[Sequence[Tuple[DiskNode, float]], List[Tuple[DiskNode, float]], Rect]] = [(items, row, rect)]

    while stack:
        current_items, current_row, current_rect = stack.pop()

        # Process items iteratively
        while current_items:
            if not current_row:
                # Start a new row with first item
                current_row = [current_items[0]]
                current_items = current_items[1:]
            else:
                # Try adding next item to current row
                if not current_items:
                    break
                first = current_items[0]
                new_row = current_row + [first]

                if _worst_ratio(new_row, current_rect) <= _worst_ratio(current_row, current_rect):
                    # Adding item improves ratio, add it to row
                    current_row = new_row
                    current_items = current_items[1:]
                else:
                    # Adding item worsens ratio, layout current row and start new one
                    current_rect = _layout_row(current_row, current_rect, acc)
                    current_row = []

        # Layout remaining row
        if current_row:
            _layout_row(current_row, current_rect, acc)


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
    """Lay out one squarify "row" perpendicular to the shorter side.

    Canonical squarified treemap (Bruls, Huijsen, van Wijk 1999): the row is
    placed against the shorter edge of the available rect, so each tile in the
    row fills the full short dimension and only its long dimension varies. This
    is what keeps tile aspect ratios close to 1 and produces the mosaic look.
    """
    if not row:
        return rect

    row_area = sum(area for _, area in row)
    if rect.width >= rect.height:
        # Wide rect → peel a vertical column from the left, stack tiles top-to-bottom.
        row_width = row_area / max(rect.height, 1e-6)
        y = rect.y
        for child, area in row:
            tile_height = area / max(row_width, 1e-6)
            acc.append((child, Rect(rect.x, y, row_width, tile_height)))
            y += tile_height
        return Rect(rect.x + row_width, rect.y, max(rect.width - row_width, 0), rect.height)

    # Tall rect → peel a horizontal strip from the top, stack tiles left-to-right.
    row_height = row_area / max(rect.width, 1e-6)
    x = rect.x
    for child, area in row:
        tile_width = area / max(row_height, 1e-6)
        acc.append((child, Rect(x, rect.y, tile_width, row_height)))
        x += tile_width
    return Rect(rect.x, rect.y + row_height, rect.width, max(rect.height - row_height, 0))


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


def filter_layout(
    layouts: Sequence[NodeRect],
    query: str,
    predicate: Optional[Callable[[DiskNode], bool]] = None,
) -> Iterable[NodeRect]:
    """Yield layout entries matching the query/predicate or providing context.

    When ``predicate`` is supplied it is used as the per-node test; otherwise
    we fall back to case-insensitive substring matching against the path.
    Ancestors and descendants of matches are preserved so context isn't lost.
    """
    if predicate is None:
        if not query:
            yield from layouts
            return
        normalized = query.lower()
        predicate = lambda node: normalized in str(node.path).lower()

    parent_map = {layout.node: layout.parent for layout in layouts}
    matching_nodes = {layout.node for layout in layouts if predicate(layout.node)}

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
