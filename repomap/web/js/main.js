// Dieu khien: bam node -> nap du lieu theo tang, tim kiem, toolbar, khoi dong.

import { $, esc, api, post, KIND_COLOR, store, SCRIPT } from './util.js';
import { buildTree, visible, collapseAll, fillCat, loadMore, ensureChild,
         attachInsight, attachDeep, attachRefs, attachScript, attachUserScript } from './tree.js';
import { initGraph, render, refresh, centerOn, fit, resetView } from './graph.js';
import { showPanel, bindPanel, togglePanel, initPanelResizer, resetPanelSize, scriptDirty, scriptStatusRefresh } from './panel.js';
import { t, applyStatic, setLang, onLangChange } from './i18n.js';
import { initSource, openSource } from './source.js';
import { initArch, showArch, hideArch, archRefresh, archFocusRepo } from './arch.js';
import { initBuild, poll as pollBuild, startBuild, stopBuild, clearBuildLog } from './build.js';

// ---------------------------------------------------------------- chon / mo node

function select(n) {
  const old = store.sel;
  store.sel = n;
  refresh(old);
  refresh(n);
}

// ---------------------------------------------------------------- kieu xem: cay (mac dinh) / khoi kien truc

let view = 'tree';

function setView(v) {
  view = v;
  document.querySelectorAll('[data-view]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === v)));
  $('#stage').classList.toggle('arch-mode', v === 'arch');
  if (v === 'arch') showArch();
  else {
    hideArch();
    if (store.sel) showPanel(store.sel);
  }
}

$('#views').addEventListener('click', e => {
  const b = e.target.closest('[data-view]');
  if (b && b.dataset.view !== view) setView(b.dataset.view);
});

function reveal(n) {
  if (view !== 'tree') setView('tree');
  for (let p = n.parent; p; p = p.parent) p.open = true;
  render();
  centerOn(n);
  clickNode(n, true);
}

// bam lan dau: chon + mo (nap du lieu neu can); bam lai node dang chon: dong/mo
async function clickNode(n, fresh = false) {
  const again = store.sel === n && !fresh;
  select(n);
  switch (n.type) {
    case 'more': loadMore(n); render(); return;
    case 'goto': if (store.byRepo[n.target]) reveal(store.byRepo[n.target]); return;
    case 'repo':
      if (!n.ins) {
        showPanel(n);
        n.ins = await api('insight', { repo: n.path });
        attachInsight(n, n.ins);
        n.open = true;
      } else n.open = again ? !n.open : true;
      break;
    case 'file':
      if (n.script) {                                     // build script: mo editor, nap lai neu chua co ban nhap
        if (!n.sc || (!scriptDirty(n) && !fresh)) {
          showPanel(n);
          n.sc = await api('script', { repo: n.repo, name: n.scriptName || SCRIPT });
        }
        render();
        centerOn(n, true);
        if (store.sel !== n) return;
        showPanel(n);
        return pollBuild(n, !n.bd || n.bd.state !== 'running');   // trang thai + log build (lan dau: phan cuoi log)
      }
      if (!n.fv) {
        showPanel(n);
        n.fv = await api('file', { repo: n.repo, f: n.f });
        attachRefs(n, n.fv);
        n.open = true;
      } else n.open = again ? !n.open : true;
      break;
    case 'cat':
      n.open = again ? !n.open : true;
      if (n.open) fillCat(n);
      break;
    default:
      if (n.children.length) n.open = again ? !n.open : true;
  }
  render();
  centerOn(n, true);
  if (store.sel === n) showPanel(n);
}

async function deepScan(n, btn) {
  btn.disabled = true;
  btn.textContent = t('scanning');
  n.dp = await api('deep', { repo: n.path, force: n.dp ? 1 : 0 });
  attachDeep(n, n.dp);
  n.open = true;
  render();
  if (store.sel === n) showPanel(n);
}

// ---------------------------------------------------------------- build script (repomap_build_script.sh)

async function scriptAction(what, btn) {
  const n = store.sel;
  if (!n) return;
  if (what === 'create' || what === 'open') {          // tu panel repo
    n.scErr = '';
    let sn = n.children.find(c => c.buildScript);
    if (what === 'create') {
      btn.disabled = true;
      btn.textContent = t('creating');
      const r = await post('script-create', { repo: n.path });
      if (r.error) { n.scErr = r.error; return showPanel(n, true); }
      if (n.ins) n.ins.script = true;
      sn = attachScript(n);
      sn.sc = r;
    }
    sn = sn || attachScript(n);
    n.open = true;
    render();
    return clickNode(sn, true);
  }
  if (what === 'user-create') {
    const repo = n.type === 'repo' ? n : store.byRepo[n.repo];
    if (!repo?.ins) return;
    repo.scErr = '';
    btn.disabled = true;
    btn.textContent = t('creating');
    const r = await post('script-empty-create', { repo: repo.path });
    btn.disabled = false;
    if (r.error) { repo.scErr = r.error; return showPanel(repo, true); }
    repo.ins.scripts ||= [];
    repo.ins.scripts.push({ name: r.name, slot: r.slot });
    const sn = attachUserScript(repo, r);
    sn.sc = r;
    repo.open = true;
    render();
    return clickNode(sn, true);
  }
  if (what === 'log-clear') {
    btn.disabled = true;
    const r = await clearBuildLog(n);
    btn.disabled = false;
    if (r.error) { n.bd.error = r.error; showPanel(n, true); }
    return;
  }
  if (what === 'delete') {
    const name = n.scriptName || SCRIPT;
    if (!await confirmDeleteScript(name)) return;
    if (btn) btn.disabled = true;
    const r = await post('script-delete', { repo: n.repo, name });
    if (r.error) {
      if (btn) btn.disabled = false;
      n.sc.saveErr = r.error;
      return scriptStatusRefresh(n);
    }
    const repo = store.byRepo[n.repo];
    if (!repo) return;
    repo.children = repo.children.filter(c => c !== n);
    if (n.customScript) repo.ins.scripts = (repo.ins.scripts || []).filter(s => s.name !== name);
    else if (repo.ins) repo.ins.script = false;
    const runs = store.builds?.[n.repo]?.scripts;
    if (runs) {
      delete runs[name];
      if (!Object.keys(runs).length) delete store.builds[n.repo];
    }
    repo.open = true;
    select(repo);
    render();
    showPanel(repo, true);
    return;
  }
  if (!n.sc) return;
  if (what === 'run' || what === 'stop') return runAction(n, what, btn);
  if (what === 'regen' && !confirm(t('regenAsk'))) return;
  btn && (btn.disabled = true);
  const text = n.sc.draft ?? n.sc.text;
  const r = what === 'save' ? await post('script-save', { repo: n.repo, name: n.scriptName || SCRIPT, text })
    : await post('script-create', { repo: n.repo, overwrite: true });
  btn && (btn.disabled = false);
  if (r.error) { n.sc.saveErr = r.error; return scriptStatusRefresh(n); }
  const draft = what === 'save' && n.sc.draft !== text ? n.sc.draft : undefined;   // go tiep trong luc dang luu
  n.sc = { ...r, draft };
  if (what === 'regen') showPanel(n, true);
  else scriptStatusRefresh(n);
}

function confirmDeleteScript(name) {
  const dialog = $('#confirm-dialog');
  $('#confirm-message').textContent = t('deleteAsk', { name });
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'yes'), { once: true });
    dialog.showModal();
  });
}

