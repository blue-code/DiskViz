"""Tkinter application providing a SpaceSniffer-like interface."""

from __future__ import annotations

import math
import queue
import shutil
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from .colors import FILE_TYPE_COLORS, color_for_node
from .model import DiskNode
from .scanner import flatten_snapshot, scan_directory
from .treemap import NodeRect, Rect, filter_layout, slice_and_dice


@dataclass
class _PendingScan:
    path: Path
    depth: int
    follow_symlinks: bool


def format_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    magnitude = min(int(math.log(num_bytes, 1024)), len(units) - 1)
    value = num_bytes / (1024 ** magnitude)
    return f"{value:.1f} {units[magnitude]}"


def lighten(color: str, factor: float = 0.35) -> str:
    color = color.lstrip("#")
    r = int(color[0:2], 16)
    g = int(color[2:4], 16)
    b = int(color[4:6], 16)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


class DiskVizApp:
    """Main application class."""

    monitor_interval_ms = 5000

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("DiskViz - SpaceSniffer for Python")
        self.root.geometry("1100x700")

        self.path_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.depth_var = tk.IntVar(value=4)
        self.follow_symlinks = tk.BooleanVar(value=False)
        self.filter_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a directory to begin analysis.")

        self._setup_ui()

        self.current_node: Optional[DiskNode] = None
        self.current_layout: List[NodeRect] = []
        self.selection: Optional[DiskNode] = None
        self.snapshot_hash: Optional[int] = None
        self.monitor_job: Optional[str] = None
        self.scan_queue: "queue.Queue[_PendingScan]" = queue.Queue()
        self.scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.scan_thread.start()
        self.is_drawing = False

        self.search_var.trace_add("write", lambda *_: self.redraw())

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        top_frame = ttk.Frame(self.root, padding=8)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="Directory:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(top_frame, textvariable=self.path_var, width=60)
        entry.grid(row=0, column=1, sticky="we", padx=(4, 4))
        ttk.Button(top_frame, text="Browse", command=self.choose_directory).grid(row=0, column=2, padx=(0, 8))

        ttk.Label(top_frame, text="Depth:").grid(row=0, column=3, sticky="w")
        depth_spin = ttk.Spinbox(top_frame, from_=1, to=10, textvariable=self.depth_var, width=5)
        depth_spin.grid(row=0, column=4, padx=(4, 8))
        depth_spin.bind("<Return>", lambda *_: self.schedule_scan())
        depth_spin.bind("<FocusOut>", lambda *_: self.schedule_scan())

        follow_box = ttk.Checkbutton(top_frame, text="Follow symlinks", variable=self.follow_symlinks, command=self.schedule_scan)
        follow_box.grid(row=0, column=5, padx=(0, 8))

        ttk.Label(top_frame, text="Search:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        search_entry = ttk.Entry(top_frame, textvariable=self.search_var)
        search_entry.grid(row=1, column=1, sticky="we", padx=(4, 4), pady=(6, 0))
        ttk.Checkbutton(
            top_frame,
            text="Hide non-matching",
            variable=self.filter_var,
            command=self.redraw,
        ).grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(6, 0))
        ttk.Button(top_frame, text="Clear", command=lambda: self.search_var.set("")).grid(
            row=1, column=3, padx=(0, 8), pady=(6, 0)
        )
        ttk.Button(top_frame, text="Rescan", command=self.schedule_scan).grid(
            row=1, column=4, padx=(0, 8), pady=(6, 0)
        )
        ttk.Button(top_frame, text="Delete Selected", command=self.delete_selected).grid(
            row=1, column=5, padx=(0, 8), pady=(6, 0)
        )

        legend = ttk.Frame(top_frame)
        legend.grid(row=0, column=6, rowspan=2, sticky="ne")
        ttk.Label(legend, text="Legend:", font=("TkDefaultFont", 9, "bold")).pack(anchor="e")
        for label, color in FILE_TYPE_COLORS.items():
            swatch = tk.Canvas(legend, width=14, height=14, highlightthickness=1, highlightbackground="#333")
            swatch.create_rectangle(0, 0, 14, 14, fill=color, outline="")
            frame = ttk.Frame(legend)
            frame.pack(anchor="e")
            swatch.pack(in_=frame, side=tk.LEFT, padx=(0, 4))
            ttk.Label(frame, text=label.title()).pack(side=tk.LEFT)

        top_frame.columnconfigure(1, weight=1)
        top_frame.columnconfigure(6, weight=0)

        self.canvas = tk.Canvas(self.root, background="#202225")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda event: self.redraw())
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Motion>", self.on_canvas_motion)

        self.tooltip_var = tk.StringVar(value="")
        tooltip = ttk.Label(self.root, textvariable=self.tooltip_var, relief=tk.GROOVE, anchor="w")
        tooltip.pack(fill=tk.X, side=tk.BOTTOM)

        status = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status.pack(fill=tk.X, side=tk.BOTTOM)

    # ------------------------------------------------------------------ directory selection
    def choose_directory(self) -> None:
        path = filedialog.askdirectory(title="Select directory to visualize")
        if path:
            self.path_var.set(path)
            self.schedule_scan()

    def schedule_scan(self) -> None:
        path_value = self.path_var.get().strip()
        if not path_value:
            return
        path = Path(path_value).expanduser()
        if not path.exists():
            self.status_var.set(f"Path does not exist: {path}")
            return
        self._clear_pending_scans()
        pending = _PendingScan(path=path, depth=int(self.depth_var.get()), follow_symlinks=self.follow_symlinks.get())
        self.scan_queue.put(pending)
        self.status_var.set(f"Scanning {path} ...")

    def _scan_worker(self) -> None:
        while True:
            pending = self.scan_queue.get()
            if pending is None:
                break
            try:
                node = scan_directory(pending.path, max_depth=pending.depth, follow_symlinks=pending.follow_symlinks)
            except Exception as exc:  # pragma: no cover - defensive
                self.root.after(0, lambda e=exc: self.status_var.set(f"Scan failed: {e}"))
                continue
            self.root.after(0, lambda n=node, p=pending: self._apply_scan(n, p))

    def _apply_scan(self, node: DiskNode, pending: _PendingScan) -> None:
        self.current_node = node
        self.selection = None
        self.status_var.set(
            f"Displaying {pending.path} (depth {pending.depth}) - total size {format_size(node.size)}"
        )
        snapshot = tuple(sorted((str(path), size, mtime) for path, size, mtime in flatten_snapshot(node)))
        self.snapshot_hash = hash(snapshot)
        self.redraw()
        self._schedule_monitor()

    # ------------------------------------------------------------------ drawing
    def redraw(self) -> None:
        if self.current_node is None or self.is_drawing:
            return
        self.is_drawing = True
        try:
            width = max(self.canvas.winfo_width(), 100)
            height = max(self.canvas.winfo_height(), 100)
            self.canvas.delete("all")
            self.canvas_rects: Dict[int, DiskNode] = {}
            self.current_layout = slice_and_dice(self.current_node, Rect(0, 0, width, height))
            query = self.search_var.get().lower().strip()
            matching_nodes = set()
            if query:
                filtered_layouts = list(filter_layout(self.current_layout, query))
                matching_nodes = {layout.node for layout in filtered_layouts}

            drawn = False
            for layout in self.current_layout:
                node = layout.node
                rect = layout.rect.inset(1.5)
                if rect.width <= 0 or rect.height <= 0:
                    continue
                is_match = query and node in matching_nodes
                if query and self.filter_var.get() and not is_match:
                    continue
                color = color_for_node(node.path, node.is_dir)
                if query and not is_match:
                    color = lighten(color, 0.55)
                    outline = "#444"
                else:
                    outline = "#111"
                if is_match:
                    outline = "#00CED1"
                if node == self.selection:
                    outline = "#FFD700"
                item = self.canvas.create_rectangle(rect.x, rect.y, rect.x + rect.width, rect.y + rect.height, fill=color, outline=outline, width=1)
                self.canvas_rects[item] = node
                drawn = True
                if rect.width > 80 and rect.height > 40:
                    label = f"{node.name}\n{format_size(node.size)}"
                    self.canvas.create_text(
                        rect.x + rect.width / 2,
                        rect.y + rect.height / 2,
                        text=label,
                        fill="#f5f5f5",
                        font=("Segoe UI", 9),
                        justify=tk.CENTER,
                    )
            if query and self.filter_var.get() and not drawn:
                self.canvas.create_text(
                    width / 2,
                    height / 2,
                    text=f"No results for '{self.search_var.get().strip()}'",
                    fill="#f5f5f5",
                    font=("Segoe UI", 12, "bold"),
                )
        finally:
            self.is_drawing = False

    # ------------------------------------------------------------------ mouse interaction
    def on_canvas_click(self, event: tk.Event) -> None:
        node = self._node_at(event.x, event.y)
        if not node:
            return
        self.selection = node
        self.tooltip_var.set(f"Selected: {node.path} ({format_size(node.size)})")
        self.redraw()

    def on_canvas_motion(self, event: tk.Event) -> None:
        node = self._node_at(event.x, event.y)
        if node:
            self.tooltip_var.set(f"{node.path} — {format_size(node.size)}")
        else:
            self.tooltip_var.set("")

    def _node_at(self, x: int, y: int) -> Optional[DiskNode]:
        overlapping = self.canvas.find_overlapping(x, y, x, y)
        for item in reversed(overlapping):
            node = self.canvas_rects.get(item)
            if node:
                return node
        return None

    # ------------------------------------------------------------------ deletion
    def delete_selected(self) -> None:
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

    # ------------------------------------------------------------------ monitoring
    def _schedule_monitor(self) -> None:
        if self.monitor_job:
            self.root.after_cancel(self.monitor_job)
        self.monitor_job = self.root.after(self.monitor_interval_ms, self._monitor_directory)

    def _monitor_directory(self) -> None:
        self.monitor_job = None
        if not self.current_node:
            return
        path_value = self.path_var.get().strip()
        if not path_value:
            return
        if self.scan_queue.qsize() > 2:
            self._schedule_monitor()
            return
        pending = _PendingScan(Path(path_value), int(self.depth_var.get()), self.follow_symlinks.get())
        self.scan_queue.put(pending)
        self._schedule_monitor()

    def _clear_pending_scans(self) -> None:
        try:
            while True:
                self.scan_queue.get_nowait()
        except queue.Empty:
            return

    # ------------------------------------------------------------------ run helper

def run_app() -> None:
    root = tk.Tk()
    app = DiskVizApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_app()
