"""The guard around AppKit's `clickCount` assertion.

macOS 27 made `-[NSEvent clickCount]` raise for events that have no click
count, and Qt's cocoa plugin asks for it whenever a menu begins tracking — so
opening the menu bar item's menu aborted the process. Two unrelated PySide6
apps here produced byte-identical stacks.

These run against the real Objective-C runtime, because a mock of AppKit would
be a mock of the thing being tested. They are skipped off macOS.

Ported from `lab_hub/tests/test_appkit_guard.py` along with the guard itself —
the fourth copy, after SONAR's and Imprint's. All of them go when Qt ships a
fixed cocoa plugin; until then keep them in step.

Sentinel acquired a menu bar item and therefore this crash on the same day. Its
menu holds the readout of what a run is costing and the way to quit, so it gets
opened while work is in flight — which is the worst moment to abort.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit only")

NON_MOUSE = 15  # NSEventTypeApplicationDefined
LEFT_MOUSE_DOWN = 1


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


@pytest.fixture(scope="module")
def appkit():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    return objc


def _sel(objc, name):
    return objc.sel_registerName(name.encode())


def _non_mouse_event(objc):
    make = objc.objc_msgSend
    make.restype = ctypes.c_void_p
    make.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, CGPoint, ctypes.c_ulong,
        ctypes.c_double, ctypes.c_long, ctypes.c_void_p, ctypes.c_short,
        ctypes.c_long, ctypes.c_long,
    ]
    return make(
        objc.objc_getClass(b"NSEvent"),
        _sel(objc, "otherEventWithType:location:modifierFlags:timestamp:"
                   "windowNumber:context:subtype:data1:data2:"),
        NON_MOUSE, CGPoint(0, 0), 0, 0.0, 0, None, 0, 0, 0,
    )


def _mouse_event(objc, clicks):
    make = objc.objc_msgSend
    make.restype = ctypes.c_void_p
    make.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, CGPoint, ctypes.c_ulong,
        ctypes.c_double, ctypes.c_long, ctypes.c_void_p, ctypes.c_long,
        ctypes.c_long, ctypes.c_float,
    ]
    return make(
        objc.objc_getClass(b"NSEvent"),
        _sel(objc, "mouseEventWithType:location:modifierFlags:timestamp:"
                   "windowNumber:context:eventNumber:clickCount:pressure:"),
        LEFT_MOUSE_DOWN, CGPoint(0, 0), 0, 0.0, 0, None, 0, clicks, 1.0,
    )


def _click_count(objc, event):
    call = objc.objc_msgSend
    call.restype = ctypes.c_long
    call.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    return call(event, _sel(objc, "clickCount"))


def test_the_guard_installs():
    from ui import appkit_guard

    assert appkit_guard.install()
    assert appkit_guard.installed()


def test_installing_twice_is_harmless():
    """A second swizzle would call the first as its 'original' and recurse."""
    from ui import appkit_guard

    appkit_guard.install()
    before = len(appkit_guard._KEEP)

    appkit_guard.install()

    assert len(appkit_guard._KEEP) == before


def test_a_non_mouse_event_answers_zero_instead_of_aborting(appkit):
    """Unguarded, this call terminates the interpreter — it is the crash."""
    from ui import appkit_guard

    appkit_guard.install()

    assert _click_count(appkit, _non_mouse_event(appkit)) == 0


def test_a_real_click_still_reports_its_count(appkit):
    """The guard must not flatten the answer it was asked for."""
    from ui import appkit_guard

    appkit_guard.install()

    assert _click_count(appkit, _mouse_event(appkit, 2)) == 2
    assert _click_count(appkit, _mouse_event(appkit, 1)) == 1


def test_every_clickable_event_type_is_passed_through():
    """The pass-through list is the point: narrow the guard to the types that
    genuinely have no click count, and change nothing else."""
    from ui.appkit_guard import MOUSE_EVENT_TYPES

    for event_type in (1, 2, 3, 4, 5, 6, 7, 25, 26, 27):
        assert event_type in MOUSE_EVENT_TYPES
    assert NON_MOUSE not in MOUSE_EVENT_TYPES
