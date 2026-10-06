"""The name and icon macOS shows for a Sentinel that runs from the checkout.

These run against the real Objective-C runtime, because a mock of AppKit would
be a mock of the thing being tested. They are skipped off macOS.

One thing here is deliberately not covered: handing the icon to NSApp. Asking
for `sharedApplication` builds a real application object in the test process,
and a test suite that puts its own icon in the Dock has overstepped. Everything
up to that point — the file check, and whether AppKit will read the artwork at
all, which is what actually breaks when the asset moves — is covered below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ui import app_identity

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppKit only")

ROOT = Path(__file__).resolve().parent.parent


def test_naming_the_process_titles_the_application_menu():
    """CFBundleName is what macOS puts next to the Apple logo. Unnamed, this
    process is titled after whatever script started it."""
    assert app_identity.name_in_menu_bar("Sentinel")
    assert app_identity.menu_bar_name() == "Sentinel"


def test_a_name_that_is_already_there_is_left_alone():
    """A real bundle answers with its own Info.plist, and that one is
    authoritative — this is what keeps the frozen build untouched."""
    app_identity.name_in_menu_bar("Sentinel")

    assert app_identity.name_in_menu_bar("Something Else")
    assert app_identity.menu_bar_name() == "Sentinel"


def test_the_dock_icon_is_refused_when_the_file_is_gone():
    assert not app_identity.set_dock_icon(ROOT / "assets" / "no-such-icon.icns")


def test_the_app_icon_is_artwork_appkit_can_read():
    """The asset exists is not the same question as AppKit will open it."""
    assert app_identity._nsimage(ROOT / "assets" / "icon.icns") is not None


def test_a_file_that_is_not_an_image_is_refused_rather_than_shown():
    assert app_identity._nsimage(ROOT / "VERSION") is None
    assert not app_identity.set_dock_icon(ROOT / "VERSION")
