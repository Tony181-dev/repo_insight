// Block view: kien truc tong cua workspace. Moi dai = 1 tang (app / lib / HAL / ext / kernel / board / build),
// moi khoi = 1 repo trong 1 tang, ben trong la cac component tu nhan dien (/api/arch).
// Bam khoi: ve phu thuoc (dung / bi dung), lam mo cac khoi khac, hien chi tiet o panel.

import { $, esc, api, KIND_COLOR } from './util.js';
import { t, tl } from './i18n.js';
import { showHtml } from './panel.js';

const A = $('#arch'), SCROLL = $('#arch-scroll'), CANVAS = $('#arch-canvas'), Q = $('#arch-q'), STAT = $('#arch-stat');
const SHOW = 12;          // so component hien trong 1 khoi (panel hien day du)

export const LAYER_COLOR = {
  app: '#58a6ff', lib: '#bc8cff', hal: '#e3b341', ext: '#56d4dd', kernel: '#ff7b72', board: '#7ee787', build: '#8b949e',
};
const ICON = {
  exe: '▶', svc: '⚙', container: '⬢', lib: '▣', iface: '◇', kmod: '✦', driver: '✦', kernel: '◆', boot: '⏻',
  dt: '⌗', machine: '▤', board: '▤', image: '▤', recipe: '◦', layer: '≡', ci: '↻', infra: '☁', tool: '⚒',
  repo: '▢', ext: '○',
};

let data = null, sel = null, loading = false;
let byId = new Map(), out = new Map(), inc = new Map();
const hidden = new Set();

const layerName = id => t('layer_' + id);
const color = b => (b.kind === 'ext' ? LAYER_COLOR.ext : KIND_COLOR[b.kind] || '#6b7280');
const typeName = c => t('type_' + (c.svc && c.type === 'exe' ? 'svc' : c.type));
const parent = p => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');

function index() {
  byId = new Map(data.boxes.map(b => [b.id, b]));
  out = new Map(); inc = new Map();
  for (const [a, b, n, k] of data.edges) {
    if (!byId.has(a) || !byId.has(b)) continue;
    (out.get(a) || out.set(a, []).get(a)).push([b, n, k]);
    (inc.get(b) || inc.set(b, []).get(b)).push([a, n, k]);
  }
}

// ---------------------------------------------------------------- ve

const chip = c => `<span class="ac t-${c.type}${c.svc ? ' svc' : ''}" title="${esc(`${typeName(c)}: ${c.name}`
  + (c.note ? ` (${c.note})` : '') + (c.svc ? `\n${t('svcUnits')}: ${c.svc.join(', ')}` : ''))}"><i>${ICON[c.svc ? 'svc' : c.type] || '•'}</i>${esc(c.name)}</span>`;

function boxHtml(b) {
  const rel = new Set([...(out.get(sel) || []), ...(inc.get(sel) || [])].map(x => x[0]));
  const cls = !sel ? '' : b.id === sel ? ' on' : rel.has(b.id) ? ' rel' : ' dim';
  const sub = b.repo && parent(b.repo);
  return `<div class="abox${cls}" data-id="${esc(b.id)}" style="--lc:${LAYER_COLOR[b.layer]}">
    <div class="abox-hd"><span class="dot" style="background:${color(b)}"></span><b title="${esc(b.repo || b.label)}">${esc(tl(b.label))}</b>${
      sub ? `<small title="${esc(b.repo)}">${esc(sub)}/</small>` : ''}</div>
    <div class="abox-cs">${b.comps.slice(0, SHOW).map(chip).join('')}${
      b.total > SHOW ? `<span class="ac more">${t('more', { n: b.total - SHOW })}</span>` : ''}</div></div>`;
}

function matches(b, q) {
  return !q || b.repo.toLowerCase().includes(q) || tl(b.label).toLowerCase().includes(q)
    || b.comps.some(c => c.name.toLowerCase().includes(q));
}

function render() {
  if (!data) return;
  const q = Q.value.trim().toLowerCase();
  let h = '', nb = 0, nc = 0;
  for (const L of data.layers) {
    if (hidden.has(L.id)) continue;
    const boxes = data.boxes.filter(b => b.layer === L.id && matches(b, q));
    if (!boxes.length) continue;
    const c = LAYER_COLOR[L.id], comps = boxes.reduce((s, b) => s + b.total, 0);
    nb += boxes.length; nc += comps;
    h += `<section class="band" style="--lc:${c};border-color:${c}55;background:${c}0d">
      <div class="band-hd"><b>${esc(layerName(L.id))}</b><small>${t('archCount', { b: boxes.length, c: comps })}</small></div>
      <div class="band-body">${boxes.map(boxHtml).join('')}</div></section>`;
  }
  CANVAS.innerHTML = `<svg id="arch-links"></svg>` + (h || `<div class="muted arch-none">${t('noMatch')}</div>`);
  STAT.textContent = t('archStat', { b: nb, c: nc, r: data.repos, s: data.took });
  renderLayers();
  requestAnimationFrame(drawLinks);
}

