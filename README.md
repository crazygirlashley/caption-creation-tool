# Caption Creator

[![Latest release](https://img.shields.io/github/v/release/crazygirlashley/caption-creation-tool)](https://github.com/crazygirlashley/caption-creation-tool/releases/latest)

A Windows desktop app for creating X-Change-style captioned images, animated GIFs, and MP4 videos. Place a styled text panel beside any image, overlay title and tagline text, apply pill color presets, and send the finished output directly to DeviantArt.

> **🔞 Adults only (18+).** Caption Creator is made for adult (NSFW) caption art and is intended for a mature audience: **Find Online** searches adult websites and can show explicit images, and the **AI writer** can produce sexually explicit text. The app asks you to confirm you're 18 or older the first time it starts. Only use it where viewing adult content is legal for you.

---

## Features

- **App background** — an image or animated GIF behind the Editor (around its writing and sidebar cards) and the Caption Builder's preview, **fit to the window** or **tiled** as a repeating pattern. Themes can bring their own (Bimbo: animated lipstick kisses; Sissy: vintage lace), and **⚙ Settings → Background…** lets you use the theme's background, **your own picture or GIF** (with a **dim** slider and an option to pause animation; copied into `backgrounds/` so it keeps working if the original moves), or none
- **Editor and Caption Builder views** — the toolbar's **✍ Editor / 🖼 Caption Builder** toggle switches between a focused writing view and the image builder. The Editor is a full-width writing space for the caption story with the AI tools as one-click buttons (write from a prompt, Smooth & polish, Shorten to fit, Add more detail, Fix spelling & grammar, custom instruction), spell check, a live word count that says whether the text fits beside the image at a readable size, and a small live preview of the caption on the image. Both views edit the same caption; the app reopens in whichever you used last
- **Standard format** — image on the left, styled text panel on the right
- **X-Change format** — adds title (top-left overlay), tagline (bottom-left overlay), and pill color presets matching real X-Change Maker styling
- **Text Message format** — renders the caption as a phone-style chat: build a list of messages, mark each as sent by you or the other person, reorder them, and toggle your bubbles between iMessage Blue and SMS Green
- **Pill presets** — Pink, Blue, and Purple with matching background and stroke colors
- **Caption panel** — auto-fit text sizing (measured at the final output resolution, so fitted text never spills past the panel; words too long for the panel, like URLs, wrap mid-word), fractional font sizes in 0.25 steps (any value can be typed), left/center/right alignment, bold, stroke; choose from every font installed on your system. Caption text is drawn at full output resolution rather than upscaled with the image, so it stays sharp on small sources
- **Caption side** — for horizontal-layout formats, place the caption panel to the left or right of the image (defaults to right); hidden for vertical-layout formats, which always caption below the image
- **Save/Save As formats** — tweak any format's settings and save them under the same name or as a brand-new format; new formats you create are personal and stay out of version control by default
- **Title & tagline sizes** — accept fractional sizes (0.5 steps, any value can be typed). **Scale title & tagline with image** (Header or Footer tab, saved with the format; off by default) treats the sizes as meant for a 300px-tall image and scales them to the real one, so they keep the same proportion on a small GIF and a large photo — the footer-linked watermark follows
- **X-Change text effects** — drop shadow and stroke on all overlaid text; defaults to Tahoma Bold for the caption body and Aardvark Cafe for the title and tagline (optional install)
- **Watermark** — auto-loads a single image from the `watermark/` folder; scales to 2× the footer font size when the footer is enabled, or, while the footer is off, to 1/6 of the image's height (**Auto**, the default — the watermark keeps the same proportions on any size source). Typing a height into the Watermark Height field switches Auto off and keeps that manual size; re-check Auto to go back to scaling with the image
- **Color eyedropper** — a **Pick** button next to Page BG, Text, and Stroke color swatches lets you click anywhere on the preview image to sample that pixel's color instead of using the color chooser
- **Animated GIF support** — all frames are processed; background rendering keeps the UI responsive, and the in-app preview plays at the source's real frame rate
- **MP4 video support** — open an MP4, caption every frame the same way as a GIF, and export as either an MP4 or a GIF, regardless of which one you started with. MP4→GIF keeps the video's exact length (GIF delays are whole hundredths of a second, so per-frame delays are spread to match, and videos above 50fps keep every other frame since GIFs can't play faster)
- **Minimum output size** — final output is upscaled (never downscaled, aspect ratio preserved) to at least 1280×720 if the source is smaller; the Preview panel label shows the actual output size next to the on-screen preview, which can still display smaller to fit the window
- **Output Size Override** — a checkbox above the Preview panel that reveals a 0–100% slider once checked. 0% is the 1280×720 floor (shrinks a source that's already bigger than it, aspect ratio preserved, no stretching — small sources have no room to shrink below it). 100% is *double* today's normal default size (the composite's natural size, or upscaled to the floor if it's smaller), giving headroom to upscale beyond the usual default when you want a bigger export. Checking the box doesn't change anything by itself — the slider starts wherever reproduces today's normal output size for the file you have open (that position depends on the file, since it's wherever the default sits between the 0% floor and the 100% ceiling), so you only get a different size once you actually move it
- **Estimated output size** — a live label under the Preview panel shows the estimated final file size as you edit: an exact in-memory encode for static images, and a real GIF/MP4 encode (sampled from a bounded number of frames for large, not-yet-fully-loaded sources) for animations. It turns amber, then red with a warning, once the estimate crosses DeviantArt's 80MB upload cap. Save and Send to DA also check the real output size afterward and warn if it's still too big. When a GIF's estimate is over the cap, a **Fit under 80 MB** button finds the largest output size (then fewest colors lost) that fits and applies it; MP4 sources also show what the file would weigh if saved as GIF
- **GIF compression** — a "Compress GIF Output" checkbox, shown only once a GIF is loaded, reveals a color-count slider (256 down to 2) that reduces the palette to shrink the file. Unchecked, or left at 256, it's a no-op; dragging it down updates the live size estimate immediately so you can see the size/quality trade-off before saving, and the same setting is applied to the real Save and Send to DA exports
- **Large-file safety** — opening a GIF/video whose frames would use a lot of memory prompts you to load it in full, or use a single-frame preview instead (editing stays fast and light on RAM); either way, **Save** and **Send to DA** composite and write one frame at a time straight to the output file instead of building the whole thing in memory first, so exporting doesn't need to hold the full file in RAM regardless of length
- **Compact GIF encoding** — animated GIF exports share one palette across frames and store only the pixels that changed from the previous frame, so the static caption panel is written once; GIF sources are upscaled nearest-neighbor (they're already dithered, so smoothing only adds colors and size). Keeps the file small enough for viewers to play at the source's real frame rate
- **Emoji** — emoji in pasted captions, titles, taglines and Text Message bubbles (e.g. `Megan ❤️: ... 💕`) render in full color using Windows' Segoe UI Emoji font, sized and aligned to the text. Multi-part emoji (skin tones, combined ones like 👩🏼‍🦰) show as their base emoji (👩), since joining them needs a text-shaping library Pillow isn't built with here
- **Trim & speed** — for GIF/MP4 sources, **Frames … to …** keeps just part of the animation and **Speed** (0.25×–3×, or type any value) plays it faster or slower; the preview, size estimate, Fit under 80 MB and export all use the trimmed, retimed version, and the row shows the resulting frame count and length
- **Projects & recent files** — **File → Save Project…** (Ctrl+Shift+S) saves everything about the current caption to a `.ccproj` file — source file, format settings, caption/chat text, title and tagline, sizes, compression, watermark, trim and speed — and **Open Project…** (Ctrl+Shift+O) puts it all back, ideal for multi-part series. Images picked from Find Online are copied next to the project (their temp download is cleaned up otherwise). If a project's settings differ from the saved format it used, they load as "<format> (project)" so your real format is untouched. **Recent Files** / **Recent Projects** are in the same menu, and Find Online's search box remembers recent searches
- **Drag and drop** — drop an image, GIF, MP4 or `.ccproj` project onto the window to open it; drag an image straight from a web browser and it's downloaded and opened
- **Spell check & grammar hints** — the caption box underlines misspellings in red (a 370k-word dictionary plus common names; contractions, curly apostrophes and emoji are understood) and likely grammar slips in blue (repeated words, lowercase "i", sentence capitals, a/an, "could of", "alot", spacing around punctuation). Right-click a word for suggestions, **Add to dictionary** (`user_dictionary.txt`) or **Ignore**, or a grammar fix; the same menu has Undo/Redo, Cut/Copy/Paste, **Fix spelling & grammar with AI**, and a toggle to turn checking off
- **Keyboard shortcuts** — Ctrl+O opens a file, Ctrl+S saves the output, Ctrl+Shift+O / Ctrl+Shift+S open / save a project, Ctrl+Z / Ctrl+Y undo / redo caption edits (including a whole AI writer run)
- **Detached export console** — GIF/MP4 exports (Save and Send to DA) run in a separate console window showing live per-frame progress, instead of on the app's main thread; the main window stays fully responsive during long exports, and the console closes on its own once the export finishes
- **Single-Frame Preview toggle** — a toolbar checkbox, shown only for GIF/MP4 sources, to manually switch between a live full-animation preview and a lightweight single-frame preview at any time — useful on lower-end machines even for files too small to trigger the automatic large-file prompt
- **Find Online…** — search [Barnorama](https://www.barnorama.com), [AcidCow](https://acidcow.com) and [sex.com](https://www.sex.com) (pictures and GIFs, grouped into one result each; male search terms search its male section) from inside the app for source images/GIFs. The query defaults to physical-description keywords (hair color, gender, body type, age, clothing) pulled from your current caption text, and results are ranked by how well their titles match it (hair color first). Posts about real celebrities are filtered out. Results group the actual images from each matching post under its title — click one to load it as the source, no manual download/re-open needed Built for finding photos of one attractive person: both sites are searched at once and results appear as each answers, multi-word queries also search their most distinctive word ("brunette woman" no longer collapses to a handful of off-topic posts), person galleries rank above memes and listicles, anything framed around children or teens is never shown, and a **Single person only** filter (on by default) uses on-device face detection (OpenCV YuNet) to hide group shots, memes and screenshots — previews are small, cached, and tagged **GIF** when animated. Each result group has a **More like this** button that searches for similar galleries using that post's descriptive words
- **DeviantArt integration** — save finished output to Sta.sh (DeviantArt's private storage area) with one click; publish to gallery when ready
- **UI themes** — Dark (default), Bimbo (pastel and hot pink, animated kiss-print background), Sissy (lilac and pastel pink, vintage lace background), or Basic (native Windows look) via **⚙ Settings → Theme**; add your own as JSON files. The UI uses a clean Segoe UI font, roomy buttons, an accent color for the main actions (Save, the selected view, "Write a story"), hover tooltips on toolbar and AI buttons (with keyboard shortcuts), helpful placeholder text in empty caption boxes, and a "drop an image here" prompt in the empty Caption Builder
- **Crash logging** — rotating log at `caption_creator_crash.log` with watchdog thread for hang detection

---

## Requirements

- **Windows 10 or later**
- For the **Quick install** below: nothing else — the download is a standalone build with Python, Tk, and ffmpeg all bundled in.
- For the **Manual install** (running from source): **Python 3.12+** — [python.org/downloads](https://www.python.org/downloads/), plus the packages in `requirements.txt` via pip (`pip install -r requirements.txt`): Pillow, requests, imageio, imageio-ffmpeg, beautifulsoup4, opencv-python-headless (Find Online's single-person filter) and tkinterdnd2 (drag and drop) — the last two are optional; the app runs without them. `imageio-ffmpeg` bundles its own ffmpeg binary, so no separate system-wide ffmpeg install is needed either way.

> Tkinter is included with the standard Python installer on Windows. If `import tkinter` fails when running from source, re-run the Python installer and ensure the **tcl/tk and IDLE** optional feature is checked.

---

## Installation

### Quick install (Windows)

Download [`install.bat`](install.bat) and run it — no Python or Git required on
your machine, it downloads a self-contained build from the
[latest release](https://github.com/crazygirlashley/caption-creation-tool/releases/latest).
It asks where to install
(defaults to `%LOCALAPPDATA%\Programs\CaptionCreator`, a standard per-user
location — no admin rights needed, but you can type any path you'd rather use
instead), and offers to add a Desktop shortcut.

The shortcut launches `CaptionCreatorLauncher.exe`, which checks for a newer
release on every launch and updates in place before starting the app if one's
available — `formats/`, `watermark/`, your DeviantArt login, and the crash log
are never touched by an update, so custom formats and settings always survive.
Re-running `install.bat` later re-installs into the same location the same way.

#### Which .exe do I run?

Every release contains two executables — always launch the first one, not the second:

| File | What it is |
|---|---|
| **`CaptionCreatorLauncher.exe`** | **Start here.** Checks GitHub for a newer release, updates in place if one's found, then opens the app. This is what `install.bat`'s Desktop shortcut points to. |
| `CaptionCreator.exe` | The app itself. `CaptionCreatorLauncher.exe` starts this automatically — you only need to run it directly if you specifically want to skip the update check. |

If you extracted the release zip by hand instead of using `install.bat`, run `CaptionCreatorLauncher.exe`, not `CaptionCreator.exe`, for the same reason.

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

## AI Writer (Optional)

**✍ AI Write…** (Caption Builder, under the caption box) / **Write a story from a prompt…** (Editor) turns a prompt into a caption
story and streams it straight into the caption text — e.g. *"TG story, first
person POV of a man waking up from hypnosis to find his sister feminized him,
ending on her line: …"*. Pick a length (~60 to ~350 words), and whether it
replaces the caption or is added to the end; edit the result freely, or
**Write** again for another take (Ctrl+Enter in the prompt box also writes).

It runs on **your own computer** with an open-source model, so it will write
explicit adult content and nothing is sent to a hosted AI service.

**✨ AI Edit** (next to ✍ AI Write in the Caption Builder, and as buttons in the Editor) works on the caption you already have — or just the selected part of it — instead of writing a new one: **Smooth & polish**, **Shorten to fit** (to the word count that fits beside the image at a comfortably readable size, worked out from the panel), **Add more detail**, **Fix spelling & grammar** (minimal edits), or a **Custom instruction** ("make it more teasing", "switch to third person"). The rewrite streams into place as one undo step (Ctrl+Z), and if you stop it or it fails the original text is put back.

**Automatic setup:** the first time you press **Write with AI…** with no local
model running, it offers to **Install for me**: it downloads the official
[Ollama](https://ollama.com) installer from Ollama's GitHub release, verifies
it against the SHA-256 checksum published there (and rejects an invalid
signature) before running it, installs it per-user (no admin), starts it,
and downloads the uncensored writing model you pick — sized to your PC:

| Model | Download | RAM | Notes |
|---|---|---|---|
| Hermes 3 3B (`hermes3:3b`) | ~2 GB | 6 GB+ | Fast; plainer prose. Default on 8 GB PCs |
| Hermes 3 8B (`hermes3:8b`) | ~4.7 GB | 10 GB+ | Best writing for most PCs |
| Dolphin Mistral 7B (`dolphin-mistral`) | ~4.1 GB | 10 GB+ | Uncensored, different voice |
| Mistral Small 24B abliterated | ~14 GB | 24 GB+ | Richest prose; needs a strong PC |

The dialog checks free disk space and RAM first and warns if a model won't
fit. Without a dedicated graphics card, expect about a minute per story.

**Use your own instead:** already running Ollama, LM Studio, KoboldCpp,
llama.cpp's `llama-server`, or text-generation-webui? Choose **Use my own…**
(or **⚙ Settings → AI Writer Settings…**), pick your app from the **Server**
list (or type a custom address), press **Refresh**, pick the model, **Save**.

Settings live in `ai_settings.json` (gitignored, never part of an update).
Reasoning models' `<think>` sections are stripped automatically.

---

## DeviantArt Upload (Optional)

Caption Creator can save finished images, GIFs, and videos directly to Sta.sh — DeviantArt's private storage area — and optionally publish them to your gallery.

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
5. The file uploads to Sta.sh, DeviantArt's private storage area
6. Click **Publish to Gallery** in the confirmation dialog to make it public

Click **Log Out** to clear the cached token (with a confirmation prompt) if you need to re-authorize or switch accounts. Tokens are saved to `da_tokens.json` and refreshed automatically (valid for 3 months). Your Client ID is saved to `da_settings.json`. Both files are excluded from version control.

---

## Custom Formats

Caption styles are defined as JSON files in the `formats/` folder. Easiest way to make one: pick a format, adjust the settings in the Caption/Header/Footer/Watermark tabs, then click **Save As…** to save it under a new name (or **Save** to overwrite the currently selected format). New formats you create this way are personal — only the six built-in formats (`Standard`, `X-Change`, `Text Message`, and their `(Vertical)` counterparts) are tracked in version control by default, so your own formats won't accidentally get pushed/committed.

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
| `chat_mode` | bool | Replace the caption text box with the Text Message chat builder and render the caption as chat bubbles |
| `header_enabled` | bool | Show title overlay on image |
| `header_text` | string | Default title text |
| `header_font` | string | Title font (use `"Aardvark Cafe"` for the X-Change font) |
| `header_size` | int | Title font size |
| `footer_enabled` | bool | Show tagline overlay on image |
| `footer_text` | string | Default tagline text |
| `footer_font` | string | Tagline font |
| `footer_size` | int | Tagline font size |

See `formats/Standard.json`, `formats/X-Change.json`, `formats/Text Message.json`, and their `(Vertical)` counterparts for examples.

---

## Custom Themes

UI color themes work the same way as formats: JSON files in a `themes/` folder, picked up automatically. **Basic** (this app's original look) is always available — it's just the system's native theme with no color overrides. `themes/Basic.json` ships for reference only (typical Windows default colors, handy as a starting point for your own theme); the app never loads it. **Dark** and **Bimbo** are the two built-in themes tracked in version control by default; drop your own `.json` file into `themes/` and it appears in **⚙ Settings → Theme** the next time you open that menu, no restart needed.

Each theme file requires every one of these keys (all hex color strings):

| Field | Description |
|---|---|
| `name` | Display name shown in the Theme menu |
| `bg` | Main window/panel background |
| `panel` | Buttons, tabs, and other raised surfaces |
| `input_bg` | Text boxes, entries, and spinboxes |
| `hover` | Button/control color on mouse-over |
| `border` | Widget borders and separators |
| `fg` | Primary text color |
| `muted_fg` | Secondary/disabled text color |
| `select_bg` | Selection highlight color |

A theme can also set an optional `font` key to a font family name (Bimbo uses `"Aardvark Cafe"`) to re-font the whole UI while it's active; if that font isn't installed, the normal UI font is used.

Your theme choice is saved to `ui_settings.json` (gitignored).

Optional keys: `accent`, `accent_fg` and `accent_hover` color the highlighted buttons (Save, the selected view, the main AI action) and selected tabs; `version` lets an updated built-in theme replace an older installed copy.

A theme can also have its own **background image or GIF**: `background` (a file name next to the theme JSON, or a full path), `background_mode` (`"fit"` to fill the window or `"tile"` to repeat it), `background_dim` (0–0.85, fades it toward the theme color), `background_animate` (`false` keeps a GIF still), and for patterns `background_scale`, `background_gap` (px between tiles) and `background_stagger` (offset every other row). Transparent areas show the theme's own color. See `themes/Dark.json`, `themes/Bimbo.json` and `themes/Sissy.json` for examples; artwork credits for the built-in backgrounds are in `themes/CREDITS.md`. A file missing any of these keys is skipped rather than crashing the app.

---

## Building a Release (maintainers)

The Quick install downloads a prebuilt release rather than running from source,
so publishing one requires a local build first:

1. Run the tests: `python -m unittest discover tests` (standard library only,
   no extra installs; uses synthetic images, so no source files needed).
2. Bump the `VERSION` file (e.g. `0.2.0`) and commit it.
3. `pip install pyinstaller`
4. `pyinstaller packaging/build.spec` — produces `dist/CaptionCreator/`
   containing `CaptionCreator.exe`, `CaptionCreatorLauncher.exe`, a shared
   `_internal/` dependency folder, and `version.txt`.
5. Zip the *contents* of `dist/CaptionCreator/` (not the folder itself) as
   `CaptionCreator-win64.zip`.
6. Create a GitHub Release tagged `v<VERSION>` (e.g. `v0.2.0` — the launcher
   strips a leading `v` when comparing) and attach that zip as a release
   asset. `install.bat` and `CaptionCreatorLauncher.exe` both fetch it from
   `.../releases/latest/download/CaptionCreator-win64.zip`, a stable URL
   that always resolves to whatever the newest published release is.

`app_paths.py` is what makes the two builds coexist correctly: it resolves a
writable `BASE_DIR` (next to the exe when frozen, next to `caption_creator.py`
when run from source) for `formats/`, `watermark/`, DA credentials, and the
crash log, separately from a read-only `RESOURCE_DIR` (PyInstaller's bundled
`_internal/` when frozen) for `assets/` and the seed copies of the built-in
formats and themes. The launcher only ever replaces `CaptionCreator.exe` and `_internal/`
on update — nothing in `BASE_DIR` is part of the release zip, so it's never
touched.

---

## Project Structure

```
caption-creation-tool/
├── app_paths.py              # Frozen-aware BASE_DIR/RESOURCE_DIR resolution
├── caption_creator.py        # Main application
├── ai_writer.py              # Local-LLM caption story writer (OpenAI-compatible API)
├── da_client.py              # DeviantArt API client (OAuth2 PKCE, Sta.sh upload)
├── wallpaper.py              # App background image/GIF rendering (fit / tile / dim)
├── ui_widgets.py             # Tooltips and placeholder text helpers
├── web_lookup.py             # Barnorama/AcidCow search client (Find Online…)
├── themes.py                 # UI theme loader/applier (⚙ Settings → Theme)
├── assets/
│   ├── logo.png              # Window/taskbar icon
│   ├── icon.png              # .exe / Desktop shortcut icon (built releases only)
│   ├── english_words.txt     # Wordlists used by Find Online's celebrity filter
│   ├── face_detection_yunet_2023mar.onnx  # Face detector for Find Online (MIT, opencv_zoo)
│   └── given_names.txt
├── formats/                  # Format definitions (JSON) — add your own here
│   ├── Standard.json
│   ├── Standard (Vertical).json
│   ├── Text Message.json
│   ├── Text Message (Vertical).json
│   ├── X-Change.json
│   └── X-Change (Vertical).json
├── themes/                   # UI theme definitions (JSON) — add your own here
│   ├── Basic.json            # Reference only, never loaded
│   ├── Bimbo.json / Bimbo_kisses.gif
│   ├── Sissy.json / Sissy_lace.png
│   ├── CREDITS.md            # Sources/licenses of the built-in theme backgrounds
│   └── Dark.json
├── packaging/                # PyInstaller build (see "Building a Release")
│   ├── build.spec            # Builds CaptionCreator.exe + CaptionCreatorLauncher.exe
│   └── launcher.py           # Update checker + launcher source
├── VERSION                   # Plain-text version string, source of truth for releases
├── install.bat               # Windows installer — downloads the latest built release
├── requirements.txt          # Python dependencies (source/dev use)
├── run.bat                   # Windows launcher (source/dev use)
├── tests/                    # Rendering/export regression tests (unittest)
├── watermark/                # Drop a single watermark image here (gitignored)
└── .gitignore
```

---

## License

Personal use. Font licenses apply separately — see the Aardvark Cafe font page for terms.
