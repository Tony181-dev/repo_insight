// Panel chi tiet ben phai: hien thong tin node dang chon.
// Cac link trong panel mang data-a="..." va duoc xu ly tap trung qua bindPanel().

import { $, esc, colorOf, KIND_COLOR, tail, store, SCRIPT } from './util.js';
import { t, tl } from './i18n.js';
import { labelOf } from './tree.js';
import { createCodeView } from './codeview.js';

const P = $('#panel');

// ---------------------------------------------------------------- khoi HTML nho

const header = (n, title, sub) => `<div class="hd"><span class="dot" style="background:${colorOf(n)}"></span>
  <div><div class="ttl">${esc(title)}</div><div class="sub">${esc(sub)}</div></div></div>`;

const h4 = (title, extra = '') => `<h4>${esc(title)}${extra !== '' ? ` <span>${esc(extra)}</span>` : ''}</h4>`;

const kv = rows => `<table class="kv">${rows.map(r => `<tr><td>${esc(tl(r[0]))}</td><td>${esc(r[1])}</td></tr>`).join('')}</table>`;

const tags = list => `<div class="tags">${list.join('')}</div>`;
const tag = (text, color) => `<span class="tag"${color ? ` style="border-color:${color}"` : ''}>${esc(text)}</span>`;

function bars(rows) {
  const max = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([k, v]) => `<div class="bar"><span title="${esc(k)}">${esc(tl(k))}</span>
    <div style="width:${Math.max(2, (v / max) * 100)}%"></div><span>${v}</span></div>`).join('');
}

// link mo file (con cua node dang chon); html: nhan da escape san
const fileLink = (it, html) => `<a data-a="open" data-repo="${esc(it.repo)}" data-f="${esc(it.f)}" data-n="${it.n || 0}"
  data-l="${esc(it.l || '')}" title="${esc(it.f)}">${html ?? esc(it.l || it.f)}</a>`;

const repoLink = (path, html) => `<a class="lnk" data-a="repo" data-p="${esc(path)}">${html ?? esc(path)}</a>`;

const lineCell = ln => `<td class="ln" data-a="line" data-n="${ln}">${ln || ''}</td>`;

// "Open full source": repo -> toan bo repo; file -> thu muc chua file (= thu muc cua module), mo san file do
const srcButton = (repo, base, file = '') => `<button data-a="src" data-repo="${esc(repo)}" data-base="${esc(base)}"
  data-f="${esc(file)}">${t('openSource')}</button>`;

// ---------------------------------------------------------------- tung loai node

function dirPanel(n) {
  const repos = [];
  (function walk(x) {
    if (x.type === 'repo') repos.push(x);
    if (['root', 'dir', 'repo'].includes(x.type)) x.children.forEach(walk);
  })(n);
  const kinds = {};
  repos.forEach(r => (kinds[r.kind] = (kinds[r.kind] || 0) + 1));
  return header(n, n.label + '/', `${store.data.root}/${n.path}`.replace(/\/$/, ''))
    + h4(t('repoKinds'), repos.length) + bars(Object.entries(kinds).sort((a, b) => b[1] - a[1]))
    + h4(t('repos')) + `<div class="ls">${repos.slice(0, 400).map(r =>
      repoLink(r.path, `<span class="dot" style="background:${colorOf(r)}"></span> ${esc(r.path)}`)).join('')}</div>`;
}

function repoPanel(n) {
  const i = n.ins, d = n.dp;
  let h = header(n, n.label, n.path)
    + tags((n.r.tags.length ? n.r.tags : [n.kind]).map(k => tag(k, KIND_COLOR[k])))
    + `<div class="btns"><button class="pri" data-a="deep">${t(d ? 'rescan' : 'deepScan')}</button>
       <button data-a="copy" data-t="${esc(store.data.root + '/' + n.path)}">${t('copyPath')}</button>
       ${srcButton(n.path, '')}
       <button class="script-btn" data-a="${i?.script ? 'script-open' : 'script-create'}"${!i || (!i.script && store.data.noEdit)
         ? ` disabled title="${esc(store.data.noEdit || '')}"` : ''}>${t(i?.script ? 'openScript' : 'createScript')}</button></div>`
    + (n.scErr ? `<div class="err">${esc(n.scErr)}</div>` : '');

  if (!i) return h + `<div class="load">${t('reading')}</div>`;
  if (i.error) return h + `<div class="err">${esc(i.error)}</div>`;
  for (const s of i.sections) h += h4(tl(s.title)) + kv(s.rows);
  if (i.keys.length) {
    h += h4(t('keyFiles'), i.keys.length)
      + `<div class="ls">${i.keys.map(f => fileLink({ repo: n.path, f })).join('')}</div>`;
  }
  if (i.dirs.length) {
    h += h4(t('topDirs'), t('dirsFiles', { d: i.dirs.length, f: i.nfiles })) + tags(i.dirs.map(x => tag(x + '/')));
  }
  if (d?.error) h += `<div class="err">${esc(d.error)}</div>`;
  else if (d) {
    h += h4(t('deepScan'), t('deepInfo', { n: d.total, src: d.src, s: d.took }))
      + `<div class="ls">${d.cats.map(c => `<a data-a="cat" data-c="${c.id}">
          <span class="dot" style="background:var(--cat)"></span> ${esc(tl(c.label))} <small>${c.count}</small></a>`).join('')}</div>`
      + h4(t('byExt')) + bars(d.ext)
      + h4(t('byDir')) + bars(d.top);
  }
  return h;
}