function renderLayers() {
  $('#arch-layers').innerHTML = data.layers.filter(L => data.boxes.some(b => b.layer === L.id)).map(L =>
    `<button class="lchip${hidden.has(L.id) ? ' off' : ''}" data-l="${L.id}" title="${esc(layerName(L.id))}">
      <span class="dot" style="background:${LAYER_COLOR[L.id]}"></span>${esc(t('layerShort_' + L.id))}</button>`).join('');
}

// duong noi khoi dang chon voi cac khoi no dung (xanh) / bi dung boi (vang, net dut)
function drawLinks() {
  const svg = $('#arch-links');
  if (!svg || !sel) return;
  const cr = CANVAS.getBoundingClientRect();
  svg.setAttribute('width', CANVAS.scrollWidth);
  svg.setAttribute('height', CANVAS.scrollHeight);
  const pos = id => {
    const e = CANVAS.querySelector(`.abox[data-id="${CSS.escape(id)}"]`);
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { x: r.left - cr.left, y: r.top - cr.top, w: r.width, h: r.height };
  };
  let h = `<defs>${['out', 'in'].map(k => `<marker id="ah-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
    markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" class="ah ${k}"/></marker>`).join('')}</defs>`;
  // fa / fb: vi tri diem dau / cuoi doc theo canh (0..1) - nhieu duong cung 1 khoi thi trai ra, khong de len nhau
  const curve = (a, b, cls, k, fa, fb) => {
    let x1, y1, x2, y2, d;
    if (b.y > a.y + a.h - 2 || a.y > b.y + b.h - 2) {                 // khac hang: noi duoi -> dau
      const down = b.y > a.y;
      x1 = a.x + a.w * fa; y1 = down ? a.y + a.h : a.y;
      x2 = b.x + b.w * fb; y2 = down ? b.y : b.y + b.h;
      const m = (y2 - y1) / 2;
      d = `M${x1},${y1}C${x1},${y1 + m} ${x2},${y2 - m} ${x2},${y2}`;
    } else {                                                          // cung hang: noi canh ben
      const right = b.x > a.x;
      x1 = right ? a.x + a.w : a.x; y1 = a.y + a.h * fa;
      x2 = right ? b.x : b.x + b.w; y2 = b.y + b.h * fb;
      const m = (x2 - x1) / 2;
      d = `M${x1},${y1}C${x1 + m},${y1} ${x2 - m},${y2} ${x2},${y2}`;
    }
    h += `<path class="alk ${cls}${k === 'build' ? ' build' : ''}" d="${d}" marker-end="url(#ah-${cls})"/>`;
  };
  const me = pos(sel);
  const ends = list => (list || []).map(([id, , k]) => [pos(id), k]).filter(x => x[0])
    .sort((p, q) => p[0].x + p[0].w / 2 - q[0].x - q[0].w / 2);
  const outs = ends(out.get(sel)), ins = ends(inc.get(sel));
  if (!me) return;
  outs.forEach(([p, k], i) => curve(me, p, 'out', k, (i + 1) / (outs.length + 1), 0.5));
  ins.forEach(([p, k], i) => curve(p, me, 'in', k, 0.5, (i + 1) / (ins.length + 1)));
  svg.innerHTML = h;
}

// ---------------------------------------------------------------- panel

function linkList(list) {
  return `<div class="ls">${list.map(([id, n, k]) => {
    const b = byId.get(id);
    return `<a data-a="abox" data-id="${esc(id)}"><span class="dot" style="background:${LAYER_COLOR[b.layer]}"></span>
      ${esc(tl(b.label))} <small>${esc(layerName(b.layer))}${k === 'build' ? ' · ' + t('builds') : ''} · ${n}</small></a>`;
  }).join('')}</div>`;
}

