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


XEM GIAO DIỆN TỪ PC

  repomap in ra một lệnh dạng:
       ssh -L 8800:127.0.0.1:8800 user@your_build_server
  Chạy lệnh đó trên PC của bạn, giữ cửa sổ mở, rồi mở http://127.0.0.1:8800


TUỲ CHỌN

  Không muốn cài:            python3 repomap.pyz ~/your_workspace_root
  Máy build có Python cũ:    PYTHON=/duong/dan/python3.8+ sh install.sh
  Cài vào thư mục khác:      sh install.sh --prefix /opt/tools
  Gỡ cài đặt:                sh install.sh --uninstall
  Tạo file cấu hình mẫu:     repomap --init-config
