# Caption Creator

A Windows desktop app for creating X-Change-style captioned images and animated GIFs. Place a styled text panel beside any image, overlay title and tagline text, apply pill color presets, and send the finished output directly to DeviantArt.

---

## Features

- **Standard format** — image on the left, styled text panel on the right
- **X-Change format** — adds title (top-left overlay), tagline (bottom-left overlay), and pill color presets matching real X-Change Maker styling
- **Pill presets** — Pink, Blue, and Purple with matching background and stroke colors
- **Caption panel** — auto-fit text sizing, left/center/right alignment, bold, stroke, drop shadow
- **Fonts** — Tahoma Bold for caption body; Aardvark Cafe for X-Change title and tagline (optional install)
- **Watermark** — auto-loads a single image from the `watermark/` folder; scales to 2× the footer font size
- **Animated GIF support** — all frames are processed; background rendering keeps the UI responsive
- **DeviantArt integration** — upload finished output to Sta.sh with one click; optionally publish to gallery
- **Crash logging** — rotating log at `caption_creator_crash.log` with watchdog thread for hang detection

---

## Requirements

- **Windows 10 or later**
- **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/)
- **Pillow** and **requests** (installed via pip — see below)

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

Place a single PNG or JPG in the `watermark/` folder:

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

Edit `run.bat` and update the Python path to match your installation, then double-click it:
```bat
@echo off
"C:\Path\To\Python312\python.exe" "%~dp0caption_creator.py"
pause
```

---

## DeviantArt Upload (Optional)

Caption Creator can upload finished images and GIFs directly to your DeviantArt Sta.sh staging area and optionally publish them to your gallery.

### One-time setup

1. Log into DeviantArt and go to [deviantart.com/developers/apps](https://www.deviantart.com/developers/apps)
2. Register a new application — choose **Public** client type
3. Set the OAuth2 redirect URI to `http://127.0.0.1`
4. Copy your **Client ID**
5. In Caption Creator, click **DA Settings** in the toolbar and paste your Client ID

### Usage

1. Open an image or GIF and apply your caption styling
2. Click **Send to DeviantArt…**
3. Your browser will open the DeviantArt authorization page — authorize the app
4. The file uploads to your private Sta.sh area
5. Click **Publish to Gallery** in the confirmation dialog to make it public

Tokens are saved to `da_tokens.json` and refreshed automatically (valid for 3 months). Your Client ID is saved to `da_settings.json`. Both files are excluded from version control.

---

## Project Structure

```
caption-creation-tool/
├── caption_creator.py     # Main application
├── da_client.py           # DeviantArt API client (OAuth2 PKCE, Sta.sh upload)
├── requirements.txt       # Python dependencies
├── run.bat                # Windows launcher
├── watermark/             # Drop a single watermark image here (gitignored)
└── .gitignore
```

---

## License

Personal use. Font licenses apply separately — see the Aardvark Cafe font page for terms.
