// Khung xem code cho file rat dai: chi ve cac dong dang thay (virtual scroll), noi dung nap theo tung doan
// tu /api/lines khi cuon toi. Dung chung cho panel ben phai va man hinh full source.
//
//   const cv = createCodeView(box, {repo, f, fv, refLines})   fv: ket qua /api/file (doan dau + tong so dong)
//   cv.goLine(n)    cuon toi dong n va to sang
//   cv.destroy()    bo khung (khi doi file / ve lai panel)

import { api } from './util.js';
import { highlightLines, langOf } from './highlight.js';

const LH = 18;          // chieu cao 1 dong (px) - khop voi CSS .vcode
const OVERSCAN = 60;    // ve them so dong tren/duoi vung nhin thay

export function createCodeView(box, { repo, f, fv, refLines = new Map() }) {
  const total = fv.lines, size = fv.chunk || 2000, lang = langOf(f);
  const chunks = new Map([[0, highlightLines(fv.text, lang)]]);    // so thu tu doan -> HTML tung dong
  const loading = new Set();
  let hl = 0, raf = 0, dead = false;

  box.classList.add('vcode');
  box.style.setProperty('--gw', String(total).length + 'ch');
  box.innerHTML = `<div class="vc-space" style="height:${total * LH}px"><div class="vc-win"></div></div>`;
  const win = box.firstChild.firstChild;

  async function load(i) {
    if (chunks.has(i) || loading.has(i)) return;
    loading.add(i);
    const r = await api('lines', { repo, f, from: i * size + 1, n: size });
    loading.delete(i);
    if (dead) return;
    chunks.set(i, r.error ? [] : highlightLines(r.lines.join('\n'), lang));
    schedule();
  }

  function render() {
    raf = 0;
    const top = box.scrollTop, h = box.clientHeight || 600;
    const a = Math.max(0, Math.floor(top / LH) - OVERSCAN), b = Math.min(total, Math.ceil((top + h) / LH) + OVERSCAN);
    let html = '';
    for (let i = a; i < b; i++) {
      const c = chunks.get(Math.floor(i / size));
      if (!c) load(Math.floor(i / size));
      const n = i + 1, ri = refLines.get(n);
      const cls = (n === hl ? 'hl ' : '') + (ri !== undefined ? 'ref' : '');
      html += `<div${cls ? ` class="${cls.trim()}"` : ''}${ri !== undefined ? ` data-ref="${ri}"` : ''}>`
        + `<b>${n}</b>${c ? c[i % size] || ' ' : '<span class="tk-cm">…</span>'}</div>`;
    }
    win.style.transform = `translateY(${a * LH}px)`;
    win.innerHTML = html;
  }

  const schedule = () => { if (!raf && !dead) raf = requestAnimationFrame(render); };
  box.addEventListener('scroll', schedule, { passive: true });
  render();

  return {
    goLine(n) {
      hl = n;
      box.scrollTop = Math.max(0, (n - 1) * LH - box.clientHeight / 2 + LH);
      render();
    },
    destroy() {
      dead = true;
      box.removeEventListener('scroll', schedule);
    },
  };
}
