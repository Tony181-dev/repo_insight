#!/bin/sh
# Cai repomap cho user hien tai, khong can root, khong can pip, khong can mang.
#   sh install.sh                          cai vao ~/.local  (lenh: ~/.local/bin/repomap)
#   sh install.sh --prefix DIR             cai vao DIR       (lenh: DIR/bin/repomap)
#   PYTHON=/duong/dan/python3 sh install.sh   dung Python khac (vd may build co Python cu)
#   sh install.sh --uninstall [--prefix DIR]  go cai dat
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PREFIX="$HOME/.local"
ACTION=install

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix)    PREFIX=$2; shift 2 ;;
        --prefix=*)  PREFIX=${1#*=}; shift ;;
        --uninstall) ACTION=uninstall; shift ;;
        -h|--help)   sed -n '2,6p' "$0"; exit 0 ;;
        *)           echo "tham so khong hop le: $1  (xem: sh install.sh --help)" >&2; exit 2 ;;
    esac
done

LIB="$PREFIX/share/repomap"
BIN="$PREFIX/bin/repomap"

if [ "$ACTION" = uninstall ]; then
    rm -f "$BIN"
    rm -rf "$LIB"
    echo "da go repomap khoi $PREFIX"
    exit 0
fi

# ---- chon Python 3.8+
PY=${PYTHON:-}
if [ -z "$PY" ]; then
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1; then PY=$(command -v "$c"); break; fi
    done
fi
if [ -z "$PY" ]; then
    echo "khong tim thay python3. Cai Python 3.8+ hoac chi dinh: PYTHON=/duong/dan/python3 sh install.sh" >&2
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
    echo "can Python 3.8+, dang co: $("$PY" -V 2>&1) ($PY)" >&2
    echo "xem muc 'May build khong co Python 3.8' trong HUONG_DAN.html" >&2
    exit 1
fi

# ---- cai dat
mkdir -p "$LIB" "$PREFIX/bin"
cp "$HERE/repomap.pyz" "$LIB/repomap.pyz"
cat > "$BIN" <<EOF
#!/bin/sh
exec "$PY" "$LIB/repomap.pyz" "\$@"
EOF
chmod +x "$BIN"

echo "da cai: $BIN  ($("$BIN" --version), $("$PY" -V 2>&1))"
case ":$PATH:" in
    *":$PREFIX/bin:"*) ;;
    *)  echo "luu y: $PREFIX/bin chua co trong PATH. Them dong sau vao ~/.bashrc roi mo terminal moi:"
        echo "  export PATH=\"$PREFIX/bin:\$PATH\"" ;;
esac
echo "chay thu: repomap ~/your_workspace_root"
