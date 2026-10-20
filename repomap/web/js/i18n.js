// Ngon ngu giao dien: English (mac dinh) / Tieng Viet. Luu lua chon trong localStorage.
//   t('key', {n: 3})    chuoi giao dien
//   tl('kind')          nhan do server tra ve (tieng Anh) -> dich khi dang o tieng Viet

const DICT = {
  en: {
    searchPh: 'Search repos (/), e.g. kernel or kind:qnx',
    panelToggle: 'Show or hide the panel',
    panelResize: 'Resize details panel',
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
    tooBig: '(file over 32 MB: showing the first 32 MB)', partialParse: 'References and outline cover the first 4 MB of this file.',
    lines: '{n} lines', openRepo: 'Open repo',
    more: '+{n} more…',
    createBuildScript: 'Create build script', openBuildScript: 'Open build script',
    createScript: 'Create script', scriptLimit: 'A repo can have up to 3 scripts.', userScript: 'Script',
    creating: 'Creating…', buildScript: 'Build script', save: 'Save', saving: 'Saving…', savedAt: 'Saved {t}', unsaved: 'Unsaved changes',
    regenerate: 'Regenerate', regenAsk: 'Replace this script with a freshly generated one? Your edits will be lost.',
    deleteScript: 'Delete script', deleteAsk: 'Delete {name}? This cannot be undone.', yes: 'Yes', no: 'No',
    ok: 'OK', cancel: 'Cancel', renameScript: 'Rename', scriptNamePrompt: 'Enter a name for this script',
    scriptNamePlaceholder: 'e.g. clean-cache', scriptNamePreview: 'File name: {name}',
    scriptNameInvalid: 'Use 1–64 letters, digits, dots, underscores or hyphens; start with a letter or digit.',
    scriptNameReserved: 'That name is reserved for the build script.',
    leaveAsk: 'A script has unsaved changes. Discard them?',
    scriptHint: 'Generated from what repomap found in this module. Fill in anything set to TODO, then Save (Ctrl+S).',
    userScriptHint: 'Write your script here, then Save (Ctrl+S). It runs with bash in this module folder.',
    readOnly: 'Read only: {why}',
    runBuild: '▶ Run build', runScript: '▶ Run script', stopBuild: '■ Stop', buildLog: 'Build log', scriptLog: 'Script log',
    clearLog: 'Clear log', stopAsk: 'Stop this build?', stopScriptAsk: 'Stop this script?',
    buildRunning: 'Running… {t}', buildDone: 'Finished OK in {t}', buildFailed: 'Failed (exit {c}) after {t}',
    buildStopped: 'Stopped after {t}',
    scriptRunning: 'Running… {t}', scriptDone: 'Finished OK in {t}', scriptFailed: 'Failed (exit {c}) after {t}',
    scriptStopped: 'Stopped after {t}',
    lostConn: 'Lost the connection to the server, retrying… The script keeps running on the server.',
    runHint: 'Runs on the build server with bash; stops with Stop or when repomap stops (Ctrl+C).',
    runScriptHint: 'Runs with bash in this module folder; stops with Stop or when repomap stops (Ctrl+C).',
    openSource: 'Open full source', back: '← Back', findFile: 'Find file by name…',
    searchText: 'Search in files (Enter)…', results: '{n} results', resultsMore: '(first ones only)',
    noResults: 'No results', searching: 'Searching…', clear: 'Clear', emptyDir: 'Empty folder',
    pickFile: 'Pick a file on the left.', outline: 'Outline',
    viewTitle: 'Switch view', viewTree: 'Tree', viewBlocks: 'Blocks',
    archFilter: 'Filter repo or component…', archLoading: 'Detecting components in every repo…',
    archStat: '{b} blocks, {c} components, {r} repos, {s}s', archCount: '{b} blocks · {c} components',
    archHelp: 'Architecture of the whole workspace, detected from build and service files.<br><br>' +
      '• Each band is a layer: applications/services, libraries, platform/HAL, external libraries, kernel/drivers, ' +
      'board/BSP, build/tooling<br>• Each block is one repo in that layer, with the components found in it<br>' +
      '• Blocks are collapsed: click one to open it and draw its links (green = what it uses, ' +
      'dashed yellow = who uses it); click it again to close<br>' +
      '• Scroll to zoom, drag to move; click the layer chips to hide or show a layer',
    expandAll: 'Expand all', collapseAll: 'Collapse all', zoomReset: 'Back to 100%',
    archErrors: '{n} repos skipped (error, hover for details)',
    showAll: 'Show all ({n})',
    follow: 'Auto-center', followTitle: 'On: clicking a block glides it to the middle of the view',
    components: 'Components', uses: 'Uses', usedBy: 'Used by', builds: 'builds', showInTree: 'Show in tree',
    svcUnits: 'started by',
    layer_app: 'Applications & services', layer_lib: 'Libraries & frameworks',
    layer_hal: 'Platform / HAL / user-space drivers', layer_ext: 'System & third-party libraries',
    layer_kernel: 'Kernel & drivers', layer_board: 'Board / BSP / boot', layer_build: 'Build, CI & tooling',
    layerShort_app: 'App', layerShort_lib: 'Lib', layerShort_hal: 'HAL', layerShort_ext: 'External',
    layerShort_kernel: 'Kernel', layerShort_board: 'Board', layerShort_build: 'Build',
    type_exe: 'executable', type_svc: 'service', type_container: 'container', type_lib: 'library',
    type_iface: 'interface', type_kmod: 'kernel module', type_driver: 'driver', type_kernel: 'kernel',
    type_boot: 'bootloader', type_dt: 'device tree', type_machine: 'machine', type_board: 'board config',
    type_image: 'image', type_recipe: 'recipe', type_layer: 'Yocto layer', type_ci: 'CI / image build',
    type_infra: 'infrastructure', type_tool: 'tool', type_repo: 'repo', type_ext: 'external',
  },
  vi: {
    searchPh: 'Tìm repo (/), vd: kernel hoặc kind:qnx',
    panelToggle: 'Ẩn/hiện panel',
    panelResize: 'Kéo để thay đổi chiều rộng bảng thông tin',
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
    tooBig: '(file trên 32 MB: chỉ hiện 32 MB đầu)', partialParse: 'Tham chiếu và cấu trúc chỉ tính trên 4 MB đầu của file này.',
    lines: '{n} dòng', openRepo: 'Mở repo',
    more: '+{n} nữa…',
    createBuildScript: 'Tạo build script', openBuildScript: 'Mở build script',
    createScript: 'Tạo script', scriptLimit: 'Mỗi repo tạo tối đa 3 script.', userScript: 'Script',
    creating: 'Đang tạo…', buildScript: 'Build script', save: 'Lưu', saving: 'Đang lưu…', savedAt: 'Đã lưu {t}', unsaved: 'Chưa lưu',
    regenerate: 'Sinh lại', regenAsk: 'Thay script này bằng bản sinh mới? Những chỗ bạn đã sửa sẽ mất.',
    deleteScript: 'Xóa script', deleteAsk: 'Xóa {name}? Thao tác này không thể hoàn tác.', yes: 'Có', no: 'Không',
    ok: 'OK', cancel: 'Hủy', renameScript: 'Đổi tên', scriptNamePrompt: 'Nhập tên cho script này',
    scriptNamePlaceholder: 'ví dụ: clean-cache', scriptNamePreview: 'Tên file: {name}',
    scriptNameInvalid: 'Dùng 1–64 chữ cái, số, dấu chấm, gạch dưới hoặc gạch ngang; bắt đầu bằng chữ hoặc số.',
    scriptNameReserved: 'Tên này dành riêng cho build script.',
    leaveAsk: 'Script có thay đổi chưa lưu. Bỏ các thay đổi đó?',
    scriptHint: 'Sinh từ thông tin repomap đọc được trong module này. Điền các giá trị TODO rồi bấm Lưu (Ctrl+S).',
    userScriptHint: 'Viết nội dung script tại đây rồi bấm Lưu (Ctrl+S). Script chạy bằng bash trong thư mục module này.',
    readOnly: 'Chỉ đọc: {why}',
    runBuild: '▶ Chạy build', runScript: '▶ Chạy script', stopBuild: '■ Dừng', buildLog: 'Log build', scriptLog: 'Log script',
    clearLog: 'Xoá log', stopAsk: 'Dừng build này?', stopScriptAsk: 'Dừng script này?',
    buildRunning: 'Đang chạy… {t}', buildDone: 'Xong, thành công sau {t}', buildFailed: 'Lỗi (exit {c}) sau {t}',
    buildStopped: 'Đã dừng sau {t}',
    scriptRunning: 'Đang chạy… {t}', scriptDone: 'Xong, thành công sau {t}', scriptFailed: 'Lỗi (exit {c}) sau {t}',
    scriptStopped: 'Đã dừng sau {t}',
    lostConn: 'Mất kết nối tới server, đang thử lại… Script vẫn chạy trên server.',
    runHint: 'Chạy trên máy build bằng bash; dừng khi bấm Dừng hoặc khi tắt repomap (Ctrl+C).',
    runScriptHint: 'Chạy bằng bash trong thư mục module; dừng khi bấm Dừng hoặc khi tắt repomap (Ctrl+C).',
    openSource: 'Mở toàn bộ source', back: '← Quay lại', findFile: 'Tìm file theo tên…',
    searchText: 'Tìm trong nội dung (Enter)…', results: '{n} kết quả', resultsMore: '(chỉ hiện phần đầu)',
    noResults: 'Không có kết quả', searching: 'Đang tìm…', clear: 'Xoá', emptyDir: 'Thư mục trống',
    pickFile: 'Chọn một file bên trái.', outline: 'Cấu trúc',
    viewTitle: 'Đổi kiểu xem', viewTree: 'Cây', viewBlocks: 'Khối',
    archFilter: 'Lọc repo hoặc component…', archLoading: 'Đang nhận diện component trong mọi repo…',
    archStat: '{b} khối, {c} component, {r} repo, {s}s', archCount: '{b} khối · {c} component',
    archHelp: 'Kiến trúc tổng của workspace, tự nhận diện từ file build và file service.<br><br>' +
      '• Mỗi dải là một tầng: ứng dụng/service, thư viện, nền tảng/HAL, thư viện ngoài, kernel/driver, ' +
      'board/BSP, build/công cụ<br>• Mỗi khối là một repo trong tầng đó, kèm các component tìm được<br>' +
      '• Khối được thu gọn: bấm vào để mở và vẽ liên kết (xanh = khối đó dùng gì, ' +
      'vàng nét đứt = ai dùng nó); bấm lần nữa để đóng<br>' +
      '• Cuộn chuột để zoom, kéo để di chuyển; bấm các nút tầng để ẩn/hiện tầng',
    expandAll: 'Mở hết', collapseAll: 'Thu hết', zoomReset: 'Về 100%',
    archErrors: '{n} repo bị bỏ qua do lỗi (rê chuột để xem)',
    showAll: 'Hiện tất cả ({n})',
    follow: 'Tự căn giữa', followTitle: 'Bật: bấm vào khối thì view tự trượt để khối nằm giữa màn hình',
    components: 'Component', uses: 'Dùng', usedBy: 'Được dùng bởi', builds: 'build', showInTree: 'Xem trên cây',
    svcUnits: 'khởi chạy bởi',
    layer_app: 'Ứng dụng & service', layer_lib: 'Thư viện & framework',
    layer_hal: 'Nền tảng / HAL / driver user-space', layer_ext: 'Thư viện hệ thống & bên thứ ba',
    layer_kernel: 'Kernel & driver', layer_board: 'Board / BSP / boot', layer_build: 'Build, CI & công cụ',
    layerShort_app: 'App', layerShort_lib: 'Lib', layerShort_hal: 'HAL', layerShort_ext: 'Ngoài',
    layerShort_kernel: 'Kernel', layerShort_board: 'Board', layerShort_build: 'Build',
    type_exe: 'chương trình', type_svc: 'service', type_container: 'container', type_lib: 'thư viện',
    type_iface: 'interface', type_kmod: 'kernel module', type_driver: 'driver', type_kernel: 'kernel',
    type_boot: 'bootloader', type_dt: 'device tree', type_machine: 'machine', type_board: 'cấu hình board',
    type_image: 'image', type_recipe: 'recipe', type_layer: 'Yocto layer', type_ci: 'CI / build image',
    type_infra: 'hạ tầng', type_tool: 'công cụ', type_repo: 'repo', type_ext: 'bên ngoài',
  },
};

// nhan tieng Anh do server tra ve -> tieng Viet
const SERVER_VI = {
  'kind': 'loại', 'modified': 'file đang sửa', 'subject': 'nội dung commit', 'date': 'ngày', 'author': 'tác giả',
  'Other config': 'Config khác', '(root)': '(gốc)',
  'C/C++ system libraries': 'Thư viện hệ thống C/C++',
  'Android libraries (outside workspace)': 'Thư viện Android (ngoài workspace)',
  'Python packages': 'Gói Python', 'npm packages': 'Gói npm', 'Rust crates': 'Crate Rust', 'Go modules': 'Module Go',
  'Java / Maven libraries': 'Thư viện Java / Maven',
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
