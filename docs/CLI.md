# cullkit

A local culling system for RAW shoots. Give it a folder and a list of numbers;
it separates the selects, pulls the embedded JPEG previews out of the RAW files
so the photos can actually be looked at, and groups the shoot by person.

Nothing leaves the machine. No Lightroom, no cloud, no subscription.

## Run it

```bash
.\cull.cmd scan "D:\Boda Sofia\RAW"
```

From PowerShell use `.\cull.ps1` instead. Both work on external drives.

## The main job: separate the selects

Put the numbers in a text file, however they come out of your head:

```
Selects - boda 2026-03-14
1200, 1203-1207
DSC_1210.NEF
1215 a 1218
0001221
```

Ranges, bare numbers, full filenames, zero-padded numbers, `a`/`to`/`hasta`,
notes mixed in — all understood. ISO dates like `2026-03-14` are never mistaken
for a range.

```bash
.\cull.cmd select "D:\Boda Sofia\RAW" --list picks.txt
```

That is a **dry run**. It prints what it would do and touches nothing. It also
tells you the two things that matter before you commit:

- **MISSING** — numbers in your list that are not in the folder. If you listed
  200 selects and only 197 exist, you get the exact 3 that are absent.
- **ambiguous** — one frame number sitting on two files, which is what happens
  when two bodies shot the same day. It never guesses; it shows you both.

Add `--apply` when the plan looks right:

```bash
.\cull.cmd select "D:\Boda Sofia\RAW" --list picks.txt --apply
```

The whole stack travels together — `DSC_1204.NEF`, `DSC_1204.JPG` and
`DSC_1204.xmp` all move as one. Existing files are never overwritten. Every
applied run writes an undo journal and prints the command that reverses it.

| flag | effect |
|---|---|
| `--copy` / `--link` | copy, or hard-link (no extra disk space), instead of moving |
| `--dest "E:\Selects"` | somewhere other than `<folder>\Selects` |
| `--sheet` | also write an HTML contact sheet of the selects |
| `--verify` | checksum every copied file |
| `--strict` | refuse to run if any listed frame is missing |
| `--apply` | actually do it |

## Seeing the photos: embedded JPEG previews

Every RAW on the market carries full JPEGs inside it. cullkit finds them by
signature and keeps the largest, which works the same way across NEF, CR2, CR3,
ARW, DNG, RAF, ORF and RW2 — no vendor SDK, no exiftool, no demosaicing.

```bash
.\cull.cmd preview "D:\Boda Sofia\RAW" --sheet
```

