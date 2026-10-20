// Ve cay ngang bang SVG: layout, animation, pan/zoom.

import { $, colorOf, trunc, store, SCRIPT } from './util.js';
import { visible, countLabel, expandable, labelOf } from './tree.js';
import { isRunning } from './build.js';

const NS = 'http://www.w3.org/2000/svg';
const CHAR_W = 7.25, ROW = 23, COL_GAP = 46, MAX_LABEL = 42, ANIM_MS = 320;

const svg = $('#svg'), viewport = $('#vp'), gLinks = $('#gl'), gNodes = $('#gn');
const els = new Map();                 // node.id -> {g, shape, text, title, link}
let view = { x: 40, y: 0, k: 1 };
let onNodeClick = () => {};
let animId = 0, drag = null, moved = false;

const el = (tag, attrs = {}) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
};

const width = n => {
  const c = countLabel(n);
  return 12 + (trunc(labelOf(n), MAX_LABEL).length + (c ? c.length + 1 : 0)) * CHAR_W;
};

// ---------------------------------------------------------------- layout

function layout() {
  const colW = [];
  let row = 0;
  (function walk(n, depth) {
    n.depth = depth;
    colW[depth] = Math.max(colW[depth] || 0, width(n));
    const kids = n.open ? n.children : [];
    if (!kids.length) n.ty = row++ * ROW;
    else {
      kids.forEach(c => walk(c, depth + 1));
      n.ty = (kids[0].ty + kids[kids.length - 1].ty) / 2;
    }
  })(store.root, 0);
  const colX = [0];
  for (let i = 1; i < colW.length; i++) colX[i] = colX[i - 1] + colW[i - 1] + COL_GAP;
  const vis = visible(store.root);
  vis.forEach(n => (n.tx = colX[n.depth]));
  return vis;
}

// ---------------------------------------------------------------- node SVG

function draw(n) {
  const g = el('g', { class: 'nd t-' + n.type + (n.script ? ` script${n.customScript ? ' custom-script' : ''}` : '') });
  const r = { repo: 6, more: 3, file: 4, goto: 4 }[n.type] || 5;
  const shape = n.type === 'cat' ? el('rect', { x: -5, y: -5, width: 10, height: 10, rx: 2 }) : el('circle', { r });
  shape.setAttribute('stroke', colorOf(n));
  const text = el('text', { x: 11, dy: '.35em' }), title = el('title');
  g.append(el('circle', { class: 'halo', r: 10 }), shape, text, title);
  g.addEventListener('click', e => { e.stopPropagation(); if (!moved) onNodeClick(n); });
  gNodes.append(g);
  const e = { g, shape, text, title };
  if (n.parent) { e.link = el('path', { class: 'lk' }); gLinks.append(e.link); }
  return e;
}

function update(n, e) {
  e.text.textContent = trunc(labelOf(n), MAX_LABEL);
  const c = countLabel(n);
  if (c) { const s = el('tspan', { class: 'cnt', dx: 7 }); s.textContent = c; e.text.append(s); }
  e.shape.style.fill = n.open || !expandable(n) ? 'var(--bg)' : colorOf(n);
  e.g.classList.toggle('sel', n === store.sel);
  if (n.script) e.g.classList.toggle('running', isRunning(n.repo, n.scriptName || SCRIPT));
  e.title.textContent = n.f ? `${n.repo}/${n.f}` : n.path || labelOf(n);
}

export function refresh(n) {
  const e = n && els.get(n.id);
  if (e) update(n, e);
}

// ---------------------------------------------------------------- render + animation

export function render() {
  const vis = layout();
  const keep = new Set(vis.map(n => n.id));
  for (const [id, e] of els) {
    if (!keep.has(id)) { e.g.remove(); e.link?.remove(); els.delete(id); }
  }
  for (const n of vis) {
    let e = els.get(n.id);
    if (!e) {                                   // node moi: bay ra tu vi tri cha
      e = draw(n);
      els.set(n.id, e);
      const p = n.parent;
      n.x = p ? p.x ?? p.tx : n.tx;
      n.y = p ? p.y ?? p.ty : n.ty;
    }
    update(n, e);
    n.fx = n.x; n.fy = n.y;
  }
  const id = ++animId, t0 = performance.now();
  (function step(now) {
    if (id !== animId) return;
    let t = Math.min(1, (now - t0) / ANIM_MS);
    t = 1 - Math.pow(1 - t, 3);
    for (const n of vis) {
      n.x = n.fx + (n.tx - n.fx) * t;
      n.y = n.fy + (n.ty - n.fy) * t;
      const e = els.get(n.id);
      e.g.setAttribute('transform', `translate(${n.x},${n.y})`);
      if (e.link) {
        const p = n.parent, x1 = p.x + width(p) + 4, x2 = n.x - 7, m = (x1 + x2) / 2;
        e.link.setAttribute('d', `M${x1},${p.y}C${m},${p.y} ${m},${n.y} ${x2},${n.y}`);
      }
    }
    if (t < 1) requestAnimationFrame(step);
  })(t0);
}

// ---------------------------------------------------------------- pan / zoom

const applyView = () => viewport.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);

function panTo(x, y, k = view.k) {
  const s = { ...view }, t0 = performance.now();
  (function step(now) {
    let t = Math.min(1, (now - t0) / 350);
    t = 1 - Math.pow(1 - t, 3);
    view = { x: s.x + (x - s.x) * t, y: s.y + (y - s.y) * t, k: s.k + (k - s.k) * t };
    applyView();
    if (t < 1) requestAnimationFrame(step);
  })(t0);
}

export function centerOn(n, soft = false) {
  const r = svg.getBoundingClientRect(), sx = view.x + n.tx * view.k, sy = view.y + n.ty * view.k;
  if (soft && sx > 40 && sx < r.width * 0.6 && sy > 60 && sy < r.height - 60) return;
  panTo(r.width * 0.28 - n.tx * view.k, r.height / 2 - n.ty * view.k);
}

export function fit() {
  const r = svg.getBoundingClientRect();
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (const n of visible(store.root)) {
    x0 = Math.min(x0, n.tx); x1 = Math.max(x1, n.tx + width(n));
    y0 = Math.min(y0, n.ty); y1 = Math.max(y1, n.ty);
  }
  const k = Math.max(0.12, Math.min(1.3, (r.width - 60) / (x1 - x0 + 1), (r.height - 90) / (y1 - y0 + 1)));
  panTo(30 - x0 * k, (r.height - (y1 - y0) * k) / 2 - y0 * k, k);
}

export function resetView() {
  const r = svg.getBoundingClientRect();
  view = { x: 40, y: r.height / 2 - store.root.ty, k: 1 };
  applyView();
}

export function initGraph(onClick) {
  onNodeClick = onClick;
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    const r = svg.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const k = Math.max(0.12, Math.min(3, view.k * Math.exp(-e.deltaY * 0.0015)));
    view = { x: mx - (mx - view.x) * k / view.k, y: my - (my - view.y) * k / view.k, k };
    applyView();
  }, { passive: false });
  svg.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }; moved = false; });
  addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
    if (moved) { view.x = drag.vx + dx; view.y = drag.vy + dy; applyView(); }
  });
  addEventListener('pointerup', () => { drag = null; setTimeout(() => (moved = false), 0); });
}
