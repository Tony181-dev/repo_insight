# repomap

Xem nhanh hệ sinh thái một workspace `repo sync` (Yocto/BitBake, Android, QNX, Linux kernel/U-Boot,
Python, DevOps) bằng giao diện web: cây repo vẽ từ manifest, bấm vào đâu thì nạp sâu vào đó,
và mở được toàn bộ source của một module.

- Chỉ cần Python 3.8+ (thư viện chuẩn), không cần mạng, không cần build workspace.
- Phân tích tĩnh: đọc manifest, file build/config. Không chạy bitbake/make/soong.
- Server chỉ nghe `127.0.0.1`.

Trang hướng dẫn có bản vẽ minh hoạ bấm thử được: [docs/index.html](docs/index.html)
(bật GitHub Pages với nguồn `main` / `docs` để xem trực tuyến).

## Chạy

Trong các lệnh dưới đây, thay các tên mẫu bằng giá trị thật của bạn:

| Tên mẫu | Là gì | Ví dụ |
|---|---|---|
| `~/your_workspace_root` | thư mục gốc bạn đã chạy `repo init` / `repo sync`, bên trong có thư mục `.repo/` | `~/projects/android13` |
| `your_build_server` | tên hoặc IP máy build bạn ssh vào | `10.0.0.25` |
| `user` | tài khoản ssh trên máy build | `dev` |

```sh
repomap ~/your_workspace_root                       # menu: 1. Run scan / 2. Configuration / 3. Quick setup / 0. Exit
repomap                                             # như trên, dùng workspace đã quét lần trước
repomap ~/your_workspace_root --run                 # bỏ qua menu, chạy ngay (dùng trong script)
repomap ~/your_workspace_root --json vendor/foo     # in JSON insight + quét sâu 1 repo
repomap --local                                     # trên PC: trang Connect / Saved servers / Deploy
python3 -m repomap ...                              # chạy từ source, cùng tham số
```

Máy build ở xa: chạy `repomap ~/your_workspace_root` trên máy đó. Lần đầu chọn `3. Quick setup` và dán lệnh ssh
của server (`ssh -p <ssh_port> <user>@<host>.vbee.lge.com`): tool tự điền ssh host/user/port, port server = 6969.
Hoặc `2. Configuration` để nhập từng giá trị (port ssh tự đọc từ `/etc/ssh/sshd_config`). Rồi `1. Run scan`.
Tool in sẵn lệnh tunnel, ví dụ `ssh -N -L 6969:127.0.0.1:6969 user@your_build_server`
(thêm `-p <port>` nếu ssh không ở port 22); chạy lệnh đó trên PC, giữ cửa sổ mở, rồi mở http://127.0.0.1:6969.
Port nào đang bận thì tool báo rõ và chuyển sang port kế tiếp.

### Trên PC: không cần gõ lệnh

Trên PC Windows có Python 3.8+, nhấp đúp `repomap-pc.cmd` (trong gói phát hành, cạnh `repomap.pyz`) hoặc chạy
`python repomap.pyz --local`. Trình duyệt mở http://127.0.0.1:6960 với 3 tab:

- **Connect**: Configuration (SSH host/user/port, port server = port PC, mặc định 6969; chấm xanh/đỏ), *Quick setup*
  (dán lệnh ssh), *Save preset*. Nút **Connect** mở cửa sổ ssh tunnel (gõ password ở đó) và tự mở trang repomap khi
  server trả lời. Port trên PC đang bận thì báo ngay.
- **Saved servers**: tối đa 5 preset, chọn một preset để điền vào Configuration.
- **Deploy to server**: `scp -r` một lần, đẩy `repomap.pyz` + `repomap.ini` (theo Configuration) vào `~/.bin` trên server.
  Trên server chỉ cần `python3 ~/.bin/repomap.pyz` → `1. Run scan`, rồi bấm Connect trên PC.

Cấu hình và preset của PC lưu ở `~/.config/repomap/local.json`. Trang tự tắt ~3 phút sau khi đóng tab.

Giao diện có nút **EN / VI** cạnh ô tìm kiếm (mặc định tiếng Anh).

## Cấu hình

Cách dễ nhất là menu `3. Quick setup` hoặc `2. Configuration`: nhập rồi lưu vào `~/.config/repomap/config.ini`
(hoặc file cấu hình đang dùng), lần sau không phải nhập lại. File `repomap.ini` nằm cạnh `repomap.pyz` (tab Deploy
trên PC đẩy lên `~/.bin`) cũng được đọc, sau `./repomap.ini`. Workspace quét lần trước cũng được nhớ. Cũng có thể sửa file bằng tay:

```sh
repomap --init-config                 # tạo ~/.config/repomap/config.ini (có chú thích)
repomap --init-config ./repomap.ini   # hoặc file cấu hình riêng cho thư mục hiện tại
repomap ~/your_workspace_root --config my.ini           # chỉ định file
```

