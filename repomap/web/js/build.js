// Chay build script cua module tren server va theo doi log (builds.py).
//   n.bd = { state, code, elapsed, text (log da doc), next (byte doc tiep), lost (mat ket noi) }
// Panel dang mo script: poll moi giay, noi them log vao khung; mat ket noi thi thu lai, noi lai doc tiep tu `next`.
// Ngoai ra poll /api/builds moi vai giay khi con build dang chay de to sang node script trong cay.

import { $, api, post, store, SCRIPT } from './util.js';
import { t } from './i18n.js';

const KEEP = 1 << 20;                   // giu toi da 1 MB log cuoi trong trinh duyet
const ANSI = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07/g;
let onChange = () => {};
let viewTimer = 0, allTimer = 0;

export const isRunning = (repo, script = SCRIPT) => {
  const b = store.builds?.[repo];
  return b?.scripts ? b.scripts[script]?.state === 'running' : b?.state === 'running';
};

export function initBuild(changed) {
  onChange = changed;
  store.builds = {};
  pollAll();
}

const anyRunning = runs => Object.values(runs).some(b =>
  b.scripts ? Object.values(b.scripts).some(s => s.state === 'running') : b.state === 'running');

export const fmtTime = s => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
};

export function statusText(bd, customScript = false) {
  if (!bd || bd.state === 'idle') return ['', ''];
  if (bd.lost) return ['lost', t('lostConn')];
  const time = fmtTime(bd.elapsed);
  if (customScript) return {
    running: ['run', t('scriptRunning', { t: time })],
    done: ['ok', t('scriptDone', { t: time })],
    failed: ['err', t('scriptFailed', { c: bd.code, t: time })],
    stopped: ['err', t('scriptStopped', { t: time })],
  }[bd.state] || ['', bd.state];
  return {
    running: ['run', t('buildRunning', { t: time })],
    done: ['ok', t('buildDone', { t: time })],
    failed: ['err', t('buildFailed', { c: bd.code, t: time })],
    stopped: ['err', t('buildStopped', { t: time })],
  }[bd.state] || ['', bd.state];
}

// trang thai moi module (de to sang node trong cay); poll thua khi khong co build nao chay
async function pollAll() {
  clearTimeout(allTimer);
  const d = await api('builds');
  if (!d.error) {
    const running = runs => Object.entries(runs).flatMap(([repo, b]) =>
      Object.entries(b.scripts || {}).filter(([, s]) => s.state === 'running').map(([name]) => `${repo}|${name}`));
    const before = JSON.stringify(running(store.builds));
    store.builds = d;
    if (JSON.stringify(running(d)) !== before) onChange(null);
  }
  allTimer = setTimeout(pollAll, anyRunning(store.builds) ? 3000 : 15000);
}

// doc tiep log cua node script n; tail=true: lan dau mo panel, chi lay phan cuoi log
export async function poll(n, tail = false) {
  clearTimeout(viewTimer);
  const bd = n.bd || (n.bd = { state: 'idle', text: '', next: 0 });
  const clearVersion = n.logClearVersion || 0;
  const d = await api('build', { repo: n.repo, name: n.scriptName || SCRIPT, from: tail ? -1 : bd.next });
  if (clearVersion !== (n.logClearVersion || 0)) return;
  if (d.error) {
    bd.lost = /fetch|network|TypeError/i.test(d.error);
    if (!bd.lost) bd.error = d.error;
  } else if (!tail && !d.reset && d.next < bd.next) {         // log da bi ghi lai (build moi): doc lai tu dau
    Object.assign(bd, { text: '', next: 0 });
    return poll(n);
  } else {
    const chunk = d.log.replace(ANSI, '').replace(/\r(?!\n)/g, '\n');
    const restart = tail || d.reset;                           // mo panel hoac vua clear log
    bd.text = restart ? (tail && d.next < d.size ? '…\n' : '') + chunk : bd.text + chunk;
    if (bd.text.length > KEEP) bd.text = '…\n' + bd.text.slice(-KEEP);
    Object.assign(bd, { state: d.state, code: d.code, elapsed: d.elapsed, next: d.next, lost: false, error: '' });
    if (store.builds && d.state !== 'idle') {
      const b = store.builds[n.repo] || (store.builds[n.repo] = { scripts: {} });
      b.scripts ||= {};
      b.scripts[n.scriptName || SCRIPT] = { state: d.state };
      b.state = Object.values(b.scripts).some(s => s.state === 'running') ? 'running' : d.state;
    }
    render(n, restart ? null : chunk);
  }
  if (bd.lost) render(n, null, true);
  const again = bd.lost || bd.state === 'running';
  if (again && store.sel === n) viewTimer = setTimeout(() => poll(n), bd.lost ? 2000 : 1000);
  if (!again) onChange(n);
}

// cap nhat khung log + dong trang thai ngay trong panel (khong ve lai ca panel)
function render(n, chunk, statusOnly = false) {
  if (store.sel !== n) return;
  const st = $('#bd-st'), log = $('#bd-log');
  if (st) {
    const [cls, msg] = statusText(n.bd, !!n.customScript);
    st.className = 'bd-st ' + cls;
    st.textContent = msg;
  }
  const running = n.bd.state === 'running';
  $('[data-a="script-run"]')?.toggleAttribute('hidden', running);
  $('[data-a="script-stop"]')?.toggleAttribute('hidden', !running);
  if (statusOnly || !log) return;
  log.hidden = !n.bd.text;
  $('#bd-hd')?.toggleAttribute('hidden', !n.bd.text);
  $('#sc-ed')?.classList.toggle('with-log', !!n.bd.text);        // co log: editor thu nho, log hien ngay ben duoi
  const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  if (chunk === null || log.textContent.length > KEEP * 1.2) log.textContent = n.bd.text;
  else if (chunk) log.append(chunk);
  if (atEnd || chunk === null) log.scrollTop = log.scrollHeight;
}

export async function startBuild(n) {
  const script = n.scriptName || SCRIPT;
  const r = await post('build-start', { repo: n.repo, name: script });
  if (r.error) return r;
  n.bd = { state: 'running', text: '', next: 0, elapsed: 0 };
  const b = store.builds[n.repo] || (store.builds[n.repo] = { scripts: {} });
  b.scripts ||= {};
  b.scripts[script] = { state: 'running' };
  b.state = 'running';
  onChange(n);
  poll(n);
  pollAll();
  return r;
}

export async function stopBuild(n) {
  const r = await post('build-stop', { repo: n.repo, name: n.scriptName || SCRIPT });
  if (!r.error) poll(n);
  return r;
}

export async function clearBuildLog(n) {
  const script = n.scriptName || SCRIPT;
  n.logClearVersion = (n.logClearVersion || 0) + 1;
  clearTimeout(viewTimer);
  const r = await post('build-clear', { repo: n.repo, name: script });
  if (r.error) {
    if (store.sel === n) viewTimer = setTimeout(() => poll(n), 0);
    return r;
  }
  const bd = n.bd || (n.bd = { state: 'idle', text: '', next: 0 });
  Object.assign(bd, { state: r.state, code: r.code, elapsed: r.elapsed, text: '', next: r.next || 0,
    lost: false, error: '' });
  render(n, null);
  if (store.sel === n) viewTimer = setTimeout(() => poll(n), bd.state === 'running' ? 1000 : 0);
  return r;
}
