"""UI color themes — "basic" (native ttk look, no palette — the app's
original appearance) plus any number of custom color palettes loaded from
JSON files in the themes/ folder, mirroring how formats/ works: drop a new
theme JSON in and it shows up in the Theme menu next time it's opened, no
code changes needed. Only the built-in themes (see _CORE_THEME_NAMES) are
tracked in git by default — anything else you add stays personal.
themes/Basic.json is the one exception: it ships for reference only (see
the skip in _load_palettes_from_disk) and never appears as a selectable
option, since "Basic" means the native theme, not a fixed palette.

A palette can optionally set "font" to a font family name (e.g. Bubblegum
uses "Aardvark Cafe") to also re-font the whole UI while that theme is
active — falls back to the app's normal font if the requested one isn't
installed.

Persisted choice goes to ui_settings.json; "Dark" is the default for a
fresh install.

Two mechanisms cover the whole UI:
- ttk.Style configuration re-themes every ttk widget instantly and stays
  live for the app's whole lifetime, including widgets not built yet.
- tk's option database (root.option_add) supplies default colors for
  classic (non-ttk) widgets, but only takes effect at widget *creation*
  time — a plain tk.Frame/tk.Text built before a theme switch keeps its
  old colors until something explicitly reconfigures it. CaptionApp
  handles that for the handful of classic-tk containers it holds direct
  references to (see CaptionApp._apply_theme); anything else (e.g. a
  dialog that isn't currently open) just picks up the new theme correctly
  the next time it's built.
"""

import json
import os
import shutil
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Optional

from app_paths import BASE_DIR, RESOURCE_DIR

_SETTINGS_PATH = os.path.join(BASE_DIR, "ui_settings.json")
_THEMES_DIR = os.path.join(BASE_DIR, "themes")

DEFAULT_THEME = "Dark"

# Themes shipped with the app — overwriting one of these locally is fine,
# but only these are pushed/committed to the repo by default (see
# .gitignore). Any other themes/*.json you drop in is personal, same as
# formats/. "Basic" ships as a JSON too, but only for reference — see the
# name.lower() == "basic" skip in _load_palettes_from_disk.
_CORE_THEME_NAMES = {"Basic", "Dark", "Bubblegum"}

# Every custom palette JSON must define all of these — a value picked for
# every color role used somewhere in the UI (see _apply_custom_ttk /
# _apply_options). A file missing one is skipped rather than crashing the
# app over a hand-edited typo.
_REQUIRED_KEYS = ("bg", "panel", "input_bg", "hover", "border", "fg", "muted_fg", "select_bg")

# Classic (non-ttk) widget colors for "basic" — Windows symbolic system
# color names, so it matches whatever the user's own OS theme/contrast
# settings already are, same as this app's look before themes existed.
_BASIC_CLASSIC = {"bg": "SystemButtonFace", "input_bg": "SystemWindow", "fg": "SystemWindowText"}

# The ttk theme name in effect before this module ever touched it —
# captured on the first apply() call so "basic" can restore it exactly,
# whatever it happens to be on this system (typically "vista" on Windows).
_native_ttk_theme = None

_palettes_cache: Optional[dict] = None


def _ensure_themes_seeded() -> None:
    """On a fresh install, themes/ won't exist yet under BASE_DIR — seed it
    from the read-only copy of the built-in themes bundled alongside the
    frozen app (RESOURCE_DIR). No-op when running from source, where the two
    dirs are the same and themes/ is already tracked in git."""
    if os.path.isdir(_THEMES_DIR) and os.listdir(_THEMES_DIR):
        return
    os.makedirs(_THEMES_DIR, exist_ok=True)
    seed_dir = os.path.join(RESOURCE_DIR, "themes")
    if os.path.isdir(seed_dir) and os.path.abspath(seed_dir) != os.path.abspath(_THEMES_DIR):
        for fname in os.listdir(seed_dir):
            shutil.copy(os.path.join(seed_dir, fname), os.path.join(_THEMES_DIR, fname))


