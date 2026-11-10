"""Drive the whole program end to end, over its own HTTP API, and write down
what it said.

Runs against a throwaway HOME so nothing touches the real settings, ledger,
journal or push devices. The point is not to test the photo maths - the unit
tests do that - it is to answer the questions that only show up when a person
actually uses this: does the machine ever claim to be free while a job is
running, does a finished job say what it did, does the record get written.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(r"C:\Users\diane\OneDrive\Escritorio\Agape Aftershot")
PY = Path(r"C:\Users\diane\.cullkit\venv\Scripts\python.exe")
PORT = 8801   # deliberately not the app's 8765
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, str(REPO))

OUT = []


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    OUT.append(line)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Origin": BASE},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def run_job(argv, label):
    """Start a job, watch it the way the page does, and report on the watching."""
    say("")
    say("=" * 72)
    say("RUN", label, "->", argv)
    say("=" * 72)
    code, res = post("/api/run", {"argv": argv, "device": "sim-device"})
    if code != 200:
        say("  REFUSED", code, res)
        return None
    jid = res["id"]
    since = 0
    saw_running = False
    saw_free_while_running = False
    phases = []
    t0 = time.time()
    while True:
        j = get(f"/api/job?id={jid}&since={since}")
        since = j["total"]
        st = get("/api/state")
        prog = j.get("progress") or {}
        if prog.get("phase") and (not phases or phases[-1] != prog["phase"]):
            phases.append(prog["phase"])
        if not j["done"]:
            if st.get("running"):
                saw_running = True
            else:
                # The exact complaint: the job is going and the machine says
                # nothing is.
                saw_free_while_running = True
                say("  !! state says free while job", jid, "is still running")
            if (st.get("program") or {}).get("state") != "working":
                say("  !! program state is", (st.get("program") or {}).get("state"),
                    "while a job runs")
        if j["done"]:
            say(f"  finished rc={j['rc']} in {time.time() - t0:.1f}s")
            say("  result:", j.get("result") or "(none)")
            say("  phases:", " > ".join(phases) or "(none)")
            say("  overview saw it running:", saw_running,
                "| ever claimed free mid-job:", saw_free_while_running)
            last = get("/api/state").get("last_done") or {}
            say("  last_done:", json.dumps({k: last.get(k) for k in
                ("what", "ok", "took", "result", "stopped")}))
            for ln in j["lines"][-6:]:
                if ln.strip():
                    say("   |", ln.rstrip())
            return j
        time.sleep(0.4)


def main():
    home = Path(tempfile.mkdtemp(prefix="aftershot-sim-home-"))
    work = Path(tempfile.mkdtemp(prefix="aftershot-sim-"))
    card = work / "CARD"
    (card / "DCIM" / "100NIKON").mkdir(parents=True)
    dest = work / "ARCHIVE"
    dest.mkdir()

    from tests.make_fixtures import build
    made = build(work / "fixtures")
    for f in sorted(made.iterdir()):
        shutil.copy2(f, card / "DCIM" / "100NIKON" / f.name)
    say("card holds", len(list((card / "DCIM" / "100NIKON").iterdir())), "files")

    env = dict(os.environ)
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["HOMEDRIVE"] = str(home)[:2]
    env["HOMEPATH"] = str(home)[2:]
    # Importing `tests` above sets this, and the child would inherit it and
    # keep no record at all - which is the one thing this run is here to read.
    env.pop("CULLKIT_NO_JOURNAL", None)
    proc = subprocess.Popen(
        [str(PY), "-m", "cullkit.cli", "app", "--port", str(PORT), "--no-open"],
        cwd=str(REPO), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        for _ in range(60):
            try:
                get("/api/state")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("server never came up")
        say("server up on", PORT, "with HOME =", home)

        st = get("/api/state")
        say("at rest:", (st.get("program") or {}).get("state"),
            "-", (st.get("program") or {}).get("detail"))

        # 1. the rehearsal - nothing must move
        run_job(["import", str(card), "--name", "sim shoot", "--dest", str(dest)],
                "import, rehearsal")
        say("  destination after the rehearsal:",
            len(list(dest.iterdir())), "entries (0 is correct)")

        # 2. the real copy
        run_job(["import", str(card), "--name", "sim shoot", "--dest", str(dest),
                 "--apply"], "import, for real")
        shoot = dest / "sim shoot"
        say("  copied:", len(list(shoot.iterdir())) if shoot.is_dir() else "MISSING")
        say("  card untouched:",
            len(list((card / "DCIM" / "100NIKON").iterdir())), "files still there")

        # 3. two at once - the second must be told no, not silently queued
        code, res = post("/api/run", {"argv": ["shoots", str(shoot), "--sheet"],
                                      "device": "sim-a"})
        code2, res2 = post("/api/run", {"argv": ["quality", str(shoot), "--sheet"],
                                        "device": "sim-b"})
        say("")
        say("two at once: first", code, "| second", code2,
            "->", (res2.get("error") or "")[:80])
        if code == 200:
            while not get(f"/api/job?id={res['id']}")["done"]:
                time.sleep(0.4)
            j = get(f"/api/job?id={res['id']}")
            say("  separating finished rc=", j["rc"], "result:", j.get("result"))

        # 4. the sift
        run_job(["quality", str(shoot), "--sheet"], "sift")

        # 5. stopping one on purpose
        code, res = post("/api/run", {"argv": ["quality", str(shoot), "--sheet",
                                               "--refresh"], "device": "sim-device"})
        if code == 200:
            time.sleep(2.5)
            post("/api/job/stop", {"ids": [res["id"]], "device": "sim-device"})
            for _ in range(80):
                j = get(f"/api/job?id={res['id']}")
                if j["done"]:
                    break
                time.sleep(0.4)
            say("")
            say("stopped on purpose: rc=", j["rc"], "| result:", j.get("result"))
            st = get("/api/state")
            say("  program state after a stop:",
                (st.get("program") or {}).get("state"),
                "-", (st.get("program") or {}).get("detail"))

        # 6. a small command must not wake anybody
        run_job(["status"], "status (should notify nobody)")

        # 7. what the phone would be told right now
        say("")
        say("what the service worker would show:", json.dumps(get("/api/progress")))

        # 8. the record
        ev = get("/api/journal?limit=500")["events"]
        say("")
        say("=" * 72)
        say("THE RECORD -", len(ev), "events")
        say("=" * 72)
        kinds = {}
        for e in ev:
            kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
        for k in sorted(kinds):
            say(f"  {k:<18} {kinds[k]}")
        say("")
        for e in ev:
            if e.get("kind", "").startswith(("push", "job.finish", "job.error",
                                             "http.error", "job.refused")):
                say(" ", e.get("t"), e.get("kind"),
                    json.dumps({k: v for k, v in e.items()
                                if k not in ("t", "kind", "traceback", "tail")},
                               ensure_ascii=False)[:180])
        (Path(sys.argv[1]) if len(sys.argv) > 1 else Path("journal.jsonl")).write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in ev),
            encoding="utf-8")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        out = proc.stdout.read() if proc.stdout else ""
        if out.strip():
            say("")
            say("--- server console ---")
            for ln in out.splitlines()[-40:]:
                say("  ", ln)
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
        (Path(__file__).resolve().parent / "simulation.log").write_text(
            "\n".join(OUT), encoding="utf-8")


main()
