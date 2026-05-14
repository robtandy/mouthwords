#!/usr/bin/env python3
"""mouthwords: hold a hotkey to dictate, release to type into the focused app.

Default hotkey: Ctrl+Cmd+\\ (push-to-talk). Release to transcribe and paste.
Override with env vars: MOUTHWORDS_MODEL, MOUTHWORDS_HOTKEY.

MOUTHWORDS_HOTKEY accepts either a single pynput Key name ("alt_r", "f13")
or a chord like "ctrl+cmd+\\", "cmd+shift+space".
"""

from __future__ import annotations

import os
import re
import signal
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from pynput import keyboard
from pywhispercpp.model import Model

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3
MAX_SECONDS = float(os.environ.get("MOUTHWORDS_MAX_SECONDS", "120"))
SILENCE_THRESHOLD = float(os.environ.get("MOUTHWORDS_SILENCE_THRESHOLD", "0.008"))
SILENCE_SECONDS = float(os.environ.get("MOUTHWORDS_SILENCE_SECONDS", "20.0"))
LIVE_INTERVAL = float(os.environ.get("MOUTHWORDS_LIVE_INTERVAL", "1.2"))
RESUME_WINDOW = float(os.environ.get("MOUTHWORDS_RESUME_WINDOW", "30"))
MODEL_NAME = os.environ.get("MOUTHWORDS_MODEL", "base.en")
HOTKEY_SPEC = os.environ.get("MOUTHWORDS_HOTKEY", "ctrl+cmd+\\")
SHOW_UI = os.environ.get("MOUTHWORDS_UI", "1").lower() not in ("0", "false", "no")

REWRITES_ENABLED = os.environ.get("MOUTHWORDS_REWRITES", "1").lower() not in ("0", "false", "no")

# Word/phrase → symbol substitutions applied after every transcribe pass.
# Whisper is a natural-speech model — it always spells out "underscore",
# "open paren", etc. These rewrites turn the speech form into the symbol.
# Compiled once at import; word-boundary anchored so they don't match
# inside larger words.
SYMBOL_REWRITES = [
    (re.compile(r"\bunderscore\b", re.IGNORECASE), "_"),
    (re.compile(r"\basterisk\b", re.IGNORECASE), "*"),
    (re.compile(r"\btilde\b", re.IGNORECASE), "~"),
    (re.compile(r"\bcaret\b", re.IGNORECASE), "^"),
    (re.compile(r"\bampersand\b", re.IGNORECASE), "&"),
    (re.compile(r"\bbackslash\b", re.IGNORECASE), r"\\"),
    (re.compile(r"\bpipe\b", re.IGNORECASE), "|"),
    (re.compile(r"\bat\s+sign\b", re.IGNORECASE), "@"),
    (re.compile(r"\b(?:hash|pound)\s+sign\b", re.IGNORECASE), "#"),
    (re.compile(r"\bdollar\s+sign\b", re.IGNORECASE), "$"),
    (re.compile(r"\bpercent\s+sign\b", re.IGNORECASE), "%"),
    (re.compile(r"\bplus\s+sign\b", re.IGNORECASE), "+"),
    (re.compile(r"\bequals\s+sign\b", re.IGNORECASE), "="),
    (re.compile(r"\bopen\s+paren(?:thesis)?\b", re.IGNORECASE), "("),
    (re.compile(r"\bclose\s+paren(?:thesis)?\b", re.IGNORECASE), ")"),
    (re.compile(r"\bopen\s+bracket\b", re.IGNORECASE), "["),
    (re.compile(r"\bclose\s+bracket\b", re.IGNORECASE), "]"),
    (re.compile(r"\bopen\s+(?:brace|curly)\b", re.IGNORECASE), "{"),
    (re.compile(r"\bclose\s+(?:brace|curly)\b", re.IGNORECASE), "}"),
]


def rewrite_symbols(text: str) -> str:
    """Run the SYMBOL_REWRITES list over text and tidy up spacing.

    'foo underscore bar' → 'foo _ bar' after substitution → 'foo_bar' after
    cleanup. Spacing inside brackets is also tightened (so 'open paren x'
    becomes '(x' not '( x').
    """
    if not REWRITES_ENABLED:
        return text
    out = text
    for pattern, repl in SYMBOL_REWRITES:
        out = pattern.sub(repl, out)
    out = re.sub(r"(?<=\w)\s*_\s*(?=\w)", "_", out)
    out = re.sub(r"([(\[{])\s+", r"\1", out)
    out = re.sub(r"\s+([)\]}])", r"\1", out)
    return out


MOD_KEYS = {
    "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
    "cmd": {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
    "shift": {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r},
    "alt": {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r},
}
MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "cmd": "cmd", "command": "cmd", "meta": "cmd",
    "shift": "shift",
    "alt": "alt", "option": "alt", "opt": "alt",
}


