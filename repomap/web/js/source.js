// Man hinh "full source" cua 1 module: cay thu muc (nap khi mo) ben trai, noi dung file ben phai.
// Vao: openSource({repo, base, file}) - them 1 muc vao lich su trinh duyet; "← Back" / nut Back cua trinh duyet
// quay ve cay chinh. Doi file ben trong chi thay muc lich su hien tai.

import { $, esc, api, tail, store, colorOf } from './util.js';
import { t, onLangChange } from './i18n.js';
import { createCodeView } from './codeview.js';

const V = $('#source'), TREE = $('#src-tree'), VIEW = $('#src-view'), TITLE = $('#src-title');
const FIND = $('#src-find'), GREP = $('#src-grep');

let mod = null;                 // {repo, base}
let cur = null;                 // {f, line, fv}
let listing = new Map();        // thu muc (tuong doi repo) -> {dirs, files} | {error}
let expanded = new Set();
let results = null;             // ket qua tim kiem dang hien thay cho cay
let entered = false;            // da pushState khi vao man hinh nay chua
let code = null;                // khung code (codeview.js) cua file dang mo

const join = (a, b) => (a ? a + '/' + b : b);
const inModule = (repo, f) => repo === mod.repo && (!mod.base || f === mod.base || f.startsWith(mod.base + '/'));
const state = () => ({ src: { repo: mod.repo, base: mod.base, file: cur ? cur.f : '' } });

// ---------------------------------------------------------------- cay thu muc

async function load(dir) {
  if (!listing.has(dir)) listing.set(dir, await api('ls', { repo: mod.repo, dir }));
  return listing.get(dir);
}

// loai file -> lop mau cua bieu tuong trong cay (mau dinh nghia o style.css: .ft-*)
const FILE_TYPES = [
  ['c', /\.(c|cc|cpp|cxx|s|S|asm)$/],
  ['h', /\.(h|hh|hpp|hxx|inl|ipp)$/],
  ['py', /\.(py|pyi)$|^(requirements|constraints)[\w.-]*\.(txt|in)$|^Pipfile/],
  ['bb', /\.(bb|bbappend|bbclass|inc)$/],
  ['dt', /\.(dts|dtsi|dtso)$/],
  ['and', /\.(bp|aidl|hal|rc|te)$|^Android\.mk$/],
  ['build', /^(Makefile.*|makefile|GNUmakefile|Kbuild|Kconfig.*|CMakeLists\.txt|meson\.build|configure\.ac|.*\.(mk|mak|cmake|sh|bash|build|use))$/],
  ['ops', /^(Dockerfile.*|Jenkinsfile.*|.*\.(tf|tfvars|hcl|groovy|dockerfile))$|^\.gitlab-ci\.yml$|^(docker-)?compose[\w.-]*\.ya?ml$/],
  ['cfg', /\.(conf|cfg|ini|toml|json|ya?ml|xml|properties|config)$|defconfig$|^\.config$/],
  ['doc', /\.(md|rst|txt|adoc|html?)$|^(README|LICENSE|COPYING|NOTICE|AUTHORS|CHANGELOG)/i],
  ['code', /\.(java|kt|go|rs|js|ts|mjs|cs|swift|rb|pl|lua)$/],
  ['bin', /\.(png|jpe?g|gif|ico|svg|bmp|bin|img|so|a|o|ko|zip|gz|xz|bz2|tar|tgz|pdf|jar|apk|ttf|woff2?)$/i],
];
const fileType = name => (FILE_TYPES.find(([, re]) => re.test(name)) || ['other'])[0];

function treeHtml(dir, depth) {
  const d = listing.get(dir);
  if (!d) return '';
  if (d.error) return `<div class="tn err" style="--d:${depth}">${esc(d.error)}</div>`;
  if (!d.dirs.length && !d.files.length) return `<div class="tn muted" style="--d:${depth}">${t('emptyDir')}</div>`;
  let h = '';
  for (const x of d.dirs) {
    const p = join(dir, x.name), open = expanded.has(p);
    const repoColor = x.repo && store.byRepo[x.repo] ? `;--fold:${colorOf(store.byRepo[x.repo])}` : '';
    h += `<button class="tn dir${open ? ' open' : ''}${x.repo ? ' nested' : ''}" style="--d:${depth}${repoColor}" `
      + `data-d="${esc(p)}" aria-expanded="${open}"><i>${open ? '▾' : '▸'}</i><span class="ic"></span>${esc(x.name)}`
      + `${x.repo ? '<span class="nr">repo</span>' : ''}</button>`;
    if (open) h += treeHtml(p, depth + 1);
  }
  for (const x of d.files) {
    const p = join(dir, x.name);
    h += `<button class="tn file ft-${fileType(x.name)}${cur && cur.f === p ? ' sel' : ''}" style="--d:${depth}" `
      + `data-f="${esc(p)}"><i></i><span class="ic"></span>${esc(x.name)}</button>`;
  }
  return h;
}

