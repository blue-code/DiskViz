"""Advanced filter language for DiskViz.

Mini-language for matching files in the treemap. Combine clauses with ``;``:

- ``*.jpg`` / ``*.{jpg,png}``      — keep matching file names
- ``|*.jpg``                       — exclude matching files
- ``\\temp``                       — keep files whose path contains a folder
                                     part matching the glob (any nesting level)
- ``|\\node_modules``              — exclude files under matching folders
- ``>1mb``, ``<500kb``, ``>=2gb``  — size comparison (B/KB/MB/GB/TB)
- ``>2years``, ``<3months``        — modification age (s/m/h/d/w/mo/y)
- ``c>1year`` / ``a<3months``      — creation / access age prefix (modify
                                     fallback for now, warning surfaced)
- ``:red`` / ``:tag:red+green-b``  — tag membership (4-color system)
- ``:class:audio``                 — match by file-type class
- anything else                    — case-insensitive substring on the path

Grouping rules:

- Tokens of the same kind (glob, tag, class) are **OR**-ed together.
- Different kinds (e.g. glob vs size) are **AND**-ed together.
- Exclude tokens (``|...``) are always AND-chained.

Empty expression matches everything.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .model import DiskNode

TAG_NAMES = ("red", "yellow", "green", "blue")
TAG_ALIASES = {
    "r": "red", "red": "red", "1": "red",
    "y": "yellow", "yellow": "yellow", "2": "yellow",
    "g": "green", "green": "green", "3": "green",
    "b": "blue", "blue": "blue", "blu": "blue", "4": "blue",
}

_SIZE_UNITS = {
    "b": 1, "kb": 1024, "k": 1024,
    "mb": 1024 ** 2, "m": 1024 ** 2,
    "gb": 1024 ** 3, "g": 1024 ** 3,
    "tb": 1024 ** 4, "t": 1024 ** 4,
}

_AGE_UNITS = {
    "s": 1.0, "sec": 1.0, "secs": 1.0,
    "min": 60.0, "mins": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
    "w": 604800.0, "week": 604800.0, "weeks": 604800.0,
    "mo": 2592000.0, "month": 2592000.0, "months": 2592000.0,
    "y": 31536000.0, "yr": 31536000.0, "year": 31536000.0, "years": 31536000.0,
}

# Date-source prefixes that may precede a size/age token (e.g. "c>1year").
# We match by first non-empty character so users can also type the long
# names ("created>1year"). All values resolve to one of these three keys.
_DATE_SOURCE_ALIASES = {
    "c": "created", "create": "created", "created": "created", "creation": "created",
    "m": "modified", "mod": "modified", "modify": "modified", "modified": "modified",
    "a": "accessed", "acc": "accessed", "access": "accessed", "accessed": "accessed",
}

_OP_RE = re.compile(r"^(>=|<=|>|<|=)?\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]+)$")
# Strips an optional letter prefix that designates the date source. Example:
# ``c>1year`` → prefix="c", rest=">1year".
_DATE_PREFIX_RE = re.compile(r"^([a-zA-Z]+)\s*(?=[<>=0-9])")

Predicate = Callable[[DiskNode], bool]
FileClassifier = Callable[[Path, bool], str]
WarningSink = Callable[[str], None]


@dataclass
class FilterError(ValueError):
    """Raised when the user provides an invalid filter expression."""

    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ---------------------------------------------------------------------------
# Token primitives
# ---------------------------------------------------------------------------


def _parse_value(token: str) -> Optional[Tuple[str, float, str]]:
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


def _glob_to_regex(pattern: str) -> re.Pattern:
    return re.compile(fnmatch.translate(pattern), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Predicate factories
# ---------------------------------------------------------------------------


def _make_size_predicate(token: str) -> Predicate:
    parsed = _parse_value(token)
    if parsed is None:
        raise FilterError(f"Invalid size filter: {token}")
    op, value, unit = parsed
    if unit not in _SIZE_UNITS:
        raise FilterError(f"Unknown size unit: {unit}")
    threshold = value * _SIZE_UNITS[unit]
    return lambda node: _compare(float(node.size), op, threshold)


def _make_age_predicate(
    token: str,
    now_ns: int,
    source: str,
    on_warning: Optional[WarningSink],
) -> Predicate:
    parsed = _parse_value(token)
    if parsed is None:
        raise FilterError(f"Invalid age filter: {token}")
    op, value, unit = parsed
    if unit not in _AGE_UNITS:
        raise FilterError(f"Unknown age unit: {unit}")
    threshold_ns = int(value * _AGE_UNITS[unit] * 1_000_000_000)

    # Scanner only records modification time; surface a one-time warning if
    # the user asked for creation or access age so they know we fell back.
    if source != "modified" and on_warning is not None:
        on_warning(
            f"{source} time not recorded yet — falling back to modify for "
            f"'{token}'"
        )

    def predicate(node: DiskNode) -> bool:
        age_ns = max(0, now_ns - node.modified_ns)
        return _compare(float(age_ns), op, float(threshold_ns))

    return predicate


def _make_glob_predicate(pattern: str, *, exclude: bool) -> Predicate:
    regex = _glob_to_regex(pattern)

    def predicate(node: DiskNode) -> bool:
        matched = bool(regex.match(node.path.name))
        if node.is_dir:
            return True  # let users drill into folders while filtering files
        return (not matched) if exclude else matched

    return predicate


def _make_folder_predicate(pattern: str, *, exclude: bool) -> Predicate:
    """Match files whose path passes through a directory matching ``pattern``.

    Directories whose own name matches are kept too so the user can drill in.
    """
    regex = _glob_to_regex(pattern)

    def matches_any_part(node: DiskNode) -> bool:
        return any(regex.match(part) for part in node.path.parts)

    def predicate(node: DiskNode) -> bool:
        hit = matches_any_part(node)
        if exclude:
            return not hit
        if node.is_dir:
            # Directories always pass include-filters so descendants stay
            # visible; the filter ultimately constrains which files appear.
            return True
        return hit

    return predicate


def _parse_tag_combo(spec: str) -> Tuple[Set[str], Set[str]]:
    """Parse a ``red+green-blue`` style combo into (required, excluded) sets.

    Tokens default to ``+`` (required). An empty spec or one that resolves
    to no required colors becomes ``("any tagged", excluded)`` so the user
    can write ``:tag:-red`` to mean "anything tagged that isn't red".
    """
    required: Set[str] = set()
    excluded: Set[str] = set()

    parts: List[Tuple[str, str]] = []
    current = ""
    sign = "+"
    for ch in spec:
        if ch in "+-":
            if current:
                parts.append((sign, current))
                current = ""
            sign = ch
        elif ch == ",":
            if current:
                parts.append((sign, current))
                current = ""
            sign = "+"
        else:
            current += ch
    if current:
        parts.append((sign, current))

    for sign, raw in parts:
        name = raw.strip().lower()
        if not name:
            continue
        if name in ("all", "any", "tagged", "a"):
            # ``all`` only makes sense as a required set; ignored when negated
            if sign == "+":
                required.update(TAG_NAMES)
            continue
        if name not in TAG_ALIASES:
            raise FilterError(f"Unknown tag color: {name}")
        color = TAG_ALIASES[name]
        if sign == "+":
            required.add(color)
        else:
            excluded.add(color)

    return required, excluded


def _make_tag_predicate(spec: str, tags: Dict[str, str]) -> Predicate:
    """Build a tag predicate for ``red``/``tag:red+green-b``/``all`` forms."""
    spec = spec.strip().lower()
    if spec.startswith("tag:") or spec.startswith("tags:"):
        spec = spec.split(":", 1)[1]
    if spec in ("all", "any", "tagged", "a"):
        return lambda node: str(node.path) in tags

    required, excluded = _parse_tag_combo(spec)
    if not required and not excluded:
        raise FilterError(f"Unknown tag: {spec}")
    if not required:
        # No required → treat as "any tagged, except excluded"
        def predicate(node: DiskNode) -> bool:
            color = tags.get(str(node.path))
            return bool(color) and color not in excluded
        return predicate

    def predicate(node: DiskNode) -> bool:
        color = tags.get(str(node.path))
        if not color:
            return False
        if color in excluded:
            return False
        return color in required

    return predicate


def _make_class_predicate(
    name: str, classifier: Optional[FileClassifier]
) -> Predicate:
    if classifier is None:
        raise FilterError(":class: filter requires a file classifier")
    target = name.strip().lower()
    # Accept common synonyms ("music" → "audio"). We rely on the classifier
    # for canonical names; an unknown class still produces a predicate that
    # never matches, surfaced as zero hits rather than an error so the user
    # can iterate.
    synonyms = {
        "music": "audio", "sound": "audio",
        "movies": "video", "movie": "video",
        "pics": "image", "picture": "image", "pictures": "image", "img": "image",
        "doc": "document", "docs": "document",
        "src": "code", "source": "code", "scripts": "code",
        "exe": "binary", "executable": "binary",
        "compressed": "archive", "zip": "archive",
        "dir": "directory", "folder": "directory",
    }
    target = synonyms.get(target, target)

    def predicate(node: DiskNode) -> bool:
        kind = classifier(node.path, node.is_dir)
        if node.is_dir:
            # Directories pass include filters (context); descendants will
            # filter files of the requested class.
            return True
        return kind == target

    return predicate


def _make_text_predicate(token: str) -> Predicate:
    needle = token.lower()
    return lambda node: needle in str(node.path).lower()


# ---------------------------------------------------------------------------
# Token classification & expression assembly
# ---------------------------------------------------------------------------


def _strip_date_prefix(token: str) -> Tuple[str, str]:
    """Return (source, rest) for tokens like ``c>1year`` → ("created", ">1year").

    Falls back to ("modified", token) if no recognizable prefix is present.
    """
    match = _DATE_PREFIX_RE.match(token)
    if not match:
        return "modified", token
    raw = match.group(1).lower()
    source = _DATE_SOURCE_ALIASES.get(raw)
    if source is None:
        return "modified", token
    return source, token[match.end():]


def _classify_token(token: str) -> Tuple[str, str]:
    """Return ``(kind, payload)`` for ``token``.

    Payload is the token text the corresponding predicate factory will see
    (with leading prefix characters stripped). ``kind`` is one of:
    ``empty / tag / class / exclude_glob / exclude_folder / folder / size
    / age / glob / text``.
    """
    if not token:
        return "empty", token
    if token.startswith(":"):
        rest = token[1:]
        if rest.lower().startswith("class:"):
            return "class", rest.split(":", 1)[1]
        return "tag", rest
    if token.startswith("|"):
        inner = token[1:]
        if not inner:
            return "empty", token
        if inner.startswith("\\"):
            return "exclude_folder", inner[1:]
        return "exclude_glob", inner
    if token.startswith("\\"):
        return "folder", token[1:]

    # Date-prefixed size/age tokens like ``c>1year``.
    source, rest = _strip_date_prefix(token)
    parsed = _parse_value(rest)
    if parsed is not None:
        _, _, unit = parsed
        if unit in _SIZE_UNITS:
            return "size", rest
        if unit in _AGE_UNITS:
            return f"age:{source}", rest

    if any(ch in token for ch in "*?[]"):
        return "glob", token
    return "text", token


def build_predicate(
    expression: str,
    tags: Optional[Dict[str, str]] = None,
    *,
    now_ns: Optional[int] = None,
    file_classifier: Optional[FileClassifier] = None,
    on_warning: Optional[WarningSink] = None,
) -> Predicate:
    """Compile a filter expression into a predicate over DiskNode.

    Tokens of the same OR-kind are unioned; different OR-kinds and AND-kinds
    are intersected. See module docstring for the grammar.
    """
    if tags is None:
        tags = {}
    if now_ns is None:
        now_ns = time.time_ns()

    tokens = [token.strip() for token in expression.split(";") if token.strip()]
    if not tokens:
        return lambda _node: True

    or_groups: Dict[str, List[Predicate]] = {}
    and_predicates: List[Predicate] = []

    for raw in tokens:
        kind, payload = _classify_token(raw)
        if kind == "empty":
            continue
        if kind == "glob":
            or_groups.setdefault("glob", []).append(
                _make_glob_predicate(payload, exclude=False)
            )
        elif kind == "folder":
            or_groups.setdefault("folder", []).append(
                _make_folder_predicate(payload, exclude=False)
            )
        elif kind == "tag":
            or_groups.setdefault("tag", []).append(
                _make_tag_predicate(payload, tags)
            )
        elif kind == "class":
            or_groups.setdefault("class", []).append(
                _make_class_predicate(payload, file_classifier)
            )
        elif kind == "text":
            or_groups.setdefault("text", []).append(_make_text_predicate(payload))
        elif kind == "exclude_glob":
            and_predicates.append(_make_glob_predicate(payload, exclude=True))
        elif kind == "exclude_folder":
            and_predicates.append(_make_folder_predicate(payload, exclude=True))
        elif kind == "size":
            and_predicates.append(_make_size_predicate(payload))
        elif kind.startswith("age:"):
            source = kind.split(":", 1)[1]
            and_predicates.append(
                _make_age_predicate(payload, now_ns, source, on_warning)
            )
        else:  # pragma: no cover - defensive
            raise FilterError(f"Unhandled filter kind {kind!r} for {raw!r}")

    def combined(node: DiskNode) -> bool:
        for group in or_groups.values():
            if not any(predicate(node) for predicate in group):
                return False
        for predicate in and_predicates:
            if not predicate(node):
                return False
        return True

    return combined


def collect_matches(root: DiskNode, predicate: Predicate) -> set:
    """Return the set of DiskNodes whose subtree should remain visible.

    A node is kept if it matches the predicate or has a matching descendant.
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
