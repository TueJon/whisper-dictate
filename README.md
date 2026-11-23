# whisper-dictate

Press a keyboard shortcut, speak, press it again — your speech is transcribed
with Gemini, OpenAI, or [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
and pasted wherever the cursor is. Works on **GNOME with Wayland or X11**.

- Toggle dictation with one shortcut (default **Ctrl+Alt+D**) — tap to start,
  tap again to stop, transcribe and paste. No push‑to‑hold.
- German **and** English out of the box with automatic language detection.
- Local Whisper fallback when the selected cloud backend fails. Local inference
  supports NVIDIA/CUDA with automatic CPU fallback.
- Pastes via the clipboard so it is **keyboard-layout independent**
  (works with the Austrian QWERTZ layout, umlauts, etc.).
- A **top-bar indicator** shows “● REC” / “⏳ …” while recording and
  transcribing, and hides itself when idle.

## Requirements

- Linux and GNOME (Wayland or X11).
- Python 3.10+.
- A microphone (PipeWire/PulseAudio default source is used).
- On Wayland, write access to `/dev/uinput` for the paste keystroke.
  On X11, the installer uses `xclip` and `xdotool`.
- For the top-bar indicator: an AppIndicator/SNI GNOME extension enabled
  (on Ubuntu `ubuntu-appindicators@ubuntu.com`; the installer enables it).
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

For a first install on a laptop without NVIDIA, use `./install.sh --cpu --no-model`.
This uses the smaller multilingual Whisper `small` model on CPU for fallback.
The local model is downloaded on the first local transcription. To prepare it
ahead of going offline, omit `--no-model`. Use `./install.sh --update` to refresh
an existing installation's scripts with its current Python environment and desktop
settings. Run a full install if the clipboard tools for your session are missing.
Reinstalling preserves the CPU preset. Use `--gpu` to switch back to the CUDA
preset, or `--cpu` to switch to CPU.

Override the shortcut with `SHORTCUT='<Super>r' ./install.sh`.

## Enable cloud dictation

Choose a provider and enter its API key at the hidden prompt on your own machine.

```bash
~/.local/bin/whisper-dictate --configure gemini   # Gemini 3.5 Transcribe
~/.local/bin/whisper-dictate --configure openai   # OpenAI GPT-Transcribe
~/.local/bin/whisper-dictate --configure local    # local-only
```

The choice and saved keys live in
`${XDG_CONFIG_HOME:-$HOME/.config}/whisper-dictate/config.json` (mode 0600).
You can also provide `GEMINI_API_KEY` or `OPENAI_API_KEY` in the environment
of the process launching dictation. The default backend is local until you
choose a cloud provider. For cloud dictation, the recorded clip goes to the
selected provider. Dictation displays a notification on cloud failure before
trying local Whisper. Gemini sends the audio inline with interaction storage
disabled. Google’s data-use and distribution rules vary by region, including
special provisions for the EEA, Switzerland, and the UK. See the
[Gemini API terms](https://aistudio.google.com/docs/terms).

[Gemini API pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-transcribe)
lists a free tier and an estimated paid rate of $0.005 per audio minute.
[OpenAI API pricing](https://developers.openai.com/api/docs/pricing#transcription-models)
lists `gpt-transcribe` at $0.0045 per audio minute.

## Usage

1. Focus any text field (browser, editor, **terminal**, chat …).
2. Press **Ctrl+Alt+D** → bell + “● Recording”. Speak.
3. Press **Ctrl+Alt+D** again → “Transcribing…”, then the text is pasted.

## Configuration

Environment overrides for the process launching dictation:

| Var | Default | Meaning |
|---|---|---|
| `WD_BACKEND` | _(saved choice or local)_ | `gemini`, `openai`, or `local` |
| `WD_CLOUD_TIMEOUT` | _15–120, based on clip size_ | Cloud socket timeout in seconds before falling back |
| `WD_MODEL` | `large-v3` (`small` for `--cpu`) | faster-whisper model name |
| `WD_DEVICE` | `cuda` (`cpu` for `--cpu`) | `cuda` or `cpu` (auto-falls back to cpu) |
| `WD_COMPUTE` | `auto` (`int8` for `--cpu`) | CT2 compute type (`auto`/`int8`/`float16`/…) |
| `WD_BEAM` | `1` | beam size (1 = greedy, snappy) |
| `WD_LANG` | _(auto)_ | hint a language, e.g. `de` |
| `WD_PASTE_KEY` | `shift+insert` (Wayland), `shift+Insert` (X11) | key sequence used to paste (see the active desktop tool for syntax) |

## How it works

```
Ctrl+Alt+D ──> whisper-dictate (toggle)
  press 1:  ffmpeg records the default mic -> /run/user/$UID/whisper-dictate/rec.wav
            writes state "recording"
  press 2:  stop ffmpeg -> state "transcribing" -> transcribe.py
            (selected cloud service, or local faster-whisper) -> text
            -> clipboard + primary -> Shift+Insert
            -> state "idle"  (cleared on every exit path via trap)

whisper-dictate-indicator (autostarted daemon)
  polls the state file every 250 ms and shows / hides the top-bar icon
```

State and the recording live in `$XDG_RUNTIME_DIR/whisper-dictate/`
(tmpfs; the audio is deleted right after transcription). The indicator
is decoupled — it only reads the state file, so the wrapper never
blocks on it and either part can be restarted independently. The last
run's stderr is in `~/.local/share/whisper-dictate/last-run.log`.

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
- **Indicator is a separate polling daemon, not driven by the wrapper.**
  A persistent panel item needs a long-lived process with a GLib loop;
  the short-lived toggle script can't host one. Coupling them (signals/
  socket) would risk wedging the hot paste path, so the wrapper only
  writes a one-word state file and the daemon polls it (250 ms). GNOME
  Wayland has no client window positioning and Mutter implements no
  layer-shell, so an overlay window is out — an AppIndicator/SNI tray
  item (via the GNOME extension) is the only robust top-bar option, and
  it needs no logout once the extension is active.

## Latency

Cloud requests start when you stop recording. Local Whisper loads its model
on demand (historically around 10–15 s with CUDA for `large-v3`). The
first local run can take longer if it needs to download the model.

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
- **No top-bar icon:** the AppIndicator extension isn't active. Check
  `gnome-extensions list --enabled | grep -i appindicator`; enable it
  with `gnome-extensions enable ubuntu-appindicators@ubuntu.com`. Check
  the daemon runs: `pgrep -af whisper-dictate-indicator`.

## Uninstall

```bash
rm -f  ~/.local/bin/whisper-dictate ~/.local/bin/whisper-dictate-indicator
rm -f  ~/.config/autostart/whisper-dictate-indicator.desktop
rm -rf ~/.local/share/whisper-dictate
pkill -f whisper-dictate-indicator
# then remove the "Whisper Dictate" entry in
# Settings → Keyboard → Keyboard Shortcuts
```
