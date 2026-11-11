"""The few things Windows and macOS do differently, in one place.

The program was written on Windows and most of it never touches the operating
system: it reads files, compares them, and moves them with the standard
library. What is left is short - which drives are mounted, how full they are,
where "the drive this folder is on" begins, keeping the machine awake during a
long job, and the Trash - and it lives here so that nothing else has to ask
which machine it is on.

**The macOS half has not been run on a Mac by the people who wrote it.** It was
written from the documentation and exercised here only as far as a Windows
machine can exercise it (the folder-walking and the fall-back paths, against
temporary folders). Treat a problem on a Mac as a bug in this file first.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# Things under /Volumes that are not somewhere a photographer keeps pictures.
_SKIP_VOLUMES = {"Recovery", "Preboot", "VM", "Update", "xarts", "iSCPreboot",
                 "Hardware", "com.apple.TimeMachine.localsnapshots"}


def disk_usage(path) -> tuple:
    """(total, free) in bytes, or (0, 0) when it cannot be read."""
    try:
        u = shutil.disk_usage(str(path))
        return u.total, u.free
    except OSError:
        return 0, 0


def mount_of(path) -> str:
    """Where the drive holding `path` begins.

    On Windows that is the anchor - `E:\\`. On a Mac every path has the same
    anchor, `/`, whichever disk it is on, so the folders are walked upward
    until one of them is a mount point: `/Volumes/Photos`, or `/` itself.
    """
    p = Path(path)
    try:
        p = p.resolve()
    except OSError:
        pass
    if IS_WIN:
        return p.anchor
    cur = p
    while True:
        try:
            if os.path.ismount(str(cur)):
                return str(cur)
        except OSError:
            pass
        if cur.parent == cur:
            return str(cur)
        cur = cur.parent


def same_drive(a, b) -> bool:
    return mount_of(a).lower() == mount_of(b).lower()


def drive_top(path) -> Path:
    """A folder at the top of `path`'s drive that the user can write to.

    `E:\\` on Windows and `/Volumes/Photos` on a Mac are fine as they are. The
    Mac's own startup disk is not - nobody may make folders in `/` - so there
    it is the home folder, which is the top of what is yours on that disk.
    """
    m = mount_of(path)
    if not IS_WIN and m == "/":
        return Path.home()
    return Path(m)


def _mac_kind(mount: str) -> str:
    """'removable' or 'fixed', as far as diskutil will say."""
    if not IS_MAC:
        return ""
    try:
        import plistlib
        run = subprocess.run(["diskutil", "info", "-plist", mount],
                             capture_output=True, timeout=4)
        info = plistlib.loads(run.stdout) if run.returncode == 0 else {}
    except Exception:
        return ""
    if not info:
        return ""
    if info.get("RemovableMediaOrExternalDevice") or info.get("Ejectable") \
            or info.get("Internal") is False:
        return "removable"
    return "fixed"


def posix_volumes(root="/", volumes_dir="/Volumes") -> list:
    """The startup disk, then everything mounted under /Volumes.

    -> [{"mount", "label", "kind", "tag"}]. `tag` is the two or three letters
    the interface shows where Windows shows `C:` - there are no drive letters
    here, but a list of drives still reads better with something short and
    bold at the start of each line.
    """
    out = []
    vdir = Path(volumes_dir)
    boot_label = ""
    extra = []
    try:
        entries = sorted(vdir.iterdir(), key=lambda p: p.name.lower()) if vdir.is_dir() else []
    except OSError:
        entries = []
    for e in entries:
        if e.name.startswith(".") or e.name in _SKIP_VOLUMES:
            continue
        try:
            real = e.resolve()
            if not real.is_dir():
                continue
        except OSError:
            continue
        # The startup disk appears in /Volumes as a link back to /.
        if str(real) == str(Path(root).resolve()):
            boot_label = e.name
            continue
        extra.append(e)
    out.append({"mount": str(root), "label": boot_label or ("Macintosh HD" if IS_MAC else "This computer"),
                "kind": "fixed", "tag": "Mac" if IS_MAC else "/"})
    for e in extra:
        try:
            card = (e / "DCIM").is_dir()
        except OSError:
            card = False
        kind = _mac_kind(str(e)) or ("removable" if card else "fixed")
        out.append({"mount": str(e), "label": e.name, "kind": kind,
                    "tag": "SD" if card else ("Ext" if kind == "removable" else "Vol")})
    return out


# ------------------------------------------------------------------ the Trash
def _unique(dst: Path) -> Path:
    if not dst.exists():
        return dst
    for n in range(2, 10000):
        cand = dst.with_name(f"{dst.stem} ({n}){dst.suffix}")
        if not cand.exists():
            return cand
    raise OSError("could not find a free name")


def move_to_bin(p: Path, bin_dir: Path) -> bool:
    """Move `p` into `bin_dir` under a name nothing there already has.

    A rename, so it only works within one disk - which is the point: a rename
    cannot half-happen, and it never turns into a copy followed by a delete.
    """
    try:
        bin_dir.mkdir(parents=True, exist_ok=True)
        os.rename(p, _unique(bin_dir / p.name))
        return True
    except OSError:
        return False


def trash(p: Path) -> bool:
    """Put `p` in the Trash on a Mac. Never deletes; False if it could not.

    Finder is asked first, because a file Finder trashed can be put back from
    the Trash with one click. If Finder will not answer - the first time, macOS
    asks whether this program may control it - the file is moved into the
    Trash folder of its own disk instead, which is the same place by hand.
    """
    p = Path(p)
    if IS_MAC:
        try:
            script = 'tell application "Finder" to delete POSIX file ' + \
                     json.dumps(str(p), ensure_ascii=False)
            run = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=30)
            if run.returncode == 0 and not p.exists():
                return True
        except Exception:
            pass
    mount = mount_of(p)
    if mount == mount_of(Path.home()):
        bin_dir = Path.home() / ".Trash"
    else:
        uid = getattr(os, "getuid", lambda: 0)()
        bin_dir = Path(mount) / ".Trashes" / str(uid)
    return move_to_bin(p, bin_dir)


# ---------------------------------------------------------------- staying awake
_caffeine = {"proc": None}


def keep_awake(want: bool) -> None:
    """Hold the Mac awake while there is work, and let it sleep after.

    `caffeinate -i` is Apple's own tool for exactly this: the machine may dim
    its screen but will not sleep while that process is alive.
    """
    if not IS_MAC:
        return
    proc = _caffeine["proc"]
    alive = proc is not None and proc.poll() is None
    if want and not alive:
        try:
            _caffeine["proc"] = subprocess.Popen(
                ["caffeinate", "-i"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            _caffeine["proc"] = None
    elif not want and alive:
        try:
            proc.terminate()
        except OSError:
            pass
        _caffeine["proc"] = None


def open_path(path: str, reveal: bool = False) -> None:
    """Open with whatever the system opens it with, or show it in its folder."""
    if IS_WIN:
        if reveal:
            subprocess.Popen(["explorer", "/select,", path])
        else:
            os.startfile(path)                 # noqa: S606 - the user's own file
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if reveal else path])


def tailscale_cli() -> str:
    """Where the Tailscale command lives, or '' if it is not installed."""
    if IS_WIN:
        exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
        if exe.exists():
            return str(exe)
    if IS_MAC:
        app = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        if app.exists():
            return str(app)
    return shutil.which("tailscale") or ""


def protected_roots() -> list:
    """Folders that belong to the system, not to the photographer."""
    if IS_WIN:
        return [Path(os.environ.get("SystemRoot", r"C:\Windows")),
                Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))]
    return [Path(x) for x in ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc",
                              "/private", "/Applications", "/opt", "/var", "/cores")] + \
           [Path.home() / "Library"]
