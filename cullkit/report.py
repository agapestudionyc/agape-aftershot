"""Console output and a self-contained HTML contact sheet."""
from __future__ import annotations

import base64
import html
import time
from pathlib import Path


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def compact_ranges(numbers):
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'"""
    nums = sorted(set(numbers))
    if not nums:
        return ""
    out, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


def print_match_report(matches, echo=print, show_ok=False):
    ok = [m for m in matches if m.status == "ok"]
    missing = [m for m in matches if m.status == "missing"]
    ambiguous = [m for m in matches if m.status == "ambiguous"]

    echo(f"  matched    {len(ok)}")
    if ambiguous:
        echo(f"  ambiguous  {len(ambiguous)}  (same frame number on more than one file)")
        for m in ambiguous[:20]:
            echo(f"      {m.number}: {m.note}")
        if len(ambiguous) > 20:
            echo(f"      ... and {len(ambiguous) - 20} more")
    if missing:
        echo(f"  MISSING    {len(missing)}  -> {compact_ranges(m.number for m in missing)}")
        echo("             (in your list, not in the folder - check the source drive)")
    if show_ok:
        for m in ok:
            echo(f"      {m.number}: {m.stacks[0].base}")
    return {"ok": len(ok), "missing": len(missing), "ambiguous": len(ambiguous)}


def print_plan(plan, echo=print, limit=15):
    counts = plan.summary()
    echo(f"  {plan.mode.upper()} -> {plan.dest}")
    pending = [o for o in plan.ops if o.status == "pending"]
    for op in pending[:limit]:
        note = f"   ({op.note})" if op.note else ""
        echo(f"      {op.src.name}  ->  {op.dst.name}{note}")
    if len(pending) > limit:
        echo(f"      ... and {len(pending) - limit} more files")
    parts = [f"{n} {k}" for k, n in sorted(counts.items())]
    echo(f"  {len(pending)} files, {human(plan.bytes_total)}   [{', '.join(parts)}]")


_CSS = """
:root{--bg:#fff;--fg:#16161a;--muted:#6b7280;--line:#e5e7eb;--card:#fafafa;}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f11;--fg:#ececf1;--muted:#9ca3af;
--line:#2a2a30;--card:#17171b;}}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--fg);
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:24px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
figure{margin:0;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden}
figure img{display:block;width:100%;height:150px;object-fit:cover;background:#000}
figcaption{padding:7px 9px;font-size:12px;font-family:ui-monospace,Consolas,monospace;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{display:inline-block;font-size:11px;color:var(--muted);margin-left:6px}
h2{font-size:16px;margin:30px 0 12px;padding-top:14px;border-top:1px solid var(--line)}
"""


def contact_sheet(entries, out_path, title="Culling contact sheet", subtitle="",
                  thumb=320):
    """entries: list of (section, label, preview_path). Fully self-contained HTML."""
    from PIL import Image
    import io

    sections = {}
    for section, label, path in entries:
        sections.setdefault(section, []).append((label, path))

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title><style>{_CSS}</style>",
        f"<h1>{html.escape(title)}</h1>",
        f"<div class='sub'>{html.escape(subtitle or time.strftime('%Y-%m-%d %H:%M'))}</div>",
    ]

    for section, items in sections.items():
        if section:
            parts.append(f"<h2>{html.escape(section)} <span class='tag'>"
                         f"{len(items)} frames</span></h2>")
        parts.append("<div class='grid'>")
        for label, path in items:
            src = ""
            try:
                with Image.open(path) as im:
                    im = im.convert("RGB")
                    im.thumbnail((thumb, thumb), Image.LANCZOS)
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=72)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                src = f"data:image/jpeg;base64,{b64}"
            except Exception:
                pass
            parts.append(
                f"<figure><img loading='lazy' src='{src}' alt=''>"
                f"<figcaption>{html.escape(str(label))}</figcaption></figure>"
            )
        parts.append("</div>")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
