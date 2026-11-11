# Agape Aftershot

**Everything a shoot needs, from the card to the client — on your own computer.**

Agape Aftershot is a post-shoot workflow for photographers who shoot RAW. It
copies the card and proves the copy, splits a card into the shoots it really
holds, sets aside the soft and blinked frames, and gives you a darkroom to make
your final selection in. It runs entirely on your machine: no cloud, no
account, no subscription, and it never deletes a photograph.

You run it on a Windows PC or a Mac and use it from that computer's browser —
or from your phone or tablet, as an installed app, while the computer does the
work.

*Hecho por Andres Martinez · Agape Studio, New York. Las instrucciones están en
inglés porque la interfaz lo está; funciona igual si escribes en español.*

---

## What it does

Three rooms, each with its own colour.

### Aftershot (green) — get the photographs off the card and cleaned up

Run the whole thing start to finish, or any one step by itself.

| step | what happens |
|---|---|
| **Import & Verify** | Copies a card or a folder and checksums every file against the original. The card is only ever read. When the report says every file matched, it is safe to reformat in camera. |
| **Separate Projects** | One card often holds several shoots. It reads capture times *and* what changes in the picture, proposes where they divide, and — once you confirm and name them — gives each its own folder. |
| **Sift** | Judges focus on the face, not the frame, so shallow depth of field is not mistaken for a miss. Soft, blinked and ruined frames are moved to a review folder for your call. |

### Darkroom (red) — choose

The lights go out and the room turns red; the photograph stays neutral, because
you cannot judge colour under a safelight. Three ways to work:

- **I have my numbers** — paste `8417, 8420-8424, 8431` and it finds and moves them.
- **Develop** — every frame, big, one at a time. Arrow keys and Space, or swipe on a phone.
- **Assisted Development** — near-identical frames are grouped first; pick the best of each.

Zoom reads the original file at 100% or 200%. Your picks are saved as you go.

### Files (blue) — a file explorer for the computer's drives

Browse every drive from any device: thumbnails (including RAW), quick look for
images, video, audio, PDF and text, search, copy, move, rename, upload from
your phone, download to it. A folder can be handed straight to Sift, Separate
Projects or the Darkroom.

### And around them

- **One session, many screens.** Start on the laptop, pick it up on the phone;
  it opens where you were. Only one device holds the controls at a time.
- **It keeps working when you close the app**, and can send a notification when a job finishes.
- **Vibes.** Up to four moods for the corner button — your own music files, or
  songs and playlists searched from YouTube Music and played through YouTube's
  own player.
- **Home-screen shortcuts** on Android for *New Aftershot*, *New Darkroom* and *Files*.

---

## The rules it never breaks

- **Nothing is deleted.** Rejects are *moved* to a folder you can look through.
  In Files, delete means the Recycle Bin on Windows and the Trash on a Mac.
- **Nothing is overwritten.** A name already taken is skipped or numbered.
- **Dry run first.** Anything that moves files shows the plan before it applies.
- **Everything is undoable.** Each applied move writes a journal that `undo` can reverse.
- **Nothing leaves the machine.** The only things that reach the internet are
  the web fonts, and YouTube if you choose to use it for music.

---

## Install

It runs on **Windows 10 or 11** and on **macOS**. It has been used daily on one
Windows machine, with Nikon `.NEF` files; other RAW formats that embed a JPEG
preview should work but are less tested.

