// Panel chi tiet ben phai: hien thong tin node dang chon.
// Cac link trong panel mang data-a="..." va duoc xu ly tap trung qua bindPanel().

import { $, esc, colorOf, KIND_COLOR, tail, store } from './util.js';

const P = $('#panel');

// ---------------------------------------------------------------- khoi HTML nho

const header = (n, title, sub) => `<div class="hd"><span class="dot" style="background:${colorOf(n)}"></span>
  <div><div class="ttl">${esc(title)}</div><div class="sub">${esc(sub)}</div></div></div>`;

const h4 = (title, extra = '') => `<h4>${esc(title)}${extra !== '' ? ` <span>${esc(extra)}</span>` : ''}</h4>`;

const kv = rows => `<table class="kv">${rows.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join('')}</table>`;

const tags = list => `<div class="tags">${list.join('')}</div>`;
const tag = (text, color) => `<span class="tag"${color ? ` style="border-color:${color}"` : ''}>${esc(text)}</span>`;

function bars(rows) {
  const max = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([k, v]) => `<div class="bar"><span title="${esc(k)}">${esc(k)}</span>
    <div style="width:${Math.max(2, (v / max) * 100)}%"></div><span>${v}</span></div>`).join('');
}

// link mo file (con cua node dang chon); html: nhan da escape san
const fileLink = (it, html) => `<a data-a="open" data-repo="${esc(it.repo)}" data-f="${esc(it.f)}" data-n="${it.n || 0}"
  data-l="${esc(it.l || '')}" title="${esc(it.f)}">${html ?? esc(it.l || it.f)}</a>`;

const repoLink = (path, html) => `<a class="lnk" data-a="repo" data-p="${esc(path)}">${html ?? esc(path)}</a>`;

const lineCell = ln => `<td class="ln" data-a="line" data-n="${ln}">${ln || ''}</td>`;

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
    + h4('Loại repo', repos.length) + bars(Object.entries(kinds).sort((a, b) => b[1] - a[1]))
    + h4('Repo') + `<div class="ls">${repos.slice(0, 400).map(r =>
      repoLink(r.path, `<span class="dot" style="background:${colorOf(r)}"></span> ${esc(r.path)}`)).join('')}</div>`;
}

function repoPanel(n) {
  const i = n.ins, d = n.dp;
  let h = header(n, n.label, n.path)
    + tags((n.r.tags.length ? n.r.tags : [n.kind]).map(t => tag(t, KIND_COLOR[t])))
    + `<div class="btns"><button class="pri" data-a="deep">${d ? 'Quét lại' : 'Quét sâu'}</button>
       <button data-a="copy" data-t="${esc(store.data.root + '/' + n.path)}">Copy path</button></div>`;

  if (!i) return h + '<div class="load">Đang đọc…</div>';
  if (i.error) return h + `<div class="err">${esc(i.error)}</div>`;
  for (const s of i.sections) h += h4(s.title) + kv(s.rows);
  if (i.keys.length) {
    h += h4('File chính', i.keys.length)
      + `<div class="ls">${i.keys.map(f => fileLink({ repo: n.path, f })).join('')}</div>`;
  }
  if (i.dirs.length) {
    h += h4('Thư mục gốc', `${i.dirs.length} dir · ${i.nfiles} file`) + tags(i.dirs.map(x => tag(x + '/')));
  }
  if (d?.error) h += `<div class="err">${esc(d.error)}</div>`;
  else if (d) {
    h += h4('Quét sâu', `${d.total} file · ${d.src} · ${d.took}s`)
      + `<div class="ls">${d.cats.map(c => `<a data-a="cat" data-c="${c.id}">
          <span class="dot" style="background:var(--cat)"></span> ${esc(c.label)} <small>${c.count}</small></a>`).join('')}</div>`
      + h4('Theo đuôi file') + bars(d.ext)
      + h4('Theo thư mục') + bars(d.top);
  }
  return h;
}

function catPanel(n) {
  const sub = n.repo + (n.total > n.items.length ? ` · hiện ${n.items.length}/${n.total}` : ` · ${n.total}`);
  const rows = n.items.map(it => fileLink({ ...it, repo: it.repo ?? n.repo },
    it.l ? `${esc(it.l)} <small>${esc(tail(it.f, 3))}</small>` : undefined));
  return header(n, n.label, sub)
    + `<input class="flt" placeholder="lọc…" data-flt=".ls a"><div class="ls">${rows.join('')}</div>`;
}

