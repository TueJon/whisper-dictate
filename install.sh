#!/usr/bin/env bash
# Reproducible installer for whisper-dictate.
# Idempotent: safe to re-run (e.g. after `git pull`).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WD_HOME="$HOME/.local/share/whisper-dictate"
BIN_DIR="$HOME/.local/bin"
SHORTCUT="${SHORTCUT:-<Control><Alt>d}"        # GNOME accelerator
APT_PKGS=(ffmpeg ydotool wl-clipboard libnotify-bin pulseaudio-utils python3-venv)

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }

# 1) System packages -----------------------------------------------------
missing=()
for p in "${APT_PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
if [ "${#missing[@]}" -gt 0 ]; then
    if command -v apt-get >/dev/null; then
        say "Installing system packages (sudo): ${missing[*]}"
        sudo apt-get update
        sudo apt-get install -y "${missing[@]}"
    else
        warn "Not an apt system. Install equivalents of: ${missing[*]}"
    fi
else
    say "System packages already present."
fi

# /dev/uinput access is required for ydotool to synthesize the paste.
if ! [ -w /dev/uinput ]; then
    warn "/dev/uinput is not writable by you. Add yourself to the 'input'"
    warn "group or add a udev rule, then re-login:"
    warn "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | sudo tee /etc/udev/rules.d/99-uinput.rules"
    warn "  sudo usermod -aG input \"$USER\"   # then log out and back in"
fi

# 2) Python venv + faster-whisper ---------------------------------------
say "Creating venv at $WD_HOME/venv"
mkdir -p "$WD_HOME" "$BIN_DIR"
[ -d "$WD_HOME/venv" ] || python3 -m venv "$WD_HOME/venv"
"$WD_HOME/venv/bin/pip" install -q -U pip wheel
say "Installing Python deps (faster-whisper + CUDA libs)"
"$WD_HOME/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

# 3) Install the scripts -------------------------------------------------
say "Installing scripts"
install -m 0755 "$REPO_DIR/transcribe.py"      "$WD_HOME/transcribe.py"
install -m 0755 "$REPO_DIR/bin/whisper-dictate" "$BIN_DIR/whisper-dictate"

# 4) Pre-download the model (skip with --no-model) ----------------------
if [[ "${1:-}" != "--no-model" ]]; then
    say "Pre-downloading the Whisper model (large-v3, ~3 GB; once)"
    LD_LIBRARY_PATH="$(echo "$WD_HOME"/venv/lib/python*/site-packages/nvidia/*/lib | tr ' ' ':')${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        "$WD_HOME/venv/bin/python" - <<'PY'
import os
from faster_whisper import WhisperModel
m = os.environ.get("WD_MODEL", "large-v3")
d = os.environ.get("WD_DEVICE", "cuda")
try:
    WhisperModel(m, device=d, compute_type="auto")
    print(f"  model '{m}' ready on {d}")
except Exception as e:
    print(f"  GPU load failed ({e!r}); fetching for CPU instead")
    WhisperModel(m, device="cpu", compute_type="int8")
PY
fi

# 5) GNOME keyboard shortcut (idempotent) -------------------------------
if command -v gsettings >/dev/null && [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]]; then
    say "Registering GNOME shortcut: $SHORTCUT"
    CMD="$BIN_DIR/whisper-dictate"
    python3 - "$CMD" "$SHORTCUT" <<'PY'
import ast, subprocess, sys
cmd, accel = sys.argv[1], sys.argv[2]
B = "org.gnome.settings-daemon.plugins.media-keys"
base = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"

def get(path, key):
    return subprocess.check_output(
        ["gsettings", "get", f"{B}.custom-keybinding:{path}", key]).decode().strip()

def setk(path, key, val):
    subprocess.check_call(["gsettings", "set", f"{B}.custom-keybinding:{path}", key, val])

cur = subprocess.check_output(["gsettings", "get", B, "custom-keybindings"]).decode().strip()
lst = [] if cur in ("@as []", "[]") else ast.literal_eval(cur)

# Reuse an existing slot if we already installed one (idempotent).
slot = None
for p in lst:
    try:
        if ast.literal_eval(get(p, "command")) == cmd:
            slot = p; break
    except Exception:
        pass
if slot is None:
    i = 0
    while f"{base}/custom{i}/" in lst:
        i += 1
    slot = f"{base}/custom{i}/"
    lst.append(slot)
    subprocess.check_call(["gsettings", "set", B, "custom-keybindings", str(lst)])

setk(slot, "name", "Whisper Dictate")
setk(slot, "command", cmd)
setk(slot, "binding", accel)
print(f"  bound {accel} -> {cmd}  ({slot})")
PY
else
    warn "Not a GNOME session — bind $BIN_DIR/whisper-dictate to a key yourself."
fi

say "Done. Make sure $BIN_DIR is on your PATH, then press the shortcut to dictate."
