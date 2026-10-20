repomap {version} - bản đồ workspace repo sync (Yocto, Android, QNX, Linux)

repomap vẽ cây repo từ manifest và chỉ đọc sâu vào chỗ bạn bấm. Chạy trên máy
đang chứa source đã `repo sync` (thường là máy build), xem giao diện bằng trình
duyệt. Chỉ cần Python 3.8+, không cần pip, không cần mạng, không cần quyền root.

Hướng dẫn đầy đủ: mở file HUONG_DAN.html bằng trình duyệt.


CÀI ĐẶT TRÊN MÁY BUILD LINUX

  1. Copy gói lên máy build (chạy trên PC của bạn):
       scp repomap-{version}.tar.gz user@your_build_server:~

  2. Trên máy build, giải nén và cài:
       tar xzf repomap-{version}.tar.gz
       cd repomap-{version}
       sh install.sh

     Lệnh được cài vào ~/.local/bin/repomap. Nếu thư mục đó chưa có trong PATH,
     install.sh sẽ in dòng cần thêm vào ~/.bashrc.

  3. Chạy:
       repomap ~/your_workspace_root

     ~/your_workspace_root là thư mục gốc bạn đã chạy `repo sync`, có thư mục .repo/ bên trong.
     Tool hiện menu:
       1. Run scan        quét và mở server
       2. Configuration   nhập port server và thông tin ssh (lưu lại cho lần sau)
       3. Quick setup     dán lệnh ssh của server, ví dụ
                            ssh -p <ssh_port> <user>@<host>.vbee.lge.com
                          -> tự điền ssh host/user/port, port server = 6969
       0. Exit


CÁCH NHANH NHẤT: DÙNG repomap TRÊN PC WINDOWS (cần Python 3.8+ trên PC)

  1. Giải nén gói trên PC, nhấp đúp repomap-pc.cmd. Trình duyệt mở trang
     http://127.0.0.1:6960 với 2 tab: Connect / Saved servers.
  2. Tab Connect -> Configuration -> Quick setup: dán lệnh ssh của server.
  3. Trong tab Connect, bấm Push to server: chép repomap.pyz + repomap.ini
     vào ~/.bin trên server (gõ password trong cửa sổ ssh nếu được hỏi).
  4. Trên server:  python3 ~/.bin/repomap.pyz ~/your_workspace_root  -> 1. Run scan
  5. Trên PC: bấm Connect. Trang repomap tự mở khi server sẵn sàng.
     Giữ cửa sổ ssh tunnel mở trong lúc dùng.


XEM GIAO DIỆN TỪ PC BẰNG LỆNH

  Sau "1. Run scan", repomap in ra một lệnh dạng:
       ssh -N -L 6969:127.0.0.1:6969 user@your_build_server
  Chạy lệnh đó trên PC của bạn, giữ cửa sổ mở, rồi mở http://127.0.0.1:6969
  Tên máy, user và port ssh trong lệnh chỉnh ở "2. Configuration".
  Port nào đang bận, tool báo rõ và chuyển sang port kế tiếp.
  Giao diện có nút EN / VI cạnh ô tìm kiếm.


TUỲ CHỌN

  Bỏ qua menu:               repomap ~/your_workspace_root --run
  Không muốn cài:            python3 repomap.pyz ~/your_workspace_root
  Máy build có Python cũ:    PYTHON=/duong/dan/python3.8+ sh install.sh
  Cài vào thư mục khác:      sh install.sh --prefix /opt/tools
  Gỡ cài đặt:                sh install.sh --uninstall
