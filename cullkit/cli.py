"""cullkit command line.

    cull scan    <folder>                      what is in this folder
    cull select  <folder> --list picks.txt     separate the selects
    cull preview <folder>                      embedded JPEGs out of the RAWs
    cull people  <folder>                      group the shoot by person
    cull name    <folder> person_01 "Sarah"    teach it a name, once
    cull sheet   <folder>                      HTML contact sheet
    cull undo    <journal.json>                reverse a run
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import actions, previews, report, scan, selects
from .stop import checkpoint

CACHE = "_cullkit"


def echo(msg=""):
    print(msg, flush=True)


def rule(title):
    echo()
    echo(title)
    echo("-" * max(12, len(title)))


def _index(args):
    idx = scan.scan(args.folder, recursive=getattr(args, "recursive", False))
    if not idx.stacks:
        echo(f"No photos found in {args.folder}")
        if idx.skipped:
            echo(f"({len(idx.skipped)} files there, none with a known photo extension)")
        sys.exit(1)
    return idx


def _previews_for(idx, stacks, args, label="previews"):
    """Extract (and cache) previews for these stacks. Returns {base: path}."""
    cache = Path(args.cache) if getattr(args, "cache", None) else previews.cache_dir_for(idx.root)
    srcs = [s.primary for s in stacks if s.primary]
    rule(f"{label}: {len(srcs)} frames -> {cache}")

    state = {"n": 0, "fail": 0}
    # Eight threads report into this. Two of them landing together lose a
    # count, and losing the wrong one loses the final line - the only one that
    # says the pass is over. A lock costs nothing next to decoding a RAW.
    counting = threading.Lock()

    def done(res):
        with counting:
            state["n"] += 1
            if not res.ok:
                state["fail"] += 1
            n = state["n"]
        if n % 25 == 0 or n == len(srcs):
            echo(f"  {n}/{len(srcs)}")

    results = previews.extract_many(
        srcs, cache, long_side=args.long_side,
        workers=args.workers, overwrite=getattr(args, "refresh", False),
        on_done=done,
    )
    good = {r.source.stem: r.out for r in results if r.ok}
    fails = [r for r in results if not r.ok]
    if fails:
        echo(f"  {len(fails)} could not be read:")
        for r in fails[:8]:
            echo(f"      {r.source.name}: {r.error}")
        if len(fails) > 8:
            echo(f"      ... and {len(fails) - 8} more")
    sizes = [r.embedded for r in results if r.ok and r.embedded]
    if sizes:
        big = max(sizes, key=lambda wh: wh[0] * wh[1])
        echo(f"  largest embedded preview found: {big[0]}x{big[1]} px")
    if good:
        # Note where the cache went, so clearing space later is exact rather
        # than a hopeful walk over the whole drive.
        try:
            from . import ledger as L
            L.record_cache(idx.root)
        except Exception:
            pass
    return good, cache


def _clean_previews(root, keep_bases=(), quiet=False):
    """Delete cached preview JPEGs, keeping the ones still worth having.

    Previews are the only thing in _cullkit that takes real space - hundreds of
    megabytes per shoot. Everything else is a few kilobytes of JSON and HTML,
    and one of those is the undo journal, so the cache folder is never removed
    wholesale.
    """
    cache = previews.cache_dir_for(root)
    if not cache.is_dir():
        return 0, 0
    keep = {str(b).lower() for b in keep_bases}
    n, freed = 0, 0
    for f in cache.glob("*.jpg"):
        if f.stem.lower() in keep:
            continue
        try:
            size = f.stat().st_size
            f.unlink()
            n += 1
            freed += size
        except OSError:
            pass
    try:
        if not any(cache.iterdir()):
            cache.rmdir()
    except OSError:
        pass
    if n and not quiet:
        kept = f", kept {len(keep)} you still need to look at" if keep else ""
        echo(f"  tidied up: {n} previews removed, {report.human(freed)} freed{kept}")
    return n, freed


# -------------------------------------------------------------------------- clean
def cmd_clean(args):
    from . import ledger as L

    if not args.folder:
        # No folder given: clear everywhere the program remembers working.
        known = L.caches(only_existing=True)
        stale = [c for c in L.caches(only_existing=False) if c not in known]
        if stale:
            L.forget_caches(stale)
            echo(f"  ({len(stale)} remembered folders no longer have a cache, forgotten)")
        if not known:
            # First run of this, or a ledger from before it existed: look once
            # through the folders you configured and adopt what is already there.
            cfg = L.load_config()
            roots = [r for r in (cfg.get("work_root"), cfg.get("archive_root")) if r]
            found = []
            for r in roots:
                rp = Path(r)
                if rp.is_dir():
                    found += [p.parent for p in rp.rglob("_cullkit") if p.is_dir()]
            for p in found:
                L.record_cache(p)
            known = L.caches(only_existing=True)
            if known:
                echo(f"  first look: adopted {len(known)} folders that already "
                     f"had a cache")
        caches = [Path(c) / CACHE for c in known]
        if not caches:
            echo("  nothing cached anywhere the program has been")
            return 0
        rule(f"{len(caches)} folders on record hold a cache")
    else:
        root = Path(args.folder)
        if not root.is_dir():
            echo(f"  not a folder: {root}")
            return 1
        if args.recursive:
            caches = [p for p in root.rglob("_cullkit") if p.is_dir()]
        else:
            caches = [root / CACHE] if (root / CACHE).is_dir() else []
        if not caches:
            echo("  nothing cached here")
            return 0

    total_n, total_b, journals = 0, 0, 0
    rule("clearing cached previews")
    for cache in caches:
        shoot = cache.parent
        # Keep the previews of frames still awaiting your eye, unless --all.
        keep = []
        qf = cache / "quality.json"
        if qf.is_file() and not args.all:
            try:
                data = json.loads(qf.read_text(encoding="utf-8"))
                keep = [f["base"] for f in data.get("frames", [])
                        if f.get("tier") and f["tier"] != "keep"]
            except Exception:
                keep = []
        if args.dry_run:
            jpgs = [f for f in (cache / "previews").glob("*.jpg")
                    if f.stem.lower() not in {k.lower() for k in keep}]
            n = len(jpgs)
            b = sum(f.stat().st_size for f in jpgs)
        else:
            n, b = _clean_previews(shoot, keep, quiet=True)
        journals += len(list(cache.glob("undo-*.json")))
        if keep:
            echo(f"  {shoot.name}: keeping {len(keep)} flagged previews for review")
        if n:
            total_n += n
            total_b += b
            echo(f"  {shoot.name}: {n} previews, {report.human(b)}")
        if args.all:
            # NOT undo-*.json. They match `*.json` and were being deleted here,
            # while this command's own help, the button and the confirmation all
            # promised the undo history was never touched. A journal is a few KB
            # and it is the only thing that can put a moved photograph back.
            for f in list(cache.glob("*.html")) + list(cache.glob("*.json")):
                if f.name.startswith("undo-"):
                    continue
                try:
                    f.unlink()
                except OSError:
                    pass

    echo("")
    if args.dry_run:
        echo(f"  {total_n} previews cached, {report.human(total_b)} could be freed")
        echo("  NOTHING WAS DELETED. This was only a look.")
        echo("  Free it with the same command without --dry-run, when you are")
        echo("  finished reviewing this work.")
    else:
        echo(f"  {total_n} previews removed, {report.human(total_b)} freed")
        echo("  Previews rebuild themselves next time you need them.")
    if journals:
        echo(f"  {journals} undo journals left alone - they are what lets you")
        echo("  reverse a move, and they are only a few KB each")
    return 0


# --------------------------------------------------------------------------- scan
def cmd_scan(args):
    idx = _index(args)
    c = idx.counts()
    rule(f"{idx.root}")
    echo(f"  {c['stacks']} frames   raw {c['raw']}  jpg {c['jpg']}  "
         f"sidecar {c['sidecar']}  video {c['video']}")
    frames = idx.frames
    if frames:
        echo(f"  frame numbers: {frames[0]} - {frames[-1]}  "
             f"({len(frames)} distinct)")
        gaps = [n for n in range(frames[0], frames[-1] + 1) if n not in idx.by_frame]
        if gaps and len(gaps) <= 40:
            echo(f"  gaps: {report.compact_ranges(gaps)}")
        elif gaps:
            echo(f"  gaps: {len(gaps)} numbers missing in the sequence")
    dupes = {n: v for n, v in idx.by_frame.items() if len(v) > 1}
    if dupes:
        echo(f"  {len(dupes)} frame numbers appear on more than one file "
             f"(two bodies?): {report.compact_ranges(dupes)}")
    total = sum(s.size for s in idx.stacks)
    echo(f"  {report.human(total)} on disk")
    return 0


# ------------------------------------------------------------------------- select
def cmd_select(args):
    idx = _index(args)

    numbers = getattr(args, "numbers", None)
    if numbers:
        sel = selects.parse_text(" ".join(numbers), max_span=args.max_span)
        source = "the numbers you gave"
    elif args.list:
        sel = selects.parse_file(args.list, max_span=args.max_span)
        source = str(args.list)
    else:
        echo("Paste or pipe the numbers, then Ctrl-Z + Enter (Windows):")
        sel = selects.parse_text(sys.stdin.read(), max_span=args.max_span)
        source = "stdin"

    rule(f"selects list ({source})")
    echo(f"  {len(sel.entries)} entries -> {len(sel.numbers)} frame numbers")
    echo(f"  {report.compact_ranges(sel.numbers)}")
    for w in sel.warnings:
        echo(f"  ! {w}")
    if sel.ignored:
        shown = ", ".join(sel.ignored[:6])
        echo(f"  ignored (no digits): {shown}"
             + (f" ... +{len(sel.ignored) - 6}" if len(sel.ignored) > 6 else ""))
    if not sel.numbers:
        echo("\nNothing to do - no frame numbers in that list.")
        return 1

    rule("matching against the folder")
    matches = scan.resolve(idx, sel)
    stats = report.print_match_report(matches, echo, show_ok=args.verbose)

    if stats["missing"] and args.strict:
        echo("\nStopped: --strict and some frames are missing.")
        return 2

    picked, seen = [], set()
    for m in matches:
        for s in m.stacks:
            key = (str(s.dirpath), s.base)
            if key not in seen:
                seen.add(key)
                picked.append(s)
    if not picked:
        echo("\nNothing matched. Nothing to do.")
        return 1

    dest = Path(args.dest) if args.dest else idx.root / args.dest_name
    mode = actions.LINK if args.link else (actions.COPY if args.copy else actions.MOVE)
    plan = actions.build_plan(picked, dest, mode=mode, on_conflict=args.on_conflict)
    args._plan = plan          # `work` reads it to know every pick really left

    rule("plan")
    report.print_plan(plan, echo)

    if not args.apply:
        echo()
        echo("  DRY RUN - nothing was touched. Re-run with --apply to do it.")
        return 0

    rule("applying")
    journal = actions.execute(plan, apply=True, verify=args.verify)
    counts = plan.summary()
    echo(f"  {counts.get('done', 0)} files {mode}d, "
         f"{counts.get('skipped', 0)} skipped, {counts.get('failed', 0)} failed")
    for op in plan.ops:
        if op.status == "failed":
            echo(f"      FAILED {op.src.name}: {op.note}")
    if journal:
        echo(f"  undo journal: {journal}")
        echo(f"  reverse it with:  cull undo \"{journal}\" --apply")

    if args.sheet:
        prev, _ = _previews_for(idx, picked, args, "contact sheet previews")
        entries = [("selects", s.base, prev[s.primary.stem])
                   for s in picked if s.primary and s.primary.stem in prev]
        out = report.contact_sheet(
            entries, dest / CACHE / "selects.html",
            title=f"Selects - {idx.root.name}",
            subtitle=f"{len(entries)} frames from {source}",
        )
        echo(f"  contact sheet: {out}")
    return 0


# ------------------------------------------------------------------------ preview
def cmd_preview(args):
    idx = _index(args)
    stacks = idx.stacks
    if args.frames:
        sel = selects.parse_text(" ".join(args.frames), max_span=args.max_span)
        wanted = set(sel.numbers)
        stacks = [s for s in idx.stacks if s.frame in wanted]
        if not stacks:
            echo("None of those frame numbers are in this folder.")
            return 1
    if args.limit:
        stacks = stacks[: args.limit]

    good, cache = _previews_for(idx, stacks, args)
    echo(f"\n  {len(good)} previews in {cache}")
    if args.sheet:
        entries = [("", s.base, good[s.primary.stem])
                   for s in stacks if s.primary and s.primary.stem in good]
        out = report.contact_sheet(entries, idx.root / CACHE / "contact-sheet.html",
                                   title=f"Contact sheet - {idx.root.name}")
        echo(f"  contact sheet: {out}")
    return 0


# ------------------------------------------------------------------------- people
def cmd_people(args):
    from . import faces as F

    idx = _index(args)
    stacks = idx.stacks[: args.limit] if args.limit else idx.stacks
    prev, cache = _previews_for(idx, stacks, args, "previews for face detection")
    if not prev:
        echo("No previews could be extracted, so there is nothing to look at.")
        return 1

    rule("faces")
    backend = F.get_backend(prefer=args.backend, on_msg=lambda m: echo(f"  {m}"))
    echo(f"  backend: {backend.name}")

    state = {"n": 0, "f": 0}

    def step(path, n):
        state["n"] += 1
        state["f"] += n
        if state["n"] % 25 == 0 or state["n"] == len(prev):
            echo(f"  {state['n']}/{len(prev)} images, {state['f']} faces")

    found = F.analyse_previews(
        list(prev.values()), backend,
        min_face_frac=args.min_face, min_score=args.min_score, on_step=step,
    )
    echo(f"  {len(found)} faces in {len(prev)} images")
    if not found:
        echo("  No faces detected. Try --min-face 0 --min-score 0.3")
        return 1

    groups = F.cluster(found, threshold=args.threshold, backend_name=backend.name)
    groups = F.name_from_library(groups, backend.name)
    groups = [g for g in groups if len(g.images) >= args.min_photos]

    people_dir = idx.root / CACHE / "people"
    crops = F.save_face_crops(groups, people_dir / "_faces")

    rule(f"{len(groups)} people")
    for g in groups:
        echo(f"  {g.name:<16} {len(g.images):>4} photos   "
             f"{len(g.faces):>4} faces   face: {crops.get(g.name, '-')}")

    stack_by_stem = {s.primary.stem: s for s in stacks if s.primary}
    manifest = {
        "root": str(idx.root), "backend": backend.name,
        "threshold": args.threshold or F.THRESHOLDS.get(backend.name),
        "people": {
            g.name: {
                "cluster": g.cluster,
                "photos": sorted(
                    stack_by_stem[p.stem].base for p in g.images if p.stem in stack_by_stem
                ),
                "centroid": [float(x) for x in g.centroid()],
            }
            for g in groups
        },
    }
    people_dir.mkdir(parents=True, exist_ok=True)
    mpath = people_dir / "people.json"
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    echo(f"\n  manifest: {mpath}")

    if args.sheet:
        entries = []
        for g in groups:
            for p in g.images:
                s = stack_by_stem.get(p.stem)
                entries.append((g.name, s.base if s else p.stem, p))
        out = report.contact_sheet(entries, people_dir / "people.html",
                                   title=f"People - {idx.root.name}",
                                   subtitle=f"{len(groups)} people, {backend.name}")
        echo(f"  contact sheet: {out}")

    dest_root = Path(args.dest) if args.dest else idx.root / "By person"
    mode = actions.COPY if args.copy else actions.LINK
    rule("plan")
    journals = []
    for g in groups:
        picked = [stack_by_stem[p.stem] for p in g.images if p.stem in stack_by_stem]
        if not picked:
            continue
        plan = actions.build_plan(picked, dest_root / g.name, mode=mode,
                                  on_conflict=args.on_conflict)
        echo(f"  {g.name}: {len([o for o in plan.ops if o.status == 'pending'])} files"
             f" -> {plan.dest}")
        if args.apply:
            j = actions.execute(plan, apply=True)
            if j:
                journals.append(j)

    if not args.apply:
        echo()
        echo(f"  DRY RUN - nothing was touched. Re-run with --apply to build "
             f"the folders ({mode}).")
    else:
        echo(f"\n  done. {len(journals)} undo journals under {dest_root}")
    echo()
    echo("  Put real names on them, once, and they stick across future shoots:")
    for g in groups[:3]:
        echo(f"      cull name \"{idx.root}\" {g.name} \"Their Name\"")
    return 0


# ------------------------------------------------------------------------- drives
def cmd_drives(args):
    from . import importer as I

    vols = I.list_volumes(count_media=not args.fast)
    rule("drives")
    for v in vols:
        tag = "  <- looks like a camera card" if (v.dcim and v.kind == "removable") else ""
        media = f"{v.media:>6} photos" if not args.fast else "           "
        echo(f"  {v.tag:<4} {v.label or '(no label)':<18} {v.kind:<10} "
             f"{report.human(v.used):>9} used of {report.human(v.total):<9} "
             f"{media}{tag}")
    if not vols:
        echo("  none found")
    return 0


# ---------------------------------------------------------------------------- app
def cmd_app(args):
    from . import server

    return server.serve(port=args.port, open_browser=not args.no_open)


# ------------------------------------------------------------------------- browse
def cmd_browse(args):
    """List a folder's subfolders with photo counts, numbered for picking."""
    from . import importer as I

    if not args.path:
        return cmd_drives(argparse.Namespace(fast=False))

    root = Path(args.path)
    if not root.is_dir():
        echo(f"  not a folder: {root}")
        return 1

    subs = []
    try:
        for p in sorted(root.iterdir()):
            if p.is_dir() and not p.name.startswith("$"):
                subs.append(p)
    except PermissionError:
        echo("  permission denied")
        return 1

    here = [p for p in root.iterdir()
            if p.is_file() and p.suffix.lower() in I.MEDIA_EXTS]
    rule(str(root))
    if here:
        echo(f"  {len(here)} photos sitting directly in this folder")
    if not subs:
        echo("  (no subfolders)")
        return 0

    echo(f"  {len(subs)} subfolders:")
    for i, p in enumerate(subs, 1):
        n = I.count_photos(p, cap=args.cap) if not args.fast else None
        kids = sum(1 for c in p.iterdir() if c.is_dir()) if p.is_dir() else 0
        bits = []
        if n is not None:
            bits.append(f"{n:>5} photos" + ("+" if n >= args.cap else ""))
        if kids:
            bits.append(f"{kids} subfolders")
        echo(f"   {i:>3}. {p.name:<38} {'   '.join(bits)}")
    return 0


