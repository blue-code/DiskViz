"""Tkinter application providing a SpaceSniffer-like interface."""

from __future__ import annotations

import math
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from .model import DiskNode
from .scanner import ScanStats, flatten_snapshot, scan_directory
from .treemap import NodeRect, Rect, filter_layout, slice_and_dice

# UI Constants
DEFAULT_WINDOW_SIZE = "1100x700"
DEFAULT_SCAN_DEPTH = 4
MONITOR_INTERVAL_MS = 5000
MAX_SCAN_QUEUE_SIZE = 2

# Canvas & palette constants (SpaceSniffer style)
CANVAS_BG_COLOR = "#0E1018"
RECT_INSET_PADDING = 1.0
MIN_LABEL_WIDTH = 80
MIN_LABEL_HEIGHT = 38

DIR_TILE_BASE = "#C48B4A"
FILE_TILE_BASE = "#4D90D5"
SELECTION_COLOR = "#FFE066"
SEARCH_MATCH_COLOR = "#47E2C1"
DIMMED_OUTLINE_COLOR = "#2F3442"
TEXT_COLOR = "#191919"

NORMAL_LIGHTEN_FACTOR = 0.25
SEARCH_LIGHTEN_FACTOR = 0.45
DEPTH_SHADE_FACTOR = 0.06
VISIBLE_DEPTH: Optional[int] = 2  # Show immediate children by default


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
    path: Path
    depth: int
    follow_symlinks: bool


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
        self.filter_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="💡 Select a folder below or use Quick Access buttons for safe directories")

        self.current_node: Optional[DiskNode] = None
        self.root_node: Optional[DiskNode] = None  # Store original root for navigation
        self.current_layout: List[NodeRect] = []
        self.canvas_rects: Dict[int, DiskNode] = {}
        self.selection: Optional[DiskNode] = None
        self.snapshot_hash: Optional[int] = None

        self._setup_ui()
        self.monitor_job: Optional[str] = None
        self.scan_queue: "queue.Queue[_PendingScan]" = queue.Queue()
        self.scan_thread: threading.Thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.scan_thread.start()
        self.is_drawing: bool = False
        self.is_fullscreen: bool = False

        self.search_var.trace_add("write", lambda *_: self.redraw())
        self._setup_keyboard_shortcuts()

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

        # Quick Access dropdown
        self._setup_quick_access(top_frame)

        ttk.Label(top_frame, text="Depth:").grid(row=0, column=4, sticky="w")
        depth_spin = ttk.Spinbox(top_frame, from_=1, to=10, textvariable=self.depth_var, width=5)
        depth_spin.grid(row=0, column=5, padx=(4, 8))
        depth_spin.bind("<Return>", lambda *_: self.schedule_scan())
        depth_spin.bind("<FocusOut>", lambda *_: self.schedule_scan())

        follow_box = ttk.Checkbutton(top_frame, text="Follow symlinks", variable=self.follow_symlinks, command=self.schedule_scan)
        follow_box.grid(row=0, column=6, padx=(0, 8))

        ttk.Label(top_frame, text="Search:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        search_entry = ttk.Entry(top_frame, textvariable=self.search_var)
        search_entry.grid(row=1, column=1, sticky="we", padx=(4, 4), pady=(6, 0))
        ttk.Checkbutton(
            top_frame,
            text="Hide non-matching",
            variable=self.filter_var,
            command=self.redraw,
        ).grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(6, 0))

        # Navigation and action buttons
        btn_frame = ttk.Frame(top_frame)
        btn_frame.grid(row=1, column=3, columnspan=3, pady=(6, 0), sticky="w")
        ttk.Button(btn_frame, text="Up ↑", command=self.go_up, width=8).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_frame, text="Reset", command=self.reset_view, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Rescan", command=self.schedule_scan, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Delete", command=self.delete_selected, width=8).pack(side=tk.LEFT, padx=2)
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
        # Backspace - Go up one level
        self.root.bind("<BackSpace>", lambda e: self.go_up())
        # Home - Reset to root view
        self.root.bind("<Home>", lambda e: self.reset_view())
        # Ctrl+F - Focus search
        self.root.bind("<Control-f>", lambda e: self.canvas.focus_set() or None)
        # Escape - Clear selection or exit fullscreen
        self.root.bind("<Escape>", lambda e: self._handle_escape())
        # Ctrl+Q - Quit
        self.root.bind("<Control-q>", lambda e: self.root.quit())

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
        import platform

        # Set initial directory to a safe location
        initial_dir = None
        if platform.system() == "Darwin":
            # Try to start in a safe location on macOS
            safe_dirs = get_safe_directories()
            if safe_dirs:
                initial_dir = str(safe_dirs[0][1])

        path = filedialog.askdirectory(
            title="Select directory to visualize",
            initialdir=initial_dir
        )
        if path:
            self.path_var.set(path)
            self.schedule_scan()

    def schedule_scan(self) -> None:
        """Schedule a directory scan with current settings."""
        path_value = self.path_var.get().strip()
        if not path_value:
            return
        path = Path(path_value).expanduser()

        if not path.exists():
            self.status_var.set(f"❌ Path does not exist: {path}")
            messagebox.showerror("Invalid Path", f"The path does not exist:\n{path}")
            return

        # Check directory access before scanning
        accessible, message = check_directory_access(path)
        if not accessible:
            self.status_var.set(f"❌ {message}: {path}")

            # Show helpful error dialog
            import platform
            error_msg = f"Cannot access directory:\n{path}\n\n{message}"

            if platform.system() == "Darwin" and "Permission denied" in message:
                error_msg += "\n\n💡 Tip: Use the 'Quick Access' menu to select\naccessible directories, or grant Full Disk Access\nin System Settings → Privacy & Security."

            result = messagebox.askyesno(
                "Access Denied",
                error_msg + "\n\nWould you like to see permission help?",
                icon="error"
            )
            if result:
                self._show_permission_help()
            return

        self._clear_pending_scans()
        pending = _PendingScan(path=path, depth=int(self.depth_var.get()), follow_symlinks=self.follow_symlinks.get())
        self.scan_queue.put(pending)
        self.status_var.set(f"🔍 Scanning {path} ...")

    def _scan_worker(self) -> None:
        """Background thread worker that processes scan requests."""
        while True:
            pending = self.scan_queue.get()
            if pending is None:
                break
            try:
                node, stats = scan_directory(pending.path, max_depth=pending.depth, follow_symlinks=pending.follow_symlinks)
            except Exception as exc:  # pragma: no cover - defensive
                self.root.after(0, lambda e=exc: self.status_var.set(f"Scan failed: {e}"))
                continue
            self.root.after(0, lambda n=node, p=pending, s=stats: self._apply_scan(n, p, s))

    def _apply_scan(self, node: DiskNode, pending: _PendingScan, stats: ScanStats) -> None:
        """Apply scan results to the UI and update the display."""
        self.current_node = node
        self.root_node = node  # Store the scan root
        self.selection = None

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
        self._schedule_monitor()

    # ------------------------------------------------------------------ drawing
    def _truncate_label(self, text: str, max_length: int = 28) -> str:
        """Truncate long labels so they fit better inside rectangles."""
        return text if len(text) <= max_length else text[: max_length - 1] + "…"

    def _format_node_label(self, node: DiskNode) -> str:
        """Build the multi-line label displayed inside each rectangle."""
        name = self._truncate_label(node.name or str(node.path))
        size_text = format_size(node.size)
        if node.is_dir:
            display_name = f"{name or '/'}"
            return f"{display_name}/\n{size_text}"
        parent = node.path.parent
        parent_name = parent.name or str(parent)
        parent_display = self._truncate_label(parent_name or "/")
        return f"{name}\n[{parent_display}]\n{size_text}"

    def _tile_colors(
        self,
        node: DiskNode,
        depth: int,
        search_match: bool,
        query_active: bool,
    ) -> tuple[str, str]:
        base = DIR_TILE_BASE if node.is_dir else FILE_TILE_BASE
        shade = min(max(depth - 1, 0), 3) * DEPTH_SHADE_FACTOR
        fill_factor = max(0.05, NORMAL_LIGHTEN_FACTOR - shade)
        fill = lighten(base, fill_factor)
        outline = darken(base, max(0.1, 0.4 - shade * 0.5))

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


    def redraw(self) -> None:
        """Redraw the treemap visualization on the canvas."""
        if self.current_node is None or self.is_drawing:
            return
        self.is_drawing = True
        try:
            width = max(self.canvas.winfo_width(), 100)
            height = max(self.canvas.winfo_height(), 100)
            self.canvas.delete("all")
            self.canvas.delete("all")
            self.canvas_rects.clear()
            self.current_layout = slice_and_dice(
                self.current_node,
                Rect(0, 0, width, height),
                max_depth=VISIBLE_DEPTH,
            )
            query = self.search_var.get().lower().strip()
            matching_nodes = set()
            if query:
                filtered_layouts = list(filter_layout(self.current_layout, query))
                matching_nodes = {layout.node for layout in filtered_layouts}

            drawn = False
            for layout in self.current_layout:
                if layout.depth == 0:
                    continue
                node = layout.node
                rect = layout.rect.inset(RECT_INSET_PADDING)
                if rect.width <= 0 or rect.height <= 0:
                    continue
                is_match = query and node in matching_nodes
                hide_non_match = self.filter_var.get()
                if query and hide_non_match and not is_match:
                    continue
                fill_color, outline = self._tile_colors(node, layout.depth, bool(is_match), bool(query))
                item = self.canvas.create_rectangle(
                    rect.x,
                    rect.y,
                    rect.x + rect.width,
                    rect.y + rect.height,
                    fill=fill_color,
                    outline=outline,
                    width=1.2,
                )
                self.canvas_rects[item] = node
                drawn = True
                if rect.width > MIN_LABEL_WIDTH and rect.height > MIN_LABEL_HEIGHT:
                    label = self._format_node_label(node)
                    # Use bold font for directories
                    font_spec = ("Segoe UI", 9, "bold") if node.is_dir else ("Segoe UI", 9)
                    self.canvas.create_text(
                        rect.x + rect.width / 2,
                        rect.y + rect.height / 2,
                        text=label,
                        fill=TEXT_COLOR,
                        font=font_spec,
                        justify=tk.CENTER,
                    )
            if query and self.filter_var.get() and not drawn:
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text=f"No results for '{self.search_var.get().strip()}'",
                    fill=TEXT_COLOR,
                    font=("Segoe UI", 12, "bold"),
                )
            elif not drawn:
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text="This folder is empty.",
                    fill="#a8b0c0",
                    font=("Segoe UI", 12, "bold"),
                )
        finally:
            self.is_drawing = False

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
        """Handle mouse motion to show tooltip information."""
        node = self._node_at(event.x, event.y)
        if node:
            self.tooltip_var.set(f"{node.path} — {format_size(node.size)}")
        else:
            self.tooltip_var.set("")

    def on_canvas_double_click(self, event: tk.Event) -> None:
        """Handle double-click to zoom into directories."""
        node = self._node_at(event.x, event.y)
        if not node:
            return
        if node.is_dir:
            self.current_node = node
            self.selection = None
            self.status_var.set(f"Viewing {node.path} — {format_size(node.size)}")
            self._update_info_bar(node)
            self.redraw()
        else:
            self._open_file(node.path)

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

        Args:
            x: X coordinate on canvas
            y: Y coordinate on canvas

        Returns:
            DiskNode at the coordinates, or None if no node is found
        """
        overlapping = self.canvas.find_overlapping(x, y, x, y)
        for item in reversed(overlapping):
            node = self.canvas_rects.get(item)
            if node:
                return node
        return None

    def _show_context_menu(self, event: tk.Event, node: DiskNode) -> None:
        """Build and show the context menu for files/folders."""
        self.context_menu.delete(0, tk.END)
        if node.is_dir:
            self.context_menu.add_command(label="Open Folder", command=lambda n=node: self._open_path(n.path))
        else:
            self.context_menu.add_command(label="Open File", command=lambda n=node: self._open_file(n.path))
            self.context_menu.add_command(label="Reveal in Finder", command=lambda n=node: self._reveal_in_finder(n.path))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Delete…", command=self.delete_selected)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _open_path(self, path: Path) -> None:
        """Open a path using the system default handler."""
        import platform

        if not path.exists():
            messagebox.showerror("DiskViz", f"Path does not exist: {path}")
            return
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.run(["open", str(path)], check=False)
            elif system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            messagebox.showerror("DiskViz", f"Failed to open: {exc}")

    def _open_file(self, path: Path) -> None:
        """Open a file with the default application."""
        import platform

        if not path.exists():
            messagebox.showerror("DiskViz", f"Path does not exist: {path}")
            return
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.run(["open", str(path)], check=False)
            elif system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            messagebox.showerror("DiskViz", f"Failed to open file: {exc}")

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
        path_value = self.path_var.get().strip()
        if not path_value:
            return
        if self.scan_queue.qsize() > MAX_SCAN_QUEUE_SIZE:
            self._schedule_monitor()
            return
        pending = _PendingScan(Path(path_value), int(self.depth_var.get()), self.follow_symlinks.get())
        self.scan_queue.put(pending)
        self._schedule_monitor()

    def _clear_pending_scans(self) -> None:
        """Clear all pending scan requests from the queue."""
        try:
            while True:
                self.scan_queue.get_nowait()
        except queue.Empty:
            return

    # ------------------------------------------------------------------ run helper

def run_app() -> None:
    """Create and run the DiskViz application."""
    root = tk.Tk()
    app = DiskVizApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_app()
