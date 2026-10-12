#!/usr/bin/env bash
# Install remmina-scroll, its KWin script and its systemd user service.
#
# Usage: ./install.sh [FACTOR]
#   FACTOR  wheel multiplier used while Remmina is focused (default 64)
#
# Safe to re-run: it updates everything in place and restarts the service.
set -euo pipefail

factor=${1:-64}
if [[ ! $factor =~ ^[1-9][0-9]*$ ]]; then
    echo "usage: $0 [FACTOR]   (FACTOR must be a positive whole number)" >&2
    exit 1
fi

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
bindir=${XDG_BIN_HOME:-$HOME/.local/bin}
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
bin=$bindir/remmina-scroll
kwin_script=remmina-focus

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> WARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m==> ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Dependencies -------------------------------------------------------------
say "Checking dependencies"
missing=()
python3 -c 'import evdev' 2>/dev/null   || missing+=(python-evdev)
python3 -c 'import jeepney' 2>/dev/null || missing+=(python-jeepney)
for cmd in kpackagetool6 kwriteconfig6 busctl systemctl; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if (( ${#missing[@]} )); then
    die "missing: ${missing[*]}  (on Arch: sudo pacman -S python-evdev python-jeepney)"
fi
command -v kdotool >/dev/null \
    || warn "optional kdotool not found; after restarting the service, switch away from Remmina and back once"

# --- System changes (need sudo; see "System changes" in README.md) --------------
# remmina-scroll runs as you, not root, so you need:
#   1. read access to the real mouse: membership of the "input" group;
#   2. write access to /dev/uinput, to create the virtual mouse and keyboard:
#      a udev rule giving /dev/uinput to the input group;
#   3. /dev/uinput to exist: the uinput kernel module loaded at boot.
need_relogin=
in_group() { [[ " $1 " == *" input "* ]]; }
if ! in_group "$(id -nG "$USER")"; then
    say "Adding $USER to the input group (sudo usermod -aG input $USER)"
    sudo usermod -aG input "$USER"
    need_relogin=1
elif ! in_group "$(id -nG)"; then
    need_relogin=1   # added earlier, but this login session predates it
fi

rule=/etc/udev/rules.d/99-remmina-scroll-uinput.rules
if ! cmp -s "$here/udev/99-remmina-scroll-uinput.rules" "$rule"; then
    say "Installing udev rule $rule (gives /dev/uinput to the input group)"
    sudo install -Dm644 "$here/udev/99-remmina-scroll-uinput.rules" "$rule"
    sudo udevadm control --reload
fi

modconf=/etc/modules-load.d/remmina-scroll-uinput.conf
if ! cmp -s "$here/modules-load/remmina-scroll-uinput.conf" "$modconf"; then
    say "Installing $modconf (loads the uinput module at boot)"
    sudo install -Dm644 "$here/modules-load/remmina-scroll-uinput.conf" "$modconf"
fi

# Apply both now rather than waiting for a reboot.
if [[ ! -e /dev/uinput ]] || [[ $(stat -c %G /dev/uinput) != input ]]; then
    say "Loading uinput and applying the udev rule"
    sudo modprobe uinput
    sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
    sudo udevadm settle
fi

# --- Script ---------------------------------------------------------------------
say "Installing $bin"
install -Dm755 "$here/remmina-scroll.py" "$bin"

# --- KWin script ------------------------------------------------------------------
say "Installing and enabling the $kwin_script KWin script"
if ! kpackagetool6 --type=KWin/Script --install "$here/kwin/$kwin_script" >/dev/null 2>&1; then
    kpackagetool6 --type=KWin/Script --upgrade "$here/kwin/$kwin_script" >/dev/null
fi
# Toggle it off and on so KWin loads the current version.
kwriteconfig6 --file kwinrc --group Plugins --key "${kwin_script}Enabled" false
busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure 2>/dev/null || true
kwriteconfig6 --file kwinrc --group Plugins --key "${kwin_script}Enabled" true
busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure 2>/dev/null \
    || warn "couldn't ask KWin to reload; the script will load at your next login"

# --- Service ------------------------------------------------------------------------
say "Installing the user service (factor $factor)"
mkdir -p "$unitdir"
sed -e "s|@BIN@|$bin|" -e "s|@FACTOR@|$factor|" \
    "$here/systemd/remmina-scroll.service" > "$unitdir/remmina-scroll.service"
systemctl --user daemon-reload
systemctl --user enable remmina-scroll.service >/dev/null

if [[ -n $need_relogin ]]; then
    say "Done. Log out and back in so your new group membership applies;"
    say "the service will start automatically at login."
else
    systemctl --user restart remmina-scroll.service
    say "Done. remmina-scroll is running:"
    systemctl --user --no-pager --lines=3 status remmina-scroll.service || true
fi