# ------------------------------------------------------------------------- import
def cmd_import(args):
    from . import importer as I
    from . import ledger as L

    src = Path(args.source)
    if not src.exists():
        echo(f"  source not found: {src}")
        return 1

    cfg = L.load_config()
    root = args.dest or cfg.get("archive_root")
    if not root:
        echo("  No destination. Pass --dest, or set one once with:")
        echo('      cull config --archive-root "E:\\Archivo"')
        return 1

    # The stage is named by the line above its rule of dashes, and that name
    # goes to the phone. "reading E:\DCIM" makes a poor sentence there and a
    # worse one once a drive letter's colon has been cut off it. The path is on
    # the next line, where it belongs.
    rule("reading the card")
    echo(f"  {src}")
    files = I.find_media(src)
    if not files:
        echo("  no photos or video found there")
        return 1
    total = sum(f.stat().st_size for f in files)
    kinds = {}
    for f in files:
        kinds[f.suffix.upper()] = kinds.get(f.suffix.upper(), 0) + 1
    echo(f"  {len(files)} files, {report.human(total)}   "
         + "  ".join(f"{k} {n}" for k, n in sorted(kinds.items())))

    name = args.name or datetime.now().strftime("%Y-%m-%d %H%M") + " import"
    dest = Path(root) / name
    echo(f"  -> {dest}")

    if not args.apply:
        echo()
        echo("  DRY RUN - nothing copied. Add --apply to do it.")
        echo("  The card is only ever read from, never modified.")
        return 0

    rule("copying and verifying")
    state = {"n": 0}

    def step(i, n, srcf, what):
        state["n"] = i
        if (i % 20 == 0 or i == n or what.startswith("FAILED")
                or what.startswith("copied on try")):
            echo(f"  {i}/{n}  {srcf.name}  {what}")

    res = I.copy_verified(files, dest, verify=not args.no_verify, on_step=step)
    echo("")
    echo(f"  copied {res.copied}, already there {res.skipped}, "
         f"failed {len(res.failed)}   ({report.human(res.bytes_copied)})")
    for f, why in res.failed[:10]:
        echo(f"      {f.name}: {why}")

    L.record_import(src, dest, res.copied, res.skipped, len(res.failed),
                    res.bytes_copied)

    if res.ok:
        echo("")
        if not args.no_verify:
            echo("  Every file was checksummed against the card and matched.")
        echo(f"  The card at {src} is untouched and safe to reformat.")
        echo("  Format it in the camera, not in Windows - it is better for the card")
        echo("  and it is the one step this tool deliberately will not do for you.")
        echo("")
        echo(f"  Next:  cull shoots \"{dest}\"")
    else:
        echo("")
        echo("  Some files failed. Do NOT format the card until that is resolved.")
        return 2
    return 0


