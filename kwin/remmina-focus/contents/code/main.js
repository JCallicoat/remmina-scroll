// Push the active window's class to remmina-scroll whenever focus changes.
// If remmina-scroll isn't running the call just fails quietly.
function report(window) {
    const windowClass = window ? String(window.resourceClass) : "";
    callDBus("local.RemminaScroll", "/", "local.RemminaScroll",
             "WindowActivated", windowClass);
}

workspace.windowActivated.connect(report);
report(workspace.activeWindow);
