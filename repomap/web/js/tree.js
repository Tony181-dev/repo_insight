// Mo hinh cay: node, dung cay tu danh sach repo, gan du lieu nap them (insight / quet sau / file).
//
// Kieu node: root | dir | repo | cat (nhom file) | file | more (nap them) | goto (nhay toi repo khac)

import { store, tail } from './util.js';
import { t, tl } from './i18n.js';

export const BATCH = 40;   // so file hien moi lan trong 1 nhom
let nextId = 0;

export function node(o, parent) {
  o.id = ++nextId;
  o.children = [];
  o.parent = parent;
  if (parent && o.repo === undefined) o.repo = parent.type === 'repo' ? parent.path : parent.repo;
  if (o.kind === undefined) o.kind = store.byRepo[o.repo]?.kind ?? parent?.kind;
  return o;
}

// nhan hien thi theo ngon ngu dang chon (node 'more' va nhom 'key files' do giao dien tu dat ten)
export function labelOf(n) {
  if (n.type === 'more') return t('more', { n: n.rest });
  if (n.i18n) return t(n.i18n);
  return tl(n.label);
}

export function countLabel(n) {
  if (n.type === 'dir' || n.type === 'root') return String(n.cnt);
  if (n.type === 'cat') return n.total > n.items.length ? `${n.items.length}/${n.total}` : String(n.total);
  return '';
}

export const expandable = n =>
  n.children.length > 0 || n.type === 'cat' ||
  (n.type === 'repo' && !n.ins) || (n.type === 'file' && !n.fv);

export function visible(n, out = []) {
  out.push(n);
  if (n.open) n.children.forEach(c => visible(c, out));
  return out;
}

// ---------------------------------------------------------------- dung cay tu manifest

export function buildTree(data) {
  const root = node({ type: 'root', label: data.root.split(/[\\/]/).pop() || data.root, path: '' });
  const dirs = { '': root };
  const dirNode = p => {
    if (dirs[p]) return dirs[p];
    const i = p.lastIndexOf('/');
    const parent = dirNode(i < 0 ? '' : p.slice(0, i));
    const n = node({ type: 'dir', label: p.slice(i + 1), path: p }, parent);
    parent.children.push(n);
    return (dirs[p] = n);
  };

  for (const r of data.repos) {
    let n = dirs[r.path];
    if (n) Object.assign(n, { type: 'repo', r, kind: r.kind });      // thu muc da tao truoc boi repo con
    else {
      const i = r.path.lastIndexOf('/');
      const parent = dirNode(i < 0 ? '' : r.path.slice(0, i));
      n = node({ type: 'repo', label: r.path.slice(i + 1), path: r.path, r, kind: r.kind }, parent);
      parent.children.push(n);
      dirs[r.path] = n;
    }
    store.byRepo[r.path] = n;
  }

  compress(root);
  count(root);
  autoOpen(root, 45);
  return root;
}

// gop chuoi thu muc chi co 1 thu muc con: vendor -> qcom -> opensource  =>  vendor/qcom/opensource
function compress(n) {
  n.children.forEach(compress);
  if (n.type === 'dir' && n.children.length === 1 && n.children[0].type === 'dir') {
    const c = n.children[0];
    n.label += '/' + c.label;
    n.path = c.path;
    n.children = c.children;
    n.children.forEach(x => (x.parent = n));
  }
}

function count(n) {
  n.children.sort((a, b) => a.label.localeCompare(b.label));
  n.cnt = n.children.reduce((s, c) => s + count(c), 0);
  return n.cnt + (n.type === 'repo' ? 1 : 0);
}

function autoOpen(root, limit) {
  root.open = true;
  let shown = root.children.length;
  const queue = [root];
  while (queue.length) {
    for (const c of queue.shift().children) {
      if (c.type === 'dir' && shown + c.children.length < limit) {
        c.open = true;
        shown += c.children.length;
        queue.push(c);
      }
    }
  }
}

export function collapseAll(root) {
  (function walk(n) { n.open = false; n.children.forEach(walk); })(root);
  root.open = true;
}

// ---------------------------------------------------------------- nhom file / file

export function fileNode(it, parent) {
  return node({ type: 'file', label: it.l || tail(it.f), f: it.f, line: it.n || 0, repo: it.repo }, parent);
}

function batch(cat, offset) {
  for (const it of cat.items.slice(offset, offset + BATCH)) cat.children.push(fileNode(it, cat));
  const rest = cat.items.length - offset - BATCH;
  if (rest > 0) cat.children.push(node({ type: 'more', label: '', rest, off: offset + BATCH }, cat));
}

export function fillCat(cat) {
  if (!cat.filled) { cat.filled = true; batch(cat, 0); }
}

export function loadMore(more) {
  const cat = more.parent;
  cat.children.splice(cat.children.indexOf(more), 1);
  batch(cat, more.off);
}

// tim hoac them 1 file con (khi bam tu panel)
export function ensureChild(parent, it) {
  if (parent.type === 'cat') fillCat(parent);
  let c = parent.children.find(x => x.f === it.f && x.repo === it.repo && (x.line || 0) === (it.n || 0));
  if (!c) {
    c = fileNode(it, parent);
    const more = parent.children.findIndex(x => x.type === 'more');
    more < 0 ? parent.children.push(c) : parent.children.splice(more, 0, c);
  }
  parent.open = true;
  return c;
}

// ---------------------------------------------------------------- gan du lieu tu API

const catNode = (parent, grp, c) =>
  node({ type: 'cat', label: c.label, cid: c.id, grp, items: c.items, total: c.count }, parent);

export function attachInsight(repo, ins) {
  if (ins.error || !ins.keys.length) return;
  const items = ins.keys.map(f => ({ f }));
  const keys = catNode(repo, 'ins', { id: 'keys', label: 'key files', items, count: items.length });
  keys.i18n = 'keyFilesCat';
  repo.children.unshift(keys);
}

export function attachDeep(repo, deep) {
  const kids = repo.children.filter(c => c.grp !== 'deep');
  if (deep.error) { repo.children = kids; return; }
  const cats = deep.cats.map(c => catNode(repo, 'deep', c));
  repo.children = [...kids.filter(c => c.grp === 'ins'), ...cats, ...kids.filter(c => !c.grp)];
}

export function attachRefs(file, fv) {
  const seen = new Set();
  for (const r of fv.refs || []) {
    if (!r.to) continue;
    const key = r.to.repo + '|' + r.to.file;
    if (seen.has(key)) continue;
    seen.add(key);
    if (!r.to.file) {
      file.children.push(node({ type: 'goto', label: '→ repo ' + r.to.repo, target: r.to.repo, repo: r.to.repo }, file));
      continue;
    }
    const where = r.to.repo === file.repo ? tail(r.to.file) : r.to.repo.split('/').pop() + ':' + tail(r.to.file, 1);
    file.children.push(fileNode({ repo: r.to.repo, f: r.to.file, l: `${r.k} ${where}` }, file));
  }
}
