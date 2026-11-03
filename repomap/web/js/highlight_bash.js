// To mau bash bang bo quet co trang thai (regex khong du: "$(cd "$(dirname "$0")" && pwd)" long nhau, va chi tu o VI TRI
// LENH moi la lenh: `echo done make` thi done / make la tham so). Khong co cu phap day du, muc tieu la mau dung.
//
//   bashTokens(text) -> [[lop, doan text], ...]   ghep cac doan lai dung bang text, ke ca khi script dang go do
//   lop: sb shebang, cm comment, kw tu khoa, fn ten ham, cmd lenh, bi lenh dung san, as ten bien khi gan, va $bien,
//        st chuoi, es ky tu escape, nu so, op tuy chon, pu toan tu, '' chu thuong

const words = s => new Set(s.split(' '));
const KEYWORD = words('if then elif else fi for while until do done case esac function select time');
const DECLARE = words('local export declare typeset readonly');
const BUILTIN = words('echo printf cd pwd source . : set unset read return exit shift trap eval test true false ' +
  'wait kill pushd popd dirs type alias unalias let ulimit umask getopts hash help jobs fg bg disown break continue ' +
  'mapfile readarray');
const PREFIX_BUILTIN = words('exec command builtin');          // tu tiep theo van la lenh
const PREFIX_CMD = words('sudo nohup env xargs nice');
const CONT_AFTER = new Set(['if', 'then', 'elif', 'else', 'while', 'until', 'do', 'time']);

const MAX_DEPTH = 40;                                          // $( long nhau qua sau: phan con lai la chu thuong
const NUMBER = /^(?:0[xX][\da-fA-F]+|\d+(?:\.\d+)?)$/;
const ASSIGN = /^([A-Za-z_]\w*(?:\[[^\]]*\])?)(\+?=)/;
const NAME = /^[A-Za-z_]\w*$/;
const FN_NAME = /^[A-Za-z_][\w:.-]*$/;

