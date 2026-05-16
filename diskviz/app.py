"""Tkinter application providing a SpaceSniffer-like interface."""

from __future__ import annotations

import csv
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional, Tuple

from .colors import (
    FILE_TYPE_COLORS,
    classify_path,
    load_user_classes,
    make_classifier,
)
from .filters import FilterError, TAG_NAMES, build_predicate
from .model import DiskNode
from .scanner import ScanStats, attach_free_space, flatten_snapshot, scan_many
from .snapshot import Snapshot, load_snapshot, save_snapshot
from .treemap import NodeRect, Rect, filter_layout, slice_and_dice
from .watcher import PathWatcher

# UI Constants
DEFAULT_WINDOW_SIZE = "1100x700"
DEFAULT_SCAN_DEPTH = 4
MONITOR_INTERVAL_MS = 5000
MAX_SCAN_QUEUE_SIZE = 2

# Canvas & palette constants (SpaceSniffer style)
CANVAS_BG_COLOR = "#0E1018"
RECT_INSET_PADDING = 1.0
MIN_LABEL_WIDTH = 40  # 더 작은 블록에도 레이블 표시
MIN_LABEL_HEIGHT = 20  # 더 작은 블록에도 레이블 표시
MIN_SMALL_LABEL_WIDTH = 25  # 매우 작은 블록용
MIN_SMALL_LABEL_HEIGHT = 15  # 매우 작은 블록용

DIR_TILE_BASE = "#D69941"  # 더 선명한 골드/오렌지 (디렉토리)
FILE_TILE_BASE = "#4A8FDB"  # 더 밝고 선명한 블루 (파일)
SELECTION_COLOR = "#FFE066"
SEARCH_MATCH_COLOR = "#47E2C1"
DIMMED_OUTLINE_COLOR = "#2F3442"
TEXT_COLOR = "#1A1A1A"  # 약간 더 진한 텍스트로 가독성 향상

NORMAL_LIGHTEN_FACTOR = 0.30  # 더 밝고 선명한 색상
SEARCH_LIGHTEN_FACTOR = 0.45
DEPTH_SHADE_FACTOR = 0.08  # 깊이에 따른 색상 대비 강화
# SpaceSniffer-style nested rendering: render the whole hierarchy at once
# (children inside parents) and let MIN_NEST_DIMENSION_PX in treemap.py stop
# the recursion when the inner space is too small.
VISIBLE_DEPTH: Optional[int] = None
HEADER_LABEL_HEIGHT = 14  # must match treemap.HEADER_HEIGHT_PX

# Tiles smaller than this on either side are skipped — too small to perceive
# or click reliably, and they only clutter the canvas. SpaceSniffer behaves
# similarly: very thin items collapse into the parent rectangle.
MIN_VISIBLE_TILE_PX = 2

# 4-color tag palette (Ctrl+1~4) – matches SpaceSniffer convention
TAG_COLORS = {
    "red": "#E03B3B",
    "yellow": "#F2C94C",
    "green": "#27AE60",
    "blue": "#2F80ED",
}
TAG_CORNER_SIZE = 14
FILTER_HINT = (
    "e.g. *.jpg;*.png;>1mb  |  \\temp;<3months  |  :class:audio  |  "
    ":tag:red+green-blue"
)
# Free-space synthetic node uses this suffix so we can spot it everywhere.
FREE_SPACE_SUFFIX = " [Free space]"
FREE_SPACE_COLOR = "#5A5F6B"


def check_directory_access(path: Path) -> tuple[bool, str]:
    """Check if a directory is accessible for scanning.

    Args:
        path: Directory path to check

    Returns:
        Tuple of (is_accessible, message)
    """
    if not path.exists():
        return False, "Directory does not exist"

    if not path.is_dir():
        return False, "Path is not a directory"

    try:
        # Try to list directory contents
        list(path.iterdir())
        return True, "Access OK"
    except PermissionError:
        return False, "Permission denied - cannot access this directory"
    except Exception as e:
        return False, f"Error accessing directory: {e}"


def get_safe_directories() -> List[tuple[str, Path]]:
    """Get a list of safe directories that typically don't require special permissions.

    Returns:
        List of (description, path) tuples for accessible directories
    """
    import platform
    from pathlib import Path

    safe_dirs = []
    home = Path.home()

    # Common safe directories
    candidates = [
        ("Home Directory", home),
        ("Downloads", home / "Downloads"),
        ("Desktop (if accessible)", home / "Desktop"),
        ("Projects/Development", home / "Projects"),
        ("Current Directory", Path.cwd()),
    ]

    # Add macOS-specific safe locations
    if platform.system() == "Darwin":
        candidates.extend([
            ("Applications", Path("/Applications")),
            ("Developer", home / "Developer"),
        ])

    # Filter to only include existing and accessible directories
    for desc, path in candidates:
        if path.exists() and path.is_dir():
            accessible, _ = check_directory_access(path)
            if accessible:
                safe_dirs.append((desc, path))

    return safe_dirs


@dataclass
class _PendingScan:
    paths: List[Path]
    depth: int
    follow_symlinks: bool
    show_hidden: bool = False
    skip_zero_size: bool = True


_LOG_PATH = Path("~/Library/Logs/DiskViz.log").expanduser()