# ------------------------------------------------------------------------- status
def cmd_journal(args):
    """Read back what the program did, or throw the record away.

    Written for two readers. A person wants to know what happened last night,
    and gets one line each: when, what, and how it ended. A machine - or
    somebody pasting the output into a conversation to ask why - wants the
    entries themselves, and gets them with `--raw`.
    """
    from . import journal

    if args.clear:
        got = journal.clear()
        n = len(got["files"])
        print(f"cleared {n} journal file(s), {got['bytes'] / 1024:.0f} KB freed")
        print("the record starts again from now")
        return 0

    kind = args.kind
    rows = journal.read(limit=0, kind=kind, contains=args.grep)
    if args.errors:
        bad = ("error", "fail", "refused", "reject", "unreadable")
        rows = [r for r in rows
                if any(b in str(r.get("kind", "")) for b in bad)
                or r.get("rc") not in (None, 0)]
    rows = rows[-args.tail:] if args.tail else rows

    if args.raw:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return 0

    info = journal.summary()
    if not rows:
        print("nothing recorded yet" if not info["events"]
              else "nothing matches that")
        print(f"  {info['events']} entries in total, at {info['dir']}")
        return 0

    # Plain ASCII between the columns. A middot is prettier and arrives as a
    # question mark on the Windows console this actually runs on, which makes
    # the one place you go when something is wrong look like it is broken too.
    SEP = "  |  "

    def say(text):
        """Print without the console's encoding being able to stop it.

        The recorded text is not ours - it is whatever the app said at the
        time, em-dashes and all - and on a cp1252 console a stray one raises
        UnicodeEncodeError and takes the whole listing down with it.
        """
        # The typographic characters the app writes into its own sentences are
        # swapped every time, not only when encoding fails: a console can
        # report utf-8 and still paint an em-dash as a question mark, which is
        # what this one does. Accents and anything else in a folder name are
        # left alone and handed to the encoder. `--raw` is untouched by any of
        # this and keeps the exact text.
        for bad, plain in (("—", "-"), ("–", "-"), ("·", "|"),
                           ("→", "->"), ("✓", "ok"), ("…", "...")):
            text = text.replace(bad, plain)
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            text.encode(enc)
        except (UnicodeEncodeError, LookupError):
            text = text.encode(enc, "replace").decode(enc, "replace")
        print(text)

    for r in rows:
        kind = str(r.get("kind", ""))
        what = r.get("what") or r.get("body") or ""
        # A stage change is "which job" and "which stage", and printing only
        # the job made every phase line in a run look identical.
        if r.get("phase") and kind.endswith(".phase"):
            what = f"{what} - {r['phase']}" if what else r["phase"]
        bits = []
        if r.get("rc") is not None:
            bits.append("ok" if r["rc"] == 0 else f"rc {r['rc']}")
        if r.get("secs") is not None:
            bits.append(f"{r['secs']}s")
        if r.get("stopped"):
            bits.append("you stopped it")
        if r.get("error"):
            bits.append(str(r["error"])[:120])
        if r.get("where"):
            bits.append(str(r["where"]))
        if r.get("folder"):
            bits.append(str(r["folder"]))
        if r.get("why"):
            bits.append(str(r["why"]))
        if r.get("blocked_by_what"):
            bits.append("busy with " + str(r["blocked_by_what"]))
        tail = (SEP + SEP.join(bits)) if bits else ""
        say(f"{r.get('t', '')}  {kind:<18} {str(what)[:58]}{tail}")

    print()
    say(f"{len(rows)} shown of {info['events']} recorded{SEP}"
        f"{info['bytes'] / 1024:.0f} KB{SEP}{info['dir']}")
    if info.get("oldest"):
        say(f"the record starts {info['oldest']}")
    return 0


def cmd_status(args):
    from . import ledger as L

    cfg = L.load_config()
    rule("settings")
    echo(f"  archive root : {cfg.get('archive_root') or '(not set)'}")
    echo(f"  work root    : {cfg.get('work_root') or '(not set)'}")

    imports = L.load()["imports"][-5:]
    if imports:
        rule("recent imports")
        for im in reversed(imports):
            echo(f"  {im['when']}  {im['copied']:>4} files  "
                 f"{report.human(im.get('bytes', 0)):>9}  -> {im['dest']}")

    live = L.caches(only_existing=True)
    if live:
        rule(f"{len(live)} folders currently hold a cache")
        total = 0
        for c in live:
            jpgs = list((Path(c) / CACHE / "previews").glob("*.jpg"))
            size = sum(f.stat().st_size for f in jpgs)
            total += size
            if jpgs:
                echo(f"  {report.human(size):>9}  {len(jpgs):>4} previews  {c}")
        echo(f"  {report.human(total)} could be freed with:  cull clean")

    rows = L.shoots()
    rule(f"{len(rows)} shoot(s) on record")
    for s in rows:
        mark = " " if s["exists"] else "?"
        cull = s.get("cull")
        bits = f"{s.get('count', '?')} frames"
        if cull:
            bits += f"   keep {cull['keep']} / review {cull['review']} / reject {cull['reject']}"
        sel = s.get("selects")
        if sel:
            bits += f"   selects picked {sum(x['count'] for x in sel)}"
        echo(f" {mark} {s.get('name', '?')}")
        echo(f"     {bits}")
        echo(f"     {s['path']}" + ("" if s["exists"] else "   (folder not found)"))
    if not rows:
        echo("  nothing yet - run 'cull shoots' on a folder to register one")
    return 0


# --------------------------------------------------------------------------- work
def cmd_work(args):
    """Pull chosen frame numbers out of a shoot and into the working folder."""
    from . import ledger as L

    cfg = L.load_config()
    # A real path is never fuzzy-matched: ledger.find() does a substring
    # search, and a folder that already exists can look like a "match" for
    # some other registered shoot whose path merely contains it (a nested
    # duplicate, for instance) - silently scanning the wrong folder.
    if Path(args.shoot).is_dir():
        folder = Path(args.shoot)
    else:
        hits = L.find(args.shoot)
        if hits:
            folder = Path(hits[0]["path"])
            if len(hits) > 1:
                echo(f"  '{args.shoot}' matches {len(hits)} shoots:")
                for h in hits:
                    echo(f"      {h['name']}   {h['path']}")
                return 1
        else:
            folder = Path(args.shoot)
    if not folder.is_dir():
        echo(f"  no such shoot: {args.shoot}")
        echo("  'cull status' lists what is on record")
        return 1

    root = args.dest or cfg.get("work_root")
    if not root:
        echo("  No working folder set. Pass --dest, or set it once with:")
        echo('      cull config --work-root "E:\\_TRABAJO ACTUAL"')
        return 1
    # Matches the convention already on the drive: "Cruz Selects", "Joe Selects".
    dest = Path(root) / (args.into or f"{folder.name} Selects")

    ns = argparse.Namespace(
        folder=str(folder), recursive=False, verbose=args.verbose,
        list=args.list, numbers=args.numbers, dest=str(dest), dest_name="Selects",
        copy=args.copy, link=False, apply=args.apply, verify=False,
        strict=False, max_span=selects.DEFAULT_MAX_SPAN,
        on_conflict="rename", sheet=False, long_side=2048, workers=0,
        cache=None, refresh=False, limit=0,
    )
    rest = None
    if args.discard_rest:
        rest, why = _discard_target(folder, dest, args, cfg)
        if why:
            echo(f"  ! the rest stays where it is: {why}")
    rc = cmd_select(ns)
    if rc == 0 and args.apply:
        sel = (selects.parse_file(args.list) if args.list
               else selects.parse_text(" ".join(args.numbers or [])))
        L.record_selects(folder, sel.numbers, dest)
        echo(f"  recorded in the ledger: {len(sel.numbers)} selects from {folder.name}")
    if rc == 0 and (args.stars or args.label):
        _mark_picks(getattr(ns, "_plan", None), args)
    if rc == 0 and rest:
        rc = _discard_rest(folder, rest, getattr(ns, "_plan", None), args.apply)
    return rc


