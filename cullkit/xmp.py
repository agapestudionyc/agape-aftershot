"""Stars and colour labels, written where Lightroom looks for them.

Lightroom reads a rating and a colour label for a RAW file from the `.xmp`
sidecar beside it (`DSC_0001.NEF` -> `DSC_0001.xmp`). That is the whole trick:
write those two fields and the selection made here arrives there, already
starred and coloured, the moment the folder is imported.

What this will and will not do:

  * It writes a sidecar. It never opens, changes or rewrites a photograph.
  * A sidecar that already exists is kept: only the rating and the label are
    changed inside it, everything else (develop settings, keywords) is left
    as it was, and a copy of the original goes to `_cullkit/xmp-backup` the
    first time it is touched.
  * JPEGs and DNGs keep their metadata inside the file, so a sidecar means
    nothing to Lightroom for those. They are skipped and counted.
  * Lightroom's Pick and Reject *flags* live in its catalogue and are not read
    from XMP by any version. The nearest thing a file can carry is a rating of
    -1, which Bridge and Camera Raw show as "Reject"; Lightroom ignores it. So
    a reject is marked with a colour, and optionally that -1 as well.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .scan import RAW_EXTS

NS_XMP = "http://ns.adobe.com/xap/1.0/"
# The label is free text, and Lightroom colours it only if the text matches
# the name in its own label set - which is translated with the program.
LABELS = {
    "en": {"red": "Red", "yellow": "Yellow", "green": "Green", "blue": "Blue",
           "purple": "Purple"},
    "es": {"red": "Rojo", "yellow": "Amarillo", "green": "Verde", "blue": "Azul",
           "purple": "Púrpura"},
}
BACKUP_DIR = "xmp-backup"

_PACKET = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Agape Aftershot">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:xmp="' + NS_XMP + '"{fields}/>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
    '<?xpacket end="w"?>\n'
)


def label_text(colour, lang="en") -> str | None:
    """'green' -> 'Green' / 'Verde'. Anything not a known colour is used as typed."""
    if not colour or str(colour).lower() in ("none", "-"):
        return None
    table = LABELS.get(lang, LABELS["en"])
    return table.get(str(colour).lower(), str(colour))


def sidecar_for(raw: Path) -> Path:
    """The sidecar beside a RAW - the one already there, whatever its case."""
    raw = Path(raw)
    for ext in (".xmp", ".XMP", ".Xmp"):
        p = raw.with_suffix(ext)
        if p.exists():
            return p
    return raw.with_suffix(".xmp")


def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _set_field(text: str, name: str, value) -> str | None:
    """Set xmp:<name> in an existing packet. None if it is not a packet we know."""
    v = _esc(value)
    attr = re.compile(r'(\bxmp:%s\s*=\s*)(["\'])(.*?)\2' % name, re.S)
    if attr.search(text):
        return attr.sub(lambda m: f'{m.group(1)}"{v}"', text, count=1)
    elem = re.compile(r'(<xmp:%s\s*>)(.*?)(</xmp:%s\s*>)' % (name, name), re.S)
    if elem.search(text):
        return elem.sub(lambda m: m.group(1) + v + m.group(3), text, count=1)
    m = re.search(r'<rdf:Description\b', text)
    if not m:
        return None
    add = f' xmp:{name}="{v}"'
    if "xmlns:xmp=" not in text:
        add = f' xmlns:xmp="{NS_XMP}"' + add
    return text[:m.end()] + add + text[m.end():]


def _parses(text: str) -> bool:
    import xml.etree.ElementTree as ET

    body = re.sub(r"<\?xpacket.*?\?>", "", text, flags=re.S).strip()
    try:
        ET.fromstring(body)
        return True
    except ET.ParseError:
        return False


def read(raw: Path) -> dict:
    """What the sidecar beside this RAW says now: {'rating': int|None, 'label': str|None}."""
    p = sidecar_for(raw)
    out = {"rating": None, "label": None}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for key, name in (("rating", "Rating"), ("label", "Label")):
        m = (re.search(r'\bxmp:%s\s*=\s*(["\'])(.*?)\1' % name, text, re.S)
             or re.search(r'<xmp:%s\s*>()(.*?)</xmp:%s\s*>' % (name, name), text, re.S))
        if m:
            out[key] = m.group(2)
    try:
        out["rating"] = None if out["rating"] is None else int(float(out["rating"]))
    except ValueError:
        out["rating"] = None
    return out


def mark(raw: Path, rating=None, label=None, apply=False, backup_root=None) -> str:
    """Give one frame a rating and/or a label. -> what happened, in one word.

    'new'      a sidecar was (or would be) created
    'changed'  an existing sidecar had its rating / label set
    'same'     it already said exactly this
    'not-raw'  a JPEG or DNG - a sidecar would mean nothing to Lightroom
    'strange'  a sidecar is there but is not XMP this can safely edit; left alone
    'failed'   the disk refused
    """
    raw = Path(raw)
    ext = raw.suffix.lower()
    if ext not in RAW_EXTS or ext == ".dng":
        return "not-raw"
    if rating is None and label is None:
        return "same"
    if rating is not None:
        rating = max(-1, min(5, int(rating)))
    side = sidecar_for(raw)

    if not side.exists():
        fields = ""
        if rating is not None:
            fields += f'\n    xmp:Rating="{rating}"'
        if label is not None:
            fields += f'\n    xmp:Label="{_esc(label)}"'
        if apply:
            try:
                tmp = side.with_name(side.name + ".tmp")
                tmp.write_text(_PACKET.format(fields=fields), encoding="utf-8")
                tmp.replace(side)
            except OSError:
                return "failed"
        return "new"

    try:
        old = side.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "strange"
    now = read(raw)
    if ((rating is None or now["rating"] == rating)
            and (label is None or now["label"] == label)):
        return "same"
    new = old
    if rating is not None:
        new = _set_field(new, "Rating", rating)
    if new is not None and label is not None:
        new = _set_field(new, "Label", label)
    if new is None or (_parses(old) and not _parses(new)):
        return "strange"
    if apply:
        try:
            # The sidecar as it was, kept once: the first copy is the original,
            # and a later run must not replace it with an already-edited one.
            bdir = Path(backup_root) if backup_root else raw.parent / "_cullkit" / BACKUP_DIR
            bdir.mkdir(parents=True, exist_ok=True)
            keep = bdir / side.name
            if not keep.exists():
                shutil.copy2(side, keep)
            tmp = side.with_name(side.name + ".tmp")
            tmp.write_text(new, encoding="utf-8")
            tmp.replace(side)
        except OSError:
            return "failed"
    return "changed"


def mark_many(items, apply=False, backup_root=None) -> dict:
    """items: [(raw path, rating|None, label|None)] -> {outcome: count}."""
    from .stop import checkpoint

    tally = {}
    for raw, rating, label in items:
        checkpoint()
        got = mark(raw, rating, label, apply=apply, backup_root=backup_root)
        tally[got] = tally.get(got, 0) + 1
    return tally


def stars(n) -> str:
    n = int(n or 0)
    # Plain words: a Windows console cannot print a star.
    return "rated -1 (reject)" if n < 0 else (f"{n} star{'' if n == 1 else 's'}" if n else "no stars")
