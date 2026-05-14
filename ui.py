"""Floating non-activating panel for live transcript and stop/cancel buttons.

Visual style: borderless NSPanel with an NSVisualEffectView background
(macOS system blur/vibrancy), rounded corners, drop shadow. Stays above
other windows and does not steal focus when clicked — paste-into-focused
still works.

All public methods are thread-safe: they queue the actual UI work onto
the main NSOperationQueue, since AppKit must only be touched from main.
"""

from __future__ import annotations

from typing import Callable

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezelStyleRegularSquare,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFloatingWindowLevel,
    NSFont,
    NSMakeRect,
    NSNoBorder,
    NSPanel,
    NSScrollView,
    NSScreen,
    NSTextField,
    NSTextView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorTransient,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskResizable,
)
from Foundation import NSObject, NSOperationQueue


PANEL_W, PANEL_H = 480, 200
CORNER_RADIUS = 14.0


def _on_main(fn: Callable[[], None]) -> None:
    """Queue fn for execution on the main thread. Returns immediately."""
    NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def _rec_color():
    return NSColor.systemRedColor()


def _transcribing_color():
    return NSColor.systemOrangeColor()


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
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - PANEL_W) / 2
        y = screen.size.height - PANEL_H - 80
        rect = NSMakeRect(x, y, PANEL_W, PANEL_H)

        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorTransient
        )
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setMovableByWindowBackground_(True)

        # Vibrancy background as the content view, with rounded corners.
        effect = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H)
        )
        effect.setMaterial_(NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(CORNER_RADIUS)
        effect.layer().setMasksToBounds_(True)
        effect.layer().setBorderWidth_(0.5)
        effect.layer().setBorderColor_(
            NSColor.colorWithWhite_alpha_(1.0, 0.18).CGColor()
        )
        panel.setContentView_(effect)

        pad_x = 20
        # Status line at the top
        status = NSTextField.alloc().initWithFrame_(
            NSMakeRect(pad_x, PANEL_H - 38, PANEL_W - pad_x * 2, 22)
        )
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setEditable_(False)
        status.setSelectable_(False)
        status.setFont_(NSFont.systemFontOfSize_ofWeight_(13, 0.3))
        status.setTextColor_(_rec_color())
        status.setStringValue_("●  recording")
        effect.addSubview_(status)

        # Transcript text view inside a scroll view, both transparent
        scroll_h = PANEL_H - 38 - 20 - 56
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(pad_x, 56, PANEL_W - pad_x * 2, scroll_h)
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSNoBorder)
        scroll.setDrawsBackground_(False)
        scroll.setAutohidesScrollers_(True)

        text = NSTextView.alloc().initWithFrame_(scroll.bounds())
        text.setEditable_(False)
        text.setSelectable_(True)
        text.setRichText_(False)
        text.setDrawsBackground_(False)
        text.setFont_(NSFont.systemFontOfSize_(15))
        text.setTextColor_(NSColor.labelColor())
        text.setTextContainerInset_((4, 4))

        scroll.setDocumentView_(text)
        effect.addSubview_(scroll)

        # Buttons bottom-right
        btn_w, btn_h = 84, 28
        gap = 8
        stop_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PANEL_W - pad_x - btn_w, 14, btn_w, btn_h)
        )
        stop_btn.setTitle_("Stop")
        stop_btn.setBezelStyle_(NSBezelStyleRounded)
        stop_btn.setKeyEquivalent_("\r")
        stop_btn.setTarget_(self)
        stop_btn.setAction_("stopClicked:")
        effect.addSubview_(stop_btn)

        cancel_btn = NSButton.alloc().initWithFrame_(
            NSMakeRect(PANEL_W - pad_x - btn_w * 2 - gap, 14, btn_w, btn_h)
        )
        cancel_btn.setTitle_("Cancel")
        cancel_btn.setBezelStyle_(NSBezelStyleRounded)
        cancel_btn.setKeyEquivalent_("\x1b")
        cancel_btn.setTarget_(self)
        cancel_btn.setAction_("cancelClicked:")
        effect.addSubview_(cancel_btn)

        self._panel = panel
        self._status = status
        self._text_view = text

    def show(self) -> None:
        self._panel.orderFront_(None)

    def hide(self) -> None:
        self._panel.orderOut_(None)

    def setStatus_(self, s: str) -> None:
        # Pick a colour from a short prefix so the dot/icon glyph matches state.
        if s.startswith("✍") or "transcrib" in s.lower():
            self._status.setTextColor_(_transcribing_color())
        else:
            self._status.setTextColor_(_rec_color())
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
