// Editor script co to mau: textarea trong suot (go, chon, Ctrl+S nhu cu) nam tren lop <pre> da to mau (highlight.js)
// va cot so dong. Cuon textarea thi lop to mau / so dong cuon theo.
//
//   editorHtml(text, readonly)  HTML dat vao panel (textarea#sc-ed giu nguyen id: panel / build.js dung)
//   initEditor(root)            gan su kien sau khi panel ve xong
//   refreshEditor(root)         to mau lai sau khi go (goi tu su kien input)

import { esc } from './util.js';
import { highlightLines } from './highlight.js';

const paint = text => highlightLines(text, 'bash').join('\n') + '\n ';       // dong cuoi trong van cuon toi duoc
const gutter = text => Array.from({ length: text.split('\n').length }, (_, i) => i + 1).join('\n') + '\n ';
const width = text => String(text.split('\n').length).length + 'ch';

export function editorHtml(text, readonly, cls = '') {
  return `<div class="sc-wrap" id="sc-wrap" style="--gw:${width(text)}">
    <pre class="sc-gut" id="sc-gut" aria-hidden="true">${gutter(text)}</pre>
    <pre class="sc-hl" id="sc-hl" aria-hidden="true">${paint(text)}</pre>
    <textarea id="sc-ed" class="sc-ed${cls}" spellcheck="false" autocomplete="off"${readonly ? ' readonly' : ''}>${
      esc(text)}</textarea></div>`;
}

function sync(root) {
  const ed = root.querySelector('#sc-ed'), hl = root.querySelector('#sc-hl'), gut = root.querySelector('#sc-gut');
  if (!ed || !hl) return;
  hl.scrollTop = gut.scrollTop = ed.scrollTop;
  hl.scrollLeft = ed.scrollLeft;
}

let raf = 0;
export function refreshEditor(root) {
  if (raf) return;
  raf = requestAnimationFrame(() => {
    raf = 0;
    const ed = root.querySelector('#sc-ed');
    if (!ed) return;
    root.querySelector('#sc-hl').innerHTML = paint(ed.value);
    root.querySelector('#sc-gut').textContent = gutter(ed.value);
    root.querySelector('#sc-wrap').style.setProperty('--gw', width(ed.value));
    sync(root);
  });
}

export function initEditor(root) {
  const ed = root.querySelector('#sc-ed');
  if (!ed) return;
  ed.addEventListener('scroll', () => sync(root), { passive: true });   // keo doi chieu cao: .sc-wrap tu theo
  sync(root);
}
