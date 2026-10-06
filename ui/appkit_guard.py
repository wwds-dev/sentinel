"""A guard against an AppKit assertion that kills Qt apps on macOS 27.

## The crash

Open a menu bar item's menu and the app aborts:

    -[NSEvent clickCount]
    -[NSAssertionHandler handleFailureInMethod:...]
    objc_exception_throw  ->  std::__terminate  ->  abort

Qt's cocoa plugin observes `NSMenuDidBeginTrackingNotification` and reads
`clickCount` off the current event to tell a click from a click-and-hold. On
macOS 27 the current event at that moment is not always a mouse event, and
`clickCount` **raises** rather than returning zero for those. Nothing in the
notification-post path catches an Objective-C exception, so it unwinds through
C++ frames into `terminate()`.

It is not this app's bug. Two unrelated PySide6 apps here — Lab Hub and SONAR —
produced byte-identical stacks on the day the machine went to macOS 27, and
PySide6 6.11.2 is the newest release available. Reproduced directly: building
any non-mouse `NSEvent` and asking it for `clickCount` aborts the interpreter.

**This file is a copy of `lab_hub/ui/appkit_guard.py`, deliberately.** There is
no shared package in this workspace to put it in, and it is not a library — it
exists only until Qt ships a fixed cocoa plugin, and then every copy is deleted
together. Lab Hub holds the original, SONAR took the second copy on 2026-09-22,
Imprint the third on 2026-10-06 with its own menu bar item, and this is the
fourth, added the same day with Sentinel's. Keep them identical:
change one, change the others, and check `AGENTS.md` for which apps have a tray
menu and therefore need it. Sentinel is not a toy case — a run can be mid-flight
and paid for when the menu opens, and the abort takes the window with it.

## The guard

Replace `-[NSEvent clickCount]` with an implementation that answers 0 for the
event types that have no click count, and calls the original for the mouse
events that do. That is what the caller expects for a non-mouse event, and it
is what AppKit's own documentation describes the value as meaning.

Swizzling a framework method is not something to do lightly, so this one is
deliberately narrow: one selector, an unchanged answer for every event the
method was ever valid for, and a clean failure that leaves AppKit untouched if
any step does not resolve. Remove it when Qt ships a fixed cocoa plugin — the
test that proves it is `tests/test_appkit_guard.py`.
"""

from __future__ import annotations

import ctypes
import ctypes.util

# The event types that carry a click count. Everything else — key, system,
# application-defined, gesture, tablet — is what AppKit raises on.
MOUSE_EVENT_TYPES = frozenset(
    {
        1,  # LeftMouseDown          6  # LeftMouseDragged
        2,  # LeftMouseUp            7  # RightMouseDragged
        3,  # RightMouseDown        25  # OtherMouseDown
        4,  # RightMouseUp          26  # OtherMouseUp
        5,  # MouseMoved            27  # OtherMouseDragged
        6,
        7,
        25,
        26,
        27,
    }
)

# The replacement implementation must outlive the process: AppKit holds a raw
# function pointer to it, and a garbage-collected trampoline is a crash with a
# far worse stack than the one this fixes.
_KEEP: list = []

_CLICK_COUNT = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


def _runtime():
    objc = ctypes.cdll.LoadLibrary(
        ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
    )
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.method_setImplementation.restype = ctypes.c_void_p
    objc.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return objc


def installed() -> bool:
    return bool(_KEEP)


def install() -> bool:
    """Make `clickCount` safe. Returns whether it is now guarded."""
    if installed():
        return True
    try:
        objc = _runtime()
        event_class = objc.objc_getClass(b"NSEvent")
        click_sel = objc.sel_registerName(b"clickCount")
        type_sel = objc.sel_registerName(b"type")
        method = objc.class_getInstanceMethod(event_class, click_sel)
        if not event_class or not method:
            return False

        original = _CLICK_COUNT(objc.method_getImplementation(method))

        event_type = objc.objc_msgSend
        event_type.restype = ctypes.c_ulong
        event_type.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        @_CLICK_COUNT
        def guarded(event, cmd):  # noqa: ANN001 - an ObjC IMP
            try:
                if event_type(event, type_sel) in MOUSE_EVENT_TYPES:
                    return original(event, cmd)
            except Exception:  # noqa: BLE001 - an IMP must never raise
                return 0
            return 0

        _KEEP.append(guarded)
        objc.method_setImplementation(method, ctypes.cast(guarded, ctypes.c_void_p))
        return True
    except (OSError, AttributeError, TypeError, ValueError):
        _KEEP.clear()
        return False
