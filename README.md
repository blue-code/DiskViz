# DiskViz

DiskViz is a Python implementation of a SpaceSniffer-like disk usage explorer. It visualizes directory structures as a treemap, monitors changes in real time, and lets you search, filter, and delete items from an intuitive Tkinter interface.

## Features

- 📁 **Treemap visualization** – slice-and-dice treemap that scales with window size and shows name/size overlays on larger tiles.
- 🔄 **Real-time monitoring** – background rescans keep the view in sync with file system changes.
- 🎨 **File type colors** – configurable colors based on file type or directory status.
- 🔍 **Search & filter** – instant highlighting plus an option to hide non-matching nodes.
- 🗑️ **Deletion support** – delete files or entire directories (with confirmation) directly from the UI.
- 🛠️ **Advanced controls** – adjustable scan depth, optional symlink following, and a legend to keep colors straight.

## Requirements

DiskViz only depends on the Python standard library. Tkinter ships with most Python distributions; on Linux you may need to install it separately (e.g. `sudo apt install python3-tk`).

## Usage

```bash
python -m diskviz
```

1. Click **Browse** to choose a directory.
2. Adjust scan depth or enable symlink following if needed.
3. Use the search box to highlight matches; enable **Hide non-matching** to filter the view.
4. Select any rectangle to see its details in the status bar. Use **Delete Selected** to remove it.

> ⚠️ **Deletion is permanent.** Ensure you have backups before deleting files.

## Development Notes

- The directory scanner uses a background thread so the UI stays responsive.
- Treemap layout uses a simple slice-and-dice algorithm for predictable rectangles.
- Real-time monitoring reschedules scans every few seconds and refreshes automatically when data changes.

Feel free to adapt the color palette, monitoring interval, or layout algorithm for your own workflows.
