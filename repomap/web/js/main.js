// Dieu khien: bam node -> nap du lieu theo tang, tim kiem, toolbar, khoi dong.

import { $, esc, api, KIND_COLOR, store } from './util.js';
import { buildTree, visible, collapseAll, fillCat, loadMore, ensureChild,
         attachInsight, attachDeep, attachRefs } from './tree.js';
import { initGraph, render, refresh, centerOn, fit, resetView } from './graph.js';
import { showPanel, bindPanel, togglePanel } from './panel.js';
import { t, applyStatic, setLang, onLangChange } from './i18n.js';

// ---------------------------------------------------------------- chon / mo node

function select(n) {
  const old = store.sel;
  store.sel = n;
  refresh(old);
  refresh(n);
}

function reveal(n) {
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

bindPanel({
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
  reveal(store.byRepo[r.path]);
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
$('#b-collapse').onclick = () => { collapseAll(store.root); render(); fit(); };
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
  if (store.sel) showPanel(store.sel, true);
  if (R.style.display === 'block') search();
});

// ---------------------------------------------------------------- khoi dong

(async function boot() {
  applyStatic();
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
})();
