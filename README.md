# DiskViz

DiskViz is a Python implementation of a SpaceSniffer-like disk usage explorer. It visualizes directory structures as a treemap, monitors changes in real time, and lets you search, filter, and delete items from an intuitive Tkinter interface.

## Features

- 📁 **Treemap visualization** – squarified treemap that scales with the window and shows name/size overlays on larger tiles.
- 🔄 **Real-time monitoring** – background rescans keep the view in sync with file system changes.
- 🎨 **File type colors** – configurable colors based on file type or directory status.
- 🔍 **Advanced filter language** – combine `*.jpg`, `|*.log` (exclude), `>1mb`, `<3months`, `:red` clauses with `;` (e.g. `*.jpg;>500kb;<1year`).
- 🏷️ **4-color tagging** – mark files/folders with red/yellow/green/blue via `Ctrl+1..4` (clear with `Ctrl+0`) and filter by tag using `:red` etc.
- 🗑️ **File operations** – delete and rename items (with confirmation) directly from the UI or context menu.
- 🗂️ **Multi-volume view** – add multiple paths via **+ Add Path** to compare directories side-by-side under one treemap.
- 👁️ **Hidden files toggle** – switch dotfiles on/off without restarting.
- 📤 **Report export** – save the visible tree to TXT or CSV (respects the active filter).
- 🛠️ **Advanced controls** – adjustable scan depth, optional symlink following, hidden-file inclusion.
- 🧭 **Navigation** – double-click to drill down, backspace to go up, keyboard shortcuts for quick access.
- 📊 **Scan statistics** – see files/directories scanned and permission-denied folders.
- 🔐 **Permission handling** – gracefully handles restricted folders on macOS with helpful guidance.
- 💾 **Snapshots** – save the scan tree (and tags) to `.diskviz.json`, reload it later or share with others.
- 🖥️ **CLI mode** – `python -m diskviz --scan PATH --save out.diskviz.json --autoclose` runs headless for scripts and CI.
- 🛰️ **Live FS events** – `watchdog`-based real-time refresh on macOS (replaces the 5s poller when available).
- 📐 **Viewable percent bar** – left-edge bar shows how much of the total disk is currently shown.
- 🎨 **Custom file classes** – extend the color palette via `~/.diskviz/file_classes.json` (extensions → class → color).

## Requirements

DiskViz only depends on the Python standard library. Tkinter ships with most Python distributions; on Linux you may need to install it separately (e.g. `sudo apt install python3-tk`).

## Usage

```bash
# Interactive GUI
python -m diskviz

# Headless: scan + snapshot + export CSV with a filter
python -m diskviz --scan /Volumes/SSD --filter '*.jpg;>1mb' \
    --save snap.diskviz.json --export-csv jpegs.csv --autoclose

# Load a previously-saved snapshot
python -m diskviz --load snap.diskviz.json
```

### CLI flags

| Flag | Meaning |
|---|---|
| `--scan PATH` | Scan PATH on launch (repeatable for multiple roots). |
| `--load FILE` | Load a `.diskviz.json` snapshot instead. |
| `--filter EXPR` | Apply a filter expression after scan/load. |
| `--save DEST` | Save the resulting tree to a snapshot at DEST. |
| `--export-txt DEST` / `--export-csv DEST` | Write a report at DEST. |
| `--depth N` | Maximum scan depth (default: 4). |
| `--show-hidden` | Include dotfiles. |
| `--follow-symlinks` | Follow symbolic links. |
| `--include-zero` | Show zero-byte files (hidden by default). |
| `--autoclose` | Run headlessly without opening a window. |

1. Click **Browse** to choose a directory.
2. Adjust scan depth or enable symlink following if needed.
3. **Double-click** any directory to drill down and focus on that folder.
4. Use **Up ↑** button or **Backspace** to navigate to parent directory.
5. Use **Reset** button or **Home** key to return to the scanned root.
6. Use the search box to highlight matches; enable **Hide non-matching** to filter the view.
7. Select any rectangle to see its details. Use **Delete** button or **Delete** key to remove it.

### Keyboard Shortcuts

