# Caption Creator

A Windows desktop app for creating X-Change-style captioned images, animated GIFs, and MP4 videos. Place a styled text panel beside any image, overlay title and tagline text, apply pill color presets, and send the finished output directly to DeviantArt.

---

## Features

- **Standard format** — image on the left, styled text panel on the right
- **X-Change format** — adds title (top-left overlay), tagline (bottom-left overlay), and pill color presets matching real X-Change Maker styling
- **Pill presets** — Pink, Blue, and Purple with matching background and stroke colors
- **Caption panel** — auto-fit text sizing, left/center/right alignment, bold, stroke; choose from every font installed on your system
- **Caption side** — for horizontal-layout formats, place the caption panel to the left or right of the image (defaults to right); hidden for vertical-layout formats, which always caption below the image
- **Save/Save As formats** — tweak any format's settings and save them under the same name or as a brand-new format; new formats you create are personal and stay out of version control by default
- **X-Change text effects** — drop shadow and stroke on all overlaid text; defaults to Tahoma Bold for the caption body and Aardvark Cafe for the title and tagline (optional install)
- **Watermark** — auto-loads a single image from the `watermark/` folder; scales to 2× the footer font size when the footer is enabled, or to a manually adjustable pixel height (shown only while the footer is off, since the 2× rule needs a footer size to scale against)
- **Color eyedropper** — a **Pick** button next to Page BG, Text, and Stroke color swatches lets you click anywhere on the preview image to sample that pixel's color instead of using the color chooser
- **Animated GIF support** — all frames are processed; background rendering keeps the UI responsive
- **MP4 video support** — open an MP4, caption every frame the same way as a GIF, and export as either an MP4 or a GIF, regardless of which one you started with
- **Minimum output size** — final output is upscaled (never downscaled, aspect ratio preserved) to at least 1280×720 if the source is smaller; the Preview panel label shows the actual output size next to the on-screen preview, which can still display smaller to fit the window
- **Output Size Override** — a checkbox above the Preview panel that reveals a 0–100% slider once checked: 100% is today's default (the composite's natural size, or the enforced 1280×720 floor if it's smaller), 0% shrinks it — aspect ratio preserved, no stretching — down to that same 1280×720 floor. Only a real downscale when the natural size is already bigger than the floor; small sources have no room to shrink below it, so both ends coincide
- **Large-file safety** — opening a GIF/video whose frames would use a lot of memory prompts you to load it in full, or use a single-frame preview instead (editing stays fast and light on RAM); either way, **Save** and **Send to DA** composite and write one frame at a time straight to the output file instead of building the whole thing in memory first, so exporting doesn't need to hold the full file in RAM regardless of length
- **Detached export console** — GIF/MP4 exports (Save and Send to DA) run in a separate console window showing live per-frame progress, instead of on the app's main thread; the main window stays fully responsive during long exports, and the console closes on its own once the export finishes
- **Single-Frame Preview toggle** — a toolbar checkbox, shown only for GIF/MP4 sources, to manually switch between a live full-animation preview and a lightweight single-frame preview at any time — useful on lower-end machines even for files too small to trigger the automatic large-file prompt
- **DeviantArt integration** — save finished output as a private draft on DeviantArt with one click; publish to gallery when ready
- **Crash logging** — rotating log at `caption_creator_crash.log` with watchdog thread for hang detection

---

## Requirements

- **Windows 10 or later**
- For the **Quick install** below: nothing else — the download is a standalone build with Python, Tk, and ffmpeg all bundled in.
- For the **Manual install** (running from source): **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/), plus **Pillow**, **requests**, **imageio**, and **imageio-ffmpeg** via pip. `imageio-ffmpeg` bundles its own ffmpeg binary, so no separate system-wide ffmpeg install is needed either way.

> Tkinter is included with the standard Python installer on Windows. If `import tkinter` fails when running from source, re-run the Python installer and ensure the **tcl/tk and IDLE** optional feature is checked.

---

## Installation

### Quick install (Windows)

Download [`install.bat`](install.bat) and run it — no Python or Git required on
your machine, it downloads a self-contained build. It asks where to install
(defaults to `%LOCALAPPDATA%\Programs\CaptionCreator`, a standard per-user
location — no admin rights needed, but you can type any path you'd rather use
instead), and offers to add a Desktop shortcut.

The shortcut launches `CaptionCreatorLauncher.exe`, which checks for a newer
release on every launch and updates in place before starting the app if one's
available — `formats/`, `watermark/`, your DeviantArt login, and the crash log
are never touched by an update, so custom formats and settings always survive.
Re-running `install.bat` later re-installs into the same location the same way.