function catPanel(n) {
  const sub = n.repo + ' · ' + (n.total > n.items.length ? t('showing', { a: n.items.length, b: n.total }) : n.total);
  const rows = n.items.map(it => fileLink({ ...it, repo: it.repo ?? n.repo },
    it.l ? `${esc(it.l)} <small>${esc(tail(it.f, 3))}</small>` : undefined));
  return header(n, labelOf(n), sub)
    + `<input class="flt" placeholder="${esc(t('filter'))}" data-flt=".ls a"><div class="ls">${rows.join('')}</div>`;
}

function refTarget(r) {
  if (!r.to) return `<span class="muted">${esc(r.raw)}</span>`;
  if (!r.to.file) return repoLink(r.to.repo, `${esc(r.raw)} → ${esc(r.to.repo)}`);
  return fileLink({ repo: r.to.repo, f: r.to.file }, esc(r.raw));
}

function filePanel(n) {
  const v = n.fv;
  let h = header(n, tail(n.f, 1), (n.repo ? n.repo + '/' : '') + n.f);
  if (!v) return h + `<div class="load">${t('reading')}</div>`;
  if (v.error) return h + `<div class="err">${esc(v.error)}</div>`;

  h += tags([tag(v.type), tag(t('lines', { n: v.lines })), tag(`${(v.size / 1024).toFixed(1)} KB`),
    store.byRepo[n.repo] ? `<a class="tag lnk" data-a="repo" data-p="${esc(n.repo)}">${t('openRepo')}</a>` : '']);
  h += `<div class="btns">${srcButton(n.repo, n.f.includes('/') ? n.f.slice(0, n.f.lastIndexOf('/')) : '', n.f)}</div>`;
  if (v.refs.length) {
    h += h4(t('refs'), v.refs.length) + `<table class="kv">${v.refs.map(r =>
      `<tr><td>${esc(r.k)}</td><td>${refTarget(r)}</td>${lineCell(r.line)}</tr>`).join('')}</table>`;
  }
  if (v.items.length) {
    h += h4(t('defs'), v.items.length) + `<table class="kv">${v.items.map(m =>
      `<tr><td>${esc(m.type)}</td><td><b>${esc(m.name)}</b>${m.deps?.length ? `<div class="muted">${esc(m.deps.join(' '))}</div>` : ''}</td>${lineCell(m.line)}</tr>`).join('')}</table>`;
  }
  if (v.vars.length) {
    h += h4(t('vars'), v.vars.length) + `<input class="flt" placeholder="${esc(t('filterVars'))}" data-flt="#vars tr">
      <div class="scroll"><table class="kv" id="vars">${v.vars.map(([ln, k, op, val]) =>
      `<tr>${lineCell(ln)}<td>${esc(k)} <span class="muted">${esc(op)}</span></td><td>${esc(val)}</td></tr>`).join('')}</table></div>`;
  }
  h += h4(t('content'), v.truncated ? t('tooBig') : '') + (v.partial ? `<div class="muted">${t('partialParse')}</div>` : '')
    + '<div class="code" id="pcode"></div>';
  return h;
}

// build script cua module: editor sua / luu ngay tren giao dien (ban nhap giu trong n.sc.draft)
export const scriptDirty = n => !!n.sc && n.sc.draft !== undefined && n.sc.draft !== n.sc.text;

function scriptStatus(n) {
  const s = n.sc;
  if (s?.saveErr) return ['err', s.saveErr];
  if (scriptDirty(n)) return ['dirty', t('unsaved')];
  return ['', s?.saved ? t('savedAt', { t: s.saved }) : ''];
}

