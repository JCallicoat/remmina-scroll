# remmina-scroll

Usable mouse scrolling and back/forward buttons when controlling a Mac from
Linux with [Remmina](https://remmina.org/) over VNC, on KDE Plasma 6 (Wayland).

## The problem

When you connect to macOS's built-in Screen Sharing with Remmina's VNC plugin,
two things about the mouse are poor:

- **Scrolling is extremely slow.** The VNC protocol has no scroll amount, only
  wheel "button" presses. Each notch of your wheel becomes a single press, and
  the Mac turns each press into a tiny scroll step. Neither Remmina nor macOS
  has a setting to scale this, and Mac-side tools such as Mos don't affect
  input injected by Screen Sharing.
- **The back and forward buttons do nothing.** Remmina doesn't send the extra
  mouse buttons over VNC.

Linux-side workarounds like `imwheel` don't work here either: they're X11-only,
and under XWayland on Plasma 6 their synthetic events loop back into
themselves.

## How it works

`remmina-scroll` sits between your physical mouse and the compositor. It
takes exclusive hold of the mouse and passes every event through to a virtual
copy of it, so the rest of the desktop sees a normal mouse. The copy has the
real mouse's name and vendor/product IDs, which is how KDE files per-device
mouse settings, so your existing settings (speed, acceleration, natural
scrolling and so on) apply to it unchanged. While Remmina is the focused
window, it also:

- repeats each wheel event `FACTOR` times, so each notch scrolls a useful
  distance on the Mac; and
- turns the back/forward buttons into Super+[ / Super+]. The Mac's VNC server
  delivers Super as Command, so these arrive as Cmd+[ / Cmd+], the standard
  back/forward shortcuts in Safari, Firefox, Chrome and Finder.

In every other window, the mouse behaves exactly as before.

To know which window is focused without polling, a small KWin script
(`remmina-focus`) calls `WindowActivated(class)` on the session bus name
`local.RemminaScroll` whenever focus changes. `remmina-scroll` listens on that
name and switches itself on or off. If [kdotool](https://github.com/jinliu/kdotool)
is installed, it is also asked once at startup which window is focused, so a
restart takes effect immediately even if Remmina already has focus.

```
mouse ──► remmina-scroll ──► virtual mouse / keyboard ──► KWin ──► Remmina ──► Mac
                ▲                                            │
                └──── D-Bus: WindowActivated(class) ◄── remmina-focus KWin script
```

## Requirements

- KDE Plasma 6 on Wayland (uses `kpackagetool6`, `kwriteconfig6` and the
  Plasma 6 KWin scripting API)
- Python 3 with `python-evdev` and `python-jeepney`
- systemd user services

Optional: `kdotool`, used once at startup (see above). Without it, everything
works, but after a restart the script waits for the next focus change.

On Arch Linux:

```sh
sudo pacman -S python-evdev python-jeepney
yay -S kdotool        # optional, from the AUR
```

## Install

```sh
./install.sh [FACTOR]
```

`FACTOR` is the wheel multiplier used inside Remmina. It defaults to 64, which
gave close to native scrolling speed against macOS Screen Sharing. Pick a
smaller number if it scrolls too far; re-run `install.sh` to change it.

The installer:

1. checks the dependencies;
2. makes the three system changes described below, using `sudo`, but only for
   the ones that aren't in place yet;
3. installs the script as `~/.local/bin/remmina-scroll`;
4. installs and enables the `remmina-focus` KWin script;
5. installs, enables and starts the `remmina-scroll` systemd user service.

If you were just added to the `input` group, log out and back in; the service
starts automatically at login. Re-running `install.sh` is safe and updates
everything in place.

## System changes

`remmina-scroll` runs as your user, not as root, so it needs permission to
read your mouse and to create virtual input devices. The installer makes
three changes outside your home directory to allow that:

| Change | What it does | Why it's needed |
| --- | --- | --- |
| You're added to the `input` group (`usermod -aG input $USER`) | Gives read access to the `/dev/input/event*` devices | To read and take hold of the real mouse |
| `/etc/udev/rules.d/99-remmina-scroll-uinput.rules` | Makes `/dev/uinput` owned by group `input` with mode `0660` | `/dev/uinput` creates virtual input devices, and by default only root may open it. The script uses it for its virtual mouse and keyboard |
| `/etc/modules-load.d/remmina-scroll-uinput.conf` | Loads the `uinput` kernel module at boot | So `/dev/uinput` exists without anyone loading the module by hand |

Both files are in this repository (`udev/` and `modules-load/`) with comments
explaining each line. The installer also loads `uinput` and applies the rule
immediately, so no reboot is needed; a new group membership does need you to
log out and back in.

**Security note:** members of `input` can read every keyboard and mouse,
including what you type, and with the rule above can create virtual devices
that type and click as you. Any program running as your user gets these
abilities, not just this one. That's what this tool needs, but be aware of it.

## Usage

Nothing to do once installed: focus Remmina and scroll. Useful commands:

```sh
systemctl --user status remmina-scroll     # is it running?
journalctl --user -u remmina-scroll -f     # its log
systemctl --user restart remmina-scroll    # after changing options
```

Options for `remmina-scroll`, set on the `ExecStart` line of
`~/.config/systemd/user/remmina-scroll.service`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--factor N` | 4 (installer sets 64) | wheel multiplier while the window is focused |
| `--match CLASS` | `org.remmina.Remmina` | exact window class to act on |
| `--device PATH` | first mouse with a wheel | mouse to use, e.g. `/dev/input/by-id/usb-…-event-mouse` |
| `--no-nav-buttons` | off | pass back/forward through unchanged |
| `--verbose` | off | log focus changes |

After editing the service file, run `systemctl --user daemon-reload` and
restart the service.

## Troubleshooting

- **Nothing changes in Remmina.** Stop the service, run
  `remmina-scroll --verbose` in a terminal, and switch to Remmina. It should
  print `org.remmina.Remmina focused: yes`. If it prints nothing, check that
  the KWin script is enabled in System Settings → Window Management → KWin
  Scripts. If the class differs, find the right one with KWin's window
  properties and pass it with `--match`.
- **The wrong mouse is used.** Pass `--device` with a path from
  `/dev/input/by-id/`.
- **Your mouse appears twice in Mouse & Touchpad.** One entry is the real
  mouse and one is the virtual copy. They share the same settings, so changing
  either changes both. A third device, `remmina-scroll keys`, is the virtual
  keyboard used for the back/forward shortcuts.
- **Scrolling is off right after a restart.** The KWin script only reports on
  focus changes. Install `kdotool` so the script can check the focused window
  when it starts; otherwise click away from Remmina and back once.
- **"D-Bus name … is taken".** Another copy is already running; stop it first.

If the service crashes, the mouse grab is released automatically, so you
can't lose your pointer.

## Uninstall

```sh
./uninstall.sh [--keep-system]
```

`uninstall.sh` stops and removes the service, the script and the KWin script,
then undoes the three [system changes](#system-changes) using `sudo`. Pass
`--keep-system` to leave the system changes in place if other software relies
on them (see below). It skips anything that's already gone, so it's safe to
run more than once.

### Uninstalling by hand

These are the same steps `uninstall.sh` runs. First stop and remove the
service, the script and the KWin script:

```sh
systemctl --user disable --now remmina-scroll
rm ~/.config/systemd/user/remmina-scroll.service ~/.local/bin/remmina-scroll
systemctl --user daemon-reload
kwriteconfig6 --file kwinrc --group Plugins --key remmina-focusEnabled false
busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure
kwriteconfig6 --file kwinrc --group Plugins --key remmina-focusEnabled --delete
kpackagetool6 --type=KWin/Script --remove remmina-focus
```

Then undo the [system changes](#system-changes):

```sh
# udev rule: /dev/uinput goes back to root-only access
sudo rm /etc/udev/rules.d/99-remmina-scroll-uinput.rules
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput

# stop loading uinput at boot
sudo rm /etc/modules-load.d/remmina-scroll-uinput.conf

# leave the input group (takes effect at your next login)
sudo gpasswd -d "$USER" input
```

Other software you run as your user may rely on these too, for example tools
that create virtual game controllers or remap input without a root daemon. If
something else needs `/dev/uinput` access or `input` membership, skip those
steps, or run `uninstall.sh --keep-system`.

The `uinput` module stays loaded until you reboot; `sudo modprobe -r uinput`
unloads it now if nothing else is using it.

## License

MIT; see [LICENSE](LICENSE).