def _mark_picks(plan, args):
    """Stars and a colour on the picks, in sidecars beside them where they land.

    Done after the move and in the selection's own folder, so the shoot the
    picks came from is never written to, and Lightroom finds the marks the
    moment that folder is imported.
    """
    from . import xmp as X

    label = X.label_text(args.label, args.label_lang)
    rating = args.stars or None
    said = " and ".join(x for x in (X.stars(rating) if rating else "",
                                    f'the label "{label}"' if label else "") if x)
    rule("for Lightroom")
    if plan is None:
        return
    if not args.apply:
        n = sum(1 for op in plan.ops if op.status == "pending"
                and X.mark(op.src, rating, label) != "not-raw")
        echo(f"  then {n} RAW files get {said}, in .xmp sidecars beside them")
        return
    tally = X.mark_many([(op.dst, rating, label) for op in plan.ops
                         if op.status == "done"], apply=True)
    _xmp_report(tally, said)


def _xmp_report(tally, said=""):
    done = tally.get("new", 0) + tally.get("changed", 0)
    echo(f"  {done} sidecars written" + (f": {said}" if said else "") +
         (f"   ({tally['same']} already said so)" if tally.get("same") else ""))
    if tally.get("changed"):
        echo(f"  {tally['changed']} of those already existed: only the rating and label "
             "were changed,")
        echo("  and the originals are kept in _cullkit\\xmp-backup")
    if tally.get("not-raw"):
        echo(f"  {tally['not-raw']} JPEG / DNG files skipped - Lightroom reads those "
             "from inside the file")
    if tally.get("strange"):
        echo(f"  {tally['strange']} sidecars were not XMP this can safely edit, and "
             "were left alone")
    if tally.get("failed"):
        echo(f"  !! {tally['failed']} could not be written (is the drive full or read-only?)")


# --------------------------------------------------------------------------- xmp
def cmd_xmp(args):
    """Sift's verdicts as stars and colour labels, where Lightroom reads them."""
    from . import plat
    from . import quality as Q
    from . import xmp as X

    folder = Path(args.folder)
    if (Path(plat.mount_of(folder)) / "DCIM").is_dir():
        echo("  That folder is on a camera card, and nothing is ever written to a card.")
        echo("  Import it first, then mark the copy.")
        return 1
    idx = _index(args)
    saved = Q.load_scores(idx.root)
    if saved is None:
        echo("  This folder has not been sifted, so there are no verdicts to write.")
        echo(f"  Run:  cull quality \"{folder}\"")
        return 1
    if args.blur_ratio is None:
        from . import taste
        learned = taste.learned_blur_ratio()
        args.blur_ratio = learned["ratio"] if learned else taste.DEFAULT_RATIO
    scores, _ = Q.judge(saved["frames"], {
        "blur_ratio": args.blur_ratio, "eye_ratio": args.eye_ratio,
        "reject_ratio": args.reject_ratio, "eye_soft": args.eye_soft,
        "blown": args.blown, "black": args.black,
    }, Q.load_overrides(idx.root))
    tier = {s.base: s.tier for s in scores}
    lang = args.label_lang
    marks = {
        "keep": (args.stars or None, X.label_text(args.keep_label, lang)),
        "review": (None, X.label_text(args.review_label, lang)),
        "reject": (-1 if args.reject_rating else None, X.label_text(args.reject_label, lang)),
    }
    rule("for Lightroom")
    for t, name in (("keep", "selects"), ("review", "review"), ("reject", "rejects")):
        r, l = marks[t]
        n = sum(1 for st in idx.stacks if st.primary and tier.get(st.primary.stem) == t)
        what = ", ".join(x for x in (X.stars(r) if r is not None else "",
                                     f'label "{l}"' if l else "") if x) or "left as they are"
        echo(f"  {n:4d} {name:<8} {what}")
    items = []
    for st in idx.stacks:
        t = tier.get(st.primary.stem) if st.primary else None
        if t not in marks or marks[t] == (None, None):
            continue
        targets = st.raws or ([st.primary] if st.primary else [])
        items.extend((raw, marks[t][0], marks[t][1]) for raw in targets)
    tally = X.mark_many(items, apply=args.apply)
    echo("")
    if not args.apply:
        n = tally.get("new", 0) + tally.get("changed", 0)
        echo(f"  DRY RUN - nothing was written. {n} sidecars would be "
             f"({tally.get('new', 0)} new, {tally.get('changed', 0)} existing ones edited).")
        if tally.get("not-raw"):
            echo(f"  {tally['not-raw']} JPEG / DNG files would be skipped.")
        echo("  No photograph is opened or changed either way. Add --apply to write them.")
        return 0
    _xmp_report(tally)
    echo("  In Lightroom: import the folder, or select the photos and choose")
    echo("  Metadata > Read Metadata from Files.")
    return 1 if tally.get("failed") else 0


def _discard_root(folder, cfg):
    """`_DESCARTABLES` at the top of the shoot's own drive, unless set."""
    if cfg.get("discard_root"):
        return Path(cfg["discard_root"])
    from . import plat
    return plat.drive_top(folder) / "_DESCARTABLES"


def _discard_target(folder, dest, args, cfg):
    """-> (target folder, None) or (None, the reason it cannot go)."""
    folder = Path(folder).resolve()
    root = Path(args.discards) if args.discards else _discard_root(folder, cfg)
    if args.copy:
        return None, "the picks were copied, not moved, so they are still in it"
    try:
        Path(dest).resolve().relative_to(folder)
        return None, "the selection is going inside this shoot, it would go too"
    except ValueError:
        pass
    try:
        root.resolve().relative_to(folder)
        return None, "the discards folder is inside this shoot"
    except ValueError:
        pass
    if folder.parent == root.resolve():
        return None, f"it is already in {root}"
    # A rename on one drive is instant and cannot half-happen. Across drives
    # it would be a copy and a delete of a whole shoot, which is not this job.
    from . import plat
    if not plat.same_drive(folder, root):
        return None, f"{root} is on another drive"
    return actions._unique(root / folder.name), None


def _discard_rest(folder, target, plan, apply):
    """Move the shoot folder, now holding only what was not picked."""
    from . import ledger as L
    folder = Path(folder).resolve()
    left = sum(1 for p in folder.rglob("*")
               if p.is_file() and CACHE not in p.relative_to(folder).parts)
    rule("the rest")
    if not apply:
        echo(f"  then the folder with what you did not pick moves to the discards:")
        echo(f"      {folder}")
        echo(f"   -> {target}")
        return 0
    if plan is not None and any(op.status == "failed" for op in plan.ops):
        echo("  some picks did not move, so the folder stays where it is")
        echo("  - nothing of yours goes to the discards by accident.")
        return 1
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(folder, target)
    except OSError as exc:
        echo(f"  could not move the folder ({type(exc).__name__}: {exc})")
        echo("  your selection is already safe; the rest stays where it was.")
        echo("  If a window or program has that folder open, close it and")
        echo("  move it into the discards by hand.")
        return 1
    op = actions.Op(folder, target, actions.MOVE, status="done",
                    note="the rest, to the discards")
    jdir = target / CACHE
    jdir.mkdir(parents=True, exist_ok=True)
    journal = jdir / f"undo-{time.strftime('%Y%m%d-%H%M%S')}-discards.json"
    journal.write_text(json.dumps({
        "created": time.strftime("%Y-%m-%d %H:%M:%S"), "dest": str(target.parent),
        "mode": actions.MOVE, "ops": [op.as_json()]}, indent=2), encoding="utf-8")
    L.relocate(folder, target)
    echo(f"  {left} file{'' if left == 1 else 's'} you did not pick moved, with their folder, to")
    echo(f"      {target}")
    echo(f"  undo journal: {journal}")
    echo(f"  put it back with:  cull undo \"{journal}\" --apply")
    return 0


# ------------------------------------------------------------------------- config
def cmd_config(args):
    from . import ledger as L

    cfg = L.load_config()
    changed = False
    for key, val in (("archive_root", args.archive_root),
                     ("work_root", args.work_root)):
        if val:
            cfg[key] = str(Path(val))
            changed = True
    if args.gap:
        cfg["gap_minutes"] = args.gap
        changed = True
    if changed:
        L.save_config(cfg)
        echo("  saved")
    rule("settings")
    for k, v in cfg.items():
        echo(f"  {k:<16} {v}")
    echo(f"\n  stored in {L.CONFIG}")
    return 0


