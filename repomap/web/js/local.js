// Trang PC (repomap --local): Connect (cau hinh, thu muc tren server, deploy + ket noi), Saved servers (toi da 5 preset).
// Server local lam phan ssh/scp (mo cua so console de nhap password); trang nay chi goi /api/local/* va poll trang thai.
// Nut chinh doi theo trang thai: Connect -> Cancel (dang noi) -> Disconnect (da noi); moi luc chi 1 ket noi.

import { $, esc } from './util.js';

const FORM = $('#cfg-form'), DIR = $('#dir-in'), WS = $('#ws-in'), MAIN = $('#connect');
const KEYS = ['host', 'user', 'port', 'srv'];
let st = null, tab = 'connect', busy = false;

async function call(ep, body) {
  try {
    const r = await fetch('/api/local/' + ep, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    return await r.json();
  } catch (e) {
    return { error: 'repomap PC is not running any more. Start it again (repomap-pc.cmd).' };
  }
}

const say = (el, cls, html) => { el.className = 'msg ' + cls; el.innerHTML = html; };
const open = url => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>`;
const spin = '<i class="spin"></i>';

// ---------------------------------------------------------------- configuration

const cfg = () => ({ ...Object.fromEntries(KEYS.map(k => [k, FORM.elements[k].value.trim()])), dir: DIR.value.trim(),
  ws: WS.value.trim() });

// "Workspace root" giong local.py remote_ws(): trong = ~ (home cua user ssh); duong dan tuong doi tinh tu ~
function remoteWs(v) {
  v = v.trim();
  if (v.length > 1) v = v.replace(/\/+$/, '');
  if (!v || v === '~') return { show: '~', home: true };
  if (!/^[\w.~/+-]+$/.test(v) || v.startsWith('-'))
    return { err: 'Use a path made of letters, digits and . _ - / ~ (no spaces).' };
  if (/^~[^/]/.test(v)) return { err: '~user paths are not supported, write the full path.' };
  return { show: v.startsWith('/') || v.startsWith('~') ? v : '~/' + v };
}

// "Server folder" giong local.py remote_dir(): trong = ~/.bin; ten = canh ~/.bin (tao neu chua co); duong dan = phai co san
function remoteDir(v) {
  v = v.trim();
  if (v.length > 1) v = v.replace(/\/+$/, '');
  if (!v) return { show: '~/.bin', path: false, def: true };
  if (!/^[\w.~/+-]+$/.test(v) || v.startsWith('-') || v.split('/').includes('..'))
    return { err: 'Use a folder name or a path made of letters, digits and . _ - / ~ (no spaces, no "..").' };
  if (!v.includes('/') && v !== '~') return v === '.' ? { err: '"." is not a folder name.' } : { show: '~/' + v, path: false };
  if (/^~[^/]/.test(v)) return { err: '~user paths are not supported, write the full path.' };
  return { show: v.startsWith('/') || v.startsWith('~') ? v : '~/' + v, path: true };
}

// kiem tra giong server (local.py check_cfg); tra ve {} neu hop le, nguoc lai {truong: loi}
function problems(c) {
  const bad = {}, port = v => /^\d+$/.test(v) && +v >= 1 && +v <= 65535;
  if (!/^[A-Za-z0-9.-]+$/.test(c.host)) bad.host = 'SSH host is missing or invalid';
  if (!/^[\w.-]+$/.test(c.user)) bad.user = 'SSH user is missing or invalid';
  if (c.port && !port(c.port)) bad.port = 'SSH port must be a number from 1 to 65535';
  if (c.srv && !port(c.srv)) bad.srv = 'Port must be a number from 1 to 65535';
  return bad;
}

const ready = () => !Object.keys(problems(cfg())).length;
const target = c => `${c.user}@${c.host}:${c.port || 22}`;
const srvPort = () => cfg().srv || st?.defaultPort || 6969;

function fill(c) {
  for (const k of KEYS) FORM.elements[k].value = c[k] ?? '';
  DIR.value = c.dir ?? '';
  WS.value = c.ws ?? '';
  if (!FORM.elements.srv.value) FORM.elements.srv.value = st?.defaultPort || 6969;
  checkCfg(false);
}

function checkCfg(showErrors = true) {
  const c = cfg(), bad = problems(c), ok = !Object.keys(bad).length;
  for (const k of KEYS) FORM.elements[k].classList.toggle('bad', showErrors && !!bad[k] && !!c[k]);
  $('#cfg-dot').className = 'dot ' + (ok ? 'ok' : 'bad');
  $('#cfg-sum').textContent = ok ? `${target(c)} · port ${c.srv || st?.defaultPort}` : 'Not set up · click to set up';
  const shown = showErrors && Object.entries(bad).filter(([k]) => c[k]).map(([, v]) => v);
  say($('#cfg-msg'), 'bad', shown && shown.length ? esc(shown.join(' · ')) : '');
  renderDir();
  renderWs();
  renderDeploy();
  return ok;
}

let saving = 0;
function saveCfg() {
  checkCfg();
  clearTimeout(saving);
  saving = setTimeout(() => call('config', cfg()), 300);
}

function toggle(btn, box, on = box.hidden) {
  box.hidden = !on;
  btn.setAttribute('aria-expanded', String(on));
  if (btn.id === 'cfg-toggle') btn.querySelector('.car').textContent = on ? '▾' : '▸';
  return on;
}

async function quickFill() {
  const text = $('#quick-in').value.trim();
  if (!text) return say($('#quick-msg'), 'bad', 'Paste the SSH command first.');
  const d = await call('quick', { text });
  if (d.error) return say($('#quick-msg'), 'bad', esc(d.error));
  fill({ ...d, dir: DIR.value, ws: WS.value });
  saveCfg();
  say($('#quick-msg'), 'ok', `Filled in: ${esc(target(d))}, port ${d.srv}.`);
}

async function savePreset() {
  const name = $('#save-name').value.trim();
  if (!name) return say($('#save-msg'), 'bad', 'Enter a server name.');
  const d = await call('preset', { ...cfg(), name });
  if (d.error) return say($('#save-msg'), 'bad', esc(d.error));
  say($('#save-msg'), 'ok', `Saved as “${esc(name)}” in Saved servers.`);
  $('#save-name').value = '';
  refresh();
}

// ---------------------------------------------------------------- tabs

function show(name) {
  tab = name;
  for (const b of document.querySelectorAll('[data-tab]')) b.setAttribute('aria-selected', String(b.dataset.tab === name));
  for (const s of document.querySelectorAll('.ltab')) s.hidden = s.id !== 'tab-' + name;
}

function renderSaved() {
  const ps = st.presets;
  $('#n-saved').textContent = ps.length ? `${ps.length}/${st.max}` : '';
  $('#saved-hint').textContent = ps.length
    ? `Pick a server to fill in the Configuration. Up to ${st.max} servers.`
    : 'No saved servers yet. Fill in the Configuration, then use “Save preset”.';
  $('#saved-list').innerHTML = ps.map((p, i) => `<div class="preset" data-i="${i}">
    <span class="dot" style="background:var(--acc)"></span>
    <div><b>${esc(p.name)}</b><div class="muted">${esc(target(p))} · port ${esc(p.srv)}${
      p.dir ? ' · ' + esc(remoteDir(p.dir).show || p.dir) : ''}</div>
      <div class="muted">scan ${esc(p.ws ? remoteWs(p.ws).show || p.ws : '~ (home)')}</div></div>
    <button type="button" class="del" data-del="${i}" title="Delete">✕</button></div>`).join('');
}

// ---------------------------------------------------------------- thu muc tren server / deploy

function renderDir() {
  const d = remoteDir(DIR.value), h = $('#dir-hint');
  DIR.classList.toggle('bad', !!d.err);
  h.classList.toggle('bad', !!d.err);
  if (d.err) h.textContent = d.err;
  else if (d.def) h.innerHTML = `→ <b>~/.bin</b> (default; created if it does not exist). Type a folder name to use `
    + '<b>~/&lt;name&gt;</b> next to it, or a full path.';
  else if (!d.path) h.innerHTML = `→ <b>${esc(d.show)}</b>, next to ~/.bin: created if it does not exist, `
    + 'otherwise the two files are replaced.';
  else h.innerHTML = `→ <b>${esc(d.show)}</b>: this path must already exist on the server (Push reports an error if not).`;
}

function renderWs() {
  const w = remoteWs(WS.value), h = $('#ws-hint');
  WS.classList.toggle('bad', !!w.err);
  h.classList.toggle('bad', !!w.err);
  if (w.err) h.textContent = w.err;
  else if (w.home) h.innerHTML = '→ scans your <b>home folder</b> on the server (e.g. /home/worker, where ~/.bin is). '
    + 'Type the workspace path to scan only that.';
  else h.innerHTML = `→ scans <b>${esc(w.show)}</b> on the server (Connect reports it if the folder is missing).`;
}

function renderDeploy() {
  const c = cfg(), d = remoteDir(DIR.value), dir = d.show || '~/.bin', w = remoteWs(WS.value);
  $('#dep-target').textContent = ready() ? `${c.user}@${c.host}:${dir}` : dir;
  $('#dep-run').textContent = `python3 ${dir}/repomap.pyz ${w.err ? '…' : w.show}`;
  $('#dep-pyz').textContent = st ? ` (v${st.version})` : '';
}

// ---------------------------------------------------------------- trang thai tunnel / deploy

function renderMain(t) {
  const s = t.state;
  const [label, act, cls] = s === 'stopping' ? ['Stopping…', 'disconnect', 'wait']
    : s === 'connecting' || s === 'starting' ? ['Cancel', 'cancel', 'wait']
    : s === 'up' || s === 'external' ? ['Disconnect', 'disconnect', 'stop'] : ['Connect', 'connect', ''];
  MAIN.textContent = label;
  MAIN.dataset.do = act;
  MAIN.className = 'big ' + (cls || 'pri');
  MAIN.disabled = busy || s === 'stopping';
}

function renderJobs() {
  const t = st.tunnel, d = st.deploy, m = $('#conn-msg'), port = srvPort();
  const url = t.url ? open(t.url) : '', dir = esc(t.dir || remoteDir(DIR.value).show || '~/.bin');
  renderMain(t);
  if (t.state === 'connecting') say(m, 'info', spin + 'Connecting… type your password in the SSH window if it asks.');
  else if (t.state === 'stopping') say(m, 'info', spin + 'Stopping the remote server and closing SSH…');
  else if (t.state === 'stale') say(m, 'bad', 'The server is running a different build. Press <b>Push to server</b>, then Connect again.');
  else if (t.state === 'starting') say(m, 'info', spin + 'Logged in. repomap is starting on the server (reading '
    + `<code>${esc(t.ws || '~')}</code>), the page opens by itself. ${url}`);
  else if (t.state === 'up') say(m, 'ok', `Connected. Opened ${url}. Keep the SSH window open; `
    + '<b>Disconnect</b> closes it and stops repomap on the server.');
  else if (t.state === 'external') say(m, 'ok', `Already connected through another SSH window: ${url}`
    + (t.root ? ` (workspace <code>${esc(t.root)}</code>)` : '') + '. No second connection was opened.');
  else if (t.state === 'nows') say(m, 'bad', `Workspace root <code>${esc(t.ws || '')}</code> was not found on the `
    + 'server. Fix it above (empty = your home folder), then Connect.');
  else if (t.state === 'otherws') say(m, 'bad', `Your repomap already runs on port ${esc(port)} with another workspace. `
    + 'Close its SSH window (or stop it on the server), or change “Server &amp; local port”, then Connect.');
  else if (t.state === 'closed') say(m, 'info', 'Disconnected. Press Connect to start again.');
  else if (t.state === 'cancelled') say(m, 'info', 'Cancelled.');
  else if (t.state === 'nopyz') say(m, 'bad', `<code>repomap.pyz</code> is not in <code>${dir}</code> on the server yet. `
    + 'Press <b>Push to server</b> first, then Connect.');
  else if (t.state === 'taken') say(m, 'bad', `Port ${esc(port)} on the server is used by another program or user. `
    + 'Change “Server &amp; local port” in the Configuration, Push again, then Connect.');
  else if (t.state === 'nopython') say(m, 'bad', '<code>python3</code> was not found on the server.');
  else if (t.state === 'failed' && t.error) say(m, 'bad', esc(t.error));
  else if (t.state === 'failed') say(m, 'bad', `SSH stopped (exit code ${esc(t.code)}`
    + `${t.code === 255 ? ': could not log in or reach the server' : ''}). See the SSH window for the reason.`);
  else if (t.state === 'manual') say(m, 'info', 'Run this command in a terminal and keep it open, then open ' + url);
  else say(m, 'info', '');
  $('#conn-cmd').textContent = t.cmd ? '$ ' + t.cmd : '';

  const dm = $('#dep-msg'), ddir = esc(d.dir || '');
  if (d.state === 'running') say(dm, 'info', spin + 'Copying… type your password in the SSH window if it asks.');
  else if (d.state === 'done') say(dm, 'ok', `Done. The files are in <code>${ddir}</code> on the server. Now press Connect.`);
  else if (d.state === 'failed') say(dm, 'bad', `Copy failed (exit code ${esc(d.code)}). ` + (d.path
    ? `Check that the folder <code>${ddir}</code> exists on the server: a path is not created, only a folder name is. `
    : '') + 'See the SSH window for the reason.');
  else if (d.state === 'manual') say(dm, 'info', 'Run this command in a terminal:');
  else say(dm, 'info', '');
  $('#dep-cmd').textContent = d.cmd ? '$ ' + d.cmd : '';
  $('#deploy').disabled = d.state === 'running';
}

async function refresh() {
  const d = await call('state');
  if (d.error) { say($('#conn-msg'), 'bad', esc(d.error)); return; }
  const first = !st;
  st = d;
  $('#ver').textContent = 'v' + d.version + ' · build ' + (d.build || '?');
  $('#ver').title = d.artifact || '';
  if (first) {
    fill(d.config);
    if (!ready()) say($('#cfg-msg'), 'info', 'Fill in the fields, or use Quick setup to paste your SSH command.');
  }
  renderSaved();
  renderDeploy();
  renderJobs();
}

async function run(ep, btn, msg) {
  if (!checkCfg()) {
    say(msg, 'bad', 'Set up the Configuration above first.');
    return toggle($('#cfg-toggle'), $('#cfg-body'), true);
  }
  const dir = remoteDir(DIR.value), ws = remoteWs(WS.value);
  if (dir.err) { DIR.focus(); return say(msg, 'bad', 'Server folder: ' + esc(dir.err)); }
  if (ws.err) { WS.focus(); return say(msg, 'bad', 'Workspace root: ' + esc(ws.err)); }
  busy = btn === MAIN;
  btn.disabled = true;
  const d = await call(ep, cfg());
  busy = false;
  btn.disabled = false;
  if (d.error) {
    return say(msg, 'bad', esc(d.error) + (/in use/.test(d.error) ? ` Or open ${open(`http://127.0.0.1:${srvPort()}/`)} anyway.` : ''));
  }
  refresh();
}

// Cancel / Disconnect: dong cua so ssh. Script dang chay tren server dung theo repomap -> hoi lai truoc
async function disconnect() {
  const was = st?.tunnel?.state;
  if (was === 'up') {
    const c = await call('check', { srv: srvPort() });
    if (c.ok && c.running > 0 && !confirm(`${c.running} script(s) started from repomap are still running on the server. `
      + 'Disconnecting stops repomap there and those scripts too. Disconnect anyway?')) return;
  }
  busy = true;
  MAIN.disabled = true;
  const d = await call('disconnect', {});
  busy = false;
  if (d.error) say($('#conn-msg'), 'bad', esc(d.error));
  else if (was === 'external') say($('#check-msg'), 'info', 'That connection belongs to another SSH window: close that '
    + 'window to disconnect it.');
  refresh();
}

async function check() {
  const m = $('#check-msg'), btn = $('#check'), port = srvPort();
  btn.disabled = true;
  say(m, 'info', spin + `Checking port ${esc(port)}…`);
  const d = await call('check', { srv: port });
  btn.disabled = false;
  if (d.error) return say(m, 'bad', esc(d.error));
  if (d.ok) {
    const parts = [d.version && `repomap v${esc(d.version)}`, d.build && `build ${esc(d.build)}`, `answers in ${d.ms} ms`,
      d.root && `workspace <code>${esc(d.root)}</code>${d.repos !== undefined ? ` (${d.repos} repos)` : ''}`,
      d.user && `user ${esc(d.user)}`, d.running ? `${d.running} script(s) running` : ''].filter(Boolean);
    return say(m, 'ok', '✓ Connection OK: ' + parts.join(' · ') + ` · ${open(`http://127.0.0.1:${port}/`)}`);
  }
  if (d.why === 'noport') return say(m, 'bad', `Not connected: nothing listens on port ${esc(port)} on this PC.`
    + (d.state === 'connecting' ? ' The SSH window is still waiting for your password.' : ''));
  say(m, 'warn', `Port ${esc(port)} is open on this PC but repomap does not answer `
    + (['connecting', 'starting'].includes(d.state) ? '(still starting on the server, try again in a moment).'
      : '(another program uses this port, or repomap stopped on the server).'));
}

// ---------------------------------------------------------------- khoi dong

document.querySelector('.ltabs').addEventListener('click', e => {
  const b = e.target.closest('[data-tab]');
  if (b) show(b.dataset.tab);
});
$('#cfg-toggle').addEventListener('click', e => toggle(e.currentTarget, $('#cfg-body')));
$('#quick-toggle').addEventListener('click', e => { if (toggle(e.currentTarget, $('#quick'))) $('#quick-in').focus(); });
$('#save-toggle').addEventListener('click', e => { if (toggle(e.currentTarget, $('#save'))) $('#save-name').focus(); });
FORM.addEventListener('input', saveCfg);
FORM.addEventListener('submit', e => e.preventDefault());
DIR.addEventListener('input', saveCfg);
WS.addEventListener('input', saveCfg);
$('#quick-go').addEventListener('click', quickFill);
$('#quick-in').addEventListener('keydown', e => { if (e.key === 'Enter') quickFill(); });
$('#quick-in').addEventListener('paste', () => setTimeout(quickFill, 0));
$('#save-go').addEventListener('click', savePreset);
$('#save-name').addEventListener('keydown', e => { if (e.key === 'Enter') savePreset(); });
MAIN.addEventListener('click', () => {
  if (MAIN.dataset.do === 'connect') { say($('#check-msg'), 'info', ''); run('connect', MAIN, $('#conn-msg')); }
  else disconnect();
});
$('#check').addEventListener('click', check);
$('#deploy').addEventListener('click', e => run('deploy', e.currentTarget, $('#dep-msg')));
$('#saved-list').addEventListener('click', async e => {
  const del = e.target.closest('[data-del]');
  if (del) {
    const p = st.presets[+del.dataset.del];
    if (p && confirm(`Delete “${p.name}”?`)) { await call('preset-del', { name: p.name }); refresh(); }
    return;
  }
  const row = e.target.closest('.preset');
  if (!row) return;
  fill(st.presets[+row.dataset.i]);
  saveCfg();
  show('connect');
  toggle($('#cfg-toggle'), $('#cfg-body'), true);
});

show('connect');
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 2000);      // trang thai tunnel + giu server local song
setInterval(() => { if (document.hidden) call('state'); }, 30000);
