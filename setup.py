"""
Setup script for building DiskViz macOS application.
"""

from pathlib import Path
from setuptools import setup

ICON_PATH = Path(__file__).parent / "assets" / "DiskViz.icns"

APP = ['diskviz/__main__.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': ['diskviz'],
    'frameworks': [
        '/Users/johnkim/miniconda3/lib/libffi.8.dylib',
        '/Users/johnkim/miniconda3/lib/libtcl8.6.dylib',
        '/Users/johnkim/miniconda3/lib/libtk8.6.dylib',
    ],
    'includes': [
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
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
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
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
