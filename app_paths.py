"""Frozen-aware path resolution shared by caption_creator.py and da_client.py.

Both modules used to compute their own __file__-relative directory for storing
formats/watermark/logs/DA credentials. That breaks under a PyInstaller build,
where __file__ resolves inside the bundle rather than next to the actual .exe.
"""

import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", _base_dir())
    return _base_dir()


# Persistent/writable: formats/, watermark/, logs, DA credentials, version.txt.
# Never touched by the launcher's update step, so it survives updates untouched.
BASE_DIR = _base_dir()

# Bundled read-only resources shipped with each release: assets/, seed formats/.
RESOURCE_DIR = _resource_dir()