function renderTree() {
  if (results) return renderResults();
  TREE.innerHTML = treeHtml(mod.base, 0) || `<div class="tn muted">${t('reading')}</div>`;
  TREE.querySelector('.tn.sel')?.scrollIntoView({ block: 'nearest' });
}

async function toggleDir(p) {
  if (expanded.has(p)) expanded.delete(p);
  else { expanded.add(p); await load(p); }
  renderTree();
}

async function reveal(f) {                  // mo cac thu muc cha cua file trong cay
  const parts = f.slice(mod.base ? mod.base.length + 1 : 0).split('/').slice(0, -1);
  let dir = mod.base;
  for (const part of parts) {
    dir = join(dir, part);
    expanded.add(dir);
    await load(dir);
  }
}

// ---------------------------------------------------------------- tim kiem

async function runSearch(mode, q) {
  q = q.trim();
  if (q.length < 2) { results = null; renderTree(); return; }
  results = { mode, q, loading: true };
  renderResults();
  const r = await api('search', { repo: mod.repo, base: mod.base, q, mode });
  if (!results || results.q !== q || results.mode !== mode) return;       // da go cau khac
  results = { mode, q, ...r };
  renderResults();
}

function renderResults() {
  const r = results;
  let h = `<div class="src-res-head"><span>${r.loading ? t('searching')
    : r.error ? esc(r.error) : t('results', { n: r.results.length }) + (r.truncated ? ' ' + t('resultsMore') : '')}</span>`
    + `<button data-clear>${t('clear')}</button></div>`;
  if (!r.loading && !r.error) {
    if (!r.results.length) h += `<div class="tn muted">${t('noResults')}</div>`;
    for (const x of r.results) {
      const rel = mod.base ? x.f.slice(mod.base.length + 1) : x.f;
      h += `<button class="tn hit file ft-${fileType(tail(x.f, 1))}" data-f="${esc(x.f)}" data-n="${x.n || 0}">`
        + `<b><span class="ic"></span>${esc(tail(rel, 1))}</b>`
        + `<small>${esc(rel)}${x.n ? ':' + x.n : ''}</small>${x.text ? `<code>${esc(x.text)}</code>` : ''}</button>`;
    }
  }
  TREE.innerHTML = h;
}

function clearSearch() {
  results = null;
  FIND.value = GREP.value = '';
  renderTree();
}

// ---------------------------------------------------------------- noi dung file

function renderView() {
  code?.destroy();
  code = null;
  if (!cur) { VIEW.innerHTML = `<div class="src-empty">${t('pickFile')}</div>`; return; }
  const v = cur.fv;
  let h = `<div class="src-head"><div class="ttl">${esc(tail(cur.f, 1))}</div><div class="sub">${esc(mod.repo + '/' + cur.f)}</div>`;
  if (!v) { VIEW.innerHTML = h + `</div><div class="load">${t('reading')}</div>`; return; }
  if (v.error) { VIEW.innerHTML = h + `</div><div class="err">${esc(v.error)}</div>`; return; }
  h += `<div class="tags"><span class="tag">${esc(v.type)}</span><span class="tag">${t('lines', { n: v.lines })}</span>`
    + `<span class="tag">${(v.size / 1024).toFixed(1)} KB</span></div></div>`;
  const refs = v.refs.filter(r => r.line || r.to);
  if (refs.length) {
    h += `<div class="src-chips"><span>${t('refs')}</span>` + refs.map((r, i) => r.to
      ? `<button class="chip-ref" data-ref="${i}" title="${esc(r.to.repo + '/' + r.to.file)}">${esc(r.k)} <b>${esc(r.raw)}</b></button>`
      : `<span class="chip-ref off" title="${esc(r.raw)}">${esc(r.k)} ${esc(r.raw)}</span>`).join('') + '</div>';
  }
  if (v.items.length) {
    h += `<div class="src-chips"><span>${t('outline')}</span>` + v.items.slice(0, 300).map(m =>
      `<button class="chip-out" data-n="${m.line}">${esc(m.type)} <b>${esc(m.name)}</b></button>`).join('') + '</div>';
  }
  if (v.truncated || v.partial) h += `<div class="muted">${t(v.truncated ? 'tooBig' : 'partialParse')}</div>`;
  h += '<div class="code src-code"></div>';
  VIEW.innerHTML = h;
  VIEW._refs = refs;
  const refLines = new Map(refs.filter(r => r.to && r.line).map(r => [r.line, refs.indexOf(r)]));
  code = createCodeView(VIEW.querySelector('.src-code'), { repo: mod.repo, f: cur.f, fv: v, refLines });
  if (cur.line) goLine(cur.line);
}