function refTarget(r) {
  if (!r.to) return `<span class="muted">${esc(r.raw)}</span>`;
  if (!r.to.file) return repoLink(r.to.repo, `${esc(r.raw)} → ${esc(r.to.repo)}`);
  return fileLink({ repo: r.to.repo, f: r.to.file }, esc(r.raw));
}

function highlight(line) {
  const e = esc(line);
  if (/^\s*(#|\/\/)/.test(line)) return `<span class="cm">${e}</span>`;
  return e.replace(/^(\s*(?:export\s+)?)([A-Za-z_][\w.:${}\-\/]*)(\s*(?:\?\?=|\?=|::=|:=|\+=|=\+|\.=|=\.|!=|=))/,
    '$1<span class="v">$2</span>$3');
}

function filePanel(n) {
  const v = n.fv;
  let h = header(n, tail(n.f, 1), (n.repo ? n.repo + '/' : '') + n.f);
  if (!v) return h + '<div class="load">Đang đọc…</div>';
  if (v.error) return h + `<div class="err">${esc(v.error)}</div>`;

  h += tags([tag(v.type), tag(`${v.lines} dòng`), tag(`${(v.size / 1024).toFixed(1)} KB`),
    store.byRepo[n.repo] ? `<a class="tag lnk" data-a="repo" data-p="${esc(n.repo)}">↗ repo</a>` : '']);
  if (v.refs.length) {
    h += h4('Tham chiếu', v.refs.length) + `<table class="kv">${v.refs.map(r =>
      `<tr><td>${esc(r.k)}</td><td>${refTarget(r)}</td>${lineCell(r.line)}</tr>`).join('')}</table>`;
  }
  if (v.items.length) {
    h += h4('Mục định nghĩa', v.items.length) + `<table class="kv">${v.items.map(m =>
      `<tr><td>${esc(m.type)}</td><td><b>${esc(m.name)}</b>${m.deps?.length ? `<div class="muted">${esc(m.deps.join(' '))}</div>` : ''}</td>${lineCell(m.line)}</tr>`).join('')}</table>`;
  }
  if (v.vars.length) {
    h += h4('Biến', v.vars.length) + `<input class="flt" placeholder="lọc biến…" data-flt="#vars tr">
      <div class="scroll"><table class="kv" id="vars">${v.vars.map(([ln, k, op, val]) =>
      `<tr>${lineCell(ln)}<td>${esc(k)} <span class="muted">${esc(op)}</span></td><td>${esc(val)}</td></tr>`).join('')}</table></div>`;
  }
  h += h4('Nội dung', v.trunc ? '(4000 dòng đầu)' : '') + `<div class="code">${v.text.split('\n').map((l, i) =>
    `<div id="L${i + 1}"><b>${i + 1}</b>${highlight(l)}</div>`).join('')}</div>`;
  return h;
}

// ---------------------------------------------------------------- API

const RENDERERS = { root: dirPanel, dir: dirPanel, repo: repoPanel, cat: catPanel, file: filePanel };

export function showPanel(n) {
  const fn = RENDERERS[n.type];
  if (!fn) return;
  P.classList.remove('hide');
  P.innerHTML = fn(n);
  P.scrollTop = 0;
  if (n.type === 'file' && n.line && n.fv) setTimeout(() => goLine(n.line), 30);
}

export function goLine(ln) {
  const row = document.getElementById('L' + ln);
  if (!row) return;
  P.querySelectorAll('.code .hl').forEach(x => x.classList.remove('hl'));
  row.classList.add('hl');
  row.scrollIntoView({ block: 'center' });
}

export const togglePanel = () => P.classList.toggle('hide');

// actions: { deep(btn), open(item), repo(path), cat(id) }
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
      case 'line': return goLine(+d.n);
      case 'copy': navigator.clipboard?.writeText(d.t); a.textContent = 'Đã copy'; return;
    }
  });
  P.addEventListener('input', e => {
    const sel = e.target.dataset.flt;
    if (!sel) return;
    const q = e.target.value.toLowerCase();
    P.querySelectorAll(sel).forEach(x => (x.style.display = x.textContent.toLowerCase().includes(q) ? '' : 'none'));
  });
}
