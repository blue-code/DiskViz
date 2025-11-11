"""Color mapping utilities for file types."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Final


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
    """Get the display color for a filesystem node.

    Args:
        path: Path to get color for
        is_dir: Whether the path is a directory

    Returns:
        Hex color string for the node type
    """
    file_type = classify_path(path, is_dir)
    return FILE_TYPE_COLORS.get(file_type, FILE_TYPE_COLORS["other"])