### Manual install (running from source)

For development, or if you'd rather run it straight from Python:

#### 1. Clone the repository

```
git clone https://github.com/crazygirlashley/caption-creation-tool.git
cd caption-creation-tool
```

#### 2. Install Python dependencies

```
pip install -r requirements.txt
```

#### 3. (Optional) Install the Aardvark Cafe font

The X-Change title and tagline use **Aardvark Cafe**, a free font available on DaFont.

1. Download from [dafont.com/aardvark-cafe.font](https://www.dafont.com/aardvark-cafe.font)
2. Right-click the `.ttf` file → **Install for all users** (or **Install** for current user)

The app detects the font automatically on startup. If it isn't installed, a fallback font is used and you'll be prompted with a download link.

#### 4. (Optional) Add a watermark

Place a single image (PNG, JPG, BMP, GIF, or WebP) in the `watermark/` folder:

```
watermark/
  your_watermark.png
```

If exactly one image is present, it loads automatically at startup and appears in the bottom-left of every output. If the folder is empty or contains more than one image, the watermark is disabled.

---

## Running the App

**If you used the Quick install:** use the Desktop shortcut, or run
`CaptionCreatorLauncher.exe` directly from the install folder.

**If you're running from source (Manual install):**

Option A — Python directly:
```
python caption_creator.py
```

Option B — Batch launcher (Windows):

Double-click `run.bat`. It uses `python` from your system PATH, so no editing needed as long as Python is installed and on PATH. If this is a git checkout, it also checks for updates first: `git fetch`, compares against `origin`, and pulls with `--ff-only` if you're behind. It never overwrites local changes or force-pulls — if a fast-forward isn't possible (e.g. you have local edits), it just prints a note and launches the current version as-is. If git isn't installed or this isn't a git checkout, the update check is skipped silently. The console window closes on its own once the app exits normally; it only stays open (with `pause`) if `caption_creator.py` exits with an error, so you can read what happened.

---

## DeviantArt Upload (Optional)

Caption Creator can save finished images, GIFs, and videos directly to your DeviantArt account as private drafts, and optionally publish them to your gallery.

### One-time setup

1. Log into DeviantArt and go to [deviantart.com/studio/apps](https://www.deviantart.com/studio/apps)
2. Register a new application — choose **Public** client type
3. Set the OAuth2 redirect URI to **exactly** `http://127.0.0.1:24858` (DeviantArt matches redirect URIs exactly, including the port — no other value will work)
4. Copy your **Client ID**
5. In Caption Creator, click **DA Settings** in the toolbar and paste your Client ID

### Usage

1. Click **DA Login** in the toolbar — your browser opens the DeviantArt authorization page; log in and authorize the app
2. Once logged in, **DA Login** is replaced by **Send to DA…** and **Log Out** (both hidden again if you log out)
3. Open an image, GIF, or video and apply your caption styling
4. Click **Send to DA…** — a dialog prompts for a **Title** (required, starts empty) and a **Description** (pre-filled with your caption text, sent as DeviantArt's artist comments); if you started from a video, you'll also choose to upload as **MP4** or **GIF** (defaults to MP4); all are editable before sending
5. The file saves as a private draft on your DeviantArt account
6. Click **Publish to Gallery** in the confirmation dialog to make it public

Click **Log Out** to clear the cached token (with a confirmation prompt) if you need to re-authorize or switch accounts. Tokens are saved to `da_tokens.json` and refreshed automatically (valid for 3 months). Your Client ID is saved to `da_settings.json`. Both files are excluded from version control.

---

## Custom Formats

Caption styles are defined as JSON files in the `formats/` folder. Easiest way to make one: pick a format, adjust the settings in the Caption/Header/Footer/Watermark tabs, then click **Save As…** to save it under a new name (or **Save** to overwrite the currently selected format). New formats you create this way are personal — only the four built-in formats (`Standard`, `X-Change`, and their `(Vertical)` counterparts) are tracked in version control by default, so your own formats won't accidentally get pushed/committed.

You can also hand-edit or drop in a `.json` file directly — it'll appear in the Format dropdown automatically (click the **↺** button next to the dropdown to reload without restarting).

Each format file supports the following fields:

| Field | Type | Description |
|---|---|---|
| `name` | string | Display name shown in the dropdown |
| `page_bg_color` | hex string | Caption panel background color |
| `font_color` | hex string | Caption text color |
| `stroke_width` | int | Text stroke thickness in px |
| `stroke_color` | hex string | Stroke color |
| `font_family` | string | Caption font name |
| `bold` | bool | Bold caption text |
| `padding` | int | Caption text padding in px |
| `auto_size` | bool | Auto-fit font size to panel height |
| `layout` | `"horizontal"` / `"vertical"` | Caption panel beside the image (default) or below it |
| `cap_panel_size` | int | Caption panel width (horizontal layout) or height (vertical layout) in px. Ignored if `dynamic_width` is true |
| `dynamic_width` | bool | Auto-set the caption panel size to 1.25× the source image/GIF/video width, overriding `cap_panel_size`. Locks the Caption Width/Height spinbox in the UI while active |
| `shadow` | bool | Drop shadow on all overlaid text |
| `align` | `"left"` / `"center"` / `"right"` | Caption text alignment |
| `caption_side` | `"left"` / `"right"` | Which side of the image the caption panel sits on (horizontal layout only; ignored for `"vertical"`) |
| `show_pill_presets` | bool | Show pill color preset buttons |
| `header_enabled` | bool | Show title overlay on image |
| `header_text` | string | Default title text |
| `header_font` | string | Title font (use `"Aardvark Cafe"` for the X-Change font) |
| `header_size` | int | Title font size |
| `footer_enabled` | bool | Show tagline overlay on image |
| `footer_text` | string | Default tagline text |
| `footer_font` | string | Tagline font |
| `footer_size` | int | Tagline font size |

See `formats/Standard.json`, `formats/X-Change.json`, and their `(Vertical)` counterparts for examples.

---

## Building a Release (maintainers)

The Quick install downloads a prebuilt release rather than running from source,
so publishing one requires a local build first:

1. Bump the `VERSION` file (e.g. `0.2.0`) and commit it.
2. `pip install pyinstaller`
3. `pyinstaller packaging/build.spec` — produces `dist/CaptionCreator/`
   containing `CaptionCreator.exe`, `CaptionCreatorLauncher.exe`, a shared
   `_internal/` dependency folder, and `version.txt`.
4. Zip the *contents* of `dist/CaptionCreator/` (not the folder itself) as
   `CaptionCreator-win64.zip`.
5. Create a GitHub Release tagged `v<VERSION>` (e.g. `v0.2.0` — the launcher
   strips a leading `v` when comparing) and attach that zip as a release
   asset. `install.bat` and `CaptionCreatorLauncher.exe` both fetch it from
   `.../releases/latest/download/CaptionCreator-win64.zip`, a stable URL
   that always resolves to whatever the newest published release is.

`app_paths.py` is what makes the two builds coexist correctly: it resolves a
writable `BASE_DIR` (next to the exe when frozen, next to `caption_creator.py`
when run from source) for `formats/`, `watermark/`, DA credentials, and the
crash log, separately from a read-only `RESOURCE_DIR` (PyInstaller's bundled
`_internal/` when frozen) for `assets/` and the seed copies of the 4 built-in
formats. The launcher only ever replaces `CaptionCreator.exe` and `_internal/`
on update — nothing in `BASE_DIR` is part of the release zip, so it's never
touched.

---

## Project Structure

```
caption-creation-tool/
├── app_paths.py            # Frozen-aware BASE_DIR/RESOURCE_DIR resolution
├── caption_creator.py      # Main application
├── da_client.py             # DeviantArt API client (OAuth2 PKCE, draft upload)
├── assets/                   # App branding
│   ├── logo.png                # Window/taskbar icon
│   └── icon.png                 # .exe / Desktop shortcut icon (built releases only)
├── formats/                 # Format definitions (JSON) — add your own here
│   ├── Standard.json
│   ├── Standard (Vertical).json
│   ├── X-Change.json
│   └── X-Change (Vertical).json
├── packaging/                # PyInstaller build (see "Building a Release")
│   ├── build.spec               # Builds CaptionCreator.exe + CaptionCreatorLauncher.exe
│   └── launcher.py               # Update checker + launcher source
├── VERSION                   # Plain-text version string, source of truth for releases
├── install.bat               # Windows installer — downloads the latest built release
├── requirements.txt          # Python dependencies (source/dev use)
├── run.bat                    # Windows launcher (source/dev use)
├── watermark/                 # Drop a single watermark image here (gitignored)
└── .gitignore
```

---

## License

Personal use. Font licenses apply separately — see the Aardvark Cafe font page for terms.
