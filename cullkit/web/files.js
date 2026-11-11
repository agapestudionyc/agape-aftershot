/* Files - the explorer.

   A screen of its own, not a panel inside Overview. It is loaded by index.html
   after the main script and leans on a few of its names: $, el, human, flash,
   go, buzz, DEVICE_ID, and the three job hand-offs at the bottom.

   What it will not do is in cullkit/files.py and worth knowing before adding
   to this: delete is the Recycle Bin and nothing else, a name that is taken is
   never replaced, and nothing on the disk changes while a job is running. The
   server enforces all three; this file only has to say so nicely. */
(() => {
'use strict';

const FX = {
  path: null,            // '' is "This PC"; null is "never opened"
  items: [], shown: [],  // what the folder holds / what is on screen, in order
  sel: new Set(), anchor: -1,
  hist: [], hi: -1,
  view: 'grid', sort: 'name', dir: 1, hidden: false,
  clip: null,            // {paths, cut}
  home: null,            // {drives, places, local}
  q: '', undo: null, busy: 0, picking: false,
};
try {
  const saved = JSON.parse(localStorage.getItem('aftershot.fx') || '{}');
  ['view', 'sort', 'dir', 'hidden'].forEach(k => { if (k in saved) FX[k] = saved[k]; });
  FX.lastPath = saved.path || '';
} catch (e) {}
const remember = () => { try {
  localStorage.setItem('aftershot.fx', JSON.stringify(
    {view: FX.view, sort: FX.sort, dir: FX.dir, hidden: FX.hidden, path: FX.path || ''}));
} catch (e) {} };

const TOUCH = () => matchMedia('(pointer:coarse)').matches;
const enc = encodeURIComponent;
const thumbUrl = (it, size) => '/api/files/thumb?size=' + size + '&path=' + enc(it.path) +
                               '&m=' + Math.round(it.mtime || 0);
const rawUrl = (it, dl) => '/api/files/raw?path=' + enc(it.path) + (dl ? '&dl=1' : '');
/* What a browser opens by itself. Anything else that is still sound or
   picture is converted by the computer while it plays - see _files_play. */
const playUrl = it => '/api/files/play?path=' + enc(it.path);
const NATIVE = new Set(['.mp4', '.m4v', '.webm', '.mov', '.ogv',
                        '.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus']);
const get = async url => (await fetch(url, {cache: 'no-store'})).json();
const post = async (what, body) => {
  try {
    const r = await fetch('/api/files/' + what, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...body, device: DEVICE_ID})});
    return await r.json();
  } catch (e) { return {error: 'the computer did not answer'}; }
};
const svg = (d, size = 18) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" ` +
  `stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  back: '<path d="M15 5l-7 7 7 7"/>', fwd: '<path d="M9 5l7 7-7 7"/>',
  up: '<path d="M12 19V6M6 11l6-6 6 6"/>',
  grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  sort: '<path d="M7 5v14M4 16l3 3 3-3M14 7h6M14 12h4M14 17h2"/>',
  plus: '<path d="M4 7.5a2 2 0 012-2h3.5l2 2H18a2 2 0 012 2V17a2 2 0 01-2 2H6a2 2 0 01-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
  upload: '<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>',
  more: '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
  folder: '<path d="M3.5 7.5a2 2 0 012-2h4l2 2h7a2 2 0 012 2V17a2 2 0 01-2 2h-13a2 2 0 01-2-2z"/>',
  file: '<path d="M7 3.5h7l4 4V19a1.5 1.5 0 01-1.5 1.5h-9.5A1.5 1.5 0 015.5 19V5A1.5 1.5 0 017 3.5z"/><path d="M14 3.5v4h4"/>',
  drive: '<rect x="3.5" y="6" width="17" height="12" rx="2.5"/><path d="M7 14.5h.01M10.5 14.5h3"/>',
  card: '<path d="M7 3.5h8l3 3V19a1.5 1.5 0 01-1.5 1.5h-9A1.5 1.5 0 016 19V5a1.5 1.5 0 011-1.5z"/><path d="M9.5 7v3M12 7v3M14.5 8v2"/>',
  pc: '<rect x="3.5" y="5" width="17" height="11" rx="2"/><path d="M9 20h6M12 16v4"/>',
  star: '<path d="M12 4.5l2.3 4.7 5.2.8-3.7 3.6.9 5.1L12 16.3 7.3 18.7l.9-5.1L4.5 10l5.2-.8z"/>',
  video: '<rect x="3.5" y="6" width="12" height="12" rx="2"/><path d="M15.5 10.5l5-2.5v8l-5-2.5"/>',
  audio: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
};

const host = $('#v-files');
host.innerHTML = `
<div class="fx">
  <aside class="fx-side" id="fx-side"></aside>
  <section class="fx-main">
    <div class="fx-title"><b id="fx-name">Files</b><span id="fx-sub"></span></div>
    <div class="fx-bar">
      <button class="fx-btn" id="fx-back" title="Back (Alt+Left)">${svg(ICON.back)}</button>
      <button class="fx-btn" id="fx-fwd" title="Forward (Alt+Right)">${svg(ICON.fwd)}</button>
      <button class="fx-btn" id="fx-up" title="Up one folder (Backspace)">${svg(ICON.up)}<span>Up</span></button>
      <div class="fx-crumb" id="fx-crumb"></div>
      <input class="fx-q" id="fx-q" type="search" placeholder="Search here" autocomplete="off">
      <button class="fx-btn" id="fx-new" title="New folder (Ctrl+Shift+N)">${svg(ICON.plus)}<span>New folder</span></button>
      <button class="fx-btn" id="fx-upload" title="Upload files into this folder">${svg(ICON.upload)}<span>Upload</span></button>
      <button class="fx-btn" id="fx-sort" title="Sort">${svg(ICON.sort)}</button>
      <button class="fx-btn" id="fx-view" title="Grid or list"></button>
      <button class="fx-btn" id="fx-more" title="More">${svg(ICON.more)}</button>
      <input type="file" id="fx-file" multiple hidden>
    </div>
    <div class="fx-status" id="fx-status"></div>
    <div class="fx-list" id="fx-list" tabindex="0"></div>
  </section>
