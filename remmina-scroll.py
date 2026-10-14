#!/usr/bin/env python3
"""Multiply mouse-wheel scrolling only while a matching window has focus.

Works on KDE Plasma (Wayland) by sitting between the physical mouse and the
compositor: it grabs the mouse, re-emits everything through a virtual copy
of it, and repeats wheel events N times while the active window's class
equals the given class. The copy has the real mouse's name and vendor/product
IDs, so KDE applies the mouse settings you already have. While that window is focused
it also turns the back/forward mouse buttons into Super+[ / Super+], which a
Mac on the other end of VNC sees as Cmd+[ / Cmd+].

The active window is pushed in by the companion KWin script (remmina-focus),
which calls WindowActivated(class) on the session-bus name local.RemminaScroll
whenever focus changes, so nothing is polled. At startup, kdotool (optional)
is asked once for the current window, so a restart while that window already
has focus takes effect immediately.

Requires: python-evdev, python-jeepney, the remmina-focus KWin script, read
access to the mouse's /dev/input/event* node and write access to /dev/uinput.
Optional: kdotool.
"""
import argparse
import queue
import shutil
import subprocess
import sys
import threading
import time

import evdev
from evdev import ecodes as e
from jeepney import HeaderFields, MessageType, new_error, new_method_return
from jeepney.bus_messages import message_bus
from jeepney.io.blocking import open_dbus_connection

# Our virtual devices carry this in their "phys" field, which isn't shown in
# the UI. The virtual mouse otherwise looks exactly like the real one, so this
# is how find_mouse() tells them apart.
VIRTUAL_PHYS = "remmina-scroll/virtual"
KEYBOARD_NAME = "remmina-scroll keys"
DBUS_NAME = "local.RemminaScroll"  # also used as the interface name
DBUS_NAME_FLAG_DO_NOT_QUEUE = 4
DBUS_REQUEST_NAME_PRIMARY_OWNER = 1
WHEEL_CODES = {
    e.REL_WHEEL,
    e.REL_HWHEEL,
    getattr(e, "REL_WHEEL_HI_RES", 11),
    getattr(e, "REL_HWHEEL_HI_RES", 12),
}

# Back/forward mouse buttons (mice report one pair or the other) and the key
# sent with Super for each. The Mac's VNC server delivers Super as Command, so
# these arrive as Cmd+[ and Cmd+], macOS's standard back/forward shortcuts.
NAV_MODIFIER = e.KEY_LEFTMETA
NAV_KEYS = {
    e.BTN_SIDE: e.KEY_LEFTBRACE,
    e.BTN_BACK: e.KEY_LEFTBRACE,
    e.BTN_EXTRA: e.KEY_RIGHTBRACE,
    e.BTN_FORWARD: e.KEY_RIGHTBRACE,
}


class ShortcutSender:
    """Taps Super+key on the virtual keyboard from a worker thread.

    Each tap has short pauses between key events so every application sees a
    distinct press and release. Doing that on the mouse thread would stall
    mouse movement for the length of the tap, so taps are queued here instead.
    """

    def __init__(self, kbd):
        self.kbd = kbd
        self.queue = queue.SimpleQueue()
        threading.Thread(target=self._run, daemon=True).start()

    def send(self, key):
        self.queue.put(key)

    def _run(self):
        while True:
            key = self.queue.get()
            for code, value in ((NAV_MODIFIER, 1), (key, 1), (key, 0), (NAV_MODIFIER, 0)):
                self.kbd.write(e.EV_KEY, code, value)
                self.kbd.syn()
                time.sleep(0.01)


def find_mouse():
    """Return the first device that looks like a mouse with a scroll wheel."""
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.phys == VIRTUAL_PHYS:
            dev.close()
            continue
        caps = dev.capabilities()
        if e.REL_WHEEL in caps.get(e.EV_REL, []) and e.BTN_LEFT in caps.get(e.EV_KEY, []):
            return dev
        dev.close()
    return None