# ------------------------------------------------------------------------- shoots
def cmd_shoots(args):
    from . import sessions as S

    idx = _index(args)
    rule(f"reading capture times from {len(idx.stacks)} frames")
    pairs = S.read_times(idx.stacks,
                         on_progress=lambda i, n: echo(f"  {i}/{n}") if i % 200 == 0 or i == n else None)
    if not pairs:
        echo("  No readable capture times. Cannot group into shoots.")
        return 1
    src = {p[1].get("taken_from") for p in pairs}
    echo(f"  got times for {len(pairs)}/{len(idx.stacks)}  (from {', '.join(sorted(src))})")

    big = S.gaps_report(pairs)
    if big:
        rule("longest quiet periods")
        for d, a, b in big:
            mins = int(d.total_seconds() // 60)
            mark = "  <- split here" if mins >= args.gap else ""
            echo(f"  {mins:>5} min   after {a.base} -> {b.base}{mark}")

    cache = Path(args.cache) if args.cache else previews.cache_dir_for(idx.root)
    if not args.by_scene:
        shoots, how = _shoots_by_subject(args, idx, pairs, cache)
        return _shoots_report(args, idx, shoots, how, cache)

    sigs = {}
    if not args.no_scene:
        rule("looking at what changes on screen")
        sigs = S.appearance_signatures(
            idx.stacks, cache,
            on_progress=lambda i, n: echo(f"  {i}/{n}") if i % 100 == 0 or i == n else None,
        )
        echo(f"  fingerprinted {len(sigs)} frames")

    shoots = S.split_combined(pairs, sigs, gap_minutes=args.gap,
                              scene_threshold=args.scene, min_frames=args.min_frames)
    how = f"gap {args.gap} min" + ("" if args.no_scene else f" or scene change > {args.scene}")
    raw_count = len(shoots)

    if not args.no_faces and shoots:
        rule(f"grouping by who is in each of {len(shoots)}")
        cache = Path(args.cache) if args.cache else previews.cache_dir_for(idx.root)
        try:
            shoots = S.merge_by_people(
                shoots, cache, gap_minutes=args.gap,
                on_msg=lambda m: echo(f"  {m}"),
                on_progress=lambda i, n: echo(f"  {i}/{n}"))
        except Exception as exc:
            echo(f"  (skipped people: {exc})")

    folded = f"  (folded back from {raw_count} by who is in them)" \
        if len(shoots) != raw_count else ""
    return _shoots_report(args, idx, shoots, how + folded, cache)


def _shoots_by_subject(args, idx, pairs, cache):
    """Cut where the person in front of the camera changes, not the wall."""
    from . import sessions as S

    faces_by_base, backend_name = None, "insightface"
    if not args.no_faces:
        picks = S.sample_for_identity(pairs, medium_minutes=args.gap_medium)
        rule(f"finding who is in front of the camera: {len(picks)} frames")
        try:
            faces_by_base, backend_name = S.find_subjects(
                picks, cache, long_side=args.long_side,
                on_msg=lambda m: echo(f"  {m}"),
                on_progress=lambda i, n: echo(f"  {i}/{n}"))
        except Exception as exc:
            # A stop is not an Exception (see server.Cancelled), so it passes
            # straight through here and ends the job as it should.
            echo(f"  (could not look at faces: {exc})")
            echo("  splitting on pauses alone instead")
            faces_by_base = None

    shoots = S.split_by_subject(
        pairs, faces_by_base, backend_name=backend_name,
        medium_minutes=args.gap_medium, big_minutes=args.gap,
        min_frames=args.min_frames)
    if faces_by_base is None:
        how = f"a pause of {min(args.gap, S.DEFAULT_BLIND_MIN)} min or more"
    else:
        how = (f"a new person after a {args.gap_medium} min pause, "
               f"or any pause of {args.gap} min")
    return shoots, how


def _shoots_report(args, idx, shoots, how, cache):
    """Print the proposal, write it down, and say what to do next."""
    from . import sessions as S

    rule(f"{len(shoots)} shoot(s) detected  ({how})")

    # The frames the app shows beside each proposal. Made here, at the one
    # standard size, so the strip is never a row of broken pictures.
    for sh in shoots:
        picks = S._spread(sh.stacks, 8)
        try:
            res = previews.extract_many([s.primary for s in picks if s.primary],
                                        cache, long_side=args.long_side)
            sh.sample = [r.out for r in res if r.ok]
        except Exception:
            pass

    for sh in shoots:
        if not sh.name:
            sh.name = sh.suggested_name()
        fr = sh.frames
        rng = f"{min(fr)}-{max(fr)}" if fr else "?"
        who = ", ".join(sh.people) if sh.people else "no one recognised"
        echo("")
        echo(f"  [{sh.index}]  {sh.name}")
        echo(f"       {sh.count} frames   {rng}")
        echo(f"       {sh.start:%a %d %b %Y  %H:%M} -> {sh.end:%H:%M}   ({sh.span_text()})")
        echo(f"       people: {who}")
        if sh.camera:
            echo(f"       camera: {sh.camera}")
        if sh.cut:
            echo(f"       starts here because: {sh.cut.get('text', '')}")
        for note in sh.joined:
            echo(f"       {note}")

    out_dir = idx.root / CACHE / "shoots"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "root": str(idx.root), "gap_minutes": args.gap,
        "gap_medium_minutes": args.gap_medium,
        "shoots": [{
            "index": sh.index, "suggested_name": sh.name, "count": sh.count,
            "frames": sh.frames,
            "start": sh.start.isoformat() if sh.start else None,
            "end": sh.end.isoformat() if sh.end else None,
            "people": sh.people, "camera": sh.camera,
            "bases": [s.base for s in sh.stacks],
            # Why it starts where it does, the pauses it was kept together
            # across, and which frames have a picture ready to show.
            "cut": sh.cut, "joined": sh.joined,
            "sample": [Path(p).stem for p in sh.sample],
        } for sh in shoots],
    }
    (out_dir / "shoots.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    echo("")
    echo(f"  proposal: {out_dir / 'shoots.json'}")

    if args.sheet:
        entries = []
        for sh in shoots:
            for p in sh.sample:
                entries.append((f"[{sh.index}] {sh.name}", Path(p).stem, p))
        if entries:
            out = report.contact_sheet(
                entries, out_dir / "shoots.html",
                title=f"Proposed shoots - {idx.root.name}",
                subtitle=f"{len(shoots)} groups, sampled frames - confirm before moving",
            )
            echo(f"  review sheet: {out}")

    echo("")
    echo("  Nothing has been moved. To create the folders, run 'cull commit'")
    echo("  with the names you want, e.g.:")
    for sh in shoots[:3]:
        echo(f"      --name {sh.index}=\"{sh.name}\"")
    return 0


# -------------------------------------------------------------------------- commit
def cmd_commit(args):
    mpath = Path(args.folder) / CACHE / "shoots" / "shoots.json"
    if not mpath.exists():
        echo(f"No proposal here yet. Run:  cull shoots \"{args.folder}\"")
        return 1
    data = json.loads(mpath.read_text(encoding="utf-8"))
    idx = scan.scan(args.folder, recursive=False)
    by_base = {s.base.lower(): s for s in idx.stacks}

    # --name accepts a single group or several joined with '+', so groups the
    # detector split by location can be merged back into one real photoshoot:
    #   --name 1="Navy tee"  --name 6+7+8+9+10="White shirt"
    renames = {}
    for item in (args.name or []):
        k, _, v = item.partition("=")
        if not v:
            echo(f"  bad --name '{item}', expected 1=\"Some Name\" or 3+4+5=\"Name\"")
            return 1
        for part in k.replace(",", "+").split("+"):
            part = part.strip()
            if part:
                renames[part] = v.strip()

    order, members = [], {}
    for sh in data["shoots"]:
        name = renames.get(str(sh["index"]), sh["suggested_name"])
        if name not in members:
            members[name] = []
            order.append(name)
        members[name].append(sh)

    rule("plan")
    plans = []
    for name in order:
        group = members[name]
        bases = [b for sh in group for b in sh["bases"]]
        picked = [by_base[b.lower()] for b in bases if b.lower() in by_base]
        if not picked:
            continue
        dest = Path(args.dest or idx.root) / _safe(name)
        plan = actions.build_plan(picked, dest, mode=actions.MOVE,
                                  on_conflict="rename")
        plans.append((name, plan))
        src = "+".join(str(sh["index"]) for sh in group)
        echo(f"  [{src}] {name}")
        echo(f"       {len(picked)} frames, {report.human(plan.bytes_total)} -> {dest}")

    if not plans:
        echo("  nothing to move")
        return 1
    if not args.apply:
        echo("")
        echo("  DRY RUN - nothing was touched. Add --apply to create the folders.")
        return 0

    from . import ledger as L

    rule("applying")
    src_cache = previews.cache_dir_for(idx.root)
    by_index = {str(sh["index"]): sh for sh in data["shoots"]}
    for name, plan in plans:
        journal = actions.execute(plan, apply=True)
        c = plan.summary()
        echo(f"  {name}: {c.get('done', 0)} files moved, {c.get('failed', 0)} failed")
        # Register the shoot so 'status' and 'work <name>' can find it later.
        meta = next((sh for sh in data["shoots"]
                     if renames.get(str(sh["index"]), sh["suggested_name"]) == name), None)
        L.record_shoot(
            plan.dest, name=name,
            count=len([o for o in plan.ops if o.status == "done"]),
            parent=idx.root,
            people=(meta or {}).get("people"),
            start=(meta or {}).get("start"),
            camera=(meta or {}).get("camera"),
        )
        # Carry each frame's cached preview along with it, so culling the new
        # folder does not re-read gigabytes of RAW off the drive again.
        moved = 0
        if src_cache.is_dir():
            dst_cache = previews.cache_dir_for(plan.dest)
            for op in plan.ops:
                if op.status != "done":
                    continue
                jpg = src_cache / (op.src.stem + ".jpg")
                if jpg.exists():
                    dst_cache.mkdir(parents=True, exist_ok=True)
                    try:
                        jpg.replace(dst_cache / jpg.name)
                        moved += 1
                    except OSError:
                        pass
        if moved:
            echo(f"      carried {moved} cached previews across")
        if journal:
            echo(f"      undo: cull undo \"{journal}\" --apply")
    return 0


def _safe(name):
    bad = '<>:"/\\|?*'
    out = "".join("-" if c in bad else c for c in name).strip(" .")
    return out or "shoot"


# -------------------------------------------------------------------------- poses
def cmd_poses(args):
    from . import poses as P
    from . import sessions as S

    idx = _index(args)
    stacks = idx.stacks[: args.limit] if args.limit else idx.stacks

    rule(f"reading capture times from {len(stacks)} frames")
    pairs = S.read_times(stacks, on_progress=lambda i, n:
                         echo(f"  {i}/{n}") if i % 200 == 0 or i == n else None)
    if not pairs:
        echo("  no readable capture times, cannot group by run")
        return 1

    prev, cache = _previews_for(idx, [s for s, _ in pairs], args, "previews")
    rule("comparing frames to each other")
    groups = P.group(pairs, prev, sim_threshold=args.similarity,
                     gap_seconds=args.gap, max_run=args.max_run,
                     on_progress=lambda i, n:
                     echo(f"  {i}/{n}") if i % 25 == 0 or i == n else None)

    sizes = [g.count for g in groups]
    rule(f"{len(groups)} runs found in {len(pairs)} frames")
    echo(f"  biggest {max(sizes)} frames, smallest {min(sizes)}, "
         f"typical {int(sorted(sizes)[len(sizes)//2])}")
    echo("")
    for g in groups[:24]:
        star = g.best.base if g.best else "?"
        echo(f"  {g.label():<34} sharpest: {star}")
    if len(groups) > 24:
        echo(f"  ... and {len(groups) - 24} more runs")

    out_dir = idx.root / CACHE / "poses"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "poses.json").write_text(json.dumps({
        "root": str(idx.root), "similarity": args.similarity, "gap": args.gap,
        "runs": [{"index": g.index, "count": g.count, "frames": g.frames,
                  "best": g.best.base if g.best else None,
                  "start": g.start.isoformat() if g.start else None,
                  "bases": [s.base for s in g.stacks]} for g in groups],
    }, indent=2), encoding="utf-8")
    echo(f"\n  {out_dir / 'poses.json'}")

    if args.sheet:
        entries = []
        for g in groups:
            for s in g.stacks:
                if s.primary and s.primary.stem in prev:
                    mark = "  ★ sharpest" if (g.best and s.base == g.best.base) else ""
                    entries.append((g.label(), s.base + mark, prev[s.primary.stem]))
        out = report.contact_sheet(entries, out_dir / "poses.html",
                                   title=f"Runs - {idx.root.name}",
                                   subtitle=f"{len(groups)} runs, sharpest starred")
        echo(f"  review sheet: {out}")

    dest_root = Path(args.dest) if args.dest else idx.root
    mode = actions.COPY if args.copy else actions.MOVE
    rule("plan")
    plans = []
    for g in groups:
        if g.count < args.min_frames:
            continue
        name = f"{args.prefix} {g.index:02d}"
        plan = actions.build_plan(g.stacks, dest_root / name, mode=mode,
                                  on_conflict="rename")
        plans.append((name, plan))
    echo(f"  {len(plans)} folders under {dest_root}")
    for name, plan in plans[:6]:
        echo(f"      {name}: {len([o for o in plan.ops if o.status == 'pending'])} files")
    if len(plans) > 6:
        echo(f"      ... and {len(plans) - 6} more")

    if not args.apply:
        echo("")
        echo("  DRY RUN - nothing was touched. Add --apply to build the folders.")
        return 0

    rule("applying")
    src_cache = previews.cache_dir_for(idx.root)
    done = 0
    for name, plan in plans:
        journal = actions.execute(plan, apply=True)
        done += plan.summary().get("done", 0)
        if not args.copy and src_cache.is_dir():
            dst = previews.cache_dir_for(plan.dest)
            for op in plan.ops:
                if op.status != "done":
                    continue
                jpg = src_cache / f"{op.src.stem}.jpg"
                if jpg.exists():
                    dst.mkdir(parents=True, exist_ok=True)
                    try:
                        jpg.replace(dst / jpg.name)
                    except OSError:
                        pass
    echo(f"  {done} files into {len(plans)} folders")
    echo("  Each folder is one run. Pick your own from inside it.")
    return 0