function goLine(n) {
  code?.goLine(n);
}

async function openFile(f, line = 0) {
  cur = { f, line, fv: null };
  history.replaceState(state(), '', '#source');
  await reveal(f);
  renderTree();
  renderView();
  const fv = await api('file', { repo: mod.repo, f });
  if (!cur || cur.f !== f) return;
  cur.fv = fv;
  renderView();
}

function follow(ref) {
  const { repo, file } = ref.to;
  if (file && inModule(repo, file)) return openFile(file);
  openSource({ repo, base: '', file }, { push: false });          // file o repo khac: doi module
}

// ---------------------------------------------------------------- vao / ra

function title() {
  const node = store.byRepo[mod.repo];
  TITLE.innerHTML = `<span class="dot" style="background:${node ? colorOf(node) : 'var(--mute)'}"></span>`
    + `<b>${esc(mod.repo || '/')}</b>${mod.base ? ` <span class="muted">/ ${esc(mod.base)}</span>` : ''}`;
}

export async function openSource({ repo, base = '', file = '' }, { push = true } = {}) {
  mod = { repo, base: base.replace(/^\/+|\/+$/g, '') };
  cur = null; results = null; listing = new Map(); expanded = new Set();
  FIND.value = GREP.value = '';
  if (push) { history.pushState(state(), '', '#source'); entered = true; }
  else history.replaceState(state(), '', '#source');
  V.hidden = false;
  document.body.classList.add('in-source');
  title();
  renderTree();
  renderView();
  await load(mod.base);
  renderTree();
  if (file) await openFile(file);
}

export function closeSource() {
  V.hidden = true;
  document.body.classList.remove('in-source');
  mod = cur = null;
}

function back() {
  if (entered) { entered = false; history.back(); return; }     // popstate se dong man hinh
  closeSource();
  history.replaceState(null, '', location.pathname + location.search);
}

export function initSource() {
  $('#src-back').addEventListener('click', back);
  TREE.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.hasAttribute('data-clear')) clearSearch();
    else if (b.dataset.d !== undefined) toggleDir(b.dataset.d);
    else if (b.dataset.f !== undefined) openFile(b.dataset.f, +b.dataset.n || 0);
  });
  VIEW.addEventListener('click', e => {
    const el = e.target.closest('[data-ref],[data-n]');
    if (!el) return;
    if (el.dataset.ref !== undefined) follow(VIEW._refs[+el.dataset.ref]);
    else goLine(+el.dataset.n);
  });
  let timer;
  FIND.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => runSearch('name', FIND.value), 300); });
  GREP.addEventListener('keydown', e => { if (e.key === 'Enter') runSearch('text', GREP.value); });
  for (const inp of [FIND, GREP]) inp.addEventListener('keydown', e => { if (e.key === 'Escape') clearSearch(); });
  addEventListener('keydown', e => {
    if (e.key === 'Escape' && !V.hidden && document.activeElement.tagName !== 'INPUT') back();
  });
  addEventListener('popstate', e => {
    const s = e.state && e.state.src;
    if (s) { entered = false; openSource(s, { push: false }); }
    else if (!V.hidden) closeSource();
  });
  onLangChange(() => { if (!V.hidden) { renderTree(); renderView(); } });
}