> **macOS is new and has not been run on a Mac by its authors.** The code for
> it was written from Apple's documentation and tested only as far as a Windows
> machine can test it. Expect rough edges on a first install, and please
> [open an issue](https://github.com/agapestudionyc/agape-aftershot/issues)
> with what you see - that is how it gets finished.

### Windows

**1. Install Python 3.12**

```bash
winget install --id Python.Python.3.12 --scope user
```

**2. Get the code**

```bash
git clone https://github.com/agapestudionyc/agape-aftershot.git
```

```bash
cd agape-aftershot
```

(Or use **Code → Download ZIP** on GitHub and unzip it.)

**3. Run setup, once**

```bash
setup.cmd
```

This creates a private Python environment in `%USERPROFILE%\.cullkit\venv` and
downloads the libraries (about 250 MB, a few minutes). The face models
(~300 MB) download by themselves the first time you sift.

**4. Tell it where your photographs live**

```bash
cull.cmd config --archive-root "D:\Photos\Archive" --work-root "D:\Photos\Current"
```

*Archive* is where imported cards land; *Working* is where your selections go.
You can change both later in the app's Settings.

**5. Start it**

Double-click **`Agape Aftershot.cmd`**, or:

```bash
cull.cmd app
```

Your browser opens at `http://localhost:8765`. Leave the console window open
while you use it. Press the green **+** to start.

### macOS (iMac, MacBook, Mac mini, Mac Studio)

**1. Install Python 3.12.** With [Homebrew](https://brew.sh):

```bash
brew install python@3.12
```

or download it from [python.org](https://www.python.org/downloads/macos/).

**2. Get the code**

```bash
git clone https://github.com/agapestudionyc/agape-aftershot.git
```

(Or **Code → Download ZIP** on GitHub.)

**3. Run setup, once.** In Finder, double-click **`setup.command`** inside the
folder. The first time, macOS may refuse because the file came from the
internet: right-click it, choose **Open**, then **Open** again. From Terminal
it is:

```bash
cd agape-aftershot && bash setup.command
```

It creates a private Python environment in `~/.cullkit/venv` and downloads the
libraries. If it stops with a message about a compiler, install Apple's
command-line tools and run it again:

```bash
xcode-select --install
```

**4. Tell it where your photographs live**

```bash
./cull config --archive-root "/Volumes/Photos/Archive" --work-root "/Volumes/Photos/Current"
```

**5. Start it.** Double-click **`Agape Aftershot.command`**. Your browser opens
at `http://localhost:8765`. Keep the Terminal window open while you use it.

**What is different on a Mac**

- Drives are the startup disk plus whatever is mounted under `/Volumes`. A
  camera card is recognised by its `DCIM` folder.
- Deleting in Files puts things in the **Trash** through Finder. The first
  time, macOS asks whether Terminal may control Finder; say yes, or files are
  moved into the Trash folder directly instead.
- The first time it reads an external drive, the Desktop or Documents, macOS
  asks for permission. If a folder shows as empty that should not be, give
  Terminal access in **System Settings → Privacy & Security → Files and Folders**.
- The Mac is kept awake while a job runs (`caffeinate`) and released after.
- It does not start by itself at login yet.

### Optional extras

| for | Windows | macOS |
|---|---|---|
| Playing video/audio the browser cannot (`.mkv`, `.avi`, `.wma`…), video thumbnails, converting music you add to a vibe | `winget install Gyan.FFmpeg` | `brew install ffmpeg` |
| Searching YouTube Music from the Vibes panel | `"%USERPROFILE%\.cullkit\venv\Scripts\pip.exe" install ytmusicapi` | `~/.cullkit/venv/bin/pip install ytmusicapi` |

---

## Use it from your phone

**On the same wifi:** the console prints an address like
`http://192.168.1.20:8765` and a six-digit code. Open the address on the phone
and type the code once. That code is the only thing between your wifi and your
photographs, so do not share a screenshot of it.

**From anywhere, as a real installed app:** Android only installs a web app
from an `https` address. The simplest way to get one that is private to your
own devices is [Tailscale](https://tailscale.com):

1. Install Tailscale on the PC and the phone, signed in to the same account.
2. In the Tailscale admin console, enable **HTTPS Certificates** (DNS page).
3. On the computer (on a Mac, the command is inside the app:
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale`):

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   ```

4. On the phone, open `https://<your-pc>.<your-tailnet>.ts.net` in Chrome →
   menu → **Install app**.

Press and hold the installed icon for the shortcuts, and drag any of them to
the home screen.

If the `https` address will not open on the phone but the wifi address does,
check that Tailscale on the PC is allowing incoming connections
(`tailscale set --shields-up=false`).

---

## Music

No music ships with this repository. The app works without any — the vibe
button falls back to a synthesised loop — and you add your own in
**Settings → Vibes**, or by dropping files into `cullkit/web/audio/` named
after the vibe (`afro - Song.mp3`, `metal - Song.mp3`, `schranz - Song.mp3`).

---

## The command line

Everything the app does is also a command, which is how it started. See
[docs/CLI.md](docs/CLI.md).

```bash
cull.cmd drives
```

```bash
cull.cmd quality "D:\Photos\Archive\2026-03-14 Sofia"
```

Every command is a dry run until you add `--apply`.

---

## For developers

- `cullkit/` — the Python package. `plat.py` holds everything that differs
  between Windows and macOS; `server.py` is the web server and session;
  `cli.py` the commands; `files.py` the explorer; `quality.py`, `faces.py`,
  `scan.py`, `poses.py` the judging.
- `cullkit/web/` — the interface: `index.html`, plus `files.js` / `files.css`.
  No build step, no framework.
- `tests/` — run them with:

  ```bash
  "%USERPROFILE%\.cullkit\venv\Scripts\python.exe" -m unittest discover -s tests
  ```

  On a Mac: `PYTHONPATH=. ~/.cullkit/venv/bin/python -m unittest discover -s tests`

Defaults such as the similarity and blur thresholds were calibrated on one
photographer's frames. Expect to tune them for your own.

## Open source

Agape Aftershot is free to use, change and share under the [MIT license](LICENSE).
If you improve it, send it back: see [CONTRIBUTING.md](CONTRIBUTING.md).
Reports of how it behaves with other cameras and RAW formats are especially
welcome - open an [issue](https://github.com/agapestudionyc/agape-aftershot/issues).

## Status

This is one photographer's working tool, shared as it is. It is used daily on
one Windows machine and has a test suite, but it has not been run on many
setups, and the macOS version has not been run on a Mac at all yet. Back up your cards until you trust it — and it is built so that you can:
it never formats, deletes or overwrites anything.