def parse_hotkey(spec: str) -> tuple[set[str], object]:
    """Parse 'ctrl+cmd+\\' or 'alt_r' into (required_mods, trigger_key)."""
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    if not parts:
        sys.exit(f"empty hotkey spec '{spec}'")
    *mod_parts, trigger_part = parts
    required = set()
    for m in mod_parts:
        key = MOD_ALIASES.get(m.lower())
        if key is None:
            sys.exit(f"unknown modifier '{m}' in hotkey '{spec}'")
        required.add(key)
    tp = trigger_part
    if hasattr(keyboard.Key, tp):
        trigger = getattr(keyboard.Key, tp)
    elif len(tp) == 1:
        trigger = keyboard.KeyCode.from_char(tp)
    else:
        sys.exit(
            f"unknown trigger key '{tp}' in hotkey '{spec}'. "
            "Use a pynput Key name (alt_r, f13, space) or a single character."
        )
    return required, trigger


def key_to_mod(key) -> str | None:
    for name, keys in MOD_KEYS.items():
        if key in keys:
            return name
    return None


def matches_trigger(key, trigger) -> bool:
    if key == trigger:
        return True
    # Char-based fallback: pynput may report KeyCode(char=...) with vk set,
    # while our trigger from from_char has vk=None — so compare chars directly.
    a = getattr(key, "char", None)
    b = getattr(trigger, "char", None)
    return a is not None and a == b


REQUIRED_MODS, TRIGGER = parse_hotkey(HOTKEY_SPEC)

state_lock = threading.Lock()
model_lock = threading.Lock()
typed_lock = threading.Lock()
recording = False
held_mods: set[str] = set()
trigger_down = False
frames: list[np.ndarray] = []
stream: sd.InputStream | None = None
watchdog: threading.Timer | None = None
live_thread: threading.Thread | None = None

# Silence detection state
speech_started = False
last_speech_time = 0.0
silence_triggered = False

# Streaming-type state. typed_text is what we've sent to the focused window for
# the current recording; we diff each new transcript against it and backspace/
# type the delta. Cross-recording continuity comes from last_session_text +
# last_session_end, which feed the next transcribe call as initial_prompt.
typed_text = ""
current_session_prompt = ""
last_session_text = ""
last_session_end = 0.0

kb = keyboard.Controller()
panel = None  # populated in main() if SHOW_UI
status_item = None  # NSStatusItem, held module-level so pyobjc doesn't free it


def chord_active() -> bool:
    return REQUIRED_MODS.issubset(held_mods) and trigger_down


def audio_callback(indata, _frames, _time, _status):
    global speech_started, last_speech_time, silence_triggered
    frames.append(indata.copy())
    if silence_triggered or not recording:
        return
    rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
    now = time.time()
    if rms > SILENCE_THRESHOLD:
        if not speech_started:
            speech_started = True
        last_speech_time = now
    elif speech_started and (now - last_speech_time) > SILENCE_SECONDS:
        silence_triggered = True
        threading.Thread(target=_silence_stop, daemon=True).start()


def _silence_stop() -> None:
    global recording
    with state_lock:
        if not recording:
            return
        recording = False
    print("(silence detected)", flush=True)
    stop_and_transcribe()


def _set_status(text: str) -> None:
    if panel is not None:
        panel.set_status(text)


def _set_transcript(text: str) -> None:
    if panel is not None:
        panel.set_transcript(text)


def _show_panel() -> None:
    if panel is not None:
        panel.set_status("🎤 recording...")
        panel.set_transcript("")
        panel.show()


def _hide_panel() -> None:
    if panel is not None:
        panel.hide()


def start_recording() -> None:
    global stream, frames, watchdog, live_thread, typed_text
    global speech_started, last_speech_time, silence_triggered
    global current_session_prompt
    frames = []
    typed_text = ""
    speech_started = False
    last_speech_time = time.time()
    silence_triggered = False
    if last_session_text and (time.time() - last_session_end) < RESUME_WINDOW:
        current_session_prompt = _trim_prompt(last_session_text)
        print(
            f"resuming from previous session ({len(current_session_prompt)} char prompt)",
            flush=True,
        )
    else:
        current_session_prompt = ""
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback
    )
    stream.start()
    watchdog = threading.Timer(
        MAX_SECONDS, lambda: abort_recording(f"hit {MAX_SECONDS:g}s limit")
    )
    watchdog.daemon = True
    watchdog.start()
    live_thread = threading.Thread(target=_live_transcribe_loop, daemon=True)
    live_thread.start()
    _show_panel()
    print("recording...", flush=True)


