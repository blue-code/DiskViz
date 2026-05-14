"""
Setup script for building DiskViz macOS application.
"""

from pathlib import Path
from setuptools import setup

ICON_PATH = Path(__file__).parent / "assets" / "DiskViz.icns"

APP = ['diskviz/__main__.py']
DATA_FILES = []
_CANDIDATE_FRAMEWORKS = [
    # Homebrew tcl-tk@8 (preferred; Tk 8.5 in system frameworks has a
    # Canvas-paint bug on macOS that leaves widgets blank in py2app bundles).
    '/opt/homebrew/opt/tcl-tk@8/lib/libtcl8.6.dylib',
    '/opt/homebrew/opt/tcl-tk@8/lib/libtk8.6.dylib',
    # Legacy miniconda location
    '/Users/johnkim/miniconda3/lib/libffi.8.dylib',
    '/Users/johnkim/miniconda3/lib/libtcl8.6.dylib',
    '/Users/johnkim/miniconda3/lib/libtk8.6.dylib',
]
_existing_frameworks = [p for p in _CANDIDATE_FRAMEWORKS if Path(p).exists()]

OPTIONS = {
    'argv_emulation': False,
    'packages': ['diskviz'],
    # Only bundle explicit frameworks if they exist on this machine; otherwise
    # py2app falls back to whatever Tcl/Tk the active interpreter uses.
    **({'frameworks': _existing_frameworks} if _existing_frameworks else {}),
    'includes': [
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.simpledialog',
        'tkinter.ttk',
    ],
    'excludes': [
        'numpy',
        'scipy',
        'matplotlib',
        'pandas',
        'PIL',
    ],
    'iconfile': str(ICON_PATH) if ICON_PATH.exists() else None,
    'plist': {
        'CFBundleName': 'DiskViz',
        'CFBundleDisplayName': 'DiskViz',
        'CFBundleGetInfoString': 'Disk usage visualizer for macOS',
        'CFBundleIdentifier': 'com.diskviz.app',
        'CFBundleVersion': '1.1.1',
        'CFBundleShortVersionString': '1.1.1',
        'NSHumanReadableCopyright': 'Copyright © 2025. All rights reserved.',
        'NSHighResolutionCapable': True,
    },
    'strip': True,  # Strip debug symbols
    'optimize': 2,  # Optimize bytecode
}

setup(
    name='DiskViz',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
