# Caption Creator

A Windows desktop app for creating X-Change-style captioned images, animated GIFs, and MP4 videos. Place a styled text panel beside any image, overlay title and tagline text, apply pill color presets, and send the finished output directly to DeviantArt.

---

## Features

- **Standard format** — image on the left, styled text panel on the right
- **X-Change format** — adds title (top-left overlay), tagline (bottom-left overlay), and pill color presets matching real X-Change Maker styling
- **Pill presets** — Pink, Blue, and Purple with matching background and stroke colors
- **Caption panel** — auto-fit text sizing, left/center/right alignment, bold, stroke; user-selectable font
- **X-Change text effects** — drop shadow and stroke on all overlaid text; defaults to Tahoma Bold for the caption body and Aardvark Cafe for the title and tagline (optional install)
- **Watermark** — auto-loads a single image from the `watermark/` folder; scales to 2× the footer font size
- **Animated GIF support** — all frames are processed; background rendering keeps the UI responsive
- **MP4 video support** — open an MP4, caption every frame the same way as a GIF, and export as either an MP4 or a GIF, regardless of which one you started with
- **DeviantArt integration** — save finished output as a private draft on DeviantArt with one click; publish to gallery when ready
- **Crash logging** — rotating log at `caption_creator_crash.log` with watchdog thread for hang detection

---

## Requirements

- **Windows 10 or later**
- **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/)
- **Pillow**, **requests**, **imageio**, and **imageio-ffmpeg** (installed via pip — see below). `imageio-ffmpeg` bundles its own ffmpeg binary, so no separate system-wide ffmpeg install is needed.

> Tkinter is included with the standard Python installer on Windows. If `import tkinter` fails, re-run the Python installer and ensure the **tcl/tk and IDLE** optional feature is checked.

---

## Installation

### 1. Clone the repository

```
git clone https://github.com/crazygirlashley/caption-creation-tool.git
cd caption-creation-tool
```

### 2. Install Python dependencies

```
pip install -r requirements.txt
```

### 3. (Optional) Install the Aardvark Cafe font

The X-Change title and tagline use **Aardvark Cafe**, a free font available on DaFont.

1. Download from [dafont.com/aardvark-cafe.font](https://www.dafont.com/aardvark-cafe.font)
2. Right-click the `.ttf` file → **Install for all users** (or **Install** for current user)

The app detects the font automatically on startup. If it isn't installed, a fallback font is used and you'll be prompted with a download link.

### 4. (Optional) Add a watermark

Place a single image (PNG, JPG, BMP, GIF, or WebP) in the `watermark/` folder:

```
watermark/
  your_watermark.png
```

If exactly one image is present, it loads automatically at startup and appears in the bottom-left of every output. If the folder is empty or contains more than one image, the watermark is disabled.

---

## Running the App

**Option A — Python directly:**
```
python caption_creator.py
```

**Option B — Batch launcher (Windows):**

Double-click `run.bat`. It uses `python` from your system PATH, so no editing needed as long as Python is installed and on PATH.

---

## DeviantArt Upload (Optional)

Caption Creator can save finished images and GIFs directly to your DeviantArt account as private drafts, and optionally publish them to your gallery.

### One-time setup

1. Log into DeviantArt and go to [deviantart.com/studio/apps](https://www.deviantart.com/studio/apps)
2. Register a new application — choose **Public** client type
3. Set the OAuth2 redirect URI to **exactly** `http://127.0.0.1:24858` (DeviantArt matches redirect URIs exactly, including the port — no other value will work)
4. Copy your **Client ID**
5. In Caption Creator, click **DA Settings** in the toolbar and paste your Client ID

### Usage

1. Click **DA Login** in the toolbar — your browser opens the DeviantArt authorization page; log in and authorize the app
2. Once logged in, the **Send to DA…** button appears in the toolbar (it's hidden until login succeeds)
3. Open an image or GIF and apply your caption styling
4. Click **Send to DA…** — the file saves as a private draft on your DeviantArt account
5. Click **Publish to Gallery** in the confirmation dialog to make it public

Tokens are saved to `da_tokens.json` and refreshed automatically (valid for 3 months). Your Client ID is saved to `da_settings.json`. Both files are excluded from version control.

---

## Custom Formats

Caption styles are defined as JSON files in the `formats/` folder. You can create your own format by adding a new `.json` file there — it will appear in the Format dropdown automatically (click the **↺** button next to the dropdown to reload without restarting).

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
| `show_pill_presets` | bool | Show pill color preset buttons |
| `header_enabled` | bool | Show title overlay on image |
| `header_text` | string | Default title text |
| `header_font` | string | Title font (use `"Aardvark Cafe"` for the X-Change font) |
| `header_size` | int | Title font size |
| `footer_enabled` | bool | Show tagline overlay on image |
| `footer_text` | string | Default tagline text |
| `footer_font` | string | Tagline font |
| `footer_size` | int | Tagline font size |

See `formats/Standard.json` and `formats/X-Change.json` for examples.

---

## Project Structure

```
caption-creation-tool/
├── caption_creator.py     # Main application
├── da_client.py           # DeviantArt API client (OAuth2 PKCE, draft upload)
├── formats/               # Format definitions (JSON) — add your own here
│   ├── Standard.json
│   ├── Standard (Vertical).json
│   ├── X-Change.json
│   └── X-Change (Vertical).json
├── requirements.txt       # Python dependencies
├── run.bat                # Windows launcher
├── watermark/             # Drop a single watermark image here (gitignored)
└── .gitignore
```

---

## License

Personal use. Font licenses apply separately — see the Aardvark Cafe font page for terms.