def _load_palettes_from_disk() -> dict:
    """Scan themes/ dir and return {name: palette_dict} for every valid
    theme JSON. A palette's "font" field is optional — a UI font family to
    switch to while that theme is active (see _set_ui_font); omit it to
    leave the font alone."""
    _ensure_themes_seeded()
    result = {}
    if not os.path.isdir(_THEMES_DIR):
        return result
    for fname in sorted(os.listdir(_THEMES_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(_THEMES_DIR, fname), encoding="utf-8") as fh:
                data = json.load(fh)
            if not all(k in data for k in _REQUIRED_KEYS):
                continue
            name = data.get("name") or os.path.splitext(fname)[0]
            if name.lower() == "basic":
                # Reserved — "Basic" means "use the native ttk theme with no
                # overrides", which isn't expressible as a fixed palette.
                # themes/Basic.json exists only as a reference for what
                # that native look approximates in hex, not a loadable option.
                continue
            palette = {k: data[k] for k in _REQUIRED_KEYS}
            if data.get("font"):
                palette["font"] = data["font"]
            result[name] = palette
        except Exception:
            continue
    return result


def get_palettes(force_reload: bool = False) -> dict:
    """{name: palette_dict} for every theme currently in themes/. Cached
    after the first call — pass force_reload=True (done whenever the
    Settings menu opens) to pick up files added since."""
    global _palettes_cache
    if _palettes_cache is None or force_reload:
        _palettes_cache = _load_palettes_from_disk()
    return _palettes_cache


def load_theme() -> str:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            name = json.load(f).get("theme")
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_THEME
    if name == "basic" or name in get_palettes():
        return name
    return DEFAULT_THEME


def save_theme(name: str) -> None:
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"theme": name}, f)


def classic_colors(name: str) -> dict:
    """{"bg", "input_bg", "fg"} for the classic-tk widgets CaptionApp
    recolors by hand on a live theme switch."""
    palettes = get_palettes()
    if name in palettes:
        c = palettes[name]
        return {"bg": c["bg"], "input_bg": c["input_bg"], "fg": c["fg"]}
    return dict(_BASIC_CLASSIC)


def apply(root: tk.Tk, style: ttk.Style, name: str) -> None:
    """Apply name ("basic" or a theme name from themes/) to every ttk widget
    and set the tk option database so classic widgets created from this
    point on match."""
    global _native_ttk_theme
    if _native_ttk_theme is None:
        _native_ttk_theme = style.theme_use()

    palettes = get_palettes()
    if name in palettes:
        c = palettes[name]
        _apply_custom_ttk(style, c)
        _apply_options(root, c)
        _set_ui_font(root, c.get("font"))
    else:
        style.theme_use(_native_ttk_theme)
        _apply_options(root, None)
        _set_ui_font(root, None)


# Every one of Tk's built-in named fonts — reconfiguring these re-fonts the
# whole UI in one shot, since every ttk widget and most classic tk widgets
# (Text included) point at one of these by default rather than a hardcoded
# family.
_NAMED_FONTS = ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont",
                "TkFixedFont", "TkCaptionFont", "TkSmallCaptionFont",
                "TkIconFont", "TkTooltipFont")

# Each named font's family before this module ever touched it — captured
# the first time _set_ui_font runs, so switching back to a theme with no
# "font" (or one whose requested font isn't installed) restores exactly
# what was there before, rather than guessing a fallback family.
_original_font_families: dict = {}


def _set_ui_font(root: tk.Tk, family: Optional[str]) -> None:
    """Point every Tk named font at family if it's actually available to Tk
    (checked via tkinter.font.families(), not the Windows-fonts-folder
    filename scan caption_creator.py's _find_font_file() uses for PIL
    rendering — Tk needs the family registered with Tk itself, a separate
    concern from PIL being able to open a file by path). Falls back to each
    font's original family if family is None or isn't installed, so an
    uninstalled theme font degrades to the app's normal font rather than
    erroring or silently keeping a previous theme's font."""
    resolved = None
    if family:
        try:
            available = tkfont.families(root)
        except tk.TclError:
            available = ()
        if family in available:
            resolved = family
    for fname in _NAMED_FONTS:
        try:
            f = tkfont.nametofont(fname, root=root)
        except tk.TclError:
            continue
        if fname not in _original_font_families:
            _original_font_families[fname] = f.cget("family")
        f.configure(family=resolved or _original_font_families[fname])


