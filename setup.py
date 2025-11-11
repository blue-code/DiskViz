"""
Setup script for building DiskViz macOS application.
"""

from setuptools import setup

APP = ['diskviz/__main__.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': ['tkinter', 'pathlib'],
    'iconfile': None,  # Add an .icns file path here if you have one
    'plist': {
        'CFBundleName': 'DiskViz',
        'CFBundleDisplayName': 'DiskViz',
        'CFBundleGetInfoString': 'Disk usage visualizer for macOS',
        'CFBundleIdentifier': 'com.diskviz.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHumanReadableCopyright': 'Copyright © 2025. All rights reserved.',
        'NSHighResolutionCapable': True,
    }
}

setup(
    name='DiskViz',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
