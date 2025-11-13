# DiskViz

DiskViz is a Python implementation of a SpaceSniffer-like disk usage explorer. It visualizes directory structures as a treemap, monitors changes in real time, and lets you search, filter, and delete items from an intuitive Tkinter interface.

## Features

- 📁 **Treemap visualization** – slice-and-dice treemap that scales with window size and shows name/size overlays on larger tiles.
- 🔄 **Real-time monitoring** – background rescans keep the view in sync with file system changes.
- 🎨 **File type colors** – configurable colors based on file type or directory status.
- 🔍 **Search & filter** – instant highlighting plus an option to hide non-matching nodes.
- 🗑️ **Deletion support** – delete files or entire directories (with confirmation) directly from the UI.
- 🛠️ **Advanced controls** – adjustable scan depth, optional symlink following, and a legend to keep colors straight.
- 🧭 **Navigation** – double-click to drill down into directories, backspace to go up, keyboard shortcuts for quick access.
- 📊 **Scan statistics** – see files/directories scanned and permission-denied folders.
- 🔐 **Permission handling** – gracefully handles restricted folders on macOS with helpful guidance.

## Requirements

DiskViz only depends on the Python standard library. Tkinter ships with most Python distributions; on Linux you may need to install it separately (e.g. `sudo apt install python3-tk`).

## Usage

```bash
python -m diskviz
```

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
- `Delete` - Delete selected item
- `Backspace` - Go up one level
- `Home` - Reset to root view
- `Escape` - Exit fullscreen or clear selection
- `Ctrl+Q` - Quit application

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
