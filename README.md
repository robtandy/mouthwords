# mouthwords

Hold a hotkey, talk, release — your words get typed into whatever app has focus. 100% local, runs on Apple Silicon using [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

## Run

Requires Python 3.10+ and a Mac.

```bash
./run.sh
```

That's it. `run.sh` is idempotent: it creates the venv on first run, installs deps only when `requirements.txt` changes, then launches the app. The first run also downloads the Whisper model (~150MB for `base.en`).

**Hold Ctrl+Cmd+\\** to record. Words appear in the focused window as you speak (whisper revisions are reconciled with backspaces, so the text stays correct). **Release** to do a final pass and lock it in. **Press Esc** while recording to abort and roll back anything that was already typed.

While recording, a small floating panel shows the live transcript and **Stop** / **Cancel** buttons. The panel is a non-activating `NSPanel`, so clicking it doesn't steal focus from the window you're dictating into.

If you re-engage the chord within `MOUTHWORDS_RESUME_WINDOW` seconds (default 30) of the last recording ending, the previous transcript is passed to whisper as `initial_prompt` so style, capitalization, and mid-sentence flow continue naturally.

Auto-stop kicks in if you go silent for `MOUTHWORDS_SILENCE_SECONDS` (default 20s) after first speaking. A watchdog also hard-aborts recordings longer than `MOUTHWORDS_MAX_SECONDS` (default 120) in case macOS drops a key-release event.

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
| `MOUTHWORDS_SILENCE_SECONDS` | `20` | After first speech, stop and transcribe if this many seconds of silence pass. |
| `MOUTHWORDS_SILENCE_THRESHOLD` | `0.008` | RMS level below which audio is considered silence. Lower in a quiet room, higher in a noisy one. |
| `MOUTHWORDS_LIVE_INTERVAL` | `1.2` | Seconds between live-transcript refreshes (also how often new text gets streamed into the focused window). |
| `MOUTHWORDS_RESUME_WINDOW` | `30` | Seconds within which a new recording inherits the previous transcript as whisper's `initial_prompt` for continuity. |
| `MOUTHWORDS_UI` | `1` | Set `0` to disable the floating panel and run headless. Streaming text into the focused window still works. |
| `MOUTHWORDS_PANEL_ALPHA` | `0.82` | Panel transparency (0.0–1.0). Lower = more see-through. |

Example:

```bash
MOUTHWORDS_MODEL=small.en MOUTHWORDS_HOTKEY=f13 ./run.sh
```

## Notes

- Text is streamed into the focused window via simulated keystrokes, not the clipboard, so your clipboard is untouched.
- Recordings shorter than 0.3s are discarded.
- Don't type manually into the target while a dictation is active — the streaming-diff logic assumes only it is editing.
