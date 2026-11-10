# logs

What the program said while it was being put through its paces, kept so it can
be read later rather than remembered.

| file | what it is |
|---|---|
| `simulate.py` | drives the whole pipeline over the app's own HTTP API |
| `2026-08-23 simulation.log` | what that run printed, in order |
| `2026-08-23 simulation record.jsonl` | the journal that run produced |

## Running it again

```bash
"%USERPROFILE%\.cullkit\venv\Scripts\python.exe" "logs\simulate.py"
```

It builds a fake camera card out of the test fixtures, starts a second copy of
the app on port 8801 with a **throwaway HOME**, and puts it through the
rehearsal, the real copy, two jobs asked for at once, separating, sifting, a
deliberate stop and a command too small to be worth a notification. Nothing it
does touches the real settings, ledger, journal, push devices or a single
photograph, and it deletes its own fixtures on the way out.

The question it exists to ask is the one that started it: while a job is
running, does the machine ever say it is free? Every poll checks, and the run
says so plainly at the end of each job.

## The real record

This folder is a snapshot. The live account of what the app actually does is
`~/.cullkit/journal.jsonl` — read it in **Overview → What the program has
done**, save it from the same panel, or from a terminal:

```bash
"%USERPROFILE%\.cullkit\venv\Scripts\python.exe" -m cullkit.cli journal --tail 50
```
