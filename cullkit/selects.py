"""Parse a photographer's selects list into frame numbers.

Accepts the messy real-world stuff: bare numbers, ranges, full filenames,
Spanish or English range words, notes and headers mixed in.

    1234, 1250-1260, 1299
    DSC_1234.NEF
    _MG_5678
    0234 0235 0240
    4501 a 4510
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Extensions we strip off a token before hunting for the frame number.
_EXT = re.compile(
    r"\.(nef|cr2|cr3|arw|dng|raf|orf|rw2|srw|pef|iiq|3fr|jpe?g|heic|heif"
    r"|tiff?|png|xmp|mov|mp4)$",
    re.I,
)
# A token's frame number is its LAST run of digits.
_LAST_DIGITS = re.compile(r"(\d+)(?!.*\d)", re.S)
_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z_\-]*?)_?\d+$")

# Lines that are obviously prose/headers, not selects.
_NOISE = re.compile(r"^\s*(#|//|selects?\b|picks?\b|favoritos?\b|notas?\b)", re.I)

DEFAULT_MAX_SPAN = 2000


@dataclass
class Entry:
    """One parsed token from the list."""

    raw: str
    kind: str  # "single" | "range"
    numbers: list[int]
    prefix: str | None = None
    pad: int = 0  # zero-pad width as written, 0 if unpadded
    digits: str = ""  # the digit run exactly as typed, for loose matching


@dataclass
class Selects:
    entries: list[Entry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)

    @property
    def numbers(self) -> list[int]:
        """Every requested frame number, de-duplicated, in list order."""
        seen: dict[int, None] = {}
        for e in self.entries:
            for n in e.numbers:
                seen.setdefault(n, None)
        return list(seen)

    def digits_for(self, number: int) -> str:
        """The digit run as typed, e.g. '0007' rather than 7."""
        for e in self.entries:
            if number in e.numbers and e.digits:
                return e.digits
        return str(number)

    def prefix_for(self, number: int) -> str | None:
        """The filename prefix the user wrote for this number, if any."""
        for e in self.entries:
            if number in e.numbers and e.prefix:
                return e.prefix
        return None

    def duplicates(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for e in self.entries:
            for n in e.numbers:
                counts[n] = counts.get(n, 0) + 1
        return {n: c for n, c in counts.items() if c > 1}


def _frame_of(token: str) -> tuple | None:
    """-> (number, prefix, pad_width) or None if the token holds no digits."""
    stem = _EXT.sub("", token.strip())
    stem = stem.strip("([{<>}])'\"`.,;:")
    if not stem:
        return None
    m = _LAST_DIGITS.search(stem)
    if not m:
        return None
    digits = m.group(1)
    pad = len(digits) if digits.startswith("0") else 0
    pm = _PREFIX.match(stem)
    prefix = pm.group(1) if pm else None
    return int(digits), prefix, pad, digits


def parse_text(text: str, max_span: int = DEFAULT_MAX_SPAN) -> Selects:
    out = Selects()

    # Protect ISO dates so 2026-08-19 is never read as a range.
    dates: list[str] = []

    def _stash(m: re.Match) -> str:
        dates.append(m.group(0))
        return f"\x00DATE{len(dates) - 1}\x00"

    text = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", _stash, text)

    lines = []
    for line in text.splitlines():
        if _NOISE.match(line):
            # Keep any numbers that follow a header word on the same line.
            line = _NOISE.sub(" ", line)
        lines.append(line)
    text = "\n".join(lines)

    # Normalise every way a range can be written down to "lo-hi".
    text = re.sub(r"(?<=\d)\s*[-\u2010-\u2015]\s*(?=\d)", "-", text)
    text = re.sub(
        r"(?<=\d)\s+(?:to|thru|through|a|al|hasta|hast)\s+(?=\d)", "-", text, flags=re.I
    )

    for token in re.split(r"[,\s;|/\\]+", text):
        token = token.strip()
        if not token or token.startswith("\x00DATE"):
            continue

        if "-" in token and not token.startswith("-"):
            lo_s, _, hi_s = token.partition("-")
            lo, hi = _frame_of(lo_s), _frame_of(hi_s)
            if lo and hi:
                a, b = lo[0], hi[0]
                if a > b:
                    out.warnings.append(f"'{token}': range runs backwards, flipped it")
                    a, b = b, a
                span = b - a + 1
                if span > max_span:
                    out.warnings.append(
                        f"'{token}': spans {span} frames (limit {max_span}) - skipped. "
                        f"Re-run with --max-span if that is really what you meant."
                    )
                    continue
                out.entries.append(
                    Entry(token, "range", list(range(a, b + 1)), lo[1], lo[2], lo[3])
                )
                continue
            # Not a real range (e.g. "DSC_1234-edit"); fall through to single.

        f = _frame_of(token)
        if f is None:
            out.ignored.append(token)
            continue
        out.entries.append(Entry(token, "single", [f[0]], f[1], f[2], f[3]))

    dupes = out.duplicates()
    if dupes:
        preview = ", ".join(str(n) for n in sorted(dupes)[:8])
        more = "" if len(dupes) <= 8 else f" (+{len(dupes) - 8} more)"
        out.warnings.append(f"listed more than once: {preview}{more}")
    return out


def parse_file(path, max_span: int = DEFAULT_MAX_SPAN) -> Selects:
    import pathlib

    raw = pathlib.Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return parse_text(raw.decode(enc), max_span=max_span)
        except UnicodeDecodeError:
            continue
    return parse_text(raw.decode("utf-8", "replace"), max_span=max_span)