def _apply_custom_ttk(style: ttk.Style, c: dict) -> None:
    # clam is a pure-Tk-drawn theme that fully respects style.configure
    # colors — Windows' native themes (vista/winnative, used for "basic")
    # draw most widgets natively and largely ignore custom colors, which is
    # exactly why a custom-colored theme needs a different base theme.
    style.theme_use("clam")
    style.configure(".", background=c["bg"], foreground=c["fg"],
                     fieldbackground=c["input_bg"], bordercolor=c["border"],
                     lightcolor=c["bg"], darkcolor=c["bg"])
    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("TButton", background=c["panel"], foreground=c["fg"],
                     bordercolor=c["border"], lightcolor=c["panel"], darkcolor=c["panel"])
    style.map("TButton",
              background=[("active", c["hover"]), ("disabled", c["bg"])],
              foreground=[("disabled", c["muted_fg"])])
    style.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
    style.map("TCheckbutton", background=[("active", c["bg"])])
    style.configure("TRadiobutton", background=c["bg"], foreground=c["fg"])
    style.map("TRadiobutton", background=[("active", c["bg"])])
    style.configure("TEntry", fieldbackground=c["input_bg"], foreground=c["fg"],
                     insertcolor=c["fg"], bordercolor=c["border"])
    style.configure("TCombobox", fieldbackground=c["input_bg"], foreground=c["fg"],
                     background=c["panel"], arrowcolor=c["fg"], bordercolor=c["border"])
    style.map("TCombobox",
              fieldbackground=[("readonly", c["input_bg"])],
              foreground=[("readonly", c["fg"])],
              selectbackground=[("readonly", c["input_bg"])],
              selectforeground=[("readonly", c["fg"])])
    style.configure("TSpinbox", fieldbackground=c["input_bg"], foreground=c["fg"],
                     arrowcolor=c["fg"], bordercolor=c["border"])
    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
    style.configure("TNotebook.Tab", background=c["panel"], foreground=c["fg"])
    style.map("TNotebook.Tab",
              background=[("selected", c["bg"])],
              foreground=[("selected", c["fg"])])
    style.configure("TLabelframe", background=c["bg"], foreground=c["fg"],
                     bordercolor=c["border"])
    style.configure("TLabelframe.Label", background=c["bg"], foreground=c["fg"])
    style.configure("TScale", background=c["bg"], troughcolor=c["input_bg"])
    style.configure("TScrollbar", background=c["panel"], troughcolor=c["bg"],
                     bordercolor=c["border"], arrowcolor=c["fg"])
    style.configure("TSeparator", background=c["border"])
    style.configure("TPanedwindow", background=c["bg"])


def _apply_options(root: tk.Tk, c: Optional[dict]) -> None:
    """Set root's own background directly (option_add never affects an
    already-existing widget, and root always already exists) plus the
    option-database defaults classic tk widgets pick up when created."""
    if c is not None:
        root.configure(bg=c["bg"])
        patterns = {
            "*Background": c["bg"],
            "*Foreground": c["fg"],
            "*activeBackground": c["hover"],
            "*activeForeground": c["fg"],
            "*selectBackground": c["select_bg"],
            "*selectForeground": c["fg"],
            "*insertBackground": c["fg"],
            "*disabledForeground": c["muted_fg"],
            "*highlightBackground": c["bg"],
            "*highlightColor": c["border"],
            "*Text.Background": c["input_bg"],
            "*Text.Foreground": c["fg"],
            "*Menu.Background": c["panel"],
            "*Menu.Foreground": c["fg"],
            "*Menu.activeBackground": c["hover"],
            "*Menu.activeForeground": c["fg"],
        }
    else:
        root.configure(bg=_BASIC_CLASSIC["bg"])
        patterns = {
            "*Background": "SystemButtonFace",
            "*Foreground": "SystemButtonText",
            "*activeBackground": "SystemButtonFace",
            "*activeForeground": "SystemButtonText",
            "*selectBackground": "SystemHighlight",
            "*selectForeground": "SystemHighlightText",
            "*insertBackground": "SystemWindowText",
            "*disabledForeground": "SystemGrayText",
            "*highlightBackground": "SystemButtonFace",
            "*highlightColor": "SystemWindowFrame",
            "*Text.Background": "SystemWindow",
            "*Text.Foreground": "SystemWindowText",
            "*Menu.Background": "SystemMenu",
            "*Menu.Foreground": "SystemMenuText",
            "*Menu.activeBackground": "SystemHighlight",
            "*Menu.activeForeground": "SystemHighlightText",
        }
    for pattern, value in patterns.items():
        root.option_add(pattern, value)