Previews land in `<folder>\_cullkit\previews\` and are cached, so the second run
is instant. Portrait frames come out upright: if the preview carries no
orientation of its own, the RAW's own orientation tag is used. Sideways faces
are faces the detector misses.

Because previews are ordinary JPEGs, Claude can open them directly — which is
how "look at these and tell me which ones are sharp" works.

## Grouping by person

```bash
.\cull.cmd people "D:\Boda Sofia\RAW"
```

Extracts previews, finds every face, and clusters them into people. Prints one
reference crop per person into `_cullkit\people\_faces\` so you can see who each
cluster is. Dry run by default; `--apply` builds a `By person\` folder tree
using hard links, so it costs no extra disk space.

Name someone once and it sticks for good:

```bash
.\cull.cmd name "D:\Boda Sofia\RAW" person_01 "Sofia"
```

That writes her face signature to `~/.cullkit/people/library.json`. Every future
shoot labels her automatically — different camera, different filenames, doesn't
matter.

| flag | effect |
|---|---|
| `--min-face 0.02` | ignore faces smaller than 2% of the frame (drops crowd backgrounds) |
| `--min-photos 3` | drop people appearing in fewer than 3 photos |
| `--threshold 0.5` | stricter matching; lower splits more, higher merges more |
| `--copy` | real copies instead of hard links |
| `--sheet` | HTML sheet grouped by person |

Two backends. `insightface` (buffalo_l) is installed and used by default. If it
is ever unavailable, it falls back to OpenCV YuNet + SFace automatically. The
`insightface` models download once, to `~/.insightface`.

## Throwing out the bad frames

```bash
.\cull.cmd quality "D:\Boda Sofia\RAW" --sheet
```

Flags three things: **soft focus**, **eyes closed**, and **blown or black
frames**. Dry run by default. `--apply` moves the flagged frames into
`Rejects\` — moved, never deleted, and reversible with `cull undo`.

**Sharpness is measured on the face, not the frame.** This matters more than
anything else here. A whole-frame blur measure marks every shallow-depth-of-
field portrait as a reject, because most of an f/1.4 frame genuinely is
blurry — that is the look you paid the lens for. Scoring the face crop asks the
right question instead: did focus land on the person. On the test set, frames
that genuinely missed focus scored 0.02–0.07x the shoot median, while
shallow-DOF frames with a sharp face scored 0.99–1.01x. Nothing in between.

**Both thresholds are relative to the shoot, not absolute.** Focus numbers mean
nothing across different lenses, apertures and light. A frame is called soft
when it is markedly softer than the median frame of that same shoot. Same for
eyes: most frames in any shoot have open eyes, so the shoot's median eye
openness *is* "open" for this face at this crop, and a blink sits far below it.
Nothing to calibrate by hand.

The run prints the sharpness distribution with the cut-off marked, so you can
see whether the line falls in a sensible place before applying anything.

| flag | effect |
|---|---|
| `--blur-ratio 0.45` | soft below this fraction of median sharpness; lower is more forgiving |
| `--eye-ratio 0.55` | blink below this fraction of median eye openness; raise it to catch half-blinks |
| `--eye-abs 0.2` | use an absolute eye threshold instead of the relative one |
| `--no-faces` | whole-frame sharpness only. Much faster, but loses the shallow-DOF protection and the eye check |
| `--sheet` | HTML review sheet, flagged frames first, with every score |
| `--apply` | move the flagged frames to `Rejects\` |

Always open the review sheet before applying. The flags are a suggestion, not a
verdict — a soft frame can still be the only shot of a moment.

### What has and has not been proven

Verified on constructed test frames: soft-focus detection, shallow-DOF
protection, blown and black frames, and that eye openness falls monotonically
as the lid closes (0.37 open → 0.28 half → 0.22 closing → 0.18 shut).

Not yet verified on real blinks. The lid test above was made by geometrically
closing the eyes on a real photograph, and the landmark model's shape prior
keeps the number from collapsing all the way. Real blinks should read lower,
but run one real shoot and check the review sheet before trusting `--apply`
on the eye flag. `--eye-ratio` is the dial.

## Undo

```bash
.\cull.cmd undo "D:\...\Selects\_cullkit\undo-20260819-201550.json" --apply
```

Moves go back where they came from; copies and links are removed. Without
`--apply` it just shows what it would restore.

## Where things live

| | |
|---|---|
| code | `cullkit\` (this folder) |
| Python + libraries | `~\.cullkit\venv` — deliberately outside OneDrive, so it isn't synced |
| face models | `~\.insightface`, `~\.cullkit\models` |
| named people | `~\.cullkit\people\library.json` — the one file worth backing up |
| per-shoot cache | `<shoot>\_cullkit\` — safe to delete any time, it regenerates |

## Commands

| | |
|---|---|
| `scan` | what is in the folder: counts, frame range, gaps, duplicate numbers |
| `select` | separate the selects from a numbers list |
| `preview` | extract embedded JPEGs from the RAWs |
| `people` | group the shoot by who is in each photo |
| `name` | give a cluster a real name, permanently |
| `quality` | flag soft, blinked and badly exposed frames |
| `sheet` | HTML contact sheet |
| `undo` | reverse an applied run |

`--help` works on every one of them.

## Tests

`tests\make_fixtures.py` builds fake RAWs shaped like real ones: TIFF header,
orientation tag, a decoy thumbnail, several hundred KB of random sensor data
containing stray JPEG signatures, then the real preview. It is the case that
breaks naive extractors.

```bash
~\.cullkit\venv\Scripts\python.exe tests\make_fixtures.py .\fixtures
.\cull.cmd select .\fixtures\shoot --list .\fixtures\picks.txt
```