</div>
<div class="fx-actions" id="fx-actions"></div>`;

const list = $('#fx-list');
const say = (text, ms) => flash(text, ms || 4500);

/* ------------------------------------------------------------- going places */
async function loadHome() {
  try { FX.home = await get('/api/files/home'); } catch (e) { FX.home = FX.home || {drives: [], places: []}; }
  paintSide();
}

async function fxGo(path, {push = true, keep = false} = {}) {
  FX.busy++;
  list.classList.add('loading');
  let d;
  try {
    d = path ? await get('/api/files/list?path=' + enc(path)) : {path: '', parent: null, items: []};
  } catch (e) { d = {error: 'the computer did not answer'}; }
  FX.busy--;
  list.classList.remove('loading');
  if (d.error) { say(d.error); return false; }
  if (!path || !FX.home) await loadHome();
  FX.path = d.path;
  FX.parent = d.parent;
  FX.items = d.items;
  FX.q = ''; $('#fx-q').value = '';
  if (!keep) { FX.sel.clear(); FX.anchor = -1; }
  if (push) { FX.hist = FX.hist.slice(0, FX.hi + 1); FX.hist.push(FX.path); FX.hi = FX.hist.length - 1; }
  if (FX.path) FX.lastPath = '';        // you are somewhere now; nothing to go back to
  remember();
  paint();
  if (!keep) list.scrollTop = 0;
  return true;
}
const refresh = () => fxGo(FX.path, {push: false, keep: true});

/* ------------------------------------------------------------------ drawing */
function sorted(items) {
  const k = FX.sort, dir = FX.dir;
  const val = it => k === 'size' ? it.size : k === 'date' ? it.mtime
                  : k === 'type' ? (it.ext || '') + it.name.toLowerCase() : it.name.toLowerCase();
  return [...items].sort((a, b) => {
    if (a.dir !== b.dir) return a.dir ? -1 : 1;          // folders first, always
    const x = val(a), y = val(b);
    const c = typeof x === 'number' ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true});
    return (c || a.name.localeCompare(b.name)) * dir;
  });
}
const when = t => {
  if (!t) return '';
  const d = new Date(t * 1000), now = new Date();
  const same = d.toDateString() === now.toDateString();
  return same ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
              : d.toLocaleDateString([], {year: 'numeric', month: 'short', day: 'numeric'});
};

let SEEN = null;
function watchThumb(img) {
  if (!SEEN) SEEN = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.src = e.target.dataset.src;
    SEEN.unobserve(e.target);
  }), {root: list, rootMargin: '300px'});
  SEEN.observe(img);
}

function glyph(it) {
  if (it.dir) return ICON.folder;
  return it.kind === 'video' ? ICON.video : it.kind === 'audio' ? ICON.audio : ICON.file;
}

function cell(it, i) {
  const c = el('div', 'fx-item' + (it.dir ? ' dir' : '') + (it.hidden ? ' dim' : ''));
  c.dataset.i = i;
  c.draggable = !TOUCH();
  const pic = el('div', 'fx-pic');
  if (it.kind === 'image' || it.kind === 'raw' || it.kind === 'video') {
    const img = el('img');
    img.alt = ''; img.decoding = 'async';
    img.dataset.src = thumbUrl(it, FX.view === 'grid' ? 320 : 96);
    img.onerror = () => { pic.innerHTML = svg(ICON.file, 30); pic.appendChild(el('b', '', (it.ext || '').slice(1))); };
    img.onload = () => img.classList.add('in');
    pic.appendChild(img);
    watchThumb(img);
  } else {
    pic.innerHTML = svg(glyph(it), it.dir ? 40 : 30);
    if (!it.dir && it.ext) pic.appendChild(el('b', '', it.ext.slice(1)));
  }
  pic.appendChild(Object.assign(el('span', 'fx-tick'), {innerHTML: svg(ICON.check, 13)}));
  c.appendChild(pic);
  const name = el('div', 'fx-name', it.name);
  name.title = it.name;
  c.appendChild(name);
  if (FX.view === 'list' || FX.q) {
    c.appendChild(el('div', 'fx-col date', when(it.mtime)));
    c.appendChild(el('div', 'fx-col type', it.dir ? 'Folder' : (it.ext ? it.ext.slice(1).toUpperCase() : 'File')));
    c.appendChild(el('div', 'fx-col size', it.dir ? '' : human(it.size)));
  }
  if (FX.q) {
    const where = el('div', 'fx-where', it.path.slice(0, it.path.length - it.name.length - 1));
    name.appendChild(where);
  }
  return c;
}

/* This PC. The drives are what he comes here for, so they are the first and
   the biggest thing on it: one card each, how full it is drawn as a bar you
   can read from across the room and said in words underneath. Places come
   after, small, because they are shortcuts into those same drives. */
function driveCard(d) {
  const used = d.total ? Math.round(d.used / d.total * 100) : 0;
  const t = el('button', 'fx-drive' + (d.card ? ' card' : '') + (used > 90 ? ' full' : ''));
  t.dataset.path = d.path;
  const top = el('div', 'fx-drive-top');
  top.innerHTML = `<span class="fx-ti">${svg(d.card ? ICON.card : ICON.drive, 26)}</span>`;
  const names = el('div', 'grow');
  names.appendChild(el('b', '', d.label || (d.card ? 'Camera card' : 'Drive')));
  names.appendChild(el('small', '', d.card ? 'Camera card' : d.kind === 'removable' ? 'Removable'
                                   : 'Drive on this computer'));
  top.appendChild(names);
  top.appendChild(el('em', '', dtag(d)));
  t.appendChild(top);
  const bar = el('span', 'fx-meter big');
  bar.appendChild(Object.assign(el('i'), {style: `width:${Math.max(used, 2)}%`}));
  t.appendChild(bar);
  const foot = el('div', 'fx-drive-foot');
  foot.appendChild(el('span', '', d.total ? `${human(d.free)} free` : 'not readable'));
  foot.appendChild(el('span', '', d.total ? `${used}% of ${human(d.total)} used` : ''));
  t.appendChild(foot);
  t.onclick = () => fxGo(d.path);
  return t;
}
function paintHomeTiles() {
  const h = FX.home || {drives: [], places: []};
  const wrap = el('div', 'fx-home');
  if (FX.lastPath) {
    const c = el('button', 'fx-resume');
    c.innerHTML = svg(ICON.fwd, 16);
    c.appendChild(el('span', '', 'Back to where you were'));
    c.appendChild(el('small', '', FX.lastPath));
    c.onclick = () => fxGo(FX.lastPath);
    wrap.appendChild(c);
  }
  wrap.appendChild(el('div', 'fx-h', h.drives.length === 1 ? '1 drive' : `${h.drives.length} drives`));
  const g = el('div', 'fx-drives');
  h.drives.forEach(d => g.appendChild(driveCard(d)));
  if (!h.drives.length) g.appendChild(el('div', 'fx-empty', 'No drives could be read.'));
  wrap.appendChild(g);
  if (h.places.length) {
    wrap.appendChild(el('div', 'fx-h', 'Places'));
    const pl = el('div', 'fx-chips');
    h.places.forEach(p => {
      const b = el('button', 'fx-chip' + (p.kind === 'aftershot' ? ' star' : ''));
      b.innerHTML = svg(p.kind === 'aftershot' ? ICON.star : ICON.folder, 16);
      b.appendChild(el('span', '', p.label));
      b.title = p.path; b.dataset.path = p.path;
      b.onclick = () => fxGo(p.path);
      pl.appendChild(b);
    });
    wrap.appendChild(pl);
  }
  return wrap;
}

const driveName = d => (d.label || 'Drive') + (d.letter ? ` (${d.letter}:)` : '');
function paintTitle() {
  const root = FX.path === '' && !FX.q;
  let name = 'Files', sub = 'Every drive on this computer';
  if (!root) {
    const parts = FX.path.split(/[\\/]+/).filter(Boolean);
    name = parts.length > 1 ? parts[parts.length - 1] : FX.path;
    // The drive this folder is on: the longest mount that is the start of the path.
    const d = ((FX.home || {}).drives || [])
      .filter(x => pinside(FX.path.toLowerCase(), x.path.toLowerCase()) || x.path === '/')
      .sort((a, b) => b.path.length - a.path.length)[0];
    const atTop = d && ptrim(FX.path).toLowerCase() === ptrim(d.path).toLowerCase();
    if (atTop) name = driveName(d);
    sub = FX.q ? `Searching for “${FX.q}” in here and below`
               : (d ? (atTop ? '' : driveName(d) + ' · ') + `${human(d.free)} free` : '');
  }
  $('#fx-name').textContent = name;
  $('#fx-sub').textContent = sub;
  host.classList.toggle('fx-root', root);
}

function paint() {
  paintTitle();
  paintCrumb(); paintSide(); paintTools();
  list.innerHTML = '';
  list.className = 'fx-list ' + (FX.q ? 'list' : FX.view) + (FX.picking ? ' picking' : '');
  if (FX.path === '' && !FX.q) {
    FX.shown = [];
    list.appendChild(paintHomeTiles());
    paintStatus(); paintActions();
    return;
  }
  FX.shown = sorted(FX.items.filter(it => FX.hidden || !it.hidden || FX.q));
  if (FX.view === 'list' || FX.q) {
    const head = el('div', 'fx-head');
    [['name', 'Name'], ['date', 'Modified'], ['type', 'Type'], ['size', 'Size']].forEach(([k, label]) => {
      const b = el('button', 'fx-col ' + k + (FX.sort === k ? ' on' : ''),
                   label + (FX.sort === k ? (FX.dir > 0 ? ' ↑' : ' ↓') : ''));
      b.onclick = () => { FX.dir = FX.sort === k ? -FX.dir : 1; FX.sort = k; remember(); paint(); };
      head.appendChild(b);
    });
    list.appendChild(head);
  }
  if (!FX.shown.length) {
    list.appendChild(el('div', 'fx-empty', FX.q ? 'Nothing here by that name.' : 'This folder is empty.'));
  }
  const frag = document.createDocumentFragment();
  FX.shown.forEach((it, i) => frag.appendChild(cell(it, i)));
  list.appendChild(frag);
  paintSel(); paintStatus();
  // The observer only speaks when the browser is drawing frames. If it has
  // said nothing in a moment - a tab in the background, a window just
  // restored - the first screenful is loaded anyway rather than left blank.
  clearTimeout(paint.nudge);
  paint.nudge = setTimeout(() => {
    [...list.querySelectorAll('.fx-pic img:not([src])')].slice(0, 60).forEach(img => {
      img.src = img.dataset.src; if (SEEN) SEEN.unobserve(img); });
  }, 700);
}

function paintCrumb() {
  const c = $('#fx-crumb'); c.innerHTML = '';
  const add = (label, path, last) => {
    const b = el('button', last ? 'here' : '', label);
    b.dataset.path = path;
    b.onclick = () => fxGo(path);
    c.appendChild(b);
  };
  add(STATE.sep === '/' ? 'This Mac' : 'This PC', '', FX.path === '');
  if (FX.path) {
    const parts = FX.path.split(/[\\/]+/).filter(Boolean);
    // `C:\\Users\\me` on Windows, `/Volumes/Photos/me` on a Mac.
    const posix = FX.path.startsWith('/');
    let acc = '';
    parts.forEach((p, i) => {
      acc = posix ? acc + '/' + p
                  : (i === 0 ? p + '\\' : acc + (acc.endsWith('\\') ? '' : '\\') + p);
      c.appendChild(el('i', '', '›'));
      add(p, acc, i === parts.length - 1);
    });
  }
  c.scrollLeft = c.scrollWidth;
}

/* The strip (phone) or column (computer) beside the files. Drives first here
   too, each with how much room is left, so they are in reach from any folder. */
function paintSide() {
  const s = $('#fx-side'); s.innerHTML = '';
  const h = FX.home;
  const here = (FX.path || '').toLowerCase();
  const row = (icon, label, path, sub, meter) => {
    const on = path === '' ? FX.path === ''
      : (path === '/' ? !((FX.home || {}).drives || []).some(x => x.path !== '/' && pinside(here, x.path.toLowerCase()))
                      : pinside(here, path.toLowerCase()));
    const b = el('button', 'fx-place' + (on ? ' on' : ''));
    b.innerHTML = svg(icon, 17);
    const txt = el('span', 'fx-place-t');
    txt.appendChild(el('b', '', label));
    if (sub) txt.appendChild(el('small', '', sub));
    b.appendChild(txt);
    if (meter) b.appendChild(meter);
    b.dataset.path = path;
    b.onclick = () => fxGo(path);
    s.appendChild(b);
  };
  row(ICON.pc, 'All drives', '');
  if (!h) return;
  if (h.drives.length) s.appendChild(el('div', 'fx-h', 'Drives'));
  h.drives.forEach(d => {
    const used = d.total ? Math.round(d.used / d.total * 100) : 0;
    const m = el('span', 'fx-meter' + (used > 90 ? ' full' : ''));
    m.appendChild(Object.assign(el('i'), {style: `width:${Math.max(used, 2)}%`}));
    row(d.card ? ICON.card : ICON.drive, ((d.letter ? d.letter + ': ' : '') + (d.label || '')).trim(), d.path,
        d.total ? human(d.free) + ' free' : '', m);
  });
  if (h.places.length) s.appendChild(el('div', 'fx-h', 'Places'));
  h.places.forEach(p => row(p.kind === 'aftershot' ? ICON.star : ICON.folder, p.label, p.path));
}

function paintTools() {
  $('#fx-back').disabled = FX.hi <= 0;
  $('#fx-fwd').disabled = FX.hi >= FX.hist.length - 1;
  $('#fx-up').disabled = !FX.path;
  $('#fx-view').innerHTML = svg(FX.view === 'grid' ? ICON.list : ICON.grid);
  const here = !!FX.path;
  $('#fx-new').disabled = !here; $('#fx-upload').disabled = !here;
}

function paintSel() {
  list.querySelectorAll('.fx-item').forEach(c =>
    c.classList.toggle('sel', FX.sel.has(FX.shown[+c.dataset.i].path)));
  const cutting = FX.clip && FX.clip.cut ? new Set(FX.clip.paths) : null;
  list.querySelectorAll('.fx-item').forEach(c =>
    c.classList.toggle('cut', !!cutting && cutting.has(FX.shown[+c.dataset.i].path)));
  list.classList.toggle('picking', FX.picking && FX.sel.size > 0);
  if (!FX.sel.size) FX.picking = false;
  paintActions(); paintStatus();
}

function chosen() { return FX.shown.filter(it => FX.sel.has(it.path)); }

function paintStatus() {
  const s = $('#fx-status'); s.innerHTML = '';
  const picked = chosen();
  let text;
  if (FX.path === '' && !FX.q) text = 'Tap a drive to open it.';
  else if (picked.length) {
    const bytes = picked.reduce((n, it) => n + (it.dir ? 0 : it.size), 0);
    text = `${picked.length} selected` + (bytes ? ` · ${human(bytes)}` : '');
  } else {
    const folders = FX.shown.filter(it => it.dir).length, files = FX.shown.length - folders;
    text = (FX.q ? `“${FX.q}” — ` : '') +
      [folders ? `${folders} folder${folders > 1 ? 's' : ''}` : '',
       files ? `${files} file${files > 1 ? 's' : ''}` : ''].filter(Boolean).join(', ') || 'empty';
    const hid = FX.items.filter(it => it.hidden).length;
    if (hid && !FX.hidden && !FX.q) text += ` · ${hid} hidden`;
    if (FX.more) text += ' · stopped early, narrow the search';
  }
  s.appendChild(el('span', '', text));
  // How to use what is on screen, for as long as nothing is selected.
  if (FX.path && !picked.length && !FX.clip && !FX.undo)
    s.appendChild(el('span', 'fx-hint', TOUCH() ? 'tap to open · hold to select'
                                               : 'double-click to open · right-click for more'));
  if (FX.clip) {
    const b = el('button', 'fx-link', `${FX.clip.cut ? 'Move' : 'Paste'} ${FX.clip.paths.length} here`);
    b.onclick = paste; b.disabled = !FX.path;
    s.appendChild(b);
    const x = el('button', 'fx-link quiet', 'forget'); x.onclick = () => { FX.clip = null; paintSel(); };
    s.appendChild(x);
  }
  if (FX.undo) {
    const u = el('button', 'fx-link', 'Undo ' + FX.undo.label);
    u.onclick = undo; s.appendChild(u);
  }
}

function paintActions() {
  const bar = $('#fx-actions'); bar.innerHTML = '';
  const picked = chosen();
  bar.classList.toggle('on', picked.length > 0);
  if (!picked.length) return;
  const add = (label, fn, cls) => { const b = el('button', 'act ghost ' + (cls || ''), label);
    b.onclick = fn; bar.appendChild(b); };
  bar.appendChild(el('span', 'fx-n', String(picked.length)));
  if (picked.length === 1) add('Open', () => open(picked[0]));
  add('Copy', () => clip(false));
  add('Cut', () => clip(true));
  if (picked.length === 1) add('Rename', () => rename(picked[0]));
  if (picked.some(it => !it.dir)) add('Download', download);
  add('Bin', bin, 'danger');
  add('⋯', e => menu(e, picked[0]));
  const x = el('button', 'act ghost', '✕'); x.title = 'Clear the selection';
  x.onclick = () => { FX.sel.clear(); paintSel(); }; bar.appendChild(x);
}

/* -------------------------------------------------------------- small dialogs */
function sheet(build) {
  return new Promise(res => {
    const back = el('div', 'fx-veil');
    const box = el('div', 'fx-sheet');
    back.appendChild(box);
    const done = v => { back.remove(); removeEventListener('keydown', key, true); res(v); };
    const key = e => { if (e.key === 'Escape') { e.stopPropagation(); done(null); } };
    addEventListener('keydown', key, true);
    back.addEventListener('pointerdown', e => { if (e.target === back) done(null); });
    build(box, done);
    document.body.appendChild(back);
  });
}
function ask(title, value, okLabel, selectStem) {
  return sheet((box, done) => {
    box.appendChild(el('h3', '', title));
    const inp = el('input'); inp.type = 'text'; inp.value = value || ''; inp.spellcheck = false;
    box.appendChild(inp);
    const row = el('div', 'row');
    const ok = el('button', 'act', okLabel || 'OK'), no = el('button', 'act ghost', 'Cancel');
    ok.onclick = () => done(inp.value.trim()); no.onclick = () => done(null);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') ok.click(); });
    row.appendChild(ok); row.appendChild(no); box.appendChild(row);
    setTimeout(() => { inp.focus();
      const dot = selectStem ? inp.value.lastIndexOf('.') : -1;
      inp.setSelectionRange(0, dot > 0 ? dot : inp.value.length); }, 30);
  });
}
function confirmBox(title, lines, okLabel, danger) {
  return sheet((box, done) => {
    box.appendChild(el('h3', '', title));
    lines.forEach(t => box.appendChild(el('p', '', t)));
    const row = el('div', 'row');
    const ok = el('button', 'act' + (danger ? ' danger' : ''), okLabel), no = el('button', 'act ghost', 'Cancel');
    ok.onclick = () => done(true); no.onclick = () => done(null);
    row.appendChild(ok); row.appendChild(no); box.appendChild(row);
    setTimeout(() => ok.focus(), 30);
  });
}

/* ------------------------------------------------------------------- actions */
function report(got, verb) {
  if (got.error) { say(got.error, 7000); return false; }
  const skipped = got.skipped || [];
  const n = (got.moved || got.copied || got.trashed || []).length;
  let text = n ? `${verb} ${n} item${n > 1 ? 's' : ''}` : `Nothing was ${verb.toLowerCase()}`;
  if (skipped.length) text += ` — ${skipped.length} skipped: ${skipped[0].name} (${skipped[0].why})` +
                              (skipped.length > 1 ? ' and others' : '');
  say(text, skipped.length ? 8000 : 4000);
  return n > 0;
}

function clip(cut) {
  const p = chosen().map(it => it.path);
  if (!p.length) return;
  FX.clip = {paths: p, cut};
  say(`${p.length} item${p.length > 1 ? 's' : ''} ready to ${cut ? 'move' : 'copy'} — open the folder they go to and press Paste`, 5000);
  FX.sel.clear(); paintSel();
}

async function paste() {
  if (!FX.clip || !FX.path) return;
  const {paths, cut} = FX.clip;
  await moveOrCopy(paths, FX.path, !cut);
  if (cut) FX.clip = null;
  await refresh();
}

async function moveOrCopy(paths, dest, copy) {
  const got = await post(copy ? 'copy' : 'move', {paths, dest});
  const ok = report(got, copy ? 'Copied' : 'Moved');
  if (ok && !copy) {
    // Enough to put them back: where each one came from.
    FX.undo = {label: 'the move', run: async () => {
      const byHome = {};
      (got.moved || []).forEach(m => {
        const home = pparent(m.src) || m.src;
        (byHome[home] = byHome[home] || []).push(m.dst);
      });
      for (const home in byHome) await post('move', {paths: byHome[home], dest: home});
    }};
  }
  return ok;
}

async function undo() {
  const u = FX.undo; FX.undo = null;
  if (!u) return;
  await u.run();
  say('Put back.', 3000);
  refresh();
}

async function rename(it) {
  const name = await ask('Rename', it.name, 'Rename', !it.dir);
  if (!name || name === it.name) return;
  const got = await post('rename', {path: it.path, name});
  if (got.error) return say(got.error, 6000);
  const old = it.name;
  FX.undo = {label: 'the rename', run: () => post('rename', {path: got.path, name: old})};
  FX.sel.clear(); FX.sel.add(got.path);
  refresh();
}

async function newFolder() {
  if (!FX.path) return;
  const name = await ask('New folder', 'New folder', 'Create');
  if (!name) return;
  const got = await post('mkdir', {path: FX.path, name});
  if (got.error) return say(got.error, 6000);
  FX.sel.clear(); FX.sel.add(got.path);
  refresh();
}

async function bin() {
  const picked = chosen();
  if (!picked.length) return;
  const what = picked.length === 1 ? `“${picked[0].name}”` : `${picked.length} items`;
  const yes = await confirmBox(`Send ${what} to the Recycle Bin?`,
    ['Nothing is erased. It goes to the Windows Recycle Bin on this computer, ' +
     'and can be put back from there until the bin is emptied.'], 'Send to the bin', true);
  if (!yes) return;
  const got = await post('trash', {paths: picked.map(it => it.path)});
  report(got, 'Binned');
  FX.sel.clear();
  refresh();
}

function download() {
  const files = chosen().filter(it => !it.dir);
  files.forEach((it, i) => setTimeout(() => {
    const a = el('a'); a.href = rawUrl(it, true); a.download = it.name;
    document.body.appendChild(a); a.click(); a.remove();
  }, i * 350));
  if (chosen().some(it => it.dir)) say('Folders are not downloaded — open one and pick its files.', 5000);
}

/* One file up, with its progress. XMLHttpRequest rather than fetch, because
   fetch still cannot say how much of a body has been sent - and a video going
   up from a phone with no bar looks exactly like a video not going up. */
function sendFile(f, dest, onProgress) {
  return new Promise(res => {
    const x = new XMLHttpRequest();
    FX.sending = x;
    x.open('POST', '/api/files/upload');
    x.setRequestHeader('X-Dest', enc(dest));
    x.setRequestHeader('X-Name', enc(f.name));
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = e => { if (e.lengthComputable) onProgress(e.loaded, e.total); };
    x.onload = () => { try { res(JSON.parse(x.responseText)); } catch (e) { res({error: 'the computer gave no answer'}); } };
    x.onerror = () => res({error: 'the upload was interrupted'});
    x.onabort = () => res({error: 'stopped'});
    x.send(f);
  });
}
async function upload(files) {
  if (!FX.path || !files || !files.length) return;
  const dest = FX.path;
  const total = files.reduce((n, f) => n + f.size, 0);
  let done = 0, sent = 0, failed = '';
  const box = el('div', 'fx-up'); const label = el('span'); const bar = el('i');
  const track = el('b'); track.appendChild(bar);
  const stop = el('button', 'fx-link quiet', 'stop'); stop.onclick = () => FX.sending && FX.sending.abort();
  box.appendChild(label); box.appendChild(track); box.appendChild(stop);
  host.querySelector('.fx-main').insertBefore(box, $('#fx-status'));
  for (const f of files) {
    const got = await sendFile(f, dest, (a) => {
      const pct = total ? Math.round((sent + a) / total * 100) : 0;
      bar.style.width = pct + '%';
      label.textContent = `Uploading ${f.name} — ${done + 1} of ${files.length} · ${human(sent + a)} of ${human(total)}`;
    });
    if (got.error) { failed = got.error; break; }
    sent += f.size; done++;
  }
  FX.sending = null;
  box.remove();
  say(failed ? `${done} uploaded, then: ${failed}` : `${done} file${done > 1 ? 's' : ''} uploaded to ${pname(dest) || dest}`, 6000);
  if (FX.path === dest) refresh();
}

async function props(it) {
  const d = await get('/api/files/info?path=' + enc(it.path));
  if (d.error) return say(d.error);
  sheet((box, done) => {
    box.classList.add('wide');
    box.appendChild(el('h3', '', d.name));
    const t = el('div', 'fx-props');
    const row = (k, v) => { if (v === undefined || v === '' || v === null) return;
      t.appendChild(el('span', '', k)); t.appendChild(el('b', '', String(v))); };
    row('Where', d.path);
    row('Kind', d.dir ? 'Folder' : (d.ext ? d.ext.slice(1).toUpperCase() + ' file' : 'File'));
    row('Size', human(d.size) + (d.partial ? ' and counting — stopped early' : ''));
    if (d.dir) row('Holds', `${d.files} files, ${d.folders} folders`);
    if (d.pixels) row('Pixels', d.pixels.join(' × '));
    row('Modified', d.mtime ? new Date(d.mtime * 1000).toLocaleString() : '');
    row('Created', d.created ? new Date(d.created * 1000).toLocaleString() : '');
    Object.entries(d.exif || {}).slice(0, 12).forEach(([k, v]) => row(k, v));
    box.appendChild(t);
    const r = el('div', 'row');
    const copy = el('button', 'act ghost', 'Copy the path');
    copy.onclick = () => copyPath(d.path);
    const ok = el('button', 'act', 'Close'); ok.onclick = () => done(null);
    r.appendChild(ok); r.appendChild(copy); box.appendChild(r);
  });
}
async function copyPath(p) {
  try { await navigator.clipboard.writeText(p); say('Path copied.', 2500); }
  catch (e) { prompt('Copy this path', p); }
}

/* ------------------------------------------------------------- the right-click */
function menu(e, it) {
  closeMenu();
  const m = el('div', 'fx-menu'); m.id = 'fx-menu';
  const picked = chosen();
  const many = picked.length > 1;
  const add = (label, fn, cls) => { const b = el('button', cls || '', label);
    b.onclick = () => { closeMenu(); fn(); }; m.appendChild(b); };
  const rule = () => m.appendChild(el('hr'));
  if (it && !many) {
    add(it.dir ? 'Open' : 'Quick look', () => open(it));
    if (FX.home && FX.home.local) {
      add('Open on this computer', async () => { const g = await post('open', {path: it.path}); if (g.error) say(g.error); });
      add('Show in Windows Explorer', async () => { const g = await post('reveal', {path: it.path}); if (g.error) say(g.error); });
    }
    if (!it.dir) add('Download', download);
    if (it.dir) {
      rule();
      add('Sift this folder', () => handOff('cull', it.path));
      add('Separate projects here', () => handOff('shoots', it.path));
      add('Open in the Darkroom', () => handOff('dev', it.path), 'red');
    }
    rule();
  }
  if (picked.length) {
    add('Copy', () => clip(false)); add('Cut', () => clip(true));
    if (!many) add('Rename', () => rename(picked[0]));
    add('Copy the path', () => copyPath(picked.map(p => p.path).join('\n')));
    add(many ? `Send ${picked.length} to the Recycle Bin` : 'Send to the Recycle Bin', bin, 'danger');
    rule();
    if (!many) add('Properties', () => props(picked[0]));
  }
  if (FX.clip && FX.path) add(`${FX.clip.cut ? 'Move' : 'Paste'} ${FX.clip.paths.length} here`, paste);
  if (FX.path) {
    add('New folder', newFolder);
    add('Upload files here', () => $('#fx-file').click());
    add('Select all', selectAll);
    add('Sort by…', () => sortMenu({currentTarget: $('#fx-more')}));
  }
  add(FX.hidden ? 'Hide hidden items' : 'Show hidden items', () => { FX.hidden = !FX.hidden; remember(); paint(); });
  add('Refresh', refresh);
  document.body.appendChild(m);
  const r = m.getBoundingClientRect();
  const px = e.clientX ?? (e.target.getBoundingClientRect().left), py = e.clientY ?? (e.target.getBoundingClientRect().top);
  m.style.left = Math.max(8, Math.min(px, innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(py, innerHeight - r.height - 8)) + 'px';
  setTimeout(() => addEventListener('pointerdown', outside, true), 0);
}
function outside(e) { if (!e.target.closest('#fx-menu')) closeMenu(); }
function closeMenu() {
  const m = $('#fx-menu'); if (m) m.remove();
  removeEventListener('pointerdown', outside, true);
}

function sortMenu(e) {
  closeMenu();
  const m = el('div', 'fx-menu'); m.id = 'fx-menu';
  [['name', 'Name'], ['date', 'Date modified'], ['size', 'Size'], ['type', 'Type']].forEach(([k, label]) => {
    const b = el('button', FX.sort === k ? 'on' : '', label + (FX.sort === k ? (FX.dir > 0 ? '  ↑' : '  ↓') : ''));
    b.onclick = () => { FX.dir = FX.sort === k ? -FX.dir : 1; FX.sort = k; remember(); closeMenu(); paint(); };
    m.appendChild(b);
  });
  document.body.appendChild(m);
  const r = e.currentTarget.getBoundingClientRect(), w = m.getBoundingClientRect();
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - w.width - 8)) + 'px';
  m.style.top = Math.min(r.bottom + 6, innerHeight - w.height - 8) + 'px';
  setTimeout(() => addEventListener('pointerdown', outside, true), 0);
}

/* Hand a folder to one of the jobs, as if it had been picked there. */
function handOff(view, path) {
  if (typeof BUSY !== 'undefined' && (BUSY > 0 || FLOW.on)) return say('Something is already running — finish it first.', 5000);
  if (view === 'dev') { LAST_RUN = null; return devEnter(path); }
  const id = view === 'cull' ? '#pick-cull' : '#pick-shoot';
  PICKED[id] = path;
  if (view === 'cull') CULL_DIR = path; else SHOOT_DIR = path;
  LAST_RUN = null;
  go(view);
  lockPicker(id);
}

/* ---------------------------------------------------------------- quick look */
let LOOK = null;
function look(it) {
  closeLook();
  const files = FX.shown.filter(x => !x.dir);
  let at = files.indexOf(it);
  const v = el('div', 'fx-look'); v.id = 'fx-look';
  const top = el('div', 'fx-look-bar');
  const title = el('b'); const meta = el('span');
  const dl = el('a', 'act ghost', 'Download');
  const close = el('button', 'act ghost', 'Close'); close.onclick = closeLook;
  top.appendChild(title); top.appendChild(meta); top.appendChild(dl); top.appendChild(close);
  const stage = el('div', 'fx-look-stage');
  const prev = el('button', 'fx-look-nav l', '‹'), next = el('button', 'fx-look-nav r', '›');
  v.appendChild(top); v.appendChild(stage); v.appendChild(prev); v.appendChild(next);
  const show = async () => {
    const f = files[at];
    title.textContent = f.name;
    meta.textContent = `${human(f.size)} · ${when(f.mtime)} · ${at + 1} of ${files.length}`;
    dl.href = rawUrl(f, true); dl.download = f.name;
    stage.innerHTML = '';
    prev.disabled = at === 0; next.disabled = at === files.length - 1;
    FX.sel.clear(); FX.sel.add(f.path); paintSel();
    if (f.kind === 'image' || f.kind === 'raw') {
      const img = el('img'); img.alt = f.name;
      img.onerror = () => { stage.innerHTML = ''; stage.appendChild(el('p', '', 'No preview could be read from this file.')); };
      img.src = thumbUrl(f, 2400);
      stage.appendChild(img);
    } else if (f.kind === 'video' || f.kind === 'audio') {
      const m = el(f.kind);
      m.controls = true; m.autoplay = true; m.playsInline = true;
      const wrap = el('div', 'fx-media'); wrap.appendChild(m);
      const note = el('p', 'fx-medianote'); wrap.appendChild(note);
      // Try the file as it is. If the browser cannot open it - or it is a
      // kind it never can - ask the computer to convert it as it plays.
      let converted = false;
      const convert = () => {
        if (converted) { note.textContent = 'This one could not be played, even converted. Download it instead.'; return; }
        converted = true;
        note.textContent = 'Converting as it plays — it will start in a moment, and cannot be skipped through.';
        m.src = playUrl(f);
      };
      m.onerror = convert;
      if (NATIVE.has(f.ext)) m.src = rawUrl(f); else convert();
      stage.appendChild(wrap);
    } else if (f.kind === 'pdf') {
      const fr = el('iframe'); fr.src = rawUrl(f); stage.appendChild(fr);
    } else if (f.kind === 'text') {
      const pre = el('pre', '', 'reading…'); stage.appendChild(pre);
      try {
        const r = await fetch(rawUrl(f), {headers: {Range: 'bytes=0-262143'}});
        pre.textContent = await r.text() + (f.size > 262144 ? '\n\n… the first 256 KB of ' + human(f.size) : '');
      } catch (e) { pre.textContent = 'could not be read'; }
    } else {
      const card = el('div', 'fx-nolook');
      card.innerHTML = svg(ICON.file, 54);
      card.appendChild(el('b', '', f.name));
      card.appendChild(el('p', '', 'There is no preview for this kind of file. Download it' +
        (FX.home && FX.home.local ? ', or open it on this computer.' : '.')));
      if (FX.home && FX.home.local) {
        const o = el('button', 'act', 'Open on this computer');
        o.onclick = async () => { const g = await post('open', {path: f.path}); if (g.error) say(g.error); };
        card.appendChild(o);
      }
      stage.appendChild(card);
    }
  };
  const step = d => { const n = at + d; if (n < 0 || n >= files.length) return; at = n; show(); };
  prev.onclick = () => step(-1); next.onclick = () => step(1);
  let x0 = null;
  stage.addEventListener('touchstart', e => { x0 = e.touches[0].clientX; }, {passive: true});
  stage.addEventListener('touchend', e => {
    if (x0 === null) return;
    const dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (Math.abs(dx) > 70) step(dx < 0 ? 1 : -1);
  }, {passive: true});
  LOOK = {step};
  document.body.appendChild(v);
  show();
}
function closeLook() { const v = $('#fx-look'); if (v) v.remove(); LOOK = null; }

function open(it) { return it.dir ? fxGo(it.path) : look(it); }

/* ---------------------------------------------------------------- selection */
function selectAll() { FX.shown.forEach(it => FX.sel.add(it.path)); FX.picking = true; paintSel(); }
function pick(i, e) {
  const it = FX.shown[i];
  if (e && e.shiftKey && FX.anchor >= 0) {
    const [a, b] = [Math.min(FX.anchor, i), Math.max(FX.anchor, i)];
    if (!(e.ctrlKey || e.metaKey)) FX.sel.clear();
    for (let n = a; n <= b; n++) FX.sel.add(FX.shown[n].path);
  } else if ((e && (e.ctrlKey || e.metaKey)) || FX.picking) {
    FX.sel.has(it.path) ? FX.sel.delete(it.path) : FX.sel.add(it.path);
    FX.anchor = i;
  } else {
    FX.sel.clear(); FX.sel.add(it.path); FX.anchor = i;
  }
  paintSel();
}

const itemOf = e => { const c = e.target.closest('.fx-item'); return c ? +c.dataset.i : -1; };

list.addEventListener('click', e => {
  const i = itemOf(e);
  if (i < 0) { if (!e.target.closest('button')) { FX.sel.clear(); paintSel(); } return; }
  // A finger opens with one tap and selects with a hold; a mouse selects with
  // one click and opens with two. The tick in the corner selects for either.
  if (e.target.closest('.fx-tick')) { FX.picking = true; return pick(i, {ctrlKey: true}); }
  if (TOUCH() && !FX.picking) return open(FX.shown[i]);
  pick(i, e);
});
list.addEventListener('dblclick', e => {
  const i = itemOf(e);
  if (i >= 0 && !TOUCH()) open(FX.shown[i]);
});
list.addEventListener('contextmenu', e => {
  e.preventDefault();
  const i = itemOf(e);
  if (i >= 0 && !FX.sel.has(FX.shown[i].path)) { FX.sel.clear(); FX.sel.add(FX.shown[i].path); FX.anchor = i; paintSel(); }
  if (i < 0) { FX.sel.clear(); paintSel(); }
  menu(e, i >= 0 ? FX.shown[i] : null);
});
// Press and hold, for a screen with no right button.
let HOLD = 0;
list.addEventListener('touchstart', e => {
  const i = itemOf(e); if (i < 0) return;
  clearTimeout(HOLD);
  HOLD = setTimeout(() => { buzz(14); FX.picking = true; pick(i, {ctrlKey: true}); HOLD = -1; }, 480);
}, {passive: true});
['touchmove', 'touchend', 'touchcancel'].forEach(t => list.addEventListener(t, () => {
  if (HOLD > 0) clearTimeout(HOLD);
}, {passive: true}));

/* Dragging: onto a folder, a breadcrumb or a place moves; with Ctrl it copies.
   Files dragged in from outside the browser are uploaded. */
list.addEventListener('dragstart', e => {
  const i = itemOf(e); if (i < 0) return;
  if (!FX.sel.has(FX.shown[i].path)) { FX.sel.clear(); FX.sel.add(FX.shown[i].path); paintSel(); }
  e.dataTransfer.setData('application/x-aftershot', '1');
  e.dataTransfer.effectAllowed = 'copyMove';
});
function dropTarget(e) {
  const c = e.target.closest('.fx-item.dir');
  if (c) return {el: c, path: FX.shown[+c.dataset.i].path};
  const b = e.target.closest('.fx-crumb button, .fx-place, .fx-tile');
  if (b && b.dataset.path) return {el: b, path: b.dataset.path};
  return null;
}
let OVER = null;
const unmark = () => { if (OVER) OVER.classList.remove('drop'); OVER = null; };
host.addEventListener('dragover', e => {
  const mine = [...e.dataTransfer.types].includes('application/x-aftershot');
  const t = dropTarget(e);
  if (mine && !t) return unmark();
  if (!mine && !FX.path) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = mine ? (e.ctrlKey ? 'copy' : 'move') : 'copy';
  unmark();
  OVER = t ? t.el : list; OVER.classList.add('drop');
});
host.addEventListener('dragleave', e => { if (!host.contains(e.relatedTarget)) unmark(); });
host.addEventListener('drop', async e => {
  const mine = [...e.dataTransfer.types].includes('application/x-aftershot');
  const t = dropTarget(e);
  unmark();
  if (mine) {
    if (!t) return;
    e.preventDefault();
    const paths = chosen().map(it => it.path).filter(p => p !== t.path);
    if (!paths.length) return;
    await moveOrCopy(paths, t.path, e.ctrlKey);
    FX.sel.clear();
    return refresh();
  }
  if (e.dataTransfer.files && e.dataTransfer.files.length && FX.path) {
    e.preventDefault();
    upload([...e.dataTransfer.files]);
  }
});

/* ------------------------------------------------------------------ toolbar */
$('#fx-back').onclick = () => { if (FX.hi > 0) fxGo(FX.hist[--FX.hi], {push: false}); };
$('#fx-fwd').onclick = () => { if (FX.hi < FX.hist.length - 1) fxGo(FX.hist[++FX.hi], {push: false}); };
$('#fx-up').onclick = () => { if (FX.path) fxGo(FX.parent || ''); };
$('#fx-view').onclick = () => { FX.view = FX.view === 'grid' ? 'list' : 'grid'; remember(); paint(); };
$('#fx-sort').onclick = sortMenu;
$('#fx-new').onclick = newFolder;
$('#fx-upload').onclick = () => $('#fx-file').click();
$('#fx-file').onchange = e => { upload([...e.target.files]); e.target.value = ''; };
$('#fx-more').onclick = e => menu(e, chosen()[0] || null);

let QT = 0;
$('#fx-q').addEventListener('input', e => {
  clearTimeout(QT);
  const q = e.target.value.trim();
  QT = setTimeout(async () => {
    if (!q) { FX.q = ''; FX.more = false; return refresh(); }
    if (!FX.path) return say('Open a drive or a folder first, then search inside it.', 4000);
    list.classList.add('loading');
    const d = await get('/api/files/search?path=' + enc(FX.path) + '&q=' + enc(q));
    list.classList.remove('loading');
    if ($('#fx-q').value.trim() !== q) return;          // a newer search is on its way
    FX.q = q; FX.more = d.more;
    FX.itemsBefore = FX.itemsBefore || FX.items;
    FX.items = d.items; FX.sel.clear();
    paint();
  }, 320);
});

/* ----------------------------------------------------------------- keyboard */
addEventListener('keydown', e => {
  if (host.classList.contains('hide')) return;
  if ($('.fx-veil')) return;
  if (LOOK) {
    if (e.key === 'Escape' || e.key === ' ') { closeLook(); e.preventDefault(); }
    if (e.key === 'ArrowRight') LOOK.step(1);
    if (e.key === 'ArrowLeft') LOOK.step(-1);
    return;
  }
  const typing = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA';
  if (typing) { if (e.key === 'Escape') e.target.blur(); return; }
  const ctrl = e.ctrlKey || e.metaKey, picked = chosen();
  const cur = FX.anchor >= 0 ? FX.anchor : -1;
  const move = n => { if (!FX.shown.length) return;
    const i = Math.max(0, Math.min(FX.shown.length - 1, cur + n));
    pick(i, e.shiftKey ? {shiftKey: true} : null);
    if (!e.shiftKey) FX.anchor = i;
    const c = list.querySelector(`.fx-item[data-i="${i}"]`); if (c) c.scrollIntoView({block: 'nearest'}); };
  const cols = () => { if (list.classList.contains('list')) return 1;
    const first = list.querySelector('.fx-item'); if (!first) return 1;
    return Math.max(1, Math.round(list.clientWidth / first.getBoundingClientRect().width)); };
  let used = true;
  if (e.key === 'Escape') { closeMenu(); FX.sel.clear(); paintSel(); }
  else if (e.altKey && e.key === 'ArrowLeft') $('#fx-back').click();
  else if (e.altKey && e.key === 'ArrowRight') $('#fx-fwd').click();
  else if (e.altKey && e.key === 'ArrowUp') $('#fx-up').click();
  else if (e.key === 'Backspace') $('#fx-up').click();
  else if (e.key === 'ArrowRight') move(1);
  else if (e.key === 'ArrowLeft') move(-1);
  else if (e.key === 'ArrowDown') move(cols());
  else if (e.key === 'ArrowUp') move(-cols());
  else if (e.key === 'Home') move(-1e6);
  else if (e.key === 'End') move(1e6);
  else if (e.key === 'Enter' && picked.length === 1) open(picked[0]);
  else if (e.key === ' ' && picked.length === 1 && !picked[0].dir) look(picked[0]);
  else if (e.key === 'F2' && picked.length === 1) rename(picked[0]);
  else if (e.key === 'Delete' && picked.length) bin();
  else if (e.key === 'F5') refresh();
  else if (ctrl && e.key.toLowerCase() === 'a') selectAll();
  else if (ctrl && e.key.toLowerCase() === 'c' && picked.length) clip(false);
  else if (ctrl && e.key.toLowerCase() === 'x' && picked.length) clip(true);
  else if (ctrl && e.key.toLowerCase() === 'v') paste();
  else if (ctrl && e.key.toLowerCase() === 'z') undo();
  else if (ctrl && e.shiftKey && e.key.toLowerCase() === 'n') newFolder();
  else if (ctrl && e.key.toLowerCase() === 'f') $('#fx-q').focus();
  else if (!ctrl && !e.altKey && e.key.length === 1 && /\S/.test(e.key)) {
    // Type a few letters and land on the first name that starts with them.
    const now = Date.now();
    FX.typed = (now - (FX.typedAt || 0) < 900 ? (FX.typed || '') : '') + e.key.toLowerCase();
    FX.typedAt = now;
    const i = FX.shown.findIndex(it => it.name.toLowerCase().startsWith(FX.typed));
    if (i >= 0) { pick(i, null); const c = list.querySelector(`.fx-item[data-i="${i}"]`); if (c) c.scrollIntoView({block: 'nearest'}); }
  } else used = false;
  if (used) { e.preventDefault(); e.stopImmediatePropagation(); }
}, true);

/* -------------------------------------------------------------------- doors */
/* Back, the way a phone's file manager means it: shut whatever is open on
   top, then the search, then one folder up - every time, all the way to This
   PC - and only from there does the app leave Files.

   It is "up", not "the folder you were in before". That was tried first and
   was two rules in one arrow: sometimes it went up, sometimes sideways,
   depending on how you had got there. One rule can be learned in one press.
   (The toolbar's back and forward on a computer still walk the history.) */
function stepBack() {
  if ($('.fx-veil')) { $('.fx-veil').dispatchEvent(new PointerEvent('pointerdown')); return true; }
  if (LOOK) { closeLook(); return true; }
  if ($('#fx-menu')) { closeMenu(); return true; }
  if (FX.picking && FX.sel.size) { FX.sel.clear(); paintSel(); return true; }
  if (FX.q) { $('#fx-q').value = ''; FX.q = ''; FX.more = false; refresh(); return true; }
  if (FX.path) { fxGo(FX.parent || ''); return true; }
  return false;
}
/* The phone's own back gesture does the same thing. One spare entry is kept
   in the browser's history while Files is open; the gesture uses it up, and
   as long as there was somewhere to go it is put straight back. */
const arm = () => { try { history.pushState({fx: 1}, ''); } catch (e) {} };
addEventListener('popstate', () => {
  if (host.classList.contains('hide')) return;
  if (stepBack()) arm();
  else if (typeof goBack === 'function') goBack();
});

window.FILES = {
  back: stepBack,
  /* Called by go('files'). Opens where it was left, or at the top. */
  enter() {
    arm();
    if (FX.skip) return loadHome();
    if (FX.path === null) return fxGo('');
    loadHome();
    if (!FX.q) refresh();
  },
  open(path) { FX.skip = true; go('files'); FX.skip = false; return fxGo(path || ''); },
  leave() { closeMenu(); closeLook(); },
};
})();
