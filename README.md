# mouthwords

Hold a hotkey, talk, release — your words get typed into whatever app has focus. 100% local, runs on Apple Silicon using [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

## Run

Requires Python 3.10+ and a Mac.

```bash
./run.sh
```

That's it. `run.sh` is idempotent: it creates the venv on first run, installs deps only when `requirements.txt` changes, then launches the app. The first run also downloads the Whisper model (~150MB for `base.en`).

**Hold Ctrl+Cmd+\\** to record. **Release** to transcribe and paste into the focused window. **Press Esc** while recording to abort without transcribing.

While recording, a small floating panel appears with a live transcript and **Stop** / **Cancel** buttons. The panel is a non-activating `NSPanel`, so clicking it doesn't steal focus from the window you're dictating into.

Auto-stop kicks in if you go silent for `MOUTHWORDS_SILENCE_SECONDS` (default 1.5s) after first speaking. A watchdog also hard-aborts recordings longer than `MOUTHWORDS_MAX_SECONDS` (default 120) in case macOS drops a key-release event.

## macOS permissions

The script needs two permissions, granted to whatever binary is running it (e.g. your Python interpreter or Terminal):

1. **Microphone** — `System Settings → Privacy & Security → Microphone`
2. **Accessibility** — `System Settings → Privacy & Security → Accessibility` (required to capture global hotkeys and simulate paste)
3. **Input Monitoring** — also under Privacy & Security; sometimes prompted alongside Accessibility

macOS will prompt the first time. If hotkeys silently don't work, the permission was denied — add Terminal (or whichever app is running Python) manually.

## Configuration

Environment variables:

| Var | Default | Notes |
|---|---|---|
| `MOUTHWORDS_MODEL` | `base.en` | Any whisper.cpp model name: `tiny.en`, `base.en`, `small.en`, `medium.en`, `large-v3` |
| `MOUTHWORDS_HOTKEY` | `ctrl+cmd+\` | Either a single [`pynput.keyboard.Key`](https://pynput.readthedocs.io/en/latest/keyboard.html#pynput.keyboard.Key) name (`alt_r`, `f13`) or a chord (`ctrl+cmd+\`, `cmd+shift+space`). Modifiers: `ctrl`, `cmd`, `shift`, `alt`. |
| `MOUTHWORDS_MAX_SECONDS` | `120` | Hard cap on a single recording. Auto-aborts if exceeded (catches stuck states when macOS drops a key-release event). |
| `MOUTHWORDS_SILENCE_SECONDS` | `1.5` | After first speech, stop and transcribe if this many seconds of silence pass. |
| `MOUTHWORDS_SILENCE_THRESHOLD` | `0.008` | RMS level below which audio is considered silence. Lower in a quiet room, higher in a noisy one. |
| `MOUTHWORDS_LIVE_INTERVAL` | `1.2` | Seconds between live-transcript refreshes in the panel. |
| `MOUTHWORDS_UI` | `1` | Set `0` to disable the floating panel and run headless (useful for SSH sessions or if AppKit fails to load). |

Example:

```bash
MOUTHWORDS_MODEL=small.en MOUTHWORDS_HOTKEY=f13 ./run.sh
```

## Notes

- Pasting uses `pbcopy` + simulated `Cmd+V`, so your clipboard is overwritten with each dictation.
- Recordings shorter than 0.3s are discarded.
