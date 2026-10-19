// Ngon ngu giao dien: English (mac dinh) / Tieng Viet. Luu lua chon trong localStorage.
//   t('key', {n: 3})    chuoi giao dien
//   tl('kind')          nhan do server tra ve (tieng Anh) -> dich khi dang o tieng Viet

const DICT = {
  en: {
    searchPh: 'Search repos (/), e.g. kernel or kind:qnx',
    panelToggle: 'Show or hide the panel',
    langTitle: 'Interface language',
    fit: 'Fit', expand: 'Expand one level', collapse: 'Collapse',
    loading: 'Reading manifest…',
    help: 'Click a repo in the tree to see its details.<br><br>' +
      '• Click a repo: quick facts and key files<br>' +
      '• <b>Deep scan</b>: every build and config file, recipes, modules<br>' +
      '• Click a file: variables and resolved include/require/inherit; click again to go deeper<br><br>' +
      'Drag to move, scroll to zoom, <b>/</b> to search.',
    status: '{n} repos, {mode}, {s}s',
    noMatch: 'No matches',
    noRepos: 'No repos found (no .repo/manifest.xml and no .git folders).',
    scanning: 'Scanning…', deepScan: 'Deep scan', rescan: 'Rescan',
    copyPath: 'Copy path', copied: 'Copied', reading: 'Reading…',
    repoKinds: 'Repo kinds', repos: 'Repos',
    keyFiles: 'Key files', keyFilesCat: 'key files',
    topDirs: 'Top-level folders', dirsFiles: '{d} folders, {f} files',
    deepInfo: '{n} files, {src}, {s}s', byExt: 'By file extension', byDir: 'By folder',
    filter: 'filter…', filterVars: 'filter variables…', showing: 'showing {a}/{b}',
    refs: 'References', defs: 'Definitions', vars: 'Variables', content: 'Content',
    first4000: '(first 4000 lines)', lines: '{n} lines', openRepo: 'Open repo',
    more: '+{n} more…',
    openSource: 'Open full source', back: '← Back', findFile: 'Find file by name…',
    searchText: 'Search in files (Enter)…', results: '{n} results', resultsMore: '(first ones only)',
    noResults: 'No results', searching: 'Searching…', clear: 'Clear', emptyDir: 'Empty folder',
    pickFile: 'Pick a file on the left.', outline: 'Outline',
  },
  vi: {
    searchPh: 'Tìm repo (/), vd: kernel hoặc kind:qnx',
    panelToggle: 'Ẩn/hiện panel',
    langTitle: 'Ngôn ngữ giao diện',
    fit: 'Vừa khung', expand: 'Mở thêm 1 tầng', collapse: 'Thu gọn',
    loading: 'Đang đọc manifest…',
    help: 'Bấm vào một repo trên cây để xem chi tiết.<br><br>' +
      '• Bấm repo: thông tin nhanh và file chính<br>' +
      '• <b>Quét sâu</b>: toàn bộ file build/config, recipe, module<br>' +
      '• Bấm file: biến, include/require/inherit đã resolve; bấm tiếp để đi sâu<br><br>' +
      'Kéo để di chuyển, cuộn để zoom, <b>/</b> để tìm.',
    status: '{n} repo, {mode}, {s}s',
    noMatch: 'Không thấy',
    noRepos: 'Không tìm thấy repo nào (không có .repo/manifest.xml và không có thư mục .git).',
    scanning: 'Đang quét…', deepScan: 'Quét sâu', rescan: 'Quét lại',
    copyPath: 'Copy đường dẫn', copied: 'Đã copy', reading: 'Đang đọc…',
    repoKinds: 'Loại repo', repos: 'Repo',
    keyFiles: 'File chính', keyFilesCat: 'file chính',
    topDirs: 'Thư mục gốc', dirsFiles: '{d} thư mục, {f} file',
    deepInfo: '{n} file, {src}, {s}s', byExt: 'Theo đuôi file', byDir: 'Theo thư mục',
    filter: 'lọc…', filterVars: 'lọc biến…', showing: 'hiện {a}/{b}',
    refs: 'Tham chiếu', defs: 'Mục định nghĩa', vars: 'Biến', content: 'Nội dung',
    first4000: '(4000 dòng đầu)', lines: '{n} dòng', openRepo: 'Mở repo',
    more: '+{n} nữa…',
    openSource: 'Mở toàn bộ source', back: '← Quay lại', findFile: 'Tìm file theo tên…',
    searchText: 'Tìm trong nội dung (Enter)…', results: '{n} kết quả', resultsMore: '(chỉ hiện phần đầu)',
    noResults: 'Không có kết quả', searching: 'Đang tìm…', clear: 'Xoá', emptyDir: 'Thư mục trống',
    pickFile: 'Chọn một file bên trái.', outline: 'Cấu trúc',
  },
};

// nhan tieng Anh do server tra ve -> tieng Viet
const SERVER_VI = {
  'kind': 'loại', 'modified': 'file đang sửa', 'subject': 'nội dung commit', 'date': 'ngày', 'author': 'tác giả',
  'Other config': 'Config khác', '(root)': '(gốc)',
};

export const LANGS = ['en', 'vi'];
const KEY = 'repomap-lang';
let lang = 'en';
try { if (LANGS.includes(localStorage.getItem(KEY))) lang = localStorage.getItem(KEY); } catch (e) { /* storage bi chan */ }
const listeners = [];

export const getLang = () => lang;

export const t = (key, params = {}) =>
  (DICT[lang][key] ?? DICT.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => params[k] ?? '');

export const tl = s => (lang === 'vi' && SERVER_VI[s]) || s;

export function applyStatic() {
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(e => (e.textContent = t(e.dataset.i18n)));
  document.querySelectorAll('[data-i18n-html]').forEach(e => (e.innerHTML = t(e.dataset.i18nHtml)));
  document.querySelectorAll('[data-i18n-ph]').forEach(e => (e.placeholder = t(e.dataset.i18nPh)));
  document.querySelectorAll('[data-i18n-title]').forEach(e => (e.title = t(e.dataset.i18nTitle)));
  document.querySelectorAll('[data-lang]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === lang)));
}

export function setLang(l) {
  if (!LANGS.includes(l) || l === lang) return;
  lang = l;
  try { localStorage.setItem(KEY, l); } catch (e) { /* storage bi chan */ }
  applyStatic();
  listeners.forEach(fn => fn(l));
}

export const onLangChange = fn => listeners.push(fn);