// Chay: luu ban nhap truoc (chay dung cai dang thay tren man hinh). Dung: hoi lai cho chac.
async function runAction(n, what, btn) {
  if (what === 'stop' && !confirm(t(n.customScript ? 'stopScriptAsk' : 'stopAsk'))) return;
  btn.disabled = true;
  let r = {};
  if (what === 'run' && scriptDirty(n)) await scriptAction('save', null);
  if (what === 'run' && n.sc.saveErr) r = { error: n.sc.saveErr };
  else r = what === 'run' ? await startBuild(n) : await stopBuild(n);
  btn.disabled = false;
  if (r.error) { n.bd = { ...(n.bd || { text: '', next: 0 }), error: r.error }; }
  if (store.sel === n) showPanel(n, true);
}

addEventListener('beforeunload', e => {
  const dirty = Object.values(store.byRepo).some(r => r.children.some(c => c.script && scriptDirty(c)));
  if (dirty) { e.preventDefault(); e.returnValue = t('leaveAsk'); }
});

bindPanel({
  script: scriptAction,
  deep: btn => deepScan(store.sel, btn),
  open: it => {
    const s = store.sel;
    const host = s.type === 'repo' ? s.children.find(c => c.cid === 'keys') || s : s;
    const child = ensureChild(host, it);
    render();
    clickNode(child, true);
  },
  repo: path => store.byRepo[path] && reveal(store.byRepo[path]),
  cat: id => {
    const c = store.sel.children.find(x => x.cid === id);
    if (c) reveal(c);
  },
  src: target => openSource(target),
});

