"""Floating non-activating panel for live transcript and stop/cancel buttons.

The panel uses NSWindowStyleMaskNonactivatingPanel so clicking its buttons
does *not* steal focus from whatever window the user was typing into —
critical, because we paste into the focused window.

All public methods are thread-safe: they queue the actual UI work onto
the main NSOperationQueue, since AppKit must only be touched from main.
"""

from __future__ import annotations

from typing import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelBorder,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScrollView,
    NSScreen,
    NSTextField,
    NSTextView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorTransient,
    NSWindowStyleMaskHUDWindow,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow,
)
from Foundation import NSObject, NSOperationQueue


def _on_main(fn: Callable[[], None]) -> None:
    """Queue fn for execution on the main thread. Returns immediately."""
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class _PanelController(NSObject):
    """Owns the NSPanel and routes button clicks back to Python callbacks."""

    def initWithStop_cancel_(self, on_stop, on_cancel):
        self = objc.super(_PanelController, self).init()
        if self is None:
            return None
        self._on_stop = on_stop
        self._on_cancel = on_cancel
        self._build()
        return self

    def _build(self) -> None:
        width, height = 520, 220
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - width) / 2
        y = screen.size.height - height - 80
        rect = NSMakeRect(x, y, width, height)

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskUtilityWindow
            | NSWindowStyleMaskHUDWindow
            | NSWindowStyleMaskNonactivatingPanel
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        panel.setTitle_("mouthwords")
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorTransient
        )
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)

        content = panel.contentView()

        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(16, height - 50, width - 32, 22)
        )
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setStringValue_("recording...")
        status.setFont_(NSFont.boldSystemFontOfSize_(13))
        content.addSubview_(status)

        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(16, 60, width - 32, height - 130)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutohidesScrollers_(True)
        text = NSTextView.alloc().initWithFrame_(scroll.bounds())
        text.setEditable_(False)
        text.setRichText_(False)
        text.setFont_(NSFont.systemFontOfSize_(14))
        text.setTextContainerInset_((6, 6))
        scroll.setDocumentView_(text)
        content.addSubview_(scroll)

        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(width - 200, 14, 88, 32)
        )
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(1)
        cancel_btn.setKeyEquivalent_("\x1b")
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancelClicked:")
        content.addSubview_(cancel_btn)

        stop_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(width - 104, 14, 88, 32)
        )
        stop_btn.setTitle_("Stop")
        stop_btn.setBezelStyle_(1)
        stop_btn.setKeyEquivalent_("\r")
        stop_btn.setTarget_(self)
        stop_btn.setAction_("stopClicked:")
        content.addSubview_(stop_btn)

        self._panel = panel
        self._status = status
        self._text_view = text

    def show(self) -> None:
        self._panel.orderFront_(None)

    def hide(self) -> None:
        self._panel.orderOut_(None)

    def setStatus_(self, s: str) -> None:
        self._status.setStringValue_(s)

    def setTranscript_(self, s: str) -> None:
        self._text_view.setString_(s)
        self._text_view.scrollRangeToVisible_((len(s), 0))

    def stopClicked_(self, sender) -> None:
        self._on_stop()

    def cancelClicked_(self, sender) -> None:
        self._on_cancel()


class Panel:
    """Thread-safe façade. Construct on main thread; methods may be called from any."""

    def __init__(self, on_stop: Callable[[], None], on_cancel: Callable[[], None]):
        self._ctrl = _PanelController.alloc().initWithStop_cancel_(on_stop, on_cancel)

    def show(self) -> None:
        _on_main(self._ctrl.show)

    def hide(self) -> None:
        _on_main(self._ctrl.hide)

    def set_status(self, text: str) -> None:
        _on_main(lambda: self._ctrl.setStatus_(text))

    def set_transcript(self, text: str) -> None:
        _on_main(lambda: self._ctrl.setTranscript_(text))
