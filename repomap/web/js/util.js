// Tien ich dung chung + trang thai toan cuc.

export const $ = s => document.querySelector(s);

export const esc = s => String(s ?? '').replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export const tail = (f, k = 2) => f.split('/').slice(-k).join('/');
export const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export async function api(endpoint, query = {}) {
  try {
    const r = await fetch(`/api/${endpoint}?` + new URLSearchParams(query));
    return await r.json();
  } catch (e) {
    return { error: String(e) };
  }
}

export const KIND_COLOR = {
  'yocto-layer': '#f0a530', 'yocto-multi': '#e8862a', 'bitbake': '#ff6b3d', 'kernel': '#f05252',
  'u-boot': '#ec4899', 'android-device': '#2fbf71', 'android': '#9ad94a', 'qnx': '#3b82f6',
  'cmake': '#14b8a6', 'meson': '#06b6d4', 'bazel': '#10b981', 'autotools': '#a8a29e', 'make': '#94a3b8',
  'devicetree': '#a78bfa', 'cargo': '#fb923c', 'go': '#38bdf8', 'gradle': '#65a30d', 'node': '#facc15',
  'python': '#60a5fa', 'other': '#6b7280', 'missing': '#3f3f46',
};

export function colorOf(n) {
  if (n.type === 'root') return '#58a6ff';
  if (n.type === 'dir') return '#7d8590';
  if (n.type === 'cat') return '#d2a8ff';
  return KIND_COLOR[n.kind] || '#6b7280';
}

// trang thai dung chung giua cac module
export const store = {
  data: null,      // ket qua /api/tree
  root: null,      // node goc cua cay
  sel: null,       // node dang chon
  byRepo: {},      // repo path -> node
};