// ---------------------------------------------------------------- tim kiem

const Q = $('#q'), R = $('#res');
let hits = [], hi = 0;

function search() {
  const s = Q.value.trim().toLowerCase();
  if (!s) { R.style.display = 'none'; return; }
  let kind = null;
  const text = s.replace(/kind:(\S+)/, (_, k) => { kind = k; return ''; }).trim();
  hits = store.data.repos.filter(r =>
    (!kind || r.kind === kind || r.tags.includes(kind)) &&
    (!text || r.path.toLowerCase().includes(text) || r.name.toLowerCase().includes(text))).slice(0, 150);
  hi = 0;
  R.innerHTML = hits.length
    ? hits.map((r, i) => `<div class="ri${i ? '' : ' on'}" data-i="${i}">
        <span class="dot" style="background:${KIND_COLOR[r.kind]}"></span>${esc(r.path)}<small>${esc(r.kind)}</small></div>`).join('')
    : `<div class="ri muted">${t('noMatch')}</div>`;
  R.style.display = 'block';
}

function pick(i) {
  const r = hits[i];
  if (!r) return;
  R.style.display = 'none';
  Q.blur();
  if (view === 'arch') archFocusRepo(r.path);
  else reveal(store.byRepo[r.path]);
}

function moveHit(d) {
  hi = Math.max(0, Math.min(hits.length - 1, hi + d));
  R.querySelectorAll('.ri').forEach((x, i) => x.classList.toggle('on', i === hi));
  R.children[hi]?.scrollIntoView({ block: 'nearest' });
}

Q.addEventListener('input', search);
Q.addEventListener('focus', search);
Q.addEventListener('blur', () => setTimeout(() => (R.style.display = 'none'), 150));
Q.addEventListener('keydown', e => {
  if (e.key === 'Enter') pick(hi);
  else if (e.key === 'Escape') Q.blur();
  else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); moveHit(e.key === 'ArrowDown' ? 1 : -1); }
});
R.addEventListener('mousedown', e => { const x = e.target.closest('[data-i]'); if (x) pick(+x.dataset.i); });
addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') { e.preventDefault(); Q.focus(); }
});

// ---------------------------------------------------------------- toolbar

$('#b-panel').onclick = togglePanel;
$('#b-fit').onclick = fit;
$('#b-collapse').onclick = () => { resetPanelSize(); collapseAll(store.root); render(); fit(); };
$('#b-more').onclick = () => {
  visible(store.root).filter(n => n.type === 'dir' && !n.open).forEach(n => (n.open = true));
  render();
  setTimeout(fit, 340);
};

function legend(repos) {
  const counts = {};
  repos.forEach(r => (counts[r.kind] = (counts[r.kind] || 0) + 1));
  const box = $('#legend');
  box.innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, c]) =>
    `<span class="chip" data-k="${k}"><span class="dot" style="background:${KIND_COLOR[k] || '#666'}"></span>${k} <b>${c}</b></span>`).join('');
  box.onclick = e => {
    const c = e.target.closest('[data-k]');
    if (c) { Q.value = `kind:${c.dataset.k} `; Q.focus(); search(); }
  };
}

// ---------------------------------------------------------------- ngon ngu

const status = () => {
  const d = store.data;
  if (d) $('#stat').textContent = t('status', { n: d.repos.length, mode: d.mode, s: d.took });
};

$('#lang').addEventListener('click', e => {
  const b = e.target.closest('[data-lang]');
  if (b) setLang(b.dataset.lang);
});

onLangChange(() => {
  status();
  if (!store.root) return;
  render();
  if (view === 'arch') archRefresh();
  else if (store.sel) showPanel(store.sel, true);
  if (R.style.display === 'block') search();
});

// ---------------------------------------------------------------- khoi dong

(async function boot() {
  applyStatic();
  initPanelResizer();
  const empty = $('#empty');
  const data = await api('tree');
  if (data.error) { empty.textContent = data.error; return; }
  store.data = data;
  $('#rootp').textContent = data.root;
  status();
  legend(data.repos);
  if (!data.repos.length) {
    empty.textContent = t('noRepos');
    return;
  }
  store.root = buildTree(data);
  initGraph(clickNode);
  render();
  empty.style.display = 'none';
  resetView();
  select(store.root);
  showPanel(store.root);
  initSource();
  initArch();
  initBuild(() => render());
  setView('tree');
  if (history.state && history.state.src) openSource(history.state.src, { push: false });   // tai lai trang khi dang xem source
})();
