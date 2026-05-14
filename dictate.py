#!/usr/bin/env python3
"""mouthwords: hold a hotkey to dictate, release to type into the focused app.

Default hotkey: Ctrl+Cmd+\\ (push-to-talk). Release to transcribe and paste.
Override with env vars: MOUTHWORDS_MODEL, MOUTHWORDS_HOTKEY.

MOUTHWORDS_HOTKEY accepts either a single pynput Key name ("alt_r", "f13")
or a chord like "ctrl+cmd+\\", "cmd+shift+space".
"""

from __future__ import annotations

import os
import subprocess
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
SILENCE_SECONDS = float(os.environ.get("MOUTHWORDS_SILENCE_SECONDS", "1.5"))
LIVE_INTERVAL = float(os.environ.get("MOUTHWORDS_LIVE_INTERVAL", "1.2"))
MODEL_NAME = os.environ.get("MOUTHWORDS_MODEL", "base.en")
HOTKEY_SPEC = os.environ.get("MOUTHWORDS_HOTKEY", "ctrl+cmd+\\")
SHOW_UI = os.environ.get("MOUTHWORDS_UI", "1").lower() not in ("0", "false", "no")

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

kb = keyboard.Controller()
panel = None  # populated in main() if SHOW_UI


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
    global stream, frames, watchdog, live_thread
    global speech_started, last_speech_time, silence_triggered
    frames = []
    speech_started = False
    last_speech_time = time.time()
    silence_triggered = False
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
    """Drop the current recording without transcribing. Safe from any thread."""
    global recording
    with state_lock:
        if not recording:
            return
        recording = False
    _teardown_stream()
    _hide_panel()
    print(f"aborted: {reason}", flush=True)


def stop_and_transcribe() -> None:
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
    with model_lock:
        segments = model.transcribe(audio)
    text = " ".join(s.text for s in segments).strip()
    elapsed = time.time() - t0
    _hide_panel()
    if not text:
        print(f"(no speech, {elapsed:.2f}s)", flush=True)
        return
    print(f"[{elapsed:.2f}s] {text}", flush=True)
    paste(text)


def _live_transcribe_loop() -> None:
    """Periodically re-transcribe the accumulated audio and push to the panel."""
    if panel is None:
        return
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
        # Skip if the user already stopped while we were waiting
        if not recording:
            return
        try:
            with model_lock:
                if not recording:
                    return
                segments = model.transcribe(audio)
        except Exception as e:
            print(f"(live transcribe error: {e})", flush=True)
            continue
        text = " ".join(s.text for s in segments).strip()
        if text and text != last_text:
            last_text = text
            _set_transcript(text)


def paste(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.05)
    with kb.pressed(keyboard.Key.cmd):
        kb.press("v")
        kb.release("v")


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


def main() -> None:
    global model, panel
    ensure_permissions()
    print(f"loading model '{MODEL_NAME}' (first run downloads it)...", flush=True)
    model = Model(MODEL_NAME, print_realtime=False, print_progress=False)
    print(f"ready. hold {HOTKEY_SPEC} to dictate. ctrl-c to quit.", flush=True)

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()

    if SHOW_UI:
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
            from PyObjCTools import AppHelper

            from ui import Panel

            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            panel = Panel(on_stop=user_stop_clicked, on_cancel=user_cancel_clicked)
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
