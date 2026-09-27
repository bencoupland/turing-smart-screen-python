#!/bin/bash
# Set up Turing Smart Screen on this Linux computer.
#
# Double-click this file and choose Run, or from a terminal:
#   bash install.sh
#
# Safe to run again. It installs into this folder, wherever the folder is.
# On another computer, unpack the archive from pack-for-another-pc.sh first.
# Copying the whole folder, including venv, only works on this computer.

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
        echo "Could not open a terminal window. Run: bash \"$DIR/install.sh\""
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
        exec "$term" -e env TURING_INSTALL_CLICKED=1 bash "$DIR/install.sh" "$@"
    fi
fi

hold_if_clicked() {
    if [[ "${TURING_INSTALL_CLICKED:-}" == 1 ]]; then
        echo
        read -r -p "Press Enter to close this window." _
    fi
}
trap hold_if_clicked EXIT

ICON="$DIR/res/icons/monitor-icon-17865/128.png"

say() {
    echo
    echo "== $1"
}

die() {
    echo
    echo "Stopped: $1"
    exit 1
}

if [[ "$(id -u)" -eq 0 ]]; then
    die "Run this as your normal user. It will ask for your password if a system package is needed."
fi

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

monitor_running() {
    local pid args
    while read -r pid args; do
        case "$args" in
            *"$DIR/main.py"*)
                return 0
                ;;
        esac
    done < <(ps -eo pid=,args=)
    return 1
}

write_launcher() {
    local dest="$1"
    local generic="$2"
    local comment="$3"
    local exec_line="$4"
    local startup="$5"
    local extra="${6:-}"
    mkdir -p "$(dirname "$dest")"
    {
        cat << EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Turing Smart Screen
GenericName=$generic
Comment=$comment
Exec=$exec_line
Path=$DIR
Icon=$ICON
Terminal=false
Categories=System;Monitor;
StartupNotify=$startup
EOF
        if [[ -n "$extra" ]]; then
            printf '%s\n' "$extra"
        fi
    } > "$dest"
    chmod +x "$dest"
}