function scriptPanel(n) {
  const s = n.sc, ro = store.data.noEdit;
  let h = header(n, SCRIPT, `${n.repo}/${SCRIPT}`).replace(/background:[^"]+/, 'background:#f0b429')
    + tags([tag(t('buildScript'), '#f0b429'),
      store.byRepo[n.repo] ? `<a class="tag lnk" data-a="repo" data-p="${esc(n.repo)}">${t('openRepo')}</a>` : '']);
  if (!s) return h + `<div class="load">${t('reading')}</div>`;
  if (s.error) return h + `<div class="err">${esc(s.error)}</div>`;
  const [cls, msg] = scriptStatus(n), off = ro ? ' disabled' : '';
  return h + `<div class="btns"><button class="pri" data-a="script-save"${off}>${t('save')}</button>
      <button data-a="script-regen"${off}>${t('regenerate')}</button>
      <span class="sc-st ${cls}" id="sc-st">${esc(msg)}</span></div>
    <div class="muted sc-hint">${ro ? esc(t('readOnly', { why: ro })) : t('scriptHint')}</div>
    <textarea id="sc-ed" class="sc-ed" spellcheck="false" autocomplete="off"${ro ? ' readonly' : ''}>${
      esc(s.draft ?? s.text)}</textarea>`;
}

export function scriptStatusRefresh(n) {
  const el = P.querySelector('#sc-st');
  if (!el || store.sel !== n) return;
  const [cls, msg] = scriptStatus(n);
  el.className = 'sc-st ' + cls;
  el.textContent = msg;
}

// ---------------------------------------------------------------- API

const RENDERERS = { root: dirPanel, dir: dirPanel, repo: repoPanel, cat: catPanel,
  file: n => (n.script ? scriptPanel(n) : filePanel(n)) };

let code = null;                // khung code cua file dang hien trong panel

export function showPanel(n, keepScroll = false) {
  const fn = RENDERERS[n.type];
  if (!fn) return;
  const top = P.scrollTop;
  code?.destroy();
  code = null;
  P.classList.remove('hide');
  P.innerHTML = fn(n);
  P.scrollTop = keepScroll ? top : 0;
  const box = P.querySelector('#pcode');
  if (box) {
    code = createCodeView(box, { repo: n.repo, f: n.f, fv: n.fv });
    if (!keepScroll && n.line) setTimeout(() => goLine(n.line), 30);
  }
}

export function goLine(ln) {
  if (!code) return;
  P.querySelector('#pcode').scrollIntoView({ block: 'nearest' });
  code.goLine(ln);
}

// noi dung dung san (vd block view)
export function showHtml(html) {
  code?.destroy();
  code = null;
  P.classList.remove('hide');
  P.innerHTML = html;
  P.scrollTop = 0;
}

export const togglePanel = () => P.classList.toggle('hide');

// actions: { deep(btn), open(item), repo(path), cat(id), src({repo, base, file}), script(action, btn) }
export function bindPanel(actions) {
  P.addEventListener('click', e => {
    const a = e.target.closest('[data-a]');
    if (!a) return;
    const d = a.dataset;
    switch (d.a) {
      case 'deep': return actions.deep(a);
      case 'open': return actions.open({ repo: d.repo, f: d.f, n: +d.n, l: d.l || undefined });
      case 'repo': return actions.repo(d.p);
      case 'cat': return actions.cat(d.c);
      case 'src': return actions.src({ repo: d.repo, base: d.base, file: d.f });
      case 'line': return goLine(+d.n);
      case 'script-create': case 'script-open': case 'script-save': case 'script-regen':
        return actions.script(d.a.slice(7), a);
      case 'copy': navigator.clipboard?.writeText(d.t); a.textContent = t('copied'); return;
    }
  });
  P.addEventListener('keydown', e => {
    if (e.target.id !== 'sc-ed') return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      actions.script('save', P.querySelector('[data-a="script-save"]'));
    } else if (e.key === 'Tab' && !e.target.readOnly) {             // Tab trong editor: chen 2 dau cach
      e.preventDefault();
      document.execCommand('insertText', false, '  ');
    }
  });
  P.addEventListener('input', e => {
    if (e.target.id === 'sc-ed') {
      const n = store.sel;
      if (n?.sc) { n.sc.draft = e.target.value; n.sc.saveErr = ''; scriptStatusRefresh(n); }
      return;
    }
    const sel = e.target.dataset.flt;
    if (!sel) return;
    const q = e.target.value.toLowerCase();
    P.querySelectorAll(sel).forEach(x => (x.style.display = x.textContent.toLowerCase().includes(q) ? '' : 'none'));
  });
}
