# mouthwords

Hold a hotkey, talk, release — your words get typed into whatever app has focus. 100% local, runs on Apple Silicon using [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

## Setup

Requires Python 3.10+ and a Mac (uses `pbcopy` and macOS keyboard APIs).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python dictate.py
```

The first run downloads the Whisper model (~150MB for `base.en`).

**Hold Right Option** to record. **Release** to transcribe and paste into the focused window.

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
| `MOUTHWORDS_HOTKEY` | `alt_r` | A [`pynput.keyboard.Key`](https://pynput.readthedocs.io/en/latest/keyboard.html#pynput.keyboard.Key) name: `alt_r`, `alt_l`, `ctrl_r`, `f13`, etc. |

Example:

```bash
MOUTHWORDS_MODEL=small.en MOUTHWORDS_HOTKEY=f13 python dictate.py
```

## Notes

- Pasting uses `pbcopy` + simulated `Cmd+V`, so your clipboard is overwritten with each dictation.
- Recordings shorter than 0.3s are discarded.