class FocusWatcher:
    """Serves WindowActivated(s) on the session bus and records whether the target window is active."""

    def __init__(self, window_class, verbose=False):
        self.window_class = window_class
        self.verbose = verbose
        self.active = False
        self.conn = open_dbus_connection(bus="SESSION")
        reply = self.conn.send_and_get_reply(
            message_bus.RequestName(DBUS_NAME, DBUS_NAME_FLAG_DO_NOT_QUEUE))
        if reply.body[0] != DBUS_REQUEST_NAME_PRIMARY_OWNER:
            sys.exit(f"D-Bus name {DBUS_NAME} is taken; is another copy already running?")
        # Done after taking the bus name and before listening: focus changes that
        # arrive meanwhile wait on the connection and are applied afterwards, so
        # they always win over this one-off check.
        self._check_current_window()
        threading.Thread(target=self._run, daemon=True).start()

    def _set(self, window_class):
        active = window_class == self.window_class
        if self.verbose and active != self.active:
            print(f"{self.window_class} focused: {'yes' if active else 'no'}", flush=True)
        self.active = active

    def _check_current_window(self):
        """Ask kdotool (if installed) which window has focus right now."""
        if not shutil.which("kdotool"):
            if self.verbose:
                print("kdotool not found; waiting for the next focus change", flush=True)
            return
        try:
            result = subprocess.run(
                ["kdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as err:
            if self.verbose:
                print(f"kdotool failed ({err}); waiting for the next focus change", flush=True)
            return
        window_class = result.stdout.strip()
        if result.returncode != 0 or not window_class:
            if self.verbose:
                print("kdotool gave no window; waiting for the next focus change", flush=True)
            return
        self._set(window_class)

    def _run(self):
        while True:
            msg = self.conn.receive()
            if msg.header.message_type != MessageType.method_call:
                continue  # e.g. the NameAcquired signal
            member = msg.header.fields.get(HeaderFields.member)
            if member == "WindowActivated" and msg.header.fields.get(HeaderFields.signature) == "s":
                self._set(msg.body[0])
                self.conn.send(new_method_return(msg))
            else:
                self.conn.send(new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", help="mouse event device, e.g. /dev/input/by-id/usb-...-event-mouse "
                                     "(default: first mouse with a wheel)")
    ap.add_argument("--factor", type=int, default=4, help="wheel multiplier inside matching windows (default 4)")
    ap.add_argument("--match", default="org.remmina.Remmina",
                    help="exact window class to act on (default 'org.remmina.Remmina')")
    ap.add_argument("--no-nav-buttons", action="store_true",
                    help="pass back/forward buttons through unchanged instead of sending Super+[ / Super+]")
    ap.add_argument("--verbose", action="store_true", help="print focus changes")
    args = ap.parse_args()

    if args.factor < 1:
        sys.exit("--factor must be 1 or more")

    dev = evdev.InputDevice(args.device) if args.device else find_mouse()
    if dev is None:
        sys.exit("No mouse with a scroll wheel found; pass --device")
    if dev.phys == VIRTUAL_PHYS:
        sys.exit(f"{dev.path} is remmina-scroll's own virtual mouse; pass the real one")
    print(f"Using {dev.path}: {dev.name}", flush=True)

    focus = FocusWatcher(args.match, args.verbose)
    # Clone the real mouse's identity: KWin files per-device settings under
    # vendor ID, product ID and name, so the copy gets the same settings.
    ui = evdev.UInput.from_device(
        dev, name=dev.name, vendor=dev.info.vendor, product=dev.info.product,
        version=dev.info.version, bustype=dev.info.bustype, phys=VIRTUAL_PHYS)
    kbd = None
    if not args.no_nav_buttons:
        kbd = evdev.UInput({e.EV_KEY: [NAV_MODIFIER] + list(set(NAV_KEYS.values()))},
                           name=KEYBOARD_NAME, phys=VIRTUAL_PHYS)
        shortcuts_out = ShortcutSender(kbd)
    dev.grab()

    suppressed = set()  # nav buttons whose press was turned into a shortcut
    frame = []
    try:
        for ev in dev.read_loop():
            if ev.type == e.EV_SYN:
                if ev.code == e.SYN_DROPPED:
                    frame = []
                    continue
                if ev.code != e.SYN_REPORT:
                    continue

                active = focus.active

                passthrough = []
                shortcuts = []
                for x in frame:
                    if kbd and x.type == e.EV_KEY and x.code in NAV_KEYS:
                        if x.value == 1 and active:
                            suppressed.add(x.code)
                            shortcuts.append(NAV_KEYS[x.code])
                            continue
                        if x.code in suppressed:
                            if x.value == 0:
                                suppressed.discard(x.code)
                            continue
                    passthrough.append(x)

                if passthrough:
                    for x in passthrough:
                        ui.write(x.type, x.code, x.value)
                    ui.syn()

                for key in shortcuts:
                    shortcuts_out.send(key)

                wheel = [x for x in frame if x.type == e.EV_REL and x.code in WHEEL_CODES]
                if wheel and active:
                    for _ in range(args.factor - 1):
                        for x in wheel:
                            ui.write(x.type, x.code, x.value)
                        ui.syn()
                frame = []
            else:
                frame.append(ev)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            dev.ungrab()
        except OSError:
            pass
        ui.close()
        if kbd:
            kbd.close()


if __name__ == "__main__":
    main()
