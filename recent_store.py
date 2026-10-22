"""Recent files, projects and Find Online searches, kept in recent.json.

Lives next to the app (app_paths.BASE_DIR) like the other per-user files,
so updates never touch it. Every function swallows I/O errors — a missing
or unreadable history just means an empty menu, never a crash.
"""

import json
import os

from app_paths import BASE_DIR

PATH = os.path.join(BASE_DIR, "recent.json")
MAX_ITEMS = 10
KINDS = ("files", "projects", "searches")


def _load() -> dict:
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def get(kind: str) -> list:
    """Most recent first. File/project entries that no longer exist on disk
    are skipped (and dropped from the stored list)."""
    data = _load()
    items = [i for i in data.get(kind, []) if isinstance(i, str) and i.strip()]
    if kind in ("files", "projects"):
        kept = [i for i in items if os.path.isfile(i)]
        if kept != items:
            data[kind] = kept
            _save(data)
        items = kept
    return items[:MAX_ITEMS]


def add(kind: str, value: str) -> None:
    value = value.strip()
    if kind not in KINDS or not value:
        return
    data = _load()
    key = os.path.normcase(os.path.abspath(value)) if kind != "searches" else value.lower()

    def _norm(v: str) -> str:
        return os.path.normcase(os.path.abspath(v)) if kind != "searches" else v.lower()

    items = [i for i in data.get(kind, []) if isinstance(i, str) and _norm(i) != key]
    data[kind] = ([os.path.abspath(value)] if kind != "searches" else [value]) + items
    data[kind] = data[kind][:MAX_ITEMS]
    _save(data)


def clear(kind: str) -> None:
    data = _load()
    data[kind] = []
    _save(data)


def get_setting(key: str, default=None):
    """Small UI preferences (e.g. the last-used view) kept alongside the
    recent lists."""
    return _load().get("settings", {}).get(key, default)


def set_setting(key: str, value) -> None:
    data = _load()
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    if settings.get(key) == value:
        return
    settings[key] = value
    data["settings"] = settings
    _save(data)
