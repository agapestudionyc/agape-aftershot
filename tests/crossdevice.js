/* Does every task really survive being handed to another device?

   Paste this whole file into the browser console with the app open, or run it
   from a driver. It answers one question for every job the interface can start,
   and for the darkroom, which is not a job at all:

       you started this somewhere else — does picking it up here work?

   It is here because the answer used to be "for the one we last looked at".
   Sift folded its folder picker and Import did not. Adopting a Sift drew its
   result and adopting anything else drew nothing. Those are not bugs you find
   by using the app, because the screen in front of you is the one that works.

   Nothing is started, moved or deleted. The running jobs are described to the
   page rather than launched, `run` is replaced for the duration, and every
   global it touches is put back in the `finally`. Safe to run on a machine
   with real photographs on it, which is the only kind there is.
*/
async function crossDeviceCheck() {
  const OTHER = 'another-device';
  const FOLDER = 'E:\\Archivo\\Self Portrait';

  // Everything this touches, so it can be handed back exactly as it was.
  const keep = {
    run: window.run, confirm: window.confirm, openDoor: window.openDoor,
    showCull: window.showCull, showShoots: window.showShoots,
    running: STATE.running, flowOn: FLOW.on, at: FLOW.at, dest: FLOW.dest,
    folders: FLOW.folders.slice(), fi: FLOW.fi, view: CURRENT_VIEW,
    devFolder: DEV.folder, busy: BUSY,
  };

  const rows = [];
  let attached = null, drew = null, doorAt = null, priorPick = null;
  window.run = (argv, logEl, onDone, adopt) => {
    attached = {job: adopt && adopt.id, panel: logEl && logEl.id, onDone};
    // Nothing is launched, but the panel is opened the same way the real
    // `run` opens it - otherwise the row below cannot see whether an adopted
    // job gets a named, dismissable panel, which is half the question.
    if (logEl) { progressFor(logEl); clearResultFor(logEl); clearOtherPanels(logEl); }
  };
  window.showCull = async f => { drew = {what: 'sift result', folder: f}; };
  window.showShoots = async f => { drew = {what: 'groups', folder: f}; };
  window.openDoor = async (d, at) => { doorAt = {door: d, at}; };
  window.confirm = () => true;

  /* One job, described as if another device had started it. */
  async function handover(cmd, argv, {picker = true, result = false} = {}) {
    attached = null; drew = null;
    SEEN_JOBS.clear(); MINE.clear(); BUSY = 0;
    FLOW.on = false; FLOW.at = -1;
    if (JOB_HOME[cmd] && JOB_HOME[cmd].pick) {
      delete PICKED[JOB_HOME[cmd].pick];
      unlockPicker(JOB_HOME[cmd].pick);
    }
    go('start');

    STATE.running = [{id: 'job-' + cmd, argv, device: OTHER,
                      elapsed: 45, phase: 'working', cur: 3, of: 9, pct: 33}];
    await adoptRunningWork();

    const home = JOB_HOME[cmd] || {};
    const pickHost = home.pick && $(home.pick);
    const bar = pickHost && pickHost.nextElementSibling;
    const folded = !!(pickHost && pickHost.classList.contains('hide') &&
                      bar && bar.classList.contains('picked-lock'));

    const row = {
      job: JOB_LABEL[cmd] || cmd,
      'lands on': CURRENT_VIEW === (home.view || '?') ? home.view : 'NO (' + CURRENT_VIEW + ')',
      'reads its log': attached && attached.panel === (home.log || '').slice(1)
                        ? 'yes' : 'NO',
      'can stop it': MINE.has('job-' + cmd) ? 'yes' : 'NO',
      'shows the folder': !picker ? '—' : (folded ? 'yes' : 'NO'),
      // A panel picked up from another device has to say what it is, the same
      // as one you started here. Without a name the header reads "Working"
      // over a job that is quite specifically moving photographs.
      'says what it is': (() => {
        const l = home.log && $(home.log);
        const p = l && l.previousElementSibling;
        const n = p && p.classList.contains('prog') && p.querySelector('.pname');
        return n && n.textContent ? n.textContent : 'NO';
      })(),
    };

    if (result && attached && attached.onDone) {
      await attached.onDone({rc: 0, argv});
      row['draws the result'] = drew ? 'yes' : 'NO';
      drew = null;
      await attached.onDone({rc: 130, argv});     // stopped: nothing to draw
      row['and not when stopped'] = drew ? 'NO' : 'yes';
    } else {
      row['draws the result'] = '—';
      row['and not when stopped'] = '—';
    }
    rows.push(row);
  }

  try {
    await handover('quality', ['quality', FOLDER, '--sheet'], {result: true});
    await handover('shoots',  ['shoots', FOLDER, '--sheet'], {result: true});
    await handover('import',  ['import', 'D:\\', '--name', 'card', '--apply']);
    await handover('commit',  ['commit', FOLDER, '--apply'], {picker: false});
    await handover('work',    ['work', FOLDER, '1234', '--apply'], {picker: false});
    await handover('preview', ['preview', FOLDER], {picker: false});
    await handover('poses',   ['poses', FOLDER], {picker: false});
    await handover('clean',   ['clean', '--dry-run'], {picker: false});

    /* The darkroom is not a job: nothing runs while you sit there choosing, so
       it is resumed from what was written down rather than from what is alive.

       Which means this row cannot avoid writing a selection - and a selection
       is written into the shoot folder, next to the photographs. The first
       version wrote three invented picks straight over a real
       `selection.json` and had no way to put it back. Read it first, put it
       back in the `finally`, and never run this against a folder you are
       midway through choosing from. */
    try {
      priorPick = await (await fetch('/api/selection?folder=' +
        encodeURIComponent(FOLDER))).json();
    } catch (e) { priorPick = null; }
    await fetch('/api/selection', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder: FOLDER, mode: 'assisted', gi: 12, i: 1,
        device: OTHER, picked: ['a', 'b', 'c'], skipped: []})});
    DARKROOM_OFFERED = false; DEV.folder = ''; doorAt = null;
    go('start');
    // `loadState()` already kicks the resume off, and does not wait for it.
    // Calling it again here returns straight away because the first one has
    // marked it handled - so the only correct thing to do is wait for the work
    // that is already in flight. Sampling too early reported a failure that was
    // not there, which is its own kind of bug in a test.
    await loadState();
    for (let n = 0; n < 40 && !DEV.folder; n++)
      await new Promise(r => setTimeout(r, 100));
    rows.push({
      job: 'Develop (the darkroom)',
      'lands on': CURRENT_VIEW === 'dev' ? 'dev' : 'NO (' + CURRENT_VIEW + ')',
      'reads its log': '—',
      'can stop it': '—',
      'shows the folder': $('#dev-where').textContent === FOLDER ? 'yes' : 'NO',
      'draws the result': doorAt && doorAt.at && doorAt.at.gi === 12
                          ? 'yes (group 12)' : 'NO',
      'and not when stopped': DEV.picked.size === 3 ? 'yes (3 picks)' : 'NO',
    });

    // A chained run, mid-stage, started elsewhere.
    SEEN_JOBS.clear(); MINE.clear(); BUSY = 0; attached = null;
    FLOW.on = true; FLOW.at = 2; FLOW.dest = FOLDER; FLOW.folders = [FOLDER];
    FLOW.fi = 0;
    STATE.running = [{id: 'job-run', argv: ['quality', FOLDER], device: OTHER,
                      elapsed: 60, phase: 'faces', cur: 1, of: 4, pct: 25}];
    await adoptRunningWork();
    rows.push({
      job: 'Aftershot (a run)',
      'lands on': 'the stage panel',
      'reads its log': attached && attached.panel === 'flow-log' ? 'yes' : 'NO',
      'can stop it': MINE.has('job-run') ? 'yes' : 'NO',
      'shows the folder': '—',
      'draws the result': '—', 'and not when stopped': '—',
    });
  } finally {
    window.run = keep.run; window.confirm = keep.confirm;
    window.openDoor = keep.openDoor;
    window.showCull = keep.showCull; window.showShoots = keep.showShoots;
    STATE.running = keep.running;
    FLOW.on = keep.flowOn; FLOW.at = keep.at; FLOW.dest = keep.dest;
    FLOW.folders = keep.folders; FLOW.fi = keep.fi;
    DEV.folder = keep.devFolder; DEV.picked = new Set(); DEV.skipped = new Set();
    BUSY = keep.busy; MINE.clear(); SEEN_JOBS.clear();
    // Put the real selection back exactly as it was found. `missing` means
    // there was nothing there, and the honest restoration of nothing is an
    // empty session rather than the three invented picks this row wrote.
    if (priorPick) await fetch('/api/selection', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({folder: FOLDER, device: DEVICE_ID,
        mode: priorPick.missing ? '' : (priorPick.mode || ''),
        gi: priorPick.gi || 0, i: priorPick.i || 0,
        picked: priorPick.picked || [], skipped: priorPick.skipped || []})})
      .catch(() => {});
    await fetch('/api/darkroom/close', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: '{}'}).catch(() => {});
    document.querySelectorAll('pre.log').forEach(l => {
      if (typeof quietPanel === 'function') quietPanel(l);
    });
    go(keep.view);
  }

  const bad = rows.filter(r => Object.values(r).some(v => String(v).includes('NO')));
  console.table(rows);
  console.log(bad.length ? `${bad.length} job(s) do not hand over properly`
                         : 'every job hands over');
  return {rows, failures: bad};
}
