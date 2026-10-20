// Trang PC (repomap --local): Connect (cau hinh + mo tunnel), Saved servers (toi da 5 preset), Deploy (scp len ~/.bin).
// Server local lam phan ssh/scp (mo cua so console de nhap password); trang nay chi goi /api/local/* va poll trang thai.

import { $, esc } from './util.js';

const FORM = $('#cfg-form');
const KEYS = ['host', 'user', 'port', 'srv'];
let st = null, tab = 'connect';

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

// ---------------------------------------------------------------- configuration

const cfg = () => Object.fromEntries(KEYS.map(k => [k, FORM.elements[k].value.trim()]));

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

function fill(c) {
  for (const k of KEYS) FORM.elements[k].value = c[k] ?? '';
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
  fill(d);
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
    <div><b>${esc(p.name)}</b><div class="muted">${esc(target(p))} · port ${esc(p.srv)}</div></div>
    <button type="button" class="del" data-del="${i}" title="Delete">✕</button></div>`).join('');
}

function renderDeploy() {
  const c = cfg(), dir = st?.remoteDir || '.bin';
  $('#dep-target').textContent = ready() ? `${c.user}@${c.host}:~/${dir}` : `~/${dir}`;
  $('#dep-run').textContent = `python3 ~/${dir}/repomap.pyz`;
  $('#dep-pyz').textContent = `the tool${st ? `, version ${st.version}` : ''}`;
  $('#dep-ini').textContent = ready() ? `SSH ${target(c)}, port ${c.srv || st?.defaultPort}`
    : 'your Configuration (set it up in Connect first)';
}

// ---------------------------------------------------------------- trang thai tunnel / deploy

function renderJobs() {
  const t = st.tunnel, d = st.deploy, m = $('#conn-msg');
  const anyway = t.url ? ` ${open(t.url)}` : '';
  if (t.state === 'waiting') say(m, 'info', 'Connecting… type your password in the SSH window if it asks.');
  else if (t.state === 'up') say(m, 'ok', `Connected. Opened ${open(t.url)}. Keep the SSH window open while you use it.`);
  else if (t.state === 'noserver') say(m, 'warn', 'The tunnel is up, but repomap is not running on the server yet. '
    + `On the server run <code>python3 ~/${esc(st.remoteDir)}/repomap.pyz</code> and choose <b>1. Run scan</b>; `
    + `this page opens it as soon as it answers.${anyway}`);
  else if (t.state === 'closed') say(m, 'info', 'Tunnel closed (the SSH window was closed). Press Connect to open it again.');
  else if (t.state === 'failed') say(m, 'bad', `SSH stopped (exit code ${esc(t.code)}). See the SSH window for the reason.`);
  else if (t.state === 'manual') say(m, 'info', 'Run this command in a terminal, keep it open, then open' + anyway);
  $('#conn-cmd').textContent = t.cmd ? '$ ' + t.cmd : '';

  const dm = $('#dep-msg');
  if (d.state === 'running') say(dm, 'info', 'Copying… type your password in the SSH window if it asks.');
  else if (d.state === 'done') say(dm, 'ok', `Done. The files are in ~/${esc(st.remoteDir)} on the server.`);
  else if (d.state === 'failed') say(dm, 'bad', `Copy failed (exit code ${esc(d.code)}). See the SSH window for the reason.`);
  else if (d.state === 'manual') say(dm, 'info', 'Run this command in a terminal:');
  $('#dep-cmd').textContent = d.cmd ? '$ ' + d.cmd : '';
}

async function refresh() {
  const d = await call('state');
  if (d.error) { say($('#conn-msg'), 'bad', esc(d.error)); return; }
  const first = !st;
  st = d;
  $('#ver').textContent = 'v' + d.version;
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
    if (ep === 'deploy') return say(msg, 'bad', 'Set up the Configuration in the Connect tab first.');
    return toggle($('#cfg-toggle'), $('#cfg-body'), true);
  }
  btn.disabled = true;
  const d = await call(ep, cfg());
  btn.disabled = false;
  if (d.error) {
    const port = cfg().srv || st.defaultPort;
    return say(msg, 'bad', esc(d.error) + (/in use/.test(d.error) ? ` Or open ${open(`http://127.0.0.1:${port}/`)} anyway.` : ''));
  }
  refresh();
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
$('#quick-go').addEventListener('click', quickFill);
$('#quick-in').addEventListener('keydown', e => { if (e.key === 'Enter') quickFill(); });
$('#quick-in').addEventListener('paste', () => setTimeout(quickFill, 0));
$('#save-go').addEventListener('click', savePreset);
$('#save-name').addEventListener('keydown', e => { if (e.key === 'Enter') savePreset(); });
$('#connect').addEventListener('click', e => run('connect', e.currentTarget, $('#conn-msg')));
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