def _teardown_stream() -> bool:
    """Stop the stream and cancel the watchdog. Returns True iff frames captured."""
    global stream, watchdog
    if watchdog is not None:
        watchdog.cancel()
        watchdog = None
    if stream is None:
        return False
    stream.stop()
    stream.close()
    stream = None
    return bool(frames)


def abort_recording(reason: str) -> None:
    """Drop the current recording without transcribing. Safe from any thread.

    Also backspaces any partial text that the live loop already streamed into
    the focused window — Cancel/Esc should leave the target app as if the
    dictation never happened.
    """
    global recording
    with state_lock:
        if not recording:
            return
        recording = False
    _teardown_stream()
    emit_text_update("")
    _hide_panel()
    print(f"aborted: {reason}", flush=True)


def stop_and_transcribe() -> None:
    global last_session_text, last_session_end
    if not _teardown_stream():
        _hide_panel()
        return
    audio = np.concatenate(frames, axis=0).flatten().astype(np.float32)
    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_SECONDS:
        _hide_panel()
        print(f"(too short: {duration:.2f}s)", flush=True)
        return
    _set_status("✍️  transcribing...")
    t0 = time.time()
    segments = _transcribe(audio)
    text = rewrite_symbols(" ".join(s.text for s in segments).strip())
    elapsed = time.time() - t0
    _hide_panel()
    if not text:
        print(f"(no speech, {elapsed:.2f}s)", flush=True)
        return
    emit_text_update(text)
    # Snapshot for resume: prior context + this recording's text.
    prior = current_session_prompt
    last_session_text = (prior + " " + text).strip() if prior else text
    last_session_end = time.time()
    print(f"[{elapsed:.2f}s] {text}", flush=True)


def _live_transcribe_loop() -> None:
    """Periodically re-transcribe the accumulated audio.

    Pushes the latest text to the panel (if any) and streams it into the
    focused window via emit_text_update so the user sees words appear as
    they speak.
    """
    last_text = ""
    last_run = time.time()
    while True:
        time.sleep(0.1)
        if not recording:
            return
        if time.time() - last_run < LIVE_INTERVAL:
            continue
        if not frames:
            continue
        snapshot = list(frames)
        audio = np.concatenate(snapshot, axis=0).flatten().astype(np.float32)
        if len(audio) < SAMPLE_RATE * 0.5:
            continue
        last_run = time.time()
        if not recording:
            return
        try:
            segments = _transcribe(audio)
        except Exception as e:
            print(f"(live transcribe error: {e})", flush=True)
            continue
        if not recording:
            return
        text = rewrite_symbols(" ".join(s.text for s in segments).strip())
        if text and text != last_text:
            last_text = text
            _set_transcript(text)
            emit_text_update(text)


def emit_text_update(new_text: str) -> None:
    """Stream a transcript update into the focused window.

    Diffs new_text against what's already been typed this recording and emits
    backspaces + new characters so revisions from whisper are reflected
    instead of duplicated. Newlines/tabs are flattened to spaces because
    those keys are submit/indent in many target apps.
    """
    global typed_text
    new_text = new_text.replace("\n", " ").replace("\r", "").replace("\t", " ")
    with typed_lock:
        old = typed_text
        if new_text == old:
            return
        i = 0
        n = min(len(old), len(new_text))
        while i < n and old[i] == new_text[i]:
            i += 1
        backspaces = len(old) - i
        addition = new_text[i:]
        for _ in range(backspaces):
            kb.press(keyboard.Key.backspace)
            kb.release(keyboard.Key.backspace)
        if addition:
            kb.type(addition)
        typed_text = new_text


def _trim_prompt(text: str, max_chars: int = 500) -> str:
    """Whisper's initial_prompt has a token cap (~224 tokens). Keep ~500 chars."""
    if len(text) <= max_chars:
        return text
    trimmed = text[-max_chars:]
    if " " in trimmed:
        trimmed = trimmed[trimmed.index(" ") + 1:]
    return trimmed


def _transcribe(audio: np.ndarray):
    """Run whisper, optionally with the previous session's text as prompt."""
    kwargs = {}
    if current_session_prompt:
        kwargs["initial_prompt"] = current_session_prompt
    with model_lock:
        return model.transcribe(audio, **kwargs)


def on_press(key) -> None:
    global recording, trigger_down
    if key == keyboard.Key.esc and recording:
        abort_recording("escape pressed")
        return
    mod = key_to_mod(key)
    if mod:
        held_mods.add(mod)
    if matches_trigger(key, TRIGGER):
        trigger_down = True
    if chord_active():
        with state_lock:
            if recording:
                return
            recording = True
        start_recording()


def on_release(key) -> None:
    global recording, trigger_down
    was_active = chord_active()
    mod = key_to_mod(key)
    if mod:
        held_mods.discard(mod)
    if matches_trigger(key, TRIGGER):
        trigger_down = False
    if was_active and not chord_active():
        with state_lock:
            if not recording:
                return
            recording = False
        threading.Thread(target=stop_and_transcribe, daemon=True).start()


