# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Caption Creator.

Builds two executables sharing one onedir dependency bundle:
  - CaptionCreator.exe           the app itself (windowed, no console)
  - CaptionCreatorLauncher.exe   update checker + launcher (console)

Onedir (not onefile) is deliberate: it keeps a persistent, editable folder next
to the exes rather than re-extracting to a temp dir every launch, which is what
lets formats/, watermark/, and the app's other writable files live safely
alongside the exe (see app_paths.py) and survive updates untouched.

Run from the repo root with: pyinstaller packaging/build.spec
"""

import os
import shutil

from PIL import Image
from PyInstaller.utils.hooks import copy_metadata

REPO_ROOT = os.path.dirname(SPECPATH)
ASSETS_DIR = os.path.join(REPO_ROOT, "assets")
FORMATS_DIR = os.path.join(REPO_ROOT, "formats")

# --- Convert assets/icon.png -> a multi-size .ico for the exe icon ---
ICON_PNG = os.path.join(ASSETS_DIR, "icon.png")
ICON_ICO = os.path.join(SPECPATH, "icon.ico")
if os.path.isfile(ICON_PNG):
    Image.open(ICON_PNG).save(ICON_ICO, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

# --- imageio_ffmpeg ships its ffmpeg binary as real package data, not a
# runtime download -- PyInstaller's import analysis won't pick up non-.py
# package data on its own, so it has to be added explicitly. ---
import imageio_ffmpeg
_ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
ffmpeg_binaries = [(_ffmpeg_exe, os.path.join("imageio_ffmpeg", "binaries"))]

# --- Data bundled read-only alongside the app (see app_paths.RESOURCE_DIR):
# the 4 built-in formats (seeded into a writable formats/ on first run by
# _ensure_formats_seeded) and the logo/icon images. ---
datas = [
    (os.path.join(ASSETS_DIR, "logo.png"), "assets"),
    (os.path.join(ASSETS_DIR, "icon.png"), "assets"),
]
for fname in ("Standard.json", "Standard (Vertical).json",
              "X-Change.json", "X-Change (Vertical).json"):
    src = os.path.join(FORMATS_DIR, fname)
    if os.path.isfile(src):
        datas.append((src, "formats"))

# imageio (and a couple of its plugins) call importlib.metadata.version() on
# themselves at import time -- PyInstaller doesn't bundle dist-info metadata
# by default, so that raises PackageNotFoundError unless copied explicitly.
for _pkg in ("imageio", "imageio-ffmpeg", "pillow", "numpy"):
    try:
        datas += copy_metadata(_pkg)
    except Exception:
        pass

app_a = Analysis(
    [os.path.join(REPO_ROOT, "caption_creator.py")],
    pathex=[REPO_ROOT],
    binaries=ffmpeg_binaries,
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
)

launcher_a = Analysis(
    [os.path.join(REPO_ROOT, "packaging", "launcher.py")],
    pathex=[REPO_ROOT],
)

app_pyz = PYZ(app_a.pure)
launcher_pyz = PYZ(launcher_a.pure)

app_exe = EXE(
    app_pyz,
    app_a.scripts,
    [],
    exclude_binaries=True,
    name="CaptionCreator",
    console=False,
    icon=ICON_ICO if os.path.isfile(ICON_ICO) else None,
)

launcher_exe = EXE(
    launcher_pyz,
    launcher_a.scripts,
    [],
    exclude_binaries=True,
    name="CaptionCreatorLauncher",
    console=True,
    icon=ICON_ICO if os.path.isfile(ICON_ICO) else None,
)

coll = COLLECT(
    app_exe, app_a.binaries, app_a.zipfiles, app_a.datas,
    launcher_exe, launcher_a.binaries, launcher_a.zipfiles, launcher_a.datas,
    strip=False,
    upx=False,
    name="CaptionCreator",
)

# --- Drop VERSION's contents next to the exes as version.txt, for the
# launcher to compare against GitHub release tags. ---
version_src = os.path.join(REPO_ROOT, "VERSION")
if os.path.isfile(version_src):
    shutil.copy(version_src, os.path.join(DISTPATH, "CaptionCreator", "version.txt"))
