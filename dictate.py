#!/usr/bin/env python3
"""mouthwords: hold a hotkey to dictate, release to type into the focused app.

Defaults: hold Right Option to record. Release to transcribe and paste.
Override with env vars: MOUTHWORDS_MODEL, MOUTHWORDS_HOTKEY.
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
MODEL_NAME = os.environ.get("MOUTHWORDS_MODEL", "base.en")
HOTKEY_NAME = os.environ.get("MOUTHWORDS_HOTKEY", "alt_r")


def resolve_hotkey(name: str) -> keyboard.Key:
    try:
        return getattr(keyboard.Key, name)
    except AttributeError:
        sys.exit(f"Unknown hotkey '{name}'. Try one of: alt_r, alt_l, ctrl_r, f13, f14")


HOTKEY = resolve_hotkey(HOTKEY_NAME)

state_lock = threading.Lock()
recording = False
frames: list[np.ndarray] = []
stream: sd.InputStream | None = None
kb = keyboard.Controller()


def audio_callback(indata, _frames, _time, _status):
    frames.append(indata.copy())


def start_recording() -> None:
    global stream, frames
    frames = []
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback
    )
    stream.start()
    print("recording...", flush=True)


def stop_and_transcribe() -> None:
    global stream
    if stream is None:
        return
    stream.stop()
    stream.close()
    stream = None
    if not frames:
        return
    audio = np.concatenate(frames, axis=0).flatten().astype(np.float32)
    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_SECONDS:
        print(f"(too short: {duration:.2f}s)", flush=True)
        return
    t0 = time.time()
    segments = model.transcribe(audio)
    text = " ".join(s.text for s in segments).strip()
    elapsed = time.time() - t0
    if not text:
        print(f"(no speech, {elapsed:.2f}s)", flush=True)
        return
    print(f"[{elapsed:.2f}s] {text}", flush=True)
    paste(text)


def paste(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.05)
    with kb.pressed(keyboard.Key.cmd):
        kb.press("v")
        kb.release("v")


def on_press(key) -> None:
    global recording
    if key != HOTKEY:
        return
    with state_lock:
        if recording:
            return
        recording = True
    start_recording()


def on_release(key) -> None:
    global recording
    if key != HOTKEY:
        return
    with state_lock:
        if not recording:
            return
        recording = False
    threading.Thread(target=stop_and_transcribe, daemon=True).start()


def main() -> None:
    global model
    print(f"loading model '{MODEL_NAME}' (first run downloads it)...", flush=True)
    model = Model(MODEL_NAME, print_realtime=False, print_progress=False)
    print(f"ready. hold {HOTKEY_NAME} to dictate. ctrl-c to quit.", flush=True)
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