# ------------------------------------------------------------------------ quality
def _sift_measure(args, idx, stacks):
    """Look at every frame once and write down what was measured.

    This is the slow half of Sift - previews, faces, focus - and the only half
    that opens a photograph. What it writes to scores.json is measurement, not
    judgement, so every later change of mind is free.
    """
    from . import quality as Q

    prev, _ = _previews_for(idx, stacks, args, "previews for quality check")
    if not prev:
        echo("No previews could be extracted, so there is nothing to judge.")
        return None

    by_image = {}
    if not args.no_faces:
        from . import faces as F

        rule("faces")
        backend = F.get_backend(prefer=args.backend, on_msg=lambda m: echo(f"  {m}"))
        echo(f"  backend: {backend.name}")
        if backend.name != "insightface":
            echo("  note: this backend has no face mesh, so eyes-closed is skipped")
        state = {"n": 0}

        def step(path, n):
            state["n"] += 1
            if state["n"] % 25 == 0 or state["n"] == len(prev):
                echo(f"  {state['n']}/{len(prev)}")

        for f in F.analyse_previews(list(prev.values()), backend, on_step=step):
            by_image.setdefault(f.image, []).append(f)

    rule("scoring")
    src_by_stem = {s.primary.stem: s.primary for s in stacks if s.primary}
    scores, rebuilt = [], 0
    for i, p in enumerate(prev.values(), 1):
        checkpoint()
        p = Path(p)
        # The cache can vanish underneath a long run - another job tidying up,
        # or the drive hiccupping. Rebuild the one frame rather than calling the
        # photograph unreadable.
        if not p.exists() and p.stem in src_by_stem:
            res = previews.extract(src_by_stem[p.stem], p, long_side=args.long_side)
            if res.ok:
                rebuilt += 1
        scores.append(Q.score_image(p, by_image.get(p, []),
                                    subject_min=args.subject_face))
        if i % 50 == 0 or i == len(prev):
            echo(f"  {i}/{len(prev)}")
    if rebuilt:
        echo(f"  ({rebuilt} previews had gone missing and were rebuilt)")

    rows = [Q.to_row(s) for s in scores]

    # Runs of near-identical frames, so the screen can offer the best of each
    # instead of making him compare eleven of the same blink.
    groups = {}
    try:
        from . import poses as P
        from . import sessions as S

        rule("similar frames")
        pairs = S.read_times(stacks)
        for g in P.group(pairs, prev):
            if g.count >= 2:
                for st in g.stacks:
                    if st.primary:
                        groups[st.primary.stem] = g.index
        echo(f"  {len(set(groups.values()))} runs of similar frames")
    except Exception as e:      # grouping is a nicety; Sift stands without it
        echo(f"  (could not group similar frames: {e})")
    for r in rows:
        r["group"] = groups.get(r["base"])

    cache = idx.root / CACHE
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / (Q.SCORES_FILE + ".tmp")
    tmp.write_text(json.dumps({
        "version": Q.SCORES_VERSION,
        "analysed": datetime.now().isoformat(timespec="seconds"),
        "subject_face": args.subject_face,
        "frames": sorted(rows, key=lambda r: r["base"]),
    }, indent=1), encoding="utf-8")
    tmp.replace(cache / Q.SCORES_FILE)
    echo(f"  measurements: {cache / Q.SCORES_FILE}")
    return rows


