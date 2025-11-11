"""Color mapping utilities for file types."""

from __future__ import annotations

from pathlib import Path
from typing import Dict


FILE_TYPE_COLORS: Dict[str, str] = {
    "image": "#6A5ACD",
    "video": "#FF8C00",
    "audio": "#20B2AA",
    "archive": "#DC143C",
    "document": "#2E8B57",
    "code": "#4682B4",
    "binary": "#8B4513",
    "other": "#696969",
    "directory": "#B0C4DE",
}

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".svg"}
VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".wmv"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".ogg"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".bz2", ".rar", ".7z"}
DOCUMENT_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}
CODE_EXT = {".py", ".js", ".ts", ".java", ".c", ".cpp", ".rs", ".go", ".rb", ".php", ".html", ".css"}
BINARY_EXT = {".exe", ".dll", ".so", ".bin", ".dylib"}


def classify_path(path: Path, is_dir: bool) -> str:
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
    file_type = classify_path(path, is_dir)
    return FILE_TYPE_COLORS.get(file_type, FILE_TYPE_COLORS["other"])
