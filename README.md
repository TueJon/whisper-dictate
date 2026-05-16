# whisper-dictate

Press a keyboard shortcut, speak, press it again — your speech is transcribed
with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and pasted
wherever the cursor is. Built for **GNOME on Wayland** (works in terminals too).

- Toggle dictation with one shortcut (default **Ctrl+Alt+D**) — tap to start,
  tap again to stop, transcribe and paste. No push‑to‑hold.
- German **and** English out of the box (auto-detected), model `large-v3`.
- GPU (NVIDIA/CUDA) with automatic CPU fallback.
- Pastes via the clipboard so it is **keyboard-layout independent**
  (works with the Austrian QWERTZ layout, umlauts, etc.).

## Requirements

- Linux, GNOME on Wayland (tested on Ubuntu 25.10 / GNOME 49).
- Python 3.10+.
- A microphone (PipeWire/PulseAudio default source is used).
- Write access to `/dev/uinput` (for synthesizing the paste keystroke).
- Optional: an NVIDIA GPU + driver for fast transcription. Without one it
  falls back to CPU automatically.

## Install

```bash
git clone <this-repo> ~/dev/whisper-dictate
cd ~/dev/whisper-dictate
./install.sh                 # add --no-model to skip the ~3 GB pre-download
```

`install.sh` is idempotent — re-run it after `git pull`. It installs the
system packages (asks for sudo), creates a private venv, installs the
scripts, pre-fetches the model, and registers the GNOME shortcut.

Override the shortcut: `SHORTCUT='<Super>r' ./install.sh`.

## Usage

1. Focus any text field (browser, editor, **terminal**, chat …).
2. Press **Ctrl+Alt+D** → bell + “● Recording”. Speak.
3. Press **Ctrl+Alt+D** again → “Transcribing…”, then the text is pasted.

## Configuration

Tunables (env vars; set them in `~/.local/bin/whisper-dictate`):

| Var | Default | Meaning |
|---|---|---|
| `WD_MODEL` | `large-v3` | faster-whisper model name |
| `WD_DEVICE` | `cuda` | `cuda` or `cpu` (auto-falls back to cpu) |
| `WD_COMPUTE` | `auto` | CT2 compute type (`auto`/`int8`/`float16`/…) |
| `WD_BEAM` | `1` | beam size (1 = greedy, snappy) |
| `WD_LANG` | _(auto)_ | force a language, e.g. `de` |
| `WD_PASTE_KEY` | `shift+insert` | ydotool key sequence used to paste |

## How it works

```
Ctrl+Alt+D ──> whisper-dictate (toggle)
  press 1:  ffmpeg records the default mic -> /run/user/$UID/whisper-dictate/rec.wav
  press 2:  stop ffmpeg -> transcribe.py (faster-whisper, GPU) -> text
            -> wl-copy (clipboard + primary) -> ydotool Shift+Insert
```

State and the recording live in `$XDG_RUNTIME_DIR/whisper-dictate/`
(tmpfs; the audio is deleted right after transcription). The last run's
stderr is in `~/.local/share/whisper-dictate/last-run.log`.

### Design notes (non-obvious decisions)

- **Paste = Shift+Insert, on clipboard *and* primary.** GNOME 49 Wayland
  has no usable focused-window API (`Introspect` is `AccessDenied`, xdotool
  can't see Wayland-native terminals), so per-app key selection isn't
  possible. Shift+Insert is the one paste key that works both in VTE
  terminals (which paste the *primary* selection) and in GTK/Qt/LibreOffice/
  JetBrains apps (which paste the *clipboard*) — so the text is put on both.
- **Clipboard paste, not key-typing.** `ydotool type` emits US scancodes
  and would mangle the Austrian QWERTZ layout; `wtype` needs the virtual
  keyboard protocol that GNOME/Mutter does not implement. Clipboard paste
  is the only layout-safe route.
- **`ydotool` 0.1.8 is daemonless** on Ubuntu (no `ydotoold`); it opens
  `/dev/uinput` directly, hence the `/dev/uinput` access requirement and
  the `--delay` before the keystroke (the freshly created virtual input
  device must be enumerated by Mutter first).
- **Lock fd is closed (`9>&-`) on every backgrounded child.** bash fds
  from `exec N>` are inherited and are *not* close-on-exec, so `ffmpeg`/
  `wl-copy`/`paplay` would otherwise keep the `flock` held and wedge
  every later press as “busy”.
- **No `setsid` for the recorder.** Under job control `setsid` may fork,
  making `$!` the wrong PID so the recorder can never be stopped. A
  GNOME-spawned script has no controlling tty, so a plain `&` already
  lets `ffmpeg` outlive the script; STOP escalates SIGINT→TERM→KILL and
  sweeps strays so a recorder can never run away.

## Latency

`large-v3` is loaded on demand each use (~10–15 s: model load + CUDA init
+ inference). For near-instant transcription, run a persistent model
server that stays warm in VRAM (not included here).

## Troubleshooting

- **“Busy — still transcribing”** that never clears: a stuck recorder.
  `pkill -INT -f 'ffmpeg.*whisper-dictate'` and remove
  `$XDG_RUNTIME_DIR/whisper-dictate/state.lock`.
- **Nothing pastes:** check `~/.local/share/whisper-dictate/last-run.log`.
  Confirm `/dev/uinput` is writable and `ydotool key shift+insert` works.
- **GPU errors in the log:** it auto-falls back to CPU; check NVIDIA
  driver/CUDA. `WD_DEVICE=cpu` forces CPU.
- **Hallucinated text on silence** (e.g. “das ist ein Test”): a known
  Whisper trait on near-silence; harmless when you actually speak.

## Uninstall

```bash
rm -rf ~/.local/share/whisper-dictate ~/.local/bin/whisper-dictate
# then remove the "Whisper Dictate" entry in
# Settings → Keyboard → Keyboard Shortcuts
```