```ini
[server]
host = 127.0.0.1      # 0.0.0.0 = mở cho cả mạng (không có xác thực)
port = 6969           # bận thì báo và tự thử port+1… tối đa port_tries lần
port_tries = 20
open_browser = auto   # auto: chỉ mở khi có màn hình (DISPLAY/WAYLAND_DISPLAY); yes / no

[ssh]                 # để in lệnh: ssh -N -L <local_port>:127.0.0.1:<port> [-p <port>] <user>@<host>
host =                # trống = tên đầy đủ (FQDN) của máy này
user =                # trống = user hiện tại
port =                # trống = đọc từ /etc/ssh/sshd_config, mặc định 22
local_port =          # trống = giống port server

[scan]
workspace =           # workspace quét lần trước (menu tự điền)
workers = 0           # 0 = tự chọn theo số CPU
```

Thứ tự ưu tiên: tham số dòng lệnh (`--port`, `--host`, `--no-browser`, `--workers`) > file cấu hình > mặc định.
File được tìm theo thứ tự: `--config` > `$REPOMAP_CONFIG` > `./repomap.ini` > `~/.config/repomap/config.ini`.

## Nạp theo tầng

| Tầng | Khi nào | Làm gì |
|---|---|---|
| 0 | Khởi động | Đọc `.repo/manifest.xml` (include, local_manifests, remove/extend-project) → cây repo. Phân loại nhanh bằng file đánh dấu ở gốc repo. Không có manifest thì tìm thư mục `.git`. |
| 1 | Bấm repo | Manifest, git HEAD, số file sửa; layer Yocto (collection, depends, machines…), kernel version/arch, BoardConfig/AndroidProducts, QNX variants, module ở gốc; danh sách file chính. |
| 2 | Bấm **Quét sâu** | Toàn bộ file (`git ls-files`), gom nhóm: recipe/bbappend/machine/image, Android.bp/.mk + module, defconfig/dts/Kconfig, QNX buildfile/variant, CMake/Meson/Make… |
| 3 | Bấm file | Biến, module/target, tham chiếu đã resolve: `require/include/inherit`, `inherit-product`, `#include` dts và C/C++, `source` Kconfig, `add_subdirectory`, recipe ↔ bbappend, `SRC_URI` → repo trong manifest, Python `import`, GitLab CI `include`, Ansible role/`import_tasks`, Terraform module. Bấm tiếp để đi sâu. |
| + | Bấm **Open full source** | Ở panel repo (cả repo) hoặc file (thư mục của module): cây thư mục nạp lười + nội dung có tô màu, tìm file theo tên, tìm nội dung (`git grep`), bấm tham chiếu để nhảy file. **← Back** (hoặc Esc / nút Back của trình duyệt) quay lại cây. |
| + | Bấm **Create build script** | Ở panel repo: tạo `repomap_build_script.sh` ở gốc repo, đoán lệnh build theo loại module (xem dưới). File hiện thành node màu vàng đầu nhánh repo; bấm vào để sửa và **Save** (Ctrl+S) ngay trên giao diện, **Regenerate** để sinh lại. |

### Build script

`repomap_build_script.sh` là bản nháp, đoán từ những gì tool đọc được, không chạy gì khi tạo:

| Module | Lệnh sinh ra | Tự tìm |
|---|---|---|
| Android | `source build/envsetup.sh` → `lunch` → `mmm <repo>` (repo device: `m`) | lunch target từ `COMMON_LUNCH_CHOICES` |
| Yocto | `source <oe-init-build-env> <build>` → `bitbake <recipe>` | recipe có `SRC_URI` trỏ vào repo này, image của layer, thư mục build có sẵn |
| Kernel / U-Boot | `make O=out ARCH=… <defconfig>` → `make` | arch và defconfig nếu chỉ có một |
| QNX | `source qnxsdp-env.sh` → `make` | `~/qnx*/qnxsdp-env.sh` |
| CMake, Meson, Bazel, Autotools, Cargo, Go, Gradle/Maven, npm, Python, Make, Docker | lệnh chuẩn | |

Giá trị không tìm được để ở khối đầu file là `TODO`; script dừng với thông báo rõ nếu chưa điền. Cách build khác
tìm thấy được ghi kèm ở cuối file (dạng comment). Tên file được thêm vào `.git/info/exclude` của repo nên không
làm bẩn `git status`.

**▶ Run build** (trong editor của script) chạy file đó trên máy build bằng `bash -l` (lưu bản đang sửa trước),
log hiện trực tiếp bên dưới; node script nhấp nháy trong cây khi đang build. Mỗi module chạy một build một lúc.

| Khi nào | Build |
|---|---|
| Bấm **■ Stop** | Dừng cả nhóm tiến trình (SIGTERM, sau 10 giây SIGKILL) |
| Tắt repomap: Ctrl+C, `kill`, phiên ssh chạy repomap bị đứt (SIGHUP) | Dừng theo |
| repomap bị `kill -9` | Linux: bash bảo vệ nhận SIGTERM (`PR_SET_PDEATHSIG`) và dừng cả nhóm |
| Trình duyệt / ssh tunnel mất kết nối, đóng tab | **Vẫn chạy**. Giao diện báo mất kết nối và tự thử lại; nối lại thì đọc tiếp log từ chỗ đang dở |

