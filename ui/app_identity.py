"""What macOS shows for Sentinel when it runs from a checkout, not a bundle.

A frozen `.app` carries its name and icon in its `Info.plist`. Sentinel normally
runs as `python main.py` — typed in a terminal, or through the thin launcher in
`/Applications`, which execs the project's interpreter. macOS resolves an app's
identity from the path of the executable the process is running, and
`.venv/bin/python` is not inside a bundle, so there is nothing to read: the menu
next to the Apple logo is titled after the script, and the Dock shows the
generic interpreter icon.

Measured rather than assumed, because the fix is not where it looks like it is:

* The **fork in the launcher is not the cause.** A bundle whose executable execs
  the venv interpreter loses its identity either way — with `fork()` and with a
  plain `exec` — because the lookup follows the new executable's path, not the
  process. `__CFBundleIdentifier` is still in the environment and is not used.
  So this is fixed in the process, and `scripts/thin_launcher.c` is left alone.
* The **menu bar title** comes from `CFBundleName` in the main bundle's info
  dictionary, which CoreFoundation hands out as an `NSMutableDictionary`.
  Writing the name into it is enough, and it has to happen **before**
  `QApplication()` — AppKit reads the name when the application object is
  created, and Qt creates it there.
* The **Dock icon** comes from `-[NSApplication applicationIconImage]`.
  `QApplication.setWindowIcon` does not touch it on macOS (checked by
  fingerprinting the icon's TIFF representation, which does not change), so it
  is set directly, after `QApplication()` has made `NSApp` exist.

What this cannot fix: the name *under* the Dock icon, in the app switcher and in
Force Quit is `NSRunningApplication`'s `localizedName`, which Launch Services
took from the executable at launch and does not re-read. `-[NSProcessInfo
setProcessName:]` is accepted and changes nothing there. Only an interpreter
living inside the bundle changes it — which is what the frozen build in
`Sentinel.spec` already does.

Reached through the Objective-C runtime with ctypes rather than pyobjc, which is
not a dependency. Every call answers False rather than raising: an app that
opens with the wrong name in the menu bar is a blemish, and one that refuses to
open is not.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from pathlib import Path

VOID = ctypes.c_void_p

_objc = None

_FAILURES = (OSError, AttributeError, TypeError, ValueError)


def _runtime():
    """The Objective-C runtime, loaded once with its signatures declared."""
    global _objc
    if _objc is None:
        objc = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
        )
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = VOID
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = VOID
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        _objc = objc
    return _objc


def _call(*signature):
    """`objc_msgSend` takes one prototype per signature, so cast per call."""
    return ctypes.cast(_runtime().objc_msgSend, ctypes.CFUNCTYPE(*signature))


def _cls(name: bytes):
    return _runtime().objc_getClass(name)


def _sel(name: bytes):
    return _runtime().sel_registerName(name)


def _send(receiver, selector: bytes):
    return _call(VOID, VOID, VOID)(receiver, _sel(selector))


def _nsstring(value: str):
    return _call(VOID, VOID, VOID, ctypes.c_char_p)(
        _cls(b"NSString"), _sel(b"stringWithUTF8String:"), value.encode()
    )


def _text(obj) -> str | None:
    if not obj:
        return None
    value = _call(ctypes.c_char_p, VOID, VOID)(obj, _sel(b"UTF8String"))
    return value.decode() if value else None


def _info_dictionary():
    """The main bundle's info dictionary, but only when it is safe to write to.

    A dictionary that is not mutable would raise an Objective-C exception on the
    first write, and that unwinds through the interpreter as an abort rather
    than as something Python can catch — so this asks first.
    """
    bundle = _send(_cls(b"NSBundle"), b"mainBundle")
    info = _send(bundle, b"infoDictionary") if bundle else None
    if not info:
        return None
    mutable = _call(ctypes.c_bool, VOID, VOID, VOID)(
        info, _sel(b"isKindOfClass:"), _cls(b"NSMutableDictionary")
    )
    return info if mutable else None


def menu_bar_name() -> str | None:
    """The name macOS titles the application menu with, as it stands now."""
    if sys.platform != "darwin":
        return None
    try:
        bundle = _send(_cls(b"NSBundle"), b"mainBundle")
        info = _send(bundle, b"infoDictionary") if bundle else None
        if not info:
            return None
        return _text(
            _call(VOID, VOID, VOID, VOID)(
                info, _sel(b"objectForKey:"), _nsstring("CFBundleName")
            )
        )
    except _FAILURES:
        return None


def name_in_menu_bar(name: str) -> bool:
    """Title the application menu `name`. Call before QApplication exists.

    A real bundle already answers with its own name; that one is authoritative
    and is left alone, so the frozen build is untouched by this.
    """
    if sys.platform != "darwin":
        return False
    try:
        if menu_bar_name():
            return True
        info = _info_dictionary()
        if not info:
            return False
        write = _call(VOID, VOID, VOID, VOID, VOID)
        for key in ("CFBundleName", "CFBundleDisplayName"):
            write(info, _sel(b"setObject:forKey:"), _nsstring(name), _nsstring(key))
        return menu_bar_name() == name
    except _FAILURES:
        return False


def _nsimage(path: Path):
    """An NSImage for `path`, or None when AppKit will not read the file.

    Not released: the one caller hands it to NSApp, which holds it for the life
    of the process — exactly as long as it is wanted.
    """
    return _call(VOID, VOID, VOID, VOID)(
        _send(_cls(b"NSImage"), b"alloc"),
        _sel(b"initWithContentsOfFile:"),
        _nsstring(str(path)),
    ) or None


def set_dock_icon(path: Path | str) -> bool:
    """Show `path` as the Dock icon. Call after QApplication exists."""
    if sys.platform != "darwin":
        return False
    icon = Path(path)
    if not icon.is_file():
        return False
    try:
        image = _nsimage(icon)
        if not image:
            return False
        app = _send(_cls(b"NSApplication"), b"sharedApplication")
        if not app:
            return False
        _call(None, VOID, VOID, VOID)(
            app, _sel(b"setApplicationIconImage:"), image
        )
        return True
    except _FAILURES:
        return False


def activate() -> bool:
    """Foreground the GUI process itself.

    Qt's `raise_()`/`activateWindow()` only order windows within this app. macOS
    will not let a background process take focus from whatever is frontmost
    without `-[NSApplication activateIgnoringOtherApps:]`, so handing off from a
    second launch raised the window *behind* what the user was looking at, which
    reads as the launch having done nothing.
    """
    if sys.platform != "darwin":
        return False
    try:
        app = _send(_cls(b"NSApplication"), b"sharedApplication")
        if not app:
            return False
        _call(None, VOID, VOID, ctypes.c_bool)(
            app, _sel(b"activateIgnoringOtherApps:"), True
        )
        return True
    except _FAILURES:
        # Qt's activation request stays as the portable fallback.
        return False