def cmd_quality(args):
    from . import quality as Q

    idx = _index(args)
    stacks = idx.stacks[: args.limit] if args.limit else idx.stacks
    saved = Q.load_scores(idx.root)
    if saved is not None and not args.from_cache and not args.fresh:
        # Already looked at, and nothing new to look at: do not look again.
        have = {r["base"] for r in saved["frames"]}
        want = {s.primary.stem for s in stacks if s.primary}
        if want <= have and saved.get("subject_face") == args.subject_face:
            echo(f"  already measured ({saved.get('analysed', 'earlier')}), and no "
                 "new frames since - judging from that.")
            echo("  To look at every frame afresh:  --fresh")
            args.from_cache = True
    if args.from_cache:
        # Judge again from what was measured last time: a second, not minutes.
        if saved is None:
            echo("This folder has not been sifted yet, so there is nothing to")
            echo("judge again. Run it once without --from-cache.")
            return 1
        rows = saved["frames"]
        echo(f"  judging {len(rows)} frames from the measurements already taken")
    else:
        rows = _sift_measure(args, idx, stacks)
        if rows is None:
            return 1

    if args.blur_ratio is None:
        from . import taste
        learned = taste.learned_blur_ratio()
        args.blur_ratio = learned["ratio"] if learned else taste.DEFAULT_RATIO
        if learned:
            echo(f"  softness bar {args.blur_ratio:.2f}x - learned from your "
                 f"{learned['votes']} thumbs up and down")
    overrides = Q.load_overrides(idx.root)
    scores, stats = Q.judge(rows, {
        "blur_ratio": args.blur_ratio, "eye_ratio": args.eye_ratio,
        "eye_abs": args.eye_abs, "min_face_frac": args.eyes_min_face,
        "reject_ratio": args.reject_ratio, "eye_soft": args.eye_soft,
        "blown": args.blown, "black": args.black,
    }, overrides, image_dir=previews.cache_dir_for(idx.root))
    if overrides:
        echo(f"  {sum(1 for s in scores if s.override)} frames carry your own call, "
             "which stands over anything measured")
    ignored = sum(1 for s in scores if s.detected and not s.faces)
    if ignored:
        echo(f"  {ignored} frames had only background faces (under "
             f"{args.subject_face * 100:.2f}% of frame) - scored on region instead")

    rule("how sharp is this shoot")
    echo(f"  median sharpness {stats['median_sharpness']:.2f}   "
         f"{stats['with_faces']}/{stats['total']} frames contain a face")
    if stats["ear_bar"] is not None:
        echo(f"  median eye openness {stats['median_ear']:.3f}   "
             f"-> blink flagged below {stats['ear_bar']:.3f}")
    echo("  distribution of sharpness relative to that median:")
    for a, b, n in Q.distribution(scores):
        bar = "#" * min(48, n)
        mark = "  <- flagged below here" if a < args.blur_ratio <= b else ""
        echo(f"    {a:4.2f}-{b:4.2f}x  {n:4d} {bar}{mark}")

    errors = [s for s in scores if s.tier == "error"]
    if errors:
        echo("")
        echo(f"  !! {len(errors)} frames could not be opened at all.")
        echo("     That is a problem with the drive or the cache, NOT with your")
        echo("     photos. They are left exactly where they are, and counted as")
        echo("     nothing. Try running this again before trusting the result.")
        for s in errors[:5]:
            echo(f"       {s.base}")
        if len(errors) > 5:
            echo(f"       ... and {len(errors) - 5} more")

    rejects = [s for s in scores if s.tier in ("reject", "review")]
    keep = [s for s in scores if s.tier == "keep"]
    by_reason = {}
    for s in rejects:
        for r in s.reasons:
            by_reason[r] = by_reason.get(r, 0) + 1

    tail = f", {len(errors)} unreadable" if errors else ""
    rule(f"verdict: {len(keep)} keep, {len(rejects)} flagged{tail}")
    for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        echo(f"  {n:4d}  {r}")

    worst = sorted(rejects, key=lambda s: (s.blur_ratio
                                           if not __import__('math').isnan(s.blur_ratio)
                                           else 9e9))
    if worst:
        echo()
        echo("  worst first:")
        for s in worst[:15]:
            ear = f" ear {s.ear:.2f}" if s.eyes_known else ""
            echo(f"    {s.base:<18} {s.blur_ratio:4.2f}x{ear}   {s.verdict}")
        if len(worst) > 15:
            echo(f"    ... and {len(worst) - 15} more")

    cache = idx.root / CACHE
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "quality.json").write_text(json.dumps({
        "median_sharpness": stats["median_sharpness"],
        "settings": {"blur_ratio": args.blur_ratio, "eye_ratio": args.eye_ratio,
                     "reject_ratio": args.reject_ratio, "eye_soft": args.eye_soft},
        "frames": [Q.frame_out(s) for s in sorted(scores, key=lambda x: x.base)],
    }, indent=2), encoding="utf-8")
    echo(f"\n  scores: {cache / 'quality.json'}")

    # Record the outcome whether or not anything gets moved, and whether or not
    # --apply was passed: the cull happened, and a later session needs to know.
    from . import ledger as L

    L.record_shoot(idx.root, count=len(scores))
    L.record_cull(idx.root, keep=len(keep),
                  review=sum(1 for s in scores if s.tier == "review"),
                  reject=sum(1 for s in scores if s.tier == "reject"))
    echo(f"  ledger: registered as \"{Path(idx.root).name}\"")

    if args.sheet or args.apply:
        entries = []
        for s in worst:
            entries.append((f"FLAGGED: {s.verdict}",
                            f"{s.base}  {s.blur_ratio:.2f}x", s.image))
        for s in sorted(keep, key=lambda x: x.base):
            entries.append(("keep", f"{s.base}  {s.blur_ratio:.2f}x", s.image))
        out = report.contact_sheet(
            entries, cache / "quality.html",
            title=f"Quality review - {idx.root.name}",
            subtitle=f"{len(rejects)} flagged of {len(scores)} - flagged shown first",
        )
        echo(f"  review sheet: {out}")
        echo("  Open that before applying. Rejections are a suggestion, not a verdict.")

    if not rejects:
        echo("\n  Nothing flagged.")
        return 0

    stack_by_stem = {s.primary.stem: s for s in stacks if s.primary}
    root = Path(args.dest) if args.dest else idx.root
    groups = [
        ("reject", args.reject_name, [s for s in scores if s.tier == "reject"]),
        ("review", args.review_name, [s for s in scores if s.tier == "review"]),
    ]

    rule("plan")
    plans = []
    for tier, name, members in groups:
        if not members:
            continue
        picked = [stack_by_stem[s.base] for s in members if s.base in stack_by_stem]
        if not picked:
            continue
        plan = actions.build_plan(picked, root / name, mode=actions.MOVE,
                                  on_conflict=args.on_conflict)
        plans.append((tier, name, plan))
        why = ("clearly unusable: black, blown, or below "
               f"{args.reject_ratio:.2f}x sharpness"
               if tier == "reject" else "your call: borderline focus, closed eyes")
        echo(f"  {name}\\  {len(members)} frames, "
             f"{report.human(plan.bytes_total)}   ({why})")

    if not plans:
        echo("  nothing to move")
        return 0

    if not args.apply:
        echo()
        echo("  DRY RUN - nothing was touched. Re-run with --apply to move these.")
        echo("  Files are moved, never deleted, and every run is reversible.")
        return 0

    rule("applying")
    for tier, name, plan in plans:
        journal = actions.execute(plan, apply=True)
        c = plan.summary()
        echo(f"  {name}\\: {c.get('done', 0)} files moved, "
             f"{c.get('failed', 0)} failed")
        if journal:
            echo(f"      undo: cull undo \"{journal}\" --apply")

    # Nothing in the cache is deleted here. The previews are what the review
    # sheet and the app's grid are made of, and you may not be finished looking.
    # Clearing space is a separate, deliberate step: 'cull clean'.
    cache = previews.cache_dir_for(idx.root)
    if cache.is_dir():
        mb = sum(f.stat().st_size for f in cache.glob("*.jpg")) / (1024 * 1024)
        if mb > 20:
            echo("")
            echo(f"  {mb:.0f} MB of previews are cached for this shoot. When you are")
            echo("  finished with it, free them with:")
            echo(f"      cull clean \"{idx.root}\"")
    return 0


# --------------------------------------------------------------------------- name
def cmd_name(args):
    from . import faces as F
    import numpy as np

    mpath = Path(args.folder) / CACHE / "people" / "people.json"
    if not mpath.exists():
        echo(f"No people.json here. Run:  cull people \"{args.folder}\"")
        return 1
    data = json.loads(mpath.read_text(encoding="utf-8"))
    people = data["people"]
    if args.cluster not in people:
        echo(f"'{args.cluster}' is not one of: {', '.join(people)}")
        return 1

    rec = people.pop(args.cluster)
    people[args.new_name] = rec
    data["people"] = people
    mpath.write_text(json.dumps(data, indent=2), encoding="utf-8")

    group = F.PersonGroup(rec["cluster"], args.new_name)
    group.faces = [F.Face(Path("."), (0, 0, 0, 0), 1.0,
                          np.asarray(rec["centroid"], dtype=np.float32))]
    F.remember(args.new_name, group, data.get("backend", "opencv"))

    faces_dir = Path(args.folder) / CACHE / "people" / "_faces"
    old, new = faces_dir / f"{args.cluster}.jpg", faces_dir / f"{args.new_name}.jpg"
    if old.exists() and not new.exists():
        old.rename(new)

    folder = Path(args.folder) / "By person"
    o, n = folder / args.cluster, folder / args.new_name
    if o.exists() and not n.exists():
        o.rename(n)
        echo(f"  renamed folder -> {n}")

    echo(f"  {args.cluster} is now {args.new_name}, and remembered in "
         f"{F.LIBRARY}")
    echo("  Future shoots will label this person automatically.")
    return 0


# -------------------------------------------------------------------------- sheet
def cmd_sheet(args):
    idx = _index(args)
    stacks = idx.stacks[: args.limit] if args.limit else idx.stacks
    good, _ = _previews_for(idx, stacks, args)
    entries = [("", s.base, good[s.primary.stem])
               for s in stacks if s.primary and s.primary.stem in good]
    out = report.contact_sheet(
        entries, Path(args.out) if args.out else idx.root / CACHE / "contact-sheet.html",
        title=f"Contact sheet - {idx.root.name}",
        subtitle=f"{len(entries)} frames",
    )
    echo(f"  {out}")
    return 0


# --------------------------------------------------------------------------- undo
def cmd_undo(args):
    results = actions.undo(args.journal, apply=args.apply)
    for path, status, note in results[:40]:
        echo(f"  {status:<9} {path.name} {note}")
    if len(results) > 40:
        echo(f"  ... and {len(results) - 40} more")
    if not args.apply:
        echo("\n  DRY RUN - re-run with --apply to actually reverse it.")
    return 0