def user_stop_clicked() -> None:
    """Called from the panel's Stop button (main thread). Spawn worker so UI stays responsive."""
    global recording
    with state_lock:
        if not recording:
            return
        recording = False
    threading.Thread(target=stop_and_transcribe, daemon=True).start()


def user_cancel_clicked() -> None:
    threading.Thread(
        target=lambda: abort_recording("cancel button"), daemon=True
    ).start()


def ensure_permissions() -> None:
    """Block startup until macOS permissions look usable."""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
    except ImportError:
        print(
            "warning: pyobjc-framework-ApplicationServices not installed; "
            "skipping accessibility check",
            flush=True,
        )
    else:
        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
            sys.exit(
                "Accessibility permission required to simulate paste.\n"
                "A system dialog should have appeared; if not, open:\n"
                "  System Settings → Privacy & Security → Accessibility\n"
                "and enable the app running this script (e.g. Terminal, iTerm).\n"
                "Then re-run."
            )

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32"
        ):
            time.sleep(0.05)
    except Exception as e:
        sys.exit(
            f"Microphone access failed: {e}\n"
            "Grant Microphone permission in:\n"
            "  System Settings → Privacy & Security → Microphone\n"
            "Then re-run."
        )


def _install_sigint_watcher() -> None:
    """Make Ctrl-C terminate the process reliably.

    AppHelper.runEventLoop blocks the main thread in CFRunLoopRun, so
    Python's signal handler (which runs at the next bytecode boundary)
    never fires. Use signal.set_wakeup_fd: Python's C-level signal
    handler also writes the signal number into a pipe — that write
    happens from kernel signal-context, asynchronous to whatever the
    main thread is doing. A dedicated watcher thread blocks on the
    read end of the pipe and hard-exits when a byte arrives.

    sigwait + pthread_sigmask was tried first but proved fragile —
    PortAudio's audio threads can unblock SIGINT and absorb it before
    our watcher sees it pending.
    """
    r, w = os.pipe()
    os.set_blocking(w, False)
    signal.set_wakeup_fd(w, warn_on_full_buffer=False)
    # No-op Python handlers; we only need Python's signal module to
    # accept the signals so the C-level handler keeps writing to the
    # wakeup fd.
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.signal(signal.SIGTERM, lambda *_: None)

    def _watcher() -> None:
        sig_byte = os.read(r, 1)
        sig_num = sig_byte[0] if sig_byte else 0
        print(f"\nshutting down (signal {sig_num})", flush=True)
        os._exit(130)

    threading.Thread(target=_watcher, daemon=True, name="sigint-watcher").start()


def main() -> None:
    global model, panel
    _install_sigint_watcher()
    ensure_permissions()
    print(f"loading model '{MODEL_NAME}' (first run downloads it)...", flush=True)
    model = Model(MODEL_NAME, print_realtime=False, print_progress=False)
    print(f"ready. hold {HOTKEY_SPEC} to dictate. ctrl-c to quit.", flush=True)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    if SHOW_UI:
        try:
            from AppKit import (
                NSApp,
                NSApplication,
                NSApplicationActivationPolicyAccessory,
                NSMenu,
                NSMenuItem,
                NSStatusBar,
                NSVariableStatusItemLength,
            )
            from PyObjCTools import AppHelper

            from ui import Panel

            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            panel = Panel(on_stop=user_stop_clicked, on_cancel=user_cancel_clicked)

            # Menu bar status item. Held module-level (status_item global) so
            # ARC/pyobjc doesn't deallocate it.
            global status_item
            status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            status_item.button().setTitle_("🎤")
            status_item.button().setToolTip_(f"mouthwords — hold {HOTKEY_SPEC} to dictate")
            menu = NSMenu.alloc().init()
            menu.addItem_(
                NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    f"hold {HOTKEY_SPEC} to dictate", None, ""
                )
            )
            menu.addItem_(NSMenuItem.separatorItem())
            quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Quit mouthwords", "terminate:", "q"
            )
            quit_item.setTarget_(NSApp)
            menu.addItem_(quit_item)
            status_item.setMenu_(menu)

            # Don't pass installInterrupt=True — pyobjc replaces our
            # signal handlers with its own mach-interrupt setup, which is
            # less reliable. _install_sigint_watcher already wired our
            # own SIGINT path before the run loop started.
            AppHelper.runEventLoop()
        except ImportError as e:
            print(
                f"warning: UI disabled ({e}). Set MOUTHWORDS_UI=0 to silence.",
                flush=True,
            )
            listener.join()
    else:
        listener.join()


if __name__ == "__main__":
    main()