export function bashTokens(text) {
  const out = [], n = text.length;
  let i = 0, depth = 0;

  const push = (cls, s) => {
    if (!s) return;
    const last = out[out.length - 1];
    if (last && last[0] === cls) last[1] += s;
    else out.push([cls, s]);
  };
  const take = (cls, end) => { push(cls, text.slice(i, end)); i = end; };
  const at = re => { re.lastIndex = i; return re.exec(text); };

  // ${...}: toi } khop ngoac; chua dong thi chi to het dong (dang go do)
  function braceEnd(from) {
    let open = 1;
    for (let p = from; p < n; p++) {
      const c = text[p];
      if (c === '\n') return p;
      if (c === '{') open++;
      else if (c === '}' && !--open) return p + 1;
    }
    return n;
  }

  function arithmetic() {
    take('pu', i + 3);
    let open = 0, m;
    while (i < n) {
      const c = text[i];
      if (c === '$') expansion();
      else if (c === '(') { open++; take('', i + 1); }
      else if (c === ')' && open) { open--; take('', i + 1); }
      else if (c === ')') return take('pu', text[i + 1] === ')' ? i + 2 : i + 1);
      else if ((m = at(/\d+/y))) take('nu', i + m[0].length);
      else if ((m = at(/[A-Za-z_]\w*/y))) take('va', i + m[0].length);
      else take('', i + 1);
    }
  }

  function substitution(open, close) {
    take('pu', i + open);
    code(close);
    if (text[i] === close) take('pu', i + 1);
  }

  function expansion() {                                       // text[i] === '$'
    const c = text[i + 1];
    if (c === '(' && depth < MAX_DEPTH) return text[i + 2] === '(' ? arithmetic() : substitution(2, ')');
    if (c === '{') return take('va', braceEnd(i + 2));
    const m = at(/\$(?:[A-Za-z_]\w*|\d|[@#?$!*-])/y);
    return take(m ? 'va' : '', m ? i + m[0].length : i + 1);
  }

  // noi dung "..." (close = ") hoac heredoc khong quote (close = null): bash van thay bien / $() ben trong
  function quoted(limit, close) {
    let start = i;
    const flush = () => push('st', text.slice(start, i));
    while (i < limit) {
      const c = text[i];
      if (c === close) { i++; break; }
      if (c === '\\' && /[$`"\\\n]/.test(text[i + 1] || '')) {
        flush();
        take('es', i + 2);
        start = i;
      } else if ((c === '$' && at(/\$[({A-Za-z_\d@#?$!*-]/y)) || (c === '`' && depth < MAX_DEPTH)) {
        flush();
        if (c === '$') expansion();
        else substitution(1, '`');
        start = i;
      } else i++;
    }
    flush();
  }

  function heredoc(h) {
    let p = i, end = n, stop = n;
    while (p < n) {
      let e = text.indexOf('\n', p);
      if (e < 0) e = n;
      const line = text.slice(p, e);
      if ((h.strip ? line.replace(/^\t+/, '') : line) === h.delim) { end = p; stop = e; break; }
      p = e + 1;
    }
    if (h.expand) quoted(end, null);
    else take('st', end);
    if (i < stop) take('st', stop);
  }

  // doan code toi `end` (')' cua $(, '`', hoac het file). Trang thai rieng cho moi lop long nhau.
  function code(end) {
    depth++;
    let cmd = true;             // tu tiep theo o vi tri lenh
    let next = '';              // tu tiep theo la: fn ten ham | var bien cua for | target dich cua redirect
    let waitIn = '';            // dang cho tu `in` cua for / case
    let decl = false;           // tham so cua local / export...: ten bien
    let assign = false;         // dang o gia tri cua FOO=...: lenh van con o phia sau
    let inTest = false, array = false, pattern = false;
    let cases = 0, parens = 0;
    const docs = [];
    const endCmd = () => { cmd = true; decl = false; next = ''; waitIn = ''; assign = false; };
    const used = () => { if (!assign) cmd = false; next = ''; };

    while (i < n) {
      const c = text[i];
      if (end && c === end && !pattern && (end === '`' || !parens)) break;
      const start = i === 0 || /[\s;&|()<>`]/.test(text[i - 1]);
      let m;

      if (c === ' ' || c === '\t' || c === '\r') {
        assign = false;
        take('', i + 1);
      } else if (c === '\n') {
        take('', i + 1);
        if (!array) endCmd();
        while (docs.length) heredoc(docs.shift());
      } else if (c === '\\') {
        take('', Math.min(i + 2, n));
        if (text[i - 1] !== '\n') used();
      } else if (c === '#' && start) {
        const e = text.indexOf('\n', i);
        take(i === 0 && text[1] === '!' ? 'sb' : 'cm', e < 0 ? n : e);
      } else if (c === "'") {
        const e = text.indexOf("'", i + 1);
        take('st', e < 0 ? n : e + 1);
        used();
      } else if (c === '"') {
        take('st', i + 1);
        quoted(n, '"');
        used();
      } else if (c === '$' && text[i + 1] === "'") {
        take('st', i + at(/\$'(?:\\[\s\S]|[^'\\])*'?/y)[0].length);
        used();
      } else if (c === '$') {
        expansion();
        used();
      } else if (c === '`') {
        if (depth < MAX_DEPTH) substitution(1, '`');
        else take('', i + 1);
        used();
      } else if ((c === '<' || c === '>' || (start && /[\d&]/.test(c))) &&
                 (m = at(/(?:\d+|&)?(?:<<<|<<-?|>>|>&|<&|<>|>\||>|<)/y))) {
        assign = false;
        take('pu', i + m[0].length);
        const op = m[0].replace(/^(?:\d+|&)/, '');
        const doc = op.startsWith('<<') && op !== '<<<' ? at(/[ \t]*(?:'([^'\n]+)'|"([^"\n]+)"|\\?([\w.-]+))/y) : null;
        if (doc) {
          docs.push({ delim: doc[1] ?? doc[2] ?? doc[3], expand: doc[3] !== undefined, strip: op === '<<-' });
          take('st', i + doc[0].length);
        } else next = 'target';
      } else if ((m = at(/;;&|;;|;&|&&|\|\||\|&|[;|&]/y))) {
        take('pu', i + m[0].length);
        assign = false;
        if (!inTest && !pattern) endCmd();
        if (m[0].length > 1 && m[0][0] === ';' && cases) pattern = true;
      } else if (c === '(') {
        array = text[i - 1] === '=';                           // FOO=(a b): phan tu, khong phai lenh
        take('pu', i + 1);
        parens++;
        cmd = !array;
      } else if (c === ')') {
        take('pu', i + 1);
        if (pattern) { pattern = false; cmd = true; continue; }
        if (parens) parens--;
        array = false;
        cmd = false;
      } else if ((m = at(/[^\s;&|<>()'"`$\\]+/y))) {
        const w = m[0], stop = i + w.length, whole = !/["'$`\\]/.test(text[stop] || ' ');
        if (!start) { take('', stop); continue; }              // phan sau cua 1 tu: "$a"/out, -j"$n"x
        if (next === 'target') { next = ''; take(NUMBER.test(w) ? 'nu' : '', stop); continue; }
        if (next === 'var') { next = ''; take('as', stop); continue; }
        if (next === 'fn') {
          next = '';
          take('fn', stop);
          if ((m = at(/[ \t]*\([ \t]*\)/y))) take('pu', i + m[0].length);
          cmd = true;
          continue;
        }
        if (pattern) {
          if (w === 'esac' && whole) { pattern = false; cases--; cmd = false; take('kw', stop); } else take('', stop);
          continue;
        }
        if (waitIn && w === 'in' && whole) {
          if (waitIn === 'case') { pattern = true; cases++; }
          waitIn = '';
          cmd = false;
          take('kw', stop);
          continue;
        }
        if (w === ']]' || w === ']') { inTest = false; cmd = false; take('pu', stop); continue; }
        if (cmd) {
          if ((m = ASSIGN.exec(w))) {
            take('as', i + m[1].length);
            take('pu', i + m[2].length);
            take('', stop);
            assign = true;
          } else if (w === '{' || w === '!') take('pu', stop);
          else if (w === '}') { cmd = false; take('pu', stop); }
          else if (w === '[') { cmd = false; take('bi', stop); }
          else if (w === '[[') { cmd = false; inTest = true; take('pu', stop); }
          else if (whole && FN_NAME.test(w) && /^[ \t]*\([ \t]*\)/.test(text.slice(stop, stop + 16))) next = 'fn';   // vong sau to
          else if (whole && KEYWORD.has(w)) {
            take('kw', stop);
            cmd = CONT_AFTER.has(w);
            if (w === 'function') next = 'fn';
            else if (w === 'for' || w === 'select') { next = 'var'; waitIn = 'for'; }
            else if (w === 'case') waitIn = 'case';
          } else if (whole && DECLARE.has(w)) { decl = true; cmd = false; take('kw', stop); }
          else if (whole && PREFIX_BUILTIN.has(w)) take('bi', stop);
          else if (whole && PREFIX_CMD.has(w)) take('cmd', stop);
          else if (whole && BUILTIN.has(w)) { cmd = false; take('bi', stop); }
          else if (/^--?[A-Za-z]/.test(w)) take('op', stop);   // tuy chon cua sudo / env / xargs
          else { cmd = false; take('cmd', stop); }
        } else if ((m = /^--?[A-Za-z@?][\w.-]*/.exec(w))) {
          take('op', i + m[0].length);
          take('', stop);
        } else if (decl && (m = ASSIGN.exec(w))) {
          take('as', i + m[1].length);
          take('pu', i + m[2].length);
          take('', stop);
        } else if (decl && NAME.test(w)) take('as', stop);
        else if (NUMBER.test(w) || /^-\d+(?:\.\d+)?$/.test(w)) take('nu', stop);
        else if (/^(?:==?|!=|=~)$/.test(w)) take('pu', stop);
        else take('', stop);
      } else take('', i + 1);
    }
    depth--;
  }

  code('');
  if (i < n) push('', text.slice(i));
  return out;
}