function panelHtml(b) {
  let h = `<div class="hd"><span class="dot" style="background:${color(b)}"></span><div>
    <div class="ttl">${esc(tl(b.label))}</div><div class="sub">${esc(b.repo ? b.repo + ' · ' : '')}${esc(layerName(b.layer))}</div></div></div>`;
  if (b.repo) {
    h += `<div class="btns"><button data-a="repo" data-p="${esc(b.repo)}">${t('showInTree')}</button>
      <button data-a="src" data-repo="${esc(b.repo)}" data-base="" data-f="">${t('openSource')}</button></div>`;
  }
  h += `<h4>${t('components')} <span>${b.total > b.comps.length ? t('showing', { a: b.comps.length, b: b.total }) : b.total}</span></h4>`;
  if (b.comps.length > 10) h += `<input class="flt" placeholder="${esc(t('filter'))}" data-flt="#acomps tr">`;
  h += `<table class="kv" id="acomps">${b.comps.map(c => {
    const extra = [c.note, c.svc && `${t('svcUnits')}: ${c.svc.join(', ')}`, c.uses && `${t('uses')}: ${c.uses.join(', ')}`]
      .filter(Boolean).map(x => `<div class="muted">${esc(x)}</div>`).join('');
    const file = c.f ? `<a class="lnk" data-a="src" data-repo="${esc(b.repo)}" data-base="${esc(parent(c.f))}" data-f="${esc(c.f)}"
      title="${esc(c.f)}">${esc(c.f.split('/').slice(-2).join('/'))}${c.n ? ':' + c.n : ''}</a>` : '';
    return `<tr><td>${ICON[c.svc ? 'svc' : c.type] || '•'} ${esc(typeName(c))}</td><td><b>${esc(c.name)}</b>${extra}${file}</td></tr>`;
  }).join('')}</table>`;
  if (out.get(b.id)) h += `<h4>${t('uses')} <span>${out.get(b.id).length}</span></h4>` + linkList(out.get(b.id));
  if (inc.get(b.id)) h += `<h4>${t('usedBy')} <span>${inc.get(b.id).length}</span></h4>` + linkList(inc.get(b.id));
  return h;
}

function showInfo() {
  if (!data) return;
  const b = sel && byId.get(sel);
  showHtml(b ? panelHtml(b) : `<div class="muted">${t('archHelp')}</div>`);
}

function select(id, scroll = false) {
  sel = id && byId.has(id) ? id : null;
  render();
  showInfo();
  if (scroll && sel) {
    requestAnimationFrame(() => CANVAS.querySelector(`.abox[data-id="${CSS.escape(sel)}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
  }
}

// ---------------------------------------------------------------- API

async function load(force = false) {
  if (loading) return;
  loading = true;
  $('#arch-rescan').disabled = true;
  if (!data) CANVAS.innerHTML = `<div class="load arch-none">${t('archLoading')}</div>`;
  else STAT.textContent = t('archLoading');
  const d = await api('arch', force ? { force: 1 } : {});
  loading = false;
  $('#arch-rescan').disabled = false;
  if (d.error) { CANVAS.innerHTML = `<div class="err arch-none">${esc(d.error)}</div>`; return; }
  data = d;
  index();
  if (sel && !byId.has(sel)) sel = null;
  render();
  showInfo();
}

export function showArch() {
  A.hidden = false;
  if (!data) load();
  else { render(); showInfo(); }
}

export const hideArch = () => { A.hidden = true; };

export const archRefresh = () => { if (!A.hidden && data) { render(); showInfo(); } };

// chon khoi dau tien cua repo (vd khi chon repo tu o tim kiem)
export function archFocusRepo(path) {
  if (!data) return;
  const b = data.boxes.find(x => x.repo === path);
  if (b) { hidden.delete(b.layer); Q.value = ''; select(b.id, true); }
}

export function initArch() {
  CANVAS.addEventListener('click', e => {
    const b = e.target.closest('.abox');
    select(b && b.dataset.id !== sel ? b.dataset.id : null);
  });
  $('#panel').addEventListener('click', e => {
    const a = e.target.closest('[data-a="abox"]');
    if (a) { const b = byId.get(a.dataset.id); if (b) hidden.delete(b.layer); select(a.dataset.id, true); }
  });
  $('#arch-layers').addEventListener('click', e => {
    const c = e.target.closest('[data-l]');
    if (!c) return;
    hidden.has(c.dataset.l) ? hidden.delete(c.dataset.l) : hidden.add(c.dataset.l);
    render();
  });
  Q.addEventListener('input', render);
  $('#arch-rescan').addEventListener('click', () => load(true));
  addEventListener('resize', () => { if (!A.hidden) drawLinks(); });
}