# --------------------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog="cull", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, with_preview=True):
        sp.add_argument("folder", help="the shoot folder")
        sp.add_argument("-r", "--recursive", action="store_true")
        sp.add_argument("-v", "--verbose", action="store_true")
        if with_preview:
            sp.add_argument("--long-side", type=int, default=2048,
                            help="preview resolution (default 2048)")
            sp.add_argument("--workers", type=int, default=0)
            sp.add_argument("--cache", help=f"preview cache (default <folder>/{CACHE})")
            sp.add_argument("--refresh", action="store_true",
                            help="re-extract even if cached")
            sp.add_argument("--limit", type=int, default=0)
            sp.add_argument("--sheet", action="store_true",
                            help="also write an HTML contact sheet")

    sp = sub.add_parser("scan", help="summarise a folder")
    common(sp, with_preview=False)
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("select", help="separate the selects from a numbers list")
    common(sp)
    sp.add_argument("-l", "--list", help="file with the numbers (omit to paste them)")
    sp.add_argument("-d", "--dest", help="destination (default <folder>/Selects)")
    sp.add_argument("--dest-name", default="Selects")
    sp.add_argument("--copy", action="store_true", help="copy instead of move")
    sp.add_argument("--link", action="store_true", help="hard-link instead of move")
    sp.add_argument("--apply", action="store_true", help="actually do it")
    sp.add_argument("--verify", action="store_true", help="checksum every copy")
    sp.add_argument("--strict", action="store_true", help="stop if any frame is missing")
    sp.add_argument("--max-span", type=int, default=selects.DEFAULT_MAX_SPAN)
    sp.add_argument("--on-conflict", choices=["rename", "skip", "fail"], default="rename")
    sp.set_defaults(func=cmd_select)

    sp = sub.add_parser("preview", help="extract embedded JPEGs from the RAWs")
    common(sp)
    sp.add_argument("--frames", nargs="*", help="only these frame numbers/ranges")
    sp.add_argument("--max-span", type=int, default=selects.DEFAULT_MAX_SPAN)
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("people", help="group the shoot by who is in the photo")
    common(sp)
    sp.add_argument("-d", "--dest", help="default <folder>/By person")
    sp.add_argument("--backend", choices=["insightface", "opencv"])
    sp.add_argument("--threshold", type=float,
                    help="cosine distance for 'same person'; lower = stricter")
    sp.add_argument("--min-face", type=float, default=0.0,
                    help="ignore faces smaller than this fraction of the frame")
    sp.add_argument("--min-score", type=float, default=0.0)
    sp.add_argument("--min-photos", type=int, default=1,
                    help="drop people appearing in fewer than N photos")
    sp.add_argument("--copy", action="store_true",
                    help="copy into the person folders (default: hard-link, no space)")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--on-conflict", choices=["rename", "skip", "fail"], default="rename")
    sp.set_defaults(func=cmd_people)

    sp = sub.add_parser("clean", help="delete cached previews to free up space")
    sp.add_argument("folder", nargs="?",
                    help="omit to clear every folder the program has worked in")
    sp.add_argument("-r", "--recursive", action="store_true",
                    help="every shoot folder underneath this one")
    sp.add_argument("--all", action="store_true",
                    help="also remove the reports; undo journals are always kept")
    sp.add_argument("--dry-run", action="store_true",
                    help="just report what is cached and how big it is")
    sp.set_defaults(func=cmd_clean)

    sp = sub.add_parser("app", help="open the visual interface in a browser")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-open", action="store_true",
                    help="do not launch a browser automatically")
    sp.set_defaults(func=cmd_app)

    sp = sub.add_parser("drives", help="what is plugged in right now")
    sp.add_argument("--fast", action="store_true", help="skip counting photos")
    sp.set_defaults(func=cmd_drives)

    sp = sub.add_parser("browse", help="look inside a folder to pick one")
    sp.add_argument("path", nargs="?", help="omit to list drives")
    sp.add_argument("--fast", action="store_true", help="skip counting photos")
    sp.add_argument("--cap", type=int, default=5000)
    sp.set_defaults(func=cmd_browse)

    sp = sub.add_parser("import", help="copy a card to the archive, verified")
    sp.add_argument("source", help="the card, e.g. D:\\ or D:\\DCIM")
    sp.add_argument("-d", "--dest", help="archive root (default: saved setting)")
    sp.add_argument("-n", "--name", help="folder name (default: today's date)")
    sp.add_argument("--no-verify", action="store_true",
                    help="skip checksums: faster, and you lose the proof")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("journal", help="what the program has been doing")
    sp.add_argument("-n", "--tail", type=int, default=60,
                    help="how many of the most recent entries to show")
    sp.add_argument("-k", "--kind", default="",
                    help="only this family, e.g. job, push, ui, http")
    sp.add_argument("-g", "--grep", default="",
                    help="only entries containing this text")
    sp.add_argument("--errors", action="store_true",
                    help="only what went wrong")
    sp.add_argument("--raw", action="store_true",
                    help="the JSON lines themselves, for a machine to read")
    sp.add_argument("--clear", action="store_true",
                    help="throw the whole record away")
    sp.set_defaults(func=cmd_journal)

    sp = sub.add_parser("status", help="what has been imported, culled and picked")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("work", help="move chosen frames into the working folder")
    sp.add_argument("shoot", help="shoot name or folder path")
    sp.add_argument("-l", "--list", help="file with the chosen numbers")
    sp.add_argument("numbers", nargs="*", help="or the numbers straight on the line")
    sp.add_argument("-d", "--dest", help="working root (default: saved setting)")
    sp.add_argument("--into", help="subfolder name (default: the shoot's name)")
    sp.add_argument("--copy", action="store_true", help="copy instead of move")
    sp.add_argument("--discard-rest", action="store_true",
                    help="then move the shoot folder, with what was not picked, "
                         "into _DESCARTABLES on the same drive")
    sp.add_argument("--discards", help="discards folder (default: <drive>\_DESCARTABLES)")
    sp.add_argument("--stars", type=int, default=0, choices=range(0, 6),
                    help="give the picks this many stars, in .xmp sidecars Lightroom reads")
    sp.add_argument("--label", help="and this colour label (red, yellow, green, blue, purple)")
    sp.add_argument("--label-lang", default="en", choices=["en", "es"],
                    help="the language your Lightroom is in - label names are translated")
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_work)

    sp = sub.add_parser("xmp", help="write Sift's verdicts as stars and colour labels "
                                    "for Lightroom")
    sp.add_argument("folder")
    sp.add_argument("-r", "--recursive", action="store_true")
    sp.add_argument("--stars", type=int, default=0, choices=range(0, 6),
                    help="stars for the frames Sift kept (default: none)")
    sp.add_argument("--keep-label", default="green")
    sp.add_argument("--review-label", default="yellow")
    sp.add_argument("--reject-label", default="red")
    sp.add_argument("--reject-rating", action="store_true",
                    help="also rate rejects -1, which Bridge shows as Reject")
    sp.add_argument("--label-lang", default="en", choices=["en", "es"])
    for flag in ("--blur-ratio", "--eye-ratio", "--reject-ratio", "--eye-soft",
                 "--blown", "--black"):
        sp.add_argument(flag, type=float, default=None)
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_xmp)

    sp = sub.add_parser("config", help="set the archive and working folders once")
    sp.add_argument("--archive-root")
    sp.add_argument("--work-root")
    sp.add_argument("--gap", type=int)
    sp.set_defaults(func=cmd_config)

    sp = sub.add_parser("shoots", help="how many separate photoshoots are in here")
    common(sp)
    sp.add_argument("--gap", type=int, default=90,
                    help="a pause this long is a new shoot whoever is in it "
                         "(minutes, default 90)")
    sp.add_argument("--gap-medium", type=int, default=10,
                    help="a pause this long is a new shoot only if the person "
                         "changed too (minutes, default 10)")
    sp.add_argument("--by-scene", action="store_true",
                    help="the old way: cut where the picture changes "
                         "(sensitive to the background)")
    sp.add_argument("--min-frames", type=int, default=5,
                    help="fewer frames than this get folded into the run before")
    sp.add_argument("--scene", type=float, default=0.42,
                    help="how much the picture must change to count as a new "
                         "shoot, 0-1 (default 0.42; lower splits more)")
    sp.add_argument("--no-scene", action="store_true",
                    help="split on time gaps only, ignore what the frames look like")
    sp.add_argument("--no-faces", action="store_true",
                    help="skip identifying people: much faster")
    sp.set_defaults(func=cmd_shoots)

    sp = sub.add_parser("commit", help="create the shoot folders and move frames in")
    sp.add_argument("folder")
    sp.add_argument("--name", action="append",
                    help='rename a group, e.g. --name 1="2026-06-14 - Cruz"')
    sp.add_argument("-d", "--dest", help="where the shoot folders go (default here)")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("poses", help="group a shoot into its runs of similar frames")
    common(sp)
    sp.add_argument("-d", "--dest", help="where the run folders go (default here)")
    sp.add_argument("--prefix", default="Pose", help='folder name prefix (default "Pose")')
    sp.add_argument("--similarity", type=float, default=0.62,
                    help="how alike two frames must be, 0-1 (lower = bigger runs)")
    sp.add_argument("--gap", type=int, default=90,
                    help="seconds of stillness that end a run (default 90)")
    sp.add_argument("--min-frames", type=int, default=1,
                    help="do not make a folder for runs smaller than this")
    sp.add_argument("--max-run", type=int, default=16,
                    help="break up any run bigger than this (0 to allow any size)")
    sp.add_argument("--copy", action="store_true", help="copy instead of move")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_poses)

    sp = sub.add_parser("quality", help="flag soft, blinked and badly exposed frames")
    common(sp)
    sp.add_argument("-d", "--dest", help="where the two folders go (default <folder>)")
    sp.add_argument("--reject-name", default="Rejects")
    sp.add_argument("--review-name", default="Review")
    sp.add_argument("--reject-ratio", type=float, default=0.25,
                    help="below this fraction of median sharpness a frame is "
                         "binned outright rather than sent to review (default 0.25)")
    sp.add_argument("--subject-face", type=float, default=0.0035,
                    help="a face must fill this fraction of the frame to count "
                         "as the subject (default 0.0035). Smaller faces are "
                         "treated as background and the frame is scored on its "
                         "sharpest region instead")
    sp.add_argument("--backend", choices=["insightface", "opencv"])
    sp.add_argument("--no-faces", action="store_true",
                    help="skip face detection: much faster, whole-frame sharpness "
                         "only, no eyes-closed check")
    sp.add_argument("--blur-ratio", type=float, default=None,
                    help="flag frames below this fraction of the shoot's median "
                         "sharpness (default: learned from your thumbs up and "
                         "down, 0.65 until there are enough; lower = more forgiving)")
    sp.add_argument("--from-cache", action="store_true",
                    help="judge again from the measurements already taken "
                         "(scores.json) instead of analysing every frame")
    sp.add_argument("--fresh", action="store_true",
                    help="measure every frame again even though it was done before")
    sp.add_argument("--blown", type=float, default=None,
                    help="fraction of the frame clipped white that counts as blown")
    sp.add_argument("--black", type=float, default=None,
                    help="fraction of the frame clipped black that counts as lost")
    sp.add_argument("--eye-soft", type=float, default=0.35,
                    help="eyes softer than this fraction of the shoot's median "
                         "are flagged 'eyes soft' (default 0.35)")
    sp.add_argument("--eye-ratio", type=float, default=0.55,
                    help="flag eyes below this fraction of the shoot's median "
                         "eye openness (default 0.55; lower = more forgiving)")
    sp.add_argument("--eye-abs", type=float,
                    help="use an absolute eye-openness threshold instead of the "
                         "shoot-relative one")
    sp.add_argument("--eyes-min-face", type=float, default=0.0,
                    help="only judge eyes when the face fills this fraction of frame")
    sp.add_argument("--keep-previews", action="store_true",
                    help="leave the preview cache in place instead of tidying it up")
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("--on-conflict", choices=["rename", "skip", "fail"], default="rename")
    sp.set_defaults(func=cmd_quality)

    sp = sub.add_parser("name", help="give a cluster a real name, permanently")
    sp.add_argument("folder")
    sp.add_argument("cluster", help="e.g. person_01")
    sp.add_argument("new_name")
    sp.set_defaults(func=cmd_name)

    sp = sub.add_parser("sheet", help="HTML contact sheet of a folder")
    common(sp)
    sp.add_argument("-o", "--out")
    sp.set_defaults(func=cmd_sheet)

    sp = sub.add_parser("undo", help="reverse an applied run")
    sp.add_argument("journal")
    sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_undo)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        echo("\ninterrupted - nothing further was changed")
        return 130
    except (NotADirectoryError, FileNotFoundError) as exc:
        echo(f"error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
