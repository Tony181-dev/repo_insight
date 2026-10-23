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
  bash: words('if then else elif fi for while until do done case esac in function select time coproc'),
  groovy: words('def pipeline agent stages stage steps sh script when environment post always success failure node ' +
    'parallel return if else for in true false null import class new try catch'),
  tf: words('resource data module provider variable output locals terraform true false null for in if each count'),
  docker: words('FROM AS RUN CMD ENTRYPOINT COPY ADD ENV ARG WORKDIR EXPOSE USER VOLUME LABEL HEALTHCHECK SHELL ' +
    'ONBUILD STOPSIGNAL'),
  yaml: words('true false null yes no on off'),
  js: words('const let var function return if else for while do switch case break continue new this class extends ' +
    'import export from default async await try catch finally throw typeof instanceof true false null undefined'),
};

// bash: lenh dung san cua shell / cong cu build hay gap (to mau rieng de doc script de hon)
const BASH_BUILTIN = words('echo printf cd pwd export source . set unset read local return exit shift trap eval exec ' +
  'test true false declare readonly typeset wait kill pushd popd dirs type command builtin alias unalias let ulimit ' +
  'umask getopts hash help jobs fg bg disown break continue mapfile readarray');
const BASH_CMD = words('make bitbake cmake ninja meson git repo python python3 pip pip3 mkdir rm cp mv ln cat grep egrep ' +
  'sed awk find xargs tar gzip unzip zip sudo docker podman gradle mvn npm npx yarn cargo go lunch mmm mm m croot ' +
  'chmod chown touch head tail sort uniq wc tee curl wget ssh scp rsync env nproc sleep date basename dirname ' +
  'realpath readlink install patch diff autoreconf configure qcc ntoaarch64-gcc gcc g++ clang ld ar strip adb fastboot');

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
  // shebang, comment (khong phai ${#x} / $#), chuoi "" duoc phep nhieu dong (bien ben trong to mau rieng), '...',
  // bien, ten ham, gan bien, tuy chon --x / -x, so, tu khoa / lenh, toan tu
  bash: [['sb', String.raw`^#![^\n]*`], ['cm', String.raw`(?<![\w$\\{])#[^\n]*`],
    ['st', String.raw`"(?:\\[\s\S]|[^"\\])*"|'[^']*'`],
    ['va', String.raw`\$\{[^}\n]*\}|\$\((?:[^()\n]|\([^()\n]*\))*\)|\$(?:\w+|[@#?$!*-])`],
    ['fn', String.raw`^[ \t]*(?:function[ \t]+)?[A-Za-z_][\w:.-]*(?=[ \t]*\(\))`],
    ['as', String.raw`\b[A-Za-z_]\w*(?=\+?=)`], ['op', String.raw`(?<=[\s=])--?[A-Za-z][\w-]*`], ['nu', NUM],
    ['id', String.raw`[A-Za-z_][\w.+-]*`], ['pu', String.raw`&&|\|\||[|;&]|>>?|<<?|\[\[|\]\]`]],
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
  if (/^(sh|bash|zsh|ksh)$/.test(ext) || /^\.?(bashrc|bash_profile|profile)$/.test(n)) return 'bash';
  if (/^(ya?ml)$/.test(ext)) return 'yaml';
  if (n === 'Dockerfile' || n.startsWith('Dockerfile.') || ext === 'dockerfile') return 'docker';
  if (n.startsWith('Jenkinsfile') || /^(groovy|gradle)$/.test(ext)) return 'groovy';
  if (/^(tf|tfvars|hcl)$/.test(ext)) return 'tf';
  if (/^(js|mjs|ts|json)$/.test(ext)) return 'js';
  if (/^(mk|mak|bb|bbappend|bbclass|inc|conf|cfg|ini|toml|cmake|txt|rc|te|use|build)$/.test(ext) ||
    /^(Makefile|makefile|GNUmakefile|Kbuild|Kconfig.*|CMakeLists\.txt|.*defconfig)$/.test(n)) return 'sh';
  return 'text';
}

// "... $VAR ... $(cmd) ..." -> phan chuoi + bien (bash thay bien ben trong chuoi nhay kep)
const IN_STR_VAR = new RegExp(VAR.replace(String.raw`\$\w+`, String.raw`\$(?:\w+|[@#?$!*-])`), 'g');
function inString(s) {
  const out = [];
  let last = 0;
  for (const m of s.matchAll(IN_STR_VAR)) {
    if (m.index > last) out.push(['st', s.slice(last, m.index)]);
    out.push(['va', m[0]]);
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(['st', s.slice(last)]);
  return out;
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
    if (cls === 'id') {
      cls = kw && kw.has(m[0]) ? 'kw' : '';
      if (!cls && lang === 'bash') cls = BASH_BUILTIN.has(m[0]) ? 'bi' : BASH_CMD.has(m[0]) ? 'cmd' : '';
    }
    if (cls === 'st' && lang === 'bash' && m[0][0] === '"') out.push(...inString(m[0]));
    else out.push([cls, m[0]]);
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