def _log(message: str) -> None:
    """Append a diagnostic line to ~/Library/Logs/DiskViz.log."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def _format_age(modified_ns: int, now_ns: Optional[int] = None) -> str:
    """Render an mtime-to-now delta as ``"1 year 3 months"``-style text."""
    if modified_ns <= 0:
        return ""
    if now_ns is None:
        now_ns = time.time_ns()
    seconds = max(0, (now_ns - modified_ns) // 1_000_000_000)
    if seconds < 60:
        return f"{seconds}s"
    units = [
        ("y", 365 * 86400),
        ("mo", 30 * 86400),
        ("d", 86400),
        ("h", 3600),
        ("m", 60),
    ]
    parts: List[str] = []
    remaining = seconds
    for suffix, divisor in units:
        if remaining >= divisor:
            n = remaining // divisor
            remaining -= n * divisor
            parts.append(f"{int(n)}{suffix}")
            if len(parts) == 2:
                break
    return " ".join(parts) if parts else f"{seconds}s"


def _format_mtime(modified_ns: int) -> str:
    """Format a ns-resolution timestamp as a local ISO-8601 string."""
    if modified_ns <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(modified_ns / 1_000_000_000))
    except (OSError, ValueError):
        return ""


def format_size(num_bytes: int) -> str:
    """Format byte count as human-readable string.

    Args:
        num_bytes: Number of bytes to format

    Returns:
        Formatted string with appropriate unit (B, KB, MB, GB, TB, PB)
    """
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    magnitude = min(int(math.log(num_bytes, 1024)), len(units) - 1)
    value = num_bytes / (1024 ** magnitude)
    return f"{value:.1f} {units[magnitude]}"


def lighten(color: str, factor: float = NORMAL_LIGHTEN_FACTOR) -> str:
    """Lighten a hex color by blending it with white.

    Args:
        color: Hex color string (e.g., "#FF0000")
        factor: Blend factor between 0 (original) and 1 (white)

    Returns:
        Lightened hex color string
    """
    color = color.lstrip("#")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def darken(color: str, factor: float = 0.25) -> str:
    """Darken a hex color by blending it with black."""
    color = color.lstrip("#")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    r = int(r * (1 - factor))
    g = int(g * (1 - factor))
    b = int(b * (1 - factor))
    return f"#{r:02x}{g:02x}{b:02x}"


class DiskVizApp:
    """Main application class providing disk usage visualization."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DiskViz - SpaceSniffer for Python")
        self.root.geometry(DEFAULT_WINDOW_SIZE)

        self.path_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.depth_var = tk.IntVar(value=DEFAULT_SCAN_DEPTH)
        self.follow_symlinks = tk.BooleanVar(value=False)
        self.show_hidden = tk.BooleanVar(value=False)
        self.skip_zero_size = tk.BooleanVar(value=True)
        self.filter_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="💡 Select a folder below or use Quick Access buttons for safe directories")

        self.current_node: Optional[DiskNode] = None
        self.root_node: Optional[DiskNode] = None  # Store original root for navigation
        self.current_layout: List[NodeRect] = []
        self.canvas_rects: Dict[int, DiskNode] = {}
        self.rect_geom: Dict[int, Rect] = {}
        self.selection: Optional[DiskNode] = None
        self.snapshot_hash: Optional[int] = None

        # Multi-volume + tagging
        self.extra_paths: List[Path] = []
        self.tags: Dict[str, str] = {}
        self.last_stats: Optional[ScanStats] = None

        # SpaceSniffer-style view options
        self.file_class_style = tk.BooleanVar(value=False)
        self.show_free_space = tk.BooleanVar(value=False)

        # Zoom history (browser-style back/forward)
        self.history: List[Path] = []
        self.history_idx: int = -1

        # User-controlled detail level. None = unlimited (default).
        self.detail_level: Optional[int] = None

        # Hover halo: nodes whose outline gets brightened on mouse-over.
        self.hover_chain: List[DiskNode] = []
        self.hover_chain_key: Tuple[int, ...] = ()
        self._hover_redraw_pending: bool = False

        self._filter_warnings_seen: set = set()

        # User-defined file classes augment FILE_TYPE_COLORS.
        user_colors, ext_map = load_user_classes()
        self.user_class_colors: Dict[str, str] = user_colors
        self.classifier = make_classifier(ext_map) if ext_map else classify_path

        # Snapshot context — non-empty when the current view came from a
        # loaded .diskviz.json file rather than a live scan.
        self.snapshot_source: Optional[Path] = None

        # Native FS event watcher. Falls back to polling when unavailable.
        self.watcher = PathWatcher()
        self._watcher_rescan_job: Optional[str] = None

        self._setup_ui()
        self.monitor_job: Optional[str] = None
        self.scan_queue: "queue.Queue[_PendingScan]" = queue.Queue()
        self.scan_thread: threading.Thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.scan_thread.start()
        self.is_drawing: bool = False
        self.is_fullscreen: bool = False
        self.is_scanning: bool = False
        self._scan_anim_job: Optional[str] = None
        self._scan_anim_step: int = 0
        self._scan_label_text: str = ""

        self.search_var.trace_add("write", lambda *_: self.redraw())
        self._setup_keyboard_shortcuts()

        # Bring the window to the foreground on launch (py2app/Tk on macOS
        # otherwise leaves it behind other apps) and prompt for a folder.
        self.root.after(100, self._on_first_appear)

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Info strip
        info_bar = tk.Frame(self.root, bg="#F6A21A", height=36)
        info_bar.pack(fill=tk.X)
        self.info_path_label = tk.Label(info_bar, text="Directory: —", bg="#F6A21A", fg="#241200", font=("Segoe UI", 11, "bold"))
        self.info_path_label.pack(side=tk.LEFT, padx=12)
        self.info_size_label = tk.Label(info_bar, text="", bg="#F6A21A", fg="#241200", font=("Segoe UI", 11))
        self.info_size_label.pack(side=tk.RIGHT, padx=12)

        top_frame = ttk.Frame(self.root, padding=8)
        top_frame.pack(fill=tk.X)
        self.top_frame = top_frame

        ttk.Label(top_frame, text="Directory:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(top_frame, textvariable=self.path_var, width=50)
        entry.grid(row=0, column=1, sticky="we", padx=(4, 4))
        ttk.Button(top_frame, text="Browse", command=self.choose_directory).grid(row=0, column=2, padx=(0, 4))
        ttk.Button(top_frame, text="+ Add Path", command=self.add_directory).grid(row=0, column=7, padx=(0, 4))

        # Quick Access dropdown
        self._setup_quick_access(top_frame)

        ttk.Label(top_frame, text="Depth:").grid(row=0, column=4, sticky="w")
        depth_spin = ttk.Spinbox(top_frame, from_=1, to=10, textvariable=self.depth_var, width=5)
        depth_spin.grid(row=0, column=5, padx=(4, 8))
        depth_spin.bind("<Return>", lambda *_: self.schedule_scan())
        depth_spin.bind("<FocusOut>", lambda *_: self.schedule_scan())

        follow_box = ttk.Checkbutton(top_frame, text="Follow symlinks", variable=self.follow_symlinks, command=self.schedule_scan)
        follow_box.grid(row=0, column=6, padx=(0, 8))

        hidden_box = ttk.Checkbutton(
            top_frame, text="Show hidden", variable=self.show_hidden, command=self.schedule_scan
        )
        hidden_box.grid(row=0, column=8, padx=(0, 8))

        # View-style toggles (SpaceSniffer parity)
        ttk.Checkbutton(
            top_frame,
            text="Color by type",
            variable=self.file_class_style,
            command=self.redraw,
        ).grid(row=0, column=9, padx=(0, 8))

        ttk.Checkbutton(
            top_frame,
            text="Free space",
            variable=self.show_free_space,
            command=self._toggle_free_space,
        ).grid(row=0, column=10, padx=(0, 8))

        ttk.Checkbutton(
            top_frame,
            text="Skip 0-byte",
            variable=self.skip_zero_size,
            command=self.schedule_scan,
        ).grid(row=0, column=11, padx=(0, 8))

        ttk.Label(top_frame, text="Filter:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        search_entry = ttk.Entry(top_frame, textvariable=self.search_var)
        search_entry.grid(row=1, column=1, sticky="we", padx=(4, 4), pady=(6, 0))
        # Tooltip-style help hint sits right under the entry as a label.
        self.filter_hint_var = tk.StringVar(value=FILTER_HINT)
        ttk.Label(
            top_frame, textvariable=self.filter_hint_var, foreground="#888888"
        ).grid(row=2, column=1, sticky="w", padx=(4, 4))
        ttk.Checkbutton(
            top_frame,
            text="Hide non-matching",
            variable=self.filter_var,
            command=self.redraw,
        ).grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))

        # Navigation and action buttons
        btn_frame = ttk.Frame(top_frame)
        btn_frame.grid(row=1, column=3, columnspan=6, pady=(6, 0), sticky="w")
        ttk.Button(btn_frame, text="Up ↑", command=self.go_up, width=8).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_frame, text="Reset", command=self.reset_view, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Rescan", command=self.schedule_scan, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Delete", command=self.delete_selected, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Rename", command=self.rename_selected, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Export…", command=self.export_report, width=9).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Save Snap", command=self.save_snapshot_dialog, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Load Snap", command=self.load_snapshot_dialog, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="⛶", command=self.toggle_fullscreen, width=3).pack(side=tk.LEFT, padx=2)

        top_frame.columnconfigure(1, weight=1)

        self.canvas = tk.Canvas(self.root, background=CANVAS_BG_COLOR, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda event: self.redraw())
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Double-Button-1>", self.on_canvas_double_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)
        for sequence in (
            "<Button-2>",
            "<ButtonRelease-2>",
            "<Button-3>",
            "<ButtonRelease-3>",
            "<Control-Button-1>",
            "<Control-ButtonRelease-1>",
        ):
            self.canvas.bind(sequence, self.on_canvas_right_click, add="+")
        self.context_menu = tk.Menu(self.root, tearoff=0)

        self.tooltip_var = tk.StringVar(value="")
        tooltip = ttk.Label(self.root, textvariable=self.tooltip_var, relief=tk.GROOVE, anchor="w")
        tooltip.pack(fill=tk.X, side=tk.BOTTOM)
        self.tooltip_label = tooltip

        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_label = status
        self._update_info_bar(None)

    def _setup_quick_access(self, parent: ttk.Frame) -> None:
        """Setup quick access menu for safe directories."""
        # Create Quick Access button with menu
        quick_btn = ttk.Menubutton(parent, text="Quick Access ▼")
        quick_btn.grid(row=0, column=3, padx=(0, 8))

        # Create menu
        menu = tk.Menu(quick_btn, tearoff=0)
        quick_btn["menu"] = menu

        # Add safe directories to menu
        safe_dirs = get_safe_directories()
        if safe_dirs:
            for desc, path in safe_dirs:
                menu.add_command(
                    label=f"{desc}: {path}",
                    command=lambda p=path: self._select_safe_directory(p)
                )
        else:
            menu.add_command(label="No accessible directories found", state="disabled")

        # Add separator and help
        menu.add_separator()
        menu.add_command(label="💡 About Permissions...", command=self._show_permission_help)

    def _select_safe_directory(self, path: Path) -> None:
        """Select a pre-verified safe directory."""
        self.path_var.set(str(path))
        self.schedule_scan()

    def _show_permission_help(self) -> None:
        """Show help about macOS permissions."""
        import platform

        if platform.system() == "Darwin":
            message = """macOS Permission Guide

Some folders require special permissions:
• Documents, Desktop, Downloads (protected by macOS)
• Library folders
• System directories

To grant access:
1. Open System Settings → Privacy & Security
2. Click 'Full Disk Access'
3. Add Terminal (or your Python IDE)
4. Restart Terminal

Alternative: Use the Quick Access menu to select
folders that don't require special permissions."""
        else:
            message = """Permission Guide

Some folders may require elevated permissions.
Try running with administrator privileges or
select a different folder."""

        messagebox.showinfo("Permission Help", message)

    def _setup_keyboard_shortcuts(self) -> None:
        """Setup keyboard shortcuts for the application."""
        # F5 - Rescan
        self.root.bind("<F5>", lambda e: self.schedule_scan())
        # F11 - Toggle fullscreen
        self.root.bind("<F11>", lambda e: self.toggle_fullscreen())
        # Delete - Delete selected
        self.root.bind("<Delete>", lambda e: self.delete_selected())
        # Backspace handled below — see history navigation.
        # Home - Reset to root view
        self.root.bind("<Home>", lambda e: self.reset_view())
        # Ctrl+F - Focus search
        self.root.bind("<Control-f>", lambda e: self.canvas.focus_set() or None)
        # Escape - Clear selection or exit fullscreen
        self.root.bind("<Escape>", lambda e: self._handle_escape())
        # Ctrl+Q - Quit
        self.root.bind("<Control-q>", lambda e: self.root.quit())
        # F2 - Rename
        self.root.bind("<F2>", lambda e: self.rename_selected())
        # Ctrl+1..4 - Tag selection with a color, Ctrl+0 - clear
        self.root.bind("<Control-Key-1>", lambda e: self.tag_selected("red"))
        self.root.bind("<Control-Key-2>", lambda e: self.tag_selected("yellow"))
        self.root.bind("<Control-Key-3>", lambda e: self.tag_selected("green"))
        self.root.bind("<Control-Key-4>", lambda e: self.tag_selected("blue"))
        self.root.bind("<Control-Key-0>", lambda e: self.tag_selected(None))

        # View / navigation bindings
        self.root.bind("<Control-t>", lambda e: self._toggle_var(self.file_class_style))
        self.root.bind("<Control-e>", lambda e: self._toggle_free_space_var())
        # Backspace now walks zoom history (forward via Shift+Backspace).
        # The "Up ↑" toolbar button still calls go_up() directly.
        self.root.bind("<BackSpace>", lambda e: self.history_back())
        self.root.bind("<Shift-BackSpace>", lambda e: self.history_forward())
        # Detail level: Ctrl + / - / 9 (restore unlimited).
        self.root.bind("<Control-plus>", lambda e: self.adjust_detail(+1))
        self.root.bind("<Control-equal>", lambda e: self.adjust_detail(+1))
        self.root.bind("<Command-equal>", lambda e: self.adjust_detail(+1))
        self.root.bind("<Control-minus>", lambda e: self.adjust_detail(-1))
        self.root.bind("<Command-minus>", lambda e: self.adjust_detail(-1))
        self.root.bind("<Control-Key-9>", lambda e: self.adjust_detail(None))
        # Ctrl+N reopens the start picker.
        self.root.bind("<Control-n>", lambda e: self._reopen_picker())

    def _on_first_appear(self) -> None:
        """Activate the app and prompt for a folder if none is set."""
        _log("_on_first_appear: enter")
        try:
            self.root.update_idletasks()
            self.root.deiconify()
            self.root.lift()
            # Make sure topmost is OFF before the picker opens; otherwise
            # the file dialog appears behind the main window on macOS.
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

        empty_path = not self.path_var.get().strip()
        no_current = not self.current_node
        _log(f"_on_first_appear: empty_path={empty_path} no_current={no_current}")
        if empty_path and no_current:
            # Defer one tick so the main window has a chance to paint
            # before the modal picker steals focus. The picker is modal,
            # so it will naturally come to the foreground without us
            # forcing process-level activation (which can prematurely
            # dismiss the dialog).
            self.root.after(200, self.choose_directory)

    # -------------------------------------------------- view-state toggles
    def _toggle_var(self, var: tk.BooleanVar) -> None:
        """Flip a BooleanVar and trigger an immediate redraw."""
        var.set(not var.get())
        self.redraw()

    def _toggle_free_space_var(self) -> None:
        self.show_free_space.set(not self.show_free_space.get())
        self._toggle_free_space()

    def _toggle_free_space(self) -> None:
        """User toggled the Free space checkbox — re-apply on current tree."""
        if self.root_node is None:
            return
        self._apply_free_space(self.root_node)
        self.redraw()

    def _apply_free_space(self, root: DiskNode) -> None:
        """Attach or detach the synthetic free-space child of ``root``."""
        # Remove any existing synthetic node first so the toggle is idempotent.
        before = len(root.children)
        root.children = [
            child for child in root.children
            if not child.path.name.endswith(FREE_SPACE_SUFFIX)
        ]
        if len(root.children) != before:
            root.size = sum(child.size for child in root.children) or root.size

        if self.show_free_space.get():
            attach_free_space(root)

        root.children.sort(key=lambda n: n.size, reverse=True)

    # -------------------------------------------------- history navigation
    def _push_history(self, path: Path) -> None:
        # Truncate any "forward" entries when the user diverges from history.
        self.history = self.history[: self.history_idx + 1]
        if self.history and self.history[-1] == path:
            return
        self.history.append(path)
        self.history_idx = len(self.history) - 1

    def history_back(self) -> None:
        if self.history_idx <= 0:
            return
        self.history_idx -= 1
        self._navigate_to_history()

    def history_forward(self) -> None:
        if self.history_idx + 1 >= len(self.history):
            return
        self.history_idx += 1
        self._navigate_to_history()

    def _navigate_to_history(self) -> None:
        if self.root_node is None or self.history_idx < 0:
            return
        target = self.history[self.history_idx]
        node = self.root_node.find_by_path(target)
        if node is None:
            # Folder may have disappeared after a rescan — fall back to root.
            node = self.root_node
        self.current_node = node
        self.selection = None
        self._update_info_bar(node)
        self.status_var.set(
            f"Viewing {node.path} — {format_size(node.size)} "
            f"(history {self.history_idx + 1}/{len(self.history)})"
        )
        self.redraw()

    # -------------------------------------------------- detail level
    def adjust_detail(self, delta: Optional[int]) -> None:
        """Bump or reset the user-controlled treemap depth.

        ``delta`` is +1 / -1; ``None`` restores the default (unlimited).
        """
        if delta is None:
            self.detail_level = None
            self.status_var.set("Detail level: unlimited")
        else:
            current = self.detail_level or 4
            new = max(1, min(20, current + delta))
            self.detail_level = new
            self.status_var.set(f"Detail level: {new}")
        self.redraw()

    # -------------------------------------------------- picker re-entry
    def _reopen_picker(self) -> None:
        if self.is_scanning:
            return
        self.choose_directory()

    def toggle_fullscreen(self) -> None:
        """Toggle fullscreen mode."""
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes("-fullscreen", self.is_fullscreen)
        self._apply_canvas_only_layout()

        # Update status message
        if self.is_fullscreen:
            self.status_var.set("🖥️ Fullscreen mode (Press F11 or ESC to exit)")
        else:
            self.status_var.set("💡 Select a folder below or use Quick Access buttons for safe directories")

    def _apply_canvas_only_layout(self) -> None:
        """Show only the treemap canvas when fullscreen is enabled."""
        if not hasattr(self, "top_frame"):
            return

        widgets = (self.top_frame, self.tooltip_label, self.status_label)
        if self.is_fullscreen:
            for widget in widgets:
                if widget.winfo_ismapped():
                    widget.pack_forget()
            self.canvas.pack_forget()
            self.canvas.pack(fill=tk.BOTH, expand=True)
        else:
            if not self.top_frame.winfo_ismapped():
                self.top_frame.pack(fill=tk.X)
            self.canvas.pack_forget()
            self.canvas.pack(fill=tk.BOTH, expand=True)
            if not self.tooltip_label.winfo_ismapped():
                self.tooltip_label.pack(fill=tk.X, side=tk.BOTTOM)
            if not self.status_label.winfo_ismapped():
                self.status_label.pack(fill=tk.X, side=tk.BOTTOM)

    def _handle_escape(self) -> None:
        """Handle Escape key - exit fullscreen or clear selection."""
        if self.is_fullscreen:
            self.toggle_fullscreen()
        else:
            self._clear_selection()

    def _update_info_bar(self, node: Optional[DiskNode] = None) -> None:
        """Refresh the info strip with current path and size."""
        if not hasattr(self, "info_path_label"):
            return
        target = node or self.current_node or self.root_node
        if not target:
            self.info_path_label.configure(text="Directory: —")
            self.info_size_label.configure(text="")
            return
        self.info_path_label.configure(text=str(target.path))
        self.info_size_label.configure(text=f"Total {format_size(target.size)}")

    def _clear_selection(self) -> None:
        """Clear the current selection."""
        if self.selection:
            self.selection = None
            self.tooltip_var.set("")
            self.redraw()

    def _show_permission_warning(self, stats: ScanStats) -> None:
        """Show a warning dialog about permission-denied folders.

        Args:
            stats: Scan statistics containing denied paths
        """
        import platform

        denied_count = len(stats.permission_denied)
        sample_paths = stats.permission_denied[:5]  # Show first 5

        message_parts = [
            f"Could not access {denied_count} folder{'s' if denied_count > 1 else ''} due to permission restrictions.\n",
        ]

        if sample_paths:
            message_parts.append("Examples:")
            for path in sample_paths:
                message_parts.append(f"  • {path}")
            if denied_count > 5:
                message_parts.append(f"  ... and {denied_count - 5} more")

        # Add macOS-specific guidance
        if platform.system() == "Darwin":
            message_parts.extend([
                "\n\nOn macOS, you may need to grant Full Disk Access:",
                "1. Open System Settings → Privacy & Security",
                "2. Click 'Full Disk Access'",
                "3. Add Terminal or your Python application",
                "\nOr run from a folder with accessible permissions."
            ])
        else:
            message_parts.append("\n\nTry running with elevated permissions or selecting a different folder.")

        messagebox.showwarning(
            "Permission Restrictions",
            "\n".join(message_parts)
        )

    # ------------------------------------------------------------------ directory selection
    def choose_directory(self) -> None:
        """Open a file dialog to select a directory to visualize."""
        _log("choose_directory: enter")
        import platform

        # Set initial directory to a safe location
        initial_dir = None
        if platform.system() == "Darwin":
            # Try to start in a safe location on macOS
            safe_dirs = get_safe_directories()
            if safe_dirs:
                initial_dir = str(safe_dirs[0][1])

        # Make sure the main window isn't pinned on top so the file picker
        # (NSOpenPanel) can render above it.
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

        _log("choose_directory: about to askdirectory")
        path = filedialog.askdirectory(
            title="Select directory to visualize",
            initialdir=initial_dir,
        )
        _log(f"choose_directory: askdirectory returned {path!r}")
        if path:
            self.path_var.set(path)
            self.schedule_scan()

    def add_directory(self) -> None:
        """Pick an additional path to merge into the current treemap view."""
        extra = filedialog.askdirectory(title="Add another directory to compare")
        if not extra:
            return
        extra_path = Path(extra).expanduser()
        if not self.path_var.get().strip():
            # Treat this as the primary path if nothing was scanned yet.
            self.path_var.set(str(extra_path))
            self.schedule_scan()
            return
        if extra_path in self.extra_paths or str(extra_path) == self.path_var.get().strip():
            self.status_var.set(f"Path already included: {extra_path}")
            return
        self.extra_paths.append(extra_path)
        self.schedule_scan()

    def _collect_scan_paths(self) -> List[Path]:
        primary = self.path_var.get().strip()
        paths: List[Path] = []
        if primary:
            paths.append(Path(primary).expanduser())
        for extra in self.extra_paths:
            if extra not in paths:
                paths.append(extra)
        return paths

    def schedule_scan(self) -> None:
        """Schedule a directory scan with current settings."""
        paths = self._collect_scan_paths()
        if not paths:
            return

        for path in paths:
            if not path.exists():
                self.status_var.set(f"❌ Path does not exist: {path}")
                messagebox.showerror("Invalid Path", f"The path does not exist:\n{path}")
                return

            accessible, message = check_directory_access(path)
            if not accessible:
                self.status_var.set(f"❌ {message}: {path}")
                import platform
                error_msg = f"Cannot access directory:\n{path}\n\n{message}"
                if platform.system() == "Darwin" and "Permission denied" in message:
                    error_msg += "\n\n💡 Tip: Use the 'Quick Access' menu to select\naccessible directories, or grant Full Disk Access\nin System Settings → Privacy & Security."
                result = messagebox.askyesno(
                    "Access Denied",
                    error_msg + "\n\nWould you like to see permission help?",
                    icon="error",
                )
                if result:
                    self._show_permission_help()
                return

        # A live scan overrides any loaded snapshot, so clear that context.
        self.snapshot_source = None
        self._clear_pending_scans()
        pending = _PendingScan(
            paths=paths,
            depth=int(self.depth_var.get()),
            follow_symlinks=self.follow_symlinks.get(),
            show_hidden=self.show_hidden.get(),
            skip_zero_size=self.skip_zero_size.get(),
        )
        self.scan_queue.put(pending)
        if len(paths) == 1:
            label = str(paths[0])
            self.status_var.set(f"🔍 Scanning {label} ...")
        else:
            label = f"{len(paths)} paths"
            self.status_var.set(f"🔍 Scanning {label} ...")
        self._scan_label_text = label
        self.is_scanning = True
        self._show_scan_overlay()

    def _scan_worker(self) -> None:
        """Background thread worker that processes scan requests."""
        while True:
            pending = self.scan_queue.get()
            if pending is None:
                break
            try:
                node, stats = scan_many(
                    pending.paths,
                    max_depth=pending.depth,
                    follow_symlinks=pending.follow_symlinks,
                    show_hidden=pending.show_hidden,
                    skip_zero_size=pending.skip_zero_size,
                )
            except Exception as exc:  # pragma: no cover - defensive
                self.root.after(0, lambda e=exc: self._on_scan_failure(e))
                continue
            self.root.after(0, lambda n=node, p=pending, s=stats: self._apply_scan(n, p, s))

    def _on_scan_failure(self, exc: BaseException) -> None:
        self.is_scanning = False
        self._stop_scan_overlay()
        self.status_var.set(f"Scan failed: {exc}")

    def _show_scan_overlay(self) -> None:
        """Display a centered animated 'Scanning…' indicator on the canvas."""
        # Cancel any prior animation before starting a new one.
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except tk.TclError:
                pass
            self._scan_anim_job = None
        self._scan_anim_step = 0
        self._tick_scan_overlay()

    def _tick_scan_overlay(self) -> None:
        if not self.is_scanning:
            return
        try:
            width = max(self.canvas.winfo_width(), 100)
            height = max(self.canvas.winfo_height(), 100)
            self.canvas.delete("scan_overlay")
            dots = "." * (self._scan_anim_step % 4)
            self.canvas.create_text(
                width / 2,
                height / 2 - 14,
                text=f"🔍  Scanning {self._scan_label_text}{dots}",
                fill="#e0e6f5",
                font=("Segoe UI", 16, "bold"),
                tags="scan_overlay",
            )
            self.canvas.create_text(
                width / 2,
                height / 2 + 16,
                text="Large drives can take a minute. Permission prompts may appear.",
                fill="#7d8499",
                font=("Segoe UI", 11),
                tags="scan_overlay",
            )
        except tk.TclError:
            return
        self._scan_anim_step += 1
        self._scan_anim_job = self.root.after(400, self._tick_scan_overlay)

    def _stop_scan_overlay(self) -> None:
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except tk.TclError:
                pass
            self._scan_anim_job = None
        try:
            self.canvas.delete("scan_overlay")
        except tk.TclError:
            pass

    def _apply_scan(self, node: DiskNode, pending: _PendingScan, stats: ScanStats) -> None:
        """Apply scan results to the UI and update the display."""
        self.is_scanning = False
        self._stop_scan_overlay()

        # Preserve the user's drill-down view across periodic rescans. We
        # remember the path of the previously-viewed folder, then resolve it
        # against the freshly-built tree. Without this, the monitor's 5s
        # rescan would always reset current_node to the scan root and the
        # user would lose their zoom every cycle.
        prior_view_path: Optional[Path] = None
        if (
            self.current_node is not None
            and self.root_node is not None
            and self.current_node is not self.root_node
        ):
            prior_view_path = self.current_node.path

        self.root_node = node  # Store the scan root
        # Re-attach the synthetic [Free space] child if the toggle is on so
        # the user keeps seeing it after each periodic rescan.
        if self.show_free_space.get():
            self._apply_free_space(self.root_node)
        # Seed zoom history on the very first scan so Backspace works
        # before the user has drilled down at all.
        if not self.history:
            self.history = [node.path]
            self.history_idx = 0
        self._filter_warnings_seen.clear()
        if prior_view_path is not None:
            match = node.find_by_path(prior_view_path)
            self.current_node = match if match is not None else node
        else:
            self.current_node = node
        self.selection = None
        self.last_stats = stats
        _log(
            f"_apply_scan: root={node.path} size={node.size} children={len(node.children)} "
            f"files={stats.files_scanned} dirs={stats.dirs_scanned} denied={len(stats.permission_denied)}"
        )

        # Build status message
        status_parts = [
            f"Scanned: {stats.files_scanned} files, {stats.dirs_scanned} dirs",
            f"Total: {format_size(node.size)}"
        ]

        if stats.permission_denied:
            status_parts.append(f"⚠ {len(stats.permission_denied)} access denied")
        if stats.errors:
            status_parts.append(f"⚠ {len(stats.errors)} errors")

        self.status_var.set(" | ".join(status_parts))
        self._update_info_bar(node)

        # Show permission warning if there are significant access issues
        if len(stats.permission_denied) >= 3:
            self._show_permission_warning(stats)

        snapshot = tuple(sorted((str(path), size, mtime) for path, size, mtime in flatten_snapshot(node)))
        self.snapshot_hash = hash(snapshot)
        self.redraw()
        self._start_watcher()
        self._schedule_monitor()

    # -------------------------------------------------- live FS watcher
    def _start_watcher(self) -> None:
        """Watch the scanned paths with native FS events when possible."""
        if not self.watcher.available:
            return
        paths = self._collect_scan_paths()
        if not paths:
            return
        self.watcher.start(paths, self._on_filesystem_change)

    def _on_filesystem_change(self) -> None:
        """Callback from the watcher thread — debounce into a UI rescan."""
        # ``after`` is thread-safe and the only Tk API we should use from
        # the watchdog thread. Chain through to schedule_scan on the main
        # loop after a quiet period so bursts collapse into one scan.
        try:
            self.root.after(0, self._debounced_rescan)
        except RuntimeError:
            # Mainloop already torn down — ignore.
            pass

    def _debounced_rescan(self) -> None:
        if self._watcher_rescan_job is not None:
            try:
                self.root.after_cancel(self._watcher_rescan_job)
            except tk.TclError:
                pass
        self._watcher_rescan_job = self.root.after(
            400, self._fire_debounced_rescan
        )

    def _fire_debounced_rescan(self) -> None:
        self._watcher_rescan_job = None
        if self.snapshot_source is not None:
            return
        self.schedule_scan()

    # ------------------------------------------------------------------ drawing
    def _truncate_label(self, text: str, max_length: int = 28) -> str:
        """Truncate long labels so they fit better inside rectangles."""
        return text if len(text) <= max_length else text[: max_length - 1] + "…"

    def _format_node_label(self, node: DiskNode, width: float, height: float) -> str:
        """Build the multi-line label displayed inside each rectangle.

        Args:
            node: Node to create label for
            width: Rectangle width
            height: Rectangle height

        Returns:
            Formatted label string (simplified for smaller rectangles)
        """
        name = self._truncate_label(node.name or str(node.path))
        size_text = format_size(node.size)

        # 매우 작은 블록: 이름만
        if width < 50 or height < 25:
            max_chars = int(width / 6)  # 대략 문자당 6px
            return name[:max_chars] if max_chars > 0 else ""

        # 작은 블록: 이름 + 크기
        if width < MIN_LABEL_WIDTH or height < MIN_LABEL_HEIGHT:
            if node.is_dir:
                return f"{name}/\n{size_text}"
            return f"{name}\n{size_text}"

        # 일반 블록: 전체 정보 + 항목 개수 (디렉토리의 경우)
        if node.is_dir:
            display_name = f"{name or '/'}"
            # 디렉토리는 파일/폴더 개수 표시
            item_count = len(node.children)
            if item_count > 0:
                files = sum(1 for c in node.children if not c.is_dir)
                dirs = item_count - files
                if dirs > 0 and files > 0:
                    count_text = f"({dirs} dirs, {files} files)"
                elif dirs > 0:
                    count_text = f"({dirs} dirs)"
                else:
                    count_text = f"({files} files)"
                return f"{display_name}/\n{count_text}\n{size_text}"
            return f"{display_name}/\n{size_text}"
        parent = node.path.parent
        parent_name = parent.name or str(parent)
        parent_display = self._truncate_label(parent_name or "/")
        return f"{name}\n[{parent_display}]\n{size_text}"

    def _format_header_label(self, node: DiskNode, width: float) -> str:
        """SpaceSniffer-style 'name - size' header, truncated to fit."""
        size_text = format_size(node.size)
        suffix = "/" if node.is_dir else ""
        # Roughly 7px/char @ 10pt; budget for the size suffix and dash.
        max_chars = max(4, int(width / 6) - len(size_text) - 4)
        name = node.name or str(node.path)
        if len(name) > max_chars:
            name = name[: max(1, max_chars - 1)] + "…"
        return f"{name}{suffix} – {size_text}"

    def _header_font_size(self, width: float, height: float) -> int:
        if height < 18:
            return 8
        if width < 80:
            return 8
        if width < 160:
            return 9
        return 10

    def _get_adaptive_font_size(self, width: float, height: float) -> int:
        """Calculate font size based on rectangle dimensions.

        Args:
            width: Rectangle width
            height: Rectangle height

        Returns:
            Font size (6-10)
        """
        # 블록이 클수록 큰 폰트 사용
        area = width * height
        if area > 10000:  # 큰 블록
            return 10
        elif area > 4000:  # 중간 블록
            return 9
        elif area > 1500:  # 작은 블록
            return 8
        elif area > 600:  # 매우 작은 블록
            return 7
        else:  # 극소 블록
            return 6

    def _tile_colors(
        self,
        node: DiskNode,
        depth: int,
        search_match: bool,
        query_active: bool,
    ) -> tuple[str, str]:
        # Synthetic "free space" node always renders in neutral gray so it's
        # visually distinct from real folders/files.
        if node.path.name.endswith(FREE_SPACE_SUFFIX):
            return lighten(FREE_SPACE_COLOR, 0.20), darken(FREE_SPACE_COLOR, 0.20)

        if self.file_class_style.get() and not node.is_dir:
            kind = self.classifier(node.path, node.is_dir)
            # User palette overrides built-ins; fall back to defaults; final
            # fallback is the plain file colour so unknown classes still draw.
            base = (
                self.user_class_colors.get(kind)
                or FILE_TYPE_COLORS.get(kind)
                or FILE_TILE_BASE
            )
        else:
            base = DIR_TILE_BASE if node.is_dir else FILE_TILE_BASE
        shade = min(max(depth - 1, 0), 3) * DEPTH_SHADE_FACTOR
        fill_factor = max(0.05, NORMAL_LIGHTEN_FACTOR - shade)
        fill = lighten(base, fill_factor)
        # 더 미세한 경계선 - SpaceSniffer 스타일
        outline = darken(base, max(0.08, 0.25 - shade * 0.3))

        if query_active:
            if search_match:
                outline = SEARCH_MATCH_COLOR
            else:
                fill = lighten(fill, SEARCH_LIGHTEN_FACTOR)
                outline = DIMMED_OUTLINE_COLOR

        if node == self.selection:
            outline = SELECTION_COLOR
            fill = lighten(fill, 0.15)
        return fill, outline


    def _compile_filter(self):
        """Build a predicate from the filter entry. Returns (predicate, raw, error)."""
        raw = self.search_var.get().strip()
        if not raw:
            return None, "", None
        try:
            predicate = build_predicate(
                raw,
                self.tags,
                file_classifier=self.classifier,
                on_warning=self._record_filter_warning,
            )
        except FilterError as exc:
            return None, raw, str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            return None, raw, str(exc)
        return predicate, raw, None

    def _record_filter_warning(self, message: str) -> None:
        """Surface a filter-engine warning in the status bar without spamming."""
        if message in self._filter_warnings_seen:
            return
        self._filter_warnings_seen.add(message)
        self.status_var.set(f"⚠ filter: {message}")

    def redraw(self) -> None:
        """Redraw the treemap visualization on the canvas."""
        if self.current_node is None or self.is_drawing:
            return
        if self.is_scanning:
            # Keep the scan overlay visible — re-tick it after a resize.
            self._tick_scan_overlay()
            return
        self.is_drawing = True
        try:
            width = max(self.canvas.winfo_width(), 100)
            height = max(self.canvas.winfo_height(), 100)
            _log(
                f"redraw: canvas={width}x{height} root={self.current_node.path} "
                f"size={self.current_node.size} children={len(self.current_node.children)}"
            )
            self.canvas.delete("all")
            self.canvas_rects.clear()
            self.rect_geom.clear()
            # Honor the user's manual detail-level override if set.
            depth_cap = self.detail_level if self.detail_level is not None else VISIBLE_DEPTH
            self.current_layout = slice_and_dice(
                self.current_node,
                Rect(0, 0, width, height),
                max_depth=depth_cap,
            )
            _log(f"redraw: layout entries={len(self.current_layout)}")

            predicate, raw_filter, filter_error = self._compile_filter()
            if filter_error:
                self.filter_hint_var.set(f"⚠ {filter_error}")
                query_active = False
                matching_nodes: set = set()
            elif predicate is None:
                self.filter_hint_var.set(FILTER_HINT)
                query_active = False
                matching_nodes = set()
            else:
                self.filter_hint_var.set(FILTER_HINT)
                query_active = True
                filtered_layouts = list(filter_layout(self.current_layout, raw_filter, predicate))
                matching_nodes = {layout.node for layout in filtered_layouts}

            drawn_children = 0
            drawn_root = False
            for layout in self.current_layout:
                node = layout.node
                rect = layout.rect.inset(RECT_INSET_PADDING)
                if rect.width <= 0 or rect.height <= 0:
                    continue
                # Don't draw sub-pixel children — they would only paint a
                # single line over the parent rect, which is more confusing
                # than informative. The parent stays clickable.
                if layout.depth > 0 and (
                    rect.width < MIN_VISIBLE_TILE_PX
                    or rect.height < MIN_VISIBLE_TILE_PX
                ):
                    continue
                is_match = query_active and node in matching_nodes
                hide_non_match = self.filter_var.get()
                if query_active and hide_non_match and not is_match:
                    continue
                fill_color, outline = self._tile_colors(node, layout.depth, bool(is_match), bool(query_active))
                is_selected = node is self.selection
                is_hovered = node in self.hover_chain
                # Integer coords for crisp rendering on the bundled Tk build.
                x1 = int(round(rect.x))
                y1 = int(round(rect.y))
                x2 = int(round(rect.x + rect.width))
                y2 = int(round(rect.y + rect.height))
                # Drop-shadow under the selected tile so it pops out of
                # deeply-nested layouts (SpaceSniffer-style cue).
                if is_selected and rect.width > 6 and rect.height > 6:
                    shadow_color = "#000000"
                    self.canvas.create_rectangle(
                        x1 + 1, y1 + 1, x2 + 2, y2 + 2,
                        fill=shadow_color, outline="",
                    )
                    self.canvas.create_rectangle(
                        x1 + 2, y1 + 2, x2 + 3, y2 + 3,
                        fill=shadow_color, outline="",
                    )
                # A thicker bright border on the selected tile mimics
                # SpaceSniffer's selection frame so the user can see exactly
                # which deeply-nested item they're on. Hover halo brightens
                # the outline of every ancestor in the chain.
                if is_selected:
                    border_color = SELECTION_COLOR
                    border_width = 3
                elif is_hovered:
                    border_color = lighten(outline, 0.55)
                    border_width = 2
                else:
                    border_color = outline
                    border_width = 1
                item = self.canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=fill_color,
                    outline=border_color,
                    width=border_width,
                )
                self.canvas_rects[item] = node
                self.rect_geom[item] = rect
                if layout.depth == 0:
                    drawn_root = True
                else:
                    drawn_children += 1

                # SpaceSniffer-style label: "name - size" at the TOP-LEFT of
                # the rect, inside the header strip we reserved. This way each
                # parent's title sits above its nested children.
                if layout.depth > 0 and rect.width >= 36 and rect.height >= 16:
                    label = self._format_header_label(node, rect.width)
                    font_size = self._header_font_size(rect.width, rect.height)
                    font_spec = (
                        ("Segoe UI", font_size, "bold")
                        if node.is_dir
                        else ("Segoe UI", font_size)
                    )
                    self.canvas.create_text(
                        x1 + 4,
                        y1 + HEADER_LABEL_HEIGHT // 2,
                        text=label,
                        fill=TEXT_COLOR,
                        font=font_spec,
                        anchor="w",
                    )

                tag = self.tags.get(str(node.path))
                if tag and rect.width > 6 and rect.height > 6:
                    self._draw_tag_corner(rect, tag)

            if drawn_root and drawn_children == 0:
                self._draw_empty_root_hint(width, height, query_active, raw_filter)
            elif query_active and self.filter_var.get() and drawn_children == 0:
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text=f"No results for '{raw_filter}'",
                    fill=TEXT_COLOR,
                    font=("Segoe UI", 12, "bold"),
                )
            elif not drawn_root and drawn_children == 0:
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text="This folder is empty.",
                    fill="#a8b0c0",
                    font=("Segoe UI", 12, "bold"),
                )
            items = self.canvas.find_all()
            self._draw_viewable_bar(width, height)
            item_count = len(items)
            # Sample the first few items so we can see if their bboxes are
            # actually within the canvas viewport.
            sample_info = []
            for item in items[:4]:
                try:
                    bbox = self.canvas.bbox(item)
                    itype = self.canvas.type(item)
                    sample_info.append(f"{itype}@{bbox}")
                except tk.TclError:
                    sample_info.append("?")
            _log(
                f"redraw: drew root={drawn_root} children={drawn_children} "
                f"canvas_items={item_count} mapped={self.canvas.winfo_ismapped()} "
                f"viewable={self.canvas.winfo_viewable()} "
                f"canvas_winfo={self.canvas.winfo_width()}x{self.canvas.winfo_height()} "
                f"canvas_rootpos={self.canvas.winfo_rootx()},{self.canvas.winfo_rooty()} "
                f"samples={sample_info}"
            )
            try:
                self.canvas.update_idletasks()
            except tk.TclError:
                pass
            if item_count == 0 and self.current_node is not None:
                _log("redraw: canvas empty after draw — scheduling retry")
                self.root.after(150, self._retry_redraw)
        finally:
            self.is_drawing = False

    def _retry_redraw(self) -> None:
        if self.is_scanning or self.current_node is None:
            return
        if len(self.canvas.find_all()) == 0:
            _log("retry_redraw: forcing redraw")
            self.redraw()

    def _draw_viewable_bar(self, canvas_w: int, canvas_h: int) -> None:
        """Vertical bar on the left edge showing the share of total disk
        currently visible (SpaceSniffer §5.12).

        The bar's filled height is proportional to
        ``current_node.size / root_node.size``. When the user is at the
        root the bar fills the full canvas height; drill-down shrinks it.
        """
        if self.root_node is None or self.current_node is None:
            return
        total = self.root_node.size
        if total <= 0:
            return
        ratio = max(0.0, min(1.0, self.current_node.size / total))
        bar_w = 4
        bar_h = int(round(canvas_h * ratio))
        if bar_h <= 0:
            bar_h = 1
        # Subtle: a dark track + a brighter fill so the bar reads even on
        # the default dark canvas background.
        self.canvas.create_rectangle(
            0, 0, bar_w, canvas_h,
            fill="#222633", outline="",
        )
        self.canvas.create_rectangle(
            0, canvas_h - bar_h, bar_w, canvas_h,
            fill="#5BC0BE", outline="",
        )

    def _draw_empty_root_hint(
        self, width: int, height: int, query_active: bool, raw_filter: str
    ) -> None:
        """Explain why no child tiles are visible (permissions, hidden, empty)."""
        node = self.current_node
        if node is None:
            return
        lines: List[str] = [str(node.path), f"Total: {format_size(node.size)}"]

        stats = self.last_stats
        denied_here: List[Path] = []
        if stats:
            target = str(node.path)
            for p in stats.permission_denied:
                if str(p).startswith(target):
                    denied_here.append(p)

        # The scanner already filters hidden items out, so we probe the
        # filesystem directly to know whether toggling 'Show hidden' would help.
        hidden_present = False
        if node.path.exists() and node.path.is_dir():
            try:
                hidden_present = any(
                    entry.name.startswith(".") for entry in os.scandir(node.path)
                )
            except (PermissionError, OSError):
                hidden_present = False

        if query_active and self.filter_var.get():
            lines.append(f"No items match filter '{raw_filter}'.")
            lines.append("Clear the filter or uncheck 'Hide non-matching'.")
        elif denied_here:
            sample = ", ".join(p.name for p in denied_here[:3])
            extra = (
                f" (+{len(denied_here) - 3} more)" if len(denied_here) > 3 else ""
            )
            lines.append(f"⚠ Permission denied for {len(denied_here)} item(s): {sample}{extra}")
            lines.append("Grant Full Disk Access in System Settings → Privacy & Security,")
            lines.append("then click Rescan (F5).")
        elif not node.children and hidden_present and not self.show_hidden.get():
            lines.append("Only hidden items exist here.")
            lines.append("Toggle 'Show hidden' above and rescan.")
        elif not node.children:
            lines.append("This folder is empty.")
        else:
            lines.append("No child tiles to draw at the current depth.")
            lines.append("Try increasing Depth or double-clicking into a subfolder.")

        self.canvas.create_text(
            width / 2,
            height / 2,
            text="\n".join(lines),
            fill="#e8edf8",
            font=("Segoe UI", 13, "bold"),
            justify=tk.CENTER,
        )

    def _draw_tag_corner(self, rect: Rect, tag: str) -> None:
        """Draw a small colored triangle in the top-right corner of a tile."""
        color = TAG_COLORS.get(tag)
        if not color:
            return
        size = min(TAG_CORNER_SIZE, rect.width * 0.45, rect.height * 0.45)
        if size < 4:
            return
        x_right = rect.x + rect.width
        y_top = rect.y
        self.canvas.create_polygon(
            x_right - size, y_top,
            x_right, y_top,
            x_right, y_top + size,
            fill=color,
            outline="",
        )

    # ------------------------------------------------------------------ mouse interaction
    def on_canvas_click(self, event: tk.Event) -> None:
        """Handle mouse click events on the canvas to select nodes."""
        if event.state & 0x4:  # Control-click -> context menu
            self.on_canvas_right_click(event)
            return
        if getattr(event, "num", 1) != 1:
            return
        node = self._node_at(event.x, event.y)
        if not node:
            return
        self.selection = node
        self.tooltip_var.set(f"Selected: {node.path} ({format_size(node.size)})")
        self.redraw()

    def on_canvas_motion(self, event: tk.Event) -> None:
        """Handle mouse motion: tooltip + SpaceSniffer-style halo chain."""
        node = self._node_at(event.x, event.y)
        if node:
            mtime_text = _format_mtime(node.modified_ns)
            age_text = _format_age(node.modified_ns)
            extras = []
            if mtime_text:
                extras.append(f"modified {mtime_text}")
            if age_text:
                extras.append(f"({age_text} ago)")
            suffix = " — " + " ".join(extras) if extras else ""
            self.tooltip_var.set(
                f"{node.path} — {format_size(node.size)}{suffix}"
            )
        else:
            self.tooltip_var.set("")

        # Build the ancestor chain of the hovered node so redraw can
        # highlight each level of nesting.
        chain: List[DiskNode] = []
        if node is not None and self.root_node is not None:
            current = node
            while current is not None:
                chain.append(current)
                if current is self.root_node:
                    break
                current = self._find_parent(self.root_node, current)
        new_key = tuple(id(n) for n in chain)
        if new_key == self.hover_chain_key:
            return
        self.hover_chain = chain
        self.hover_chain_key = new_key
        # Throttle: schedule at most one redraw per idle tick.
        if not self._hover_redraw_pending:
            self._hover_redraw_pending = True
            self.root.after_idle(self._consume_hover_redraw)

    def _consume_hover_redraw(self) -> None:
        self._hover_redraw_pending = False
        self.redraw()

    def on_canvas_double_click(self, event: tk.Event) -> None:
        """Handle double-click to zoom into directories.

        Viewer-only behavior: double-clicking a file does NOT open it
        (which would trigger iCloud downloads or launch the default app).
        It only selects the tile so the user can inspect details.
        """
        node = self._node_at(event.x, event.y)
        if not node:
            return
        # Free-space synthetic tile is not a real directory — only select.
        if node.path.name.endswith(FREE_SPACE_SUFFIX):
            self.selection = node
            self.tooltip_var.set(
                f"Free space: {format_size(node.size)}"
            )
            self.redraw()
            return
        if node.is_dir:
            self.current_node = node
            self.selection = None
            self._push_history(node.path)
            self.status_var.set(f"Viewing {node.path} — {format_size(node.size)}")
            self._update_info_bar(node)
            self.redraw()
        else:
            self.selection = node
            self.tooltip_var.set(
                f"Selected: {node.path} ({format_size(node.size)})"
            )
            self.redraw()

    def on_canvas_right_click(self, event: tk.Event) -> None:
        """Display a context menu for the clicked node."""
        node = self._node_at(event.x, event.y)
        if not node:
            return
        self.selection = node
        self.tooltip_var.set(f"Selected: {node.path} ({format_size(node.size)})")
        # Avoid triggering animation for context menu redraws
        self.redraw()
        self._show_context_menu(event, node)

    def _node_at(self, x: int, y: int) -> Optional[DiskNode]:
        """Find the DiskNode at the given canvas coordinates.

        Uses a small tolerance box so single-pixel tiles next to a click are
        still selectable. ``find_overlapping`` returns items back-to-front in
        canvas stacking order; reversing yields the topmost (smallest /
        deepest) tile first, which is what the user usually means.
        """
        tolerance = 3
        overlapping = self.canvas.find_overlapping(
            x - tolerance, y - tolerance, x + tolerance, y + tolerance
        )
        # Prefer the smallest matching tile so a sub-pixel file under a large
        # parent dir gets picked over the surrounding directory.
        best: Optional[DiskNode] = None
        best_area: float = float("inf")
        for item in reversed(overlapping):
            node = self.canvas_rects.get(item)
            if node is None:
                continue
            rect = self.rect_geom.get(item)
            area = rect.width * rect.height if rect else float("inf")
            if area < best_area:
                best = node
                best_area = area
        return best

    def _show_context_menu(self, event: tk.Event, node: DiskNode) -> None:
        """Build and show the context menu for files/folders.

        Viewer-only: no "Open File" action — that would trigger iCloud
        downloads or launch the file's default app. Reveal in Finder is
        kept because it only navigates Finder, never reads file content.
        """
        self.context_menu.delete(0, tk.END)
        self.context_menu.add_command(
            label="Reveal in Finder",
            command=lambda n=node: self._reveal_in_finder(n.path),
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Rename… (F2)", command=self.rename_selected)
        self.context_menu.add_command(label="Delete… (⌫)", command=self.delete_selected)
        self.context_menu.add_separator()

        tag_menu = tk.Menu(self.context_menu, tearoff=0)
        current_tag = self.tags.get(str(node.path))
        for idx, tag in enumerate(TAG_NAMES, start=1):
            label = f"{tag.capitalize()}  (Ctrl+{idx})"
            if current_tag == tag:
                label = "✓ " + label
            tag_menu.add_command(label=label, command=lambda t=tag: self.tag_selected(t))
        tag_menu.add_separator()
        tag_menu.add_command(label="Clear tag  (Ctrl+0)", command=lambda: self.tag_selected(None))
        self.context_menu.add_cascade(label="Tag", menu=tag_menu)

        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def tag_selected(self, tag: Optional[str]) -> None:
        """Apply or clear a tag on the currently selected node."""
        if not self.selection:
            self.status_var.set("Select a tile first to tag it.")
            return
        key = str(self.selection.path)
        if tag is None:
            self.tags.pop(key, None)
            self.status_var.set(f"Tag cleared: {self.selection.path.name}")
        else:
            self.tags[key] = tag
            self.status_var.set(f"Tagged {self.selection.path.name} → {tag}")
        self.redraw()

    def _reveal_in_finder(self, path: Path) -> None:
        """Reveal the file in Finder (macOS only)."""
        import platform

        if platform.system() != "Darwin":
            messagebox.showinfo("DiskViz", "Finder integration is only available on macOS.")
            return
        if not path.exists():
            messagebox.showerror("DiskViz", f"Path does not exist: {path}")
            return
        try:
            subprocess.run(["open", "-R", str(path)], check=False)
        except Exception as exc:
            messagebox.showerror("DiskViz", f"Failed to reveal file: {exc}")

    # ------------------------------------------------------------------ rename
    def rename_selected(self) -> None:
        """Rename the currently selected file or directory in place."""
        if not self.selection:
            messagebox.showinfo("DiskViz", "Please select a file or directory to rename.")
            return
        target = self.selection.path
        if not target.exists():
            messagebox.showinfo("DiskViz", f"Path already removed: {target}")
            self.schedule_scan()
            return
        new_name = simpledialog.askstring(
            "Rename",
            f"New name for:\n{target}",
            initialvalue=target.name,
            parent=self.root,
        )
        if not new_name or new_name == target.name:
            return
        if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            messagebox.showerror("DiskViz", "Name must not contain path separators.")
            return
        new_path = target.with_name(new_name)
        if new_path.exists():
            messagebox.showerror("DiskViz", f"A path already exists at:\n{new_path}")
            return
        try:
            target.rename(new_path)
        except OSError as exc:
            messagebox.showerror("DiskViz", f"Rename failed: {exc}")
            return
        # Carry the tag over to the new path so the user doesn't lose it.
        old_key = str(target)
        if old_key in self.tags:
            self.tags[str(new_path)] = self.tags.pop(old_key)
        self.status_var.set(f"Renamed → {new_path}")
        self.schedule_scan()

    # ------------------------------------------------------------------ deletion
    def delete_selected(self) -> None:
        """Delete the currently selected file or directory."""
        if not self.selection:
            messagebox.showinfo("DiskViz", "Please select a file or directory to delete.")
            return
        target = self.selection.path
        if not target.exists():
            messagebox.showinfo("DiskViz", f"Path already removed: {target}")
            self.schedule_scan()
            return
        if not messagebox.askyesno("DiskViz", f"Delete {target}? This cannot be undone."):
            return
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except Exception as exc:
            messagebox.showerror("DiskViz", f"Failed to delete {target}: {exc}")
            return
        self.status_var.set(f"Deleted {target}. Refreshing ...")
        self.schedule_scan()

    def go_up(self) -> None:
        """Navigate to the parent directory of the current view."""
        if not self.current_node or not self.root_node:
            return

        # If we're at the root, go to parent directory
        if self.current_node == self.root_node:
            parent_path = self.current_node.path.parent
            if parent_path == self.current_node.path:
                messagebox.showinfo("DiskViz", "Already at the root directory.")
                return
            self.path_var.set(str(parent_path))
            self.schedule_scan()
        else:
            # Navigate up within the tree
            parent = self._find_parent(self.root_node, self.current_node)
            if parent:
                self.current_node = parent
                self.status_var.set(f"Viewing {parent.path} — {format_size(parent.size)}")
                self._update_info_bar(parent)
                self.redraw()
            else:
                # Fallback to root
                self.reset_view()

    def reset_view(self) -> None:
        """Reset view to the scanned root directory."""
        if self.root_node:
            self.current_node = self.root_node
            self.selection = None
            self.status_var.set(f"Viewing {self.root_node.path} — {format_size(self.root_node.size)}")
            self._update_info_bar(self.root_node)
            self.redraw()

    def _find_parent(self, root: DiskNode, target: DiskNode) -> Optional[DiskNode]:
        """Find the parent node of target within the tree rooted at root.

        Args:
            root: Root of the tree to search
            target: Node to find parent of

        Returns:
            Parent DiskNode, or None if not found
        """
        if target in root.children:
            return root
        for child in root.children:
            if child.is_dir:
                parent = self._find_parent(child, target)
                if parent:
                    return parent
        return None

    # ------------------------------------------------------------------ monitoring
    def _schedule_monitor(self) -> None:
        """Schedule the next directory monitor check."""
        if self.monitor_job:
            self.root.after_cancel(self.monitor_job)
        self.monitor_job = self.root.after(MONITOR_INTERVAL_MS, self._monitor_directory)

    def _monitor_directory(self) -> None:
        """Check directory for changes and trigger rescan if needed."""
        self.monitor_job = None
        if not self.current_node:
            return
        # Snapshots are frozen-in-time — never overwrite them with a fresh scan.
        if self.snapshot_source is not None:
            return
        # Native FS event watcher handles change detection; we only poll
        # when watchdog isn't available (non-macOS, missing dependency...).
        if self.watcher.available:
            return
        paths = self._collect_scan_paths()
        if not paths:
            return
        if self.scan_queue.qsize() > MAX_SCAN_QUEUE_SIZE:
            self._schedule_monitor()
            return
        pending = _PendingScan(
            paths=paths,
            depth=int(self.depth_var.get()),
            follow_symlinks=self.follow_symlinks.get(),
            show_hidden=self.show_hidden.get(),
            skip_zero_size=self.skip_zero_size.get(),
        )
        self.scan_queue.put(pending)
        self._schedule_monitor()

    def _clear_pending_scans(self) -> None:
        """Clear all pending scan requests from the queue."""
        try:
            while True:
                self.scan_queue.get_nowait()
        except queue.Empty:
            return

    # ------------------------------------------------------------------ snapshot
    def save_snapshot_dialog(self) -> None:
        """Persist the current scan tree to a portable JSON snapshot."""
        if self.root_node is None:
            messagebox.showinfo("DiskViz", "Scan something first, then save.")
            return
        default_name = self.root_node.path.name or "diskviz"
        filename = filedialog.asksaveasfilename(
            title="Save snapshot",
            defaultextension=".diskviz.json",
            initialfile=f"{default_name}.diskviz.json",
            filetypes=[("DiskViz snapshot", "*.diskviz.json"), ("JSON", "*.json")],
        )
        if not filename:
            return
        try:
            save_snapshot(
                Path(filename),
                self.root_node,
                self.tags,
                created_iso=_format_mtime(time.time_ns()),
            )
        except OSError as exc:
            messagebox.showerror("DiskViz", f"Failed to save snapshot: {exc}")
            return
        self.status_var.set(f"Saved snapshot → {filename}")

    def load_snapshot_dialog(self) -> None:
        """Open a snapshot file and replace the current view with it."""
        filename = filedialog.askopenfilename(
            title="Open snapshot",
            filetypes=[("DiskViz snapshot", "*.diskviz.json"), ("JSON", "*.json")],
        )
        if not filename:
            return
        try:
            snapshot = load_snapshot(Path(filename))
        except (OSError, ValueError) as exc:
            messagebox.showerror("DiskViz", f"Failed to load snapshot: {exc}")
            return
        self.apply_snapshot(snapshot, Path(filename))

    def apply_snapshot(self, snapshot: Snapshot, source: Path) -> None:
        """Swap the current view to a loaded snapshot."""
        self.is_scanning = False
        self._stop_scan_overlay()
        self.root_node = snapshot.root
        self.current_node = snapshot.root
        self.tags = dict(snapshot.tags)
        self.selection = None
        self.snapshot_source = source
        self.history = [snapshot.root.path]
        self.history_idx = 0
        self.path_var.set(str(snapshot.root.path))
        self.status_var.set(
            f"Loaded snapshot {source.name} — {format_size(snapshot.root.size)}"
            f" (frozen in time)"
        )
        self._update_info_bar(snapshot.root)
        self._filter_warnings_seen.clear()
        self.redraw()

    # ------------------------------------------------------------------ export
    def export_report(self) -> None:
        """Export the currently visible tree to a TXT or CSV report."""
        if not self.current_node:
            messagebox.showinfo("DiskViz", "Nothing to export yet — scan a directory first.")
            return

        filename = filedialog.asksaveasfilename(
            title="Export report",
            defaultextension=".txt",
            initialfile=f"diskviz-{self.current_node.path.name or 'report'}.txt",
            filetypes=[("Text report", "*.txt"), ("CSV", "*.csv")],
        )
        if not filename:
            return

        # Apply the active filter so the report mirrors what the user sees.
        predicate, _raw, _err = self._compile_filter()
        rows = list(self._iter_report_rows(self.current_node, predicate))
        rows.sort(key=lambda row: row["size"], reverse=True)

        try:
            if filename.lower().endswith(".csv"):
                self._write_csv_report(filename, rows)
            else:
                self._write_text_report(filename, rows)
        except OSError as exc:
            messagebox.showerror("DiskViz", f"Failed to export: {exc}")
            return
        self.status_var.set(f"Exported {len(rows)} entries → {filename}")

    def _iter_report_rows(self, node: DiskNode, predicate):
        """Yield report rows for ``node`` and all descendants matching predicate."""
        for entry in node.iter_all():
            if predicate is not None and not predicate(entry):
                continue
            yield {
                "path": str(entry.path),
                "size": entry.size,
                "type": "dir" if entry.is_dir else (entry.path.suffix.lower() or "file"),
                "tag": self.tags.get(str(entry.path), ""),
                "modified_ns": entry.modified_ns,
            }

    def _write_csv_report(self, filename: str, rows: List[dict]) -> None:
        with open(filename, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["path", "size_bytes", "size_human", "type", "tag", "modified_iso"])
            for row in rows:
                writer.writerow([
                    row["path"],
                    row["size"],
                    format_size(row["size"]),
                    row["type"],
                    row["tag"],
                    _format_mtime(row["modified_ns"]),
                ])

    def _write_text_report(self, filename: str, rows: List[dict]) -> None:
        total = sum(row["size"] for row in rows)
        with open(filename, "w", encoding="utf-8") as fh:
            fh.write(f"DiskViz report — {self.current_node.path}\n")
            fh.write(f"Generated: {_format_mtime(time.time_ns())}\n")
            filter_text = self.search_var.get().strip()
            if filter_text:
                fh.write(f"Filter: {filter_text}\n")
            fh.write(f"Entries: {len(rows)}    Total: {format_size(total)}\n")
            fh.write("-" * 78 + "\n")
            fh.write(f"{'Size':>12}  {'Tag':<6}  {'Type':<8}  Path\n")
            fh.write("-" * 78 + "\n")
            for row in rows:
                fh.write(
                    f"{format_size(row['size']):>12}  "
                    f"{row['tag']:<6}  {row['type']:<8}  {row['path']}\n"
                )

    # ------------------------------------------------------------------ run helper

def run_app() -> None:
    """Create and run the DiskViz application."""
    root = tk.Tk()
    app = DiskVizApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_app()
