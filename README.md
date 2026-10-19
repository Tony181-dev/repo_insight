# repomap

Xem nhanh hệ sinh thái một workspace `repo sync` (Yocto/BitBake, Android, QNX, Linux kernel/U-Boot)
bằng giao diện web: cây repo vẽ từ manifest, bấm vào đâu thì nạp sâu vào đó.

- Chỉ cần Python 3.8+ (thư viện chuẩn), không cần mạng, không cần build workspace.
- Phân tích tĩnh: đọc manifest, file build/config. Không chạy bitbake/make/soong.
- Server chỉ nghe `127.0.0.1`.

## Chạy

```sh
python3 -m repomap /path/to/workspace            # từ source
./dist/repomap.pyz /path/to/workspace            # bản đóng gói 1 file
repomap /path/to/workspace --port 9000 --no-browser
repomap /path/to/workspace --json vendor/foo     # in JSON insight + quét sâu 1 repo
```

Máy build ở xa: chạy `repomap /ws` trên máy đó, tool in sẵn lệnh tunnel đúng port, ví dụ
`ssh -L 8800:127.0.0.1:8800 user@build01`; chạy lệnh đó trên máy bạn rồi mở http://127.0.0.1:8800.

## Cấu hình

```sh
repomap --init-config                 # tạo ~/.config/repomap/config.ini (có chú thích)
repomap --init-config ./repomap.ini   # hoặc file cấu hình riêng cho thư mục hiện tại
repomap /ws --config my.ini           # chỉ định file
```

```ini
[server]
host = 127.0.0.1      # 0.0.0.0 = mở cho cả mạng (không có xác thực)
port = 8800           # bận thì tự thử port+1… tối đa port_tries lần
port_tries = 20
open_browser = auto   # auto: chỉ mở khi có màn hình (DISPLAY/WAYLAND_DISPLAY); yes / no

[ssh]                 # để in lệnh: ssh -L <local_port>:127.0.0.1:<port> <user>@<host>
local_port =          # trống = giống port server
user =                # trống = user hiện tại
host =                # trống = hostname máy này

[scan]
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
| 3 | Bấm file | Biến, module/target, tham chiếu đã resolve: `require/include/inherit`, `inherit-product`, `#include` dts, `source` Kconfig, `add_subdirectory`, recipe ↔ bbappend, `SRC_URI` → repo trong manifest. Bấm tiếp để đi sâu. |

## Cấu trúc

```
repomap/
  cli.py         dòng lệnh
  settings.py    file cấu hình INI (host, port, ssh tunnel, ...)
  server.py      HTTP server + API JSON (/api/tree, insight, deep, file)
  workspace.py   danh sách repo, ánh xạ đường dẫn ↔ repo, cache, chỉ mục Yocto
  manifest.py    đọc manifest repo / tìm .git            (tầng 0)
  classify.py    phân loại nhanh repo                     (tầng 0)
  insight.py     thông tin nhanh 1 repo                   (tầng 1)
  deepscan.py    quét sâu 1 repo                          (tầng 2)
  fileview.py    xem 1 file + resolve tham chiếu          (tầng 3)
  parsers.py     parser BitBake/Make/Android.bp/Kconfig/DTS/CMake/QNX
  rules.py       bảng nhóm file + biến quan trọng (sửa ở đây để thêm loại mới)
  web/           index.html, style.css, js/{main,tree,graph,panel,util}.js
tests/           unittest với workspace giả lập
```

Thêm hệ build mới: thêm nhóm vào `rules.CATS`, file đánh dấu vào `classify.MARKS`,
parser vào `parsers.parse_file`, màu vào `web/js/util.js` (`KIND_COLOR`).

## Đóng gói

```sh
make test     # unit test
make pyz      # dist/repomap.pyz - 1 file, copy sang máy Linux nào có python3 là chạy
make wheel    # dist/repomap-*.whl - pip install được, có lệnh `repomap`
```
