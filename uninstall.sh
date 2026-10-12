#!/usr/bin/env bash
# Remove everything install.sh set up.
#
# Usage: ./uninstall.sh [--keep-system]
#   --keep-system  leave the system changes in place (the udev rule, the
#                  uinput module autoload and your input group membership),
#                  e.g. because other software relies on them
#
# Safe to run more than once: parts that are already gone are skipped.
set -euo pipefail

keep_system=
case ${1:-} in
    "")            ;;
    --keep-system) keep_system=1 ;;
    *)  echo "usage: $0 [--keep-system]" >&2; exit 1 ;;
esac

bindir=${XDG_BIN_HOME:-$HOME/.local/bin}
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
bin=$bindir/remmina-scroll
unit=$unitdir/remmina-scroll.service
kwin_script=remmina-focus
rule=/etc/udev/rules.d/99-remmina-scroll-uinput.rules
modconf=/etc/modules-load.d/remmina-scroll-uinput.conf

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null; }

# --- Service ------------------------------------------------------------------------
if have systemctl; then
    if systemctl --user is-enabled --quiet remmina-scroll.service 2>/dev/null \
       || systemctl --user is-active --quiet remmina-scroll.service 2>/dev/null; then
        say "Stopping and disabling the remmina-scroll service"
        systemctl --user disable --now remmina-scroll.service >/dev/null 2>&1 || true
    fi
fi
if [[ -e $unit ]]; then
    say "Removing $unit"
    rm -f "$unit"
    have systemctl && systemctl --user daemon-reload
fi

# --- Script ---------------------------------------------------------------------------
if [[ -e $bin ]]; then
    say "Removing $bin"
    rm -f "$bin"
fi

# --- KWin script ------------------------------------------------------------------------
if have kwriteconfig6; then
    say "Disabling and removing the $kwin_script KWin script"
    kwriteconfig6 --file kwinrc --group Plugins --key "${kwin_script}Enabled" false
    busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
    kwriteconfig6 --file kwinrc --group Plugins --key "${kwin_script}Enabled" --delete
fi
if have kpackagetool6; then
    kpackagetool6 --type=KWin/Script --remove "$kwin_script" >/dev/null 2>&1 || true
fi

# --- System changes ------------------------------------------------------------------------
if [[ -n $keep_system ]]; then
    say "Leaving the udev rule, uinput autoload and input group membership in place"
    say "Done."
    exit 0
fi

if [[ -e $rule ]]; then
    say "Removing udev rule $rule (/dev/uinput goes back to root-only)"
    sudo rm -f "$rule"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
fi

if [[ -e $modconf ]]; then
    say "Removing $modconf (uinput is no longer loaded at boot)"
    sudo rm -f "$modconf"
fi

relogin=
if [[ " $(id -nG "$USER") " == *" input "* ]]; then
    say "Removing $USER from the input group (sudo gpasswd -d $USER input)"
    sudo gpasswd -d "$USER" input >/dev/null
    relogin=1
fi

say "Done."
[[ -n $relogin ]] && say "The input group change applies from your next login."
say "The uinput module stays loaded until reboot; 'sudo modprobe -r uinput' unloads it now if unused."