Log của lần chạy gần nhất: `~/.cache/repomap/builds-<hash>/<repo>.log` (hoặc `$REPOMAP_CACHE_DIR`). Script chạy không có
stdin, nên lệnh cần nhập (vd `sudo` hỏi mật khẩu) sẽ lỗi thay vì treo. `REPOMAP_BASH` chọn bash khác nếu cần.

Sửa/lưu/chạy file chỉ được phép khi server nghe `127.0.0.1` và request đến từ chính trang repomap của user đang chạy
server (trên Linux kiểm tra cả user sở hữu kết nối, nên user khác trên máy build dùng chung không ghi được);
xem `repomap/guard.py`. Khi chạy với `--host 0.0.0.0` thì chỉ xem.

Python: đọc `pyproject.toml`/`setup.cfg`/`setup.py`/`requirements*.txt` (tên, version, phụ thuộc, package, test),
`import` bằng `ast`. DevOps: Jenkins, GitLab CI, GitHub Actions, Docker/Compose, Ansible, Terraform, Helm.

## Cấu trúc

```
repomap/
  cli.py         dòng lệnh
  menu.py        menu tương tác: Run scan / Configuration
  settings.py    file cấu hình INI (host, port, ssh tunnel, ...), dò port sshd
  server.py      HTTP server + API JSON (/api/tree, insight, deep, file)
  workspace.py   danh sách repo, ánh xạ đường dẫn ↔ repo, cache, chỉ mục Yocto
  manifest.py    đọc manifest repo / tìm .git            (tầng 0)
  classify.py    phân loại nhanh repo                     (tầng 0)
  insight.py     thông tin nhanh 1 repo                   (tầng 1)
  deepscan.py    quét sâu 1 repo                          (tầng 2)
  insight_devops.py  thông tin nhanh Python + DevOps        (tầng 1)
  fileview.py    xem 1 file + resolve tham chiếu          (tầng 3)
  source.py      Open full source: liệt kê thư mục, tìm file / tìm nội dung
  buildscript.py sinh / đọc / lưu repomap_build_script.sh của module
  builds.py      chạy / dừng script đó, log build; dừng theo repomap
  guard.py       ai được ghi file qua server (chỉ user chạy repomap, từ chính trang repomap)
  local.py       chế độ PC (--local): Connect / Saved servers / Deploy
  parsers.py     parser BitBake/Make/Android.bp/Kconfig/DTS/CMake/QNX/C-C++
  parsers_py.py  parser Python (ast) + đọc TOML tối giản
  parsers_devops.py  parser YAML CI/Ansible/compose, Dockerfile, Jenkinsfile, Terraform
  rules.py       bảng nhóm file + biến quan trọng (sửa ở đây để thêm loại mới)
  web/           index.html, style.css, js/{main,tree,graph,panel,source,highlight,util,i18n}.js
tests/           unittest với workspace giả lập
tools/           build_pyz.py, make_release.py, install.sh, release_readme.txt (gói phát hành)
legacy/          modmap.py - bản 1 file cũ, giữ để tham khảo, không còn phát triển
```

CI (GitHub Actions) chạy test trên Linux với Python 3.8 và 3.12 mỗi lần push, tạo gói phát hành,
cài thử bằng `install.sh`, và đính kèm `repomap.pyz` cùng gói `.tar.gz`/`.zip` ở mục Artifacts.

Thêm hệ build mới: thêm nhóm vào `rules.CATS`, file đánh dấu vào `classify.MARKS`,
parser vào `parsers.parse_file`, màu vào `web/js/util.js` (`KIND_COLOR`).

## Phát hành cho người dùng (không đưa mã nguồn)

```sh
release.bat     # Windows: chạy test rồi tạo dist\repomap-<ver>.tar.gz và .zip (release.bat notest để bỏ test)
make release    # Linux: cùng gói như trên (cùng dùng tools/make_release.py)
```

Gói chỉ chứa `repomap.pyz`, `install.sh`, `README.txt`, `HUONG_DAN.html` (trang `docs/index.html`
đã cắt các đoạn đánh dấu `maintainer:start/end`) và `app.png`; không có test, công cụ build hay lịch sử git.
Người dùng cài trên máy Linux, không cần root, pip hay mạng:

```sh
tar xzf repomap-1.0.0.tar.gz && cd repomap-1.0.0 && sh install.sh   # -> ~/.local/bin/repomap
```

Lưu ý: `repomap.pyz` là file zip chứa mã Python dạng text; giải nén ra là đọc được.

## Đóng gói khác

```sh
build.bat     # Windows: chỉ tạo dist\repomap.pyz (build.bat notest để bỏ test)
make pyz      # Linux: như trên (cùng dùng tools/build_pyz.py)
make test     # unit test
make wheel    # dist/repomap-*.whl - pip install được, có lệnh `repomap` (cần tải setuptools từ PyPI)
```
