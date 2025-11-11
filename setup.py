"""
Setup script for building DiskViz macOS application.
"""

from setuptools import setup

APP = ['diskviz/__main__.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': ['diskviz'],
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
        'setuptools',
        'distutils',
        'test',
        'unittest',
        'email',
        'html',
        'http',
        'urllib',
        'xml',
        'xmlrpc',
        'pydoc',
        'doctest',
        'argparse',
        'difflib',
        'inspect',
        'pdb',
        'bdb',
        'cmd',
        'code',
        'codeop',
        'py_compile',
        'compileall',
        'dis',
        'pickletools',
        'sqlite3',
        'multiprocessing',
        'concurrent',
        'asyncio',
        'ctypes',
    ],
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
