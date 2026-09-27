#!/bin/bash
# Make an archive of this program for another Linux computer.
# The archive leaves out venv, which only works on the computer that created it.
#
#   bash pack-for-another-pc.sh
#   bash pack-for-another-pc.sh /path/to/turing-smart-screen-portable.tar.gz
#
# On the other computer: unpack the archive, then double-click install.sh.

set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    if [[ "$SOURCE" != /* ]]; then
        SOURCE="$DIR/$SOURCE"
    fi
done
DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$DIR"

if [[ ! -t 1 && "${TURING_INSTALL_FOREGROUND:-}" != 1 ]]; then
    if [[ "${TURING_INSTALL_CLICKED:-}" == 1 ]]; then
        echo "Could not open a terminal window. Run: bash \"$DIR/pack-for-another-pc.sh\""
        exit 1
    fi
    term=""
    for candidate in x-terminal-emulator gnome-terminal xfce4-terminal konsole xterm; do
        if command -v "$candidate" >/dev/null 2>&1; then
            term="$candidate"
            break
        fi
    done
    if [[ -n "$term" ]]; then
        exec "$term" -e env TURING_INSTALL_CLICKED=1 bash "$DIR/pack-for-another-pc.sh" "$@"
    fi
fi

hold_if_clicked() {
    if [[ "${TURING_INSTALL_CLICKED:-}" == 1 ]]; then
        echo
        read -r -p "Press Enter to close this window." _
    fi
}
trap hold_if_clicked EXIT

out="${1:-$(dirname "$DIR")/turing-smart-screen-portable.tar.gz}"
if [[ "$out" != /* ]]; then
    out="$PWD/$out"
fi
out_dir="$(dirname "$out")"
out_base="$(basename "$out")"
if [[ ! -d "$out_dir" ]]; then
    echo "Folder does not exist: $out_dir"
    exit 1
fi
out="$(cd "$out_dir" && pwd)/$out_base"
case "$out" in
    "$DIR"|"$DIR"/*)
        echo "Save the archive outside the program folder."
        exit 1
        ;;
esac
folder="$(basename "$DIR")"
parent="$(dirname "$DIR")"

echo "Packing $DIR"
echo "Leaving out venv, git history, logs, and cached files."

tar -C "$parent" -czf "$out" \
    --exclude="$folder/venv" \
    --exclude="$folder/.git" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="log.log" \
    --exclude="screencap.png" \
    --exclude="$folder/tmp" \
    "$folder"

echo
echo "Archive:"
echo "  $out"
echo
echo "Copy that file to the other computer, unpack it, and double-click install.sh."
echo "install.sh creates a new Python environment there and adds the desktop icon,"
echo "the application menu entry, and the login start."
echo "config.yaml goes along with the archive. On the other computer, open the"
echo "desktop icon and set the network card and fan if those names differ."
