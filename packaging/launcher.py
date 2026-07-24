"""Caption Creator update checker + launcher.

Built by PyInstaller into CaptionCreatorLauncher.exe, living next to
CaptionCreator.exe in the install directory. On every launch: checks GitHub for
a newer release, downloads and swaps in the app if one exists, then starts it.

Only ever replaces CaptionCreator.exe and its _internal/ dependency folder —
those are exactly what ships in the release zip. formats/, watermark/,
da_settings.json, da_tokens.json, and the crash log are never part of that zip,
so they're never touched here, regardless of how many times an update runs.

Any failure along the way (offline, GitHub unreachable, no release published
yet, a corrupt download) fails open: whatever is currently installed just
launches as-is, same philosophy as run.bat's existing git-update-check.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

import requests

REPO = "crazygirlashley/caption-creation-tool"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "CaptionCreator-win64.zip"
APP_EXE_NAME = "CaptionCreator.exe"
INTERNAL_DIR_NAME = "_internal"

INSTALL_DIR = os.path.dirname(os.path.abspath(sys.executable))
VERSION_FILE = os.path.join(INSTALL_DIR, "version.txt")
APP_EXE = os.path.join(INSTALL_DIR, APP_EXE_NAME)
INTERNAL_DIR = os.path.join(INSTALL_DIR, INTERNAL_DIR_NAME)


def get_local_version() -> str:
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "0"


def fetch_latest_release_info() -> dict:
    """Returns the GitHub API's release JSON, or {} on any failure (offline,
    no release published yet, rate-limited, etc.) — callers should treat an
    empty dict as "nothing to do, launch what's installed"."""
    try:
        resp = requests.get(
            RELEASES_API, timeout=5,
            headers={"User-Agent": "CaptionCreatorLauncher", "Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def find_asset_url(release_info: dict) -> str:
    for asset in release_info.get("assets", []):
        if asset.get("name") == ASSET_NAME:
            return asset.get("browser_download_url", "")
    return ""


def install_update_from_zip(zip_path: str, new_version: str) -> bool:
    """Extract zip_path and swap it into INSTALL_DIR in place of the current
    CaptionCreator.exe/_internal/. Returns True on success. Never touches
    anything outside those two paths."""
    extract_dir = tempfile.mkdtemp(prefix="cc_update_extracted_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(extract_dir)

        new_exe = os.path.join(extract_dir, APP_EXE_NAME)
        new_internal = os.path.join(extract_dir, INTERNAL_DIR_NAME)
        if not os.path.isfile(new_exe) or not os.path.isdir(new_internal):
            return False

        if os.path.isfile(APP_EXE):
            os.remove(APP_EXE)
        if os.path.isdir(INTERNAL_DIR):
            shutil.rmtree(INTERNAL_DIR)

        shutil.move(new_exe, APP_EXE)
        shutil.move(new_internal, INTERNAL_DIR)

        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(new_version)
        return True
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def check_and_update() -> None:
    release_info = fetch_latest_release_info()
    if not release_info:
        return

    latest_version = str(release_info.get("tag_name", "")).lstrip("v")
    if not latest_version or latest_version == get_local_version():
        return

    asset_url = find_asset_url(release_info)
    if not asset_url:
        return

    print(f"Update found: {get_local_version()} -> {latest_version}. Downloading...")
    tmp_dir = tempfile.mkdtemp(prefix="cc_update_")
    try:
        zip_path = os.path.join(tmp_dir, ASSET_NAME)
        resp = requests.get(asset_url, timeout=120)
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            f.write(resp.content)

        if install_update_from_zip(zip_path, latest_version):
            print(f"Updated to {latest_version}.")
        else:
            print("Update download looked incomplete -- launching current version.")
    except Exception as exc:
        print(f"Update failed ({exc}) -- launching current version.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def launch_app() -> None:
    if not os.path.isfile(APP_EXE):
        print(f"ERROR: {APP_EXE_NAME} not found in {INSTALL_DIR}.")
        return
    subprocess.Popen([APP_EXE], cwd=INSTALL_DIR)


def main() -> None:
    print("Checking for updates...")
    check_and_update()
    launch_app()


if __name__ == "__main__":
    main()
