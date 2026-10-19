// To mau code nhe, khong thu vien ngoai (tool chay offline).
//   highlightLines(text, lang) -> mang HTML, moi phan tu 1 dong (token nhieu dong, vd /* */, duoc cat theo dong).

import { esc } from './util.js';

const words = s => new Set(s.split(/\s+/));
const KW = {
  c: words('auto break case char const continue default do double else enum extern float for goto if inline int long ' +
    'register return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false ' +
    'nullptr NULL class namespace template typename public private protected virtual override final new delete this ' +
    'using try catch throw operator friend explicit constexpr noexcept static_cast reinterpret_cast const_cast ' +
    'dynamic_cast uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t int64_t size_t'),
  py: words('False None True and as assert async await break class continue def del elif else except finally for from ' +
    'global if import in is lambda nonlocal not or pass raise return try while with yield self cls match case'),
  sh: words('if then else elif fi for while until do done case esac in function return local export source exit set ' +
    'unset shift readonly declare ifeq ifneq ifdef ifndef endif define endef include inherit require python'),
  groovy: words('def pipeline agent stages stage steps sh script when environment post always success failure node ' +
    'parallel return if else for in true false null import class new try catch'),
  tf: words('resource data module provider variable output locals terraform true false null for in if each count'),
  docker: words('FROM AS RUN CMD ENTRYPOINT COPY ADD ENV ARG WORKDIR EXPOSE USER VOLUME LABEL HEALTHCHECK SHELL ' +
    'ONBUILD STOPSIGNAL'),
  yaml: words('true false null yes no on off'),
  js: words('const let var function return if else for while do switch case break continue new this class extends ' +
    'import export from default async await try catch finally throw typeof instanceof true false null undefined'),
};

const STR = String.raw`"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'`;
const NUM = String.raw`\b(?:0x[\da-fA-F]+|\d[\d_]*(?:\.\d+)?)[uUlLfF]*\b`;
const ID = String.raw`[A-Za-z_][\w$]*`;
const HASH_CM = String.raw`#[^\n]*`;
const C_CM = String.raw`\/\*[\s\S]*?\*\/|\/\/[^\n]*`;
const VAR = String.raw`\$\{[^}\n]*\}|\$\([^)\n]*\)|\$\w+`;

// [lop css, regex] - thu tu = do uu tien
const RULES = {
  c: [['cm', C_CM], ['pp', String.raw`^[ \t]*#[ \t]*\w+`], ['st', STR], ['nu', NUM], ['id', ID]],
  py: [['cm', HASH_CM], ['st', String.raw`"""[\s\S]*?"""|'''[\s\S]*?'''`], ['st', STR], ['de', String.raw`@[\w.]+`],
    ['nu', NUM], ['id', ID]],
  sh: [['cm', HASH_CM], ['st', STR], ['va', VAR], ['nu', NUM], ['id', ID]],
  yaml: [['cm', HASH_CM], ['ky', String.raw`^[ \t]*(?:-[ \t]+)?[\w.\-/"']+(?=[ \t]*:(?:\s|$))`], ['st', STR], ['va', VAR],
    ['nu', NUM], ['id', ID]],
  docker: [['cm', HASH_CM], ['st', STR], ['va', VAR], ['id', ID]],
  tf: [['cm', C_CM + '|' + HASH_CM], ['st', STR], ['va', VAR], ['nu', NUM], ['id', ID]],
  groovy: [['cm', C_CM], ['st', STR], ['va', VAR], ['nu', NUM], ['id', ID]],
  js: [['cm', C_CM], ['st', STR + '|`(?:\\\\.|[^`\\\\])*`'], ['nu', NUM], ['id', ID]],
  text: [],
};
const COMPILED = {};
for (const [lang, rules] of Object.entries(RULES)) {
  COMPILED[lang] = rules.length ? new RegExp(rules.map(([, re]) => `(${re})`).join('|'), 'gm') : null;
}

export function langOf(path) {
  const n = path.split('/').pop();
  const ext = n.includes('.') ? n.split('.').pop().toLowerCase() : '';
  if (/^(c|h|cc|cpp|cxx|hh|hpp|hxx|ipp|inl|dts|dtsi|aidl|hal|bp)$/.test(ext)) return 'c';
  if (ext === 'py') return 'py';
  if (/^(ya?ml)$/.test(ext)) return 'yaml';
  if (n === 'Dockerfile' || n.startsWith('Dockerfile.') || ext === 'dockerfile') return 'docker';
  if (n.startsWith('Jenkinsfile') || /^(groovy|gradle)$/.test(ext)) return 'groovy';
  if (/^(tf|tfvars|hcl)$/.test(ext)) return 'tf';
  if (/^(js|mjs|ts|json)$/.test(ext)) return 'js';
  if (/^(sh|bash|mk|mak|bb|bbappend|bbclass|inc|conf|cfg|ini|toml|cmake|txt|rc|te|use|build)$/.test(ext) ||
    /^(Makefile|makefile|GNUmakefile|Kbuild|Kconfig.*|CMakeLists\.txt|.*defconfig)$/.test(n)) return 'sh';
  return 'text';
}

function tokens(text, lang) {
  const re = COMPILED[lang], rules = RULES[lang], kw = KW[lang === 'sh' ? 'sh' : lang];
  const out = [];
  if (!re) return [['', text]];
  let last = 0, m;
  re.lastIndex = 0;
  while ((m = re.exec(text))) {
    if (m[0] === '') { re.lastIndex++; continue; }
    if (m.index > last) out.push(['', text.slice(last, m.index)]);
    const g = m.findIndex((x, i) => i > 0 && x !== undefined);
    let cls = rules[g - 1][0];
    if (cls === 'id') cls = kw && kw.has(m[0]) ? 'kw' : '';
    out.push([cls, m[0]]);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(['', text.slice(last)]);
  return out;
}

export function highlightLines(text, lang = 'text') {
  const lines = [''];
  for (const [cls, s] of tokens(text, lang)) {
    s.split('\n').forEach((part, i) => {
      if (i) lines.push('');
      if (part) lines[lines.length - 1] += cls ? `<span class="tk-${cls}">${esc(part)}</span>` : esc(part);
    });
  }
  return lines;
}