- `F5` - Rescan current directory
- `F11` - Toggle fullscreen mode
- `F2` - Rename selected item
- `Delete` - Delete selected item
- `Backspace` - Zoom history: back
- `Shift+Backspace` - Zoom history: forward
- `Home` - Reset to root view
- `Escape` - Exit fullscreen or clear selection
- `Ctrl+N` - Reopen the folder picker (start dialog)
- `Ctrl+T` - Toggle color-by-file-type
- `Ctrl+E` - Toggle free-space synthetic tile (drive scans only)
- `Ctrl++` / `Ctrl+=` / `Cmd+=` - Detail level +1 (deeper nesting)
- `Ctrl+-` / `Cmd+-` - Detail level -1
- `Ctrl+9` - Reset detail level to unlimited
- `Ctrl+1..4` - Tag selection (red / yellow / green / blue)
- `Ctrl+0` - Clear tag on selection
- `Ctrl+Q` - Quit application

### Filter Language

Type into the **Filter** box. Combine clauses with `;`. Tokens of the same
kind are **OR**-ed together; different kinds (e.g. glob vs size) and
exclude tokens are **AND**-ed.

| Example | Meaning |
|---|---|
| `*.jpg` or `*.{jpg,png}` | Keep matching files (glob on basename) |
| `\|*.log` | Exclude matching files |
| `\temp` | Keep files inside a folder named `temp` (any depth) |
| `\|\node_modules` | Exclude everything under `node_modules` |
| `>1mb`, `<500kb`, `>=2gb` | Size comparison (B/KB/MB/GB/TB) |
| `>2years`, `<3months`, `<7d` | Modification age (s/m/h/d/w/mo/y) |
| `c>1year`, `a<3months` | Creation / access age (fallbacks to modify for now, with a status-bar warning) |
| `:class:audio` | File-type class filter (image/video/audio/archive/document/code/binary) |
| `:red` / `:yellow` / `:green` / `:blue` | Single tag |
| `:tag:red+green-blue` | Multi-tag combo (must be red OR green AND NOT blue) |
| `:tagged` / `:all` | Any tagged item |
| `foo` | Plain substring on full path |
| `*.jpg;*.png;>500kb;<1year` | Big-ish recent JPEGs/PNGs |

> ⚠️ **Deletion is permanent.** Ensure you have backups before deleting files.

## macOS Permissions

On macOS, you may encounter permission issues when scanning certain directories (Documents, Desktop, etc.). DiskViz will:
- Continue scanning accessible folders
- Show statistics about permission-denied folders
- Display a helpful dialog with macOS-specific guidance

To grant full access on macOS:
1. Open **System Settings** → **Privacy & Security**
2. Click **Full Disk Access**
3. Add **Terminal** (or your Python IDE)
4. Restart Terminal and run DiskViz again

Alternatively, scan directories that don't require special permissions (like `/Users/yourname/Downloads` or project folders).

## Building macOS DMG

To build a standalone macOS application and DMG installer:

### Prerequisites

```bash
pip install -r requirements-dev.txt
```

### Build

```bash
# Uses the python/pip from your current shell.
# If you need a specific interpreter, set PYTHON_BIN / PIP_BIN:
#   PYTHON_BIN=/Users/you/miniconda3/bin/python \
#   PIP_BIN=/Users/you/miniconda3/bin/pip \
#   ./build_dmg.sh
./build_dmg.sh
```

This will:
1. Clean previous builds
2. Create a `.app` bundle using py2app
3. Generate a DMG installer
4. Output files to `dist/` directory

### Output

- `dist/DiskViz.app` - macOS application bundle
- `dist/DiskViz-1.0.0.dmg` - DMG installer

### Custom icon

1. Save the desired PNG (512px or larger) as `assets/DiskViz.png`.
2. Run `./scripts/make_icon.sh` on macOS to convert it into `assets/DiskViz.icns`.
3. Re-run `./build_dmg.sh` and `./create_dmg_only.sh`.

If the `.icns` file is missing the build still succeeds, but macOS will fall back to the default py2app icon.

### Installation

1. Open the generated DMG file
2. Drag `DiskViz.app` to your Applications folder
3. Launch from Applications or Spotlight

> **Note**: On first launch, you may need to right-click the app and select "Open" to bypass Gatekeeper, or go to System Settings → Privacy & Security and allow the app.

## Development Notes

- The directory scanner uses a background thread so the UI stays responsive.
- Treemap layout uses a simple slice-and-dice algorithm for predictable rectangles.
- Real-time monitoring reschedules scans every few seconds and refreshes automatically when data changes.

Feel free to adapt the color palette, monitoring interval, or layout algorithm for your own workflows.
