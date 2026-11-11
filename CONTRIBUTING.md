# Contributing

Agape Aftershot is open source under the MIT license. Use it, change it, and
send back what you improve.

## Ways to help

- **Tell us what happened on your setup.** It has been used on one Windows
  machine with Nikon files. A report that it works - or how it failed - with
  another camera, another RAW format or another drive layout is worth a lot.
  Open an issue and include the camera, the file type and what you saw.
- **Fix or add something.** Fork the repository, make the change on a branch,
  and open a pull request that says what it does and why.
- **Try it on a Mac.** The macOS support in `cullkit/plat.py` and the
  `.command` launchers were written without a Mac to run them on. The single
  most useful thing anyone can do right now is install it on one and report
  what happens - or fix what does not.
- **Linux.** Not supported. `plat.py` is where it would go.

## Before you send a change

Run the tests:

```bash
"%USERPROFILE%\.cullkit\venv\Scripts\python.exe" -m unittest discover -s tests
```

They run against synthetic photographs in a temporary folder and never touch
real files.

## Four rules a change must keep

These are what make the program safe to point at a card full of work.

1. **Nothing is deleted.** Move it somewhere the photographer can look.
2. **Nothing is overwritten.** A taken name is skipped or numbered.
3. **A dry run comes first.** Anything that moves files shows its plan before `--apply`.
4. **Nothing leaves the machine** unless the photographer chose a feature that says it does.

A pull request that breaks one of these will be asked to change, however good
the rest of it is.

## Style

- No build step and no framework in `cullkit/web/`. It is plain HTML, CSS and JavaScript on purpose.
- Comments say *why*, especially when the code looks odd. Most odd-looking
  code here is the fix for something that went wrong on real photographs.
- The interface is in English.
