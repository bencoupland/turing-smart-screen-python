#!/bin/bash
# Remove the Turing Smart Screen desktop icon, menu entry, and login start.
# The program folder is left where it is. Pass --yes to skip the question.

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

assume_yes=0
for arg in "$@"; do
    if [[ "$arg" == "--yes" ]]; then
        assume_yes=1
    fi
done

if [[ ! -t 1 && "${TURING_INSTALL_FOREGROUND:-}" != 1 ]]; then
    if [[ "${TURING_INSTALL_CLICKED:-}" == 1 ]]; then
        echo "Could not open a terminal window. Run: bash \"$DIR/uninstall.sh\""
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
        exec "$term" -e env TURING_INSTALL_CLICKED=1 bash "$DIR/uninstall.sh" "$@"
    fi
fi

hold_if_clicked() {
    if [[ "${TURING_INSTALL_CLICKED:-}" == 1 ]]; then
        echo
        read -r -p "Press Enter to close this window." _
    fi
}
trap hold_if_clicked EXIT

desktop_dir() {
    local raw dir="$HOME/Desktop"
    if [[ -f "$HOME/.config/user-dirs.dirs" ]]; then
        raw="$(sed -n 's/^XDG_DESKTOP_DIR=//p' "$HOME/.config/user-dirs.dirs")"
        raw="${raw%%$'\n'*}"
        raw="${raw#\"}"
        raw="${raw%\"}"
        raw="${raw/\$HOME/$HOME}"
        if [[ -n "$raw" ]]; then
            dir="$raw"
        fi
    fi
    printf '%s\n' "$dir"
}

if [[ "$assume_yes" -ne 1 ]]; then
    if [[ ! -t 0 ]]; then
        echo "Run uninstall.sh from a terminal, or pass --yes."
        exit 1
    fi
    echo "This removes the desktop icon, the application menu entry, and the login start"
    echo "for the copy in:"
    echo "  $DIR"
    echo "If that copy is running, the screen turns off. The folder itself stays on disk."
    read -r -p "Remove those shortcuts? [y/N] " reply
    case "$reply" in
        y|Y|yes|YES) ;;
        *)
            echo "Cancelled."
            exit 0
            ;;
    esac
fi

remove_if_ours() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    if grep -F -q "$DIR/" "$file"; then
        rm -f "$file"
        echo "Removed $file"
    else
        echo "Left $file in place. It points at a different copy."
    fi
}

remove_if_ours "$(desktop_dir)/turing-smart-screen.desktop"
remove_if_ours "$HOME/.local/share/applications/turing-smart-screen.desktop"
remove_if_ours "$HOME/.config/autostart/turing-smart-screen.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

stopped=0
while read -r pid args; do
    case "$args" in
        *"$DIR/main.py"*)
            echo "Stopping monitor process $pid"
            kill -TERM "$pid" 2>/dev/null || true
            stopped=1
            ;;
    esac
done < <(ps -eo pid=,args=)

if [[ "$stopped" -eq 1 ]]; then
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        still=0
        while read -r pid args; do
            case "$args" in
                *"$DIR/main.py"*) still=1 ;;
            esac
        done < <(ps -eo pid=,args=)
        [[ "$still" -eq 0 ]] && break
        sleep 0.5
    done
else
    echo "No monitor from this folder was running."
fi

echo
echo "The program folder is still here:"
echo "  $DIR"
echo "Delete that folder yourself if you want the program gone."
echo "Done."