config_value() {
    local key="$1"
    [[ -f "$DIR/config.yaml" ]] || return 0
    awk -v key="$key" '
        index($0, "  " key ":") == 1 {
            sub(/^  [^:]+:[[:space:]]*/, "")
            sub(/[[:space:]]*#.*/, "")
            sub(/[[:space:]]+$/, "")
            print
            exit
        }
    ' "$DIR/config.yaml"
}

say "Turing Smart Screen setup"
echo "Folder: $DIR"

case "$DIR" in
    /media/*|/mnt/*|/run/media/*)
        echo
        echo "This folder is on a removable drive. The login start only runs when that drive is already mounted. Copy the folder onto this computer's home drive, then run install.sh from there."
        ;;
esac

say "Checking Python"
command -v python3 >/dev/null 2>&1 || die "python3 is not installed."
python3 - << 'PY' || die "Python 3.9 through 3.13 is required."
import sys
raise SystemExit(0 if (3, 9) <= sys.version_info[:2] <= (3, 13) else 1)
PY
python3 -c 'import sys; print("Python %d.%d" % sys.version_info[:2])'

if command -v apt-get >/dev/null 2>&1; then
    say "Checking system packages"
    packages=(python3 python3-venv python3-pip python3-tk python3-gi gir1.2-ayatanaappindicator3-0.1 git)
    missing=()
    for package in "${packages[@]}"; do
        if ! dpkg -s "$package" >/dev/null 2>&1; then
            missing+=("$package")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "Installing: ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    else
        echo "The system packages are already installed."
    fi
else
    say "System packages"
    echo "This installer installs packages automatically on Mint, Ubuntu, and other Debian-family systems."
    echo "On this system, install the equivalents of: python3, python3-venv, python3-tk, python3-gi, the Ayatana AppIndicator typelib, and git. Then run install.sh again."
fi

say "Serial port permission"
serial_group=""
if getent group dialout >/dev/null 2>&1; then
    serial_group="dialout"
elif getent group uucp >/dev/null 2>&1; then
    serial_group="uucp"
fi
need_logout=0
if [[ -n "$serial_group" ]]; then
    if id -nG "$USER" | grep -qw "$serial_group"; then
        echo "$USER is in the $serial_group group."
        writable=0
        for dev in /dev/ttyACM*; do
            [[ -e "$dev" && -w "$dev" ]] && writable=1
        done
        if compgen -G "/dev/ttyACM*" >/dev/null && [[ "$writable" -eq 0 ]]; then
            echo "The account can use the serial port, but this login session cannot yet."
            need_logout=1
        fi
    else
        echo "Adding $USER to the $serial_group group so the program can open the USB display."
        sudo usermod -aG "$serial_group" "$USER"
        need_logout=1
    fi
else
    echo "No dialout or uucp group was found. If the display never opens, the user needs permission to use /dev/ttyACM0."
fi

say "Python environment"
venv_kept=0
if [[ -x "$DIR/venv/bin/python" ]] && "$DIR/venv/bin/python" -c "import psutil, serial, ruamel.yaml, PIL, sv_ttk" >/dev/null 2>&1; then
    echo "The Python environment in this folder already works. Leaving it in place."
    venv_kept=1
else
    echo "Creating a new Python environment. The first run downloads packages and can take a few minutes."
    rm -rf "$DIR/venv"
    python3 -m venv "$DIR/venv"
    "$DIR/venv/bin/python" -m pip install -U pip
    if ! "$DIR/venv/bin/python" -m pip install -r "$DIR/requirements.txt"; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "Retrying after installing compiler packages. An AMD sensor library sometimes needs them."
            sudo apt-get install -y build-essential python3-dev libdrm-dev
            "$DIR/venv/bin/python" -m pip install -r "$DIR/requirements.txt"
        else
            die "Could not install the Python packages in requirements.txt."
        fi
    fi
fi

chmod +x "$DIR/start-monitor.sh" "$DIR/open-config.sh" "$DIR/install.sh" "$DIR/uninstall.sh" "$DIR/pack-for-another-pc.sh" 2>/dev/null || true

say "Shortcuts"
desktop="$(desktop_dir)"
if [[ -d "$desktop" || -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    mkdir -p "$desktop"
    write_launcher \
        "$desktop/turing-smart-screen.desktop" \
        "USB system monitor settings" \
        "Open the Turing Smart Screen configuration window" \
        "$DIR/open-config.sh" \
        true
    if command -v gio >/dev/null 2>&1; then
        gio set "$desktop/turing-smart-screen.desktop" metadata::trusted true || true
    fi
    echo "Desktop icon: $desktop/turing-smart-screen.desktop"
else
    echo "No desktop folder was found, so no desktop icon was added."
fi

mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart"
write_launcher \
    "$HOME/.local/share/applications/turing-smart-screen.desktop" \
    "USB system monitor settings" \
    "Open the Turing Smart Screen configuration window" \
    "$DIR/open-config.sh" \
    true
echo "Application menu: $HOME/.local/share/applications/turing-smart-screen.desktop"

write_launcher \
    "$HOME/.config/autostart/turing-smart-screen.desktop" \
    "USB system monitor" \
    "Show CPU, GPU, and memory stats on the Turing USB screen" \
    "$DIR/start-monitor.sh" \
    false \
    $'X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=8'
echo "Login start: $HOME/.config/autostart/turing-smart-screen.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

say "Monitor"
if monitor_running; then
    echo "The monitor from this folder is already running. The tray icon stays with it."
elif [[ "$need_logout" -eq 1 ]]; then
    echo "The monitor was not started. Log out and back in so the serial-port permission takes effect. It will then start on its own at login."
elif compgen -G "/dev/ttyACM*" >/dev/null; then
    echo "Starting the monitor. The tray icon appears with it."
    if ! setsid -f "$DIR/start-monitor.sh" </dev/null >>"$DIR/log.log" 2>&1; then
        setsid "$DIR/start-monitor.sh" </dev/null >>"$DIR/log.log" 2>&1 &
    fi
else
    echo "No USB display is plugged in yet. Plug it in and log in again, or run start-monitor.sh. The login start waits up to 30 seconds for the display."
fi

say "Settings kept from this folder"
eth="$(config_value ETH)"
fan="$(config_value CPU_FAN)"
echo "Theme, display revision, and brightness stay as they are in config.yaml."
if [[ -n "$eth" && "$eth" != "''" && "$eth" != '""' ]]; then
    echo "Network card is set to: $eth"
fi
if [[ -n "$fan" && "$fan" != "AUTO" && "$fan" != "''" ]]; then
    echo "CPU fan is set to: $fan"
fi
echo "If this is a different computer, open the desktop icon and choose this machine's network card and fan."

if [[ "$venv_kept" -eq 1 ]]; then
    echo
    echo "To set up another computer, run pack-for-another-pc.sh in this folder."
    echo "Copy that archive, unpack it, and double-click install.sh."
    echo "The venv directory only works on the computer where it was created."
fi
echo
echo "If you move this folder later, run install.sh again so the shortcuts point at the new place."
echo "Done."
