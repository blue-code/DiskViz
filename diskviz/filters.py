"""Advanced filter language for DiskViz.

Supports a SpaceSniffer-style mini-language combined with `;`:

- ``*.jpg`` / ``*.{jpg,png}``      → keep matching extensions / glob
- ``|*.jpg``                       → exclude matching extensions / glob
- ``>1mb``, ``<500kb``, ``>=2gb``  → size comparison (B, KB, MB, GB, TB)
- ``>2years``, ``<3months``        → modification age (s, m, h, d, w, mo, y)
- ``:red`` / ``:yellow`` / ``:green`` / ``:blue`` / ``:tagged`` / ``:all``
- anything else                    → case-insensitive substring on full path

Multiple clauses combined with ``;`` are ANDed together. Empty string
matches everything.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .model import DiskNode

TAG_NAMES = ("red", "yellow", "green", "blue")

_SIZE_UNITS = {
    "b": 1,
    "kb": 1024,
    "k": 1024,
    "mb": 1024 ** 2,
    "m": 1024 ** 2,
    "gb": 1024 ** 3,
    "g": 1024 ** 3,
    "tb": 1024 ** 4,
    "t": 1024 ** 4,
}

_AGE_UNITS = {
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
    "w": 604800.0,
    "week": 604800.0,
    "weeks": 604800.0,
    "mo": 2592000.0,
    "month": 2592000.0,
    "months": 2592000.0,
    "y": 31536000.0,
    "yr": 31536000.0,
    "year": 31536000.0,
    "years": 31536000.0,
}

_OP_RE = re.compile(r"^(>=|<=|>|<|=)?\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]+)$")

Predicate = Callable[[DiskNode], bool]


@dataclass
class FilterError(ValueError):
    """Raised when the user provides an invalid filter expression."""

    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _parse_value(token: str) -> Optional[Tuple[str, float, str]]:
    """Return (op, value, unit) tuple for ``>1mb`` style tokens, else None."""
    match = _OP_RE.match(token)
    if not match:
        return None
    op, value, unit = match.groups()
    return op or ">", float(value), unit.lower()


def _compare(value: float, op: str, threshold: float) -> bool:
    if op == ">":
        return value > threshold
    if op == ">=":
        return value >= threshold
    if op == "<":
        return value < threshold
    if op == "<=":
        return value <= threshold
    if op == "=":
        return value == threshold
    return False


def _make_size_predicate(token: str) -> Predicate:
    parsed = _parse_value(token)
    if parsed is None:
        raise FilterError(f"Invalid size filter: {token}")
    op, value, unit = parsed
    if unit not in _SIZE_UNITS:
        raise FilterError(f"Unknown size unit: {unit}")
    threshold = value * _SIZE_UNITS[unit]
    return lambda node: _compare(float(node.size), op, threshold)


def _make_age_predicate(token: str, now_ns: int) -> Predicate:
    parsed = _parse_value(token)
    if parsed is None:
        raise FilterError(f"Invalid age filter: {token}")
    op, value, unit = parsed
    if unit not in _AGE_UNITS:
        raise FilterError(f"Unknown age unit: {unit}")
    threshold_ns = int(value * _AGE_UNITS[unit] * 1_000_000_000)

    def predicate(node: DiskNode) -> bool:
        age_ns = max(0, now_ns - node.modified_ns)
        return _compare(float(age_ns), op, float(threshold_ns))

    return predicate


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a glob to a case-insensitive regex matching the basename."""
    return re.compile(fnmatch.translate(pattern), re.IGNORECASE)


def _make_glob_predicate(pattern: str, *, exclude: bool) -> Predicate:
    regex = _glob_to_regex(pattern)

    def predicate(node: DiskNode) -> bool:
        # Match against basename so `*.jpg` works regardless of directory.
        matched = bool(regex.match(node.path.name))
        if node.is_dir:
            # Directories survive both include and exclude globs so the
            # user can still drill into them when filtering files.
            return True
        return (not matched) if exclude else matched

    return predicate


def _make_tag_predicate(tag: str, tags: Dict[str, str]) -> Predicate:
    tag = tag.lower()
    if tag == "all" or tag == "tagged":
        return lambda node: str(node.path) in tags
    if tag not in TAG_NAMES:
        raise FilterError(f"Unknown tag: {tag}")

    def predicate(node: DiskNode) -> bool:
        return tags.get(str(node.path)) == tag

    return predicate


def _make_text_predicate(token: str) -> Predicate:
    needle = token.lower()

    def predicate(node: DiskNode) -> bool:
        return needle in str(node.path).lower()

    return predicate


def _classify_token(token: str) -> str:
    if not token:
        return "empty"
    if token.startswith(":"):
        return "tag"
    if token.startswith("|"):
        return "exclude"
    if _OP_RE.match(token):
        parsed = _parse_value(token)
        if parsed is None:
            return "text"
        _, _, unit = parsed
        if unit in _SIZE_UNITS:
            return "size"
        if unit in _AGE_UNITS:
            return "age"
        return "text"
    if any(ch in token for ch in "*?[]"):
        return "glob"
    return "text"


def build_predicate(
    expression: str,
    tags: Optional[Dict[str, str]] = None,
    *,
    now_ns: Optional[int] = None,
) -> Predicate:
    """Compile a filter expression into a predicate over DiskNode.

    An empty expression matches everything. Raises FilterError on bad syntax.
    """
    if tags is None:
        tags = {}
    if now_ns is None:
        now_ns = time.time_ns()

    tokens = [token.strip() for token in expression.split(";") if token.strip()]
    if not tokens:
        return lambda _node: True

    predicates: List[Predicate] = []
    for token in tokens:
        kind = _classify_token(token)
        if kind == "tag":
            predicates.append(_make_tag_predicate(token[1:], tags))
        elif kind == "exclude":
            predicates.append(_make_glob_predicate(token[1:], exclude=True))
        elif kind == "size":
            predicates.append(_make_size_predicate(token))
        elif kind == "age":
            predicates.append(_make_age_predicate(token, now_ns))
        elif kind == "glob":
            predicates.append(_make_glob_predicate(token, exclude=False))
        else:
            predicates.append(_make_text_predicate(token))

    def combined(node: DiskNode) -> bool:
        return all(predicate(node) for predicate in predicates)

    return combined


def collect_matches(root: DiskNode, predicate: Predicate) -> set:
    """Return the set of DiskNode objects whose subtree should remain visible.

    A node is kept if it matches the predicate, has an ancestor that matches
    a directory-style filter (handled by predicate), or has a matching
    descendant — this mirrors how SpaceSniffer keeps context around hits.
    """
    matches: set = set()

    def visit(node: DiskNode) -> bool:
        descendant_match = False
        for child in node.children:
            if visit(child):
                descendant_match = True
        if predicate(node) or descendant_match:
            matches.add(node)
            return True
        return False

    visit(root)
    return matches
