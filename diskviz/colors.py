"""Color mapping utilities for file types.

The built-in :data:`FILE_TYPE_COLORS` is hardcoded so the app always has
sensible defaults, but :func:`load_user_classes` lets the user override
or extend the palette via ``~/.diskviz/file_classes.json``. The JSON
structure is::

    {
      "video": {"color": "#FF8C00", "extensions": [".mp4", ".mkv"]},
      "ebook": {"color": "#9B59B6", "extensions": [".epub", ".mobi"]}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Final, Iterable, Tuple


# Color palette for different file types
FILE_TYPE_COLORS: Final[Dict[str, str]] = {
    "image": "#6A5ACD",      # Slate blue
    "video": "#FF8C00",      # Dark orange
    "audio": "#20B2AA",      # Light sea green
    "archive": "#DC143C",    # Crimson
    "document": "#2E8B57",   # Sea green
    "code": "#4682B4",       # Steel blue
    "binary": "#8B4513",     # Saddle brown
    "other": "#696969",      # Dim gray
    "directory": "#B0C4DE",  # Light steel blue
}

# File extension sets for classification
IMAGE_EXT: Final[set] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".svg", ".webp", ".ico"}
VIDEO_EXT: Final[set] = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"}
AUDIO_EXT: Final[set] = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}
ARCHIVE_EXT: Final[set] = {".zip", ".tar", ".gz", ".bz2", ".rar", ".7z", ".xz"}
DOCUMENT_EXT: Final[set] = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".odt", ".rtf"}
CODE_EXT: Final[set] = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".rs", ".go", ".rb", ".php", ".html", ".css", ".json", ".xml", ".yaml", ".yml"}
BINARY_EXT: Final[set] = {".exe", ".dll", ".so", ".bin", ".dylib", ".app"}


def classify_path(path: Path, is_dir: bool) -> str:
    """Classify a path into a file type category.

    Args:
        path: Path to classify
        is_dir: Whether the path is a directory

    Returns:
        String category: 'directory', 'image', 'video', 'audio', 'archive',
        'document', 'code', 'binary', or 'other'
    """
    if is_dir:
        return "directory"

    suffix = path.suffix.lower()
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in VIDEO_EXT:
        return "video"
    if suffix in AUDIO_EXT:
        return "audio"
    if suffix in ARCHIVE_EXT:
        return "archive"
    if suffix in DOCUMENT_EXT:
        return "document"
    if suffix in CODE_EXT:
        return "code"
    if suffix in BINARY_EXT:
        return "binary"
    return "other"


def color_for_node(path: Path, is_dir: bool) -> str:
    """Get the display color for a filesystem node."""
    file_type = classify_path(path, is_dir)
    return FILE_TYPE_COLORS.get(file_type, FILE_TYPE_COLORS["other"])


USER_CLASSES_PATH = Path("~/.diskviz/file_classes.json").expanduser()


def load_user_classes(
    path: Path = USER_CLASSES_PATH,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load user-defined file classes from ``path``.

    Returns ``(colors, ext_to_class)`` where ``colors`` extends/overrides
    :data:`FILE_TYPE_COLORS` and ``ext_to_class`` maps file extensions
    (``.epub``) to a class name. Missing or malformed files yield empty
    dicts so callers can simply fall back to defaults.
    """
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}, {}

    colors: Dict[str, str] = {}
    ext_to_class: Dict[str, str] = {}
    if not isinstance(data, dict):
        return colors, ext_to_class

    for class_name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        color = spec.get("color")
        if isinstance(color, str) and color.startswith("#"):
            colors[class_name] = color
        for ext in spec.get("extensions", []) or []:
            if not isinstance(ext, str):
                continue
            if not ext.startswith("."):
                ext = "." + ext
            ext_to_class[ext.lower()] = class_name

    return colors, ext_to_class


def make_classifier(
    ext_to_class: Dict[str, str],
):
    """Return a ``classify_path``-compatible function that prefers user-defined
    extensions, falling back to the built-in classifier."""

    def classifier(path: Path, is_dir: bool) -> str:
        if is_dir:
            return "directory"
        suffix = path.suffix.lower()
        if suffix in ext_to_class:
            return ext_to_class[suffix]
        return classify_path(path, is_dir)

    return classifier
